"""Create the deterministic, local-only AgentScope sample workspace."""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from seed_demo_traces import make_trace
from sqlalchemy import select

from agentscope_api.database import SessionLocal
from agentscope_api.models.automatic_drift import AutomaticDriftCheckRecord
from agentscope_api.models.monitoring import MonitoringSnapshotRecord
from agentscope_api.schemas.evaluations import EvaluationRunExecution
from agentscope_api.schemas.judging import JudgeDecision
from agentscope_api.services import automatic_drift
from agentscope_api.services.evaluation_orchestration import (
    process_evaluation_message,
    submit_evaluation_run,
)
from agentscope_api.services.experiment_execution import (
    process_experiment_message,
    submit_experiment_run,
)
from agentscope_api.services.judge_orchestration import process_judge_message, submit_judge_run
from agentscope_api.services.openai_judge import JudgeSnapshot, ProviderJudgment

DEMO = "AgentScope Demo"
START = datetime(2026, 9, 20, 0, tzinfo=UTC)
TRACE_IDS = [f"agentscope-demo-workspace-v1-{index:02d}" for index in range(20)]


class Api:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, payload: object = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"{method} {path} failed ({error.code}): {detail}") from error
        except urllib.error.URLError as error:
            raise RuntimeError(f"AgentScope API is unavailable at {self.base_url}") from error


class _DemoJudge:
    decisions = ("passed", "passed", "failed", "failed")

    def __init__(self) -> None:
        self.calls = 0

    async def judge(self, _: JudgeSnapshot, candidate: object) -> ProviderJudgment:
        decision = JudgeDecision(self.decisions[self.calls])
        self.calls += 1
        return ProviderJudgment(
            decision=decision,
            rationale=f"Deterministic demo {decision.value} decision.",
            provider_request_id=f"agentscope_demo_{self.calls}",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            latency_ms=1.0,
        )


def _find(api: Any, path: str, name: str) -> dict[str, Any] | None:
    page = api.request("GET", f"{path}{'&' if '?' in path else '?'}page_size=100")
    return next((item for item in page["items"] if item.get("name") == name), None)


def _create_or_find(
    api: Any, list_path: str, create_path: str, name: str, payload: dict[str, Any], summary: dict
) -> dict[str, Any]:
    existing = _find(api, list_path, name)
    if existing:
        summary["reused"] += 1
        return existing
    summary["created"] += 1
    return api.request("POST", create_path, payload)


