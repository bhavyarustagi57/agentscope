from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal
from agentscope_api.models.bisection import BisectionCommitRecord, BisectionSessionRecord
from agentscope_api.models.bisection_execution import (
    BisectionExecutionAttemptRecord,
    BisectionExecutionRunRecord,
    BisectionExecutionTargetRecord,
)
from agentscope_api.schemas.bisection_execution import (
    BisectionExecutionAttempt,
    BisectionExecutionRun,
    BisectionExecutionRunCreate,
    BisectionExecutionRunList,
    BisectionExecutionRunListParams,
    BisectionExecutionRunSubmit,
    BisectionExecutionTarget,
    ProbeConfiguration,
)
from agentscope_api.services.bisection_worktree import (
    ProbeResult,
    WorktreeProbeRunner,
    classify_exit_code,
)

logger = logging.getLogger(__name__)
LEASE_DURATION = timedelta(minutes=6)
HEARTBEAT_INTERVAL_SECONDS = 60
QUEUE_RECOVERY_AGE = timedelta(minutes=5)
MAX_RECOVERY_BATCH = 100


class BisectionSessionNotFound(Exception):
    pass


class BisectionSessionNotReady(Exception):
    pass


class BisectionExecutionRunNotFound(Exception):
    pass


class BisectionExecutionRunConflict(Exception):
    pass


class BisectionExecutionRunNotPending(Exception):
    pass


class CommitNotInBisectionPlan(Exception):
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
    run_id: UUID | None = None
    commit_sha: str | None = None
    token: UUID | None = None
    attempt: int = 0


@dataclass(frozen=True, slots=True)
class Work:
    run_id: UUID
    commit_sha: str
    token: UUID
    attempt: int
    repository_root: str
    configuration: ProbeConfiguration


async def _run_schema(
    session: AsyncSession, record: BisectionExecutionRunRecord
) -> BisectionExecutionRun:
    targets = list(
        await session.scalars(
            select(BisectionExecutionTargetRecord)
            .where(BisectionExecutionTargetRecord.run_id == record.id)
            .order_by(BisectionExecutionTargetRecord.commit_position)
        )
    )
    attempts = list(
        await session.scalars(
            select(BisectionExecutionAttemptRecord)
            .where(BisectionExecutionAttemptRecord.run_id == record.id)
            .order_by(
                BisectionExecutionAttemptRecord.commit_sha,
                BisectionExecutionAttemptRecord.attempt_number,
            )
        )
    )
    by_commit: dict[str, list[BisectionExecutionAttempt]] = {}
    for attempt in attempts:
        by_commit.setdefault(attempt.commit_sha, []).append(
            BisectionExecutionAttempt.model_validate(attempt)
        )
    target_schemas = [
        BisectionExecutionTarget(
            **BisectionExecutionTarget.model_validate(target).model_dump(exclude={"attempts"}),
            attempts=by_commit.get(target.commit_sha, []),
        )
        for target in targets
    ]
    outcomes = [target.outcome for target in targets]
    valid = sum(outcome in {"pass", "regression"} for outcome in outcomes)
    indeterminate = outcomes.count("indeterminate")
    failures = outcomes.count("execution_failed")
    terminal = valid + indeterminate + failures
    return BisectionExecutionRun(
        id=record.id,
        session_id=record.session_id,
        status=record.status,
        configuration_schema_version="1",
        configuration=ProbeConfiguration.model_validate(record.configuration),
        repository_fingerprint=record.repository_fingerprint,
        repository_root=record.repository_root,
        planned_commit_count=record.planned_commit_count,
        requested_commit_count=record.requested_commit_count,
        completed_valid_count=valid,
        regression_count=outcomes.count("regression"),
        indeterminate_count=indeterminate,
        execution_failure_count=failures,
        remaining_count=max(0, record.requested_commit_count - terminal),
        error_category=record.error_category,
        error_message=record.error_message,
        created_at=record.created_at,
        queued_at=record.queued_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        targets=target_schemas,
    )


