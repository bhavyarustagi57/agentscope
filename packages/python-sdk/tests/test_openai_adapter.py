import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient as AsyncHttpClient
from httpx import Client as HttpClient
from httpx import MockTransport, Request, Response
from openai import AsyncOpenAI, OpenAI

from agentscope_sdk import ExecutionStatus, InMemoryTraceExporter, SpanKind, Tracer
from agentscope_sdk.adapters.openai import OpenAICapture, OpenAIInstrumentation


@dataclass
class FakeFunction:
    name: str
    arguments: str


@dataclass
class FakeToolCall:
    id: str
    function: FakeFunction
    type: str = "function"


@dataclass
class FakeMessage:
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[FakeToolCall] | None = None


class ExplodingRepr:
    def __repr__(self) -> str:
        raise AssertionError("provider object repr must not be used")


class BrokenMessage:
    @property
    def role(self) -> str:
        raise ValueError("cannot normalize message")


def completion(
    *,
    content: str | None = "done",
    tool_calls: list[FakeToolCall] | None = None,
    model: str = "gpt-returned",
    finish_reason: str = "stop",
    marker: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id="chatcmpl_test",
        model=model,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=FakeMessage(content=content, tool_calls=tool_calls),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=5, total_tokens=13),
        marker=marker,
    )


class SyncCompletions:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)

    def create(self, **kwargs: object) -> object:
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class AsyncCompletions(SyncCompletions):
    async def create(self, **kwargs: object) -> object:
        await asyncio.sleep(0)
        return super().create(**kwargs)


def client(completions: object) -> SimpleNamespace:
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_adapter_import_does_not_require_openai_package() -> None:
    instrumentation = OpenAIInstrumentation(Tracer())

    assert instrumentation.capture is OpenAICapture.METADATA


def test_selected_openai_sdk_client_surface_works_without_network() -> None:
    def respond(request: Request) -> Response:
        assert request.url.host == "openai.example.test"
        return Response(
            200,
            json={
                "id": "chatcmpl_sdk_test",
                "object": "chat.completion",
                "created": 1_789_484_400,
                "model": "gpt-returned",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "offline"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
        )

    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer)
    openai_client = OpenAI(
        api_key="sk-test-not-a-real-key",
        base_url="https://openai.example.test/v1",
        http_client=HttpClient(transport=MockTransport(respond)),
    )

    with tracer.trace("real-sdk-shape") as trace:
        response = instrumentation.chat_completions(
            openai_client,
            model="gpt-requested",
            messages=[{"role": "user", "content": "hello"}],
        )
    openai_client.close()

    assert response.id == "chatcmpl_sdk_test"
    assert trace.spans[0].llm is not None
    assert trace.spans[0].llm.model == "gpt-returned"
    assert trace.spans[0].llm.token_usage is not None
    assert trace.spans[0].llm.token_usage.total_tokens == 4


async def test_selected_async_openai_sdk_client_surface_works_without_network() -> None:
    async def respond(request: Request) -> Response:
        assert request.url.host == "openai.example.test"
        return Response(
            200,
            json={
                "id": "chatcmpl_async_sdk_test",
                "object": "chat.completion",
                "created": 1_789_484_400,
                "model": "gpt-async-returned",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "offline"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            },
        )

    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer)
    openai_client = AsyncOpenAI(
        api_key="sk-test-not-a-real-key",
        base_url="https://openai.example.test/v1",
        http_client=AsyncHttpClient(transport=MockTransport(respond)),
    )

    async with tracer.trace("real-async-sdk-shape") as trace:
        response = await instrumentation.achat_completions(
            openai_client,
            model="gpt-async-requested",
            messages=[{"role": "user", "content": "hello"}],
        )
    await openai_client.close()

    assert response.id == "chatcmpl_async_sdk_test"
    assert trace.spans[0].llm is not None
    assert trace.spans[0].llm.model == "gpt-async-returned"
    assert trace.spans[0].llm.token_usage is not None
    assert trace.spans[0].llm.token_usage.total_tokens == 3


