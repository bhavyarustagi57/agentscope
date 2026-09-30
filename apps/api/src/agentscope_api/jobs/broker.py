import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import AsyncIO

from agentscope_api.core.config import get_settings
from agentscope_api.jobs.recover import EvaluationRecoveryMiddleware

broker = RedisBroker(url=get_settings().redis_url)  # type: ignore[no-untyped-call]
broker.add_middleware(AsyncIO())
broker.add_middleware(EvaluationRecoveryMiddleware())
dramatiq.set_broker(broker)
