from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import JsonValue
from sqlalchemy import exists, func, or_, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.database import SessionLocal
from agentscope_api.models.experiment import (
    ExperimentEvaluationConditionRecord,
    ExperimentRecord,
    ExperimentRunRecord,
    ExperimentRunResultRecord,
    ExperimentSubjectRecord,
)
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.experiment_runs import ExperimentRunStatus
from agentscope_api.schemas.experiments import ExperimentStatus
from agentscope_api.services.evaluator_engine import (
    EvaluationInput,
    Evaluator,
    InvalidEvaluatorConfiguration,
    UnsupportedEvaluatorKind,
    resolve_evaluator,
)
from agentscope_api.services.experiment_runs import ExperimentRunNotFound

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 4
LEASE_DURATION = timedelta(minutes=6)
QUEUE_RECOVERY_AGE = timedelta(minutes=5)
MAX_RECOVERY_BATCH = 100
DECISION_CHUNK_SIZE = 100


class ExperimentRunNotExecutable(Exception):
    pass


class ClaimOutcome(StrEnum):
    CLAIMED = "claimed"
    IGNORED = "ignored"
    EXHAUSTED = "exhausted"


class ProcessOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    REQUEUED = "requeued"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class Claim:
    outcome: ClaimOutcome
    token: UUID | None = None
    attempt: int = 0


@dataclass(frozen=True, slots=True)
class ExperimentDecision:
    subject_position: int
    variant: Literal["A", "B"]
    condition_position: int
    trace_id: str
    definition_id: UUID
    outcome: Literal["passed", "failed", "error"]
    score: float | None
    details: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class WorkItem:
    subject_position: int
    variant: Literal["A", "B"]
    condition_position: int
    trace_id: str
    definition_id: UUID
    evaluator_kind: str
    evaluator_config: dict[str, object]
    candidate: object
    captured: bool


async def submit_experiment_run(session: AsyncSession, run_id: UUID) -> None:
    timestamp = datetime.now(UTC)
    async with session.begin():
        run = await session.scalar(
            select(ExperimentRunRecord)
            .where(ExperimentRunRecord.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise ExperimentRunNotFound
        if run.status == ExperimentRunStatus.QUEUED.value:
            return
        if run.status != ExperimentRunStatus.PENDING.value:
            raise ExperimentRunNotExecutable
        run.status = ExperimentRunStatus.QUEUED.value
        run.queued_at = timestamp


async def mark_experiment_run_enqueued(session: AsyncSession, run_id: UUID) -> None:
    async with session.begin():
        await session.execute(
            update(ExperimentRunRecord)
            .where(
                ExperimentRunRecord.id == run_id,
                ExperimentRunRecord.status == ExperimentRunStatus.QUEUED.value,
            )
            .values(last_enqueued_at=datetime.now(UTC))
        )


async def claim_experiment_run(
    session: AsyncSession, run_id: UUID, *, now: datetime | None = None
) -> Claim:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        run = await session.scalar(
            select(ExperimentRunRecord)
            .where(ExperimentRunRecord.id == run_id)
            .with_for_update()
        )
        if run is None or run.status != ExperimentRunStatus.QUEUED.value:
            return Claim(ClaimOutcome.IGNORED)
        experiment = await session.scalar(
            select(ExperimentRecord)
            .where(ExperimentRecord.id == run.experiment_id)
            .with_for_update()
        )
        if experiment is None:
            return Claim(ClaimOutcome.IGNORED)
        if run.attempt_count >= MAX_ATTEMPTS:
            _fail_run(run, experiment, timestamp, "attempts_exhausted", "attempt limit exceeded")
            return Claim(ClaimOutcome.EXHAUSTED, attempt=run.attempt_count)
        token = uuid4()
        run.attempt_count += 1
        run.status = ExperimentRunStatus.RUNNING.value
        run.started_at = run.started_at or timestamp
        run.lease_token = token
        run.heartbeat_at = timestamp
        run.lease_expires_at = timestamp + LEASE_DURATION
        experiment.status = ExperimentStatus.RUNNING.value
        experiment.updated_at = timestamp
        return Claim(ClaimOutcome.CLAIMED, token=token, attempt=run.attempt_count)


async def _next_work(run_id: UUID, experiment_id: UUID) -> list[WorkItem]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    ExperimentSubjectRecord.position,
                    ExperimentSubjectRecord.variant_key,
                    ExperimentSubjectRecord.trace_id,
                    ExperimentEvaluationConditionRecord.position.label("condition_position"),
                    ExperimentEvaluationConditionRecord.definition_id,
                    ExperimentEvaluationConditionRecord.evaluator_kind,
                    ExperimentEvaluationConditionRecord.evaluator_config,
                    TraceRecord.output,
                )
                .select_from(ExperimentSubjectRecord)
                .join(TraceRecord, TraceRecord.trace_id == ExperimentSubjectRecord.trace_id)
                .join(ExperimentEvaluationConditionRecord, true())
                .where(
                    ExperimentSubjectRecord.experiment_id == experiment_id,
                    ExperimentEvaluationConditionRecord.experiment_id == experiment_id,
                    ~exists().where(
                        (ExperimentRunResultRecord.run_id == run_id)
                        & (
                            ExperimentRunResultRecord.subject_position
                            == ExperimentSubjectRecord.position
                        )
                        & (
                            ExperimentRunResultRecord.variant_key
                            == ExperimentSubjectRecord.variant_key
                        )
                        & (
                            ExperimentRunResultRecord.condition_position
                            == ExperimentEvaluationConditionRecord.position
                        )
                    ),
                )
                .order_by(
                    ExperimentSubjectRecord.position,
                    ExperimentSubjectRecord.variant_key,
                    ExperimentEvaluationConditionRecord.position,
                )
                .limit(DECISION_CHUNK_SIZE)
            )
        ).all()
    return [
        WorkItem(
            subject_position=row.position,
            variant=cast(Literal["A", "B"], row.variant_key),
            condition_position=row.condition_position,
            trace_id=row.trace_id,
            definition_id=row.definition_id,
            evaluator_kind=row.evaluator_kind,
            evaluator_config=row.evaluator_config,
            candidate=row.output,
            captured=row.output is not None,
        )
        for row in rows
    ]