def test_sync_completion_creates_typed_llm_span_under_active_agent() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = OpenAIInstrumentation(tracer)

    with tracer.trace("agent") as trace:
        with trace.span("workflow", kind=SpanKind.AGENT) as agent:
            response = instrumentation.chat_completions(
                client(SyncCompletions(completion(model="gpt-returned"))),
                model="gpt-requested",
                messages=[{"role": "user", "content": "hello"}],
                temperature=0.2,
            )

    assert response.model == "gpt-returned"
    llm_span = trace.spans[1]
    assert llm_span.kind is SpanKind.LLM
    assert llm_span.parent_span_id == agent.span_id
    assert llm_span.llm is not None
    assert llm_span.llm.provider == "OpenAI"
    assert llm_span.llm.model == "gpt-returned"
    assert llm_span.llm.operation == "chat.completions"
    assert llm_span.llm.temperature == 0.2
    assert llm_span.llm.finish_reason == "stop"
    assert llm_span.llm.token_usage is not None
    assert llm_span.llm.token_usage.total_tokens == 13
    assert llm_span.llm.attributes == {
        "openai.request_id": "chatcmpl_test",
        "openai.requested_model": "gpt-requested",
    }
    assert llm_span.input is None
    assert llm_span.output is None


def test_capture_policy_normalizes_structured_messages_and_outputs() -> None:
    call = FakeToolCall("call_1", FakeFunction("get_weather", '{"city":"Paris"}'))
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer, capture=OpenAICapture.ALL)
    messages: list[object] = [
        {"role": "user", "content": "weather?"},
        FakeMessage(content=None, tool_calls=[call]),
        {"role": "tool", "tool_call_id": "call_1", "content": '{"temp":21}'},
    ]

    with tracer.trace("agent") as trace:
        first_response = completion(
            content=None,
            tool_calls=[call],
            finish_reason="tool_calls",
        )
        response = instrumentation.chat_completions(
            client(SyncCompletions(first_response)),
            model="gpt-test",
            messages=messages,
        )

    span = trace.spans[0]
    assert response.id == "chatcmpl_test"
    assert span.input == {
        "messages": [
            {"role": "user", "content": "weather?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": {"city": "Paris"},
                        },
                    }
                ],
            },
            {"role": "tool", "content": '{"temp":21}', "tool_call_id": "call_1"},
        ]
    }
    assert span.output == {
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": {"city": "Paris"},
                    },
                }
            ],
        }
    }
    assert span.llm is not None
    assert span.llm.tool_calls[0].id == "call_1"
    assert span.llm.tool_calls[0].name == "get_weather"
    assert span.llm.tool_calls[0].arguments == {"city": "Paris"}


@pytest.mark.parametrize(
    ("capture", "has_input", "has_output"),
    [
        (OpenAICapture.METADATA, False, False),
        (OpenAICapture.INPUTS, True, False),
        (OpenAICapture.OUTPUTS, False, True),
        (OpenAICapture.ALL, True, True),
    ],
)
def test_capture_policy_is_explicit(
    capture: OpenAICapture, has_input: bool, has_output: bool
) -> None:
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer, capture=capture)

    with tracer.trace("capture") as trace:
        instrumentation.chat_completions(
            client(SyncCompletions(completion())),
            model="gpt-test",
            messages=[{"role": "user", "content": "private"}],
        )

    assert (trace.spans[0].input is not None) is has_input
    assert (trace.spans[0].output is not None) is has_output


async def test_async_completion_and_concurrent_contexts_are_isolated() -> None:
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = OpenAIInstrumentation(tracer)

    async def run(name: str) -> None:
        async with tracer.trace(name) as trace:
            async with trace.span("agent", kind=SpanKind.AGENT) as agent:
                await instrumentation.achat_completions(
                    client(AsyncCompletions(completion(content=name))),
                    model="gpt-test",
                    messages=[{"role": "user", "content": name}],
                )
                assert trace.spans[1].parent_span_id == agent.span_id
                assert tracer.current_span is agent

    await asyncio.gather(run("one"), run("two"))

    assert {trace.name for trace in exporter.traces} == {"one", "two"}
    assert all(len(trace.spans) == 2 for trace in exporter.traces)
    assert tracer.current_trace is None
    assert tracer.current_span is None


@pytest.mark.parametrize("is_async", [False, True])
async def test_provider_exception_marks_span_error_propagates_and_restores_context(
    is_async: bool,
) -> None:
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer)
    provider_error = RuntimeError("provider unavailable")
    fake_client = client(
        AsyncCompletions(provider_error) if is_async else SyncCompletions(provider_error)
    )

    with tracer.trace("failure") as trace:
        with trace.span("agent", kind=SpanKind.AGENT) as agent:
            with pytest.raises(RuntimeError) as raised:
                if is_async:
                    await instrumentation.achat_completions(
                        fake_client, model="gpt-test", messages=[]
                    )
                else:
                    instrumentation.chat_completions(
                        fake_client, model="gpt-test", messages=[]
                    )
            assert raised.value is provider_error
            assert tracer.current_span is agent

    failed = trace.spans[1]
    assert failed.status is ExecutionStatus.ERROR
    assert failed.error is not None and failed.error.type == "RuntimeError"


