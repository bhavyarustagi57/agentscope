from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.judging import (
    CalibrationAnalysisRecord,
    JudgeResultRecord,
    JudgeRunRecord,
)
from agentscope_api.services.calibration_analysis import (
    calculate_calibration_metrics,
    create_analysis,
)


def test_perfect_agreement_with_both_classes() -> None:
    metrics = calculate_calibration_metrics(
        [("passed", "passed"), ("passed", "passed"), ("failed", "failed")]
    )

    assert (metrics.true_positive, metrics.true_negative) == (2, 1)
    assert (metrics.false_positive, metrics.false_negative) == (0, 0)
    assert metrics.observed_agreement == 1.0
    assert metrics.precision_passed == 1.0
    assert metrics.recall_passed == 1.0
    assert metrics.f1_passed == 1.0
    assert metrics.specificity_failed == 1.0
    assert metrics.cohens_kappa == 1.0


def test_mixed_matrix_matches_independent_expected_values() -> None:
    metrics = calculate_calibration_metrics(
        [
            ("passed", "passed"),
            ("passed", "passed"),
            ("failed", "failed"),
            ("failed", "passed"),
            ("passed", "failed"),
        ]
    )

    assert metrics.sample_count == 5
    assert (metrics.human_passed_count, metrics.human_failed_count) == (3, 2)
    assert (metrics.judge_passed_count, metrics.judge_failed_count) == (3, 2)
    assert (metrics.agreement_count, metrics.disagreement_count) == (3, 2)
    assert (metrics.true_positive, metrics.true_negative) == (2, 1)
    assert (metrics.false_positive, metrics.false_negative) == (1, 1)
    assert metrics.observed_agreement == pytest.approx(3 / 5)
    assert metrics.precision_passed == pytest.approx(2 / 3)
    assert metrics.recall_passed == pytest.approx(2 / 3)
    assert metrics.f1_passed == pytest.approx(2 / 3)
    assert metrics.specificity_failed == pytest.approx(1 / 2)
    assert metrics.expected_agreement == pytest.approx(13 / 25)
    assert metrics.cohens_kappa == pytest.approx(1 / 6)


def test_complete_disagreement_is_negative_kappa() -> None:
    metrics = calculate_calibration_metrics(
        [("passed", "failed"), ("failed", "passed")]
    )

    assert metrics.observed_agreement == 0.0
    assert metrics.cohens_kappa == -1.0
    assert metrics.f1_passed is None


@pytest.mark.parametrize(
    ("pairs", "field"),
    [
        ([('passed', 'failed'), ('failed', 'failed')], "precision_passed"),
        ([('failed', 'passed'), ('failed', 'failed')], "recall_passed"),
        ([('passed', 'passed'), ('passed', 'failed')], "specificity_failed"),
    ],
)
def test_zero_denominator_metrics_are_null(
    pairs: list[tuple[str, str]], field: str
) -> None:
    assert getattr(calculate_calibration_metrics(pairs), field) is None


def test_constructed_chance_agreement_has_zero_kappa() -> None:
    metrics = calculate_calibration_metrics(
        [
            ("passed", "passed"),
            ("passed", "failed"),
            ("failed", "passed"),
            ("failed", "failed"),
        ]
    )
    assert metrics.observed_agreement == 0.5
    assert metrics.expected_agreement == 0.5
    assert metrics.cohens_kappa == 0.0


def test_negative_kappa_case() -> None:
    metrics = calculate_calibration_metrics(
        [
            ("passed", "failed"),
            ("failed", "passed"),
            ("failed", "passed"),
            ("failed", "failed"),
        ]
    )
    assert metrics.cohens_kappa == pytest.approx(-0.5)


def test_identical_single_category_has_undefined_kappa() -> None:
    metrics = calculate_calibration_metrics(
        [("passed", "passed"), ("passed", "passed")]
    )
    assert metrics.observed_agreement == 1.0
    assert metrics.expected_agreement == 1.0
    assert metrics.cohens_kappa is None
    assert metrics.kappa_is_defined is False


def test_one_subject_and_single_rater_categories_are_deliberate() -> None:
    one = calculate_calibration_metrics([("passed", "failed")])
    all_human_pass = calculate_calibration_metrics(
        [("passed", "passed"), ("passed", "failed")]
    )
    all_judge_fail = calculate_calibration_metrics(
        [("passed", "failed"), ("failed", "failed")]
    )

    assert one.cohens_kappa == 0.0
    assert all_human_pass.cohens_kappa == 0.0
    assert all_judge_fail.cohens_kappa == 0.0


