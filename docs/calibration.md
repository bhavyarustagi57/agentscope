# Human Reference and Calibration Foundation

AgentScope calibrates LLM judges against an explicit human reference standard. Durable OpenAI-backed
execution and canonical agreement statistics remain separate from any claim that a human label is
metaphysical ground truth.

## Domain

A **human reference set** owns one bounded, ordered selection of 1–500 existing traces. Membership is
defined once while the set is `draft`; the API rejects a different repeated definition. A set moves
only `draft -> labeling -> frozen`. Labeling cannot begin with zero subjects, and freezing requires an
authoritative reference label for every subject.

A **human annotation** is one raw `passed` or `failed` judgment per set, trace, and annotator ID.
Different annotators may disagree. The same annotator may relabel while the set is `labeling`; the row
identity remains stable. Optional rationales are plain text limited to 4,000 characters. The current
source is explicitly `manual`.

An **authoritative reference label** is separately adjudicated on the subject. It is never inferred by
majority vote. Its label, bounded rationale, adjudicator attribution, and timestamp are stored on the
reference subject. Missing remains `null`; it is never coerced to `failed`, and operational errors are
not valid human classes.

A **calibration study** can be created only from a nonempty, complete, frozen reference set. Creation
copies only each ordered trace ID and authoritative `passed`/`failed` label into study subjects. It
does not copy trace inputs, outputs, spans, or rationales. Repeating creation deliberately produces a
new study identity. The initial study status is `draft`; a validated judge configuration and execution
can then be attached without changing the captured human inputs.

## Reproducibility and concurrency

Every lifecycle-sensitive write locks the reference-set row in PostgreSQL. Uniqueness constraints
enforce one subject position, one subject membership, and one annotation per annotator/subject.
Freeze therefore serializes against annotations and adjudication. Frozen memberships, raw
annotations, and reference labels cannot be changed through the API. Study snapshots remain stable
even if unrelated records are added elsewhere.

Trace foreign keys use `RESTRICT` for reference and study subjects, so evidence cannot be deleted
while it supports calibration. Child rows cascade only when their owning set or study is deliberately
removed; no deletion endpoint is exposed.

## API and bounds

- `POST/GET /api/v1/human-reference-sets` and `GET /{id}` create and inspect sets.
- `POST/GET /api/v1/human-reference-sets/{id}/subjects` defines and pages ordered subjects.
- `POST /api/v1/human-reference-sets/{id}/begin-labeling` starts labeling.
- `POST/GET /api/v1/human-reference-sets/{id}/annotations` upserts and pages raw judgments.
- `GET /api/v1/human-annotations/{id}` reads one annotation.
- `POST /api/v1/human-reference-sets/{id}/subjects/{trace_id}/reference-label` adjudicates.
- `POST /api/v1/human-reference-sets/{id}/freeze` finalizes complete human inputs.
- `POST/GET /api/v1/calibration-studies`, `GET /{id}`, and `GET /{id}/subjects` create and inspect
  stable study snapshots.

All list pages are limited to 100 items and offsets to 100,000. Set summaries derive subject,
annotation, authoritative reference, unlabeled, passed, and failed counts in PostgreSQL; clients do
not infer completion from a page. Subject lists join only lightweight trace identity/name metadata.

## Identity, privacy, and current scope

Annotator IDs are bounded local attribution metadata, not authenticated identities. Production use
still requires authentication, authorization, tenancy, audit policy, and retention controls. APIs and
logs do not duplicate or emit trace payloads as calibration metadata, and rationales are stored and
returned as plain text with no HTML interpretation contract.

## OpenAI judge execution

A **judge configuration** is an immutable API resource containing `provider=openai`, a bounded model
ID and rubric, structured-output schema version `1`, an explicit 5–300 second timeout, a bounded
output-token limit, and configuration version `1`. Temperature is intentionally omitted because it
is not accepted consistently by every Responses API model. Creating a **judge run** snapshots these
fields, so later configurations cannot change an existing run.

