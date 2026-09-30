from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal
from agentscope_api.models.calibration import CalibrationStudySubjectRecord
from agentscope_api.models.judging import JudgeResultRecord, JudgeRunRecord
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.judging import JudgeErrorCategory, JudgeRunStatus
from agentscope_api.services.judging import JudgeRunNotFound
from agentscope_api.services.openai_judge import (
    JudgeProvider,
    JudgeProviderError,
    JudgeSnapshot,
    OpenAIJudgeProvider,
    ProviderJudgment,
    create_openai_provider,
)

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_DURATION = timedelta(minutes=6)
HEARTBEAT_INTERVAL_SECONDS = 20
QUEUE_RECOVERY_AGE = timedelta(minutes=5)
MAX_RECOVERY_BATCH = 100
MAX_RETRY_DELAY_MS = 60_000
SUBJECT_CHUNK_SIZE = 20


class JudgeRunNotExecutable(Exception):
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
class Work:
    study_id: UUID
    provider: str
    model: str
    rubric: str
    timeout_seconds: int
    max_output_tokens: int


async def submit_judge_run(session: AsyncSession, run_id: UUID) -> None:
    now = datetime.now(UTC)
    async with session.begin():
        run = await session.scalar(
            select(JudgeRunRecord).where(JudgeRunRecord.id == run_id).with_for_update()
        )
        if run is None:
            raise JudgeRunNotFound
        if run.status == JudgeRunStatus.QUEUED.value:
            return
        if run.status != JudgeRunStatus.PENDING.value:
            raise JudgeRunNotExecutable
        run.status = JudgeRunStatus.QUEUED.value
        run.queued_at = now


async def mark_judge_run_enqueued(session: AsyncSession, run_id: UUID) -> None:
    async with session.begin():
        await session.execute(
            update(JudgeRunRecord)
            .where(
                JudgeRunRecord.id == run_id,
                JudgeRunRecord.status == JudgeRunStatus.QUEUED.value,
            )
            .values(last_enqueued_at=datetime.now(UTC))
        )


async def claim_judge_run(
    session: AsyncSession, run_id: UUID, *, now: datetime | None = None
) -> Claim:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        run = await session.scalar(
            select(JudgeRunRecord).where(JudgeRunRecord.id == run_id).with_for_update()
        )
        if run is None or run.status != JudgeRunStatus.QUEUED.value:
            return Claim(ClaimOutcome.IGNORED)
        if run.attempt_count >= MAX_ATTEMPTS:
            run.status = JudgeRunStatus.FAILED.value
            run.error_category = JudgeErrorCategory.ATTEMPTS_EXHAUSTED.value
            run.completed_at = timestamp
            return Claim(ClaimOutcome.EXHAUSTED, attempt=run.attempt_count)
        token = uuid4()
        run.attempt_count += 1
        run.status = JudgeRunStatus.RUNNING.value
        run.started_at = run.started_at or timestamp
        run.lease_token = token
        run.heartbeat_at = timestamp
        run.lease_expires_at = timestamp + LEASE_DURATION
        return Claim(ClaimOutcome.CLAIMED, token=token, attempt=run.attempt_count)


async def heartbeat_judge_run(
    session: AsyncSession,
    run_id: UUID,
    token: UUID,
    attempt: int,
    *,
    now: datetime | None = None,
) -> bool:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        result = await session.execute(
            update(JudgeRunRecord)
            .where(
                JudgeRunRecord.id == run_id,
                JudgeRunRecord.status == JudgeRunStatus.RUNNING.value,
                JudgeRunRecord.lease_token == token,
                JudgeRunRecord.attempt_count == attempt,
                JudgeRunRecord.lease_expires_at > timestamp,
            )
            .values(heartbeat_at=timestamp, lease_expires_at=timestamp + LEASE_DURATION)
        )
        return bool(getattr(result, "rowcount", 0))


async def _heartbeat_loop(run_id: UUID, token: UUID, attempt: int, stop: asyncio.Event) -> None:
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            return
        except TimeoutError:
            async with SessionLocal() as session:
                if not await heartbeat_judge_run(session, run_id, token, attempt):
                    return
        except Exception:
            logger.error("judge_heartbeat_failed run_id=%s attempt=%d", run_id, attempt)
            return


async def _load_work(run_id: UUID, token: UUID, attempt: int) -> Work | None:
    async with SessionLocal() as session:
        run = await session.scalar(
            select(JudgeRunRecord).where(
                JudgeRunRecord.id == run_id,
                JudgeRunRecord.status == JudgeRunStatus.RUNNING.value,
                JudgeRunRecord.lease_token == token,
                JudgeRunRecord.attempt_count == attempt,
            )
        )
        if run is None:
            return None
        return Work(
            study_id=run.study_id,
            provider=run.provider,
            model=run.model,
            rubric=run.rubric,
            timeout_seconds=run.timeout_seconds,
            max_output_tokens=run.max_output_tokens,
        )


