from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.experiment import (
    ExperimentConditionAnalysisRecord,
    ExperimentEvaluationConditionRecord,
    ExperimentRunAnalysisRecord,
    ExperimentRunRecord,
    ExperimentRunResultRecord,
    ExperimentVariantRecord,
)
from agentscope_api.models.regression import (
    RegressionCheckRecord,
    RegressionFindingRecord,
    RegressionPolicyRecord,
)
from agentscope_api.schemas.regressions import (
    RegressionCheck,
    RegressionCheckCreate,
    RegressionCheckList,
    RegressionCheckListParams,
    RegressionCheckSummary,
    RegressionClassification,
    RegressionFinding,
    RegressionPolicy,
    RegressionPolicyCreate,
    RegressionPolicyList,
    RegressionPolicyListParams,
)


class RegressionPolicyNotFound(Exception):
    pass


class RegressionSourceRunNotFound(Exception):
    pass


class RegressionSourceRunNotCompleted(Exception):
    pass


class RegressionAnalysisRequired(Exception):
    pass


class IncompleteRegressionEvidence(Exception):
    pass


class DuplicateRegressionCheck(Exception):
    pass


def _finding_schema(record: RegressionFindingRecord) -> RegressionFinding:
    return RegressionFinding.model_validate(record)


async def _get_check_record(session: AsyncSession, check_id: UUID) -> RegressionCheckRecord | None:
    return await session.get(RegressionCheckRecord, check_id)


async def _check_schema(session: AsyncSession, record: RegressionCheckRecord) -> RegressionCheck:
    findings = list(
        await session.scalars(
            select(RegressionFindingRecord)
            .where(RegressionFindingRecord.regression_check_id == record.id)
            .order_by(RegressionFindingRecord.condition_position)
        )
    )
    return RegressionCheck(
        **RegressionCheckSummary.model_validate(record).model_dump(),
        findings=[_finding_schema(item) for item in findings],
    )


def _oriented_metrics(
    source: ExperimentConditionAnalysisRecord, baseline_variant: str
) -> dict[str, int | float | bool | None]:
    assert source.a_passed_count is not None
    assert source.b_passed_count is not None
    assert source.a_pass_rate is not None
    assert source.b_pass_rate is not None
    assert source.pass_rate_difference is not None
    assert source.a_only_passed_count is not None
    assert source.b_only_passed_count is not None
    assert source.confidence_interval_lower is not None
    assert source.confidence_interval_upper is not None
    if baseline_variant == "A":
        return {
            "baseline_passed_count": source.a_passed_count,
            "candidate_passed_count": source.b_passed_count,
            "baseline_pass_rate": source.a_pass_rate,
            "candidate_pass_rate": source.b_pass_rate,
            "candidate_minus_baseline": source.pass_rate_difference,
            "baseline_only_passed_count": source.a_only_passed_count,
            "candidate_only_passed_count": source.b_only_passed_count,
            "matched_pairs_odds_ratio": source.matched_pairs_odds_ratio,
            "confidence_interval_lower": source.confidence_interval_lower,
            "confidence_interval_upper": source.confidence_interval_upper,
        }
    odds = (
        None
        if not source.b_only_passed_count
        else source.a_only_passed_count / source.b_only_passed_count
    )
    return {
        "baseline_passed_count": source.b_passed_count,
        "candidate_passed_count": source.a_passed_count,
        "baseline_pass_rate": source.b_pass_rate,
        "candidate_pass_rate": source.a_pass_rate,
        "candidate_minus_baseline": -source.pass_rate_difference,
        "baseline_only_passed_count": source.b_only_passed_count,
        "candidate_only_passed_count": source.a_only_passed_count,
        "matched_pairs_odds_ratio": odds,
        "confidence_interval_lower": -source.confidence_interval_upper,
        "confidence_interval_upper": -source.confidence_interval_lower,
    }


