# Evaluation Domain

AgentScope exposes deterministic evaluators and durable asynchronous execution contracts as a
browser product. PostgreSQL remains the source of truth; Redis carries only lightweight run-ID
wake-up messages.

## Domain model

- An **evaluation definition** is reusable configuration: name, optional description, evaluator kind,
  validated kind-specific configuration, enabled state, and timestamps.
- An **evaluation run** is one attempt to execute a definition. At creation it snapshots the
  definition ID, name, evaluator kind, and normalized configuration so later definition changes do
  not reinterpret historical work.
- An **evaluation result** is one outcome for one existing trace in one run. It stores only the trace
  identity, outcome, optional normalized score, and bounded structured details; trace/span payloads
  remain canonical in the trace tables.

Definitions, runs, and results use server-created UUIDs. A run may contain at most one result for a
given trace. Definition deletion is restricted while runs reference it, run deletion cascades its
results, and trace deletion is restricted while evaluation evidence references it.

## Evaluator kinds and configuration

The accepted kinds are deliberately small and explicit:

| Kind | Configuration |
| --- | --- |
| `exact_match` | `expected` JSON value and optional `case_sensitive` boolean |
| `contains` | non-empty `substring` up to 4,000 characters and optional `case_sensitive` boolean |
| `numeric_threshold` | finite `minimum`, `maximum`, or both; minimum cannot exceed maximum |

Unknown kinds and unknown configuration fields are rejected. Configuration is normalized before it
is persisted and is limited to 32 KiB, 16 levels, and 2,000 JSON nodes. Secrets and API keys do not
belong in these objects.

## Execution architecture and input contract

An evaluator consumes an `EvaluationInput` and returns an `EvaluationDecision`. The input carries a
trace ID for association, the candidate value, and whether that value was captured. The decision
contains `passed`, `failed`, or `error`, an optional score, and bounded non-sensitive details. A
closed dispatcher maps the three enum kinds to their evaluator and validated configuration class; it
performs no imports or user-directed code loading.

The candidate is the trace's top-level `output` JSON value. Execution batch-loads only trace IDs and
outputs in one query; it does not load spans. The current persistence contract cannot distinguish an
explicit JSON null output from an uncaptured output, so either is treated as not captured. It is not
coerced to an empty string or another value. Such a subject produces an `error` result with no score
and `candidate_not_captured`; result details never copy the candidate.

Evaluator semantics are deterministic:

- `exact_match` compares strings exactly, or with Unicode `casefold()` when case-insensitive.
  Whitespace is significant. Other finite JSON values compare by canonical JSON structure and type;
  values are not stringified.
- `contains` performs a literal substring check on string candidates, optionally applying Unicode
  case folding. Non-string candidates are subject errors.
- `numeric_threshold` accepts finite JSON numbers except booleans. Minimum and maximum bounds are
  inclusive; numeric strings and non-finite numbers are subject errors.

A successful judgment scores `1.0`; an unsuccessful judgment scores `0.0`. A subject-level input
problem is `error` with no score. It does not fail other subjects or the run.

## Lifecycle and outcomes

Run states are `pending`, `queued`, `running`, `completed`, and `failed`. New runs are `pending`.
Database checks enforce the associated timestamp/error shape: pending and queued runs have not
started; running runs have a start but no completion; completed runs have ordered start/completion
timestamps and no error; failed runs have an ordered completion. The execution service is the single
owner of legal transitions: `pending -> queued|running`, `queued -> running`, and
`running -> completed|failed`. Terminal states cannot be restarted.

Result outcomes are `passed`, `failed`, and `error`. `passed`/`failed` describe evaluator judgments;
`error` means the evaluator could not make a judgment. Scores are optional finite values normalized
to the inclusive `[0, 1]` range, and error outcomes cannot carry a score. Details are limited to
64 KiB, 16 levels, and 2,000 JSON nodes.

## Asynchronous orchestration

`POST /api/v1/evaluation-runs/{run_id}/execute` accepts 1–1,000 unique existing trace IDs. In one
transaction it locks the pending run, persists an ordered immutable subject set in
`evaluation_run_subjects`, and transitions the run to queued. Only after commit does the API enqueue
a Dramatiq message containing the run UUID. A successful send returns `queue_delivery: enqueued`;
failure returns `queue_delivery: deferred` while preserving the queued intent for recovery. Redis is
never the only copy of the subject set, evaluator configuration, or lifecycle state.

An identical ordered queued submission is accepted and sends another harmless wake-up. Reordered or
different subjects, a non-pending/non-queued run, or any pre-existing result is a conflict. Creating
another run is the way to request another logical execution.

The worker uses three boundaries:

1. A short claim transaction locks the queued run, increments its bounded attempt counter, sets it
   running, and commits.
2. A read session batch-loads only the immutable run snapshot plus ordered top-level trace outputs,
   then closes before evaluation begins. No row lock or database transaction is held while evaluator
   code runs.
3. A short finalization transaction verifies running status, claim generation, and exact ordered
   subject/result identity, then atomically inserts every result and marks the run completed. A stale
   worker or incomplete decision set cannot finalize after recovery.