The worker uses the OpenAI Responses API structured-output parser. It sends only the snapshotted
rubric as instructions and the top-level trace output as delimited candidate JSON. It does not load
or send reference labels, raw annotations, human rationales, trace inputs, spans, trace metadata,
errors, or credentials. Candidate output is limited to 64 KiB at this boundary, tools are disabled,
provider-side storage is disabled, and SDK retries are disabled.

Each subject produces exactly one durable result containing either a `passed`/`failed` decision plus
a bounded rationale, or an explicit safe execution-error category. Successful results may include
the provider request ID, token counts, latency, provider, model, and durable attempt number. Candidate
content and raw provider exceptions are never copied into results or logs.

## Leases, progress, and recovery

Redis carries only a run UUID. PostgreSQL claims a queued run with a random lease token, durable
attempt number, heartbeat timestamp, and six-minute expiration. A separate heartbeat session renews
the lease every 20 seconds. Every result write and terminal transition verifies the run ID, attempt,
lease token, and ownership; a recovered older worker therefore cannot write. Results commit one
subject at a time, so a retry resumes only subjects without a result.

OpenAI transport retries are disabled to avoid multiplying retry layers. Timeout, rate-limit, and
availability failures return the run to `queued` while the three-attempt durable budget remains;
retry wake-ups use bounded 5-second then 10-second delays.
Authentication and invalid configuration fail immediately. Periodic recovery uses the existing
bounded `FOR UPDATE SKIP LOCKED` scanner and only reclaims running work whose lease has expired.

With at most 500 subjects and three run attempts, completed subjects are skipped and one transient
failure ends an attempt. A completing run therefore makes at most 502 provider calls: one durable
result per subject plus at most two ambiguous/transient duplicates. Exactly-once remote calls are not
claimed. If OpenAI returns and the worker dies before committing, recovery may repeat that subject.

The crash boundaries are deliberate: a post-claim/pre-call crash is recovered after lease expiry; an
in-flight call remains owned while heartbeat succeeds; a post-response/pre-commit crash can duplicate
one call; a post-commit crash resumes at the next incomplete subject; and an expired older attempt
cannot persist or finalize after a newer lease is issued.

## Judge API

- `POST/GET /api/v1/judge-configurations` and `GET /{id}` create and inspect configuration.
- `POST/GET /api/v1/calibration-judge-runs` and `GET /{id}` create and inspect runs.
- `POST /api/v1/calibration-judge-runs/{id}/execute` durably queues a pending run.
- `GET /api/v1/calibration-judge-runs/{id}/progress` returns canonical progress counts.
- `GET /api/v1/calibration-judge-runs/{id}/results` pages decisions and execution errors.

