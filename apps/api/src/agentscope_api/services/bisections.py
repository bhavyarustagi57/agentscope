from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.core.config import get_settings
from agentscope_api.models.bisection import BisectionCommitRecord, BisectionSessionRecord
from agentscope_api.models.regression import RegressionCheckRecord
from agentscope_api.schemas.bisections import (
    BisectionCommit,
    BisectionSession,
    BisectionSessionCreate,
    BisectionSessionList,
    BisectionSessionListParams,
    BisectionSessionSummary,
)
from agentscope_api.schemas.regressions import RegressionClassification
from agentscope_api.services.git_inspection import GitInspector


class RegressionCheckNotFound(Exception):
    pass


class RegressionNotDetected(Exception):
    pass


class BaselineGitRevisionRequired(Exception):
    pass


class CandidateGitRevisionRequired(Exception):
    pass


class RegressionEvidenceChanged(Exception):
    pass


class DuplicateBisectionSession(Exception):
    pass


def _revision(provenance: dict[str, object], *, baseline: bool) -> str:
    value = provenance.get("git_commit_sha")
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        if baseline:
            raise BaselineGitRevisionRequired
        raise CandidateGitRevisionRequired
    return value.strip()


async def _session_schema(
    session: AsyncSession, record: BisectionSessionRecord
) -> BisectionSession:
    commits = list(
        await session.scalars(
            select(BisectionCommitRecord)
            .where(BisectionCommitRecord.session_id == record.id)
            .order_by(BisectionCommitRecord.position)
        )
    )
    return BisectionSession(
        **BisectionSessionSummary.model_validate(record).model_dump(),
        commits=[BisectionCommit.model_validate(commit) for commit in commits],
    )


async def create_bisection_session(
    session: AsyncSession, payload: BisectionSessionCreate
) -> BisectionSession:
    source = await session.get(RegressionCheckRecord, payload.regression_check_id)
    if source is None:
        raise RegressionCheckNotFound
    if source.classification != RegressionClassification.REGRESSION_DETECTED:
        raise RegressionNotDetected
    baseline_provenance = dict(source.baseline_provenance)
    candidate_provenance = dict(source.candidate_provenance)
    baseline_revision = _revision(baseline_provenance, baseline=True)
    candidate_revision = _revision(candidate_provenance, baseline=False)
    await session.rollback()

    maximum_commit_count = get_settings().bisection_max_commit_range
    plan = await asyncio.to_thread(
        GitInspector().inspect,
        payload.repository_path,
        baseline_revision,
        candidate_revision,
        maximum_commit_count=maximum_commit_count,
    )
    record = BisectionSessionRecord(
        regression_check_id=payload.regression_check_id,
        repository_path=plan.repository.path,
        repository_root=plan.repository.root,
        repository_common_dir=plan.repository.common_dir,
        repository_fingerprint=plan.repository.fingerprint,
        repository_head_sha=plan.repository.head_sha,
        baseline_revision=baseline_revision,
        candidate_revision=candidate_revision,
        baseline_commit_sha=plan.baseline_sha,
        candidate_commit_sha=plan.candidate_sha,
        baseline_provenance=baseline_provenance,
        candidate_provenance=candidate_provenance,
        commit_count=len(plan.commits),
        maximum_commit_count=maximum_commit_count,
        status="ready",
    )
    async with session.begin():
        current = await session.scalar(
            select(RegressionCheckRecord)
            .where(RegressionCheckRecord.id == payload.regression_check_id)
            .with_for_update()
        )
        if current is None:
            raise RegressionCheckNotFound
        if (
            current.classification != RegressionClassification.REGRESSION_DETECTED
            or current.baseline_provenance != baseline_provenance
            or current.candidate_provenance != candidate_provenance
        ):
            raise RegressionEvidenceChanged
        duplicate = await session.scalar(
            select(BisectionSessionRecord.id).where(
                BisectionSessionRecord.regression_check_id == payload.regression_check_id,
                BisectionSessionRecord.repository_fingerprint == plan.repository.fingerprint,
                BisectionSessionRecord.baseline_commit_sha == plan.baseline_sha,
                BisectionSessionRecord.candidate_commit_sha == plan.candidate_sha,
            )
        )
        if duplicate is not None:
            raise DuplicateBisectionSession
        session.add(record)
        await session.flush()
        for commit in plan.commits:
            session.add(
                BisectionCommitRecord(
                    session_id=record.id,
                    position=commit.position,
                    commit_sha=commit.commit_sha,
                    committed_at=commit.committed_at,
                    subject=commit.subject,
                    parent_shas=commit.parent_shas,
                )
            )
        await session.flush()
        response = await _session_schema(session, record)
    return response


async def get_bisection_session(session: AsyncSession, session_id: UUID) -> BisectionSession | None:
    record = await session.get(BisectionSessionRecord, session_id)
    return None if record is None else await _session_schema(session, record)


async def list_bisection_sessions(
    session: AsyncSession, params: BisectionSessionListParams
) -> BisectionSessionList:
    statement = select(BisectionSessionRecord)
    if params.regression_check_id is not None:
        statement = statement.where(
            BisectionSessionRecord.regression_check_id == params.regression_check_id
        )
    records = list(
        await session.scalars(
            statement.order_by(
                BisectionSessionRecord.created_at.desc(), BisectionSessionRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return BisectionSessionList(
        items=[
            BisectionSessionSummary.model_validate(record) for record in records[: params.page_size]
        ],
        has_more=len(records) > params.page_size,
    )