def _traces() -> list[dict[str, Any]]:
    outcomes = (
        "approved",
        "approved",
        "rejected",
        "approved",
        "approved",
        "rejected",
        "rejected",
        "rejected",
        "approved",
        "rejected",
    )
    traces: list[dict[str, Any]] = []
    for index, trace_id in enumerate(TRACE_IDS):
        trace = make_trace("workspace-v1", index, START + timedelta(hours=index // 4 + 1))
        trace["trace_id"] = trace_id
        trace["name"] = f"{DEMO} — {trace['name']}"
        trace["metadata"] = {
            "environment": "local-demo",
            "agentscope_demo": True,
            "fixture_version": 1,
        }
        trace["tags"] = ["agentscope-demo", "synthetic", trace["status"]]
        trace["output"] = outcomes[index] if index < len(outcomes) else "approved"
        for span in trace["spans"]:
            old_id = span["trace_id"]
            span["trace_id"] = trace_id
            span["span_id"] = span["span_id"].replace(old_id, trace_id, 1)
            if span["parent_span_id"]:
                span["parent_span_id"] = span["parent_span_id"].replace(old_id, trace_id, 1)
            span["metadata"] = {"agentscope_demo": True, "synthetic": True}
        traces.append(trace)
    return traces


def _seed_traces(api: Any, summary: dict[str, Any]) -> None:
    traces = _traces()
    accepted = duplicates = 0
    for offset in range(0, len(traces), 10):
        result = api.request(
            "POST",
            "/api/v1/traces",
            {"schema_version": "1", "traces": traces[offset : offset + 10]},
        )
        accepted += result["accepted"]
        duplicates += result["duplicates"]
    summary["created"] += accepted
    summary["reused"] += duplicates


def _seed_evaluation(api: Any, summary: dict[str, Any], run_async: Any) -> tuple[dict, dict]:
    definition = _create_or_find(
        api,
        "/api/v1/evaluation-definitions",
        "/api/v1/evaluation-definitions",
        f"{DEMO} — approved output",
        {
            "name": f"{DEMO} — approved output",
            "description": "Synthetic exact-match evaluator for the local demo.",
            "evaluator_kind": "exact_match",
            "evaluator_config": {"expected": "approved", "case_sensitive": True},
        },
        summary,
    )
    runs = api.request(
        "GET", f"/api/v1/evaluation-runs?definition_id={definition['id']}&page_size=100"
    )["items"]
    run = next((item for item in runs if item["status"] == "completed"), None)
    if run is None:
        run = api.request("POST", "/api/v1/evaluation-runs", {"definition_id": definition["id"]})

        async def execute() -> Any:
            run_id = UUID(run["id"])
            async with SessionLocal() as session:
                await submit_evaluation_run(
                    session, run_id, EvaluationRunExecution(trace_ids=TRACE_IDS)
                )
            return await process_evaluation_message(run_id)

        outcome = run_async(execute)
        if outcome.value != "completed":
            raise RuntimeError(f"demo evaluation did not complete: {outcome.value}")
        run = api.request("GET", f"/api/v1/evaluation-runs/{run['id']}")
        summary["created"] += 1
    else:
        summary["reused"] += 1
    return definition, run


def _seed_calibration(api: Any, summary: dict[str, Any], run_async: Any) -> tuple[dict, dict]:
    reference = _create_or_find(
        api,
        "/api/v1/human-reference-sets",
        "/api/v1/human-reference-sets",
        f"{DEMO} — human reference",
        {
            "name": f"{DEMO} — human reference",
            "description": "Synthetic labels for local onboarding.",
        },
        summary,
    )
    reference = api.request("GET", f"/api/v1/human-reference-sets/{reference['id']}")
    if reference["status"] == "draft":
        api.request(
            "POST",
            f"/api/v1/human-reference-sets/{reference['id']}/subjects",
            {"trace_ids": TRACE_IDS[:4]},
        )
        api.request("POST", f"/api/v1/human-reference-sets/{reference['id']}/begin-labeling")
        reference["status"] = "labeling"
    if reference["status"] == "labeling":
        labels = ("passed", "failed", "passed", "failed")
        for index, (trace_id, label) in enumerate(zip(TRACE_IDS[:4], labels, strict=True)):
            api.request(
                "POST",
                f"/api/v1/human-reference-sets/{reference['id']}/annotations",
                {
                    "trace_id": trace_id,
                    "annotator_id": f"demo-reviewer-{index % 2 + 1}",
                    "label": "failed" if label == "passed" else "passed",
                    "rationale": "Deliberately conflicting synthetic annotation.",
                    "source": "manual",
                },
            )
            api.request(
                "POST",
                f"/api/v1/human-reference-sets/{reference['id']}/subjects/{trace_id}/reference-label",
                {
                    "label": label,
                    "annotator_id": "demo-adjudicator",
                    "rationale": "Deterministic synthetic authoritative label.",
                },
            )
        api.request("POST", f"/api/v1/human-reference-sets/{reference['id']}/freeze")
    study = _create_or_find(
        api,
        "/api/v1/calibration-studies",
        "/api/v1/calibration-studies",
        f"{DEMO} — calibration study",
        {"name": f"{DEMO} — calibration study", "reference_set_id": reference["id"]},
        summary,
    )
    configuration = _create_or_find(
        api,
        "/api/v1/judge-configurations",
        "/api/v1/judge-configurations",
        f"{DEMO} — deterministic judge",
        {
            "name": f"{DEMO} — deterministic judge",
            "provider": "openai",
            "model": "local-deterministic-demo-v1",
            "rubric": "Synthetic deterministic decisions only; no provider call is made.",
        },
        summary,
    )
    runs = api.request("GET", "/api/v1/calibration-judge-runs?page_size=100")["items"]
    run = next(
        (
            item
            for item in runs
            if item["status"] == "completed"
            and item["study_id"] == study["id"]
            and item["configuration_id"] == configuration["id"]
        ),
        None,
    )
    if run is None:
        run = api.request(
            "POST",
            "/api/v1/calibration-judge-runs",
            {"study_id": study["id"], "configuration_id": configuration["id"]},
        )

        async def execute() -> Any:
            run_id = UUID(run["id"])
            async with SessionLocal() as session:
                await submit_judge_run(session, run_id)
            return await process_judge_message(run_id, _DemoJudge())

        outcome = run_async(execute)
        if outcome.value != "completed":
            raise RuntimeError(f"demo calibration did not complete: {outcome.value}")
        summary["created"] += 1
    else:
        summary["reused"] += 1
    analysis = api.request("POST", f"/api/v1/calibration-judge-runs/{run['id']}/analysis")
    return run, analysis


def _seed_experiment(
    api: Any,
    definition: dict,
    summary: dict[str, Any],
    run_async: Any,
    *,
    name: str = f"{DEMO} — paired agent comparison",
    baseline_sha: str = "0" * 40,
    candidate_sha: str = "f" * 40,
) -> tuple[dict, dict]:
    experiment = _find(api, "/api/v1/experiments", name)
    if experiment is None:
        experiment = api.request(
            "POST",
            "/api/v1/experiments",
            {
                "name": name,
                "description": "Synthetic paired evidence; no automatic winner is claimed.",
            },
        )
        pairs = [
            {"a_trace_id": TRACE_IDS[index], "b_trace_id": TRACE_IDS[index + 1]}
            for index in range(0, 10, 2)
        ]
        experiment = api.request(
            "POST",
            f"/api/v1/experiments/{experiment['id']}/configuration",
            {
                "name": experiment["name"],
                "description": experiment["description"],
                "variants": [
                    {
                        "key": "A",
                        "name": f"{DEMO} Control",
                        "provenance": {
                            "agent_version": "demo-1.0",
                            "git_commit_sha": baseline_sha,
                            "metadata": {"agentscope_demo": True},
                        },
                    },
                    {
                        "key": "B",
                        "name": f"{DEMO} Candidate",
                        "provenance": {
                            "agent_version": "demo-1.1",
                            "git_commit_sha": candidate_sha,
                            "metadata": {"agentscope_demo": True},
                        },
                    },
                ],
                "subjects": pairs,
                "evaluation_definition_ids": [definition["id"]],
            },
        )
        experiment = api.request("POST", f"/api/v1/experiments/{experiment['id']}/ready")
        summary["created"] += 1
    else:
        summary["reused"] += 1
    runs = api.request("GET", f"/api/v1/experiments/{experiment['id']}/runs?page_size=100")["items"]
    run = next((item for item in runs if item["status"] == "completed"), None)
    if run is None:
        run = next((item for item in runs if item["status"] in {"pending", "queued"}), None)
        if run is None:
            run = api.request("POST", f"/api/v1/experiments/{experiment['id']}/runs")

        async def execute() -> Any:
            run_id = UUID(run["id"])
            async with SessionLocal() as session:
                await submit_experiment_run(session, run_id)
            return await process_experiment_message(run_id)

        outcome = run_async(execute)
        if outcome.value != "completed":
            raise RuntimeError(f"demo experiment did not complete: {outcome.value}")
        summary["created"] += 1
    else:
        summary["reused"] += 1
    analysis = api.request("POST", f"/api/v1/experiment-runs/{run['id']}/analysis")
    return run, analysis


def _seed_regression(api: Any, run: dict, summary: dict[str, Any]) -> dict:
    policy = _create_or_find(
        api,
        "/api/v1/regression-policies",
        "/api/v1/regression-policies",
        f"{DEMO} — practical pass-rate drop",
        {
            "name": f"{DEMO} — practical pass-rate drop",
            "description": "Synthetic non-causal regression threshold.",
            "minimum_pass_rate_drop": 0.2,
            "minimum_sample_size": 5,
        },
        summary,
    )
    checks = api.request(
        "GET", f"/api/v1/regression-checks?experiment_run_id={run['id']}&page_size=100"
    )["items"]
    if checks:
        summary["reused"] += 1
        return checks[0]
    summary["created"] += 1
    return api.request(
        "POST",
        "/api/v1/regression-checks",
        {
            "experiment_run_id": run["id"],
            "regression_policy_id": policy["id"],
            "baseline_variant": "A",
            "candidate_variant": "B",
        },
    )


async def _seed_monitoring_history(
    configuration_id: UUID, monitor_id: UUID, summary: dict[str, Any]
) -> None:
    async with SessionLocal() as session:
        existing = list(
            await session.scalars(
                select(MonitoringSnapshotRecord).where(
                    MonitoringSnapshotRecord.monitoring_definition_id == monitor_id
                )
            )
        )
    if existing:
        summary["reused"] += len(existing)
        return
    rates = (0.0, 0.2, 0.2, 0.4, 0.4, 0.4, 0.6)
    snapshots: list[UUID] = []
    for offset, rate in enumerate(rates):
        count, failed = 100, round(rate * 100)
        async with SessionLocal() as session, session.begin():
            record = MonitoringSnapshotRecord(
                monitoring_definition_id=monitor_id,
                window_start=START + timedelta(hours=offset),
                window_end=START + timedelta(hours=offset + 1),
                status="completed",
                trace_count=count,
                successful_trace_count=count - failed,
                failed_trace_count=failed,
                success_rate=1 - rate,
                failure_rate=rate,
                queued_at=START,
                completed_at=START + timedelta(hours=offset + 1),
            )
            session.add(record)
            await session.flush()
            snapshots.append(record.id)
    for offset, snapshot_id in enumerate(snapshots[1:], start=1):
        now = START + timedelta(hours=10 + offset)
        async with SessionLocal() as session, session.begin():
            check = AutomaticDriftCheckRecord(
                automatic_drift_configuration_id=configuration_id,
                current_snapshot_id=snapshot_id,
                status="queued",
                queued_at=now,
            )
            session.add(check)
            await session.flush()
            check_id = check.id
        async with SessionLocal() as session:
            claim = await automatic_drift.claim_automatic_check(session, check_id, now=now)
        if claim is None:
            raise RuntimeError("demo drift check could not be claimed")
        outcome = await automatic_drift.finalize_automatic_check(
            claim, now=now + timedelta(seconds=1)
        )
        if outcome.value != "completed":
            raise RuntimeError(f"demo drift check did not complete: {outcome.value}")
    summary["created"] += 13


def _seed_monitoring(api: Any, summary: dict[str, Any], run_async: Any) -> tuple[dict, dict, dict]:
    monitor = _create_or_find(
        api,
        "/api/v1/monitoring-definitions",
        "/api/v1/monitoring-definitions",
        f"{DEMO} — hourly reliability",
        {
            "name": f"{DEMO} — hourly reliability",
            "description": "Fixed-window synthetic reliability evidence.",
            "window_duration": "1h",
            "trace_scope": {},
        },
        summary,
    )
    policy = _create_or_find(
        api,
        "/api/v1/drift-policies",
        "/api/v1/drift-policies",
        f"{DEMO} — failure-rate drift",
        {
            "name": f"{DEMO} — failure-rate drift",
            "description": "Synthetic absolute failure-rate increase.",
            "rules": [
                {
                    "metric": "trace_failure_rate",
                    "direction": "increase",
                    "threshold_type": "absolute",
                    "practical_threshold": 0.1,
                    "minimum_baseline_samples": 1,
                    "minimum_current_samples": 1,
                }
            ],
        },
        summary,
    )
    configuration = _create_or_find(
        api,
        "/api/v1/automatic-drift-configurations",
        "/api/v1/automatic-drift-configurations",
        f"{DEMO} — automatic drift",
        {
            "name": f"{DEMO} — automatic drift",
            "monitoring_definition_id": monitor["id"],
            "drift_policy_id": policy["id"],
            "cooldown_seconds": 0,
            "resolve_after_clean_windows": 2,
        },
        summary,
    )
    run_async(_seed_monitoring_history, UUID(configuration["id"]), UUID(monitor["id"]), summary)
    incidents = api.request(
        "GET",
        f"/api/v1/monitoring-incidents?automatic_drift_configuration_id={configuration['id']}"
        "&page_size=100",
    )["items"]
    if not incidents:
        raise RuntimeError("demo monitoring did not create an incident")
    incident = max(incidents, key=lambda item: item["occurrence_count"])
    return configuration, monitor, incident


def seed_demo(api: Any, run_async: Any = None) -> dict[str, Any]:
    if run_async is None:
        with asyncio.Runner() as runner:
            return seed_demo(api, lambda function, *args: runner.run(function(*args)))
    api.request("GET", "/health")
    runner = run_async
    summary: dict[str, Any] = {
        "created": 0,
        "reused": 0,
        "paid_provider_calls": 0,
        "trace_ids": TRACE_IDS,
        "ids": {},
    }
    _seed_traces(api, summary)
    definition, evaluation_run = _seed_evaluation(api, summary, runner)
    judge_run, calibration_analysis = _seed_calibration(api, summary, runner)
    experiment_run, experiment_analysis = _seed_experiment(api, definition, summary, runner)
    regression_check = _seed_regression(api, experiment_run, summary)
    monitoring, monitor, incident = _seed_monitoring(api, summary, runner)
    summary["ids"] = {
        "evaluation_definition": definition["id"],
        "evaluation_run": evaluation_run["id"],
        "judge_run": judge_run["id"],
        "calibration_analysis": calibration_analysis["run_id"],
        "experiment_run": experiment_run["id"],
        "experiment_analysis": experiment_analysis["id"],
        "regression_check": regression_check["id"],
        "monitoring_configuration": monitoring["id"],
        "monitoring_definition": monitor["id"],
        "monitoring_incident": incident["id"],
    }
    summary["routes"] = {
        "overview": "/",
        "representative_trace": f"/traces/{TRACE_IDS[0]}",
        "evaluation_run": f"/evaluations/runs/{evaluation_run['id']}",
        "calibration_report": f"/calibration/runs/{judge_run['id']}",
        "experiment_report": f"/experiments/runs/{experiment_run['id']}",
        "regression_check": f"/regressions/{regression_check['id']}",
        "monitor": f"/monitoring/{monitor['id']}",
        "incident": f"/monitoring/incidents/{incident['id']}",
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    args = parser.parse_args()
    try:
        result = seed_demo(Api(args.api_url))
    except Exception as error:
        raise SystemExit(
            f"Demo seed failed safely ({type(error).__name__}); no data was reset."
        ) from None
    print(
        f"AgentScope Demo ready: {result['created']} created, "
        f"{result['reused']} reused; paid provider calls: 0"
    )
    print("Evidence routes:")
    for key, value in result["routes"].items():
        print(f"  {key}: {value}")
    print("Next: run scripts/run_bisection_demo.py; walkthrough: docs/demo.md")


if __name__ == "__main__":
    main()
