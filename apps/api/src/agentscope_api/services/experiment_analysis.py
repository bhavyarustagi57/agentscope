from __future__ import annotations

import asyncio
from collections.abc import Sequence
from math import comb
from random import Random
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from agentscope_api.models.experiment import (
    ExperimentConditionAnalysisRecord,
    ExperimentEvaluationConditionRecord,
    ExperimentRunAnalysisRecord,
    ExperimentRunRecord,
    ExperimentRunResultRecord,
    ExperimentSubjectRecord,
)
from agentscope_api.schemas.experiment_analysis import (
    ChangeDirection,
    EligibleConditionResult,
    ExperimentChangedSubject,
    ExperimentChangedSubjectList,
    ExperimentChangeListParams,
    ExperimentConditionAnalysis,
    ExperimentRunAnalysis,
    IneligibleConditionResult,
    PairedBinaryStatistics,
)
from agentscope_api.services.experiment_runs import ExperimentRunNotFound

BOOTSTRAP_ITERATIONS = 10_000
BOOTSTRAP_SEED = 6_003
CONFIDENCE_LEVEL = 0.95
ALPHA = 0.05
_BINARY_OUTCOMES = {"passed", "failed"}
_PERSISTED_STAT_FIELDS = (
    "sample_size",
    "a_passed_count",
    "a_failed_count",
    "b_passed_count",
    "b_failed_count",
    "a_pass_rate",
    "b_pass_rate",
    "pass_rate_difference",
    "both_passed_count",
    "both_passed_rate",
    "both_failed_count",
    "both_failed_rate",
    "a_only_passed_count",
    "a_only_passed_rate",
    "b_only_passed_count",
    "b_only_passed_rate",
    "matched_pairs_odds_ratio",
    "confidence_interval_lower",
    "confidence_interval_upper",
    "discordant_count",
    "p_value",
    "rejects_null",
)


class ExperimentRunNotCompleted(Exception):
    pass


class IncompleteExperimentPopulation(Exception):
    pass


class ExperimentAnalysisNotFound(Exception):
    pass


class ExperimentConditionNotFound(Exception):
    pass


class ExperimentConditionNotAnalyzable(Exception):
    pass


def _exact_mcnemar_p_value(a_only: int, b_only: int) -> float:
    discordant = a_only + b_only
    if not discordant:
        return 1.0
    lower_tail = sum(comb(discordant, index) for index in range(min(a_only, b_only) + 1))
    return min(1.0, 2 * lower_tail / (1 << discordant))


def _percentile(sorted_values: list[float], quantile: float) -> float:
    position = (len(sorted_values) - 1) * quantile
    lower = int(position)
    fraction = position - lower
    if not fraction:
        return sorted_values[lower]
    return sorted_values[lower] + fraction * (
        sorted_values[lower + 1] - sorted_values[lower]
    )


def _paired_bootstrap_interval(
    sample_size: int,
    a_only: int,
    b_only: int,
    *,
    seed: int,
    iterations: int,
) -> tuple[float, float]:
    random = Random(seed)
    b_only_probability = b_only / sample_size
    a_only_given_not_b = a_only / (sample_size - b_only) if b_only < sample_size else 0
    differences: list[float] = []
    for _ in range(iterations):
        sampled_b_only = random.binomialvariate(sample_size, b_only_probability)
        sampled_a_only = random.binomialvariate(
            sample_size - sampled_b_only, a_only_given_not_b
        )
        differences.append((sampled_b_only - sampled_a_only) / sample_size)
    differences.sort()
    tail = (1 - CONFIDENCE_LEVEL) / 2
    return _percentile(differences, tail), _percentile(differences, 1 - tail)


