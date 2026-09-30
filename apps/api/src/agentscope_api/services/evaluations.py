from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from agentscope_api.models.evaluation import (
    EvaluationDefinitionRecord,
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationRunSubjectRecord,
)
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.evaluations import (
    DefinitionListParams,
    EvaluationDefinition,
    EvaluationDefinitionCreate,
    EvaluationDefinitionList,
    EvaluationResult,
    EvaluationResultCreate,
    EvaluationResultList,
    EvaluationRun,
    EvaluationRunCreate,
    EvaluationRunList,
    EvaluationRunStatus,
    ResultListParams,
    RunListParams,
)


class EvaluationDefinitionNotFound(Exception):
    pass


class EvaluationDefinitionDisabled(Exception):
    pass


class EvaluationRunNotFound(Exception):
    pass


class TraceNotFound(Exception):
    def __init__(self, trace_ids: list[str] | None = None) -> None:
        self.trace_ids = sorted(trace_ids or [])
        message = (
            "trace not found"
            if not self.trace_ids
            else f"trace not found: {', '.join(self.trace_ids)}"
        )
        super().__init__(message)


class InvalidEvaluationRunState(Exception):
    pass


async def create_definition(
    session: AsyncSession, payload: EvaluationDefinitionCreate
) -> EvaluationDefinitionRecord:
    record = EvaluationDefinitionRecord(**payload.model_dump(mode="python"))
    async with session.begin():
        session.add(record)
    await session.refresh(record)
    return record


async def get_definition(
    session: AsyncSession, definition_id: UUID
) -> EvaluationDefinitionRecord | None:
    return await session.get(EvaluationDefinitionRecord, definition_id)


