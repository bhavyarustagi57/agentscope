from __future__ import annotations

import logging
from uuid import UUID

import dramatiq

from agentscope_api.jobs.broker import broker as broker
from agentscope_api.services.evaluation_orchestration import process_evaluation_message

logger = logging.getLogger(__name__)


@dramatiq.actor(
    queue_name="evaluations",
    max_retries=3,
    min_backoff=5_000,
    max_backoff=60_000,
)
async def evaluation_run_job(run_id: str) -> None:
    try:
        parsed_run_id = UUID(run_id)
    except ValueError:
        logger.warning("evaluation_message_rejected category=invalid_run_id")
        return
    await process_evaluation_message(parsed_run_id)


def enqueue_evaluation_run(run_id: UUID) -> None:
    evaluation_run_job.send(str(run_id))