def _evaluate(items: list[WorkItem]) -> list[ExperimentDecision]:
    evaluators: dict[int, Evaluator] = {}
    decisions: list[ExperimentDecision] = []
    for item in items:
        evaluator = evaluators.get(item.condition_position)
        if evaluator is None:
            evaluator = resolve_evaluator(item.evaluator_kind, item.evaluator_config)
            evaluators[item.condition_position] = evaluator
        decision = evaluator.evaluate(
            EvaluationInput(
                trace_id=item.trace_id,
                candidate=item.candidate,
                captured=item.captured,
            )
        )
        decisions.append(
            ExperimentDecision(
                subject_position=item.subject_position,
                variant=item.variant,
                condition_position=item.condition_position,
                trace_id=item.trace_id,
                definition_id=item.definition_id,
                outcome=decision.outcome.value,
                score=decision.score,
                details={"evaluator_kind": item.evaluator_kind, **decision.details},
            )
        )
    return decisions


async def persist_decision_chunk(
    run_id: UUID,
    token: UUID,
    attempt: int,
    decisions: list[ExperimentDecision],
    *,
    now: datetime | None = None,
) -> bool:
    timestamp = now or datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(ExperimentRunRecord)
            .where(ExperimentRunRecord.id == run_id)
            .with_for_update()
        )
        if (
            run is None
            or run.status != ExperimentRunStatus.RUNNING.value
            or run.lease_token != token
            or run.attempt_count != attempt
            or run.lease_expires_at is None
            or run.lease_expires_at <= timestamp
        ):
            return False
        for decision in decisions:
            identity = (
                run_id,
                decision.subject_position,
                decision.variant,
                decision.condition_position,
            )
            if await session.get(ExperimentRunResultRecord, identity) is not None:
                continue
            session.add(
                ExperimentRunResultRecord(
                    run_id=run_id,
                    experiment_id=run.experiment_id,
                    subject_position=decision.subject_position,
                    variant_key=decision.variant,
                    condition_position=decision.condition_position,
                    trace_id=decision.trace_id,
                    definition_id=decision.definition_id,
                    outcome=decision.outcome,
                    score=decision.score,
                    details=decision.details,
                    attempt_count=attempt,
                )
            )
        run.heartbeat_at = timestamp
        run.lease_expires_at = timestamp + LEASE_DURATION
    return True


async def finish_experiment_run(run_id: UUID, token: UUID, attempt: int) -> bool:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(ExperimentRunRecord)
            .where(ExperimentRunRecord.id == run_id)
            .with_for_update()
        )
        timestamp = datetime.now(UTC)
        if (
            run is None
            or run.status != ExperimentRunStatus.RUNNING.value
            or run.lease_token != token
            or run.attempt_count != attempt
            or run.lease_expires_at is None
            or run.lease_expires_at <= timestamp
        ):
            return False
        result_count = await session.scalar(
            select(func.count())
            .select_from(ExperimentRunResultRecord)
            .where(ExperimentRunResultRecord.run_id == run_id)
        )
        if result_count != run.expected_decision_count:
            return False
        run.status = ExperimentRunStatus.COMPLETED.value
        run.completed_at = timestamp
        run.lease_token = None
        run.lease_expires_at = None
        experiment = await session.get(ExperimentRecord, run.experiment_id, with_for_update=True)
        if experiment is not None:
            experiment.status = ExperimentStatus.COMPLETED.value
            experiment.updated_at = timestamp
    return True