def calculate_paired_binary_statistics(
    pairs: Sequence[tuple[str, str]],
    *,
    seed: int = BOOTSTRAP_SEED,
    iterations: int = BOOTSTRAP_ITERATIONS,
) -> PairedBinaryStatistics:
    if not pairs:
        raise ValueError("paired analysis requires at least one subject")
    if any(a not in _BINARY_OUTCOMES or b not in _BINARY_OUTCOMES for a, b in pairs):
        raise ValueError("paired outcomes must be passed or failed")
    if iterations < 1:
        raise ValueError("bootstrap iterations must be positive")

    both_passed = sum(a == b == "passed" for a, b in pairs)
    both_failed = sum(a == b == "failed" for a, b in pairs)
    a_only = sum(a == "passed" and b == "failed" for a, b in pairs)
    b_only = sum(a == "failed" and b == "passed" for a, b in pairs)
    sample_size = len(pairs)
    a_passed = both_passed + a_only
    b_passed = both_passed + b_only
    lower, upper = _paired_bootstrap_interval(
        sample_size, a_only, b_only, seed=seed, iterations=iterations
    )
    p_value = _exact_mcnemar_p_value(a_only, b_only)

    return PairedBinaryStatistics(
        sample_size=sample_size,
        a_passed_count=a_passed,
        a_failed_count=sample_size - a_passed,
        b_passed_count=b_passed,
        b_failed_count=sample_size - b_passed,
        a_pass_rate=a_passed / sample_size,
        b_pass_rate=b_passed / sample_size,
        pass_rate_difference=(b_passed - a_passed) / sample_size,
        both_passed_count=both_passed,
        both_passed_rate=both_passed / sample_size,
        both_failed_count=both_failed,
        both_failed_rate=both_failed / sample_size,
        a_only_passed_count=a_only,
        a_only_passed_rate=a_only / sample_size,
        b_only_passed_count=b_only,
        b_only_passed_rate=b_only / sample_size,
        matched_pairs_odds_ratio=b_only / a_only if a_only else None,
        confidence_level=CONFIDENCE_LEVEL,
        confidence_interval_lower=max(-1.0, lower),
        confidence_interval_upper=min(1.0, upper),
        confidence_method="paired_percentile_bootstrap",
        test_method="exact_two_sided_mcnemar_binomial",
        discordant_count=a_only + b_only,
        test_statistic=None,
        p_value=p_value,
        alpha=ALPHA,
        rejects_null=p_value < ALPHA,
    )


def _eligible_result(record: ExperimentConditionAnalysisRecord) -> EligibleConditionResult:
    return EligibleConditionResult.model_validate(
        {
            "eligible": True,
            **{field: getattr(record, field) for field in _PERSISTED_STAT_FIELDS},
            "confidence_level": CONFIDENCE_LEVEL,
            "confidence_method": "paired_percentile_bootstrap",
            "test_method": "exact_two_sided_mcnemar_binomial",
            "test_statistic": None,
            "alpha": ALPHA,
        }
    )


async def _load_analysis(
    session: AsyncSession, record: ExperimentRunAnalysisRecord
) -> ExperimentRunAnalysis:
    rows = (
        await session.execute(
            select(ExperimentConditionAnalysisRecord, ExperimentEvaluationConditionRecord)
            .join(
                ExperimentEvaluationConditionRecord,
                and_(
                    ExperimentEvaluationConditionRecord.experiment_id
                    == ExperimentConditionAnalysisRecord.experiment_id,
                    ExperimentEvaluationConditionRecord.position
                    == ExperimentConditionAnalysisRecord.condition_position,
                    ExperimentEvaluationConditionRecord.definition_id
                    == ExperimentConditionAnalysisRecord.definition_id,
                ),
            )
            .where(ExperimentConditionAnalysisRecord.analysis_id == record.id)
            .order_by(ExperimentConditionAnalysisRecord.condition_position)
        )
    ).all()
    return ExperimentRunAnalysis(
        id=record.id,
        run_id=record.run_id,
        experiment_id=record.experiment_id,
        analysis_schema_version=record.analysis_schema_version,
        confidence_method=record.confidence_method,
        confidence_level=record.confidence_level,
        bootstrap_seed=record.bootstrap_seed,
        bootstrap_iterations=record.bootstrap_iterations,
        hypothesis_test_method=record.hypothesis_test_method,
        alpha=record.alpha,
        created_at=record.created_at,
        conditions=[
            ExperimentConditionAnalysis(
                condition_position=condition.position,
                definition_id=condition.definition_id,
                definition_name=condition.definition_name,
                evaluator_kind=condition.evaluator_kind,
                result=(
                    _eligible_result(analysis)
                    if analysis.eligible
                    else IneligibleConditionResult(
                        ineligible_reason="non_binary_outcome"
                    )
                ),
            )
            for analysis, condition in rows
        ],
    )


