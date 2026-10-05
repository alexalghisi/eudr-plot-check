"""HTTP API: customers upload farm lists and get a report and a fix list back.

No `from __future__ import annotations` here: FastAPI resolves the dependency annotations at runtime.
"""

import csv
import io
import os
from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Response, UploadFile, status

from .service import assess
from .store import load_report, migrate, save_report

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def parse_api_keys(raw: str) -> dict[str, str]:
    """PLOT_CHECK_API_KEYS="key1:customer-a,key2:customer-b"."""
    pairs = (item.split(":", 1) for item in raw.split(",") if ":" in item)
    return {key.strip(): customer.strip() for key, customer in pairs}


def create_app(database_url: str, api_keys: dict[str, str]) -> FastAPI:
    app = FastAPI(title="EUDR plot check")

    with psycopg.connect(database_url) as conn:
        migrate(conn)

    def connection() -> Iterator[psycopg.Connection[Any]]:
        with psycopg.connect(database_url) as conn:
            yield conn

    def customer(authorization: Annotated[str, Header()] = "") -> str:
        key = authorization.removeprefix("Bearer ").strip()
        if key not in api_keys:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key")
        return api_keys[key]

    Conn = Annotated[psycopg.Connection[Any], Depends(connection)]
    Customer = Annotated[str, Depends(customer)]

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/farm-lists", status_code=status.HTTP_201_CREATED)
    async def upload(file: UploadFile, conn: Conn, who: Customer) -> dict[str, Any]:
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "farm lists are limited to 10 MB")
        filename = file.filename or "upload.csv"
        try:
            summary, results = assess(conn, filename, data)
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
        report_id = save_report(conn, who, filename, summary, [r.model_dump(mode="json") for r in results])
        return {"id": report_id, "summary": summary}

    @app.get("/farm-lists/{report_id}")
    def report(report_id: str, conn: Conn, who: Customer) -> dict[str, Any]:
        found = load_report(conn, who, report_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
        return found

    @app.get("/farm-lists/{report_id}/issues.csv")
    def issues_csv(report_id: str, conn: Conn, who: Customer) -> Response:
        """One line per problem, in the row order of the uploaded file, ready to send back to the supplier."""
        found = load_report(conn, who, report_id)
        if found is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["row", "farm_id", "status", "severity", "code", "field", "message"])
        for result in found["results"]:
            for issue in result["issues"]:
                writer.writerow(
                    [
                        result["row"],
                        result["farm_id"],
                        result["status"],
                        issue["severity"],
                        issue["code"],
                        issue["field"],
                        issue["message"],
                    ]
                )
            if result["status"] == "deforestation_risk":
                writer.writerow(
                    [
                        result["row"],
                        result["farm_id"],
                        result["status"],
                        "error",
                        "DEFORESTATION_DETECTED",
                        "geometry",
                        f"{result['deforested_ha']} ha of tree cover loss after 2020-12-31",
                    ]
                )
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="issues-{report_id}.csv"'},
        )

    return app


def app_from_env() -> FastAPI:
    return create_app(os.environ["DATABASE_URL"], parse_api_keys(os.environ.get("PLOT_CHECK_API_KEYS", "")))
