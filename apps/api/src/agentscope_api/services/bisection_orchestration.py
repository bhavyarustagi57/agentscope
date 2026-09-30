from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.database import SessionLocal
from agentscope_api.models.bisection import BisectionCommitRecord, BisectionSessionRecord
from agentscope_api.models.bisection_analysis import (
    BisectionAnalysisRecord,
    BisectionAnalysisStepRecord,
)
from agentscope_api.models.bisection_execution import (
    BisectionExecutionRunRecord,
    BisectionExecutionTargetRecord,
)
from agentscope_api.schemas.bisection_execution import ProbeConfiguration
from agentscope_api.services.bisection_analysis import (
    contradictory_evidence,
    select_position,
    updated_boundaries,
)
from agentscope_api.services.bisection_execution import (
    BisectionExecutionRunConflict,
    ensure_execution_run_for_commit,
)

LEASE_DURATION = timedelta(minutes=2)
QUEUE_RECOVERY_AGE = timedelta(minutes=5)
MAX_RECOVERY_BATCH = 100


class AnalysisProcessOutcome(StrEnum):
    ATTRIBUTED = "attributed"
    INCONCLUSIVE = "inconclusive"
    REQUEUED = "requeued"
    WAITING = "waiting"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class AnalysisClaim:
    analysis_id: UUID
    token: UUID


@dataclass(frozen=True, slots=True)
class AnalysisProcessResult:
    outcome: AnalysisProcessOutcome
    execution_run_id: UUID | None = None
    execution_created: bool = False


@dataclass(frozen=True, slots=True)
class NeedExecution:
    position: int
    commit_sha: str


async def claim_analysis(
    session: AsyncSession, analysis_id: UUID, *, now: datetime | None = None
) -> AnalysisClaim | None:
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        record = await session.get(BisectionAnalysisRecord, analysis_id, with_for_update=True)
        if record is None or record.status not in {"queued", "waiting"}:
            return None
        token = uuid4()
        record.status = "running"
        record.lease_token = token
        record.lease_expires_at = timestamp + LEASE_DURATION
        record.started_at = record.started_at or timestamp
        return AnalysisClaim(analysis_id, token)


def _claim_is_current(
    record: BisectionAnalysisRecord, claim: AnalysisClaim, timestamp: datetime
) -> bool:
    return (
        record.status == "running"
        and record.lease_token == claim.token
        and record.lease_expires_at is not None
        and record.lease_expires_at > timestamp
    )


async def _compatible_targets(
    session: AsyncSession, record: BisectionAnalysisRecord
) -> list[tuple[BisectionExecutionTargetRecord, BisectionExecutionRunRecord]]:
    rows = (
        await session.execute(
            select(BisectionExecutionTargetRecord, BisectionExecutionRunRecord)
            .join(
                BisectionExecutionRunRecord,
                BisectionExecutionRunRecord.id == BisectionExecutionTargetRecord.run_id,
            )
            .where(
                BisectionExecutionRunRecord.session_id == record.session_id,
                BisectionExecutionRunRecord.configuration_schema_version == "1",
                BisectionExecutionRunRecord.configuration == record.configuration,
                BisectionExecutionTargetRecord.status.in_(("completed", "execution_failed")),
            )
            .order_by(
                BisectionExecutionRunRecord.created_at.desc(),
                BisectionExecutionRunRecord.id.desc(),
            )
        )
    ).all()
    return [(row[0], row[1]) for row in rows]


def _decision(outcome: str, *, candidate_confirmation: bool) -> str:
    if candidate_confirmation and outcome in {"pass", "regression"}:
        return "confirm_candidate"
    return {
        "pass": "advance_good",
        "regression": "retreat_bad",
        "indeterminate": "skip_indeterminate",
        "execution_failed": "skip_execution_failed",
    }[outcome]


def _release_lease(record: BisectionAnalysisRecord) -> None:
    record.lease_token = None
    record.lease_expires_at = None


def _finish_inconclusive(
    record: BisectionAnalysisRecord, timestamp: datetime, reason: str, message: str
) -> AnalysisProcessResult:
    record.status = "inconclusive"
    record.terminal_reason = reason
    record.terminal_message = message[:1_000]
    record.completed_at = timestamp
    _release_lease(record)
    return AnalysisProcessResult(AnalysisProcessOutcome.INCONCLUSIVE)


def _commit_snapshot(commit: BisectionCommitRecord) -> dict[str, object]:
    return {
        "sha": commit.commit_sha,
        "position": commit.position,
        "committed_at": commit.committed_at.isoformat(),
        "subject": commit.subject,
        "parent_shas": commit.parent_shas,
    }


async def _finish_attributed(
    session: AsyncSession,
    record: BisectionAnalysisRecord,
    bisection: BisectionSessionRecord,
    commits: dict[int, BisectionCommitRecord],
    timestamp: datetime,
) -> AnalysisProcessResult:
    record.status = "attributed"
    record.final_good_position = record.good_position
    record.final_good_commit_sha = record.good_commit_sha
    record.final_bad_position = record.bad_position
    record.final_bad_commit_sha = record.bad_commit_sha
    record.final_good_snapshot = (
        {
            "sha": bisection.baseline_commit_sha,
            "position": -1,
            "provenance": bisection.baseline_provenance,
        }
        if record.good_position == -1
        else _commit_snapshot(commits[record.good_position])
    )
    record.final_bad_snapshot = _commit_snapshot(commits[record.bad_position])
    record.completed_at = timestamp
    _release_lease(record)
    await session.flush()
    return AnalysisProcessResult(AnalysisProcessOutcome.ATTRIBUTED)


async def advance_analysis_claim(claim: AnalysisClaim) -> AnalysisProcessResult | NeedExecution:
    timestamp = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        record = await session.get(BisectionAnalysisRecord, claim.analysis_id, with_for_update=True)
        if record is None or not _claim_is_current(record, claim, timestamp):
            return AnalysisProcessResult(AnalysisProcessOutcome.IGNORED)
        bisection = await session.get(BisectionSessionRecord, record.session_id)
        if bisection is None:
            record.status = "failed"
            record.terminal_reason = "session_missing"
            record.terminal_message = "frozen bisection session is unavailable"
            record.completed_at = timestamp
            _release_lease(record)
            return AnalysisProcessResult(AnalysisProcessOutcome.IGNORED)
        commit_rows = list(
            await session.scalars(
                select(BisectionCommitRecord)
                .where(BisectionCommitRecord.session_id == record.session_id)
                .order_by(BisectionCommitRecord.position)
            )
        )
        commits = {commit.position: commit for commit in commit_rows}
        steps = list(
            await session.scalars(
                select(BisectionAnalysisStepRecord)
                .where(BisectionAnalysisStepRecord.analysis_id == record.id)
                .order_by(BisectionAnalysisStepRecord.sequence_number)
            )
        )
        compatible = await _compatible_targets(session, record)
        binary = [
            (target.commit_position, str(target.outcome))
            for target, _run in compatible
            if target.outcome in {"pass", "regression"}
        ]
        binary.append((-1, "pass"))
        if contradictory_evidence(binary):
            return _finish_inconclusive(
                record,
                timestamp,
                "inconsistent_evidence",
                "compatible probe evidence is non-monotonic or contradictory",
            )

        candidate_confirmed = any(step.decision == "confirm_candidate" for step in steps)
        source = "reused"
        execution_run_id: UUID | None = None
        outcome: str | None = None
        if record.waiting_execution_run_id is not None:
            target = await session.get(
                BisectionExecutionTargetRecord,
                (record.waiting_execution_run_id, record.waiting_commit_sha),
            )
            if target is None or target.status not in {"completed", "execution_failed"}:
                record.status = "waiting"
                _release_lease(record)
                return AnalysisProcessResult(AnalysisProcessOutcome.WAITING)
            selected_position = record.waiting_position
            selected_sha = record.waiting_commit_sha
            execution_run_id = record.waiting_execution_run_id
            source = "requested"
            outcome = target.outcome
        else:
            if not candidate_confirmed:
                selected_position = record.total_commit_count - 1
                selected_sha = record.candidate_commit_sha
            elif record.bad_position - record.good_position == 1:
                return await _finish_attributed(session, record, bisection, commits, timestamp)
            else:
                unusable = {step.selected_position for step in steps}
                selected_position = select_position(
                    record.good_position, record.bad_position, unusable
                )
                if selected_position is None:
                    reason = (
                        "execution_failure_gap"
                        if record.execution_failure_count
                        else "indeterminate_gap"
                    )
                    return _finish_inconclusive(
                        record,
                        timestamp,
                        reason,
                        "no usable unobserved commit remains in the active interval",
                    )
                selected_sha = commits[selected_position].commit_sha
            matches = [
                (target, run)
                for target, run in compatible
                if target.commit_sha == selected_sha and target.outcome in {"pass", "regression"}
            ]
            if matches:
                outcomes = {target.outcome for target, _run in matches}
                if len(outcomes) != 1:
                    return _finish_inconclusive(
                        record,
                        timestamp,
                        "inconsistent_evidence",
                        "the selected commit has conflicting compatible probe outcomes",
                    )
                target, run = matches[0]
                outcome = target.outcome
                execution_run_id = run.id

        assert selected_position is not None and selected_sha is not None
        if outcome is None or execution_run_id is None:
            return NeedExecution(selected_position, selected_sha)

        candidate_step = not candidate_confirmed and selected_sha == record.candidate_commit_sha
        good_before = record.good_position
        good_sha_before = record.good_commit_sha
        bad_before = record.bad_position
        bad_sha_before = record.bad_commit_sha
        good_after, bad_after = (
            (good_before, bad_before)
            if candidate_step
            else updated_boundaries(good_before, bad_before, selected_position, outcome)
        )
        good_sha_after = selected_sha if good_after != good_before else good_sha_before
        bad_sha_after = selected_sha if bad_after != bad_before else bad_sha_before
        decision = _decision(outcome, candidate_confirmation=candidate_step)
        record.step_count += 1
        if source == "reused":
            record.reused_evidence_count += 1
        else:
            record.new_evidence_count += 1
        if outcome == "indeterminate":
            record.indeterminate_count += 1
        elif outcome == "execution_failed":
            record.execution_failure_count += 1
        session.add(
            BisectionAnalysisStepRecord(
                analysis_id=record.id,
                sequence_number=record.step_count,
                session_id=record.session_id,
                good_position_before=good_before,
                good_commit_sha_before=good_sha_before,
                bad_position_before=bad_before,
                bad_commit_sha_before=bad_sha_before,
                selected_position=selected_position,
                selected_commit_sha=selected_sha,
                evidence_source=source,
                execution_run_id=execution_run_id,
                observed_outcome=outcome,
                decision=decision,
                good_position_after=good_after,
                good_commit_sha_after=good_sha_after,
                bad_position_after=bad_after,
                bad_commit_sha_after=bad_sha_after,
            )
        )
        record.good_position = good_after
        record.good_commit_sha = good_sha_after
        record.bad_position = bad_after
        record.bad_commit_sha = bad_sha_after
        record.waiting_position = None
        record.waiting_commit_sha = None
        record.waiting_execution_run_id = None

        if candidate_step and outcome != "regression":
            reason = {
                "pass": "candidate_probe_pass",
                "indeterminate": "candidate_probe_indeterminate",
                "execution_failed": "candidate_execution_failed",
            }[outcome]
            return _finish_inconclusive(
                record,
                timestamp,
                reason,
                "candidate probe evidence does not confirm the required regressed boundary",
            )
        if contradictory_evidence([*binary, (selected_position, outcome)]):
            return _finish_inconclusive(
                record,
                timestamp,
                "inconsistent_evidence",
                "compatible probe evidence is non-monotonic or contradictory",
            )
        if record.bad_position - record.good_position == 1:
            return await _finish_attributed(session, record, bisection, commits, timestamp)
        record.status = "queued"
        record.last_enqueued_at = None
        _release_lease(record)
        return AnalysisProcessResult(AnalysisProcessOutcome.REQUEUED)


async def _set_waiting(claim: AnalysisClaim, need: NeedExecution, execution_run_id: UUID) -> bool:
    timestamp = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        record = await session.get(BisectionAnalysisRecord, claim.analysis_id, with_for_update=True)
        if record is None or not _claim_is_current(record, claim, timestamp):
            return False
        record.status = "waiting"
        record.waiting_position = need.position
        record.waiting_commit_sha = need.commit_sha
        record.waiting_execution_run_id = execution_run_id
        record.last_enqueued_at = None
        _release_lease(record)
        return True


async def _requeue_claim(claim: AnalysisClaim) -> None:
    timestamp = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        record = await session.get(BisectionAnalysisRecord, claim.analysis_id, with_for_update=True)
        if record is not None and _claim_is_current(record, claim, timestamp):
            record.status = "queued"
            record.last_enqueued_at = None
            _release_lease(record)


async def process_analysis_message(analysis_id: UUID) -> AnalysisProcessResult:
    async with SessionLocal() as session:
        claim = await claim_analysis(session, analysis_id)
    if claim is None:
        return AnalysisProcessResult(AnalysisProcessOutcome.IGNORED)
    action = await advance_analysis_claim(claim)
    if isinstance(action, AnalysisProcessResult):
        return action
    async with SessionLocal() as session:
        record = await session.get(BisectionAnalysisRecord, analysis_id)
        if record is None:
            return AnalysisProcessResult(AnalysisProcessOutcome.IGNORED)
        configuration = ProbeConfiguration.model_validate(record.configuration)
        session_id = record.session_id
    try:
        execution_run_id, created = await ensure_execution_run_for_commit(
            session_id, configuration, action.commit_sha
        )
    except BisectionExecutionRunConflict:
        await _requeue_claim(claim)
        return AnalysisProcessResult(AnalysisProcessOutcome.REQUEUED)
    if not await _set_waiting(claim, action, execution_run_id):
        return AnalysisProcessResult(AnalysisProcessOutcome.IGNORED)
    return AnalysisProcessResult(
        AnalysisProcessOutcome.WAITING,
        execution_run_id=execution_run_id,
        execution_created=created,
    )


async def recover_analyses(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = MAX_RECOVERY_BATCH,
) -> list[UUID]:
    if not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError("invalid recovery limit")
    timestamp = now or datetime.now(UTC)
    async with session.begin():
        records = list(
            await session.scalars(
                select(BisectionAnalysisRecord)
                .where(
                    or_(
                        BisectionAnalysisRecord.status.in_(("queued", "waiting"))
                        & (
                            BisectionAnalysisRecord.last_enqueued_at.is_(None)
                            | (
                                BisectionAnalysisRecord.last_enqueued_at
                                <= timestamp - QUEUE_RECOVERY_AGE
                            )
                        ),
                        (BisectionAnalysisRecord.status == "running")
                        & (BisectionAnalysisRecord.lease_expires_at <= timestamp),
                    )
                )
                .order_by(BisectionAnalysisRecord.created_at, BisectionAnalysisRecord.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for record in records:
            if record.status == "running":
                record.status = "queued"
                _release_lease(record)
            record.last_enqueued_at = timestamp
        return [record.id for record in records]


async def release_analysis_recovery_reservation(
    session: AsyncSession, analysis_id: UUID, reserved_at: datetime
) -> None:
    async with session.begin():
        await session.execute(
            update(BisectionAnalysisRecord)
            .where(
                BisectionAnalysisRecord.id == analysis_id,
                BisectionAnalysisRecord.status.in_(("queued", "waiting")),
                BisectionAnalysisRecord.last_enqueued_at == reserved_at,
            )
            .values(last_enqueued_at=None)
        )
