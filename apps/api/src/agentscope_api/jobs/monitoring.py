from __future__ import annotations

import logging
from uuid import UUID

import dramatiq

from agentscope_api.jobs.broker import broker as broker

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="evaluations", max_retries=0)
async def monitoring_snapshot_job(snapshot_id: str) -> None:
    try:
        parsed = UUID(snapshot_id)
    except ValueError:
        logger.warning("monitoring_snapshot_message_rejected category=invalid_snapshot_id")
        return
    from agentscope_api.services.monitoring_orchestration import process_monitoring_message

    await process_monitoring_message(parsed)


def enqueue_monitoring_snapshot(snapshot_id: UUID) -> None:
    monitoring_snapshot_job.send(str(snapshot_id))
