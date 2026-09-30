from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.schemas.judging import JudgeDecision
from agentscope_api.services.judge_orchestration import (
    ProcessOutcome,
    process_judge_message,
    submit_judge_run,
)
from agentscope_api.services.openai_judge import (
    JudgeSnapshot,
    ProviderJudgment,
)

pytestmark = pytest.mark.usefixtures("clean_database")


class _DeterministicJudge:
    def __init__(self) -> None:
        self.candidates: list[object] = []

    async def judge(self, _: JudgeSnapshot, candidate: object) -> ProviderJudgment:
        self.candidates.append(candidate)
        assert isinstance(candidate, dict)
        decision = JudgeDecision(candidate["judge_decision"])
        return ProviderJudgment(
            decision=decision,
            rationale=f"Deterministic {decision.value} decision.",
            provider_request_id=f"fake_{len(self.candidates)}",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            latency_ms=1.0,
        )


def test_complete_phase5_workflow_uses_fake_judge_and_preserves_trace_identity() -> None:
    human_labels = ["passed", "failed", "passed", "failed"]
    judge_decisions = ["passed", "passed", "failed", "failed"]
    traces = []
    for index, decision in enumerate(judge_decisions):
        trace = sdk_trace_payload(f"phase5 integration {index}")
        trace["output"] = {"answer": f"candidate {index}", "judge_decision": decision}
        traces.append(trace)
    trace_ids = [str(trace["trace_id"]) for trace in traces]

    with TestClient(create_app()) as client:
        assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202

        reference = client.post(
            "/api/v1/human-reference-sets",
            json={"name": "Phase 5 integration reference"},
        ).json()
        set_id = reference["id"]
        defined = client.post(
            f"/api/v1/human-reference-sets/{set_id}/subjects",
            json={"trace_ids": trace_ids},
        )
        assert defined.status_code == 200, defined.text
        assert client.post(
            f"/api/v1/human-reference-sets/{set_id}/begin-labeling"
        ).status_code == 200

        for trace_id, human_label in zip(trace_ids, human_labels, strict=True):
            raw_label = "failed" if human_label == "passed" else "passed"
            annotation = client.post(
                f"/api/v1/human-reference-sets/{set_id}/annotations",
                json={
                    "trace_id": trace_id,
                    "annotator_id": "raw-reviewer",
                    "label": raw_label,
                    "rationale": "RAW_HUMAN_RATIONALE_SENTINEL",
                    "source": "manual",
                },
            )
            assert annotation.status_code == 200, annotation.text
            authoritative = client.post(
                f"/api/v1/human-reference-sets/{set_id}/subjects/"
                f"{trace_id}/reference-label",
                json={
                    "label": human_label,
                    "annotator_id": "lead-adjudicator",
                    "rationale": "AUTHORITATIVE_RATIONALE_SENTINEL",
                },
            )
            assert authoritative.status_code == 204, authoritative.text

        frozen = client.post(f"/api/v1/human-reference-sets/{set_id}/freeze")
        assert frozen.status_code == 200, frozen.text
        assert frozen.json()["status"] == "frozen"
        assert frozen.json()["annotation_count"] == 4
        assert frozen.json()["reference_count"] == 4

        study = client.post(
            "/api/v1/calibration-studies",
            json={"name": "Phase 5 integration study", "reference_set_id": set_id},
        ).json()
        snapshotted = client.get(
            f"/api/v1/calibration-studies/{study['id']}/subjects?page_size=100"
        ).json()["items"]
        assert [item["trace_id"] for item in snapshotted] == trace_ids
        assert [item["reference_label"] for item in snapshotted] == human_labels

        configuration = client.post(
            "/api/v1/judge-configurations",
            json={
                "name": "Deterministic integration judge",
                "provider": "openai",
                "model": "fake-model-v1",
                "rubric": "Use only the candidate payload.",
            },
        ).json()
        run = client.post(
            "/api/v1/calibration-judge-runs",
            json={
                "study_id": study["id"],
                "configuration_id": configuration["id"],
            },
        ).json()

    provider = _DeterministicJudge()

    async def execute() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            await submit_judge_run(session, run_id)
        assert await process_judge_message(run_id, provider) is ProcessOutcome.COMPLETED

    asyncio.run(execute())
    sent = repr(provider.candidates)
    assert "RAW_HUMAN_RATIONALE_SENTINEL" not in sent
    assert "AUTHORITATIVE_RATIONALE_SENTINEL" not in sent
    assert provider.candidates == [trace["output"] for trace in traces]

    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/calibration-judge-runs/{run['id']}").json()
        progress = client.get(
            f"/api/v1/calibration-judge-runs/{run['id']}/progress"
        ).json()
        results = client.get(
            f"/api/v1/calibration-judge-runs/{run['id']}/results?page_size=100"
        ).json()["items"]
        analysis_response = client.post(
            f"/api/v1/calibration-judge-runs/{run['id']}/analysis"
        )

        assert detail["status"] == "completed"
        assert progress == {
            "run_id": run["id"],
            "status": "completed",
            "subject_count": 4,
            "result_count": 4,
            "passed_count": 2,
            "failed_count": 2,
            "error_count": 0,
            "pending_count": 0,
        }
        assert [item["trace_id"] for item in results] == trace_ids
        assert analysis_response.status_code == 201, analysis_response.text
        analysis = analysis_response.json()
        assert {
            key: analysis[key]
            for key in (
                "sample_count",
                "true_positive",
                "true_negative",
                "false_positive",
                "false_negative",
                "agreement_count",
                "disagreement_count",
            )
        } == {
            "sample_count": 4,
            "true_positive": 1,
            "true_negative": 1,
            "false_positive": 1,
            "false_negative": 1,
            "agreement_count": 2,
            "disagreement_count": 2,
        }
        assert analysis["observed_agreement"] == 0.5
        assert analysis["cohens_kappa"] == 0.0
        assert analysis["precision_passed"] == 0.5
        assert analysis["recall_passed"] == 0.5
        assert analysis["f1_passed"] == 0.5
        assert analysis["specificity_failed"] == 0.5

        disagreements = client.get(
            f"/api/v1/calibration-judge-runs/{run['id']}/disagreements?page_size=100"
        ).json()["items"]
        assert disagreements == [
            {
                "trace_id": trace_ids[1],
                "category": "false_positive",
                "human_label": "failed",
                "judge_decision": "passed",
            },
            {
                "trace_id": trace_ids[2],
                "category": "false_negative",
                "human_label": "passed",
                "judge_decision": "failed",
            },
        ]
        false_positives = client.get(
            f"/api/v1/calibration-judge-runs/{run['id']}/disagreements"
            "?category=false_positive&page_size=1"
        ).json()
        assert false_positives["items"] == disagreements[:1]
        assert false_positives["has_more"] is False