def test_streaming_is_rejected_before_calling_provider() -> None:
    completions = SyncCompletions(completion())
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer)

    with tracer.trace("streaming") as trace:
        with pytest.raises(NotImplementedError, match="streaming"):
            instrumentation.chat_completions(
                client(completions), model="gpt-test", messages=[], stream=True
            )

    assert completions.responses
    assert trace.spans == []


def test_client_credentials_and_arbitrary_provider_objects_are_never_captured() -> None:
    fake_client = client(SyncCompletions(completion(marker=ExplodingRepr())))
    fake_client.api_key = "sk-test-not-a-real-key"
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer, capture=OpenAICapture.ALL)

    with tracer.trace("safe") as trace:
        instrumentation.chat_completions(
            fake_client,
            model="gpt-test",
            messages=[{"role": "user", "content": "hello"}],
        )

    payload = trace.to_json()
    assert "sk-test-not-a-real-key" not in payload
    assert "ExplodingRepr" not in payload


def test_input_capture_failure_does_not_prevent_provider_call() -> None:
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer, capture=OpenAICapture.INPUTS)

    with tracer.trace("capture-failure") as trace:
        response = instrumentation.chat_completions(
            client(SyncCompletions(completion())),
            model="gpt-test",
            messages=[BrokenMessage()],
        )

    assert response.id == "chatcmpl_test"
    assert trace.spans[0].input is None
    assert trace.spans[0].events[0].name == "instrumentation.error"
    assert trace.spans[0].events[0].attributes == {
        "stage": "input_capture",
        "type": "ValueError",
    }


def test_response_normalization_failure_is_observable_and_returns_response() -> None:
    bad_usage = completion()
    bad_usage.usage.total_tokens = 99
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer)

    with tracer.trace("response-failure") as trace:
        response = instrumentation.chat_completions(
            client(SyncCompletions(bad_usage)),
            model="gpt-test",
            messages=[],
        )

    assert response is bad_usage
    assert trace.spans[0].events[0].name == "instrumentation.error"
    assert trace.spans[0].events[0].attributes == {
        "stage": "response_capture",
        "type": "ValueError",
    }


def test_captured_payload_still_obeys_trace_size_limit() -> None:
    tracer = Tracer()
    instrumentation = OpenAIInstrumentation(tracer, capture=OpenAICapture.INPUTS)

    with tracer.trace("bounded") as trace:
        instrumentation.chat_completions(
            client(SyncCompletions(completion())),
            model="gpt-test",
            messages=[{"role": "user", "content": "x" * 1_000}],
        )

    with pytest.raises(ValueError, match="maximum size"):
        trace.to_json(max_bytes=500)


def test_deterministic_two_llm_one_tool_agent_loop_exports_complete_hierarchy() -> None:
    tool_call = FakeToolCall("call_weather", FakeFunction("get_weather", '{"city":"Paris"}'))
    exporter = InMemoryTraceExporter()
    tracer = Tracer(exporter=exporter)
    instrumentation = OpenAIInstrumentation(tracer, capture=OpenAICapture.ALL)
    fake_client = client(
        SyncCompletions(
            completion(content=None, tool_calls=[tool_call], finish_reason="tool_calls"),
            completion(content="It is 21 C in Paris."),
        )
    )
    messages: list[Any] = [{"role": "user", "content": "Weather in Paris?"}]

    with tracer.trace("weather-agent") as trace:
        with trace.span("agent-loop", kind=SpanKind.AGENT) as agent:
            first = instrumentation.chat_completions(
                fake_client, model="gpt-test", messages=messages
            )
            messages.append(first.choices[0].message)
            with trace.span(
                "get_weather",
                kind=SpanKind.TOOL,
                input={"city": "Paris"},
            ) as tool:
                result = {"temperature_c": 21}
                tool.set_output(result)
            messages.append(
                {"role": "tool", "tool_call_id": "call_weather", "content": result}
            )
            final = instrumentation.chat_completions(
                fake_client, model="gpt-test", messages=messages
            )
            trace.set_output({"answer": final.choices[0].message.content})

    assert exporter.traces == (trace,)
    assert [span.kind for span in trace.spans] == [
        SpanKind.AGENT,
        SpanKind.LLM,
        SpanKind.TOOL,
        SpanKind.LLM,
    ]
    assert all(span.parent_span_id == agent.span_id for span in trace.spans[1:])
    assert trace.spans[1].llm is not None
    assert trace.spans[1].llm.tool_calls[0].name == "get_weather"
    assert trace.output == {"answer": "It is 21 C in Paris."}
    assert trace.to_json()
