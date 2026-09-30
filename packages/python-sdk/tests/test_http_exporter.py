import asyncio
import json
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from agentscope_sdk import (
    DEFAULT_MAX_JSON_BYTES,
    ExportFailureKind,
    HttpTraceExporter,
    RetryPolicy,
    Trace,
    Tracer,
)
from agentscope_sdk.http import _HttpRequest, _HttpResponse


@dataclass
class FakeTransport:
    outcomes: deque[_HttpResponse | Exception]

    def __init__(self, *outcomes: _HttpResponse | Exception) -> None:
        self.outcomes = deque(outcomes)
        self.requests: list[_HttpRequest] = []
        self.called = threading.Event()
        self._lock = threading.Lock()

    def send(self, request: _HttpRequest) -> _HttpResponse:
        with self._lock:
            self.requests.append(request)
            outcome = self.outcomes.popleft() if self.outcomes else _HttpResponse(202)
            self.called.set()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def completed_trace(name: str = "test", *, input: object = None) -> Trace:
    tracer = Tracer()
    with tracer.trace(name, input=input) as trace:  # type: ignore[arg-type]
        trace.set_output({"ok": True})
    return trace


def make_exporter(
    transport: FakeTransport,
    *,
    sleeps: list[float] | None = None,
    **kwargs: object,
) -> HttpTraceExporter:
    return HttpTraceExporter(
        endpoint="https://traces.example.test/api/v1/traces",
        retry_policy=RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=5, jitter=0),
        _transport=transport,
        _sleep=(sleeps.append if sleeps is not None else lambda _: None),
        **kwargs,
    )


def test_successful_single_export_uses_documented_wire_contract() -> None:
    transport = FakeTransport(_HttpResponse(202, body=b'{"accepted":1}'))
    exporter = make_exporter(transport, timeout=2.5, batch_size=10, flush_interval=60)
    trace = completed_trace("wire-contract", input={"question": "hello"})

    exporter.export(trace)
    assert exporter.flush(timeout=1)
    assert exporter.close(timeout=1)

    assert exporter.delivered_traces == 1
    assert exporter.failures == ()
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url == "https://traces.example.test/api/v1/traces"
    assert request.timeout == 2.5
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["Idempotency-Key"] == trace.trace_id
    assert json.loads(request.body) == {
        "schema_version": "1",
        "traces": [trace.to_dict()],
    }


def test_authentication_and_custom_headers_are_safe() -> None:
    secret = "super-secret-token"
    transport = FakeTransport(_HttpResponse(401, body=b'{"error":"unauthorized"}'))
    exporter = make_exporter(
        transport,
        api_token=secret,
        headers={"X-Agent-Environment": "test"},
    )

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    request = transport.requests[0]
    assert request.headers["Authorization"] == f"Bearer {secret}"
    assert request.headers["X-Agent-Environment"] == "test"
    assert secret.encode() not in request.body
    assert secret not in repr(exporter)
    assert secret not in repr(exporter.failures)
    assert exporter.failures[0].kind is ExportFailureKind.HTTP
    assert exporter.failures[0].status_code == 401


@pytest.mark.parametrize(
    "outcome",
    [ConnectionError("offline"), TimeoutError("slow")],
)
def test_transient_transport_failures_are_retried(outcome: Exception) -> None:
    sleeps: list[float] = []
    transport = FakeTransport(outcome, _HttpResponse(202))
    exporter = make_exporter(transport, sleeps=sleeps)

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert len(transport.requests) == 2
    assert sleeps == [0.1]
    assert exporter.delivered_traces == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_http_statuses_are_retried(status: int) -> None:
    transport = FakeTransport(_HttpResponse(status), _HttpResponse(202))
    exporter = make_exporter(transport)

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert len(transport.requests) == 2
    assert exporter.delivered_traces == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_non_retryable_http_statuses_fail_once(status: int) -> None:
    transport = FakeTransport(_HttpResponse(status))
    exporter = make_exporter(transport)

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert len(transport.requests) == 1
    assert exporter.failures[0].kind is ExportFailureKind.HTTP
    assert exporter.failures[0].attempts == 1
    assert exporter.failures[0].status_code == status


def test_retries_are_bounded_and_use_exponential_backoff() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(_HttpResponse(503), _HttpResponse(503), _HttpResponse(503))
    exporter = make_exporter(transport, sleeps=sleeps)

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert len(transport.requests) == 3
    assert sleeps == [0.1, 0.2]
    assert exporter.failures[0].attempts == 3


