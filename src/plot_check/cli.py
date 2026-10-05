"""
plot-check load-forest-loss losses.geojson --source "detection model v3"
plot-check check farms.csv
Both read DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

from .service import assess
from .store import migrate

STATUS = {"ok": "ok", "needs_fix": "NEEDS FIX", "deforestation_risk": "DEFORESTATION"}


def load_forest_loss(conn: psycopg.Connection[object], path: Path, source: str) -> int:
    collection = json.loads(path.read_text())
    count = 0
    for feature in collection["features"]:
        conn.execute(
            """INSERT INTO forest_loss (source, detected_on, geom)
               VALUES (%s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))""",
            (source, feature["properties"]["detected_on"], json.dumps(feature["geometry"])),
        )
        count += 1
    conn.commit()
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="plot-check")
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser(
        "load-forest-loss", help="load tree cover loss polygons (GeoJSON with properties.detected_on)"
    )
    load.add_argument("path", type=Path)
    load.add_argument("--source", required=True)
    check = sub.add_parser("check", help="check a farm list (CSV or GeoJSON)")
    check.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        migrate(conn)
        if args.command == "load-forest-loss":
            print(f"loaded {load_forest_loss(conn, args.path, args.source)} forest loss polygons from {args.path.name}")
            return 0
        try:
            summary, results = assess(conn, args.path.name, args.path.read_bytes())
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2

    print(f"{'row':>4}  {'farm':<8} {'status':<14} {'area ha':>8} {'lost ha':>8}  issues")
    for r in results:
        area = f"{r.area_ha:.2f}" if r.area_ha is not None else "point"
        lost = f"{r.deforested_ha:.2f}" if r.deforested_ha else "-"
        label = "ok, warnings" if r.status == "ok" and r.issues else STATUS[r.status]
        print(f"{r.row:>4}  {r.farm_id:<8} {label:<14} {area:>8} {lost:>8}  {', '.join(i.code for i in r.issues)}")
    print(
        f"\n{summary['plots']} plots: {summary['ok']} ok, {summary['needs_fix']} need fixing, "
        f"{summary['deforestation_risk']} with deforestation after 2020-12-31"
    )
    return 1 if summary["needs_fix"] or summary["deforestation_risk"] else 0


if __name__ == "__main__":
    sys.exit(main())