async def _next_subjects(run_id: UUID, study_id: UUID) -> list[tuple[str, object | None]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(CalibrationStudySubjectRecord.trace_id, TraceRecord.output)
                .join(TraceRecord, TraceRecord.trace_id == CalibrationStudySubjectRecord.trace_id)
                .where(
                    CalibrationStudySubjectRecord.study_id == study_id,
                    ~exists().where(
                        (JudgeResultRecord.run_id == run_id)
                        & (JudgeResultRecord.trace_id == CalibrationStudySubjectRecord.trace_id)
                    ),
                )
                .order_by(CalibrationStudySubjectRecord.position)
                .limit(SUBJECT_CHUNK_SIZE)
            )
        ).all()
        return [(row[0], row[1]) for row in rows]


async def _persist_result(
    run_id: UUID,
    work: Work,
    trace_id: str,
    token: UUID,
    attempt: int,
    *,
    judgment: ProviderJudgment | None = None,
    error_category: JudgeErrorCategory | None = None,
) -> bool:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(JudgeRunRecord).where(JudgeRunRecord.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.status != JudgeRunStatus.RUNNING.value
            or run.lease_token != token
            or run.attempt_count != attempt
            or run.lease_expires_at is None
            or run.lease_expires_at <= datetime.now(UTC)
        ):
            return False
        if await session.get(JudgeResultRecord, (run_id, trace_id)) is not None:
            return True
        session.add(
            JudgeResultRecord(
                run_id=run_id,
                study_id=work.study_id,
                trace_id=trace_id,
                decision=judgment.decision.value if judgment else None,
                rationale=judgment.rationale if judgment else None,
                error_category=error_category.value if error_category else None,
                provider_request_id=judgment.provider_request_id if judgment else None,
                provider=work.provider,
                model=work.model,
                input_tokens=judgment.input_tokens if judgment else None,
                output_tokens=judgment.output_tokens if judgment else None,
                total_tokens=judgment.total_tokens if judgment else None,
                latency_ms=judgment.latency_ms if judgment else None,
                attempt_count=attempt,
            )
        )
    return True


async def _finish(run_id: UUID, token: UUID, attempt: int) -> bool:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(JudgeRunRecord).where(JudgeRunRecord.id == run_id).with_for_update()
        )
        if (
            run is None
            or run.status != JudgeRunStatus.RUNNING.value
            or run.lease_token != token
            or run.attempt_count != attempt
        ):
            return False
        subjects = await session.scalar(
            select(func.count())
            .select_from(CalibrationStudySubjectRecord)
            .where(CalibrationStudySubjectRecord.study_id == run.study_id)
        )
        results = await session.scalar(
            select(func.count())
            .select_from(JudgeResultRecord)
            .where(JudgeResultRecord.run_id == run_id)
        )
        if subjects != results:
            return False
        run.status = JudgeRunStatus.COMPLETED.value
        run.completed_at = datetime.now(UTC)
        run.lease_token = None
        run.lease_expires_at = None
    return True


async def _stop_attempt(
    run_id: UUID,
    token: UUID,
    attempt: int,
    category: JudgeErrorCategory,
    *,
    retryable: bool,
) -> ProcessOutcome:
    async with SessionLocal() as session, session.begin():
        run = await session.scalar(
            select(JudgeRunRecord).where(JudgeRunRecord.id == run_id).with_for_update()
        )
        if run is None or run.lease_token != token or run.attempt_count != attempt:
            return ProcessOutcome.IGNORED
        run.lease_token = None
        run.lease_expires_at = None
        if retryable and attempt < MAX_ATTEMPTS:
            run.status = JudgeRunStatus.QUEUED.value
            run.last_enqueued_at = None
            return ProcessOutcome.REQUEUED
        run.status = JudgeRunStatus.FAILED.value
        run.error_category = (
            JudgeErrorCategory.ATTEMPTS_EXHAUSTED.value if retryable else category.value
        )
        run.completed_at = datetime.now(UTC)
        return ProcessOutcome.FAILED


