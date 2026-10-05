from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from plot_check.store import migrate

DATABASE_URL = os.environ.get("PLOT_CHECK_TEST_DATABASE_URL")
needs_postgis = pytest.mark.skipif(DATABASE_URL is None, reason="PLOT_CHECK_TEST_DATABASE_URL is not set")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    assert DATABASE_URL is not None
    with psycopg.connect(DATABASE_URL) as connection:
        migrate(connection)
        connection.execute("TRUNCATE forest_loss, farm_list_reports")
        connection.commit()
        yield connection


def square(lon: float, lat: float, side_deg: float) -> list[list[float]]:
    """A closed square ring with its south-west corner at (lon, lat)."""
    return [[lon, lat], [lon + side_deg, lat], [lon + side_deg, lat + side_deg], [lon, lat + side_deg], [lon, lat]]


def add_forest_loss(conn: psycopg.Connection[Any], ring: list[list[float]], detected_on: str) -> None:
    conn.execute(
        """INSERT INTO forest_loss (source, detected_on, geom)
           VALUES ('test', %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))""",
        (detected_on, f'{{"type":"Polygon","coordinates":[{ring}]}}'),
    )
    conn.commit()
