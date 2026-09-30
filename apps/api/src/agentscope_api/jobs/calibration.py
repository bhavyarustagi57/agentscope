from __future__ import annotations

import logging
from uuid import UUID

import dramatiq

from agentscope_api.jobs.broker import broker as broker
from agentscope_api.services.judge_orchestration import (
    ProcessOutcome,
    judge_retry_delay_ms,
    process_judge_message,
)

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="calibration", max_retries=0)
async def judge_run_job(run_id: str) -> None:
    try:
        parsed = UUID(run_id)
    except ValueError:
        logger.warning("judge_message_rejected category=invalid_run_id")
        return
    if await process_judge_message(parsed) is ProcessOutcome.REQUEUED:
        judge_run_job.send_with_options(
            args=(run_id,), delay=await judge_retry_delay_ms(parsed)
        )


def enqueue_judge_run(run_id: UUID) -> None:
    judge_run_job.send(str(run_id))
