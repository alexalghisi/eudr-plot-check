from __future__ import annotations

import csv
import io
from typing import Any

import psycopg
from fastapi.testclient import TestClient

from plot_check.api import create_app, parse_api_keys

from .conftest import DATABASE_URL, add_forest_loss, needs_postgis

pytestmark = needs_postgis


def wkt_square(lon: float, lat: float, side: float = 0.001) -> str:
    corners = [(lon, lat), (lon + side, lat), (lon + side, lat + side), (lon, lat + side), (lon, lat)]
    return "POLYGON((" + ", ".join(f"{x:.6f} {y:.6f}" for x, y in corners) + "))"


FARMS = (
    "farm_id,country,latitude,longitude,area_ha,polygon\n"
    f'F-001,CI,,,1.2,"{wkt_square(-5.30, 6.80)}"\n'
    "F-002,CI,6.812345,-5.312345,2.0,\n"
    "F-003,CI,6.81,-5.31,2.0,\n"
    "F-004,CI,6.825413,-5.284221,9.5,\n"
    f'F-005,CI,,,1.2,"{wkt_square(-5.29, 6.80)}"\n'
)
KEYS = {"key-roaster": "roaster-a", "key-other": "roaster-b"}


def client() -> TestClient:
    assert DATABASE_URL is not None
    return TestClient(create_app(DATABASE_URL, KEYS))


def upload(c: TestClient, key: str = "key-roaster", body: str = FARMS, name: str = "farms.csv") -> Any:
    return c.post(
        "/farm-lists", files={"file": (name, body.encode(), "text/csv")}, headers={"Authorization": f"Bearer {key}"}
    )


def test_reports_status_per_plot(conn: psycopg.Connection[Any]) -> None:
    add_forest_loss(
        conn,
        [[-5.2900, 6.8000], [-5.2895, 6.8000], [-5.2895, 6.8010], [-5.2900, 6.8010], [-5.2900, 6.8000]],
        "2023-04-01",
    )
    c = client()
    response = upload(c)
    assert response.status_code == 201
    body = response.json()
    assert body["summary"] == {
        "plots": 5,
        "ok": 2,
        "needs_fix": 2,
        "deforestation_risk": 1,
        "issues": {"LOW_PRECISION": 1, "POLYGON_REQUIRED": 1},
    }
    report = c.get(f"/farm-lists/{body['id']}", headers={"Authorization": "Bearer key-roaster"}).json()
    by_farm = {r["farm_id"]: r for r in report["results"]}
    assert by_farm["F-005"]["status"] == "deforestation_risk"
    assert 0.55 < by_farm["F-005"]["deforested_ha"] < 0.65


def test_issues_csv_is_a_fix_list_for_the_supplier(conn: psycopg.Connection[Any]) -> None:
    add_forest_loss(
        conn,
        [[-5.2900, 6.8000], [-5.2895, 6.8000], [-5.2895, 6.8010], [-5.2900, 6.8010], [-5.2900, 6.8000]],
        "2023-04-01",
    )
    c = client()
    report_id = upload(c).json()["id"]
    response = c.get(f"/farm-lists/{report_id}/issues.csv", headers={"Authorization": "Bearer key-roaster"})
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [(r["row"], r["code"]) for r in rows] == [
        ("4", "LOW_PRECISION"),
        ("5", "POLYGON_REQUIRED"),
        ("6", "DEFORESTATION_DETECTED"),
    ]


def test_reports_are_private_to_the_customer(conn: psycopg.Connection[Any]) -> None:
    c = client()
    report_id = upload(c).json()["id"]
    assert c.get(f"/farm-lists/{report_id}", headers={"Authorization": "Bearer key-other"}).status_code == 404
    assert c.get(f"/farm-lists/{report_id}").status_code == 401
    assert c.get("/farm-lists/not-a-uuid", headers={"Authorization": "Bearer key-roaster"}).status_code == 404


def test_unreadable_files_get_a_useful_422(conn: psycopg.Connection[Any]) -> None:
    response = upload(client(), body="id;lat;lng\n1;2;3\n")
    assert response.status_code == 422
    assert "needs farm_id, country and either latitude/longitude or polygon" in response.json()["detail"]


def test_api_keys_config() -> None:
    assert parse_api_keys("a:roaster-a, b:roaster-b,broken") == {"a": "roaster-a", "b": "roaster-b"}
