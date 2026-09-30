from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.judging import JudgeConfigurationRecord, JudgeRunRecord
from agentscope_api.schemas.judging import (
    JudgeConfigurationCreate,
    JudgeDecision,
    JudgeErrorCategory,
)
from agentscope_api.services.judge_orchestration import (
    ClaimOutcome,
    ProcessOutcome,
    claim_judge_run,
    heartbeat_judge_run,
    judge_retry_delay_ms,
    process_judge_message,
    recover_judge_runs,
    submit_judge_run,
)
from agentscope_api.services.openai_judge import (
    JudgeProviderError,
    JudgeSnapshot,
    OpenAIJudgeProvider,
    ProviderJudgment,
)


def test_judge_configuration_is_bounded_and_openai_only() -> None:
    config = JudgeConfigurationCreate(
        name="strict correctness",
        provider="openai",
        model="gpt-4.1-mini",
        rubric="Pass only when the candidate answers the question correctly.",
    )
    assert config.output_schema_version == "1"
    assert config.timeout_seconds == 30
    with pytest.raises(ValidationError):
        JudgeConfigurationCreate(
            name="bad",
            provider="other",
            model="model",
            rubric="rubric",  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("model", "contains whitespace"),
        ("rubric", ""),
        ("rubric", "x" * 8_001),
        ("timeout_seconds", 4),
        ("timeout_seconds", 301),
    ],
)
def test_judge_configuration_rejects_invalid_bounds(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "name": "judge",
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "rubric": "rubric",
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        JudgeConfigurationCreate.model_validate(payload)


class _Responses:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def parse(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return SimpleNamespace(
            id="resp_safe",
            status="completed",
            output_parsed={"decision": "passed", "rationale": "Meets the rubric."},
            usage=SimpleNamespace(input_tokens=11, output_tokens=4, total_tokens=15),
        )


class _Client:
    def __init__(self) -> None:
        self.responses = _Responses()

    def with_options(self, **_: object) -> _Client:
        return self


@pytest.mark.asyncio
async def test_openai_request_contains_only_rubric_and_candidate() -> None:
    client = _Client()
    provider = OpenAIJudgeProvider(client)  # type: ignore[arg-type]
    result = await provider.judge(
        JudgeSnapshot(
            model="gpt-4.1-mini",
            rubric="RUBRIC_ONLY_72",
            timeout_seconds=20,
            max_output_tokens=200,
        ),
        {"answer": "CANDIDATE_ONLY_91"},
    )

    request = repr(client.responses.kwargs)
    assert "RUBRIC_ONLY_72" in request
    assert "CANDIDATE_ONLY_91" in request
    assert "HUMAN_LABEL_SENTINEL" not in request
    assert client.responses.kwargs["store"] is False
    assert client.responses.kwargs["tools"] == []
    assert result.decision is JudgeDecision.PASSED
    assert result.total_tokens == 15


@pytest.mark.asyncio
async def test_openai_refusal_is_an_execution_error_not_a_failed_decision() -> None:
    client = _Client()

    async def refuse(**_: object) -> object:
        return SimpleNamespace(id="resp_refusal", status="completed", output_parsed=None)

    client.responses.parse = refuse  # type: ignore[method-assign]
    with pytest.raises(JudgeProviderError) as caught:
        await OpenAIJudgeProvider(client).judge(  # type: ignore[arg-type]
            JudgeSnapshot("gpt-4.1-mini", "rubric", 20, 200), {"answer": "candidate"}
        )
    assert caught.value.category is JudgeErrorCategory.INVALID_PROVIDER_RESPONSE
    assert caught.value.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parsed",
    [
        {"decision": "maybe", "rationale": "uncertain"},
        {"decision": "failed", "rationale": "x" * 4_001},
    ],
)
async def test_openai_rejects_invalid_structured_judgments(parsed: object) -> None:
    client = _Client()

    async def invalid(**_: object) -> object:
        return SimpleNamespace(id="resp_invalid", status="completed", output_parsed=parsed)

    client.responses.parse = invalid  # type: ignore[method-assign]
    with pytest.raises(JudgeProviderError) as caught:
        await OpenAIJudgeProvider(client).judge(  # type: ignore[arg-type]
            JudgeSnapshot("gpt-4.1-mini", "rubric", 20, 200), {"answer": "candidate"}
        )
    assert caught.value.category is JudgeErrorCategory.INVALID_PROVIDER_RESPONSE


def _run(client: TestClient, outputs: list[object]) -> dict[str, object]:
    traces = []
    for index, output in enumerate(outputs):
        trace = sdk_trace_payload(f"judge trace {index}")
        trace["output"] = output
        traces.append(trace)
    assert client.post("/api/v1/traces", json=envelope(*traces)).status_code == 202
    trace_ids = [str(item["trace_id"]) for item in traces]
    reference = client.post("/api/v1/human-reference-sets", json={"name": "Judge set"}).json()
    set_id = reference["id"]
    assert client.post(
        f"/api/v1/human-reference-sets/{set_id}/subjects", json={"trace_ids": trace_ids}
    ).status_code == 200
    assert client.post(f"/api/v1/human-reference-sets/{set_id}/begin-labeling").status_code == 200
    for trace_id in trace_ids:
        assert client.post(
            f"/api/v1/human-reference-sets/{set_id}/subjects/{trace_id}/reference-label",
            json={
                "label": "failed",
                "annotator_id": "HUMAN_LABEL_SENTINEL",
                "rationale": "HUMAN_RATIONALE_SENTINEL",
            },
        ).status_code == 204
    assert client.post(f"/api/v1/human-reference-sets/{set_id}/freeze").status_code == 200
    study = client.post(
        "/api/v1/calibration-studies",
        json={"name": "Study", "reference_set_id": set_id},
    ).json()
    configuration = client.post(
        "/api/v1/judge-configurations",
        json={
            "name": "Judge",
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "rubric": "RUBRIC_SENTINEL",
        },
    ).json()
    response = client.post(
        "/api/v1/calibration-judge-runs",
        json={"study_id": study["id"], "configuration_id": configuration["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


class _FakeProvider:
    def __init__(self, *, fail_call: int | None = None) -> None:
        self.candidates: list[object] = []
        self.fail_call = fail_call

    async def judge(self, _: JudgeSnapshot, candidate: object) -> ProviderJudgment:
        self.candidates.append(candidate)
        if len(self.candidates) == self.fail_call:
            raise JudgeProviderError(JudgeErrorCategory.PROVIDER_TIMEOUT, retryable=True)
        return ProviderJudgment(
            decision=JudgeDecision.PASSED,
            rationale="Meets the rubric.",
            provider_request_id=f"resp_{len(self.candidates)}",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            latency_ms=4.0,
        )


@pytest.mark.usefixtures("clean_database")
def test_run_snapshot_is_unchanged_when_source_configuration_changes() -> None:
    with TestClient(create_app()) as client:
        run = _run(client, ["candidate"])
        assert client.patch(
            f"/api/v1/judge-configurations/{run['configuration_id']}", json={"rubric": "changed"}
        ).status_code == 405

    async def mutate_source() -> None:
        async with SessionLocal() as session:
            record = await session.get(
                JudgeConfigurationRecord, UUID(str(run["configuration_id"]))
            )
            assert record is not None
            record.rubric = "changed outside the immutable API"
            await session.commit()

    asyncio.run(mutate_source())
    with TestClient(create_app()) as client:
        stored = client.get(f"/api/v1/calibration-judge-runs/{run['id']}").json()
    assert stored["rubric"] == "RUBRIC_SENTINEL"


@pytest.mark.usefixtures("clean_database")
def test_remote_run_resumes_partial_progress_without_human_data_or_duplicates() -> None:
    with TestClient(create_app()) as client:
        run = _run(client, [{"answer": "one"}, {"answer": "two"}])

    async def exercise() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            await submit_judge_run(session, run_id)
        first = _FakeProvider(fail_call=2)
        assert await process_judge_message(run_id, first) is ProcessOutcome.REQUEUED
        second = _FakeProvider()
        assert await process_judge_message(run_id, second) is ProcessOutcome.COMPLETED
        sent = repr(first.candidates + second.candidates)
        assert "HUMAN_LABEL_SENTINEL" not in sent
        assert "HUMAN_RATIONALE_SENTINEL" not in sent

    asyncio.run(exercise())
    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/calibration-judge-runs/{run['id']}").json()
        results = client.get(f"/api/v1/calibration-judge-runs/{run['id']}/results").json()
        progress = client.get(f"/api/v1/calibration-judge-runs/{run['id']}/progress").json()
    assert detail["status"] == "completed"
    assert detail["attempt_count"] == 2
    assert len(results["items"]) == 2
    assert len({item["trace_id"] for item in results["items"]}) == 2
    assert progress["passed_count"] == 2
    assert progress["pending_count"] == 0


@pytest.mark.usefixtures("clean_database")
def test_lease_heartbeat_fences_recovery_until_expiration() -> None:
    with TestClient(create_app()) as client:
        run = _run(client, ["candidate"])

    async def exercise() -> None:
        run_id = UUID(str(run["id"]))
        started = datetime(2026, 9, 18, tzinfo=UTC)
        async with SessionLocal() as session:
            await submit_judge_run(session, run_id)
        async with SessionLocal() as session:
            claim = await claim_judge_run(session, run_id, now=started)
        assert claim.outcome is ClaimOutcome.CLAIMED
        assert claim.token is not None
        async with SessionLocal() as session:
            assert not await heartbeat_judge_run(
                session, run_id, uuid4(), claim.attempt, now=started + timedelta(seconds=30)
            )
        async with SessionLocal() as session:
            assert await heartbeat_judge_run(
                session, run_id, claim.token, claim.attempt, now=started + timedelta(minutes=1)
            )
        async with SessionLocal() as session:
            assert await recover_judge_runs(session, now=started + timedelta(minutes=2)) == []
        async with SessionLocal() as session:
            assert await recover_judge_runs(session, now=started + timedelta(minutes=8)) == [run_id]

    asyncio.run(exercise())


@pytest.mark.usefixtures("clean_database")
def test_transient_failures_exhaust_once_without_infinite_retry() -> None:
    with TestClient(create_app()) as client:
        run = _run(client, ["candidate"])

    async def exercise() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            await submit_judge_run(session, run_id)
        provider = _FakeProvider(fail_call=1)
        assert await process_judge_message(run_id, provider) is ProcessOutcome.REQUEUED
        assert await judge_retry_delay_ms(run_id) == 5_000
        provider = _FakeProvider(fail_call=1)
        assert await process_judge_message(run_id, provider) is ProcessOutcome.REQUEUED
        assert await judge_retry_delay_ms(run_id) == 10_000
        provider = _FakeProvider(fail_call=1)
        assert await process_judge_message(run_id, provider) is ProcessOutcome.FAILED

    asyncio.run(exercise())
    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/calibration-judge-runs/{run['id']}").json()
    assert detail["attempt_count"] == 3
    assert detail["status"] == "failed"
    assert detail["error_category"] == "attempts_exhausted"


@pytest.mark.usefixtures("clean_database")
def test_only_one_concurrent_delivery_can_claim_a_run() -> None:
    with TestClient(create_app()) as client:
        run = _run(client, ["candidate"])

    async def exercise() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            await submit_judge_run(session, run_id)

        async def claim() -> ClaimOutcome:
            async with SessionLocal() as session:
                return (await claim_judge_run(session, run_id)).outcome

        assert sorted(await asyncio.gather(claim(), claim())) == [
            ClaimOutcome.CLAIMED,
            ClaimOutcome.IGNORED,
        ]

    asyncio.run(exercise())


@pytest.mark.usefixtures("clean_database")
def test_expired_old_attempt_cannot_persist_after_new_claim() -> None:
    with TestClient(create_app()) as client:
        run = _run(client, ["candidate"])

    async def exercise() -> None:
        run_id = UUID(str(run["id"]))
        async with SessionLocal() as session:
            await submit_judge_run(session, run_id)

        class RecoverDuringCall:
            async def judge(self, _: JudgeSnapshot, __: object) -> ProviderJudgment:
                now = datetime.now(UTC)
                async with SessionLocal() as session, session.begin():
                    record = await session.get(JudgeRunRecord, run_id)
                    assert record is not None
                    record.lease_expires_at = now - timedelta(seconds=1)
                async with SessionLocal() as session:
                    assert await recover_judge_runs(session, now=now) == [run_id]
                async with SessionLocal() as session:
                    new_claim = await claim_judge_run(session, run_id, now=now)
                assert new_claim.outcome is ClaimOutcome.CLAIMED
                assert new_claim.attempt == 2
                return ProviderJudgment(
                    decision=JudgeDecision.PASSED,
                    rationale="stale response",
                    provider_request_id="resp_stale",
                    input_tokens=1,
                    output_tokens=1,
                    total_tokens=2,
                    latency_ms=1,
                )

        assert await process_judge_message(run_id, RecoverDuringCall()) is ProcessOutcome.IGNORED

    asyncio.run(exercise())
    with TestClient(create_app()) as client:
        detail = client.get(f"/api/v1/calibration-judge-runs/{run['id']}").json()
        results = client.get(f"/api/v1/calibration-judge-runs/{run['id']}/results").json()
    assert detail["status"] == "running"
    assert detail["attempt_count"] == 2
    assert results["items"] == []
