from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Protocol, cast

from agentscope_sdk.core import (
    JsonValue,
    LLMAttributes,
    Span,
    SpanKind,
    TokenUsage,
    ToolCall,
    Tracer,
)

__all__ = ["OpenAICapture", "OpenAIInstrumentation"]


class OpenAICapture(StrEnum):
    """Prompt/response capture policy for OpenAI calls."""

    METADATA = "metadata"
    INPUTS = "inputs"
    OUTPUTS = "outputs"
    ALL = "all"


class _SyncCreate(Protocol):
    def create(self, **kwargs: object) -> object: ...


class _AsyncCreate(Protocol):
    async def create(self, **kwargs: object) -> object: ...


class OpenAIInstrumentation:
    """Explicit tracing for modern OpenAI Chat Completions calls."""

    def __init__(
        self,
        tracer: Tracer,
        *,
        capture: OpenAICapture = OpenAICapture.METADATA,
    ) -> None:
        self._tracer = tracer
        self.capture = capture

    def chat_completions(self, client: object, **kwargs: object) -> object:
        """Call ``client.chat.completions.create`` inside an LLM span."""

        self._reject_streaming(kwargs)
        with self._span(kwargs) as span:
            self._record_input(span, kwargs)
            response = self._sync_create(client).create(**kwargs)
            self._record_response(span, response, kwargs)
            return response

    async def achat_completions(self, client: object, **kwargs: object) -> object:
        """Await ``AsyncOpenAI.chat.completions.create`` inside an LLM span."""

        self._reject_streaming(kwargs)
        async with self._span(kwargs) as span:
            self._record_input(span, kwargs)
            response = await self._async_create(client).create(**kwargs)
            self._record_response(span, response, kwargs)
            return response

    def _span(self, kwargs: Mapping[str, object]) -> Span:
        requested_model = _string(_field(kwargs, "model"))
        temperature = _number(_field(kwargs, "temperature"))
        return self._tracer.span(
            "openai.chat.completions",
            kind=SpanKind.LLM,
            llm=LLMAttributes(
                provider="OpenAI",
                model=requested_model,
                operation="chat.completions",
                temperature=temperature,
                attributes=(
                    {"openai.requested_model": requested_model}
                    if requested_model is not None
                    else {}
                ),
            ),
        )

    def _record_input(self, span: Span, kwargs: Mapping[str, object]) -> None:
        if self.capture not in {OpenAICapture.INPUTS, OpenAICapture.ALL}:
            return
        try:
            span.input = {"messages": _messages(_field(kwargs, "messages"))}
        except Exception as error:
            span.add_event(
                "instrumentation.error",
                attributes={"stage": "input_capture", "type": type(error).__name__},
            )

    def _record_response(
        self,
        span: Span,
        response: object,
        kwargs: Mapping[str, object],
    ) -> None:
        try:
            message, finish_reason = _first_message(response)
            requested_model = _string(_field(kwargs, "model"))
            response_model = _string(_field(response, "model")) or requested_model
            response_id = _string(_field(response, "id"))
            attributes: dict[str, JsonValue] = {}
            if requested_model is not None:
                attributes["openai.requested_model"] = requested_model
            if response_id is not None:
                attributes["openai.request_id"] = response_id
            span.llm = LLMAttributes(
                provider="OpenAI",
                model=response_model,
                operation="chat.completions",
                token_usage=_usage(_field(response, "usage")),
                finish_reason=finish_reason,
                tool_calls=_tool_calls(message),
                temperature=_number(_field(kwargs, "temperature")),
                attributes=attributes,
            )
            if self.capture in {OpenAICapture.OUTPUTS, OpenAICapture.ALL}:
                span.set_output({"message": _message(message)})
        except Exception as error:
            span.add_event(
                "instrumentation.error",
                attributes={"stage": "response_capture", "type": type(error).__name__},
            )

    @staticmethod
    def _reject_streaming(kwargs: Mapping[str, object]) -> None:
        if kwargs.get("stream") is True:
            raise NotImplementedError("OpenAI streaming instrumentation is not supported")

    @staticmethod
    def _sync_create(client: object) -> _SyncCreate:
        return cast(_SyncCreate, _field(_field(client, "chat"), "completions"))

    @staticmethod
    def _async_create(client: object) -> _AsyncCreate:
        return cast(_AsyncCreate, _field(_field(client, "chat"), "completions"))


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _items(value: object) -> Sequence[object]:
    return value if isinstance(value, (list, tuple)) else ()


def _messages(value: object) -> list[JsonValue]:
    return [_message(item) for item in _items(value)]


def _message(value: object) -> dict[str, JsonValue]:
    normalized: dict[str, JsonValue] = {}
    for name in ("role", "name", "tool_call_id"):
        field = _string(_field(value, name))
        if field is not None:
            normalized[name] = field
    content = _field(value, "content")
    if content is not None or _has_field(value, "content"):
        normalized["content"] = _content(content)
    calls = _tool_call_payloads(value)
    if calls:
        normalized["tool_calls"] = calls
    return normalized


def _has_field(value: object, name: str) -> bool:
    return name in value if isinstance(value, Mapping) else hasattr(value, name)


def _content(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            key: _content(item)
            for key, item in value.items()
            if isinstance(key, str)
        }
    if isinstance(value, (list, tuple)):
        return [_content(item) for item in value]
    normalized = {
        name: _content(field)
        for name in ("type", "text", "refusal", "image_url")
        if (field := _field(value, name)) is not None
    }
    return normalized or None


def _first_message(response: object) -> tuple[object, str | None]:
    choices = _items(_field(response, "choices"))
    if not choices:
        return None, None
    choice = choices[0]
    return _field(choice, "message"), _string(_field(choice, "finish_reason"))


def _tool_call_payloads(message: object) -> list[JsonValue]:
    payloads: list[JsonValue] = []
    for call in _items(_field(message, "tool_calls")):
        function = _field(call, "function")
        payload: dict[str, JsonValue] = {}
        if (call_id := _string(_field(call, "id"))) is not None:
            payload["id"] = call_id
        if (call_type := _string(_field(call, "type"))) is not None:
            payload["type"] = call_type
        payload["function"] = {
            "name": _string(_field(function, "name")),
            "arguments": _arguments(_field(function, "arguments")),
        }
        payloads.append(payload)
    return payloads


def _tool_calls(message: object) -> tuple[ToolCall, ...]:
    calls: list[ToolCall] = []
    for call in _items(_field(message, "tool_calls")):
        function = _field(call, "function")
        name = _string(_field(function, "name"))
        if name is None:
            continue
        calls.append(
            ToolCall(
                id=_string(_field(call, "id")),
                name=name,
                arguments=_arguments(_field(function, "arguments")),
            )
        )
    return tuple(calls)


def _arguments(value: object) -> JsonValue:
    if not isinstance(value, str):
        return _content(value)
    try:
        return cast(JsonValue, json.loads(value))
    except json.JSONDecodeError:
        return value


def _usage(value: object) -> TokenUsage | None:
    if value is None:
        return None
    return TokenUsage(
        input_tokens=_integer(_field(value, "prompt_tokens")),
        output_tokens=_integer(_field(value, "completion_tokens")),
        total_tokens=_integer(_field(value, "total_tokens")),
    )
