from __future__ import annotations

import uuid
from importlib.resources import files
from typing import Any

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def migrate(conn: Connection[Any]) -> None:
    conn.execute(files("plot_check").joinpath("schema.sql").read_text())
    conn.commit()


def save_report(
    conn: Connection[Any], customer: str, filename: str, summary: dict[str, Any], results: list[Any]
) -> str:
    report_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO farm_list_reports (id, customer, filename, summary, results) VALUES (%s, %s, %s, %s, %s)",
        (report_id, customer, filename, Jsonb(summary), Jsonb(results)),
    )
    conn.commit()
    return report_id


def load_report(conn: Connection[Any], customer: str, report_id: str) -> dict[str, Any] | None:
    """Reports are only visible to the customer that uploaded them."""
    try:
        uuid.UUID(report_id)
    except ValueError:
        return None
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "SELECT id, filename, created_at, summary, results FROM farm_list_reports WHERE id = %s AND customer = %s",
            (report_id, customer),
        ).fetchone()
    conn.commit()
    return row