async def create_execution_run(
    session: AsyncSession, session_id: UUID, payload: BisectionExecutionRunCreate
) -> BisectionExecutionRun:
    async with session.begin():
        bisection = await session.get(BisectionSessionRecord, session_id, with_for_update=True)
        if bisection is None:
            raise BisectionSessionNotFound
        if bisection.status != "ready":
            raise BisectionSessionNotReady
        active = await session.scalar(
            select(BisectionExecutionRunRecord.id).where(
                BisectionExecutionRunRecord.session_id == session_id,
                BisectionExecutionRunRecord.status.in_(("pending", "queued", "running")),
            )
        )
        if active is not None:
            raise BisectionExecutionRunConflict
        record = BisectionExecutionRunRecord(
            session_id=session_id,
            configuration=payload.configuration.model_dump(mode="json"),
            repository_fingerprint=bisection.repository_fingerprint,
            repository_root=bisection.repository_root,
            planned_commit_count=bisection.commit_count,
        )
        session.add(record)
        await session.flush()
        response = await _run_schema(session, record)
    return response


async def submit_execution_run(
    session: AsyncSession, run_id: UUID, payload: BisectionExecutionRunSubmit
) -> None:
    timestamp = datetime.now(UTC)
    async with session.begin():
        run = await session.get(BisectionExecutionRunRecord, run_id, with_for_update=True)
        if run is None:
            raise BisectionExecutionRunNotFound
        if run.status == "queued":
            existing = set(
                await session.scalars(
                    select(BisectionExecutionTargetRecord.commit_sha).where(
                        BisectionExecutionTargetRecord.run_id == run_id
                    )
                )
            )
            if existing == set(payload.commit_shas):
                return
            raise BisectionExecutionRunNotPending
        if run.status != "pending":
            raise BisectionExecutionRunNotPending
        commits = list(
            await session.scalars(
                select(BisectionCommitRecord).where(
                    BisectionCommitRecord.session_id == run.session_id,
                    BisectionCommitRecord.commit_sha.in_(payload.commit_shas),
                )
            )
        )
        by_sha = {commit.commit_sha: commit for commit in commits}
        if len(by_sha) != len(payload.commit_shas):
            raise CommitNotInBisectionPlan
        for commit_sha in payload.commit_shas:
            commit = by_sha[commit_sha]
            session.add(
                BisectionExecutionTargetRecord(
                    run_id=run.id,
                    session_id=run.session_id,
                    commit_sha=commit.commit_sha,
                    commit_position=commit.position,
                )
            )
        run.requested_commit_count = len(payload.commit_shas)
        run.status = "queued"
        run.queued_at = timestamp


async def ensure_execution_run_for_commit(
    session_id: UUID, configuration: ProbeConfiguration, commit_sha: str
) -> tuple[UUID, bool]:
    timestamp = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        bisection = await session.get(BisectionSessionRecord, session_id, with_for_update=True)
        if bisection is None:
            raise BisectionSessionNotFound
        commit = await session.scalar(
            select(BisectionCommitRecord).where(
                BisectionCommitRecord.session_id == session_id,
                BisectionCommitRecord.commit_sha == commit_sha,
            )
        )
        if commit is None:
            raise CommitNotInBisectionPlan
        active = await session.scalar(
            select(BisectionExecutionRunRecord)
            .where(
                BisectionExecutionRunRecord.session_id == session_id,
                BisectionExecutionRunRecord.status.in_(("pending", "queued", "running")),
            )
            .with_for_update()
        )
        if active is not None:
            target = await session.get(BisectionExecutionTargetRecord, (active.id, commit_sha))
            if (
                active.configuration_schema_version == "1"
                and active.configuration == configuration.model_dump(mode="json")
                and target is not None
            ):
                return active.id, False
            raise BisectionExecutionRunConflict
        run = BisectionExecutionRunRecord(
            session_id=session_id,
            status="queued",
            configuration=configuration.model_dump(mode="json"),
            repository_fingerprint=bisection.repository_fingerprint,
            repository_root=bisection.repository_root,
            planned_commit_count=bisection.commit_count,
            requested_commit_count=1,
            queued_at=timestamp,
        )
        session.add(run)
        await session.flush()
        session.add(
            BisectionExecutionTargetRecord(
                run_id=run.id,
                session_id=session_id,
                commit_sha=commit.commit_sha,
                commit_position=commit.position,
            )
        )
        return run.id, True


async def mark_execution_run_enqueued(session: AsyncSession, run_id: UUID) -> None:
    async with session.begin():
        await session.execute(
            update(BisectionExecutionRunRecord)
            .where(
                BisectionExecutionRunRecord.id == run_id,
                BisectionExecutionRunRecord.status == "queued",
            )
            .values(last_enqueued_at=datetime.now(UTC))
        )


