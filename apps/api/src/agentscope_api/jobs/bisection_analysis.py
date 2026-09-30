from __future__ import annotations

import logging
from uuid import UUID

import dramatiq

from agentscope_api.database import SessionLocal
from agentscope_api.jobs.bisection_execution import enqueue_bisection_execution
from agentscope_api.jobs.broker import broker as broker
from agentscope_api.services.bisection_execution import mark_execution_run_enqueued
from agentscope_api.services.bisection_orchestration import (
    AnalysisProcessOutcome,
    process_analysis_message,
)

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="evaluations", max_retries=0)
async def bisection_analysis_job(analysis_id: str) -> None:
    try:
        parsed = UUID(analysis_id)
    except ValueError:
        logger.warning("bisection_analysis_message_rejected category=invalid_analysis_id")
        return
    result = await process_analysis_message(parsed)
    if result.execution_run_id is not None and result.execution_created:
        try:
            enqueue_bisection_execution(result.execution_run_id)
        except Exception:
            logger.warning(
                "bisection_analysis_execution_enqueue_deferred run_id=%s",
                result.execution_run_id,
            )
        else:
            async with SessionLocal() as session:
                await mark_execution_run_enqueued(session, result.execution_run_id)
    if result.outcome in {AnalysisProcessOutcome.REQUEUED, AnalysisProcessOutcome.WAITING}:
        delay = 5_000 if result.outcome is AnalysisProcessOutcome.WAITING else 100
        try:
            bisection_analysis_job.send_with_options(args=(analysis_id,), delay=delay)
        except Exception:
            logger.warning("bisection_analysis_reenqueue_deferred analysis_id=%s", analysis_id)


def enqueue_bisection_analysis(analysis_id: UUID) -> None:
    bisection_analysis_job.send(str(analysis_id))
