from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.evaluation import EvaluationResultRecord, EvaluationRunRecord
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.evaluations import (
    EvaluationResultCreate,
    EvaluationRunExecution,
    EvaluationRunStatus,
)
from agentscope_api.services.evaluations import (
    EvaluationRunNotFound,
    InvalidEvaluationRunState,
    TraceNotFound,
)
from agentscope_api.services.evaluator_engine import (
    EvaluationInput,
    InvalidEvaluatorConfiguration,
    UnsupportedEvaluatorKind,
    resolve_evaluator,
)

logger = logging.getLogger(__name__)


class ExistingEvaluationResults(Exception):
    pass


class InvalidEvaluationSubjects(ValueError):
    pass


class EvaluationRunExecutionFailed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunExecutionSummary:
    run_id: UUID
    status: EvaluationRunStatus
    result_count: int
    error_count: int


_ALLOWED_TRANSITIONS = {
    EvaluationRunStatus.PENDING: {EvaluationRunStatus.QUEUED, EvaluationRunStatus.RUNNING},
    EvaluationRunStatus.QUEUED: {EvaluationRunStatus.RUNNING},
    EvaluationRunStatus.RUNNING: {
        EvaluationRunStatus.COMPLETED,
        EvaluationRunStatus.FAILED,
    },
}


def transition_run(
    run: EvaluationRunRecord,
    target: str | EvaluationRunStatus,
    *,
    now: datetime | None = None,
    error_message: str | None = None,
) -> None:
    try:
        current = EvaluationRunStatus(run.status)
        target_status = EvaluationRunStatus(target)
    except ValueError as error:
        raise InvalidEvaluationRunState from error
    if target_status not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise InvalidEvaluationRunState

    timestamp = now or datetime.now(UTC)
    run.status = target_status.value
    if target_status is EvaluationRunStatus.QUEUED:
        run.queued_at = run.queued_at or timestamp
        return
    if target_status is EvaluationRunStatus.RUNNING:
        run.queued_at = run.queued_at or timestamp
        run.started_at = timestamp
        run.completed_at = None
        run.error_message = None
        return
    run.completed_at = timestamp
    run.error_message = error_message[:4_000] if error_message else None


async def execute_evaluation_run(
    session: AsyncSession, run_id: UUID, trace_ids: list[str]
) -> RunExecutionSummary:
    try:
        subjects = EvaluationRunExecution(trace_ids=trace_ids)
    except ValidationError as error:
        raise InvalidEvaluationSubjects("invalid evaluation subjects") from error

    started = time.perf_counter()
    execution_error: str | None = None
    summary: RunExecutionSummary | None = None
    evaluator_kind = "unknown"

    async with session.begin():
        run = await session.scalar(
            select(EvaluationRunRecord).where(EvaluationRunRecord.id == run_id).with_for_update()
        )
        if run is None:
            raise EvaluationRunNotFound
        if run.status not in {
            EvaluationRunStatus.PENDING.value,
            EvaluationRunStatus.QUEUED.value,
        }:
            raise InvalidEvaluationRunState
        if await session.scalar(
            select(func.count())
            .select_from(EvaluationResultRecord)
            .where(EvaluationResultRecord.run_id == run_id)
        ):
            raise ExistingEvaluationResults

        rows = (
            await session.execute(
                select(TraceRecord.trace_id, TraceRecord.output).where(
                    TraceRecord.trace_id.in_(subjects.trace_ids)
                )
            )
        ).all()
        outputs = {trace_id: output for trace_id, output in rows}
        missing = sorted(set(subjects.trace_ids) - outputs.keys())
        if missing:
            raise TraceNotFound(missing)

        transition_run(run, EvaluationRunStatus.RUNNING)
        evaluator_kind = run.evaluator_kind
        try:
            evaluator = resolve_evaluator(run.evaluator_kind, run.evaluator_config)
        except (InvalidEvaluatorConfiguration, UnsupportedEvaluatorKind):
            execution_error = "invalid evaluator snapshot"
            transition_run(run, EvaluationRunStatus.FAILED, error_message=execution_error)
        else:
            decisions = [
                (
                    trace_id,
                    evaluator.evaluate(
                        EvaluationInput(
                            trace_id=trace_id,
                            candidate=outputs[trace_id],
                            captured=outputs[trace_id] is not None,
                        )
                    ),
                )
                for trace_id in subjects.trace_ids
            ]
            for trace_id, decision in decisions:
                payload = EvaluationResultCreate(
                    run_id=run_id,
                    trace_id=trace_id,
                    outcome=decision.outcome,
                    score=decision.score,
                    details={"evaluator_kind": run.evaluator_kind, **decision.details},
                )
                session.add(EvaluationResultRecord(**payload.model_dump(mode="python")))
            transition_run(run, EvaluationRunStatus.COMPLETED)
            summary = RunExecutionSummary(
                run_id=run_id,
                status=EvaluationRunStatus.COMPLETED,
                result_count=len(decisions),
                error_count=sum(decision.outcome.value == "error" for _, decision in decisions),
            )

    duration_ms = (time.perf_counter() - started) * 1_000
    if execution_error is not None:
        logger.error(
            "evaluation_run_failed run_id=%s evaluator_kind=%s duration_ms=%.3f reason=%s",
            run_id,
            evaluator_kind,
            duration_ms,
            execution_error,
        )
        raise EvaluationRunExecutionFailed(execution_error)
    assert summary is not None
    logger.info(
        "evaluation_run_completed run_id=%s evaluator_kind=%s result_count=%d "
        "error_count=%d duration_ms=%.3f entry_point=internal_sync",
        run_id,
        evaluator_kind,
        summary.result_count,
        summary.error_count,
        duration_ms,
    )
    return summary
