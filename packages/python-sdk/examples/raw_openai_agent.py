"""Offline OpenAI-compatible function-calling loop with AgentScope tracing."""

from types import SimpleNamespace

from agentscope_sdk import (
    InMemoryTraceExporter,
    OpenAICapture,
    OpenAIInstrumentation,
    SpanKind,
    Tracer,
)


def response(
    content: str | None,
    *,
    tool_calls: list[SimpleNamespace] | None = None,
    finish_reason: str = "stop",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="chatcmpl_offline_demo",
        model="gpt-offline-demo",
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls,
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=8, completion_tokens=5, total_tokens=13),
    )


class FakeCompletions:
    """Deterministic stand-in for ``client.chat.completions``."""

    def __init__(self) -> None:
        tool_call = SimpleNamespace(
            id="call_weather",
            type="function",
            function=SimpleNamespace(name="get_weather", arguments='{"city":"Paris"}'),
        )
        self._responses = [
            response(None, tool_calls=[tool_call], finish_reason="tool_calls"),
            response("It is 21 C in Paris."),
        ]

    def create(self, **kwargs: object) -> SimpleNamespace:
        return self._responses.pop(0)


exporter = InMemoryTraceExporter()
tracer = Tracer(exporter=exporter)
instrumentation = OpenAIInstrumentation(tracer, capture=OpenAICapture.ALL)
client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
messages: list[object] = [{"role": "user", "content": "What is the weather in Paris?"}]

with tracer.trace("offline-weather-agent") as trace:
    with trace.span("agent-loop", kind=SpanKind.AGENT):
        first = instrumentation.chat_completions(
            client,
            model="gpt-offline-demo",
            messages=messages,
        )
        messages.append(first.choices[0].message)

        with trace.span(
            "get_weather",
            kind=SpanKind.TOOL,
            input={"city": "Paris"},
        ) as tool:
            tool_result = {"temperature_c": 21}
            tool.set_output(tool_result)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": "call_weather",
                "content": tool_result,
            }
        )

        final = instrumentation.chat_completions(
            client,
            model="gpt-offline-demo",
            messages=messages,
        )
        trace.set_output({"answer": final.choices[0].message.content})

print(exporter.traces[0].to_json(indent=2))
