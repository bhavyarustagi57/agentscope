from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.database import SessionLocal
from agentscope_api.models.evaluation import (
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationRunSubjectRecord,
)
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.evaluations import (
    EvaluationResultCreate,
    EvaluationRunExecution,
    EvaluationRunStatus,
)
from agentscope_api.services.evaluation_execution import transition_run
from agentscope_api.services.evaluations import EvaluationRunNotFound, TraceNotFound
from agentscope_api.services.evaluator_engine import (
    EvaluationDecision,
    EvaluationInput,
    InvalidEvaluatorConfiguration,
    UnsupportedEvaluatorKind,
    resolve_evaluator,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 4
MAX_RECOVERY_BATCH = 100
QUEUE_RECOVERY_AGE = timedelta(minutes=5)
RUNNING_STALE_AGE = timedelta(minutes=5)


class ExecutionIntentConflict(Exception):
    pass


class ExecutionNotAllowed(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Submission:
    run_id: UUID
    subject_count: int


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    IGNORED = "ignored"
    EXHAUSTED = "exhausted"


class ProcessOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class Claim:
    outcome: ClaimOutcome
    attempt: int = 0


@dataclass(frozen=True, slots=True)
class EvaluationWork:
    run_id: UUID
    evaluator_kind: str
    evaluator_config: dict[str, object]
    inputs: tuple[EvaluationInput, ...]


async def submit_evaluation_run(
    session: AsyncSession, run_id: UUID, payload: EvaluationRunExecution
) -> Submission:
    requested = payload.trace_ids
    async with session.begin():
        run = await session.scalar(
            select(EvaluationRunRecord).where(EvaluationRunRecord.id == run_id).with_for_update()
        )
        if run is None:
            raise EvaluationRunNotFound

        stored_subjects = (
            await session.scalars(
                select(EvaluationRunSubjectRecord.trace_id)
                .where(EvaluationRunSubjectRecord.run_id == run_id)
                .order_by(EvaluationRunSubjectRecord.position)
            )
        ).all()
        result_count = await session.scalar(
            select(func.count())
            .select_from(EvaluationResultRecord)
            .where(EvaluationResultRecord.run_id == run_id)
        )
        if result_count:
            raise ExecutionIntentConflict
        if run.status == EvaluationRunStatus.QUEUED.value:
            if list(stored_subjects) != requested:
                raise ExecutionIntentConflict
            return Submission(run_id=run_id, subject_count=len(stored_subjects))
        if run.status != EvaluationRunStatus.PENDING.value:
            raise ExecutionNotAllowed
        if stored_subjects:
            raise ExecutionIntentConflict

        existing = set(
            await session.scalars(
                select(TraceRecord.trace_id).where(TraceRecord.trace_id.in_(requested))
            )
        )
        missing = sorted(set(requested) - existing)
        if missing:
            raise TraceNotFound(missing)

        session.add_all(
            EvaluationRunSubjectRecord(run_id=run_id, trace_id=trace_id, position=position)
            for position, trace_id in enumerate(requested)
        )
        transition_run(run, EvaluationRunStatus.QUEUED)

    return Submission(run_id=run_id, subject_count=len(requested))


async def mark_run_enqueued(
    session: AsyncSession, run_id: UUID, *, now: datetime | None = None
) -> None:
    async with session.begin():
        await session.execute(
            update(EvaluationRunRecord)
            .where(
                EvaluationRunRecord.id == run_id,
                EvaluationRunRecord.status == EvaluationRunStatus.QUEUED.value,
            )
            .values(last_enqueued_at=now or datetime.now(UTC))
        )


async def claim_evaluation_run(
    session: AsyncSession, run_id: UUID, *, now: datetime | None = None
) -> Claim:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        run = await session.scalar(
            select(EvaluationRunRecord).where(EvaluationRunRecord.id == run_id).with_for_update()
        )
        if run is None or run.status != EvaluationRunStatus.QUEUED.value:
            return Claim(ClaimOutcome.IGNORED)

        subject_count = await session.scalar(
            select(func.count())
            .select_from(EvaluationRunSubjectRecord)
            .where(EvaluationRunSubjectRecord.run_id == run_id)
        )
        result_count = await session.scalar(
            select(func.count())
            .select_from(EvaluationResultRecord)
            .where(EvaluationResultRecord.run_id == run_id)
        )
        if run.attempt_count >= MAX_ATTEMPTS or not subject_count or result_count:
            transition_run(run, EvaluationRunStatus.RUNNING, now=timestamp)
            transition_run(
                run,
                EvaluationRunStatus.FAILED,
                now=timestamp,
                error_message="evaluation run cannot be claimed",
            )
            return Claim(ClaimOutcome.EXHAUSTED, run.attempt_count)

        run.attempt_count += 1
        transition_run(run, EvaluationRunStatus.RUNNING, now=timestamp)
        return Claim(ClaimOutcome.CLAIMED, run.attempt_count)


async def _load_claimed_work(run_id: UUID, attempt: int) -> EvaluationWork | None:
    async with SessionLocal() as session:
        run = await session.scalar(
            select(EvaluationRunRecord).where(
                EvaluationRunRecord.id == run_id,
                EvaluationRunRecord.status == EvaluationRunStatus.RUNNING.value,
                EvaluationRunRecord.attempt_count == attempt,
            )
        )
        if run is None:
            return None
        rows = (
            await session.execute(
                select(
                    EvaluationRunSubjectRecord.trace_id,
                    TraceRecord.output,
                )
                .join(TraceRecord, TraceRecord.trace_id == EvaluationRunSubjectRecord.trace_id)
                .where(EvaluationRunSubjectRecord.run_id == run_id)
                .order_by(EvaluationRunSubjectRecord.position)
            )
        ).all()
    if not rows:
        return None
    return EvaluationWork(
        run_id=run_id,
        evaluator_kind=run.evaluator_kind,
        evaluator_config=run.evaluator_config,
        inputs=tuple(
            EvaluationInput(trace_id=trace_id, candidate=output, captured=output is not None)
            for trace_id, output in rows
        ),
    )


def _evaluate_work(work: EvaluationWork) -> list[tuple[str, EvaluationDecision]]:
    evaluator = resolve_evaluator(work.evaluator_kind, work.evaluator_config)
    return [(input_.trace_id, evaluator.evaluate(input_)) for input_ in work.inputs]


async def finalize_evaluation_run(
    run_id: UUID,
    attempt: int,
    decisions: list[tuple[str, EvaluationDecision]],
) -> bool:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(EvaluationRunRecord).where(EvaluationRunRecord.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.status != EvaluationRunStatus.RUNNING.value
            or run.attempt_count != attempt
        ):
            return False
        if await session.scalar(
            select(func.count())
            .select_from(EvaluationResultRecord)
            .where(EvaluationResultRecord.run_id == run_id)
        ):
            return False
        subject_ids = list(
            await session.scalars(
                select(EvaluationRunSubjectRecord.trace_id)
                .where(EvaluationRunSubjectRecord.run_id == run_id)
                .order_by(EvaluationRunSubjectRecord.position)
            )
        )
        if [trace_id for trace_id, _ in decisions] != subject_ids:
            return False
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
    return True


async def _finish_failed(run_id: UUID, attempt: int, message: str) -> bool:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(EvaluationRunRecord).where(EvaluationRunRecord.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.status != EvaluationRunStatus.RUNNING.value
            or run.attempt_count != attempt
        ):
            return False
        transition_run(run, EvaluationRunStatus.FAILED, error_message=message)
    return True


async def _requeue_claim(run_id: UUID, attempt: int) -> bool:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(EvaluationRunRecord).where(EvaluationRunRecord.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.status != EvaluationRunStatus.RUNNING.value
            or run.attempt_count != attempt
        ):
            return False
        run.status = EvaluationRunStatus.QUEUED.value
        run.started_at = None
        run.completed_at = None
        run.error_message = None
        run.last_enqueued_at = None
    return True


async def _record_permanent_failure(run_id: UUID, attempt: int, message: str) -> None:
    try:
        await _finish_failed(run_id, attempt, message)
    except SQLAlchemyError:
        try:
            await _requeue_claim(run_id, attempt)
        except SQLAlchemyError:
            logger.error(
                "evaluation_requeue_failed run_id=%s attempt=%d entry_point=dramatiq",
                run_id,
                attempt,
            )
        raise


async def process_evaluation_message(run_id: UUID) -> ProcessOutcome:
    async with SessionLocal() as session:
        claim = await claim_evaluation_run(session, run_id)
    if claim.outcome is not ClaimOutcome.CLAIMED:
        logger.info(
            "evaluation_message_ignored run_id=%s claim_outcome=%s entry_point=dramatiq",
            run_id,
            claim.outcome.value,
        )
        return ProcessOutcome.IGNORED

    try:
        work = await _load_claimed_work(run_id, claim.attempt)
        if work is None:
            await _finish_failed(run_id, claim.attempt, "durable evaluation subjects unavailable")
            return ProcessOutcome.FAILED
        decisions = _evaluate_work(work)
        completed = await finalize_evaluation_run(run_id, claim.attempt, decisions)
    except (InvalidEvaluatorConfiguration, UnsupportedEvaluatorKind):
        await _record_permanent_failure(run_id, claim.attempt, "invalid evaluator snapshot")
        logger.error(
            "evaluation_run_failed run_id=%s attempt=%d category=invalid_snapshot "
            "entry_point=dramatiq",
            run_id,
            claim.attempt,
        )
        return ProcessOutcome.FAILED
    except SQLAlchemyError:
        try:
            await _requeue_claim(run_id, claim.attempt)
        except SQLAlchemyError:
            logger.error(
                "evaluation_requeue_failed run_id=%s attempt=%d entry_point=dramatiq",
                run_id,
                claim.attempt,
            )
        raise
    except Exception:
        await _record_permanent_failure(run_id, claim.attempt, "evaluation execution failed")
        logger.error(
            "evaluation_run_failed run_id=%s attempt=%d category=internal entry_point=dramatiq",
            run_id,
            claim.attempt,
        )
        return ProcessOutcome.FAILED

    if not completed:
        return ProcessOutcome.IGNORED
    logger.info(
        "evaluation_run_completed run_id=%s evaluator_kind=%s attempt=%d "
        "subject_count=%d error_count=%d entry_point=dramatiq",
        run_id,
        work.evaluator_kind,
        claim.attempt,
        len(decisions),
        sum(decision.outcome.value == "error" for _, decision in decisions),
    )
    return ProcessOutcome.COMPLETED


async def recover_evaluation_runs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = MAX_RECOVERY_BATCH,
) -> list[UUID]:
    if not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError(f"limit must be between 1 and {MAX_RECOVERY_BATCH}")
    timestamp = now or datetime.now(UTC)
    queue_cutoff = timestamp - QUEUE_RECOVERY_AGE
    running_cutoff = timestamp - RUNNING_STALE_AGE

    async with session.begin():
        runs = (
            await session.scalars(
                select(EvaluationRunRecord)
                .where(
                    or_(
                        (
                            (EvaluationRunRecord.status == EvaluationRunStatus.QUEUED.value)
                            & (
                                EvaluationRunRecord.last_enqueued_at.is_(None)
                                | (EvaluationRunRecord.last_enqueued_at <= queue_cutoff)
                            )
                        ),
                        (
                            (EvaluationRunRecord.status == EvaluationRunStatus.RUNNING.value)
                            & (EvaluationRunRecord.started_at <= running_cutoff)
                        ),
                    )
                )
                .order_by(
                    func.coalesce(
                        EvaluationRunRecord.started_at,
                        EvaluationRunRecord.queued_at,
                        EvaluationRunRecord.created_at,
                    ),
                    EvaluationRunRecord.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not runs:
            return []
        run_ids = [run.id for run in runs]
        runs_with_results = set(
            await session.scalars(
                select(EvaluationResultRecord.run_id)
                .where(EvaluationResultRecord.run_id.in_(run_ids))
                .distinct()
            )
        )
        recoverable: list[UUID] = []
        for run in runs:
            if run.id in runs_with_results or run.attempt_count >= MAX_ATTEMPTS:
                if run.status == EvaluationRunStatus.QUEUED.value:
                    transition_run(run, EvaluationRunStatus.RUNNING, now=timestamp)
                transition_run(
                    run,
                    EvaluationRunStatus.FAILED,
                    now=timestamp,
                    error_message="evaluation recovery limit exceeded",
                )
                continue
            if run.status == EvaluationRunStatus.RUNNING.value:
                run.status = EvaluationRunStatus.QUEUED.value
                run.started_at = None
                run.completed_at = None
                run.error_message = None
            run.last_enqueued_at = timestamp
            recoverable.append(run.id)
        return recoverable


async def release_recovery_reservation(
    session: AsyncSession,
    run_id: UUID,
    reserved_at: datetime,
) -> None:
    async with session.begin():
        await session.execute(
            update(EvaluationRunRecord)
            .where(
                EvaluationRunRecord.id == run_id,
                EvaluationRunRecord.status == EvaluationRunStatus.QUEUED.value,
                EvaluationRunRecord.last_enqueued_at == reserved_at,
            )
            .values(last_enqueued_at=None)
        )
