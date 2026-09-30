from __future__ import annotations

import hashlib
import json
import random
import re
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from email.utils import parsedate_to_datetime
from enum import StrEnum
from threading import Condition, Lock, Thread
from typing import IO, Protocol
from urllib.error import HTTPError
from urllib.parse import SplitResult, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agentscope_sdk.core import DEFAULT_MAX_JSON_BYTES, Trace, TraceSerializationError

__all__ = ["ExportFailure", "ExportFailureKind", "HttpTraceExporter", "RetryPolicy"]

_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_RESERVED_HEADERS = {
    "accept",
    "authorization",
    "content-length",
    "content-type",
    "host",
    "idempotency-key",
}
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_RESPONSE_BYTES = 65_536
_BATCH_PREFIX = b'{"schema_version":"1","traces":['
_BATCH_SUFFIX = b"]}"


class ExportFailureKind(StrEnum):
    """Stable categories for failures observed by an HTTP exporter."""

    SERIALIZATION = "serialization"
    TRANSPORT = "transport"
    HTTP = "http"
    PROTOCOL = "protocol"
    QUEUE_FULL = "queue_full"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class ExportFailure:
    """Sanitized delivery failure details suitable for callbacks and inspection."""

    kind: ExportFailureKind
    message: str
    trace_ids: tuple[str, ...]
    attempts: int = 0
    status_code: int | None = None


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential-backoff policy for transient delivery failures."""

    max_attempts: int = 3
    base_delay: float = 0.25
    max_delay: float = 5.0
    jitter: float = 0.2

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("retry delays must be non-negative")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be greater than or equal to base_delay")
        if not 0 <= self.jitter <= 1:
            raise ValueError("jitter must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class _HttpRequest:

    method: str
    url: str
    headers: dict[str, str]
    body: bytes
    timeout: float


@dataclass(frozen=True, slots=True)
class _HttpResponse:

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""


class _HttpTransport(Protocol):
    def send(self, request: _HttpRequest) -> _HttpResponse: ...


class _UrllibTransport:
    def __init__(self) -> None:
        self._opener = build_opener(_NoRedirectHandler())

    def send(self, request: _HttpRequest) -> _HttpResponse:
        raw_request = Request(
            request.url,
            data=request.body,
            headers=request.headers,
            method=request.method,
        )
        try:
            with self._opener.open(raw_request, timeout=request.timeout) as response:
                return _HttpResponse(
                    status_code=response.status,
                    headers=dict(response.headers.items()),
                    body=response.read(_MAX_RESPONSE_BYTES + 1),
                )
        except HTTPError as error:
            return _HttpResponse(
                status_code=error.code,
                headers=dict(error.headers.items()) if error.headers else {},
                body=error.read(_MAX_RESPONSE_BYTES + 1),
            )


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> Request | None:
        return None


@dataclass(frozen=True, slots=True)
class _PendingTrace:
    trace_id: str
    payload: bytes


class HttpTraceExporter:
    """Bounded background HTTP exporter with batching and transient retries."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 5.0,
        api_token: str | None = None,
        headers: Mapping[str, str] | None = None,
        retry_policy: RetryPolicy | None = None,
        batch_size: int = 10,
        flush_interval: float = 1.0,
        max_queue_size: int = 100,
        max_queue_bytes: int = 16 * DEFAULT_MAX_JSON_BYTES,
        max_trace_bytes: int = DEFAULT_MAX_JSON_BYTES,
        max_batch_bytes: int = 4 * DEFAULT_MAX_JSON_BYTES,
        close_timeout: float = 5.0,
        on_error: Callable[[ExportFailure], None] | None = None,
        _transport: _HttpTransport | None = None,
        _sleep: Callable[[float], None] = time.sleep,
        _random: Callable[[], float] = random.random,
        _now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        parsed_endpoint = _validate_endpoint(endpoint)
        if api_token is not None and parsed_endpoint.scheme != "https":
            raise ValueError("api_token requires an HTTPS endpoint")
        _require_positive("timeout", timeout)
        _require_positive_int("batch_size", batch_size)
        _require_positive("flush_interval", flush_interval)
        _require_positive_int("max_queue_size", max_queue_size)
        _require_positive_int("max_queue_bytes", max_queue_bytes)
        _require_positive_int("max_trace_bytes", max_trace_bytes)
        if max_trace_bytes > DEFAULT_MAX_JSON_BYTES:
            raise ValueError("max_trace_bytes cannot exceed the SDK 1 MiB trace boundary")
        _require_positive_int("max_batch_bytes", max_batch_bytes)
        _require_non_negative("close_timeout", close_timeout)

        self._endpoint = endpoint
        self._endpoint_host = parsed_endpoint.hostname or ""
        self._timeout = timeout
        self._headers = _build_headers(api_token, headers)
        self._retry_policy = retry_policy or RetryPolicy()
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_queue_size = max_queue_size
        self._max_queue_bytes = max_queue_bytes
        self._max_trace_bytes = max_trace_bytes
        self._max_batch_bytes = max_batch_bytes
        self._close_timeout = close_timeout
        self._on_error = on_error
        self._transport = _transport or _UrllibTransport()
        self._sleep = _sleep
        self._random = _random
        self._now = _now

        self._condition = Condition()
        self._queue: deque[_PendingTrace] = deque()
        self._queued_bytes = 0
        self._oldest_enqueued_at: float | None = None
        self._in_flight = 0
        self._flush_requested = False
        self._closing = False
        self._closed = False
        self._delivered_traces = 0
        self._failures: deque[ExportFailure] = deque(maxlen=100)
        self._failure_lock = Lock()
        self._worker = Thread(
            target=self._run,
            name="agentscope-http-exporter",
            daemon=True,
        )
        self._worker.start()

    def __repr__(self) -> str:
        return (
            f"HttpTraceExporter(endpoint_host={self._endpoint_host!r}, "
            f"batch_size={self._batch_size}, closed={self.is_closed})"
        )

    def export(self, trace: Trace) -> None:
        """Serialize and enqueue a completed trace without performing network I/O."""

        try:
            payload = trace.to_json(max_bytes=self._max_trace_bytes).encode("utf-8")
        except TraceSerializationError:
            self._record_failure(
                ExportFailure(
                    kind=ExportFailureKind.SERIALIZATION,
                    message="trace serialization failed",
                    trace_ids=(trace.trace_id,),
                )
            )
            return

        pending = _PendingTrace(trace_id=trace.trace_id, payload=payload)
        if len(_encode_batch((pending,))) > self._max_batch_bytes:
            self._record_failure(
                ExportFailure(
                    kind=ExportFailureKind.PAYLOAD_TOO_LARGE,
                    message="trace cannot fit within the configured batch size",
                    trace_ids=(trace.trace_id,),
                )
            )
            return

        failure: ExportFailure | None = None
        with self._condition:
            if self._closing or self._closed:
                failure = ExportFailure(
                    kind=ExportFailureKind.CLOSED,
                    message="exporter is closed",
                    trace_ids=(trace.trace_id,),
                )
            elif (
                len(self._queue) >= self._max_queue_size
                or self._queued_bytes + len(payload) > self._max_queue_bytes
            ):
                failure = ExportFailure(
                    kind=ExportFailureKind.QUEUE_FULL,
                    message="export queue capacity was reached",
                    trace_ids=(trace.trace_id,),
                )
            else:
                if not self._queue:
                    self._oldest_enqueued_at = time.monotonic()
                self._queue.append(pending)
                self._queued_bytes += len(payload)
                self._condition.notify_all()
        if failure is not None:
            self._record_failure(failure)

    def flush(self, timeout: float = 5.0) -> bool:
        """Request delivery of queued traces and wait at most ``timeout`` seconds."""

        _require_non_negative("timeout", timeout)
        deadline = time.monotonic() + timeout
        with self._condition:
            self._flush_requested = True
            self._condition.notify_all()
            while self._queue or self._in_flight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True

    def close(self, timeout: float | None = None) -> bool:
        """Stop accepting traces, perform a final bounded flush, and stop the worker."""

        wait_timeout = self._close_timeout if timeout is None else timeout
        _require_non_negative("timeout", wait_timeout)
        deadline = time.monotonic() + wait_timeout
        with self._condition:
            if self._closed:
                worker = self._worker
            else:
                self._closing = True
                self._flush_requested = True
                self._condition.notify_all()
                worker = self._worker

        flushed = self.flush(max(0.0, deadline - time.monotonic()))
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        worker.join(max(0.0, deadline - time.monotonic()))
        return flushed and not worker.is_alive()

    @property
    def failures(self) -> tuple[ExportFailure, ...]:
        with self._failure_lock:
            return tuple(self._failures)

    @property
    def delivered_traces(self) -> int:
        with self._condition:
            return self._delivered_traces

    @property
    def pending_traces(self) -> int:
        with self._condition:
            return len(self._queue) + self._in_flight

    @property
    def is_closed(self) -> bool:
        with self._condition:
            return self._closed

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._ready_to_send():
                    if self._closed and not self._queue and not self._in_flight:
                        return
                    wait_time = self._next_wait()
                    self._condition.wait(wait_time)
                batch = self._pop_batch()
                self._in_flight = len(batch)

            try:
                failure = self._deliver(batch)
            except Exception as error:
                failure = ExportFailure(
                    kind=ExportFailureKind.TRANSPORT,
                    message=f"unexpected exporter failure ({type(error).__name__})",
                    trace_ids=tuple(item.trace_id for item in batch),
                )

            with self._condition:
                self._in_flight = 0
                if failure is None:
                    self._delivered_traces += len(batch)
                if not self._queue:
                    self._flush_requested = False
                    self._oldest_enqueued_at = None
                self._condition.notify_all()
            if failure is not None:
                self._record_failure(failure)

    def _ready_to_send(self) -> bool:
        if not self._queue:
            return False
        if self._closing or self._closed or self._flush_requested:
            return True
        if len(self._queue) >= self._batch_size:
            return True
        oldest = self._oldest_enqueued_at
        return oldest is not None and time.monotonic() - oldest >= self._flush_interval

    def _next_wait(self) -> float | None:
        if not self._queue or self._oldest_enqueued_at is None:
            return None
        elapsed = time.monotonic() - self._oldest_enqueued_at
        return max(0.0, self._flush_interval - elapsed)

    def _pop_batch(self) -> tuple[_PendingTrace, ...]:
        batch: list[_PendingTrace] = []
        payload_size = len(_BATCH_PREFIX) + len(_BATCH_SUFFIX)
        while self._queue and len(batch) < self._batch_size:
            pending = self._queue[0]
            candidate_size = payload_size + len(pending.payload) + int(bool(batch))
            if batch and candidate_size > self._max_batch_bytes:
                break
            self._queue.popleft()
            self._queued_bytes -= len(pending.payload)
            batch.append(pending)
            payload_size = candidate_size
        self._oldest_enqueued_at = time.monotonic() if self._queue else None
        return tuple(batch)

    def _deliver(self, batch: tuple[_PendingTrace, ...]) -> ExportFailure | None:
        trace_ids = tuple(item.trace_id for item in batch)
        body = _encode_batch(batch)
        headers = dict(self._headers)
        headers["Idempotency-Key"] = _delivery_key(trace_ids)
        request = _HttpRequest(
            method="POST",
            url=self._endpoint,
            headers=headers,
            body=body,
            timeout=self._timeout,
        )

        for attempt in range(1, self._retry_policy.max_attempts + 1):
            try:
                response = self._transport.send(request)
            except (ConnectionError, OSError, TimeoutError) as error:
                if attempt < self._retry_policy.max_attempts:
                    self._sleep(self._retry_delay(attempt, None))
                    continue
                return ExportFailure(
                    kind=ExportFailureKind.TRANSPORT,
                    message=f"HTTP transport failed ({type(error).__name__})",
                    trace_ids=trace_ids,
                    attempts=attempt,
                )

            if 200 <= response.status_code < 300:
                if _valid_success_body(response.body):
                    return None
                return ExportFailure(
                    kind=ExportFailureKind.PROTOCOL,
                    message="successful response body was not an empty body or JSON object",
                    trace_ids=trace_ids,
                    attempts=attempt,
                    status_code=response.status_code,
                )

            if (
                response.status_code in _RETRYABLE_STATUSES
                and attempt < self._retry_policy.max_attempts
            ):
                retry_after = _retry_after(response.headers, self._now())
                self._sleep(self._retry_delay(attempt, retry_after))
                continue

            return ExportFailure(
                kind=ExportFailureKind.HTTP,
                message="HTTP endpoint rejected the trace batch",
                trace_ids=trace_ids,
                attempts=attempt,
                status_code=response.status_code,
            )
        raise AssertionError("bounded retry loop exited unexpectedly")

    def _retry_delay(self, failed_attempt: int, retry_after: float | None) -> float:
        policy = self._retry_policy
        if retry_after is not None:
            return min(policy.max_delay, max(0.0, retry_after))
        base = min(policy.max_delay, policy.base_delay * (2 ** (failed_attempt - 1)))
        return float(min(policy.max_delay, base + base * policy.jitter * self._random()))

    def _record_failure(self, failure: ExportFailure) -> None:
        with self._failure_lock:
            self._failures.append(failure)
        if self._on_error is not None:
            try:
                self._on_error(failure)
            except Exception:
                pass


