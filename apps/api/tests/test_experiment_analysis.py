from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from test_experiment_execution import _definition, _ready_experiment, _trace

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.experiment import (
    ExperimentConditionAnalysisRecord,
    ExperimentEvaluationConditionRecord,
    ExperimentRecord,
    ExperimentRunAnalysisRecord,
    ExperimentRunRecord,
    ExperimentRunResultRecord,
    ExperimentSubjectRecord,
)
from agentscope_api.services.experiment_analysis import (
    calculate_paired_binary_statistics,
    create_experiment_analysis,
)
from agentscope_api.services.experiment_execution import ProcessOutcome, process_experiment_message


def test_balanced_paired_table_has_zero_directional_effect() -> None:
    result = calculate_paired_binary_statistics(
        [("passed", "passed"), ("failed", "failed"), ("passed", "failed"), ("failed", "passed")]
    )

    assert result.sample_size == 4
    assert (result.a_passed_count, result.a_failed_count) == (2, 2)
    assert (result.b_passed_count, result.b_failed_count) == (2, 2)
    assert result.a_pass_rate == result.b_pass_rate == 0.5
    assert result.pass_rate_difference == 0.0
    assert (
        result.both_passed_count,
        result.both_failed_count,
        result.a_only_passed_count,
        result.b_only_passed_count,
    ) == (1, 1, 1, 1)
    assert result.matched_pairs_odds_ratio == 1.0
    assert result.discordant_count == 2
    assert result.p_value == 1.0
    assert result.rejects_null is False
    assert result.confidence_interval_lower <= 0 <= result.confidence_interval_upper


@pytest.mark.parametrize(
    ("pairs", "expected_difference", "expected_odds_ratio"),
    [
        (
            [("passed", "failed"), ("failed", "passed")] * 1
            + [("failed", "passed")] * 2
            + [("passed", "passed"), ("failed", "failed")],
            1 / 3,
            3.0,
        ),
        (
            [("failed", "passed"), ("passed", "failed")] * 1
            + [("passed", "failed")] * 2
            + [("passed", "passed"), ("failed", "failed")],
            -1 / 3,
            1 / 3,
        ),
    ],
)
def test_direction_is_consistently_b_minus_a(
    pairs: list[tuple[str, str]],
    expected_difference: float,
    expected_odds_ratio: float,
) -> None:
    result = calculate_paired_binary_statistics(pairs)

    assert math.isclose(result.pass_rate_difference, expected_difference)
    assert math.isclose(result.matched_pairs_odds_ratio or -1, expected_odds_ratio)
    assert result.discordant_count == 4
    assert result.p_value == 0.625


def test_perfect_agreement_and_zero_cells_are_finite_or_null() -> None:
    agreement = calculate_paired_binary_statistics(
        [("passed", "passed"), ("failed", "failed")]
    )
    zero_denominator = calculate_paired_binary_statistics(
        [("failed", "passed"), ("failed", "passed")]
    )

    assert agreement.discordant_count == 0
    assert agreement.p_value == 1.0
    assert agreement.test_statistic is None
    assert agreement.matched_pairs_odds_ratio is None
    assert agreement.confidence_interval_lower == agreement.confidence_interval_upper == 0.0
    assert zero_denominator.matched_pairs_odds_ratio is None
    assert zero_denominator.p_value == 0.5
    assert zero_denominator.confidence_interval_lower == 1.0
    assert zero_denominator.confidence_interval_upper == 1.0
    assert "NaN" not in zero_denominator.model_dump_json()
    assert "Infinity" not in zero_denominator.model_dump_json()


def test_one_pair_is_supported_without_non_finite_values() -> None:
    result = calculate_paired_binary_statistics([("failed", "passed")])

    assert result.sample_size == 1
    assert result.pass_rate_difference == 1.0
    assert result.p_value == 1.0
    assert result.confidence_interval_lower == result.confidence_interval_upper == 1.0


@pytest.mark.parametrize(
    "pairs",
    [[], [("error", "passed")], [("passed", "error")]],
)
def test_non_binary_or_empty_pairs_are_rejected(pairs: list[tuple[str, str]]) -> None:
    with pytest.raises(ValueError):
        calculate_paired_binary_statistics(pairs)