async def get_execution_run(session: AsyncSession, run_id: UUID) -> BisectionExecutionRun | None:
    record = await session.get(BisectionExecutionRunRecord, run_id)
    return None if record is None else await _run_schema(session, record)


async def list_execution_runs(
    session: AsyncSession,
    session_id: UUID,
    params: BisectionExecutionRunListParams,
) -> BisectionExecutionRunList:
    if await session.get(BisectionSessionRecord, session_id) is None:
        raise BisectionSessionNotFound
    statement = select(BisectionExecutionRunRecord).where(
        BisectionExecutionRunRecord.session_id == session_id
    )
    if params.status is not None:
        statement = statement.where(BisectionExecutionRunRecord.status == params.status.value)
    records = list(
        await session.scalars(
            statement.order_by(
                BisectionExecutionRunRecord.created_at.desc(),
                BisectionExecutionRunRecord.id.desc(),
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return BisectionExecutionRunList(
        items=[await _run_schema(session, record) for record in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )


async def claim_execution_target(
    session: AsyncSession, run_id: UUID, *, now: datetime | None = None
) -> Claim:
    timestamp = now or datetime.now(UTC)
    max_attempts = get_settings().bisection_execution_max_attempts
    async with session.begin():
        run = await session.get(BisectionExecutionRunRecord, run_id, with_for_update=True)
        if run is None or run.status != "queued":
            return Claim(ClaimOutcome.IGNORED)
        target = await session.scalar(
            select(BisectionExecutionTargetRecord)
            .where(
                BisectionExecutionTargetRecord.run_id == run_id,
                BisectionExecutionTargetRecord.status == "queued",
            )
            .order_by(BisectionExecutionTargetRecord.commit_position)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if target is None:
            return Claim(ClaimOutcome.IGNORED)
        if target.attempt_count >= max_attempts:
            _fail_target(target, timestamp, "attempts_exhausted", "attempt limit exceeded")
            _fail_run(
                run, timestamp, "attempts_exhausted", "commit execution attempt limit exceeded"
            )
            return Claim(ClaimOutcome.EXHAUSTED, run_id=run_id, commit_sha=target.commit_sha)
        token = uuid4()
        target.attempt_count += 1
        target.status = "running"
        target.lease_token = token
        target.heartbeat_at = timestamp
        target.lease_expires_at = timestamp + LEASE_DURATION
        target.started_at = target.started_at or timestamp
        run.status = "running"
        run.started_at = run.started_at or timestamp
        session.add(
            BisectionExecutionAttemptRecord(
                run_id=run_id,
                commit_sha=target.commit_sha,
                attempt_number=target.attempt_count,
                lease_token=token,
                started_at=timestamp,
            )
        )
        return Claim(
            ClaimOutcome.CLAIMED,
            run_id=run_id,
            commit_sha=target.commit_sha,
            token=token,
            attempt=target.attempt_count,
        )


async def heartbeat_execution_target(
    session: AsyncSession,
    run_id: UUID,
    commit_sha: str,
    token: UUID,
    attempt: int,
    *,
    now: datetime | None = None,
) -> bool:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        result = await session.execute(
            update(BisectionExecutionTargetRecord)
            .where(
                BisectionExecutionTargetRecord.run_id == run_id,
                BisectionExecutionTargetRecord.commit_sha == commit_sha,
                BisectionExecutionTargetRecord.status == "running",
                BisectionExecutionTargetRecord.lease_token == token,
                BisectionExecutionTargetRecord.attempt_count == attempt,
                BisectionExecutionTargetRecord.lease_expires_at > timestamp,
            )
            .values(heartbeat_at=timestamp, lease_expires_at=timestamp + LEASE_DURATION)
        )
        return bool(getattr(result, "rowcount", 0))


async def _heartbeat_loop(claim: Claim, stop: asyncio.Event) -> None:
    assert claim.run_id and claim.commit_sha and claim.token
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            return
        except TimeoutError:
            async with SessionLocal() as session:
                if not await heartbeat_execution_target(
                    session,
                    claim.run_id,
                    claim.commit_sha,
                    claim.token,
                    claim.attempt,
                ):
                    return
        except Exception:
            logger.error(
                "bisection_execution_heartbeat_failed run_id=%s commit_sha=%s attempt=%d",
                claim.run_id,
                claim.commit_sha,
                claim.attempt,
            )
            return


async def _load_work(claim: Claim) -> Work | None:
    assert claim.run_id and claim.commit_sha and claim.token
    async with SessionLocal() as session:
        row = (
            await session.execute(
                select(BisectionExecutionRunRecord, BisectionExecutionTargetRecord)
                .join(
                    BisectionExecutionTargetRecord,
                    BisectionExecutionTargetRecord.run_id == BisectionExecutionRunRecord.id,
                )
                .where(
                    BisectionExecutionRunRecord.id == claim.run_id,
                    BisectionExecutionTargetRecord.commit_sha == claim.commit_sha,
                    BisectionExecutionTargetRecord.status == "running",
                    BisectionExecutionTargetRecord.lease_token == claim.token,
                    BisectionExecutionTargetRecord.attempt_count == claim.attempt,
                )
            )
        ).one_or_none()
    if row is None:
        return None
    run, _ = row
    return Work(
        run_id=run.id,
        commit_sha=claim.commit_sha,
        token=claim.token,
        attempt=claim.attempt,
        repository_root=run.repository_root,
        configuration=ProbeConfiguration.model_validate(run.configuration),
    )


def _copy_result(record: Any, result: ProbeResult) -> None:
    for field in (
        "exit_code",
        "duration_ms",
        "stdout",
        "stderr",
        "stdout_truncated",
        "stderr_truncated",
        "timed_out",
        "failure_kind",
        "failure_message",
        "cleanup_failed",
        "cleanup_message",
    ):
        setattr(record, field, getattr(result, field))


def _fail_target(
    target: BisectionExecutionTargetRecord, timestamp: datetime, kind: str, message: str
) -> None:
    target.status = "execution_failed"
    target.outcome = "execution_failed"
    target.finished_at = timestamp
    target.failure_kind = kind
    target.failure_message = message[:1_000]
    target.lease_token = None
    target.lease_expires_at = None


def _fail_run(
    run: BisectionExecutionRunRecord, timestamp: datetime, category: str, message: str
) -> None:
    run.status = "failed"
    run.error_category = category
    run.error_message = message[:4_000]
    run.completed_at = timestamp


async def persist_execution_result(claim: Claim, result: ProbeResult) -> ProcessOutcome:
    assert claim.run_id and claim.commit_sha and claim.token
    timestamp = datetime.now(UTC)
    max_attempts = get_settings().bisection_execution_max_attempts
    async with SessionLocal() as session, session.begin():
        run = await session.get(BisectionExecutionRunRecord, claim.run_id, with_for_update=True)
        target = await session.get(
            BisectionExecutionTargetRecord,
            (claim.run_id, claim.commit_sha),
            with_for_update=True,
        )
        attempt = await session.scalar(
            select(BisectionExecutionAttemptRecord).where(
                BisectionExecutionAttemptRecord.lease_token == claim.token
            )
        )
        if (
            run is None
            or target is None
            or attempt is None
            or target.status != "running"
            or target.lease_token != claim.token
            or target.attempt_count != claim.attempt
            or target.lease_expires_at is None
            or target.lease_expires_at <= timestamp
        ):
            return ProcessOutcome.IGNORED
        _copy_result(attempt, result)
        attempt.finished_at = timestamp
        if result.failure_kind is not None:
            attempt.status = "failed"
            attempt.outcome = "execution_failed"
            target.lease_token = None
            target.lease_expires_at = None
            if claim.attempt < max_attempts:
                target.status = "queued"
                run.status = "queued"
                run.last_enqueued_at = None
                return ProcessOutcome.REQUEUED
            _copy_result(target, result)
            _fail_target(
                target, timestamp, result.failure_kind, result.failure_message or "execution failed"
            )
            _fail_run(run, timestamp, "execution_failed", "commit execution attempts exhausted")
            return ProcessOutcome.FAILED

        outcome = classify_exit_code(result.exit_code if result.exit_code is not None else -1)
        attempt.status = "completed"
        attempt.outcome = outcome
        _copy_result(target, result)
        target.status = "completed"
        target.outcome = outcome
        target.finished_at = timestamp
        target.lease_token = None
        target.lease_expires_at = None
        remaining = await session.scalar(
            select(func.count())
            .select_from(BisectionExecutionTargetRecord)
            .where(
                BisectionExecutionTargetRecord.run_id == claim.run_id,
                BisectionExecutionTargetRecord.status.in_(("queued", "running")),
            )
        )
        if remaining:
            run.status = "queued"
            run.last_enqueued_at = None
            return ProcessOutcome.REQUEUED
        run.status = "completed"
        run.completed_at = timestamp
        return ProcessOutcome.COMPLETED


async def process_execution_message(run_id: UUID) -> ProcessOutcome:
    async with SessionLocal() as session:
        claim = await claim_execution_target(session, run_id)
    if claim.outcome is not ClaimOutcome.CLAIMED or claim.token is None:
        return ProcessOutcome.IGNORED
    work = await _load_work(claim)
    if work is None:
        return ProcessOutcome.IGNORED
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat_loop(claim, stop))
    try:
        try:
            result = await asyncio.to_thread(
                WorktreeProbeRunner().execute,
                repository_root=work.repository_root,
                work_root=get_settings().bisection_work_root,
                run_id=work.run_id,
                commit_sha=work.commit_sha,
                token=work.token,
                configuration=work.configuration,
            )
        except Exception:
            logger.exception(
                "bisection_execution_failed run_id=%s commit_sha=%s",
                work.run_id,
                work.commit_sha,
            )
            result = ProbeResult(
                exit_code=None,
                duration_ms=0,
                failure_kind="internal_failure",
                failure_message="commit execution failed",
            )
        return await persist_execution_result(claim, result)
    finally:
        stop.set()
        await heartbeat


async def recover_execution_runs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = MAX_RECOVERY_BATCH,
) -> list[UUID]:
    if not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError("invalid recovery limit")
    timestamp = now or datetime.now(UTC)
    max_attempts = get_settings().bisection_execution_max_attempts
    async with session.begin():
        runs = list(
            await session.scalars(
                select(BisectionExecutionRunRecord)
                .where(
                    or_(
                        (BisectionExecutionRunRecord.status == "queued")
                        & (
                            BisectionExecutionRunRecord.last_enqueued_at.is_(None)
                            | (
                                BisectionExecutionRunRecord.last_enqueued_at
                                <= timestamp - QUEUE_RECOVERY_AGE
                            )
                        ),
                        (BisectionExecutionRunRecord.status == "running")
                        & exists().where(
                            (
                                BisectionExecutionTargetRecord.run_id
                                == BisectionExecutionRunRecord.id
                            )
                            & (BisectionExecutionTargetRecord.status == "running")
                            & (BisectionExecutionTargetRecord.lease_expires_at <= timestamp)
                        ),
                    )
                )
                .order_by(BisectionExecutionRunRecord.created_at, BisectionExecutionRunRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        recovered: list[UUID] = []
        for run in runs:
            expired = list(
                await session.scalars(
                    select(BisectionExecutionTargetRecord)
                    .where(
                        BisectionExecutionTargetRecord.run_id == run.id,
                        BisectionExecutionTargetRecord.status == "running",
                        BisectionExecutionTargetRecord.lease_expires_at <= timestamp,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            exhausted = False
            for target in expired:
                attempt = await session.scalar(
                    select(BisectionExecutionAttemptRecord).where(
                        BisectionExecutionAttemptRecord.lease_token == target.lease_token
                    )
                )
                if attempt is not None and attempt.status == "running":
                    attempt.status = "failed"
                    attempt.outcome = "execution_failed"
                    attempt.finished_at = timestamp
                    attempt.failure_kind = "lease_expired"
                    attempt.failure_message = "worker lease expired"
                target.lease_token = None
                target.lease_expires_at = None
                if target.attempt_count >= max_attempts:
                    _fail_target(target, timestamp, "attempts_exhausted", "attempt limit exceeded")
                    exhausted = True
                else:
                    target.status = "queued"
            if exhausted:
                _fail_run(
                    run, timestamp, "attempts_exhausted", "commit execution attempts exhausted"
                )
                continue
            run.status = "queued"
            run.last_enqueued_at = timestamp
            recovered.append(run.id)
        return recovered


async def release_execution_recovery_reservation(
    session: AsyncSession, run_id: UUID, reserved_at: datetime
) -> None:
    async with session.begin():
        await session.execute(
            update(BisectionExecutionRunRecord)
            .where(
                BisectionExecutionRunRecord.id == run_id,
                BisectionExecutionRunRecord.status == "queued",
                BisectionExecutionRunRecord.last_enqueued_at == reserved_at,
            )
            .values(last_enqueued_at=None)
        )
