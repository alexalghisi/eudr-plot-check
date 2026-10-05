from __future__ import annotations

from typing import Any

import psycopg

from plot_check.geometry import GeometryChecks
from plot_check.models import Plot

from .conftest import add_forest_loss, needs_postgis, square

pytestmark = needs_postgis

# about 1.1 km per 0.01 degree at this latitude, so a 0.01-degree square is roughly 122 ha
LON, LAT = -5.30, 6.80


def plot(row: int, geometry: dict[str, Any], area: float | None = None, farm_id: str | None = None) -> Plot:
    return Plot(row=row, farm_id=farm_id or f"F-{row}", country="CI", geometry=geometry, declared_area_ha=area)


def polygon(ring: list[list[float]]) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": [ring]}


def codes(result: Any) -> list[str]:
    return [i.code for i in result.issues]


def test_measures_area_geodesically_and_flags_mismatches(conn: psycopg.Connection[Any]) -> None:
    results = GeometryChecks(conn).run(
        [
            plot(2, polygon(square(LON, LAT, 0.001)), area=1.2),
            plot(3, polygon(square(LON + 0.01, LAT, 0.001)), area=3.0),
        ]
    )
    assert results[2].area_ha is not None and 1.2 < results[2].area_ha < 1.25
    assert codes(results[2]) == []
    assert codes(results[3]) == ["AREA_MISMATCH"]


def test_repairs_what_it_can_and_rejects_the_rest(conn: psycopg.Connection[Any]) -> None:
    bow_tie = [[LON, LAT], [LON + 0.001, LAT + 0.001], [LON + 0.001, LAT], [LON, LAT + 0.001], [LON, LAT]]
    open_ring = square(LON + 0.02, LAT, 0.001)[:-1]
    line = [[LON, LAT], [LON + 0.001, LAT], [LON, LAT]]
    results = GeometryChecks(conn).run([plot(2, polygon(bow_tie)), plot(3, polygon(open_ring)), plot(4, polygon(line))])
    assert codes(results[2]) == ["POLYGON_REPAIRED"]
    assert results[2].area_ha is not None and results[2].area_ha > 0
    assert codes(results[3]) == ["RING_NOT_CLOSED"]
    assert codes(results[4]) == ["INVALID_POLYGON"]


def test_large_plots_need_polygons(conn: psycopg.Connection[Any]) -> None:
    point = {"type": "Point", "coordinates": [LON, LAT]}
    results = GeometryChecks(conn).run([plot(2, point, area=3.5), plot(3, point, area=12)])
    assert codes(results[2]) == []
    assert codes(results[3]) == ["POLYGON_REQUIRED"]


def test_polygons_in_lat_lon_order_are_caught(conn: psycopg.Connection[Any]) -> None:
    swapped = [[lat, lon] for lon, lat in square(LON, LAT, 0.001)]
    assert codes(GeometryChecks(conn).run([plot(2, polygon(swapped))])[2]) == ["LAT_LON_SWAPPED"]


def test_duplicates_and_overlaps(conn: psycopg.Connection[Any]) -> None:
    results = GeometryChecks(conn).run(
        [
            plot(2, polygon(square(LON, LAT, 0.002)), farm_id="F-A"),
            plot(3, polygon(square(LON + 0.001, LAT, 0.002)), farm_id="F-B"),  # overlaps half of row 2
            plot(4, polygon(square(LON + 0.05, LAT, 0.001)), farm_id="F-A"),
        ]
    )
    assert codes(results[3]) == ["OVERLAPPING_PLOTS"]
    assert results[3].issues[0].message == "overlaps row 2 by 50% of the smaller plot"
    assert codes(results[4]) == ["DUPLICATE_FARM_ID"]


def test_counts_only_forest_loss_after_the_cutoff(conn: psycopg.Connection[Any]) -> None:
    add_forest_loss(conn, square(LON, LAT, 0.0005), "2022-06-01")  # a quarter of the plot below
    add_forest_loss(conn, square(LON + 0.0005, LAT, 0.0005), "2019-03-01")  # before the cutoff: allowed
    results = GeometryChecks(conn).run(
        [plot(2, polygon(square(LON, LAT, 0.001))), plot(3, polygon(square(LON + 0.01, LAT, 0.001)))]
    )
    assert results[2].deforested_ha is not None and 0.29 < results[2].deforested_ha < 0.32
    assert results[3].deforested_ha == 0


def test_points_are_assessed_with_their_declared_area(conn: psycopg.Connection[Any]) -> None:
    add_forest_loss(conn, square(LON - 0.0001, LAT - 0.0001, 0.0002), "2023-01-15")  # ~0.05 ha around the point
    point = {"type": "Point", "coordinates": [LON, LAT]}
    far = {"type": "Point", "coordinates": [LON + 0.01, LAT]}
    results = GeometryChecks(conn).run([plot(2, point, area=1.0), plot(3, far, area=1.0)])
    assert results[2].deforested_ha is not None and 0.04 < results[2].deforested_ha < 0.06
    assert results[3].deforested_ha == 0