def _completed_run(
    client: TestClient, pairs_by_condition: list[list[tuple[str, str]]]
) -> dict[str, object]:
    subject_count = len(pairs_by_condition[0])
    assert all(len(pairs) == subject_count for pairs in pairs_by_condition)
    trace_ids = [
        (_trace(client, f"subject {position} A", "candidate"),
         _trace(client, f"subject {position} B", "candidate"))
        for position in range(subject_count)
    ]
    definition_ids = [
        _definition(client, f"Condition {position}", "exact_match", {"expected": "candidate"})
        for position in range(len(pairs_by_condition))
    ]
    experiment = client.post("/api/v1/experiments", json={"name": "Analysis experiment"}).json()
    configured = client.post(
        f"/api/v1/experiments/{experiment['id']}/configuration",
        json={
            "name": "Analysis experiment",
            "variants": [
                {"key": "A", "name": "Control", "provenance": {}},
                {"key": "B", "name": "Candidate", "provenance": {}},
            ],
            "subjects": [
                {"a_trace_id": a_trace_id, "b_trace_id": b_trace_id}
                for a_trace_id, b_trace_id in trace_ids
            ],
            "evaluation_definition_ids": definition_ids,
        },
    )
    assert configured.status_code == 200, configured.text
    assert client.post(f"/api/v1/experiments/{experiment['id']}/ready").status_code == 200
    run = client.post(f"/api/v1/experiments/{experiment['id']}/runs").json()

    async def complete() -> None:
        run_id = UUID(str(run["id"]))
        experiment_id = UUID(str(experiment["id"]))
        async with SessionLocal() as session, session.begin():
            run_record = await session.get(ExperimentRunRecord, run_id)
            experiment_record = await session.get(ExperimentRecord, experiment_id)
            assert run_record is not None and experiment_record is not None
            run_record.status = "completed"
            run_record.completed_at = datetime.now(UTC)
            experiment_record.status = "completed"
            subjects = list(
                await session.scalars(
                    select(ExperimentSubjectRecord)
                    .where(ExperimentSubjectRecord.experiment_id == experiment_id)
                    .order_by(
                        ExperimentSubjectRecord.position,
                        ExperimentSubjectRecord.variant_key,
                    )
                )
            )
            conditions = list(
                await session.scalars(
                    select(ExperimentEvaluationConditionRecord)
                    .where(ExperimentEvaluationConditionRecord.experiment_id == experiment_id)
                    .order_by(ExperimentEvaluationConditionRecord.position)
                )
            )
            for condition, pairs in zip(conditions, pairs_by_condition, strict=True):
                for subject in subjects:
                    outcome = pairs[subject.position][0 if subject.variant_key == "A" else 1]
                    session.add(
                        ExperimentRunResultRecord(
                            run_id=run_id,
                            experiment_id=experiment_id,
                            subject_position=subject.position,
                            variant_key=subject.variant_key,
                            condition_position=condition.position,
                            trace_id=subject.trace_id,
                            definition_id=condition.definition_id,
                            outcome=outcome,
                            score=1.0 if outcome == "passed" else None,
                            details={},
                            attempt_count=1,
                        )
                    )

    asyncio.run(complete())
    return {**run, "experiment_id": experiment["id"], "trace_ids": trace_ids}


@pytest.mark.usefixtures("clean_database")
def test_analysis_api_persists_independent_condition_results_and_is_idempotent() -> None:
    balanced = [
        ("passed", "passed"),
        ("failed", "failed"),
        ("passed", "failed"),
        ("failed", "passed"),
    ]
    directional = [
        ("failed", "passed"),
        ("failed", "passed"),
        ("passed", "failed"),
        ("failed", "failed"),
    ]
    with TestClient(create_app()) as client:
        run = _completed_run(client, [balanced, directional])
        created = client.post(f"/api/v1/experiment-runs/{run['id']}/analysis")
        repeated = client.post(f"/api/v1/experiment-runs/{run['id']}/analysis")
        fetched = client.get(f"/api/v1/experiment-runs/{run['id']}/analysis")

    assert created.status_code == repeated.status_code == 201
    assert fetched.status_code == 200
    assert created.json() == repeated.json() == fetched.json()
    assert created.json()["analysis_schema_version"] == "1"
    assert created.json()["bootstrap_iterations"] == 10_000
    assert len(created.json()["conditions"]) == 2
    first = created.json()["conditions"][0]["result"]
    second = created.json()["conditions"][1]["result"]
    assert first["eligible"] is True
    assert first["pass_rate_difference"] == 0.0
    assert second["eligible"] is True
    assert second["pass_rate_difference"] == 0.25
    assert first["p_value"] == 1.0
    assert second["p_value"] == 1.0


