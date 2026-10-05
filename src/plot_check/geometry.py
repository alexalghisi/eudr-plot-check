"""Geometry checks that need real GIS: validity, geodesic area, overlaps and deforestation, all in PostGIS."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from psycopg import Connection, errors

from .countries import contains
from .models import Issue, Plot, Severity

EUDR_CUTOFF = date(2020, 12, 31)  # deforestation after this date makes a plot non-compliant
POLYGON_REQUIRED_ABOVE_HA = 4.0  # larger plots must be described by a polygon, not a point
AREA_TOLERANCE = 0.2  # declared vs measured area
MIN_AREA_HA = 0.01
OVERLAP_SHARE = 0.1
POINT_FALLBACK_HA = 4.0  # a point with no declared area is assessed as the largest plot a point may describe


@dataclass
class GeometryResult:
    issues: list[Issue] = field(default_factory=list)
    area_ha: float | None = None
    deforested_ha: float | None = None


def _warning(code: str, message: str, field_name: str = "geometry") -> Issue:
    return Issue(code=code, severity=Severity.WARNING, message=message, field=field_name)


def _error(code: str, message: str, field_name: str = "geometry") -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=message, field=field_name)


def _close_rings(geometry: dict[str, Any]) -> bool:
    """Closes unclosed polygon rings in place, a frequent export mistake. Returns True if any was closed."""
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    closed = False
    for polygon in polygons:
        for ring in polygon:
            if ring and ring[0] != ring[-1]:
                ring.append(list(ring[0]))
                closed = True
    return closed


class GeometryChecks:
    def __init__(self, conn: Connection[Any], cutoff: date = EUDR_CUTOFF) -> None:
        self._conn = conn
        self._cutoff = cutoff

    def run(self, plots: list[Plot]) -> dict[int, GeometryResult]:
        results = {p.row: GeometryResult() for p in plots}
        with self._conn.transaction():
            self._conn.execute(
                """CREATE TEMP TABLE plots_in (
                       row int PRIMARY KEY, farm_id text, country text, declared_ha double precision,
                       geom geometry, fixed geometry, footprint geometry
                   ) ON COMMIT DROP"""
            )
            self._load(plots, results)
            self._validity(results)
            self._areas(results)
            self._locations(plots, results)
            self._duplicates_and_overlaps(results)
            self._deforestation(results)
        return results

    def _load(self, plots: list[Plot], results: dict[int, GeometryResult]) -> None:
        for plot in plots:
            geometry = json.loads(json.dumps(plot.geometry))
            if geometry["type"] != "Point" and _close_rings(geometry):
                results[plot.row].issues.append(_warning("RING_NOT_CLOSED", "polygon ring was not closed; closed it"))
            try:
                with self._conn.transaction():
                    self._conn.execute(
                        """INSERT INTO plots_in (row, farm_id, country, declared_ha, geom)
                           VALUES (%s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))""",
                        (plot.row, plot.farm_id, plot.country, plot.declared_area_ha, json.dumps(geometry)),
                    )
            except errors.DataException as error:
                message = str(error).splitlines()[0]
                results[plot.row].issues.append(_error("INVALID_POLYGON", f"geometry can't be read: {message}"))

    def _validity(self, results: dict[int, GeometryResult]) -> None:
        rows = self._conn.execute(
            """UPDATE plots_in SET fixed = CASE
                   WHEN GeometryType(geom) = 'POINT' THEN geom
                   WHEN ST_IsValid(geom) THEN geom
                   ELSE ST_CollectionExtract(ST_MakeValid(geom), 3) END
               RETURNING row, GeometryType(geom), ST_IsValid(geom), ST_IsValidReason(geom), ST_IsEmpty(fixed)"""
        ).fetchall()
        for row, kind, valid, reason, empty in rows:
            if kind == "POINT" or valid:
                continue
            if empty:
                results[row].issues.append(
                    _error("INVALID_POLYGON", f"polygon is invalid and can't be repaired: {reason}")
                )
            else:
                results[row].issues.append(_warning("POLYGON_REPAIRED", f"polygon was invalid ({reason}); repaired it"))
        self._conn.execute("DELETE FROM plots_in WHERE ST_IsEmpty(fixed)")

    def _areas(self, results: dict[int, GeometryResult]) -> None:
        rows = self._conn.execute(
            """SELECT row, GeometryType(fixed), declared_ha,
                      CASE WHEN GeometryType(fixed) = 'POINT' THEN NULL ELSE ST_Area(fixed::geography) / 10000 END
               FROM plots_in"""
        ).fetchall()
        for row, kind, declared, measured in rows:
            result = results[row]
            if kind == "POINT":
                if declared is not None and declared > POLYGON_REQUIRED_ABOVE_HA:
                    result.issues.append(
                        _error(
                            "POLYGON_REQUIRED",
                            f"plots over {POLYGON_REQUIRED_ABOVE_HA:g} ha need a polygon, not a point "
                            f"({declared:g} ha declared)",
                        )
                    )
                area = declared if declared is not None else POINT_FALLBACK_HA
                radius = math.sqrt(area * 10_000 / math.pi)
                self._conn.execute(
                    "UPDATE plots_in SET footprint = ST_Buffer(fixed::geography, %s)::geometry WHERE row = %s",
                    (radius, row),
                )
                continue
            result.area_ha = round(measured, 4)
            if measured < MIN_AREA_HA:
                result.issues.append(_warning("TOO_SMALL", f"polygon covers only {measured * 10_000:.0f} m²"))
            if declared is not None and abs(measured - declared) > AREA_TOLERANCE * declared:
                result.issues.append(
                    _warning(
                        "AREA_MISMATCH",
                        f"polygon measures {measured:.2f} ha but {declared:g} ha was declared",
                        "area_ha",
                    )
                )
        self._conn.execute("UPDATE plots_in SET footprint = fixed WHERE footprint IS NULL")

    def _locations(self, plots: list[Plot], results: dict[int, GeometryResult]) -> None:
        countries = {p.row: p.country for p in plots}
        rows = self._conn.execute(
            """SELECT row, ST_X(ST_PointOnSurface(fixed)), ST_Y(ST_PointOnSurface(fixed))
               FROM plots_in WHERE GeometryType(fixed) <> 'POINT'"""
        ).fetchall()
        for row, lon, lat in rows:
            if contains(countries[row], lon, lat) is False:
                code, message = (
                    ("LAT_LON_SWAPPED", "polygon coordinates look like lat lon; WKT and GeoJSON use lon lat")
                    if contains(countries[row], lat, lon)
                    else ("OUTSIDE_COUNTRY", f"the polygon is not in {countries[row]}")
                )
                results[row].issues.append(_error(code, message, "polygon"))

    def _duplicates_and_overlaps(self, results: dict[int, GeometryResult]) -> None:
        for farm_id, rows in self._conn.execute(
            "SELECT farm_id, array_agg(row ORDER BY row) FROM plots_in GROUP BY farm_id HAVING count(*) > 1"
        ).fetchall():
            for row in rows[1:]:
                results[row].issues.append(
                    _error("DUPLICATE_FARM_ID", f"farm_id {farm_id} is also used on row {rows[0]}", "farm_id")
                )
        overlaps = self._conn.execute(
            """SELECT a.row, b.row,
                      ST_Area(ST_Intersection(a.fixed, b.fixed)::geography)
                        / LEAST(ST_Area(a.fixed::geography), ST_Area(b.fixed::geography))
               FROM plots_in a JOIN plots_in b
                 ON a.row < b.row AND ST_Intersects(a.fixed, b.fixed)
                AND GeometryType(a.fixed) <> 'POINT' AND GeometryType(b.fixed) <> 'POINT'"""
        ).fetchall()
        for a, b, share in overlaps:
            if share > OVERLAP_SHARE:
                results[b].issues.append(
                    _warning("OVERLAPPING_PLOTS", f"overlaps row {a} by {share:.0%} of the smaller plot")
                )

    def _deforestation(self, results: dict[int, GeometryResult]) -> None:
        rows = self._conn.execute(
            """SELECT p.row, ST_Area(ST_Intersection(p.footprint, ST_Union(l.geom))::geography) / 10000
               FROM plots_in p
               JOIN forest_loss l ON ST_Intersects(p.footprint, l.geom) AND l.detected_on > %s
               GROUP BY p.row, p.footprint""",
            (self._cutoff,),
        ).fetchall()
        lost = {row: hectares for row, hectares in rows}
        for row in self._conn.execute("SELECT row FROM plots_in").fetchall():
            results[row[0]].deforested_ha = round(lost.get(row[0], 0.0), 4)
