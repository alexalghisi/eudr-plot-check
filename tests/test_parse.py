import json

import pytest

from plot_check.models import ParsedRow
from plot_check.parse import parse_csv, parse_geojson


def codes(row: ParsedRow) -> list[str]:
    return [i.code for i in row.issues]


HEADER = "farm_id,country,latitude,longitude,area_ha,polygon\n"


def test_reads_points_and_polygons() -> None:
    rows = parse_csv(
        (
            HEADER
            + "F-001,CI,6.825413,-5.284221,1.8,\n"
            + 'F-002,CI,,,6.0,"POLYGON((-5.29 6.83, -5.28 6.83, -5.28 6.84, -5.29 6.84, -5.29 6.83))"\n'
        ).encode()
    )
    assert [r.issues for r in rows] == [[], []]
    assert rows[0].plot is not None and rows[0].plot.geometry == {"type": "Point", "coordinates": [-5.284221, 6.825413]}
    assert rows[1].plot is not None and rows[1].plot.geometry["type"] == "Polygon"
    assert rows[1].plot.geometry["coordinates"][0][0] == [-5.29, 6.83]


def test_reports_every_problem_per_row() -> None:
    rows = parse_csv(
        (
            HEADER
            + "F-010,CI,6.82,-5.28,1.0,\n"  # not precise enough
            + "F-011,CI,-5.284221,6.825413,1.0,\n"  # swapped
            + "F-012,GH,6.825413,-5.284221,1.0,\n"  # that's Côte d'Ivoire, not Ghana
            + ",Ivory Coast,abc,-5.284221,-2,\n"
            + 'F-014,CI,,,3,"POLYGON((oops))"\n'
        ).encode()
    )
    assert [codes(r) for r in rows] == [
        ["LOW_PRECISION"],
        ["LAT_LON_SWAPPED"],
        ["OUTSIDE_COUNTRY"],
        ["MISSING_FARM_ID", "INVALID_COUNTRY", "INVALID_AREA", "INVALID_COORDINATES"],
        ["INVALID_POLYGON"],
    ]
    assert [r.row for r in rows] == [2, 3, 4, 5, 6]  # spreadsheet row numbers, header is row 1
    assert all(r.plot is None for r in rows[3:])


def test_unknown_countries_skip_the_location_check() -> None:
    rows = parse_csv((HEADER + "F-1,XX,45.123456,7.123456,1,\n").encode())
    assert rows[0].issues == []


def test_rejects_files_without_the_required_columns() -> None:
    with pytest.raises(ValueError, match="needs farm_id, country and either latitude/longitude or polygon"):
        parse_csv(b"id,lat,lng\n1,2,3\n")


def test_reads_geojson_including_common_property_names() -> None:
    collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"ProducerName": "F-100", "ProducerCountry": "ET", "Area": 0.9},
                "geometry": {"type": "Point", "coordinates": [38.473125, 6.351278]},
            },
            {
                "type": "Feature",
                "properties": {"farm_id": "F-101", "country": "ET"},
                "geometry": {"type": "LineString"},
            },
        ],
    }
    rows = parse_geojson(json.dumps(collection).encode())
    assert rows[0].plot is not None and rows[0].plot.declared_area_ha == 0.9
    assert codes(rows[1]) == ["INVALID_POLYGON"]
