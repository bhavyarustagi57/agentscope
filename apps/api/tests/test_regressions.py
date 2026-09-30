from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_experiment_analysis import _completed_run

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.experiment import (
    ExperimentEvaluationConditionRecord,
    ExperimentRunRecord,
    ExperimentRunResultRecord,
    ExperimentVariantRecord,
)
from agentscope_api.models.regression import RegressionPolicyRecord

pytestmark = pytest.mark.usefixtures("clean_database")


def _policy(
    client: TestClient, *, drop: float = 0.25, minimum_sample_size: int = 1
) -> dict[str, object]:
    response = client.post(
        "/api/v1/regression-policies",
        json={
            "name": "Release guard",
            "minimum_pass_rate_drop": drop,
            "minimum_sample_size": minimum_sample_size,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _analyzed_run(
    client: TestClient, pairs_by_condition: list[list[tuple[str, str]]]
) -> tuple[dict[str, object], dict[str, object]]:
    run = _completed_run(client, pairs_by_condition)
    analysis = client.post(f"/api/v1/experiment-runs/{run['id']}/analysis")
    assert analysis.status_code == 201, analysis.text
    return run, analysis.json()


def _create_check(
    client: TestClient,
    run_id: object,
    policy_id: object,
    baseline: str = "A",
    candidate: str = "B",
):
    return client.post(
        "/api/v1/regression-checks",
        json={
            "experiment_run_id": run_id,
            "regression_policy_id": policy_id,
            "baseline_variant": baseline,
            "candidate_variant": candidate,
        },
    )


def test_policy_create_list_and_get_preserve_fraction_threshold() -> None:
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/v1/regression-policies",
            json={
                "name": "Release guard",
                "description": "Five percentage-point practical drop.",
                "minimum_pass_rate_drop": 0.05,
                "minimum_sample_size": 20,
            },
        )
        listing = client.get("/api/v1/regression-policies", params={"page_size": 1})
        fetched = client.get(f"/api/v1/regression-policies/{created.json()['id']}")

    assert created.status_code == 201, created.text
    assert listing.status_code == fetched.status_code == 200
    assert created.json()["minimum_pass_rate_drop"] == 0.05
    assert created.json()["minimum_sample_size"] == 20
    assert listing.json() == {"items": [created.json()], "has_more": False}
    assert fetched.json() == created.json()


@pytest.mark.parametrize("threshold", [0, -0.01, 1.01])
def test_policy_rejects_out_of_range_practical_drop(threshold: float) -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/regression-policies",
            json={
                "name": "Invalid threshold",
                "minimum_pass_rate_drop": threshold,
                "minimum_sample_size": 1,
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("minimum_sample_size", [0, 1_001])
def test_policy_rejects_out_of_range_minimum_sample(minimum_sample_size: int) -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            "/api/v1/regression-policies",
            json={
                "name": "Invalid sample",
                "minimum_pass_rate_drop": 0.05,
                "minimum_sample_size": minimum_sample_size,
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_policy_database_constraints_reject_invalid_direct_write() -> None:
    async def write_invalid() -> None:
        async with SessionLocal() as session:
            with pytest.raises(IntegrityError):
                async with session.begin():
                    session.add(
                        RegressionPolicyRecord(
                            name="Invalid direct write",
                            minimum_pass_rate_drop=0,
                            minimum_sample_size=0,
                        )
                    )

    asyncio.run(write_invalid())


def test_exact_practical_drop_is_regression_for_a_baseline_b_candidate() -> None:
    pairs = [("passed", "passed")] * 3 + [("passed", "failed")]
    with TestClient(create_app()) as client:
        policy = _policy(client)
        run, analysis = _analyzed_run(client, [pairs])
        created = _create_check(client, run["id"], policy["id"])
        listing = client.get("/api/v1/regression-checks", params={"page_size": 1})
        fetched = client.get(f"/api/v1/regression-checks/{created.json()['id']}")

    assert created.status_code == 201, created.text
    assert created.json() == fetched.json()
    assert listing.json()["items"][0]["id"] == created.json()["id"]
    assert created.json()["classification"] == "regression_detected"
    assert created.json()["analysis_id"] == analysis["id"]
    finding = created.json()["findings"][0]
    assert finding["classification"] == "regression_detected"
    assert finding["sample_size"] == 4
    assert finding["baseline_passed_count"] == 4
    assert finding["candidate_passed_count"] == 3
    assert finding["baseline_pass_rate"] == 1.0
    assert finding["candidate_pass_rate"] == 0.75
    assert finding["candidate_minus_baseline"] == -0.25
    assert finding["minimum_pass_rate_drop"] == 0.25
    assert finding["minimum_sample_size"] == 1


@pytest.mark.parametrize(
    ("pairs", "expected_effect"),
    [
        ([("passed", "passed")] * 4 + [("passed", "failed")], -0.2),
        ([("passed", "passed")] * 3 + [("failed", "passed")], 0.25),
        ([("passed", "passed"), ("failed", "failed")], 0.0),
    ],
)
def test_nonregressing_effects_are_not_regressions(
    pairs: list[tuple[str, str]], expected_effect: float
) -> None:
    with TestClient(create_app()) as client:
        policy = _policy(client)
        run, _ = _analyzed_run(client, [pairs])
        response = _create_check(client, run["id"], policy["id"])

    assert response.status_code == 201, response.text
    assert response.json()["classification"] == "no_regression_detected"
    assert response.json()["findings"][0]["candidate_minus_baseline"] == expected_effect


def test_reversed_orientation_transforms_canonical_b_minus_a() -> None:
    pairs = [("passed", "passed")] * 3 + [("passed", "failed")]
    with TestClient(create_app()) as client:
        policy = _policy(client)
        run, _ = _analyzed_run(client, [pairs])
        response = _create_check(client, run["id"], policy["id"], "B", "A")

    assert response.status_code == 201, response.text
    finding = response.json()["findings"][0]
    assert response.json()["classification"] == "no_regression_detected"
    assert finding["baseline_variant"] == "B"
    assert finding["candidate_variant"] == "A"
    assert finding["candidate_minus_baseline"] == 0.25
    assert finding["confidence_interval_lower"] == -1 * finding["source_interval_upper"]
    assert finding["confidence_interval_upper"] == -1 * finding["source_interval_lower"]


def test_overall_classification_is_conservative_for_insufficient_conditions() -> None:
    regression = [("passed", "passed")] * 3 + [("passed", "failed")]
    eligible = [("passed", "passed"), ("failed", "failed")]
    ineligible = [("passed", "error"), ("failed", "failed")]
    with TestClient(create_app()) as client:
        policy = _policy(client, minimum_sample_size=3)
        partial_run, _ = _analyzed_run(client, [eligible, ineligible])
        partial = _create_check(client, partial_run["id"], policy["id"])
        regressed_run, _ = _analyzed_run(client, [regression, ineligible * 2])
        regressed = _create_check(client, regressed_run["id"], policy["id"])

    assert partial.json()["classification"] == "insufficient_evidence"
    assert [item["classification"] for item in partial.json()["findings"]] == [
        "insufficient_evidence",
        "insufficient_evidence",
    ]
    assert regressed.json()["classification"] == "regression_detected"


def test_check_rejects_invalid_orientation_and_source_state() -> None:
    with TestClient(create_app()) as client:
        policy = _policy(client)
        run = _completed_run(client, [[("passed", "passed")]])
        missing_analysis = _create_check(client, run["id"], policy["id"])
        same_variant = _create_check(client, run["id"], policy["id"], "A", "A")
        unknown = _create_check(
            client,
            "00000000-0000-0000-0000-000000000000",
            policy["id"],
        )

    assert missing_analysis.status_code == 409
    assert missing_analysis.json()["error"]["code"] == "EXPERIMENT_ANALYSIS_REQUIRED"
    assert same_variant.status_code == 422
    assert unknown.status_code == 404


def test_incomplete_run_is_rejected() -> None:
    with TestClient(create_app()) as client:
        policy = _policy(client)
        run = _completed_run(client, [[("passed", "passed")]])

        async def mark_incomplete() -> None:
            async with SessionLocal() as session, session.begin():
                record = await session.get(ExperimentRunRecord, UUID(str(run["id"])))
                assert record is not None
                record.status = "pending"
                record.completed_at = None

        asyncio.run(mark_incomplete())
        response = _create_check(client, run["id"], policy["id"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EXPERIMENT_RUN_NOT_COMPLETED"


def test_missing_result_after_analysis_is_rejected_without_partial_check() -> None:
    with TestClient(create_app()) as client:
        policy = _policy(client)
        run, _ = _analyzed_run(client, [[("passed", "passed")]])

        async def remove_result() -> None:
            async with SessionLocal() as session, session.begin():
                result = await session.scalar(
                    select(ExperimentRunResultRecord).where(
                        ExperimentRunResultRecord.run_id == UUID(str(run["id"]))
                    )
                )
                assert result is not None
                await session.delete(result)

        asyncio.run(remove_result())
        response = _create_check(client, run["id"], policy["id"])
        checks = client.get("/api/v1/regression-checks")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INCOMPLETE_REGRESSION_EVIDENCE"
    assert checks.json()["items"] == []


def test_policy_provenance_and_condition_snapshots_are_immutable() -> None:
    with TestClient(create_app()) as client:
        policy = _policy(client, drop=0.1)
        run, _ = _analyzed_run(client, [[("passed", "failed")]])

        async def set_source_values(*, changed: bool) -> None:
            async with SessionLocal() as session, session.begin():
                run_record = await session.get(ExperimentRunRecord, UUID(str(run["id"])))
                policy_record = await session.get(RegressionPolicyRecord, UUID(str(policy["id"])))
                assert run_record is not None and policy_record is not None
                variants = list(
                    await session.scalars(
                        select(ExperimentVariantRecord).where(
                            ExperimentVariantRecord.experiment_id == run_record.experiment_id
                        )
                    )
                )
                condition = await session.scalar(
                    select(ExperimentEvaluationConditionRecord).where(
                        ExperimentEvaluationConditionRecord.experiment_id
                        == run_record.experiment_id
                    )
                )
                assert condition is not None
                for variant in variants:
                    variant.provenance = {
                        "git_sha": "changed" if changed else f"sha-{variant.variant_key}",
                        "version": "2" if changed else "1",
                    }
                condition.evaluator_config = {"expected": "changed" if changed else "candidate"}
                if changed:
                    policy_record.name = "Changed policy"
                    policy_record.minimum_pass_rate_drop = 0.9

        asyncio.run(set_source_values(changed=False))
        created = _create_check(client, run["id"], policy["id"])
        asyncio.run(set_source_values(changed=True))
        fetched = client.get(f"/api/v1/regression-checks/{created.json()['id']}")

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["policy_name"] == "Release guard"
    assert body["minimum_pass_rate_drop"] == 0.1
    assert body["baseline_provenance"]["git_sha"] == "sha-A"
    assert body["candidate_provenance"]["git_sha"] == "sha-B"
    assert body["findings"][0]["evaluator_config"] == {"expected": "candidate"}
    assert "cause" not in str(body).lower()


def test_ineligible_findings_preserve_null_statistics_and_complete_transaction() -> None:
    pairs = [
        [("passed", "passed"), ("failed", "failed")],
        [("passed", "error"), ("failed", "failed")],
    ]
    with TestClient(create_app()) as client:
        policy = _policy(client)
        run, _ = _analyzed_run(client, pairs)
        response = _create_check(client, run["id"], policy["id"])

    assert response.status_code == 201
    findings = response.json()["findings"]
    assert len(findings) == 2
    ineligible = findings[1]
    assert ineligible["classification"] == "insufficient_evidence"
    for field in (
        "sample_size",
        "baseline_passed_count",
        "candidate_passed_count",
        "candidate_minus_baseline",
        "matched_pairs_odds_ratio",
        "p_value",
        "confidence_interval_lower",
        "confidence_interval_upper",
    ):
        assert ineligible[field] is None


def test_duplicate_check_is_rejected_and_list_can_filter_by_run() -> None:
    with TestClient(create_app()) as client:
        policy = _policy(client)
        first_run, _ = _analyzed_run(client, [[("passed", "passed")]])
        second_run, _ = _analyzed_run(client, [[("passed", "passed")]])
        first = _create_check(client, first_run["id"], policy["id"])
        _create_check(client, second_run["id"], policy["id"])
        duplicate = _create_check(client, first_run["id"], policy["id"])
        filtered = client.get(
            "/api/v1/regression-checks",
            params={"experiment_run_id": first_run["id"], "page_size": 1},
        )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "REGRESSION_CHECK_EXISTS"
    assert [item["id"] for item in filtered.json()["items"]] == [first.json()["id"]]
    assert filtered.json()["has_more"] is False
