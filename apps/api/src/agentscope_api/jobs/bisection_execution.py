from __future__ import annotations

import logging
from uuid import UUID

import dramatiq

from agentscope_api.jobs.broker import broker as broker
from agentscope_api.services.bisection_execution import ProcessOutcome, process_execution_message

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="evaluations", max_retries=0)
async def bisection_execution_job(run_id: str) -> None:
    try:
        parsed = UUID(run_id)
    except ValueError:
        logger.warning("bisection_execution_message_rejected category=invalid_run_id")
        return
    if await process_execution_message(parsed) is ProcessOutcome.REQUEUED:
        bisection_execution_job.send_with_options(args=(run_id,), delay=5_000)


def enqueue_bisection_execution(run_id: UUID) -> None:
    bisection_execution_job.send(str(run_id))
