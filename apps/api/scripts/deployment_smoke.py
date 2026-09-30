"""Run bounded, read-only checks against a running AgentScope deployment."""

from __future__ import annotations

import argparse
import json
import urllib.request
from collections.abc import Callable
from typing import Any, NamedTuple
from urllib.parse import urlsplit


class CheckResult(NamedTuple):
    name: str
    passed: bool
    detail: str


REQUIRED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("deployment URLs must be HTTP(S) origins")
    return value.rstrip("/")


def _request(
    name: str,
    url: str,
    timeout_seconds: float,
    opener: Callable[..., Any],
    validate: Callable[[bytes], bool],
) -> CheckResult:
    try:
        with opener(urllib.request.Request(url), timeout=timeout_seconds) as response:
            body = response.read()
            headers_ok = all(
                response.headers.get(key) == value
                for key, value in REQUIRED_HEADERS.items()
            )
            if not headers_ok:
                return CheckResult(name, False, "security headers missing")
            if not validate(body):
                return CheckResult(name, False, "unexpected response")
    except Exception:
        return CheckResult(name, False, "unavailable")
    return CheckResult(name, True, "ok")


def _json_matches(expected: Callable[[dict[str, Any]], bool]) -> Callable[[bytes], bool]:
    def validate(body: bytes) -> bool:
        value = json.loads(body)
        return isinstance(value, dict) and expected(value)

    return validate


def check_deployment(
    api_url: str,
    web_url: str,
    *,
    timeout_seconds: float = 5,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[CheckResult]:
    if not 0.1 <= timeout_seconds <= 10:
        raise ValueError("timeout must be between 0.1 and 10 seconds")
    api = _origin(api_url)
    web = _origin(web_url)
    return [
        _request(
            "health",
            f"{api}/health",
            timeout_seconds,
            opener,
            _json_matches(lambda value: value.get("status") == "ok"),
        ),
        _request(
            "readiness",
            f"{api}/ready",
            timeout_seconds,
            opener,
            _json_matches(
                lambda value: value.get("status") == "ready"
                and value.get("dependencies") == {"postgres": "ok", "redis": "ok"}
            ),
        ),
        _request(
            "api-read",
            f"{api}/api/v1/traces?page_size=1",
            timeout_seconds,
            opener,
            _json_matches(lambda value: isinstance(value.get("items"), list)),
        ),
        _request(
            "web",
            f"{web}/",
            timeout_seconds,
            opener,
            lambda body: b"AgentScope" in body,
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--web-url", default="http://localhost:3000")
    parser.add_argument("--timeout-seconds", type=float, default=5)
    args = parser.parse_args()
    try:
        results = check_deployment(
            args.api_url,
            args.web_url,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError as error:
        raise SystemExit(f"[FAIL] configuration: {error}") from None
    for result in results:
        print(f"[{'PASS' if result.passed else 'FAIL'}] {result.name}: {result.detail}")
    if not all(result.passed for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