async def list_definitions(
    session: AsyncSession, params: DefinitionListParams
) -> EvaluationDefinitionList:
    statement = select(EvaluationDefinitionRecord)
    if params.evaluator_kind is not None:
        statement = statement.where(
            EvaluationDefinitionRecord.evaluator_kind == params.evaluator_kind.value
        )
    if params.is_enabled is not None:
        statement = statement.where(EvaluationDefinitionRecord.is_enabled == params.is_enabled)
    rows = (
        await session.scalars(
            statement.order_by(
                EvaluationDefinitionRecord.created_at.desc(),
                EvaluationDefinitionRecord.id.desc(),
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return EvaluationDefinitionList(
        items=[EvaluationDefinition.model_validate(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def create_run(session: AsyncSession, payload: EvaluationRunCreate) -> EvaluationRunRecord:
    async with session.begin():
        definition = await session.get(EvaluationDefinitionRecord, payload.definition_id)
        if definition is None:
            raise EvaluationDefinitionNotFound
        if not definition.is_enabled:
            raise EvaluationDefinitionDisabled
        record = EvaluationRunRecord(
            definition_id=definition.id,
            definition_name=definition.name,
            evaluator_kind=definition.evaluator_kind,
            evaluator_config=definition.evaluator_config,
            status=EvaluationRunStatus.PENDING.value,
        )
        session.add(record)
    await session.refresh(record)
    return record


def _run_summary_statement() -> Select[Any]:
    subjects = (
        select(
            EvaluationRunSubjectRecord.run_id.label("run_id"),
            func.count().label("subject_count"),
        )
        .group_by(EvaluationRunSubjectRecord.run_id)
        .subquery()
    )
    results = (
        select(
            EvaluationResultRecord.run_id.label("run_id"),
            func.count().label("result_count"),
            func.count().filter(EvaluationResultRecord.outcome == "passed").label("passed_count"),
            func.count().filter(EvaluationResultRecord.outcome == "failed").label("failed_count"),
            func.count().filter(EvaluationResultRecord.outcome == "error").label("error_count"),
            func.count(EvaluationResultRecord.score).label("scored_count"),
            func.avg(EvaluationResultRecord.score).label("average_score"),
        )
        .group_by(EvaluationResultRecord.run_id)
        .subquery()
    )
    return (
        select(
            EvaluationRunRecord,
            func.greatest(
                func.coalesce(subjects.c.subject_count, 0),
                func.coalesce(results.c.result_count, 0),
            ).label("subject_count"),
            func.coalesce(results.c.result_count, 0).label("result_count"),
            func.coalesce(results.c.passed_count, 0).label("passed_count"),
            func.coalesce(results.c.failed_count, 0).label("failed_count"),
            func.coalesce(results.c.error_count, 0).label("error_count"),
            func.coalesce(results.c.scored_count, 0).label("scored_count"),
            results.c.average_score,
        )
        .outerjoin(subjects, subjects.c.run_id == EvaluationRunRecord.id)
        .outerjoin(results, results.c.run_id == EvaluationRunRecord.id)
    )


def _run_summary(row: Row[Any]) -> EvaluationRun:
    return EvaluationRun.model_validate(row[0]).model_copy(
        update={
            "subject_count": row.subject_count,
            "result_count": row.result_count,
            "passed_count": row.passed_count,
            "failed_count": row.failed_count,
            "error_count": row.error_count,
            "scored_count": row.scored_count,
            "average_score": row.average_score,
        }
    )


async def get_run(session: AsyncSession, run_id: UUID) -> EvaluationRun | None:
    row = (
        await session.execute(_run_summary_statement().where(EvaluationRunRecord.id == run_id))
    ).one_or_none()
    return None if row is None else _run_summary(row)


async def list_runs(session: AsyncSession, params: RunListParams) -> EvaluationRunList:
    statement = _run_summary_statement()
    if params.definition_id is not None:
        statement = statement.where(EvaluationRunRecord.definition_id == params.definition_id)
    if params.status is not None:
        statement = statement.where(EvaluationRunRecord.status == params.status.value)
    rows = (
        await session.execute(
            statement.order_by(EvaluationRunRecord.created_at.desc(), EvaluationRunRecord.id.desc())
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return EvaluationRunList(
        items=[_run_summary(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def create_result(
    session: AsyncSession, payload: EvaluationResultCreate
) -> EvaluationResultRecord:
    async with session.begin():
        run = await session.get(EvaluationRunRecord, payload.run_id)
        if run is None:
            raise EvaluationRunNotFound
        if run.status != EvaluationRunStatus.RUNNING.value:
            raise InvalidEvaluationRunState
        if await session.get(TraceRecord, payload.trace_id) is None:
            raise TraceNotFound
        record = EvaluationResultRecord(**payload.model_dump(mode="python"))
        session.add(record)
    await session.refresh(record)
    return record


def _result(row: Row[Any]) -> EvaluationResult:
    return EvaluationResult.model_validate(row[0]).model_copy(update={"trace_name": row.trace_name})


async def get_result(session: AsyncSession, result_id: UUID) -> EvaluationResult | None:
    row = (
        await session.execute(
            select(EvaluationResultRecord, TraceRecord.name.label("trace_name"))
            .join(TraceRecord, TraceRecord.trace_id == EvaluationResultRecord.trace_id)
            .where(EvaluationResultRecord.id == result_id)
        )
    ).one_or_none()
    return None if row is None else _result(row)


async def list_results(
    session: AsyncSession, run_id: UUID, params: ResultListParams
) -> EvaluationResultList:
    if await session.get(EvaluationRunRecord, run_id) is None:
        raise EvaluationRunNotFound
    statement = (
        select(EvaluationResultRecord, TraceRecord.name.label("trace_name"))
        .join(TraceRecord, TraceRecord.trace_id == EvaluationResultRecord.trace_id)
        .where(EvaluationResultRecord.run_id == run_id)
    )
    if params.trace_id is not None:
        statement = statement.where(EvaluationResultRecord.trace_id == params.trace_id)
    if params.outcome is not None:
        statement = statement.where(EvaluationResultRecord.outcome == params.outcome.value)
    rows = (
        await session.execute(
            statement.order_by(
                EvaluationResultRecord.created_at.desc(), EvaluationResultRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return EvaluationResultList(
        items=[_result(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )
