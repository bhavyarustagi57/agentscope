from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.bisection import BisectionCommitRecord, BisectionSessionRecord
from agentscope_api.models.bisection_analysis import (
    BisectionAnalysisRecord,
    BisectionAnalysisStepRecord,
)
from agentscope_api.models.regression import RegressionCheckRecord
from agentscope_api.schemas.bisection_analysis import (
    BisectionAnalysis,
    BisectionAnalysisCreate,
    BisectionAnalysisList,
    BisectionAnalysisListParams,
    BisectionAnalysisStep,
)
from agentscope_api.schemas.bisection_execution import ProbeConfiguration


class BisectionSessionNotFound(Exception):
    pass


class BisectionSessionNotReady(Exception):
    pass


class BisectionAnalysisNotFound(Exception):
    pass


class BisectionAnalysisConflict(Exception):
    pass


class BisectionAnalysisNotPending(Exception):
    pass


def select_position(good_position: int, bad_position: int, unusable: set[int]) -> int | None:
    candidates = [
        position for position in range(good_position + 1, bad_position) if position not in unusable
    ]
    if not candidates:
        return None
    midpoint = (good_position + bad_position) // 2
    return min(candidates, key=lambda position: (abs(position - midpoint), position))


def updated_boundaries(
    good_position: int, bad_position: int, selected_position: int, outcome: str
) -> tuple[int, int]:
    if outcome == "pass":
        return selected_position, bad_position
    if outcome == "regression":
        return good_position, selected_position
    return good_position, bad_position


def contradictory_evidence(observations: Iterable[tuple[int, str]]) -> bool:
    binary = list(observations)
    outcomes: dict[int, set[str]] = {}
    for position, outcome in binary:
        if outcome in {"pass", "regression"}:
            outcomes.setdefault(position, set()).add(outcome)
    if any(len(values) > 1 for values in outcomes.values()):
        return True
    regressions = [position for position, values in outcomes.items() if "regression" in values]
    passes = [position for position, values in outcomes.items() if "pass" in values]
    return bool(regressions and passes and min(regressions) < max(passes))


async def _analysis_schema(
    session: AsyncSession, record: BisectionAnalysisRecord
) -> BisectionAnalysis:
    steps = list(
        await session.scalars(
            select(BisectionAnalysisStepRecord)
            .where(BisectionAnalysisStepRecord.analysis_id == record.id)
            .order_by(BisectionAnalysisStepRecord.sequence_number)
        )
    )
    usable = sum(step.observed_outcome in {"pass", "regression"} for step in steps)
    return BisectionAnalysis(
        id=record.id,
        session_id=record.session_id,
        status=record.status,
        configuration_schema_version="1",
        configuration=ProbeConfiguration.model_validate(record.configuration),
        repository_fingerprint=record.repository_fingerprint,
        baseline_commit_sha=record.baseline_commit_sha,
        candidate_commit_sha=record.candidate_commit_sha,
        total_commit_count=record.total_commit_count,
        current_interval_size=max(0, record.bad_position - record.good_position - 1),
        good_position=record.good_position,
        good_commit_sha=record.good_commit_sha,
        bad_position=record.bad_position,
        bad_commit_sha=record.bad_commit_sha,
        step_count=record.step_count,
        reused_evidence_count=record.reused_evidence_count,
        new_evidence_count=record.new_evidence_count,
        usable_evidence_count=usable,
        indeterminate_count=record.indeterminate_count,
        execution_failure_count=record.execution_failure_count,
        final_good_commit_sha=record.final_good_commit_sha,
        final_good_position=record.final_good_position,
        final_bad_commit_sha=record.final_bad_commit_sha,
        final_bad_position=record.final_bad_position,
        final_good_snapshot=record.final_good_snapshot,
        final_bad_snapshot=record.final_bad_snapshot,
        terminal_reason=record.terminal_reason,
        terminal_message=record.terminal_message,
        created_at=record.created_at,
        queued_at=record.queued_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        steps=[BisectionAnalysisStep.model_validate(step) for step in steps],
    )


async def create_analysis(
    session: AsyncSession, session_id: UUID, payload: BisectionAnalysisCreate
) -> BisectionAnalysis:
    async with session.begin():
        bisection = await session.get(BisectionSessionRecord, session_id, with_for_update=True)
        if bisection is None:
            raise BisectionSessionNotFound
        regression = await session.get(RegressionCheckRecord, bisection.regression_check_id)
        if (
            bisection.status != "ready"
            or regression is None
            or regression.classification != "regression_detected"
        ):
            raise BisectionSessionNotReady
        active = await session.scalar(
            select(BisectionAnalysisRecord.id).where(
                BisectionAnalysisRecord.session_id == session_id,
                BisectionAnalysisRecord.status.in_(("pending", "queued", "running", "waiting")),
            )
        )
        if active is not None:
            raise BisectionAnalysisConflict
        candidate = await session.get(
            BisectionCommitRecord, (session_id, bisection.commit_count - 1)
        )
        if candidate is None or candidate.commit_sha != bisection.candidate_commit_sha:
            raise BisectionSessionNotReady
        record = BisectionAnalysisRecord(
            session_id=session_id,
            configuration=payload.configuration.model_dump(mode="json"),
            repository_fingerprint=bisection.repository_fingerprint,
            baseline_commit_sha=bisection.baseline_commit_sha,
            candidate_commit_sha=bisection.candidate_commit_sha,
            total_commit_count=bisection.commit_count,
            good_position=-1,
            good_commit_sha=bisection.baseline_commit_sha,
            bad_position=candidate.position,
            bad_commit_sha=candidate.commit_sha,
        )
        session.add(record)
        await session.flush()
        response = await _analysis_schema(session, record)
    return response


async def start_analysis(session: AsyncSession, analysis_id: UUID) -> None:
    timestamp = datetime.now(UTC)
    async with session.begin():
        record = await session.get(BisectionAnalysisRecord, analysis_id, with_for_update=True)
        if record is None:
            raise BisectionAnalysisNotFound
        if record.status == "queued":
            return
        if record.status != "pending":
            raise BisectionAnalysisNotPending
        record.status = "queued"
        record.queued_at = timestamp


async def mark_analysis_enqueued(session: AsyncSession, analysis_id: UUID) -> None:
    async with session.begin():
        await session.execute(
            update(BisectionAnalysisRecord)
            .where(
                BisectionAnalysisRecord.id == analysis_id,
                BisectionAnalysisRecord.status.in_(("queued", "waiting")),
            )
            .values(last_enqueued_at=datetime.now(UTC))
        )


async def get_analysis(session: AsyncSession, analysis_id: UUID) -> BisectionAnalysis | None:
    record = await session.get(BisectionAnalysisRecord, analysis_id)
    return None if record is None else await _analysis_schema(session, record)


async def list_analyses(
    session: AsyncSession, session_id: UUID, params: BisectionAnalysisListParams
) -> BisectionAnalysisList:
    if await session.get(BisectionSessionRecord, session_id) is None:
        raise BisectionSessionNotFound
    statement = select(BisectionAnalysisRecord).where(
        BisectionAnalysisRecord.session_id == session_id
    )
    if params.status is not None:
        statement = statement.where(BisectionAnalysisRecord.status == params.status.value)
    records = list(
        await session.scalars(
            statement.order_by(
                BisectionAnalysisRecord.created_at.desc(), BisectionAnalysisRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return BisectionAnalysisList(
        items=[await _analysis_schema(session, record) for record in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )
