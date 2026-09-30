from agentscope_sdk import (
    InMemoryTraceExporter,
    LLMAttributes,
    SpanKind,
    TokenUsage,
    Tracer,
)

exporter = InMemoryTraceExporter()
tracer = Tracer(exporter=exporter)

with tracer.trace(
    "research-agent",
    input={"question": "Why use structured traces?"},
    metadata={"environment": "example"},
    tags=("demo",),
) as trace:
    with trace.span("plan", kind=SpanKind.AGENT):
        with trace.span("draft", kind=SpanKind.LLM, llm=LLMAttributes(
            provider="example",
            model="local-demo",
            operation="chat",
            token_usage=TokenUsage(input_tokens=9, output_tokens=6),
        )) as completion:
            completion.set_output({"content": "Structured traces preserve execution evidence."})
        with trace.span("save-note", kind=SpanKind.TOOL) as tool:
            tool.set_output({"saved": True})
    trace.set_output({"answer": completion.output})

print(exporter.traces[0].to_json(indent=2))