def _fail_run(
    run: ExperimentRunRecord,
    experiment: ExperimentRecord,
    timestamp: datetime,
    category: str,
    message: str,
) -> None:
    run.status = ExperimentRunStatus.FAILED.value
    run.error_category = category
    run.error_message = message[:4_000]
    run.completed_at = timestamp
    run.lease_token = None
    run.lease_expires_at = None
    experiment.status = ExperimentStatus.FAILED.value
    experiment.updated_at = timestamp


async def _stop_attempt(
    run_id: UUID,
    token: UUID,
    attempt: int,
    category: str,
    message: str,
    *,
    retryable: bool,
) -> ProcessOutcome:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(ExperimentRunRecord)
            .where(ExperimentRunRecord.id == run_id)
            .with_for_update()
        )
        timestamp = datetime.now(UTC)
        if (
            run is None
            or run.status != ExperimentRunStatus.RUNNING.value
            or run.lease_token != token
            or run.attempt_count != attempt
            or run.lease_expires_at is None
            or run.lease_expires_at <= timestamp
        ):
            return ProcessOutcome.IGNORED
        experiment = await session.get(ExperimentRecord, run.experiment_id, with_for_update=True)
        if experiment is None:
            return ProcessOutcome.IGNORED
        run.lease_token = None
        run.lease_expires_at = None
        if retryable and attempt < MAX_ATTEMPTS:
            run.status = ExperimentRunStatus.QUEUED.value
            run.last_enqueued_at = None
            return ProcessOutcome.REQUEUED
        _fail_run(run, experiment, timestamp, category, message)
        return ProcessOutcome.FAILED


async def process_experiment_message(run_id: UUID) -> ProcessOutcome:
    async with SessionLocal() as session:
        claim = await claim_experiment_run(session, run_id)
    if claim.outcome is not ClaimOutcome.CLAIMED or claim.token is None:
        return ProcessOutcome.IGNORED
    async with SessionLocal() as session:
        run = await session.get(ExperimentRunRecord, run_id)
        if run is None:
            return ProcessOutcome.IGNORED
        experiment_id = run.experiment_id
    try:
        while items := await _next_work(run_id, experiment_id):
            if not await persist_decision_chunk(
                run_id, claim.token, claim.attempt, _evaluate(items)
            ):
                return ProcessOutcome.IGNORED
        return (
            ProcessOutcome.COMPLETED
            if await finish_experiment_run(run_id, claim.token, claim.attempt)
            else ProcessOutcome.IGNORED
        )
    except (InvalidEvaluatorConfiguration, UnsupportedEvaluatorKind):
        return await _stop_attempt(
            run_id,
            claim.token,
            claim.attempt,
            "invalid_snapshot",
            "invalid frozen evaluation condition",
            retryable=False,
        )
    except Exception:
        logger.exception("experiment_run_execution_failed run_id=%s", run_id)
        return await _stop_attempt(
            run_id,
            claim.token,
            claim.attempt,
            "execution_failure",
            "experiment execution failed",
            retryable=True,
        )


async def recover_experiment_runs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = MAX_RECOVERY_BATCH,
) -> list[UUID]:
    if not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError("invalid recovery limit")
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        runs = list(
            await session.scalars(
                select(ExperimentRunRecord)
                .where(
                    or_(
                        (ExperimentRunRecord.status == ExperimentRunStatus.QUEUED.value)
                        & (
                            ExperimentRunRecord.last_enqueued_at.is_(None)
                            | (
                                ExperimentRunRecord.last_enqueued_at
                                <= timestamp - QUEUE_RECOVERY_AGE
                            )
                        ),
                        (ExperimentRunRecord.status == ExperimentRunStatus.RUNNING.value)
                        & (ExperimentRunRecord.lease_expires_at <= timestamp),
                    )
                )
                .order_by(ExperimentRunRecord.created_at, ExperimentRunRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        recoverable: list[UUID] = []
        for run in runs:
            if run.attempt_count >= MAX_ATTEMPTS:
                experiment = await session.get(
                    ExperimentRecord, run.experiment_id, with_for_update=True
                )
                if experiment is not None:
                    _fail_run(
                        run,
                        experiment,
                        timestamp,
                        "attempts_exhausted",
                        "experiment recovery attempt limit exceeded",
                    )
                continue
            if run.status == ExperimentRunStatus.RUNNING.value:
                run.status = ExperimentRunStatus.QUEUED.value
                run.lease_token = None
                run.lease_expires_at = None
            run.last_enqueued_at = timestamp
            recoverable.append(run.id)
        return recoverable


async def release_experiment_recovery_reservation(
    session: AsyncSession, run_id: UUID, reserved_at: datetime
) -> None:
    async with session.begin():
        await session.execute(
            update(ExperimentRunRecord)
            .where(
                ExperimentRunRecord.id == run_id,
                ExperimentRunRecord.status == ExperimentRunStatus.QUEUED.value,
                ExperimentRunRecord.last_enqueued_at == reserved_at,
            )
            .values(last_enqueued_at=None)
        )
