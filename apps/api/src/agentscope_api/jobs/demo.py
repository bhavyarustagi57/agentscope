import logging

import dramatiq

from agentscope_api.jobs.broker import broker as broker

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name="default", max_retries=0)
def demo_job(message: str = "AgentScope worker is ready") -> None:
    logger.info("Demo job completed: %s", message)