@pytest.mark.usefixtures("clean_database")
def test_error_outcome_marks_only_its_condition_ineligible() -> None:
    with TestClient(create_app()) as client:
        run = _completed_run(
            client,
            [
                [("passed", "error"), ("failed", "failed")],
                [("passed", "passed"), ("failed", "passed")],
            ],
        )
        response = client.post(f"/api/v1/experiment-runs/{run['id']}/analysis")

    assert response.status_code == 201
    conditions = response.json()["conditions"]
    assert conditions[0]["result"] == {
        "eligible": False,
        "ineligible_reason": "non_binary_outcome",
    }
    assert conditions[1]["result"]["eligible"] is True


@pytest.mark.usefixtures("clean_database")
def test_analysis_rejects_noncompleted_or_incomplete_runs() -> None:
    with TestClient(create_app()) as client:
        unknown_state = client.post(f"/api/v1/experiment-runs/{UUID(int=0)}/analysis")
        assert unknown_state.status_code == 404
        run = _completed_run(client, [[("passed", "passed")]])
        pending = client.post(
            f"/api/v1/experiments/{run['experiment_id']}/runs"
        ).json()
        not_completed = client.post(
            f"/api/v1/experiment-runs/{pending['id']}/analysis"
        )

    assert not_completed.status_code == 409
    assert not_completed.json()["error"]["code"] == "EXPERIMENT_RUN_NOT_COMPLETED"

    async def remove_result() -> None:
        async with SessionLocal() as session, session.begin():
            await session.execute(
                delete(ExperimentRunResultRecord).where(
                    ExperimentRunResultRecord.run_id == UUID(str(run["id"])),
                    ExperimentRunResultRecord.variant_key == "B",
                )
            )

    asyncio.run(remove_result())
    with TestClient(create_app()) as client:
        incomplete = client.post(f"/api/v1/experiment-runs/{run['id']}/analysis")

    assert incomplete.status_code == 409
    assert incomplete.json()["error"]["code"] == "INCOMPLETE_EXPERIMENT_POPULATION"


@pytest.mark.usefixtures("clean_database")
def test_concurrent_analysis_creation_returns_one_canonical_identity() -> None:
    with TestClient(create_app()) as client:
        run = _completed_run(client, [[("passed", "passed"), ("failed", "passed")]])
    run_id = UUID(str(run["id"]))

    async def create() -> object:
        async with SessionLocal() as session:
            return await create_experiment_analysis(session, run_id)

    async def exercise() -> tuple[object, object]:
        first, second = await asyncio.gather(create(), create())
        return first, second

    first, second = asyncio.run(exercise())
    assert first == second

    async def count() -> int:
        async with SessionLocal() as session:
            return int(
                await session.scalar(
                    select(func.count())
                    .select_from(ExperimentRunAnalysisRecord)
                    .where(ExperimentRunAnalysisRecord.run_id == run_id)
                )
                or 0
            )

    assert asyncio.run(count()) == 1