async def create_experiment_analysis(
    session: AsyncSession, run_id: UUID
) -> ExperimentRunAnalysis:
    async with session.begin():
        run = await session.scalar(
            select(ExperimentRunRecord)
            .where(ExperimentRunRecord.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise ExperimentRunNotFound
        if run.status != "completed":
            raise ExperimentRunNotCompleted
        existing = await session.scalar(
            select(ExperimentRunAnalysisRecord).where(
                ExperimentRunAnalysisRecord.run_id == run_id
            )
        )
        if existing is not None:
            return await _load_analysis(session, existing)

        subjects = list(
            await session.scalars(
                select(ExperimentSubjectRecord)
                .where(ExperimentSubjectRecord.experiment_id == run.experiment_id)
                .order_by(
                    ExperimentSubjectRecord.position,
                    ExperimentSubjectRecord.variant_key,
                )
            )
        )
        conditions = list(
            await session.scalars(
                select(ExperimentEvaluationConditionRecord)
                .where(
                    ExperimentEvaluationConditionRecord.experiment_id == run.experiment_id
                )
                .order_by(ExperimentEvaluationConditionRecord.position)
            )
        )
        results = list(
            await session.scalars(
                select(ExperimentRunResultRecord)
                .where(ExperimentRunResultRecord.run_id == run_id)
                .order_by(
                    ExperimentRunResultRecord.subject_position,
                    ExperimentRunResultRecord.variant_key,
                    ExperimentRunResultRecord.condition_position,
                )
            )
        )
        subject_map = {
            (subject.position, subject.variant_key): subject for subject in subjects
        }
        condition_map = {condition.position: condition for condition in conditions}
        result_map = {
            (result.subject_position, result.variant_key, result.condition_position): result
            for result in results
        }
        expected_keys = {
            (subject.position, subject.variant_key, condition.position)
            for subject in subjects
            for condition in conditions
        }
        identities_valid = all(
            result.trace_id
            == subject_map[(result.subject_position, result.variant_key)].trace_id
            and result.definition_id == condition_map[result.condition_position].definition_id
            for result in results
            if (result.subject_position, result.variant_key) in subject_map
            and result.condition_position in condition_map
        )
        if (
            not subjects
            or not conditions
            or len(subjects) % 2
            or run.expected_decision_count != len(expected_keys)
            or len(results) != run.expected_decision_count
            or set(result_map) != expected_keys
            or not identities_valid
        ):
            raise IncompleteExperimentPopulation

        record = ExperimentRunAnalysisRecord(
            run_id=run.id,
            experiment_id=run.experiment_id,
            analysis_schema_version="1",
            confidence_method="paired_percentile_bootstrap",
            confidence_level=CONFIDENCE_LEVEL,
            bootstrap_seed=BOOTSTRAP_SEED,
            bootstrap_iterations=BOOTSTRAP_ITERATIONS,
            hypothesis_test_method="exact_two_sided_mcnemar_binomial",
            alpha=ALPHA,
        )
        session.add(record)
        await session.flush()
        positions = sorted({subject.position for subject in subjects})
        for condition in conditions:
            pairs = [
                (
                    result_map[(position, "A", condition.position)].outcome,
                    result_map[(position, "B", condition.position)].outcome,
                )
                for position in positions
            ]
            if any(a not in _BINARY_OUTCOMES or b not in _BINARY_OUTCOMES for a, b in pairs):
                condition_analysis = ExperimentConditionAnalysisRecord(
                    analysis_id=record.id,
                    experiment_id=run.experiment_id,
                    condition_position=condition.position,
                    definition_id=condition.definition_id,
                    eligible=False,
                    ineligible_reason="non_binary_outcome",
                )
            else:
                statistics = await asyncio.to_thread(
                    calculate_paired_binary_statistics,
                    pairs,
                    seed=BOOTSTRAP_SEED + condition.position,
                    iterations=BOOTSTRAP_ITERATIONS,
                )
                condition_analysis = ExperimentConditionAnalysisRecord(
                    analysis_id=record.id,
                    experiment_id=run.experiment_id,
                    condition_position=condition.position,
                    definition_id=condition.definition_id,
                    eligible=True,
                    **{
                        field: getattr(statistics, field)
                        for field in _PERSISTED_STAT_FIELDS
                    },
                )
            session.add(condition_analysis)
        await session.flush()
        await session.refresh(record)
        return await _load_analysis(session, record)


async def get_experiment_analysis(
    session: AsyncSession, run_id: UUID
) -> ExperimentRunAnalysis:
    if await session.get(ExperimentRunRecord, run_id) is None:
        raise ExperimentRunNotFound
    record = await session.scalar(
        select(ExperimentRunAnalysisRecord).where(
            ExperimentRunAnalysisRecord.run_id == run_id
        )
    )
    if record is None:
        raise ExperimentAnalysisNotFound
    return await _load_analysis(session, record)


async def list_changed_subjects(
    session: AsyncSession,
    run_id: UUID,
    params: ExperimentChangeListParams,
) -> ExperimentChangedSubjectList:
    analysis = await session.scalar(
        select(ExperimentRunAnalysisRecord).where(
            ExperimentRunAnalysisRecord.run_id == run_id
        )
    )
    if analysis is None:
        if await session.get(ExperimentRunRecord, run_id) is None:
            raise ExperimentRunNotFound
        raise ExperimentAnalysisNotFound
    condition_analysis = await session.get(
        ExperimentConditionAnalysisRecord,
        (analysis.id, params.condition_position),
    )
    if condition_analysis is None:
        raise ExperimentConditionNotFound
    if not condition_analysis.eligible:
        raise ExperimentConditionNotAnalyzable

    a_result = aliased(ExperimentRunResultRecord)
    b_result = aliased(ExperimentRunResultRecord)
    statement = (
        select(
            a_result.subject_position,
            a_result.trace_id.label("a_trace_id"),
            b_result.trace_id.label("b_trace_id"),
            a_result.outcome.label("a_outcome"),
            b_result.outcome.label("b_outcome"),
            ExperimentEvaluationConditionRecord.definition_id,
            ExperimentEvaluationConditionRecord.definition_name,
        )
        .select_from(a_result)
        .join(
            b_result,
            and_(
                b_result.run_id == a_result.run_id,
                b_result.subject_position == a_result.subject_position,
                b_result.condition_position == a_result.condition_position,
                b_result.variant_key == "B",
            ),
        )
        .join(
            ExperimentEvaluationConditionRecord,
            and_(
                ExperimentEvaluationConditionRecord.experiment_id
                == a_result.experiment_id,
                ExperimentEvaluationConditionRecord.position
                == a_result.condition_position,
            ),
        )
        .where(
            a_result.run_id == run_id,
            a_result.variant_key == "A",
            a_result.condition_position == params.condition_position,
            a_result.outcome.in_(_BINARY_OUTCOMES),
            b_result.outcome.in_(_BINARY_OUTCOMES),
            a_result.outcome != b_result.outcome,
        )
    )
    if params.direction is ChangeDirection.A_PASS_B_FAIL:
        statement = statement.where(
            a_result.outcome == "passed", b_result.outcome == "failed"
        )
    elif params.direction is ChangeDirection.A_FAIL_B_PASS:
        statement = statement.where(
            a_result.outcome == "failed", b_result.outcome == "passed"
        )
    rows = (
        await session.execute(
            statement.order_by(a_result.subject_position)
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    ).all()
    return ExperimentChangedSubjectList(
        items=[
            ExperimentChangedSubject(
                subject_position=row.subject_position,
                condition_position=params.condition_position,
                definition_id=row.definition_id,
                definition_name=row.definition_name,
                direction=(
                    ChangeDirection.A_PASS_B_FAIL
                    if row.a_outcome == "passed"
                    else ChangeDirection.A_FAIL_B_PASS
                ),
                a_trace_id=row.a_trace_id,
                b_trace_id=row.b_trace_id,
                a_outcome=cast(Literal["passed", "failed"], row.a_outcome),
                b_outcome=cast(Literal["passed", "failed"], row.b_outcome),
            )
            for row in rows[: params.page_size]
        ],
        has_more=len(rows) > params.page_size,
    )
