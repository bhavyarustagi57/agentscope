from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.calibration import CalibrationStudySubjectRecord
from agentscope_api.models.judging import (
    CalibrationAnalysisRecord,
    JudgeResultRecord,
    JudgeRunRecord,
)
from agentscope_api.schemas.calibration import PageParams
from agentscope_api.schemas.calibration_analysis import (
    CalibrationAnalysis,
    CalibrationDisagreement,
    CalibrationDisagreementList,
    CalibrationMetrics,
    DisagreementCategory,
    UndefinedMetric,
)
from agentscope_api.services.judging import JudgeRunNotFound

_LABELS = {"passed", "failed"}
_PERSISTED_METRICS = (
    "sample_count",
    "human_passed_count",
    "human_failed_count",
    "judge_passed_count",
    "judge_failed_count",
    "agreement_count",
    "disagreement_count",
    "true_positive",
    "true_negative",
    "false_positive",
    "false_negative",
    "observed_agreement",
    "expected_agreement",
    "precision_passed",
    "recall_passed",
    "f1_passed",
    "specificity_failed",
    "cohens_kappa",
)


class CalibrationAnalysisNotFound(Exception):
    pass


class JudgeRunNotCompleted(Exception):
    pass


class IncompleteComparisonPopulation(Exception):
    pass


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def calculate_calibration_metrics(
    pairs: Sequence[tuple[str, str]],
) -> CalibrationMetrics:
    if not pairs:
        raise ValueError("calibration analysis requires at least one paired label")
    if any(human not in _LABELS or judge not in _LABELS for human, judge in pairs):
        raise ValueError("calibration labels must be passed or failed")

    true_positive = sum(human == judge == "passed" for human, judge in pairs)
    true_negative = sum(human == judge == "failed" for human, judge in pairs)
    false_positive = sum(human == "failed" and judge == "passed" for human, judge in pairs)
    false_negative = sum(human == "passed" and judge == "failed" for human, judge in pairs)
    sample_count = len(pairs)
    human_passed = true_positive + false_negative
    human_failed = true_negative + false_positive
    judge_passed = true_positive + false_positive
    judge_failed = true_negative + false_negative
    agreements = true_positive + true_negative
    precision = _ratio(true_positive, judge_passed)
    recall = _ratio(true_positive, human_passed)
    specificity = _ratio(true_negative, human_failed)
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    observed = agreements / sample_count
    expected = (
        human_passed * judge_passed + human_failed * judge_failed
    ) / sample_count**2
    kappa = (observed - expected) / (1 - expected) if expected != 1 else None

    undefined = []
    for name, value in (
        (UndefinedMetric.PRECISION_PASSED, precision),
        (UndefinedMetric.RECALL_PASSED, recall),
        (UndefinedMetric.F1_PASSED, f1),
        (UndefinedMetric.SPECIFICITY_FAILED, specificity),
        (UndefinedMetric.COHENS_KAPPA, kappa),
    ):
        if value is None:
            undefined.append(name)

    return CalibrationMetrics(
        sample_count=sample_count,
        human_passed_count=human_passed,
        human_failed_count=human_failed,
        judge_passed_count=judge_passed,
        judge_failed_count=judge_failed,
        agreement_count=agreements,
        disagreement_count=false_positive + false_negative,
        true_positive=true_positive,
        true_negative=true_negative,
        false_positive=false_positive,
        false_negative=false_negative,
        observed_agreement=observed,
        expected_agreement=expected,
        precision_passed=precision,
        recall_passed=recall,
        f1_passed=f1,
        specificity_failed=specificity,
        cohens_kappa=kappa,
        kappa_is_defined=kappa is not None,
        undefined_metrics=undefined,
    )


def _analysis(record: CalibrationAnalysisRecord) -> CalibrationAnalysis:
    undefined = [
        metric
        for metric in UndefinedMetric
        if getattr(record, metric.value) is None
    ]
    return CalibrationAnalysis.model_validate(
        {
            **{
                field: getattr(record, field)
                for field in CalibrationAnalysis.model_fields
                if hasattr(record, field)
            },
            "kappa_is_defined": record.cohens_kappa is not None,
            "undefined_metrics": undefined,
        }
    )


