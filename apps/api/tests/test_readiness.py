import asyncio

from agentscope_api.services import readiness


async def test_readiness_bounds_slow_dependency_probes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def slow_probe() -> bool:
        await asyncio.sleep(1)
        return True

    monkeypatch.setattr(readiness, "_postgres_available", slow_probe)
    monkeypatch.setattr(readiness, "_redis_available", slow_probe)

    started = asyncio.get_running_loop().time()
    result = await readiness.check_readiness(timeout_seconds=0.01)
    elapsed = asyncio.get_running_loop().time() - started

    assert result.model_dump() == {"postgres": "unavailable", "redis": "unavailable"}
    assert elapsed < 0.2