def _validate_endpoint(endpoint: str) -> SplitResult:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint must be an absolute http or https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint must not contain credentials")
    if parsed.query:
        raise ValueError("endpoint must not contain a query string")
    if parsed.fragment:
        raise ValueError("endpoint must not contain a fragment")
    return parsed


def _build_headers(api_token: str | None, headers: Mapping[str, str] | None) -> dict[str, str]:
    result = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_token is not None:
        _validate_header_value(api_token)
        if not api_token:
            raise ValueError("api_token must not be empty")
        result["Authorization"] = f"Bearer {api_token}"

    seen = {name.casefold() for name in result}
    for name, value in (headers or {}).items():
        lowered = name.casefold()
        if not _HEADER_NAME.fullmatch(name):
            raise ValueError(f"invalid HTTP header name: {name!r}")
        if lowered in _RESERVED_HEADERS or lowered in seen:
            raise ValueError(f"custom header {name!r} is reserved")
        _validate_header_value(value)
        result[name] = value
        seen.add(lowered)
    return result


def _validate_header_value(value: str) -> None:
    if not isinstance(value, str):
        raise ValueError("HTTP header values must be strings")
    if "\r" in value or "\n" in value:
        raise ValueError("HTTP header values must not contain line breaks")


def _require_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_non_negative(name: str, value: float) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be non-negative")


def _require_positive_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _encode_batch(batch: tuple[_PendingTrace, ...]) -> bytes:
    return _BATCH_PREFIX + b",".join(item.payload for item in batch) + _BATCH_SUFFIX


def _delivery_key(trace_ids: tuple[str, ...]) -> str:
    if len(trace_ids) == 1:
        return trace_ids[0]
    digest = hashlib.sha256("\n".join(trace_ids).encode("utf-8")).hexdigest()
    return f"batch_{digest}"


def _valid_success_body(body: bytes) -> bool:
    if not body:
        return True
    if len(body) > _MAX_RESPONSE_BYTES:
        return False
    try:
        return isinstance(json.loads(body), dict)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False


def _retry_after(headers: Mapping[str, str], now: datetime) -> float | None:
    value = next(
        (value for name, value in headers.items() if name.casefold() == "retry-after"),
        None,
    )
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                return None
            return max(0.0, (parsed.astimezone(UTC) - now.astimezone(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None