@pytest.mark.usefixtures("clean_database")
def test_changed_subjects_are_attributed_filtered_ordered_and_paginated() -> None:
    pairs = [
        ("passed", "failed"),
        ("failed", "passed"),
        ("failed", "passed"),
        ("passed", "passed"),
    ]
    with TestClient(create_app()) as client:
        run = _completed_run(client, [pairs])
        assert client.post(f"/api/v1/experiment-runs/{run['id']}/analysis").status_code == 201
        first_page = client.get(
            f"/api/v1/experiment-runs/{run['id']}/analysis/changes",
            params={"condition_position": 0, "page_size": 1},
        )
        b_gains = client.get(
            f"/api/v1/experiment-runs/{run['id']}/analysis/changes",
            params={"condition_position": 0, "direction": "A_FAIL_B_PASS"},
        )

    assert first_page.status_code == b_gains.status_code == 200
    assert first_page.json()["has_more"] is True
    assert first_page.json()["items"][0] == {
        "subject_position": 0,
        "condition_position": 0,
        "definition_id": first_page.json()["items"][0]["definition_id"],
        "definition_name": "Condition 0",
        "direction": "A_PASS_B_FAIL",
        "a_trace_id": run["trace_ids"][0][0],
        "b_trace_id": run["trace_ids"][0][1],
        "a_outcome": "passed",
        "b_outcome": "failed",
    }
    assert [item["subject_position"] for item in b_gains.json()["items"]] == [1, 2]
    assert all(item["direction"] == "A_FAIL_B_PASS" for item in b_gains.json()["items"])


@pytest.mark.usefixtures("clean_database")
def test_complete_phase6_workflow_preserves_attributed_analysis_and_history() -> None:
    with TestClient(create_app()) as client:
        experiment, trace_ids, _ = _ready_experiment(client)
        assert experiment["variants"][0]["provenance"] == {
            "agent_version": "1.0",
            "model": None,
            "prompt_version": None,
            "workflow_version": None,
            "git_commit_sha": None,
            "deployment_id": None,
            "metadata": {"lane": "control"},
        }
        first = client.post(f"/api/v1/experiments/{experiment['id']}/runs")
        assert first.status_code == 201, first.text
        first_run = first.json()
        submitted = client.post(f"/api/v1/experiment-runs/{first_run['id']}/execute")
        assert submitted.status_code == 202, submitted.text

    first_run_id = UUID(str(first_run["id"]))
    assert asyncio.run(process_experiment_message(first_run_id)) is ProcessOutcome.COMPLETED

    with TestClient(create_app()) as client:
        completed = client.get(f"/api/v1/experiment-runs/{first_run_id}")
        raw = client.get(
            f"/api/v1/experiment-runs/{first_run_id}/results", params={"page_size": 8}
        )
        analysis = client.post(f"/api/v1/experiment-runs/{first_run_id}/analysis")
        changes = client.get(
            f"/api/v1/experiment-runs/{first_run_id}/analysis/changes",
            params={"condition_position": 1},
        )
        second = client.post(f"/api/v1/experiments/{experiment['id']}/runs")
        history = client.get(f"/api/v1/experiments/{experiment['id']}/runs")
        preserved = client.get(f"/api/v1/experiment-runs/{first_run_id}/analysis")

    assert completed.json()["status"] == "completed"
    assert completed.json()["completed_decision_count"] == 8
    assert len(raw.json()["items"]) == 8
    assert analysis.status_code == 201
    assert preserved.status_code == 200
    conditions = analysis.json()["conditions"]
    assert conditions[0]["result"]["pass_rate_difference"] == -0.5
    assert conditions[1]["result"]["pass_rate_difference"] == 1.0
    assert [item["subject_position"] for item in changes.json()["items"]] == [0, 1]
    assert [item["a_trace_id"] for item in changes.json()["items"]] == [
        trace_ids[0],
        trace_ids[2],
    ]
    assert [item["b_trace_id"] for item in changes.json()["items"]] == [
        trace_ids[1],
        trace_ids[3],
    ]
    assert second.status_code == 201
    assert [item["id"] for item in history.json()["items"]] == [
        second.json()["id"],
        str(first_run_id),
    ]
    assert preserved.json() == analysis.json()


@pytest.mark.usefixtures("clean_database")
def test_database_rejects_non_finite_condition_metrics() -> None:
    with TestClient(create_app()) as client:
        run = _completed_run(client, [[("passed", "passed")]])
        assert client.post(f"/api/v1/experiment-runs/{run['id']}/analysis").status_code == 201

    async def corrupt() -> None:
        async with SessionLocal() as session:
            record = await session.scalar(select(ExperimentConditionAnalysisRecord))
            assert record is not None
            record.p_value = float("inf")
            with pytest.raises(IntegrityError):
                await session.commit()

    asyncio.run(corrupt())