def test_imbalanced_population_and_json_never_emit_non_finite_values() -> None:
    metrics = calculate_calibration_metrics(
        [("failed", "failed")] * 9 + [("passed", "failed")]
    )

    assert metrics.sample_count == 10
    assert metrics.observed_agreement == 0.9
    assert metrics.precision_passed is None
    encoded = metrics.model_dump_json()
    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert json.loads(encoded)["cohens_kappa"] == 0.0


def test_empty_population_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one paired label"):
        calculate_calibration_metrics([])


def _judge_run(client: TestClient, human_labels: list[str]) -> dict[str, object]:
    traces = [sdk_trace_payload(f"analysis trace {index}") for index in range(len(human_labels))]
    assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
    trace_ids = [str(trace["trace_id"]) for trace in traces]
    reference = client.post(
        "/api/v1/human-reference-sets", json={"name": "Analysis reference"}
    ).json()
    set_id = reference["id"]
    assert client.post(
        f"/api/v1/human-reference-sets/{set_id}/subjects", json={"trace_ids": trace_ids}
    ).status_code == 200
    assert client.post(f"/api/v1/human-reference-sets/{set_id}/begin-labeling").status_code == 200
    for trace_id, label in zip(trace_ids, human_labels, strict=True):
        assert client.post(
            f"/api/v1/human-reference-sets/{set_id}/subjects/{trace_id}/reference-label",
            json={
                "label": label,
                "annotator_id": "reference-reviewer",
                "rationale": "PRIVATE_HUMAN_RATIONALE",
            },
        ).status_code == 204
    assert client.post(f"/api/v1/human-reference-sets/{set_id}/freeze").status_code == 200
    study = client.post(
        "/api/v1/calibration-studies",
        json={"name": "Analysis study", "reference_set_id": set_id},
    ).json()
    configuration = client.post(
        "/api/v1/judge-configurations",
        json={
            "name": "Analysis judge",
            "model": "gpt-4.1-mini",
            "rubric": "Judge the candidate.",
        },
    ).json()
    response = client.post(
        "/api/v1/calibration-judge-runs",
        json={"study_id": study["id"], "configuration_id": configuration["id"]},
    )
    assert response.status_code == 201, response.text
    return {**response.json(), "trace_ids": trace_ids}


async def _finish_run(
    run: dict[str, object],
    decisions: list[str | None],
    *,
    status: str = "completed",
) -> None:
    async with SessionLocal() as session, session.begin():
        record = await session.get(JudgeRunRecord, UUID(str(run["id"])))
        assert record is not None
        record.status = status
        record.completed_at = datetime.now(UTC)
        record.error_category = "attempts_exhausted" if status == "failed" else None
        if status == "completed":
            for trace_id, decision in zip(run["trace_ids"], decisions, strict=False):
                session.add(
                    JudgeResultRecord(
                        run_id=record.id,
                        study_id=record.study_id,
                        trace_id=str(trace_id),
                        decision=decision,
                        rationale="Judged." if decision else None,
                        error_category=None if decision else "provider_timeout",
                        provider="openai",
                        model=record.model,
                        attempt_count=1,
                    )
                )


@pytest.mark.usefixtures("clean_database")
def test_analysis_api_persists_canonical_metrics_without_human_rationale() -> None:
    with TestClient(create_app()) as client:
        run = _judge_run(client, ["passed", "passed", "failed", "failed"])
    asyncio.run(_finish_run(run, ["passed", "failed", "passed", "failed"]))

    with TestClient(create_app()) as client:
        created = client.post(f"/api/v1/calibration-judge-runs/{run['id']}/analysis")
        repeated = client.post(f"/api/v1/calibration-judge-runs/{run['id']}/analysis")
        fetched = client.get(f"/api/v1/calibration-judge-runs/{run['id']}/analysis")

    assert created.status_code == repeated.status_code == 201
    assert fetched.status_code == 200
    assert created.json() == repeated.json() == fetched.json()
    assert created.json()["true_positive"] == 1
    assert created.json()["true_negative"] == 1
    assert created.json()["false_positive"] == 1
    assert created.json()["false_negative"] == 1
    assert "PRIVATE_HUMAN_RATIONALE" not in created.text


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize("terminal_status", ["pending", "failed"])
def test_analysis_rejects_runs_that_did_not_complete_successfully(terminal_status: str) -> None:
    with TestClient(create_app()) as client:
        run = _judge_run(client, ["passed"])
    if terminal_status == "failed":
        asyncio.run(_finish_run(run, [], status="failed"))

    with TestClient(create_app()) as client:
        response = client.post(f"/api/v1/calibration-judge-runs/{run['id']}/analysis")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "JUDGE_RUN_NOT_COMPLETED"


