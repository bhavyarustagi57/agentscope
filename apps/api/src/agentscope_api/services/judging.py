from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.calibration import CalibrationStudyRecord, CalibrationStudySubjectRecord
from agentscope_api.models.judging import (
    JudgeConfigurationRecord,
    JudgeResultRecord,
    JudgeRunRecord,
)
from agentscope_api.schemas.calibration import PageParams
from agentscope_api.schemas.judging import (
    JudgeConfiguration,
    JudgeConfigurationCreate,
    JudgeConfigurationList,
    JudgeResult,
    JudgeResultList,
    JudgeRun,
    JudgeRunCreate,
    JudgeRunList,
    JudgeRunProgress,
    JudgeRunStatus,
)


class JudgeConfigurationNotFound(Exception):
    pass


class JudgeRunNotFound(Exception):
    pass


class JudgeStudyNotFound(Exception):
    pass


class EmptyJudgeStudy(Exception):
    pass


async def create_configuration(
    session: AsyncSession, payload: JudgeConfigurationCreate
) -> JudgeConfiguration:
    record = JudgeConfigurationRecord(**payload.model_dump(mode="python"))
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return JudgeConfiguration.model_validate(record)


async def get_configuration(
    session: AsyncSession, configuration_id: UUID
) -> JudgeConfiguration | None:
    record = await session.get(JudgeConfigurationRecord, configuration_id)
    return JudgeConfiguration.model_validate(record) if record else None


async def list_configurations(session: AsyncSession, params: PageParams) -> JudgeConfigurationList:
    records = list(
        await session.scalars(
            select(JudgeConfigurationRecord)
            .order_by(JudgeConfigurationRecord.created_at, JudgeConfigurationRecord.id)
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return JudgeConfigurationList(
        items=[JudgeConfiguration.model_validate(item) for item in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )


async def create_run(session: AsyncSession, payload: JudgeRunCreate) -> JudgeRun:
    async with session.begin():
        study = await session.get(CalibrationStudyRecord, payload.study_id)
        if study is None:
            raise JudgeStudyNotFound
        configuration = await session.get(JudgeConfigurationRecord, payload.configuration_id)
        if configuration is None:
            raise JudgeConfigurationNotFound
        subject_count = await session.scalar(
            select(func.count())
            .select_from(CalibrationStudySubjectRecord)
            .where(CalibrationStudySubjectRecord.study_id == payload.study_id)
        )
        if not subject_count:
            raise EmptyJudgeStudy
        record = JudgeRunRecord(
            study_id=study.id,
            configuration_id=configuration.id,
            provider=configuration.provider,
            model=configuration.model,
            rubric=configuration.rubric,
            output_schema_version=configuration.output_schema_version,
            timeout_seconds=configuration.timeout_seconds,
            max_output_tokens=configuration.max_output_tokens,
            configuration_version=configuration.configuration_version,
        )
        session.add(record)
        await session.flush()
        run_id = record.id
    result = await get_run(session, run_id)
    assert result is not None
    return result


async def _run_counts(session: AsyncSession, run: JudgeRunRecord) -> tuple[int, int]:
    subject_count = int(
        await session.scalar(
            select(func.count())
            .select_from(CalibrationStudySubjectRecord)
            .where(CalibrationStudySubjectRecord.study_id == run.study_id)
        )
        or 0
    )
    result_count = int(
        await session.scalar(
            select(func.count())
            .select_from(JudgeResultRecord)
            .where(JudgeResultRecord.run_id == run.id)
        )
        or 0
    )
    return subject_count, result_count


async def _run_model(session: AsyncSession, record: JudgeRunRecord) -> JudgeRun:
    subject_count, result_count = await _run_counts(session, record)
    return JudgeRun.model_validate(
        {
            **{
                field: getattr(record, field)
                for field in JudgeRun.model_fields
                if hasattr(record, field)
            },
            "subject_count": subject_count,
            "result_count": result_count,
        }
    )


async def get_run(session: AsyncSession, run_id: UUID) -> JudgeRun | None:
    record = await session.get(JudgeRunRecord, run_id)
    return await _run_model(session, record) if record else None


async def list_runs(session: AsyncSession, params: PageParams) -> JudgeRunList:
    records = list(
        await session.scalars(
            select(JudgeRunRecord)
            .order_by(JudgeRunRecord.created_at, JudgeRunRecord.id)
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    items = [await _run_model(session, item) for item in records[: params.page_size]]
    return JudgeRunList(items=items, has_more=len(records) > params.page_size)


async def list_results(session: AsyncSession, run_id: UUID, params: PageParams) -> JudgeResultList:
    if await session.get(JudgeRunRecord, run_id) is None:
        raise JudgeRunNotFound
    records = list(
        await session.scalars(
            select(JudgeResultRecord)
            .where(JudgeResultRecord.run_id == run_id)
            .order_by(JudgeResultRecord.created_at, JudgeResultRecord.trace_id)
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return JudgeResultList(
        items=[JudgeResult.model_validate(item) for item in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )


async def get_progress(session: AsyncSession, run_id: UUID) -> JudgeRunProgress:
    run = await session.get(JudgeRunRecord, run_id)
    if run is None:
        raise JudgeRunNotFound
    subject_count, result_count = await _run_counts(session, run)
    counts = (
        await session.execute(
            select(
                func.count().filter(JudgeResultRecord.decision == "passed"),
                func.count().filter(JudgeResultRecord.decision == "failed"),
                func.count().filter(JudgeResultRecord.error_category.is_not(None)),
            ).where(JudgeResultRecord.run_id == run_id)
        )
    ).one()
    return JudgeRunProgress(
        run_id=run.id,
        status=JudgeRunStatus(run.status),
        subject_count=subject_count,
        result_count=result_count,
        passed_count=counts[0],
        failed_count=counts[1],
        error_count=counts[2],
        pending_count=subject_count - result_count,
    )
