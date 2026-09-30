from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.evaluation import EvaluationDefinitionRecord
from agentscope_api.models.experiment import (
    ExperimentEvaluationConditionRecord,
    ExperimentRecord,
    ExperimentSubjectRecord,
    ExperimentVariantRecord,
)
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.experiments import (
    Experiment,
    ExperimentConfigure,
    ExperimentCreate,
    ExperimentEvaluationCondition,
    ExperimentList,
    ExperimentListParams,
    ExperimentStatus,
    ExperimentSubjectPair,
    ExperimentSummary,
    ExperimentVariant,
    VariantProvenance,
)


class ExperimentNotFound(Exception):
    pass


class ExperimentImmutable(Exception):
    pass


class ExperimentIncomplete(Exception):
    pass


class ExperimentTraceNotFound(Exception):
    pass


class ExperimentEvaluationDefinitionNotFound(Exception):
    pass


class ExperimentEvaluationDefinitionDisabled(Exception):
    pass


async def create_experiment(session: AsyncSession, payload: ExperimentCreate) -> Experiment:
    record = ExperimentRecord(**payload.model_dump(mode="python"))
    async with session.begin():
        session.add(record)
    result = await get_experiment(session, record.id)
    assert result is not None
    return result


async def configure_experiment(
    session: AsyncSession, experiment_id: UUID, payload: ExperimentConfigure
) -> Experiment:
    async with session.begin():
        experiment = await session.scalar(
            select(ExperimentRecord)
            .where(ExperimentRecord.id == experiment_id)
            .with_for_update()
        )
        if experiment is None:
            raise ExperimentNotFound
        if experiment.status != ExperimentStatus.DRAFT.value:
            raise ExperimentImmutable

        trace_ids = [
            trace_id
            for subject in payload.subjects
            for trace_id in (subject.a_trace_id, subject.b_trace_id)
        ]
        existing_trace_ids = set(
            await session.scalars(
                select(TraceRecord.trace_id).where(TraceRecord.trace_id.in_(trace_ids))
            )
        )
        if len(existing_trace_ids) != len(trace_ids):
            raise ExperimentTraceNotFound

        definitions = list(
            await session.scalars(
                select(EvaluationDefinitionRecord).where(
                    EvaluationDefinitionRecord.id.in_(payload.evaluation_definition_ids)
                )
            )
        )
        definitions_by_id = {definition.id: definition for definition in definitions}
        if len(definitions_by_id) != len(payload.evaluation_definition_ids):
            raise ExperimentEvaluationDefinitionNotFound
        if any(not definition.is_enabled for definition in definitions):
            raise ExperimentEvaluationDefinitionDisabled

        experiment.name = payload.name
        experiment.description = payload.description
        experiment.updated_at = datetime.now(UTC)

        stored_variants = {
            variant.variant_key: variant
            for variant in await session.scalars(
                select(ExperimentVariantRecord).where(
                    ExperimentVariantRecord.experiment_id == experiment_id
                )
            )
        }
        for variant in payload.variants:
            record = stored_variants.get(variant.key)
            provenance = variant.provenance.model_dump(mode="json")
            if record is None:
                session.add(
                    ExperimentVariantRecord(
                        experiment_id=experiment_id,
                        variant_key=variant.key,
                        name=variant.name,
                        provenance=provenance,
                    )
                )
            else:
                record.name = variant.name
                record.provenance = provenance
        await session.flush()

        await session.execute(
            delete(ExperimentSubjectRecord).where(
                ExperimentSubjectRecord.experiment_id == experiment_id
            )
        )
        await session.execute(
            delete(ExperimentEvaluationConditionRecord).where(
                ExperimentEvaluationConditionRecord.experiment_id == experiment_id
            )
        )
        session.add_all(
            ExperimentSubjectRecord(
                experiment_id=experiment_id,
                position=position,
                variant_key=variant_key,
                trace_id=trace_id,
            )
            for position, subject in enumerate(payload.subjects)
            for variant_key, trace_id in (
                ("A", subject.a_trace_id),
                ("B", subject.b_trace_id),
            )
        )
        session.add_all(
            ExperimentEvaluationConditionRecord(
                experiment_id=experiment_id,
                position=position,
                definition_id=definition.id,
                definition_name=definition.name,
                evaluator_kind=definition.evaluator_kind,
                evaluator_config=definition.evaluator_config,
            )
            for position, definition_id in enumerate(payload.evaluation_definition_ids)
            for definition in (definitions_by_id[definition_id],)
        )

    result = await get_experiment(session, experiment_id)
    assert result is not None
    return result


async def mark_experiment_ready(session: AsyncSession, experiment_id: UUID) -> Experiment:
    async with session.begin():
        experiment = await session.scalar(
            select(ExperimentRecord)
            .where(ExperimentRecord.id == experiment_id)
            .with_for_update()
        )
        if experiment is None:
            raise ExperimentNotFound
        if experiment.status == ExperimentStatus.READY.value:
            return await _get_experiment_in_transaction(session, experiment)
        if experiment.status != ExperimentStatus.DRAFT.value:
            raise ExperimentImmutable

        variant_keys = set(
            await session.scalars(
                select(ExperimentVariantRecord.variant_key).where(
                    ExperimentVariantRecord.experiment_id == experiment_id
                )
            )
        )
        subject_count, pair_count = (
            await session.execute(
                select(
                    func.count(), func.count(func.distinct(ExperimentSubjectRecord.position))
                ).where(ExperimentSubjectRecord.experiment_id == experiment_id)
            )
        ).one()
        condition_count = await session.scalar(
            select(func.count())
            .select_from(ExperimentEvaluationConditionRecord)
            .where(ExperimentEvaluationConditionRecord.experiment_id == experiment_id)
        )
        if (
            variant_keys != {"A", "B"}
            or pair_count == 0
            or subject_count != pair_count * 2
            or not condition_count
        ):
            raise ExperimentIncomplete

        now = datetime.now(UTC)
        experiment.status = ExperimentStatus.READY.value
        experiment.ready_at = now
        experiment.updated_at = now

    result = await get_experiment(session, experiment_id)
    assert result is not None
    return result


