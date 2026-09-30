from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from dramatiq.middleware import Middleware

from agentscope_api.core.config import get_settings
from agentscope_api.database import SessionLocal, engine
from agentscope_api.services.automatic_drift import (
    discover_automatic_checks,
    recover_automatic_checks,
)
from agentscope_api.services.automatic_drift import (
    release_recovery_reservation as release_automatic_drift_recovery_reservation,
)
from agentscope_api.services.bisection_execution import (
    recover_execution_runs,
    release_execution_recovery_reservation,
)
from agentscope_api.services.bisection_orchestration import (
    recover_analyses,
    release_analysis_recovery_reservation,
)
from agentscope_api.services.evaluation_orchestration import (
    recover_evaluation_runs,
    release_recovery_reservation,
)
from agentscope_api.services.experiment_execution import (
    recover_experiment_runs,
    release_experiment_recovery_reservation,
)
from agentscope_api.services.judge_orchestration import (
    recover_judge_runs,
    release_judge_recovery_reservation,
)
from agentscope_api.services.monitoring_orchestration import (
    discover_missing_snapshots,
    recover_snapshots,
)
from agentscope_api.services.monitoring_orchestration import (
    release_recovery_reservation as release_monitoring_recovery_reservation,
)

logger = logging.getLogger(__name__)


class EvaluationRecoveryMiddleware(Middleware):
    @property
    def forks(self) -> list[Callable[[], int]]:
        return [run_recovery_loop]


async def recover_once(
    *,
    enqueue: Callable[[UUID], None] | None = None,
    now: datetime | None = None,
) -> int:
    if enqueue is None:
        from agentscope_api.jobs.evaluations import enqueue_evaluation_run

        enqueue = enqueue_evaluation_run
    reserved_at = now or datetime.now(UTC)
    async with SessionLocal() as session:
        run_ids = await recover_evaluation_runs(session, now=reserved_at)
    sent = 0
    for run_id in run_ids:
        try:
            await asyncio.to_thread(enqueue, run_id)
        except Exception:
            logger.warning("evaluation_recovery_enqueue_deferred run_id=%s", run_id)
            async with SessionLocal() as session:
                await release_recovery_reservation(session, run_id, reserved_at)
            continue
        sent += 1
    logger.info(
        "evaluation_recovery_tick candidate_count=%d enqueued_count=%d entry_point=periodic",
        len(run_ids),
        sent,
    )
    return sent


async def _recover_and_dispose() -> None:
    try:
        await recover_once()
        await recover_experiments_once()
        await recover_judges_once()
        await recover_bisections_once()
        await recover_bisection_analyses_once()
        await recover_monitoring_once()
        await recover_automatic_drift_once()
    finally:
        await engine.dispose()


def run_recovery_loop() -> int:
    interval = get_settings().evaluation_recovery_interval_seconds
    while True:
        try:
            asyncio.run(_recover_and_dispose())
        except Exception as error:
            logger.error(
                "evaluation_recovery_tick_failed category=%s entry_point=periodic",
                type(error).__name__,
            )
        time.sleep(interval)


async def recover_judges_once(
    *, enqueue: Callable[[UUID], None] | None = None, now: datetime | None = None
) -> int:
    if enqueue is None:
        from agentscope_api.jobs.calibration import enqueue_judge_run

        enqueue = enqueue_judge_run
    reserved_at = now or datetime.now(UTC)
    async with SessionLocal() as session:
        run_ids = await recover_judge_runs(session, now=reserved_at)
    sent = 0
    for run_id in run_ids:
        try:
            await asyncio.to_thread(enqueue, run_id)
        except Exception:
            logger.warning("judge_recovery_enqueue_deferred run_id=%s", run_id)
            async with SessionLocal() as session:
                await release_judge_recovery_reservation(session, run_id, reserved_at)
            continue
        sent += 1
    logger.info(
        "judge_recovery_tick candidate_count=%d enqueued_count=%d entry_point=periodic",
        len(run_ids),
        sent,
    )
    return sent


async def recover_experiments_once(
    *, enqueue: Callable[[UUID], None] | None = None, now: datetime | None = None
) -> int:
    if enqueue is None:
        from agentscope_api.jobs.experiments import enqueue_experiment_run

        enqueue = enqueue_experiment_run
    reserved_at = now or datetime.now(UTC)
    async with SessionLocal() as session:
        run_ids = await recover_experiment_runs(session, now=reserved_at)
    sent = 0
    for run_id in run_ids:
        try:
            await asyncio.to_thread(enqueue, run_id)
        except Exception:
            logger.warning("experiment_recovery_enqueue_deferred run_id=%s", run_id)
            async with SessionLocal() as session:
                await release_experiment_recovery_reservation(session, run_id, reserved_at)
            continue
        sent += 1
    logger.info(
        "experiment_recovery_tick candidate_count=%d enqueued_count=%d entry_point=periodic",
        len(run_ids),
        sent,
    )
    return sent


async def recover_bisections_once(
    *, enqueue: Callable[[UUID], None] | None = None, now: datetime | None = None
) -> int:
    if enqueue is None:
        from agentscope_api.jobs.bisection_execution import enqueue_bisection_execution

        enqueue = enqueue_bisection_execution
    reserved_at = now or datetime.now(UTC)
    async with SessionLocal() as session:
        run_ids = await recover_execution_runs(session, now=reserved_at)
    sent = 0
    for run_id in run_ids:
        try:
            await asyncio.to_thread(enqueue, run_id)
        except Exception:
            logger.warning("bisection_execution_recovery_enqueue_deferred run_id=%s", run_id)
            async with SessionLocal() as session:
                await release_execution_recovery_reservation(session, run_id, reserved_at)
            continue
        sent += 1
    logger.info(
        "bisection_execution_recovery_tick candidate_count=%d enqueued_count=%d "
        "entry_point=periodic",
        len(run_ids),
        sent,
    )
    return sent


async def recover_bisection_analyses_once(
    *, enqueue: Callable[[UUID], None] | None = None, now: datetime | None = None
) -> int:
    if enqueue is None:
        from agentscope_api.jobs.bisection_analysis import enqueue_bisection_analysis

        enqueue = enqueue_bisection_analysis
    reserved_at = now or datetime.now(UTC)
    async with SessionLocal() as session:
        analysis_ids = await recover_analyses(session, now=reserved_at)
    sent = 0
    for analysis_id in analysis_ids:
        try:
            await asyncio.to_thread(enqueue, analysis_id)
        except Exception:
            logger.warning(
                "bisection_analysis_recovery_enqueue_deferred analysis_id=%s", analysis_id
            )
            async with SessionLocal() as session:
                await release_analysis_recovery_reservation(session, analysis_id, reserved_at)
            continue
        sent += 1
    logger.info(
        "bisection_analysis_recovery_tick candidate_count=%d enqueued_count=%d "
        "entry_point=periodic",
        len(analysis_ids),
        sent,
    )
    return sent


async def recover_monitoring_once(
    *, enqueue: Callable[[UUID], None] | None = None, now: datetime | None = None
) -> int:
    if enqueue is None:
        from agentscope_api.jobs.monitoring import enqueue_monitoring_snapshot

        enqueue = enqueue_monitoring_snapshot
    reserved_at = now or datetime.now(UTC)
    async with SessionLocal() as session:
        await discover_missing_snapshots(
            session,
            now=reserved_at,
            max_windows=get_settings().monitoring_max_catchup_windows,
        )
    async with SessionLocal() as session:
        snapshot_ids = await recover_snapshots(session, now=reserved_at)
    sent = 0
    for snapshot_id in snapshot_ids:
        try:
            await asyncio.to_thread(enqueue, snapshot_id)
        except Exception:
            logger.warning("monitoring_recovery_enqueue_deferred snapshot_id=%s", snapshot_id)
            async with SessionLocal() as session:
                await release_monitoring_recovery_reservation(session, snapshot_id, reserved_at)
            continue
        sent += 1
    logger.info(
        "monitoring_recovery_tick candidate_count=%d enqueued_count=%d entry_point=periodic",
        len(snapshot_ids),
        sent,
    )
    return sent


async def recover_automatic_drift_once(
    *, enqueue: Callable[[UUID], None] | None = None, now: datetime | None = None
) -> int:
    if enqueue is None:
        from agentscope_api.jobs.automatic_drift import enqueue_automatic_drift_check

        enqueue = enqueue_automatic_drift_check
    reserved_at = now or datetime.now(UTC)
    limit = get_settings().monitoring_max_drift_checks_per_scan
    async with SessionLocal() as session:
        await discover_automatic_checks(session, now=reserved_at, limit=limit)
    async with SessionLocal() as session:
        check_ids = await recover_automatic_checks(session, now=reserved_at, limit=limit)
    sent = 0
    for check_id in check_ids:
        try:
            await asyncio.to_thread(enqueue, check_id)
        except Exception:
            logger.warning("automatic_drift_recovery_enqueue_deferred check_id=%s", check_id)
            async with SessionLocal() as session:
                await release_automatic_drift_recovery_reservation(session, check_id, reserved_at)
            continue
        sent += 1
    logger.info(
        "automatic_drift_recovery_tick candidate_count=%d enqueued_count=%d entry_point=periodic",
        len(check_ids),
        sent,
    )
    return sent
