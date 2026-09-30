from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).parents[3] / "packages" / "python-sdk" / "src"))

from agentscope_api.core.config import get_settings  # noqa: E402
from agentscope_api.database import engine  # noqa: E402


async def _clear_traces() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE monitoring_incident_events, monitoring_incidents, "
                "automatic_drift_checks, automatic_drift_configurations, "
                "drift_findings, drift_comparisons, drift_policy_rules, "
                "drift_policies, monitoring_snapshots, monitoring_definitions, "
                "bisection_analysis_steps, bisection_analyses, "
                "bisection_execution_attempts, bisection_execution_targets, "
                "bisection_execution_runs, bisection_commits, bisection_sessions, "
                "regression_findings, regression_checks, regression_policies, "
                "experiment_condition_analyses, experiment_run_analyses, "
                "experiment_run_results, experiment_runs, "
                "experiment_evaluation_conditions, experiment_subjects, "
                "experiment_variants, experiments, "
                "calibration_analyses, calibration_judge_results, "
                "calibration_judge_runs, "
                "judge_configurations, calibration_study_subjects, calibration_studies, "
                "human_annotations, human_reference_subjects, human_reference_sets, "
                "evaluation_run_subjects, evaluation_results, evaluation_runs, "
                "evaluation_definitions, spans, traces"
            )
        )
    await engine.dispose()


@pytest.fixture
def clean_database() -> Iterator[None]:
    settings = get_settings()
    database_name = make_url(settings.database_url).database or ""
    if settings.app_env != "test" or not database_name.endswith("_test"):
        pytest.fail("integration tests require APP_ENV=test and a *_test database")
    asyncio.run(_clear_traces())
    yield
    asyncio.run(_clear_traces())