async def create_analysis(session: AsyncSession, run_id: UUID) -> CalibrationAnalysis:
    async with session.begin():
        run = await session.scalar(
            select(JudgeRunRecord)
            .where(JudgeRunRecord.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise JudgeRunNotFound
        if run.status != "completed":
            raise JudgeRunNotCompleted
        existing = await session.get(CalibrationAnalysisRecord, run_id)
        if existing is not None:
            return _analysis(existing)

        rows = (
            await session.execute(
                select(
                    CalibrationStudySubjectRecord.reference_label,
                    JudgeResultRecord.decision,
                    JudgeResultRecord.error_category,
                )
                .outerjoin(
                    JudgeResultRecord,
                    and_(
                        JudgeResultRecord.run_id == run.id,
                        JudgeResultRecord.study_id == CalibrationStudySubjectRecord.study_id,
                        JudgeResultRecord.trace_id == CalibrationStudySubjectRecord.trace_id,
                    ),
                )
                .where(CalibrationStudySubjectRecord.study_id == run.study_id)
                .order_by(CalibrationStudySubjectRecord.position)
            )
        ).all()
        result_count = int(
            await session.scalar(
                select(func.count())
                .select_from(JudgeResultRecord)
                .where(JudgeResultRecord.run_id == run.id)
            )
            or 0
        )
        if (
            not rows
            or result_count != len(rows)
            or any(
                row.decision not in _LABELS or row.error_category is not None
                for row in rows
            )
        ):
            raise IncompleteComparisonPopulation

        metrics = calculate_calibration_metrics(
            [(row.reference_label, row.decision) for row in rows]
        )
        record = CalibrationAnalysisRecord(
            run_id=run.id,
            study_id=run.study_id,
            metric_schema_version="1",
            **{field: getattr(metrics, field) for field in _PERSISTED_METRICS},
        )
        session.add(record)
        await session.flush()
        await session.refresh(record)
        return _analysis(record)


async def get_analysis(session: AsyncSession, run_id: UUID) -> CalibrationAnalysis:
    if await session.get(JudgeRunRecord, run_id) is None:
        raise JudgeRunNotFound
    record = await session.get(CalibrationAnalysisRecord, run_id)
    if record is None:
        raise CalibrationAnalysisNotFound
    return _analysis(record)


async def list_disagreements(
    session: AsyncSession,
    run_id: UUID,
    params: PageParams,
    category: DisagreementCategory | None = None,
) -> CalibrationDisagreementList:
    analysis = await get_analysis(session, run_id)
    statement = (
        select(
            CalibrationStudySubjectRecord.trace_id,
            CalibrationStudySubjectRecord.reference_label,
            JudgeResultRecord.decision,
        )
        .join(
            JudgeResultRecord,
            and_(
                JudgeResultRecord.run_id == run_id,
                JudgeResultRecord.study_id == CalibrationStudySubjectRecord.study_id,
                JudgeResultRecord.trace_id == CalibrationStudySubjectRecord.trace_id,
            ),
        )
        .where(
            CalibrationStudySubjectRecord.study_id == analysis.study_id,
            JudgeResultRecord.decision != CalibrationStudySubjectRecord.reference_label,
        )
    )
    if category is DisagreementCategory.FALSE_POSITIVE:
        statement = statement.where(
            CalibrationStudySubjectRecord.reference_label == "failed",
            JudgeResultRecord.decision == "passed",
        )
    elif category is DisagreementCategory.FALSE_NEGATIVE:
        statement = statement.where(
            CalibrationStudySubjectRecord.reference_label == "passed",
            JudgeResultRecord.decision == "failed",
        )
    rows = (
        await session.execute(
            statement.order_by(
                CalibrationStudySubjectRecord.position,
                CalibrationStudySubjectRecord.trace_id,
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return CalibrationDisagreementList(
        items=[
            CalibrationDisagreement(
                trace_id=row.trace_id,
                category=(
                    DisagreementCategory.FALSE_POSITIVE
                    if row.reference_label == "failed"
                    else DisagreementCategory.FALSE_NEGATIVE
                ),
                human_label=row.reference_label,
                judge_decision=row.decision,
            )
            for row in rows[: params.page_size]
        ],
        has_more=len(rows) > params.page_size,
    )