async def process_judge_message(
    run_id: UUID, provider: JudgeProvider | None = None
) -> ProcessOutcome:
    async with SessionLocal() as session:
        claim = await claim_judge_run(session, run_id)
    if claim.outcome is not ClaimOutcome.CLAIMED or claim.token is None:
        return ProcessOutcome.IGNORED
    work = await _load_work(run_id, claim.token, claim.attempt)
    if work is None:
        return ProcessOutcome.IGNORED
    created_provider: OpenAIJudgeProvider | None = None
    if provider is None:
        key = get_settings().openai_api_key
        if key is None:
            return await _stop_attempt(
                run_id,
                claim.token,
                claim.attempt,
                JudgeErrorCategory.INVALID_CONFIGURATION,
                retryable=False,
            )
        created_provider = create_openai_provider(key.get_secret_value())
        provider = created_provider

    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat_loop(run_id, claim.token, claim.attempt, stop))
    try:
        while subjects := await _next_subjects(run_id, work.study_id):
            for trace_id, candidate in subjects:
                if candidate is None:
                    if not await _persist_result(
                        run_id,
                        work,
                        trace_id,
                        claim.token,
                        claim.attempt,
                        error_category=JudgeErrorCategory.CANDIDATE_UNAVAILABLE,
                    ):
                        return ProcessOutcome.IGNORED
                    continue
                try:
                    judgment = await provider.judge(
                        JudgeSnapshot(
                            model=work.model,
                            rubric=work.rubric,
                            timeout_seconds=work.timeout_seconds,
                            max_output_tokens=work.max_output_tokens,
                        ),
                        candidate,
                    )
                except JudgeProviderError as error:
                    if error.retryable:
                        return await _stop_attempt(
                            run_id,
                            claim.token,
                            claim.attempt,
                            error.category,
                            retryable=True,
                        )
                    if error.category in {
                        JudgeErrorCategory.PROVIDER_AUTHENTICATION,
                        JudgeErrorCategory.INVALID_CONFIGURATION,
                    }:
                        return await _stop_attempt(
                            run_id,
                            claim.token,
                            claim.attempt,
                            error.category,
                            retryable=False,
                        )
                    if not await _persist_result(
                        run_id,
                        work,
                        trace_id,
                        claim.token,
                        claim.attempt,
                        error_category=error.category,
                    ):
                        return ProcessOutcome.IGNORED
                else:
                    if not await _persist_result(
                        run_id,
                        work,
                        trace_id,
                        claim.token,
                        claim.attempt,
                        judgment=judgment,
                    ):
                        return ProcessOutcome.IGNORED
        return (
            ProcessOutcome.COMPLETED
            if await _finish(run_id, claim.token, claim.attempt)
            else ProcessOutcome.IGNORED
        )
    finally:
        stop.set()
        await heartbeat
        if created_provider is not None:
            await created_provider.close()


async def recover_judge_runs(
    session: AsyncSession, *, now: datetime | None = None, limit: int = MAX_RECOVERY_BATCH
) -> list[UUID]:
    if not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError("invalid recovery limit")
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        runs = list(
            await session.scalars(
                select(JudgeRunRecord)
                .where(
                    or_(
                        (JudgeRunRecord.status == JudgeRunStatus.QUEUED.value)
                        & (
                            JudgeRunRecord.last_enqueued_at.is_(None)
                            | (JudgeRunRecord.last_enqueued_at <= timestamp - QUEUE_RECOVERY_AGE)
                        ),
                        (JudgeRunRecord.status == JudgeRunStatus.RUNNING.value)
                        & (JudgeRunRecord.lease_expires_at <= timestamp),
                    )
                )
                .order_by(JudgeRunRecord.created_at, JudgeRunRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        recoverable: list[UUID] = []
        for run in runs:
            if run.attempt_count >= MAX_ATTEMPTS:
                run.status = JudgeRunStatus.FAILED.value
                run.error_category = JudgeErrorCategory.ATTEMPTS_EXHAUSTED.value
                run.completed_at = timestamp
                run.lease_token = None
                run.lease_expires_at = None
                continue
            if run.status == JudgeRunStatus.RUNNING.value:
                run.status = JudgeRunStatus.QUEUED.value
                run.lease_token = None
                run.lease_expires_at = None
            run.last_enqueued_at = timestamp
            recoverable.append(run.id)
        return recoverable


async def release_judge_recovery_reservation(
    session: AsyncSession, run_id: UUID, reserved_at: datetime
) -> None:
    async with session.begin():
        await session.execute(
            update(JudgeRunRecord)
            .where(
                JudgeRunRecord.id == run_id,
                JudgeRunRecord.status == JudgeRunStatus.QUEUED.value,
                JudgeRunRecord.last_enqueued_at == reserved_at,
            )
            .values(last_enqueued_at=None)
        )


async def judge_retry_delay_ms(run_id: UUID) -> int:
    async with SessionLocal() as session:
        attempt = await session.scalar(
            select(JudgeRunRecord.attempt_count).where(JudgeRunRecord.id == run_id)
        )
    if attempt is None:
        return MAX_RETRY_DELAY_MS
    attempt_number = int(attempt)
    return min(MAX_RETRY_DELAY_MS, 5_000 << max(0, attempt_number - 1))
