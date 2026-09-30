from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path


def _script_module(name: str = "seed_demo.py"):
    script = Path(__file__).parents[1] / "scripts" / name
    sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("agentscope_demo_seed_runtime", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_seed_runner_reuses_one_event_loop(monkeypatch) -> None:
    demo = _script_module()
    loop_ids: list[int] = []

    async def capture_loop() -> None:
        loop_ids.append(id(asyncio.get_running_loop()))

    def run_step(run_async) -> None:
        run_async(capture_loop)

    monkeypatch.setattr(demo, "_seed_traces", lambda *_: None)
    monkeypatch.setattr(
        demo,
        "_seed_evaluation",
        lambda _api, _summary, run_async: (
            run_step(run_async) or {"id": "definition"},
            {"id": "evaluation"},
        ),
    )
    monkeypatch.setattr(
        demo,
        "_seed_calibration",
        lambda _api, _summary, run_async: (
            run_step(run_async) or {"id": "judge"},
            {"run_id": "calibration"},
        ),
    )
    monkeypatch.setattr(
        demo,
        "_seed_experiment",
        lambda _api, _definition, _summary, run_async: (
            run_step(run_async) or {"id": "experiment"},
            {"id": "analysis"},
        ),
    )
    monkeypatch.setattr(demo, "_seed_regression", lambda *_: {"id": "regression"})
    monkeypatch.setattr(
        demo,
        "_seed_monitoring",
        lambda _api, _summary, run_async: run_step(run_async)
        or ({"id": "monitoring"}, {"id": "monitor"}, {"id": "incident"}),
    )

    class Api:
        @staticmethod
        def request(*_: object) -> None:
            return None

    result = demo.seed_demo(Api())

    assert len(loop_ids) == 4
    assert len(set(loop_ids)) == 1
    assert result["paid_provider_calls"] == 0
    assert result["routes"] == {
        "overview": "/",
        "representative_trace": "/traces/agentscope-demo-workspace-v1-00",
        "evaluation_run": "/evaluations/runs/evaluation",
        "calibration_report": "/calibration/runs/judge",
        "experiment_report": "/experiments/runs/experiment",
        "regression_check": "/regressions/regression",
        "monitor": "/monitoring/monitor",
        "incident": "/monitoring/incidents/incident",
    }


def test_default_bisection_runner_reuses_one_event_loop(monkeypatch, tmp_path: Path) -> None:
    demo = _script_module("run_bisection_demo.py")
    loops: list[asyncio.AbstractEventLoop] = []
    commits = [str(index) for index in range(5)]

    async def capture_loop() -> None:
        loops.append(asyncio.get_running_loop())

    def seed_demo(_api, run_async):
        run_async(capture_loop)
        return {"ids": {"evaluation_definition": "definition"}}

    def seed_experiment(_api, _definition, _summary, run_async, **_kwargs):
        run_async(capture_loop)
        return {"id": "run"}, {"id": "analysis"}

    monkeypatch.setattr(demo, "_owned_repository", lambda _: (tmp_path, commits))
    monkeypatch.setattr(demo, "_git", lambda *_: "unchanged")
    monkeypatch.setattr(demo, "seed_demo", seed_demo)
    monkeypatch.setattr(demo, "_seed_experiment", seed_experiment)
    monkeypatch.setattr(demo, "_seed_regression", lambda *_: {"id": "check"})

    class Api:
        @staticmethod
        def request(_method: str, path: str, *_: object):
            if path.startswith("/api/v1/evaluation-definitions/"):
                return {"id": "definition"}
            if path.startswith("/api/v1/bisection-sessions?"):
                return {"items": [{"id": "session"}]}
            if path == "/api/v1/bisection-sessions/session":
                return {"id": "session"}
            if path.endswith("/analyses?page_size=100"):
                return {
                    "items": [
                        {
                            "id": "bisection-analysis",
                            "status": "attributed",
                            "final_good_commit_sha": commits[2],
                            "final_bad_commit_sha": commits[3],
                        }
                    ]
                }
            raise AssertionError(path)

    result = demo.run_bisection_demo(Api(), tmp_path)

    assert len(loops) == 2
    assert loops[0] is loops[1]
    assert result["target_unchanged"] is True
