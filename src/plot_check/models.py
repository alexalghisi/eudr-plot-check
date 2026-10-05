from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class Severity(StrEnum):
    ERROR = "error"  # the plot can't be used for a due diligence statement until it's fixed
    WARNING = "warning"  # usable, but someone should look


class Issue(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    severity: Severity
    message: str
    field: str | None = None


class Plot(BaseModel):
    """One row of a customer's farm list, as submitted."""

    model_config = ConfigDict(frozen=True)

    row: int
    farm_id: str
    country: str
    geometry: dict[str, Any]  # GeoJSON Point or Polygon/MultiPolygon, WGS84
    declared_area_ha: float | None = None


class ParsedRow(BaseModel):
    row: int
    farm_id: str | None
    plot: Plot | None
    issues: list[Issue]


Status = Literal["ok", "needs_fix", "deforestation_risk"]


class PlotResult(BaseModel):
    row: int
    farm_id: str
    status: Status
    issues: list[Issue]
    area_ha: float | None = None
    deforested_ha: float | None = None
    geometry: dict[str, Any] | None = None