def test_retry_after_is_respected_within_the_delay_cap() -> None:
    sleeps: list[float] = []
    transport = FakeTransport(
        _HttpResponse(429, headers={"Retry-After": "3"}),
        _HttpResponse(202),
    )
    exporter = make_exporter(transport, sleeps=sleeps)

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert sleeps == [3.0]


def test_retry_after_http_date_is_supported() -> None:
    sleeps: list[float] = []
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    transport = FakeTransport(
        _HttpResponse(503, headers={"Retry-After": "Wed, 16 Sep 2026 12:00:04 GMT"}),
        _HttpResponse(202),
    )
    exporter = make_exporter(transport, sleeps=sleeps, _now=lambda: now)

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert sleeps == [4.0]


def test_retry_reuses_payload_trace_identity_and_idempotency_key() -> None:
    transport = FakeTransport(ConnectionError("offline"), _HttpResponse(202))
    exporter = make_exporter(transport)
    trace = completed_trace()

    exporter.export(trace)
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    first, second = transport.requests
    assert first.body == second.body
    assert first.headers["Idempotency-Key"] == trace.trace_id
    assert second.headers["Idempotency-Key"] == trace.trace_id


def test_malformed_success_response_is_observable_and_not_retried() -> None:
    transport = FakeTransport(_HttpResponse(202, body=b"not-json"))
    exporter = make_exporter(transport)

    exporter.export(completed_trace())
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert len(transport.requests) == 1
    assert exporter.failures[0].kind is ExportFailureKind.PROTOCOL


def test_serialization_failure_is_distinct_and_does_not_break_application() -> None:
    failures = []
    transport = FakeTransport()
    exporter = make_exporter(transport, on_error=failures.append)
    tracer = Tracer(exporter=exporter)

    with tracer.trace("unsafe", input=object()):  # type: ignore[arg-type]
        pass

    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)
    assert transport.requests == []
    assert failures[0].kind is ExportFailureKind.SERIALIZATION
    assert exporter.failures[0] == failures[0]


def test_application_exception_survives_transport_failure() -> None:
    transport = FakeTransport(ConnectionError("offline"))
    exporter = HttpTraceExporter(
        endpoint="https://traces.example.test/api/v1/traces",
        retry_policy=RetryPolicy(max_attempts=1),
        _transport=transport,
    )
    tracer = Tracer(exporter=exporter)
    application_error = ValueError("application failed")

    with pytest.raises(ValueError) as raised:
        with tracer.trace("failure"):
            raise application_error

    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)
    assert raised.value is application_error
    assert exporter.failures[0].kind is ExportFailureKind.TRANSPORT


def test_batch_threshold_flushes_in_order_with_stable_batch_identity() -> None:
    transport = FakeTransport(_HttpResponse(202))
    exporter = make_exporter(transport, batch_size=2, flush_interval=60)
    first = completed_trace("first")
    second = completed_trace("second")

    exporter.export(first)
    exporter.export(second)
    assert transport.called.wait(1)
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    request = transport.requests[0]
    payload = json.loads(request.body)
    assert [item["trace_id"] for item in payload["traces"]] == [
        first.trace_id,
        second.trace_id,
    ]
    assert request.headers["Idempotency-Key"].startswith("batch_")


def test_explicit_flush_sends_partial_batch() -> None:
    transport = FakeTransport(_HttpResponse(202))
    exporter = make_exporter(transport, batch_size=10, flush_interval=60)
    exporter.export(completed_trace())

    assert transport.requests == []
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert len(transport.requests) == 1


def test_batch_byte_limit_splits_requests_without_reordering() -> None:
    traces = [completed_trace(str(index), input={"value": "x" * 100}) for index in range(3)]
    one_size = len(
        json.dumps(
            {"schema_version": "1", "traces": [traces[0].to_dict()]},
            separators=(",", ":"),
        ).encode()
    )
    two_size = len(
        json.dumps(
            {"schema_version": "1", "traces": [trace.to_dict() for trace in traces[:2]]},
            separators=(",", ":"),
        ).encode()
    )
    transport = FakeTransport(_HttpResponse(202), _HttpResponse(202), _HttpResponse(202))
    exporter = make_exporter(
        transport,
        batch_size=10,
        flush_interval=60,
        max_batch_bytes=max(one_size, two_size - 1),
    )

    for trace in traces:
        exporter.export(trace)
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    delivered_ids = [
        trace["trace_id"]
        for request in transport.requests
        for trace in json.loads(request.body)["traces"]
    ]
    assert delivered_ids == [trace.trace_id for trace in traces]
    assert all(len(request.body) <= max(one_size, two_size - 1) for request in transport.requests)


