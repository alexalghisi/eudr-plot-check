from __future__ import annotations

from collections import Counter
from typing import Any

from psycopg import Connection

from .geometry import GeometryChecks
from .models import PlotResult, Severity, Status
from .parse import parse

# Below this, overlap with a loss polygon is treated as edge noise rather than deforestation (100 m²).
DEFORESTATION_THRESHOLD_HA = 0.01


def assess(conn: Connection[Any], filename: str, data: bytes) -> tuple[dict[str, Any], list[PlotResult]]:
    """Parses a farm list, checks every plot and returns a summary plus one result per row."""
    rows = parse(filename, data)
    geometry = GeometryChecks(conn).run([r.plot for r in rows if r.plot is not None])
    results: list[PlotResult] = []
    for row in rows:
        checked = geometry.get(row.row)
        issues = row.issues + (checked.issues if checked else [])
        deforested = checked.deforested_ha if checked else None
        status: Status
        if any(i.severity is Severity.ERROR for i in issues) or row.plot is None:
            status = "needs_fix"
        elif deforested is not None and deforested >= DEFORESTATION_THRESHOLD_HA:
            status = "deforestation_risk"
        else:
            status = "ok"
        results.append(
            PlotResult(
                row=row.row,
                farm_id=row.farm_id or "",
                status=status,
                issues=issues,
                area_ha=checked.area_ha if checked else None,
                deforested_ha=deforested,
                geometry=checked.geometry if checked else None,
            )
        )
    statuses = Counter(r.status for r in results)
    summary = {
        "plots": len(results),
        "ok": statuses["ok"],
        "needs_fix": statuses["needs_fix"],
        "deforestation_risk": statuses["deforestation_risk"],
        "issues": dict(Counter(i.code for r in results for i in r.issues).most_common()),
    }
    return summary, results