@pytest.mark.usefixtures("clean_database")
@pytest.mark.parametrize("decisions", [["passed"], ["passed", None]])
def test_analysis_rejects_missing_or_error_results(decisions: list[str | None]) -> None:
    with TestClient(create_app()) as client:
        run = _judge_run(client, ["passed", "failed"])
    asyncio.run(_finish_run(run, decisions))

    with TestClient(create_app()) as client:
        response = client.post(f"/api/v1/calibration-judge-runs/{run['id']}/analysis")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INCOMPLETE_COMPARISON_POPULATION"


@pytest.mark.usefixtures("clean_database")
def test_concurrent_analysis_creation_returns_one_canonical_row() -> None:
    with TestClient(create_app()) as client:
        run = _judge_run(client, ["passed", "failed"])
    asyncio.run(_finish_run(run, ["passed", "failed"]))

    async def create() -> object:
        async with SessionLocal() as session:
            return await create_analysis(session, UUID(str(run["id"])))

    async def exercise() -> tuple[object, object]:
        first, second = await asyncio.gather(create(), create())
        return first, second

    first, second = asyncio.run(exercise())
    assert first == second

    async def count() -> int:
        async with SessionLocal() as session:
            return len(
                list(
                    await session.scalars(
                        CalibrationAnalysisRecord.__table__.select().where(
                            CalibrationAnalysisRecord.run_id == UUID(str(run["id"]))
                        )
                    )
                )
            )

    assert asyncio.run(count()) == 1


@pytest.mark.usefixtures("clean_database")
def test_disagreements_are_classified_ordered_filtered_and_paginated() -> None:
    with TestClient(create_app()) as client:
        run = _judge_run(client, ["failed", "passed", "failed", "passed"])
    asyncio.run(_finish_run(run, ["passed", "failed", "passed", "passed"]))

    with TestClient(create_app()) as client:
        created = client.post(f"/api/v1/calibration-judge-runs/{run['id']}/analysis")
        assert created.status_code == 201
        first_page = client.get(
            f"/api/v1/calibration-judge-runs/{run['id']}/disagreements",
            params={"page_size": 1},
        )
        false_positives = client.get(
            f"/api/v1/calibration-judge-runs/{run['id']}/disagreements",
            params={"category": "false_positive"},
        )
        invalid = client.get(
            f"/api/v1/calibration-judge-runs/{run['id']}/disagreements",
            params={"category": "wrong"},
        )

    assert first_page.status_code == false_positives.status_code == 200
    assert first_page.json()["has_more"] is True
    assert first_page.json()["items"][0] == {
        "trace_id": run["trace_ids"][0],
        "category": "false_positive",
        "human_label": "failed",
        "judge_decision": "passed",
    }
    assert [item["trace_id"] for item in false_positives.json()["items"]] == [
        run["trace_ids"][0],
        run["trace_ids"][2],
    ]
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_DISAGREEMENT_FILTER"


@pytest.mark.usefixtures("clean_database")
def test_analysis_get_requires_existing_analysis_and_unknown_run_is_stable() -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    with TestClient(create_app()) as client:
        run = _judge_run(client, ["passed"])
        absent = client.get(f"/api/v1/calibration-judge-runs/{run['id']}/analysis")
        unknown = client.post(f"/api/v1/calibration-judge-runs/{missing}/analysis")

    assert absent.status_code == 404
    assert absent.json()["error"]["code"] == "CALIBRATION_ANALYSIS_NOT_FOUND"
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "JUDGE_RUN_NOT_FOUND"


@pytest.mark.usefixtures("clean_database")
def test_database_rejects_impossible_analysis_counts() -> None:
    with TestClient(create_app()) as client:
        run = _judge_run(client, ["passed"])
    asyncio.run(_finish_run(run, ["passed"]))

    async def insert_invalid() -> None:
        async with SessionLocal() as session:
            session.add(
                CalibrationAnalysisRecord(
                    run_id=UUID(str(run["id"])),
                    study_id=UUID(str(run["study_id"])),
                    sample_count=1,
                    human_passed_count=1,
                    human_failed_count=0,
                    judge_passed_count=1,
                    judge_failed_count=0,
                    agreement_count=0,
                    disagreement_count=0,
                    true_positive=1,
                    true_negative=0,
                    false_positive=0,
                    false_negative=0,
                    observed_agreement=1,
                    expected_agreement=1,
                    precision_passed=1,
                    recall_passed=1,
                    f1_passed=1,
                    specificity_failed=None,
                    cohens_kappa=None,
                    metric_schema_version="1",
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    asyncio.run(insert_invalid())
