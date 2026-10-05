"""
Reads farm lists as customers send them (CSV with coordinates or WKT polygons, or GeoJSON) and reports
every problem per row instead of failing on the first one.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from .countries import contains
from .models import Issue, ParsedRow, Plot, Severity

MIN_DECIMALS = 6  # EUDR geolocation: at least six decimal digits
MAX_ROWS = 50_000


def _decimals(raw: str) -> int:
    _, _, fraction = raw.strip().partition(".")
    return len(fraction.rstrip())


def _error(code: str, message: str, field: str | None = None) -> Issue:
    return Issue(code=code, severity=Severity.ERROR, message=message, field=field)


def _float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _parse_wkt_polygon(wkt: str) -> dict[str, Any] | None:
    """POLYGON((lon lat, ...)) and MULTIPOLYGON(((...))) into GeoJSON, without a geometry library."""
    text = wkt.strip().upper()
    numbers = r"-?\d+(?:\.\d+)?"
    ring_pattern = re.compile(rf"\(\s*({numbers}\s+{numbers}(?:\s*,\s*{numbers}\s+{numbers})*)\s*\)")

    def rings(body: str) -> list[list[list[float]]]:
        return [[[float(v) for v in pair.split()] for pair in ring.split(",")] for ring in ring_pattern.findall(body)]

    if text.startswith("POLYGON"):
        found = rings(text)
        return {"type": "Polygon", "coordinates": found} if found else None
    if text.startswith("MULTIPOLYGON"):
        polygons = [
            rings(p) for p in re.findall(r"\(\s*(\(.*?\)(?:\s*,\s*\(.*?\))*)\s*\)", text[len("MULTIPOLYGON") :])
        ]
        polygons = [p for p in polygons if p]
        return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None
    return None


def _check_point(country: str, lat_raw: str, lon_raw: str) -> list[Issue]:
    issues: list[Issue] = []
    lat, lon = _float(lat_raw), _float(lon_raw)
    if lat is None or lon is None:
        return [_error("INVALID_COORDINATES", "latitude and longitude must be numbers", "latitude")]
    if min(_decimals(lat_raw), _decimals(lon_raw)) < MIN_DECIMALS:
        issues.append(
            _error("LOW_PRECISION", f"coordinates need at least {MIN_DECIMALS} decimal digits for EUDR", "latitude")
        )
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        issues.append(_error("OUT_OF_RANGE", "latitude must be within ±90 and longitude within ±180", "latitude"))
        return issues
    inside = contains(country, lon, lat)
    if inside is False:
        if contains(country, lat, lon):
            issues.append(_error("LAT_LON_SWAPPED", "latitude and longitude look swapped", "latitude"))
        else:
            issues.append(_error("OUTSIDE_COUNTRY", f"the location is not in {country.upper()}", "country"))
    return issues


def parse_csv(data: bytes) -> list[ParsedRow]:
    text = data.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = {f.strip().lower() for f in reader.fieldnames or []}
    missing = {"farm_id", "country"} - fields
    if missing or not ({"latitude", "longitude"} <= fields or "polygon" in fields):
        raise ValueError(
            "a farm list needs farm_id, country and either latitude/longitude or polygon (WKT) columns; "
            f"found: {', '.join(sorted(fields)) or 'none'}"
        )
    rows: list[ParsedRow] = []
    for index, raw in enumerate(reader, start=2):  # row 1 is the header
        if index - 1 > MAX_ROWS:
            raise ValueError(f"farm lists are limited to {MAX_ROWS} rows; split the file")
        record = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        rows.append(_row(index, record))
    return rows


def _row(index: int, record: dict[str, str]) -> ParsedRow:
    farm_id = record.get("farm_id") or None
    country = record.get("country", "").upper()
    issues: list[Issue] = []
    if farm_id is None:
        issues.append(_error("MISSING_FARM_ID", "farm_id is required", "farm_id"))
    if len(country) != 2:
        issues.append(_error("INVALID_COUNTRY", "country must be an ISO 3166-1 alpha-2 code", "country"))

    area = _float(record.get("area_ha"))
    if record.get("area_ha") and (area is None or area <= 0):
        issues.append(_error("INVALID_AREA", "area_ha must be a positive number", "area_ha"))
        area = None

    geometry: dict[str, Any] | None = None
    if record.get("polygon"):
        geometry = _parse_wkt_polygon(record["polygon"])
        if geometry is None:
            issues.append(
                _error("INVALID_POLYGON", "polygon must be WKT POLYGON or MULTIPOLYGON in lon lat order", "polygon")
            )
    elif record.get("latitude") or record.get("longitude"):
        point_issues = _check_point(country, record.get("latitude", ""), record.get("longitude", ""))
        issues += point_issues
        lat, lon = _float(record.get("latitude")), _float(record.get("longitude"))
        if lat is not None and lon is not None:
            geometry = {"type": "Point", "coordinates": [lon, lat]}
    else:
        issues.append(_error("MISSING_GEOLOCATION", "each plot needs coordinates or a polygon", "latitude"))

    plot = None
    if farm_id is not None and geometry is not None and len(country) == 2:
        plot = Plot(row=index, farm_id=farm_id, country=country, geometry=geometry, declared_area_ha=area)
    return ParsedRow(row=index, farm_id=farm_id, plot=plot, issues=issues)


def parse_geojson(data: bytes) -> list[ParsedRow]:
    try:
        collection = json.loads(data)
    except json.JSONDecodeError as error:
        raise ValueError(f"not valid JSON: {error.msg} at line {error.lineno}") from error
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection":
        raise ValueError("expected a GeoJSON FeatureCollection")
    rows: list[ParsedRow] = []
    for index, feature in enumerate(collection.get("features", []), start=1):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        issues: list[Issue] = []
        farm_id = str(properties.get("farm_id") or properties.get("ProducerName") or "") or None
        country = str(properties.get("country") or properties.get("ProducerCountry") or "").upper()
        if farm_id is None:
            issues.append(_error("MISSING_FARM_ID", "properties.farm_id is required", "farm_id"))
        if geometry.get("type") not in ("Point", "Polygon", "MultiPolygon"):
            issues.append(_error("INVALID_POLYGON", "geometry must be a Point, Polygon or MultiPolygon", "geometry"))
            geometry = {}
        elif geometry["type"] == "Point":
            lon, lat = geometry["coordinates"][:2]
            issues += _check_point(country, str(lat), str(lon))
        area = properties.get("area_ha") or properties.get("Area")
        plot = None
        if farm_id is not None and geometry:
            plot = Plot(
                row=index,
                farm_id=farm_id,
                country=country,
                geometry=geometry,
                declared_area_ha=float(area) if isinstance(area, int | float) else None,
            )
        rows.append(ParsedRow(row=index, farm_id=farm_id, plot=plot, issues=issues))
    return rows


def parse(filename: str, data: bytes) -> list[ParsedRow]:
    if filename.lower().endswith((".geojson", ".json")):
        return parse_geojson(data)
    return parse_csv(data)