async def create_regression_check(
    session: AsyncSession, payload: RegressionCheckCreate
) -> RegressionCheck:
    async with session.begin():
        run = await session.scalar(
            select(ExperimentRunRecord)
            .where(ExperimentRunRecord.id == payload.experiment_run_id)
            .with_for_update()
        )
        if run is None:
            raise RegressionSourceRunNotFound
        if run.status != "completed":
            raise RegressionSourceRunNotCompleted
        policy = await session.get(RegressionPolicyRecord, payload.regression_policy_id)
        if policy is None:
            raise RegressionPolicyNotFound
        duplicate = await session.scalar(
            select(RegressionCheckRecord.id).where(
                RegressionCheckRecord.experiment_run_id == run.id,
                RegressionCheckRecord.regression_policy_id == policy.id,
                RegressionCheckRecord.baseline_variant == payload.baseline_variant,
                RegressionCheckRecord.candidate_variant == payload.candidate_variant,
            )
        )
        if duplicate is not None:
            raise DuplicateRegressionCheck
        analysis = await session.scalar(
            select(ExperimentRunAnalysisRecord).where(ExperimentRunAnalysisRecord.run_id == run.id)
        )
        if analysis is None:
            raise RegressionAnalysisRequired
        result_count = await session.scalar(
            select(func.count())
            .select_from(ExperimentRunResultRecord)
            .where(ExperimentRunResultRecord.run_id == run.id)
        )
        conditions = list(
            await session.scalars(
                select(ExperimentEvaluationConditionRecord)
                .where(ExperimentEvaluationConditionRecord.experiment_id == run.experiment_id)
                .order_by(ExperimentEvaluationConditionRecord.position)
            )
        )
        source_findings = list(
            await session.scalars(
                select(ExperimentConditionAnalysisRecord)
                .where(ExperimentConditionAnalysisRecord.analysis_id == analysis.id)
                .order_by(ExperimentConditionAnalysisRecord.condition_position)
            )
        )
        if (
            result_count != run.expected_decision_count
            or not conditions
            or len(source_findings) != len(conditions)
            or any(
                source.condition_position != condition.position
                or source.definition_id != condition.definition_id
                for source, condition in zip(source_findings, conditions, strict=True)
            )
        ):
            raise IncompleteRegressionEvidence
        variants = {
            variant.variant_key: variant
            for variant in await session.scalars(
                select(ExperimentVariantRecord).where(
                    ExperimentVariantRecord.experiment_id == run.experiment_id
                )
            )
        }
        if set(variants) != {"A", "B"}:
            raise IncompleteRegressionEvidence

        check = RegressionCheckRecord(
            experiment_run_id=run.id,
            experiment_id=run.experiment_id,
            analysis_id=analysis.id,
            regression_policy_id=policy.id,
            baseline_variant=payload.baseline_variant,
            candidate_variant=payload.candidate_variant,
            classification=RegressionClassification.NO_REGRESSION_DETECTED,
            policy_name=policy.name,
            policy_description=policy.description,
            minimum_pass_rate_drop=policy.minimum_pass_rate_drop,
            minimum_sample_size=policy.minimum_sample_size,
            baseline_provenance=variants[payload.baseline_variant].provenance,
            candidate_provenance=variants[payload.candidate_variant].provenance,
        )
        session.add(check)
        await session.flush()

        classifications: list[RegressionClassification] = []
        for source, condition in zip(source_findings, conditions, strict=True):
            metrics: dict[str, int | float | bool | None] = {}
            if source.eligible:
                metrics = _oriented_metrics(source, payload.baseline_variant)
                effect = metrics["candidate_minus_baseline"]
                if source.sample_size is None or source.sample_size < policy.minimum_sample_size:
                    classification = RegressionClassification.INSUFFICIENT_EVIDENCE
                elif isinstance(effect, float) and effect <= -policy.minimum_pass_rate_drop:
                    classification = RegressionClassification.REGRESSION_DETECTED
                else:
                    classification = RegressionClassification.NO_REGRESSION_DETECTED
            else:
                classification = RegressionClassification.INSUFFICIENT_EVIDENCE
            classifications.append(classification)
            session.add(
                RegressionFindingRecord(
                    regression_check_id=check.id,
                    condition_position=condition.position,
                    analysis_id=analysis.id,
                    definition_id=condition.definition_id,
                    definition_name=condition.definition_name,
                    evaluator_kind=condition.evaluator_kind,
                    evaluator_config=condition.evaluator_config,
                    baseline_variant=payload.baseline_variant,
                    candidate_variant=payload.candidate_variant,
                    source_eligible=source.eligible,
                    source_ineligible_reason=source.ineligible_reason,
                    classification=classification,
                    minimum_pass_rate_drop=policy.minimum_pass_rate_drop,
                    minimum_sample_size=policy.minimum_sample_size,
                    sample_size=source.sample_size,
                    both_passed_count=source.both_passed_count,
                    both_failed_count=source.both_failed_count,
                    discordant_count=source.discordant_count,
                    p_value=source.p_value,
                    rejects_null=source.rejects_null,
                    source_interval_lower=source.confidence_interval_lower,
                    source_interval_upper=source.confidence_interval_upper,
                    **metrics,
                )
            )
        if RegressionClassification.REGRESSION_DETECTED in classifications:
            check.classification = RegressionClassification.REGRESSION_DETECTED
        elif RegressionClassification.INSUFFICIENT_EVIDENCE in classifications:
            check.classification = RegressionClassification.INSUFFICIENT_EVIDENCE
        await session.flush()
        response = await _check_schema(session, check)
    return response


async def get_regression_check(session: AsyncSession, check_id: UUID) -> RegressionCheck | None:
    record = await _get_check_record(session, check_id)
    return None if record is None else await _check_schema(session, record)


async def list_regression_checks(
    session: AsyncSession, params: RegressionCheckListParams
) -> RegressionCheckList:
    statement = select(RegressionCheckRecord)
    if params.experiment_run_id is not None:
        statement = statement.where(
            RegressionCheckRecord.experiment_run_id == params.experiment_run_id
        )
    records = list(
        await session.scalars(
            statement.order_by(
                RegressionCheckRecord.created_at.desc(), RegressionCheckRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return RegressionCheckList(
        items=[RegressionCheckSummary.model_validate(item) for item in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )


async def create_regression_policy(
    session: AsyncSession, payload: RegressionPolicyCreate
) -> RegressionPolicy:
    record = RegressionPolicyRecord(**payload.model_dump(mode="python"))
    async with session.begin():
        session.add(record)
    await session.refresh(record)
    return RegressionPolicy.model_validate(record)


async def get_regression_policy(session: AsyncSession, policy_id: UUID) -> RegressionPolicy | None:
    record = await session.get(RegressionPolicyRecord, policy_id)
    return None if record is None else RegressionPolicy.model_validate(record)


async def list_regression_policies(
    session: AsyncSession, params: RegressionPolicyListParams
) -> RegressionPolicyList:
    records = list(
        await session.scalars(
            select(RegressionPolicyRecord)
            .order_by(RegressionPolicyRecord.created_at.desc(), RegressionPolicyRecord.id.desc())
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return RegressionPolicyList(
        items=[RegressionPolicy.model_validate(item) for item in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )
