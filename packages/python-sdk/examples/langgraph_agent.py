"""Offline LangGraph + OpenAI-compatible + tool tracing composition."""

from types import SimpleNamespace
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from agentscope_sdk import InMemoryTraceExporter, OpenAIInstrumentation, SpanKind, Tracer
from agentscope_sdk.adapters.langgraph import LangGraphCapture, LangGraphInstrumentation


class AgentState(TypedDict):
    messages: list[dict[str, object]]
    weather: NotRequired[dict[str, int]]
    answer: NotRequired[str]


class FakeCompletions:
    """Deterministic stand-in for ``client.chat.completions``."""

    def __init__(self) -> None:
        self._responses = [
            self._response(None, finish_reason="tool_calls"),
            self._response("It is 21 C in Paris."),
        ]

    @staticmethod
    def _response(content: str | None, *, finish_reason: str = "stop") -> SimpleNamespace:
        return SimpleNamespace(
            id="chatcmpl_offline_graph",
            model="gpt-offline-demo",
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(role="assistant", content=content, tool_calls=None),
                )
            ],
            usage=SimpleNamespace(prompt_tokens=8, completion_tokens=5, total_tokens=13),
        )

    def create(self, **kwargs: object) -> SimpleNamespace:
        return self._responses.pop(0)


exporter = InMemoryTraceExporter()
tracer = Tracer(exporter=exporter)
openai = OpenAIInstrumentation(tracer)
langgraph = LangGraphInstrumentation(tracer, capture=LangGraphCapture.ALL)
client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))


def planner(state: AgentState) -> AgentState:
    openai.chat_completions(client, model="gpt-offline-demo", messages=state["messages"])
    return {
        "messages": [
            *state["messages"],
            {"role": "assistant", "tool_call": {"name": "get_weather", "city": "Paris"}},
        ]
    }


def weather_tool(state: AgentState) -> AgentState:
    with tracer.span("get_weather", kind=SpanKind.TOOL, input={"city": "Paris"}) as span:
        weather = {"temperature_c": 21}
        span.set_output(weather)
    return {
        "messages": [*state["messages"], {"role": "tool", "content": weather}],
        "weather": weather,
    }


def answer(state: AgentState) -> AgentState:
    response = openai.chat_completions(
        client,
        model="gpt-offline-demo",
        messages=state["messages"],
    )
    text = response.choices[0].message.content
    return {"messages": state["messages"], "weather": state["weather"], "answer": text}


builder = StateGraph(AgentState)
builder.add_node("planner", planner)
builder.add_node("weather_tool", weather_tool)
builder.add_node("answer", answer)
builder.add_edge(START, "planner")
builder.add_edge("planner", "weather_tool")
builder.add_edge("weather_tool", "answer")
builder.add_edge("answer", END)

langgraph.invoke(
    builder.compile(),
    {"messages": [{"role": "user", "content": "What is the weather in Paris?"}]},
    name="offline-weather-graph",
)

print(exporter.traces[0].to_json(indent=2))
