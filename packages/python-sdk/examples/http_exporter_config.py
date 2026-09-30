import os

from agentscope_sdk import HttpTraceExporter, RetryPolicy, Tracer

exporter = HttpTraceExporter(
    endpoint=os.environ.get(
        "AGENTSCOPE_ENDPOINT",
        "https://agentscope.example.test/api/v1/traces",
    ),
    api_token=os.environ.get("AGENTSCOPE_API_TOKEN"),
    retry_policy=RetryPolicy(max_attempts=3),
    batch_size=10,
)
tracer = Tracer(exporter=exporter)

# Instrument with `tracer` in the application. No trace is created here because the
# server-side ingestion endpoint is not part of this repository yet.
print(exporter)
exporter.close()

