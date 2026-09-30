from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Integer, and_, cast, exists, func, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.trace import SpanRecord, TraceRecord
from agentscope_api.schemas.trace_queries import (
    SpanDetail,
    TraceDetail,
    TraceListParams,
    TraceListResponse,
    TraceSummary,
    decode_cursor,
    encode_cursor,
)


async def list_traces(session: AsyncSession, params: TraceListParams) -> TraceListResponse:
    aggregates = (
        select(
            SpanRecord.trace_id.label("trace_id"),
            func.count(SpanRecord.span_id).label("span_count"),
            func.count(SpanRecord.span_id)
            .filter(SpanRecord.status == "error")
            .label("error_span_count"),
            func.count(SpanRecord.span_id).filter(SpanRecord.kind == "llm").label("llm_span_count"),
            func.count(SpanRecord.span_id)
            .filter(SpanRecord.kind == "tool")
            .label("tool_span_count"),
            func.sum(cast(SpanRecord.llm["token_usage"]["input_tokens"].astext, Integer)).label(
                "total_input_tokens"
            ),
            func.sum(cast(SpanRecord.llm["token_usage"]["output_tokens"].astext, Integer)).label(
                "total_output_tokens"
            ),
            func.sum(cast(SpanRecord.llm["token_usage"]["total_tokens"].astext, Integer)).label(
                "total_tokens"
            ),
        )
        .group_by(SpanRecord.trace_id)
        .subquery()
    )
    statement = select(TraceRecord, aggregates).outerjoin(
        aggregates, aggregates.c.trace_id == TraceRecord.trace_id
    )
    conditions = []
    if params.status is not None:
        conditions.append(TraceRecord.status == params.status.value)
    if params.name is not None:
        escaped = params.name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(TraceRecord.name.ilike(f"%{escaped}%", escape="\\"))
    if params.started_after is not None:
        conditions.append(TraceRecord.started_at >= params.started_after)
    if params.started_before is not None:
        conditions.append(TraceRecord.started_at <= params.started_before)
    if params.min_duration_ms is not None:
        conditions.append(TraceRecord.duration_ms >= params.min_duration_ms)
    if params.max_duration_ms is not None:
        conditions.append(TraceRecord.duration_ms <= params.max_duration_ms)
    error_span = exists().where(
        SpanRecord.trace_id == TraceRecord.trace_id,
        SpanRecord.status == "error",
    )
    has_error = or_(TraceRecord.status == "error", error_span)
    if params.has_error is not None:
        conditions.append(has_error if params.has_error else ~has_error)
    if params.span_kind is not None:
        conditions.append(
            exists().where(
                SpanRecord.trace_id == TraceRecord.trace_id,
                SpanRecord.kind == params.span_kind.value,
            )
        )
    if params.cursor is not None:
        cursor = decode_cursor(params.cursor, params)
        conditions.append(
            or_(
                TraceRecord.started_at < cursor.started_at,
                and_(
                    TraceRecord.started_at == cursor.started_at,
                    TraceRecord.trace_id < cursor.trace_id,
                ),
            )
        )
    if conditions:
        statement = statement.where(*conditions)
    rows = (
        await session.execute(
            statement.order_by(TraceRecord.started_at.desc(), TraceRecord.trace_id.desc()).limit(
                params.page_size + 1
            )
        )
    ).all()
    has_more = len(rows) > params.page_size
    rows = rows[: params.page_size]
    items = [_summary(row) for row in rows]
    next_cursor = None
    if has_more and rows:
        record = rows[-1][0]
        next_cursor = encode_cursor(record.started_at, record.trace_id, params)
    return TraceListResponse(items=items, next_cursor=next_cursor, has_more=has_more)


async def get_trace(session: AsyncSession, trace_id: str) -> TraceDetail | None:
    trace = await session.get(TraceRecord, trace_id)
    if trace is None:
        return None
    spans = (
        await session.scalars(
            select(SpanRecord)
            .where(SpanRecord.trace_id == trace_id)
            .order_by(SpanRecord.started_at, SpanRecord.span_id)
        )
    ).all()
    return TraceDetail(
        trace_id=trace.trace_id,
        name=trace.name,
        status=trace.status,
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        duration_ms=trace.duration_ms,
        ingested_at=trace.ingested_at,
        input=trace.input,
        output=trace.output,
        error=trace.error,
        metadata=trace.metadata_,
        tags=trace.tags,
        spans=[_span_detail(span) for span in _parent_first(spans)],
    )


def _summary(row: Row[Any]) -> TraceSummary:
    record = row[0]
    return TraceSummary(
        trace_id=record.trace_id,
        name=record.name,
        status=record.status,
        started_at=record.started_at,
        ended_at=record.ended_at,
        duration_ms=record.duration_ms,
        ingested_at=record.ingested_at,
        tags=record.tags,
        output_available=record.output is not None,
        span_count=row.span_count or 0,
        error_span_count=row.error_span_count or 0,
        llm_span_count=row.llm_span_count or 0,
        tool_span_count=row.tool_span_count or 0,
        total_input_tokens=row.total_input_tokens,
        total_output_tokens=row.total_output_tokens,
        total_tokens=row.total_tokens,
    )


def _parent_first(spans: Sequence[SpanRecord]) -> list[SpanRecord]:
    children: dict[str | None, list[SpanRecord]] = defaultdict(list)
    for span in spans:
        children[span.parent_span_id].append(span)
    ordered: list[SpanRecord] = []
    stack = list(reversed(children[None]))
    while stack:
        span = stack.pop()
        ordered.append(span)
        stack.extend(reversed(children[span.span_id]))
    if len(ordered) != len(spans):
        included = {span.span_id for span in ordered}
        ordered.extend(span for span in spans if span.span_id not in included)
    return ordered


def _span_detail(span: SpanRecord) -> SpanDetail:
    return SpanDetail(
        span_id=span.span_id,
        trace_id=span.trace_id,
        parent_span_id=span.parent_span_id,
        name=span.name,
        kind=span.kind,
        status=span.status,
        started_at=span.started_at,
        ended_at=span.ended_at,
        duration_ms=span.duration_ms,
        input=span.input,
        output=span.output,
        error=span.error,
        metadata=span.metadata_,
        attributes=span.attributes,
        events=span.events,
        llm=span.llm,
    )
