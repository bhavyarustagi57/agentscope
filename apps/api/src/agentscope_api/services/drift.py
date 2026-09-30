from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentscope_api.models.drift import (
    DriftComparisonRecord,
    DriftFindingRecord,
    DriftPolicyRecord,
    DriftPolicyRuleRecord,
)
from agentscope_api.models.monitoring import MonitoringSnapshotRecord
from agentscope_api.schemas.drift import (
    DriftClassification,
    DriftComparison,
    DriftComparisonCreate,
    DriftComparisonList,
    DriftComparisonListParams,
    DriftComparisonSummary,
    DriftDirection,
    DriftFinding,
    DriftMetric,
    DriftPolicy,
    DriftPolicyCreate,
    DriftPolicyList,
    DriftPolicyListParams,
    DriftPolicyRule,
    DriftPolicyRuleCreate,
    DriftThresholdType,
)


class DriftSnapshotEvidence(Protocol):
    trace_count: int
    successful_trace_count: int
    failed_trace_count: int
    success_rate: float | None
    failure_rate: float | None
    duration_sample_count: int
    mean_duration_ms: float | None
    p95_duration_ms: float | None
    token_sample_count: int
    mean_total_tokens: float | None
    evaluated_result_count: int
    passed_evaluation_count: int
    evaluator_error_count: int
    valid_binary_evaluation_count: int
    evaluation_pass_rate: float | None
    evaluation_error_rate: float | None


@dataclass(frozen=True)
class DriftFindingEvidence:
    baseline_sample_count: int
    current_sample_count: int
    baseline_value: float | None
    current_value: float | None
    absolute_delta: float | None
    relative_delta: float | None
    classification: DriftClassification
    z_statistic: float | None
    p_value: float | None


_VALUE_FIELDS = {
    DriftMetric.TRACE_FAILURE_RATE: "failure_rate",
    DriftMetric.TRACE_SUCCESS_RATE: "success_rate",
    DriftMetric.EVALUATION_PASS_RATE: "evaluation_pass_rate",
    DriftMetric.EVALUATION_ERROR_RATE: "evaluation_error_rate",
    DriftMetric.MEAN_DURATION_MS: "mean_duration_ms",
    DriftMetric.P95_DURATION_MS: "p95_duration_ms",
    DriftMetric.MEAN_TOTAL_TOKENS: "mean_total_tokens",
    DriftMetric.TRACE_COUNT: "trace_count",
}

_SAMPLE_FIELDS = {
    DriftMetric.TRACE_FAILURE_RATE: "trace_count",
    DriftMetric.TRACE_SUCCESS_RATE: "trace_count",
    DriftMetric.EVALUATION_PASS_RATE: "valid_binary_evaluation_count",
    DriftMetric.EVALUATION_ERROR_RATE: "evaluated_result_count",
    DriftMetric.MEAN_DURATION_MS: "duration_sample_count",
    DriftMetric.P95_DURATION_MS: "duration_sample_count",
    DriftMetric.MEAN_TOTAL_TOKENS: "token_sample_count",
    DriftMetric.TRACE_COUNT: "trace_count",
}

_RATE_EVENT_FIELDS = {
    DriftMetric.TRACE_FAILURE_RATE: "failed_trace_count",
    DriftMetric.TRACE_SUCCESS_RATE: "successful_trace_count",
    DriftMetric.EVALUATION_PASS_RATE: "passed_evaluation_count",
    DriftMetric.EVALUATION_ERROR_RATE: "evaluator_error_count",
}


def _finite(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _rate_statistics(
    metric: DriftMetric,
    baseline: DriftSnapshotEvidence,
    current: DriftSnapshotEvidence,
    baseline_samples: int,
    current_samples: int,
) -> tuple[float | None, float | None]:
    event_field = _RATE_EVENT_FIELDS.get(metric)
    if event_field is None or baseline_samples <= 0 or current_samples <= 0:
        return None, None
    baseline_events = getattr(baseline, event_field)
    current_events = getattr(current, event_field)
    if not (
        isinstance(baseline_events, int)
        and isinstance(current_events, int)
        and 0 <= baseline_events <= baseline_samples
        and 0 <= current_events <= current_samples
    ):
        return None, None
    pooled = (baseline_events + current_events) / (baseline_samples + current_samples)
    standard_error = math.sqrt(pooled * (1 - pooled) * (1 / baseline_samples + 1 / current_samples))
    if standard_error == 0:
        return None, None
    statistic = (
        current_events / current_samples - baseline_events / baseline_samples
    ) / standard_error
    p_value = math.erfc(abs(statistic) / math.sqrt(2))
    return statistic, p_value


def evaluate_rule(
    rule: DriftPolicyRuleCreate,
    baseline: DriftSnapshotEvidence,
    current: DriftSnapshotEvidence,
) -> DriftFindingEvidence:
    baseline_samples = int(getattr(baseline, _SAMPLE_FIELDS[rule.metric]))
    current_samples = int(getattr(current, _SAMPLE_FIELDS[rule.metric]))
    baseline_value = _finite(getattr(baseline, _VALUE_FIELDS[rule.metric]))
    current_value = _finite(getattr(current, _VALUE_FIELDS[rule.metric]))
    absolute_delta = (
        None if baseline_value is None or current_value is None else current_value - baseline_value
    )
    relative_delta = (
        None
        if absolute_delta is None or baseline_value is None or baseline_value == 0
        else absolute_delta / abs(baseline_value)
    )
    statistic, p_value = _rate_statistics(
        rule.metric, baseline, current, baseline_samples, current_samples
    )
    evidence_sufficient = (
        baseline_samples >= rule.minimum_baseline_samples
        and current_samples >= rule.minimum_current_samples
        and baseline_value is not None
        and current_value is not None
        and not (rule.threshold_type is DriftThresholdType.RELATIVE and relative_delta is None)
    )
    if not evidence_sufficient:
        classification = DriftClassification.INSUFFICIENT_EVIDENCE
    else:
        change = (
            absolute_delta if rule.threshold_type is DriftThresholdType.ABSOLUTE else relative_delta
        )
        assert change is not None
        unfavorable_change = change if rule.direction is DriftDirection.INCREASE else -change
        classification = (
            DriftClassification.DRIFT_DETECTED
            if unfavorable_change > rule.practical_threshold
            or math.isclose(
                unfavorable_change,
                rule.practical_threshold,
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
            else DriftClassification.NO_DRIFT_DETECTED
        )
    return DriftFindingEvidence(
        baseline_sample_count=baseline_samples,
        current_sample_count=current_samples,
        baseline_value=baseline_value,
        current_value=current_value,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        classification=classification,
        z_statistic=statistic,
        p_value=p_value,
    )


def classify_overall(
    classifications: list[DriftClassification],
) -> DriftClassification:
    if DriftClassification.DRIFT_DETECTED in classifications:
        return DriftClassification.DRIFT_DETECTED
    if DriftClassification.INSUFFICIENT_EVIDENCE in classifications:
        return DriftClassification.INSUFFICIENT_EVIDENCE
    return DriftClassification.NO_DRIFT_DETECTED


class DriftPolicyNotFound(Exception):
    pass


class MonitoringSnapshotNotFound(Exception):
    pass


class DriftSnapshotsMustDiffer(Exception):
    pass


class DriftSnapshotNotCompleted(Exception):
    pass


class DriftMonitorMismatch(Exception):
    pass


class DriftWindowDurationMismatch(Exception):
    pass


class DriftBaselineNotBeforeCurrent(Exception):
    pass


class DriftWindowsOverlap(Exception):
    pass


class DriftPolicyEmpty(Exception):
    pass


def _policy_schema(policy: DriftPolicyRecord, rules: list[DriftPolicyRuleRecord]) -> DriftPolicy:
    return DriftPolicy(
        id=policy.id,
        name=policy.name,
        description=policy.description,
        created_at=policy.created_at,
        rules=[DriftPolicyRule.model_validate(rule) for rule in rules],
    )


async def _load_policy_rules(session: AsyncSession, policy_id: UUID) -> list[DriftPolicyRuleRecord]:
    return list(
        await session.scalars(
            select(DriftPolicyRuleRecord)
            .where(DriftPolicyRuleRecord.drift_policy_id == policy_id)
            .order_by(DriftPolicyRuleRecord.position)
        )
    )


async def create_drift_policy(session: AsyncSession, payload: DriftPolicyCreate) -> DriftPolicy:
    policy = DriftPolicyRecord(name=payload.name, description=payload.description)
    async with session.begin():
        session.add(policy)
        await session.flush()
        rules = [
            DriftPolicyRuleRecord(
                drift_policy_id=policy.id,
                position=position,
                **rule.model_dump(mode="python"),
            )
            for position, rule in enumerate(payload.rules)
        ]
        session.add_all(rules)
        await session.flush()
        result = _policy_schema(policy, rules)
    return result


async def get_drift_policy(session: AsyncSession, policy_id: UUID) -> DriftPolicy | None:
    policy = await session.get(DriftPolicyRecord, policy_id)
    if policy is None:
        return None
    return _policy_schema(policy, await _load_policy_rules(session, policy_id))


async def list_drift_policies(
    session: AsyncSession, params: DriftPolicyListParams
) -> DriftPolicyList:
    policies = list(
        await session.scalars(
            select(DriftPolicyRecord)
            .order_by(DriftPolicyRecord.created_at.desc(), DriftPolicyRecord.id.desc())
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    visible = policies[: params.page_size]
    rules = (
        list(
            await session.scalars(
                select(DriftPolicyRuleRecord)
                .where(DriftPolicyRuleRecord.drift_policy_id.in_([item.id for item in visible]))
                .order_by(DriftPolicyRuleRecord.drift_policy_id, DriftPolicyRuleRecord.position)
            )
        )
        if visible
        else []
    )
    rules_by_policy: dict[UUID, list[DriftPolicyRuleRecord]] = {}
    for rule in rules:
        rules_by_policy.setdefault(rule.drift_policy_id, []).append(rule)
    return DriftPolicyList(
        items=[_policy_schema(item, rules_by_policy.get(item.id, [])) for item in visible],
        has_more=len(policies) > params.page_size,
    )


async def _comparison_schema(
    session: AsyncSession, comparison: DriftComparisonRecord
) -> DriftComparison:
    findings = list(
        await session.scalars(
            select(DriftFindingRecord)
            .where(DriftFindingRecord.drift_comparison_id == comparison.id)
            .order_by(DriftFindingRecord.rule_position)
        )
    )
    return DriftComparison(
        **DriftComparisonSummary.model_validate(comparison).model_dump(),
        findings=[DriftFinding.model_validate(item) for item in findings],
    )


async def create_drift_comparison(
    session: AsyncSession, payload: DriftComparisonCreate
) -> DriftComparison:
    async with session.begin():
        return await create_drift_comparison_in_transaction(session, payload)


async def create_drift_comparison_in_transaction(
    session: AsyncSession, payload: DriftComparisonCreate
) -> DriftComparison:
    if payload.baseline_snapshot_id == payload.current_snapshot_id:
        raise DriftSnapshotsMustDiffer
    policy = await session.get(DriftPolicyRecord, payload.drift_policy_id)
    if policy is None:
        raise DriftPolicyNotFound
    rules = await _load_policy_rules(session, policy.id)
    if not rules:
        raise DriftPolicyEmpty
    baseline = await session.get(MonitoringSnapshotRecord, payload.baseline_snapshot_id)
    current = await session.get(MonitoringSnapshotRecord, payload.current_snapshot_id)
    if baseline is None or current is None:
        raise MonitoringSnapshotNotFound
    if baseline.status != "completed" or current.status != "completed":
        raise DriftSnapshotNotCompleted
    if baseline.monitoring_definition_id != current.monitoring_definition_id:
        raise DriftMonitorMismatch
    if baseline.window_end - baseline.window_start != current.window_end - current.window_start:
        raise DriftWindowDurationMismatch
    if baseline.window_start >= current.window_start:
        raise DriftBaselineNotBeforeCurrent
    if baseline.window_end > current.window_start:
        raise DriftWindowsOverlap

    evidence = [
        evaluate_rule(DriftPolicyRuleCreate.model_validate(rule), baseline, current)
        for rule in rules
    ]
    overall = classify_overall([item.classification for item in evidence])
    comparison = DriftComparisonRecord(
        monitoring_definition_id=baseline.monitoring_definition_id,
        drift_policy_id=policy.id,
        baseline_snapshot_id=baseline.id,
        current_snapshot_id=current.id,
        classification=overall,
        policy_name=policy.name,
        policy_description=policy.description,
    )
    session.add(comparison)
    await session.flush()
    session.add_all(
        [
            DriftFindingRecord(
                drift_comparison_id=comparison.id,
                rule_position=rule.position,
                metric=rule.metric,
                direction=rule.direction,
                threshold_type=rule.threshold_type,
                practical_threshold=rule.practical_threshold,
                minimum_baseline_samples=rule.minimum_baseline_samples,
                minimum_current_samples=rule.minimum_current_samples,
                baseline_sample_count=result.baseline_sample_count,
                current_sample_count=result.current_sample_count,
                baseline_value=result.baseline_value,
                current_value=result.current_value,
                absolute_delta=result.absolute_delta,
                relative_delta=result.relative_delta,
                classification=result.classification,
                z_statistic=result.z_statistic,
                p_value=result.p_value,
            )
            for rule, result in zip(rules, evidence, strict=True)
        ]
    )
    await session.flush()
    return await _comparison_schema(session, comparison)


async def get_drift_comparison(
    session: AsyncSession, comparison_id: UUID
) -> DriftComparison | None:
    comparison = await session.get(DriftComparisonRecord, comparison_id)
    return None if comparison is None else await _comparison_schema(session, comparison)


async def list_drift_comparisons(
    session: AsyncSession, params: DriftComparisonListParams
) -> DriftComparisonList:
    statement = select(DriftComparisonRecord)
    filters = (
        (DriftComparisonRecord.monitoring_definition_id, params.monitoring_definition_id),
        (DriftComparisonRecord.drift_policy_id, params.drift_policy_id),
        (DriftComparisonRecord.current_snapshot_id, params.current_snapshot_id),
        (DriftComparisonRecord.classification, params.classification),
    )
    for column, value in filters:
        if value is not None:
            statement = statement.where(column == value)
    records = list(
        await session.scalars(
            statement.order_by(
                DriftComparisonRecord.created_at.desc(), DriftComparisonRecord.id.desc()
            )
            .offset(params.offset)
            .limit(params.page_size + 1)
        )
    )
    return DriftComparisonList(
        items=[DriftComparisonSummary.model_validate(item) for item in records[: params.page_size]],
        has_more=len(records) > params.page_size,
    )