async def _get_experiment_in_transaction(
    session: AsyncSession, record: ExperimentRecord
) -> Experiment:
    return await _assemble_experiment(session, record)


async def get_experiment(session: AsyncSession, experiment_id: UUID) -> Experiment | None:
    record = await session.get(ExperimentRecord, experiment_id)
    if record is None:
        return None
    return await _assemble_experiment(session, record)


async def _assemble_experiment(session: AsyncSession, record: ExperimentRecord) -> Experiment:
    variants = list(
        await session.scalars(
            select(ExperimentVariantRecord)
            .where(ExperimentVariantRecord.experiment_id == record.id)
            .order_by(ExperimentVariantRecord.variant_key)
        )
    )
    subject_rows = (
        await session.execute(
            select(
                ExperimentSubjectRecord.position,
                func.max(ExperimentSubjectRecord.trace_id)
                .filter(ExperimentSubjectRecord.variant_key == "A")
                .label("a_trace_id"),
                func.max(ExperimentSubjectRecord.trace_id)
                .filter(ExperimentSubjectRecord.variant_key == "B")
                .label("b_trace_id"),
            )
            .where(ExperimentSubjectRecord.experiment_id == record.id)
            .group_by(ExperimentSubjectRecord.position)
            .order_by(ExperimentSubjectRecord.position)
        )
    ).all()
    conditions = list(
        await session.scalars(
            select(ExperimentEvaluationConditionRecord)
            .where(ExperimentEvaluationConditionRecord.experiment_id == record.id)
            .order_by(ExperimentEvaluationConditionRecord.position)
        )
    )
    return Experiment(
        id=record.id,
        name=record.name,
        description=record.description,
        status=ExperimentStatus(record.status),
        created_at=record.created_at,
        updated_at=record.updated_at,
        ready_at=record.ready_at,
        variants=[
            ExperimentVariant(
                id=variant.id,
                key=cast(Literal["A", "B"], variant.variant_key),
                name=variant.name,
                provenance=VariantProvenance.model_validate(variant.provenance),
            )
            for variant in variants
        ],
        subjects=[
            ExperimentSubjectPair(
                position=row.position,
                a_trace_id=row.a_trace_id,
                b_trace_id=row.b_trace_id,
            )
            for row in subject_rows
        ],
        evaluation_conditions=[
            ExperimentEvaluationCondition(
                position=condition.position,
                definition_id=condition.definition_id,
                definition_name=condition.definition_name,
                evaluator_kind=condition.evaluator_kind,
                evaluator_config=condition.evaluator_config,
            )
            for condition in conditions
        ],
    )


def _summary(row: Row[Any]) -> ExperimentSummary:
    record = row[0]
    return ExperimentSummary(
        id=record.id,
        name=record.name,
        description=record.description,
        status=record.status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        ready_at=record.ready_at,
        variant_count=row.variant_count,
        subject_count=row.subject_count,
        evaluation_condition_count=row.evaluation_condition_count,
    )


async def list_experiments(
    session: AsyncSession, params: ExperimentListParams
) -> ExperimentList:
    variants = (
        select(
            ExperimentVariantRecord.experiment_id,
            func.count().label("variant_count"),
        )
        .group_by(ExperimentVariantRecord.experiment_id)
        .subquery()
    )
    subjects = (
        select(
            ExperimentSubjectRecord.experiment_id,
            func.count(func.distinct(ExperimentSubjectRecord.position)).label("subject_count"),
        )
        .group_by(ExperimentSubjectRecord.experiment_id)
        .subquery()
    )
    conditions = (
        select(
            ExperimentEvaluationConditionRecord.experiment_id,
            func.count().label("evaluation_condition_count"),
        )
        .group_by(ExperimentEvaluationConditionRecord.experiment_id)
        .subquery()
    )
    statement = (
        select(
            ExperimentRecord,
            func.coalesce(variants.c.variant_count, 0).label("variant_count"),
            func.coalesce(subjects.c.subject_count, 0).label("subject_count"),
            func.coalesce(conditions.c.evaluation_condition_count, 0).label(
                "evaluation_condition_count"
            ),
        )
        .outerjoin(variants, variants.c.experiment_id == ExperimentRecord.id)
        .outerjoin(subjects, subjects.c.experiment_id == ExperimentRecord.id)
        .outerjoin(conditions, conditions.c.experiment_id == ExperimentRecord.id)
    )
    if params.status is not None:
        statement = statement.where(ExperimentRecord.status == params.status.value)
    rows = (
        await session.execute(
            statement.order_by(ExperimentRecord.created_at.desc(), ExperimentRecord.id.desc())
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return ExperimentList(
        items=[_summary(row) for row in rows[: params.page_size]],
        has_more=len(rows) > params.page_size,
    )
