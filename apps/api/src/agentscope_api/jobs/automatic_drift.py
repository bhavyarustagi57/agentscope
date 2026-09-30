from __future__ import annotations

import logging
from uuid import UUID

import dramatiq

from agentscope_api.jobs.broker import broker as broker

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="evaluations", max_retries=0)
async def automatic_drift_check_job(check_id: str) -> None:
    try:
        parsed = UUID(check_id)
    except ValueError:
        logger.warning("automatic_drift_message_rejected category=invalid_check_id")
        return
    from agentscope_api.services.automatic_drift import process_automatic_drift_message

    outcome = await process_automatic_drift_message(parsed)
    logger.info("automatic_drift_check_finished check_id=%s outcome=%s", parsed, outcome)


def enqueue_automatic_drift_check(check_id: UUID) -> None:
    automatic_drift_check_job.send(str(check_id))