Execution submission returns `503 OPENAI_NOT_CONFIGURED` when `OPENAI_API_KEY` is absent. Configure
the secret only in the environment; `.env.example` contains an empty placeholder. Tests inject fake
providers and make no paid calls. Later prompts add agreement/confusion statistics (including
Cohen's kappa) and the labeling/calibration UI.

Using this feature transmits the selected candidate output and rubric to OpenAI. Candidate output can
contain prompt-injection attempts; the system mitigates rather than eliminates that risk with a
strong instruction boundary, explicit candidate delimiters, minimal context, structured output, and
no tools or external actions.

## Statistical calibration analysis

A calibration analysis compares one successfully completed judge run with the immutable labels in
that run's calibration study. Human labels are the reference truth for terminology, and `passed` is
the positive class: true positive means human `passed` and judge `passed`; true negative means both
`failed`; false positive means human `failed` and judge `passed`; false negative means human `passed`
and judge `failed`. Disagreement is evidence to inspect, not an evaluation execution failure.

Analysis is rejected unless the run is `completed` and its successful decisions match the complete
study population exactly. Pending, queued, running, failed, missing, and provider-error subsets are
never analyzed. PostgreSQL stores one immutable canonical analysis per run. A run-row lock makes
duplicate and concurrent creation idempotent, while database constraints require nonnegative counts,
a complete 2x2 matrix, consistent marginal totals, and bounded finite metrics.

For sample count `N`, observed agreement is `(TP + TN) / N`. Precision for passed is
`TP / (TP + FP)`, recall is `TP / (TP + FN)`, F1 is
`2 * precision * recall / (precision + recall)`, and failed-class specificity is
`TN / (TN + FP)`. A zero denominator produces `null`, never zero, NaN, or infinity; the response also
lists each undefined metric.

Cohen's kappa separates raw agreement from agreement expected from the raters' marginal label rates:
`Pe = (human_pass/N * judge_pass/N) + (human_fail/N * judge_fail/N)` and
`kappa = (observed_agreement - Pe) / (1 - Pe)`. Kappa is `null` when `Pe == 1`, because the
denominator is zero. A numeric zero remains distinguishable from undefined through
`kappa_is_defined` and `undefined_metrics`. High raw agreement can coexist with low kappa in an
imbalanced population; these values are statistical evidence, not an automatic trust verdict.

The analysis API is:

- `POST /api/v1/calibration-judge-runs/{id}/analysis` creates or returns the canonical analysis.
- `GET /api/v1/calibration-judge-runs/{id}/analysis` reads it.
- `GET /api/v1/calibration-judge-runs/{id}/disagreements` pages deterministic false-positive and
  false-negative trace identities, with an optional `category` filter.

Drill-down queries join study labels to judge decisions and return no raw annotation, rationale, or
trace payload. Thresholds and deployment recommendations are intentionally not derived from
calibration metrics. The product provides canonical reports and descriptive run comparison without
declaring superiority.

## Calibration product workflow

The `/calibration` workspace presents the existing domain in its required order: create and label a
human reference set, freeze it, snapshot it into a study, define how an OpenAI judge evaluates a
candidate, and create a judge run. Raw annotations and authoritative labels use separate controls and
copy; the UI never infers adjudication from annotator votes. Frozen sets hide mutation controls and
are described as immutable, while the API remains the authoritative lifecycle guard.

Judge-run detail polls only `queued` and `running` runs. Valid passed/failed decisions, pending work,
and provider/execution failures remain separate. Operational failures never appear in the
disagreement explorer and are not silently removed from progress. The browser stores no provider
secret; real submission still depends on server-side `OPENAI_API_KEY` configuration.

Completed runs can create or read their one canonical server-side analysis. The browser displays the
server counts and metrics without recomputing them. Null precision, recall, F1, specificity, or kappa
renders as `Undefined`, never zero, NaN, or infinity. The confusion table explicitly uses human labels
as rows and judge decisions as columns, with `passed` as the positive class. No trust score, grade,
winner, or production-readiness claim is derived from these measurements.

The disagreement explorer uses server-side FP/FN filtering and deterministic pagination, joins the
available judge rationale from the bounded result set, and links to the existing trace detail page.
Run comparison reads two canonical analyses and shows provider/model/configuration, population
identity, sample size, metrics, FP/FN, and disagreement totals side by side. Same-study runs are
identified as sharing an immutable population; different studies produce a visible warning that the
comparison is not controlled or apples-to-apples. No significance or superiority claim is made.

Calibration measures agreement against the chosen authoritative human reference labels. It does not
prove universal correctness, eliminate human labeling error, or establish that one judge will behave
the same way on a different population or over time.

## Final integration verification

The calibration integration test exercises the real trace-ingestion, reference-set, annotation,
adjudication, freeze, study, judge-run, result, analysis, and disagreement boundaries against an
isolated PostgreSQL test database. A deterministic fake implements the existing judge-provider
protocol, so the test proves persistence and statistical behavior without a paid OpenAI call. Raw
annotations deliberately differ from authoritative labels, and assertions confirm that neither human
rationales nor labels enter provider candidates. The expected matrix contains one TP, TN, FP, and FN,
which also verifies both disagreement categories and source-trace identity end to end.