Results are all-or-nothing for one attempt. The database uniqueness constraint remains the final
`(run_id, trace_id)` guard. Invalid snapshots and unexpected evaluator failures become safe terminal
run failures; subject input problems remain ordinary `error` results within a completed run.

The actor retries transient database failures at most three times, with 5–60 second exponential
backoff. Before retry, a successfully claimed run is returned to queued when possible. Across worker
claims, `attempt_count` is capped at four; exhaustion fails the run rather than looping forever.

The worker registers one supervised recovery process through Dramatiq middleware. It scans
immediately and then every `EVALUATION_RECOVERY_INTERVAL_SECONDS` (60 seconds by default), at most
100 eligible runs per tick. Multiple worker instances may scan concurrently. Each scan uses
`FOR UPDATE SKIP LOCKED` and records a durable enqueue reservation inside the same transaction, so
only one scanner emits a wake-up for an eligible row during the five-minute queue-recovery window.

Queued work is eligible when never sent or when its last send/reservation is at least five minutes
old. Running work is eligible after five minutes. A stale running run with no results returns to
queued; terminal, fresh-running, exhausted, and result-bearing runs are not executed again. If Redis
enqueue fails, recovery conditionally releases only its own reservation so the next periodic tick can
retry without a worker restart. Duplicate delivery remains harmless because only one database claim
can win. Attempt generation prevents a recovered run's older worker from finalizing.

Clients poll `GET /api/v1/evaluation-runs/{id}` until `completed` or `failed`, then retrieve the
persisted results. Logs contain run IDs, evaluator kind, attempt and aggregate counts only; evaluator
configuration, trace input/output, candidate values, and result details are not logged. API errors use
bounded generic messages rather than Redis, database, or evaluator exception text.

## Product workflow and summaries

Open `/evaluations` to create a kind-specific deterministic definition without writing JSON, then
create a pending run from an enabled definition. A definition is reusable configuration; a run is one
immutable attempt containing the definition name, evaluator kind, and normalized configuration as a
snapshot. A pending run lists existing traces through the canonical trace query API and exposes only identity,
timing, status, duration, and an `output_available` boolean. It never downloads candidate output for
selection. Because persisted JSON null and uncaptured output are indistinguishable, the UI uses the
accurate phrase **output unavailable**.

Submission accepts 1–1,000 selected traces. `queue_delivery: deferred` is not a failed execution: the
subjects and queued intent are durable in PostgreSQL and periodic recovery can re-enqueue the run. The
run page polls every three seconds only while status is `queued` or `running`, performs no overlapping
requests, aborts on unmount, tolerates temporary read failures, and stops on `completed` or `failed`.
Progress is persisted results over durable subjects, never an invented percentage.

Run list/detail responses derive `subject_count`, `result_count`, per-outcome counts, `scored_count`,
and `average_score` in PostgreSQL with grouped subqueries. Result errors have no score and are excluded
from the average; when `scored_count` is zero, `average_score` is null. Legacy synchronous runs without
durable subject rows report at least their persisted result count as their subject total. Summary reads
do not load result ORM collections, trace payloads, or spans and do not issue per-run queries.

Results are ordered by `(created_at, id)` descending, displayed in pages of 20, and filterable by
`passed`, `failed`, or `error` through URL query state. The API retains its 1–100 page-size and
0–100,000 offset bounds. Offset pagination is deterministic for a fixed result set; restart at offset
zero if concurrent writes require a fresh view. Each result includes a joined trace display name,
textual outcome, nullable score, bounded reason details, and a safe internal link to trace detail.
Candidate outputs and hidden server exceptions are not exposed by evaluation list/result APIs.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/evaluation-definitions` | Create a validated definition |
| `GET` | `/api/v1/evaluation-definitions` | List definitions; filter by kind/enabled state |
| `GET` | `/api/v1/evaluation-definitions/{id}` | Retrieve one definition |
| `POST` | `/api/v1/evaluation-runs` | Create a pending run and immutable snapshot |
| `POST` | `/api/v1/evaluation-runs/{id}/execute` | Persist subjects and request async execution |
| `GET` | `/api/v1/evaluation-runs` | List runs with server-derived summaries; filter by definition/status |
| `GET` | `/api/v1/evaluation-runs/{id}` | Retrieve one run with server-derived summary |
| `GET` | `/api/v1/evaluation-runs/{id}/results` | List results; optionally filter by trace or outcome |
| `GET` | `/api/v1/evaluation-results/{id}` | Retrieve one result |

Lists are ordered by `(created_at, id)` descending and use bounded `page_size` (1–100) and `offset`
(0–100,000). Offset pages are deterministic for a fixed dataset but are not snapshot-isolated from
concurrent inserts; restart traversal when a fresh consistent view is required.

## Calibration boundary

Calibration relies on immutable ordered subjects and snapshots but keeps remote judge runs
separate from deterministic evaluation runs. Remote calls use explicit timeouts, disabled SDK
retries, per-subject commits, and renewable attempt-owned leases rather than this deterministic
evaluator's five-minute stale threshold. See [calibration.md](calibration.md).

Authentication, tenancy, remote alert delivery, and externally managed monitoring remain outside the
current local product boundary.
