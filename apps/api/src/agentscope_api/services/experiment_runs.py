from __future__ import annotations

from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.experiment import (
    ExperimentEvaluationConditionRecord,
    ExperimentRecord,
    ExperimentRunRecord,
    ExperimentRunResultRecord,
    ExperimentSubjectRecord,
)
from agentscope_api.schemas.experiment_runs import (
    ExperimentRun,
    ExperimentRunList,
    ExperimentRunListParams,
    ExperimentRunResult,
    ExperimentRunResultList,
    ExperimentRunResultListParams,
    ExperimentRunStatus,
)
from agentscope_api.schemas.experiments import ExperimentStatus


class ExperimentRunNotFound(Exception):
    pass


class ExperimentRunConflict(Exception):
    pass


class ExperimentNotExecutable(Exception):
    pass


async def create_experiment_run(
    session: AsyncSession, experiment_id: UUID
) -> ExperimentRun:
    async with session.begin():
        experiment = await session.scalar(
            select(ExperimentRecord)
            .where(ExperimentRecord.id == experiment_id)
            .with_for_update()
        )
        if experiment is None:
            raise ExperimentRunNotFound
        if experiment.status not in {
            ExperimentStatus.READY.value,
            ExperimentStatus.COMPLETED.value,
            ExperimentStatus.FAILED.value,
        }:
            raise ExperimentNotExecutable
        subject_count = await session.scalar(
            select(func.count())
            .select_from(ExperimentSubjectRecord)
            .where(ExperimentSubjectRecord.experiment_id == experiment_id)
        )
        condition_count = await session.scalar(
            select(func.count())
            .select_from(ExperimentEvaluationConditionRecord)
            .where(ExperimentEvaluationConditionRecord.experiment_id == experiment_id)
        )
        expected = int(subject_count or 0) * int(condition_count or 0)
        if not expected:
            raise ExperimentNotExecutable
        record = ExperimentRunRecord(
            experiment_id=experiment_id,
            status=ExperimentRunStatus.PENDING.value,
            expected_decision_count=expected,
        )
        session.add(record)
    result = await get_experiment_run(session, record.id)
    assert result is not None
    return result


def _run_statement() -> Any:
    counts = (
        select(
            ExperimentRunResultRecord.run_id,
            func.count().label("completed_decision_count"),
            func.count()
            .filter(ExperimentRunResultRecord.outcome == "error")
            .label("evaluator_error_count"),
        )
        .group_by(ExperimentRunResultRecord.run_id)
        .subquery()
    )
    return (
        select(
            ExperimentRunRecord,
            func.coalesce(counts.c.completed_decision_count, 0).label(
                "completed_decision_count"
            ),
            func.coalesce(counts.c.evaluator_error_count, 0).label("evaluator_error_count"),
        )
        .outerjoin(counts, counts.c.run_id == ExperimentRunRecord.id)
    )


def _run(row: Row[Any]) -> ExperimentRun:
    record = row[0]
    completed = int(row.completed_decision_count)
    return ExperimentRun(
        id=record.id,
        experiment_id=record.experiment_id,
        status=ExperimentRunStatus(record.status),
        expected_decision_count=record.expected_decision_count,
        completed_decision_count=completed,
        evaluator_error_count=int(row.evaluator_error_count),
        remaining_decision_count=max(0, record.expected_decision_count - completed),
        error_category=record.error_category,
        error_message=record.error_message,
        created_at=record.created_at,
        queued_at=record.queued_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        attempt_count=record.attempt_count,
    )


async def get_experiment_run(session: AsyncSession, run_id: UUID) -> ExperimentRun | None:
    row = (
        await session.execute(_run_statement().where(ExperimentRunRecord.id == run_id))
    ).one_or_none()
    return None if row is None else _run(row)


async def list_experiment_runs(
    session: AsyncSession,
    experiment_id: UUID,
    params: ExperimentRunListParams,
) -> ExperimentRunList:
    if await session.get(ExperimentRecord, experiment_id) is None:
        raise ExperimentRunNotFound
    statement = _run_statement().where(ExperimentRunRecord.experiment_id == experiment_id)
    if params.status is not None:
        statement = statement.where(ExperimentRunRecord.status == params.status.value)
    rows = (
        await session.execute(
            statement.order_by(ExperimentRunRecord.created_at.desc(), ExperimentRunRecord.id.desc())
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return ExperimentRunList(
        items=[_run(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )


async def list_experiment_results(
    session: AsyncSession,
    run_id: UUID,
    params: ExperimentRunResultListParams,
) -> ExperimentRunResultList:
    if await session.get(ExperimentRunRecord, run_id) is None:
        raise ExperimentRunNotFound
    statement = (
        select(ExperimentRunResultRecord, ExperimentEvaluationConditionRecord)
        .join(
            ExperimentEvaluationConditionRecord,
            (
                ExperimentEvaluationConditionRecord.experiment_id
                == ExperimentRunResultRecord.experiment_id
            )
            & (
                ExperimentEvaluationConditionRecord.position
                == ExperimentRunResultRecord.condition_position
            ),
        )
        .where(ExperimentRunResultRecord.run_id == run_id)
    )
    if params.variant is not None:
        statement = statement.where(ExperimentRunResultRecord.variant_key == params.variant)
    if params.condition_position is not None:
        statement = statement.where(
            ExperimentRunResultRecord.condition_position == params.condition_position
        )
    rows = (
        await session.execute(
            statement.order_by(
                ExperimentRunResultRecord.subject_position,
                ExperimentRunResultRecord.variant_key,
                ExperimentRunResultRecord.condition_position,
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return ExperimentRunResultList(
        items=[
            ExperimentRunResult(
                run_id=result.run_id,
                experiment_id=result.experiment_id,
                subject_position=result.subject_position,
                variant=cast(Literal["A", "B"], result.variant_key),
                condition_position=result.condition_position,
                trace_id=result.trace_id,
                definition_id=result.definition_id,
                definition_name=condition.definition_name,
                evaluator_kind=condition.evaluator_kind,
                evaluator_config=condition.evaluator_config,
                outcome=cast(Literal["passed", "failed", "error"], result.outcome),
                score=result.score,
                details=result.details,
                attempt_count=result.attempt_count,
                created_at=result.created_at,
            )
            for result, condition in rows[: params.page_size]
        ],
        has_more=len(rows) > params.page_size,
    )