def test_queue_limits_memory_and_reports_dropped_trace() -> None:
    transport = FakeTransport(_HttpResponse(202))
    exporter = make_exporter(
        transport,
        batch_size=10,
        flush_interval=60,
        max_queue_size=1,
    )

    exporter.export(completed_trace("accepted"))
    exporter.export(completed_trace("dropped"))

    assert exporter.pending_traces == 1
    assert exporter.failures[0].kind is ExportFailureKind.QUEUE_FULL
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)


def test_failed_batch_is_discarded_after_bounded_attempts() -> None:
    transport = FakeTransport(_HttpResponse(500))
    exporter = HttpTraceExporter(
        endpoint="https://traces.example.test/api/v1/traces",
        batch_size=2,
        retry_policy=RetryPolicy(max_attempts=1),
        _transport=transport,
    )
    traces = [completed_trace("first"), completed_trace("second")]

    for trace in traces:
        exporter.export(trace)
    assert exporter.flush(timeout=1)
    exporter.close(timeout=1)

    assert exporter.pending_traces == 0
    assert exporter.delivered_traces == 0
    assert exporter.failures[0].trace_ids == tuple(trace.trace_id for trace in traces)


def test_close_flushes_is_idempotent_and_rejects_new_work_observably() -> None:
    transport = FakeTransport(_HttpResponse(202))
    exporter = make_exporter(transport, batch_size=10, flush_interval=60)
    exporter.export(completed_trace("before-close"))

    assert exporter.close(timeout=1)
    assert exporter.close(timeout=1)
    exporter.export(completed_trace("after-close"))

    assert len(transport.requests) == 1
    assert exporter.is_closed
    assert exporter.failures[-1].kind is ExportFailureKind.CLOSED


def test_concurrent_exports_are_safe_and_do_not_cross_corrupt_traces() -> None:
    transport = FakeTransport()
    exporter = make_exporter(
        transport,
        batch_size=5,
        flush_interval=60,
        max_queue_size=50,
    )
    traces = [completed_trace(str(index)) for index in range(20)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(exporter.export, traces))
    assert exporter.flush(timeout=2)
    exporter.close(timeout=1)

    delivered = [
        item["trace_id"]
        for request in transport.requests
        for item in json.loads(request.body)["traces"]
    ]
    assert sorted(delivered) == sorted(trace.trace_id for trace in traces)


async def test_async_tracing_enqueues_without_corrupting_task_context() -> None:
    transport = FakeTransport(_HttpResponse(202))
    exporter = make_exporter(transport, batch_size=2, flush_interval=60)
    tracer = Tracer(exporter=exporter)

    async def run(name: str) -> str:
        async with tracer.trace(name) as trace:
            async with trace.span("work"):
                await asyncio.sleep(0)
                assert tracer.current_trace is trace
            return trace.trace_id

    trace_ids = await asyncio.gather(run("one"), run("two"))
    assert await asyncio.to_thread(exporter.flush, 1)
    await asyncio.to_thread(exporter.close, 1)

    delivered = [item["trace_id"] for item in json.loads(transport.requests[0].body)["traces"]]
    assert sorted(delivered) == sorted(trace_ids)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"endpoint": "file:///tmp/traces"}, "http or https"),
        ({"endpoint": "https://token@example.test/traces"}, "credentials"),
        ({"endpoint": "https://example.test/traces?token=secret"}, "query"),
        ({"api_token": "bad\r\ntoken"}, "line breaks"),
        (
            {"endpoint": "http://example.test/traces", "api_token": "secret"},
            "HTTPS",
        ),
        ({"headers": {"Authorization": "override"}}, "reserved"),
        ({"headers": {"Bad Header": "value"}}, "header name"),
        ({"headers": {"X-Test": "bad\nvalue"}}, "line breaks"),
        ({"max_trace_bytes": DEFAULT_MAX_JSON_BYTES + 1}, "1 MiB"),
    ],
)
def test_configuration_rejects_unsafe_values(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        HttpTraceExporter(  # type: ignore[arg-type]
            **{"endpoint": "https://example.test/api/v1/traces", **kwargs}
        )
