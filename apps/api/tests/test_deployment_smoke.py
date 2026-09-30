from __future__ import annotations

import importlib.util
from email.message import Message
from pathlib import Path
from urllib.error import URLError

import pytest


def _script_module():
    script = Path(__file__).parents[1] / "scripts" / "deployment_smoke.py"
    spec = importlib.util.spec_from_file_location("agentscope_deployment_smoke", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, body: str, headers: dict[str, str] | None = None) -> None:
        self._body = body.encode()
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_smoke_checks_live_routes_and_security_headers() -> None:
    smoke = _script_module()
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }
    responses = {
        "http://api/health": _Response('{"status":"ok"}', headers),
        "http://api/ready": _Response(
            '{"status":"ready","dependencies":{"postgres":"ok","redis":"ok"}}', headers
        ),
        "http://api/api/v1/traces?page_size=1": _Response(
            '{"items":[],"next_cursor":null,"has_more":false}', headers
        ),
        "http://web/": _Response("<!doctype html><title>AgentScope</title>", headers),
    }

    def open_response(request, **_):
        return responses[request.full_url]

    result = smoke.check_deployment(
        "http://api", "http://web", timeout_seconds=1, opener=open_response
    )

    assert [check.name for check in result] == ["health", "readiness", "api-read", "web"]
    assert all(check.passed for check in result)


def test_smoke_reports_sanitized_failure() -> None:
    smoke = _script_module()

    def unavailable(*_: object, **__: object):
        raise URLError("postgresql://secret:password@database/internal")

    result = smoke.check_deployment(
        "http://api", "http://web", timeout_seconds=1, opener=unavailable
    )

    assert all(not check.passed for check in result)
    assert {check.detail for check in result} == {"unavailable"}


def test_smoke_rejects_unbounded_timeout() -> None:
    smoke = _script_module()

    with pytest.raises(ValueError, match="between 0.1 and 10 seconds"):
        smoke.check_deployment("http://api", "http://web", timeout_seconds=60)
