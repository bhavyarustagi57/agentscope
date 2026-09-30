from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.trace import SpanRecord, TraceRecord
from agentscope_api.schemas.traces import IngestionEnvelopeV1, SpanV1, TraceV1, trace_fingerprint

TRACE_TABLE = cast(Table, TraceRecord.__table__)
SPAN_TABLE = cast(Table, SpanRecord.__table__)


class DuplicateTraceConflict(Exception):
    def __init__(self, trace_ids: list[str]) -> None:
        self.trace_ids = sorted(trace_ids)
        super().__init__("trace_id already exists with a different payload")


@dataclass(frozen=True, slots=True)
class IngestionResult:
    accepted: int
    duplicates: int


async def ingest_traces(session: AsyncSession, envelope: IngestionEnvelopeV1) -> IngestionResult:
    fingerprints = {trace.trace_id: trace_fingerprint(trace) for trace in envelope.traces}
    trace_rows = sorted(
        (_trace_row(trace, fingerprints[trace.trace_id]) for trace in envelope.traces),
        key=lambda row: str(row["trace_id"]),
    )

    async with session.begin():
        inserted = set(
            (
                await session.scalars(
                    pg_insert(TRACE_TABLE)
                    .values(trace_rows)
                    .on_conflict_do_nothing(index_elements=[TRACE_TABLE.c.trace_id])
                    .returning(TRACE_TABLE.c.trace_id)
                )
            ).all()
        )
        rows = (
            (
                await session.execute(
                    select(TraceRecord.trace_id, TraceRecord.payload_fingerprint).where(
                        TraceRecord.trace_id.in_(fingerprints)
                    )
                )
            )
            .tuples()
            .all()
        )
        existing: dict[str, str] = dict(rows)
        conflicts = [
            trace_id
            for trace_id, fingerprint in fingerprints.items()
            if existing.get(trace_id) != fingerprint
        ]
        if conflicts:
            raise DuplicateTraceConflict(conflicts)

        span_rows = [
            _span_row(span)
            for trace in envelope.traces
            if trace.trace_id in inserted
            for span in trace.spans
        ]
        if span_rows:
            await session.execute(pg_insert(SPAN_TABLE), span_rows)

    return IngestionResult(
        accepted=len(inserted),
        duplicates=len(envelope.traces) - len(inserted),
    )


def _trace_row(trace: TraceV1, fingerprint: str) -> dict[str, object]:
    payload = trace.model_dump(mode="json")
    return {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "status": trace.status.value,
        "started_at": trace.start_time,
        "ended_at": trace.end_time,
        "duration_ms": trace.duration_ms,
        "input": payload["input"],
        "output": payload["output"],
        "error": payload["error"],
        "metadata": payload["metadata"],
        "tags": payload["tags"],
        "payload_fingerprint": fingerprint,
    }


def _span_row(span: SpanV1) -> dict[str, object]:
    payload = span.model_dump(mode="json")
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "kind": span.kind.value,
        "status": span.status.value,
        "started_at": span.start_time,
        "ended_at": span.end_time,
        "duration_ms": span.duration_ms,
        "input": payload["input"],
        "output": payload["output"],
        "error": payload["error"],
        "metadata": payload["metadata"],
        "attributes": payload["attributes"],
        "events": payload["events"],
        "llm": payload["llm"],
    }
