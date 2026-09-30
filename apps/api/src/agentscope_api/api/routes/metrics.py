from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute

from agentscope_api.database import DatabaseSession
from agentscope_api.models.automatic_drift import (
    AutomaticDriftCheckRecord,
    MonitoringIncidentRecord,
)
from agentscope_api.models.drift import DriftComparisonRecord
from agentscope_api.models.monitoring import MonitoringSnapshotRecord

router = APIRouter(tags=["system"])


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: DatabaseSession) -> str:
    families = (
        (
            "agentscope_monitoring_snapshots",
            "Monitoring snapshots by lifecycle status.",
            MonitoringSnapshotRecord.status,
            ("pending", "queued", "running", "completed", "failed"),
            "status",
        ),
        (
            "agentscope_automatic_drift_checks",
            "Automatic drift checks by lifecycle status.",
            AutomaticDriftCheckRecord.status,
            ("pending", "queued", "running", "completed", "failed", "skipped"),
            "status",
        ),
        (
            "agentscope_monitoring_incidents",
            "Monitoring incidents by lifecycle status.",
            MonitoringIncidentRecord.status,
            ("open", "acknowledged", "resolved"),
            "status",
        ),
        (
            "agentscope_drift_comparisons",
            "Drift comparisons by canonical classification.",
            DriftComparisonRecord.classification,
            ("drift_detected", "no_drift_detected", "insufficient_evidence"),
            "classification",
        ),
    )
    lines: list[str] = []
    for name, help_text, column, values, label in families:
        counts = await _counts(session, column)
        lines.extend((f"# HELP {name} {help_text}", f"# TYPE {name} gauge"))
        lines.extend(f'{name}{{{label}="{value}"}} {counts.get(value, 0)}' for value in values)
    return "\n".join(lines) + "\n"


async def _counts(session: DatabaseSession, column: InstrumentedAttribute[str]) -> dict[str, int]:
    rows = (await session.execute(select(column, func.count()).group_by(column))).all()
    return {value: count for value, count in rows}
