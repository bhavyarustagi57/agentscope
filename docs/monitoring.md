# Production Monitoring

AgentScope stores reusable monitoring definitions, immutable canonical snapshots and drift
comparisons, durable automatic checks, and operational incidents with append-only events. PostgreSQL
is the source of record; Redis transports only snapshot or automatic-check UUIDs to Dramatiq workers.

## Definitions and windows

A definition selects one fixed duration (`5m`, `15m`, `1h`, `6h`, or `24h`), optional exact trace
name/status filters, and optionally one deterministic evaluation definition. Durations align to Unix
epoch UTC boundaries. A snapshot covers a half-open interval `[window_start, window_end)`, so evidence
at the start is included and evidence exactly at the end belongs to the next window. Only fully closed
windows can be materialized.

Completed snapshots are historical evidence and are not recomputed when late source rows arrive. A
database unique constraint permits only one snapshot per definition and exact window.

## Metrics

Trace metrics use persisted top-level traces: population, success/error counts and rates, duration
sample count plus mean/median/p95, and token sample count plus input/output/total and mean total tokens.
Token totals are first summed across LLM spans per trace. A token sample is a trace with captured
`total_tokens`. PostgreSQL `percentile_cont` provides deterministic interpolated median and p95 values.

When an evaluation definition is selected, monitoring reads only persisted results from completed
runs whose result timestamp is inside the window. It records passed, failed, and evaluator-error
counts separately; pass rate uses only valid passed/failed results, while error rate uses all terminal
results.

Missing evidence is not zero. Empty windows complete with zero populations, but rates and
distribution/token values without samples remain `null`. Non-finite aggregate values are never
persisted.

## Materialization and recovery

The API commits a queued snapshot intent before attempting Redis delivery. A worker claims it with a
UUID lease, increments a bounded attempt count, aggregates without retaining a row lock, and commits
results only while its lease still owns the snapshot. Duplicate messages are harmless, stale owners
cannot finalize, expired leases return to the queue, and attempt exhaustion becomes a durable failure.

The existing worker recovery fork scans immediately and periodically. Each scan discovers at most
`MONITORING_MAX_CATCHUP_WINDOWS` recent closed windows per enabled definition (default `24`), inserts
missing intents idempotently, and retries queued or expired work. Open windows and disabled
definitions are never discovered.

The same recovery fork discovers at most `MONITORING_MAX_DRIFT_CHECKS_PER_SCAN` automatic checks per
tick (default `100`). It commits each check intent before Redis delivery, re-enqueues abandoned queued
work or expired leases, and caps processing at four monotonic attempts. Duplicate delivery is
idempotent and lease fencing prevents stale attempts from creating comparisons, incidents, or events.

## API

- `POST/GET /api/v1/monitoring-definitions`
- `GET/PATCH /api/v1/monitoring-definitions/{definition_id}` (`PATCH` toggles `is_enabled` only)
- `POST /api/v1/monitoring-definitions/{definition_id}/snapshots` (explicit aligned window, or `{}`
  for the latest closed window)
- `GET /api/v1/monitoring-definitions/{definition_id}/snapshots`
- `GET /api/v1/monitoring-snapshots/{snapshot_id}`
- `POST/GET /api/v1/drift-policies`
- `GET /api/v1/drift-policies/{policy_id}`
- `POST/GET /api/v1/drift-comparisons`
- `GET /api/v1/drift-comparisons/{comparison_id}`
- `POST/GET /api/v1/automatic-drift-configurations`
- `GET/PATCH /api/v1/automatic-drift-configurations/{configuration_id}`
- `GET /api/v1/automatic-drift-checks`
- `GET /api/v1/automatic-drift-checks/{check_id}`
- `GET /api/v1/monitoring-incidents`
- `GET /api/v1/monitoring-incidents/{incident_id}`
- `POST /api/v1/monitoring-incidents/{incident_id}/acknowledge`
- `GET /api/v1/monitoring-incidents/{incident_id}/events`
- `GET /metrics` (Prometheus-compatible operational counts)

List reads are bounded and may filter snapshot status and window range. Lease tokens are internal and
never appear in API responses.

## Drift comparisons

A drift policy contains 1–20 ordered rules. Each rule names a persisted snapshot metric, explicitly
defines whether an increase or decrease is unfavorable, selects an absolute or relative practical
threshold, and requires separate minimum baseline/current sample counts. Relative change is
`(current - baseline) / abs(baseline)` and is undefined when the baseline is zero; AgentScope records
that case as insufficient evidence rather than substituting an epsilon or infinity.

Comparisons accept two distinct, completed, non-overlapping snapshots from the same monitoring
definition and window duration. The baseline must precede the current window. AgentScope snapshots
the policy identity, ordered rule semantics, metric values, sample counts, deltas, and classifications
into one atomic record; later policy or source changes cannot rewrite that historical interpretation.

A rule detects drift only when its evidence minimums are met and its unfavorable practical change
meets or exceeds the configured threshold. Missing metric values, undersized samples, and undefined
relative changes are `insufficient_evidence`, never `no_drift_detected`. Overall classification is
conservative: any detected rule wins; otherwise any insufficient rule makes the comparison
insufficient; only an entirely sufficient non-drift set is `no_drift_detected`.

Rate findings include a two-sided pooled two-proportion z statistic and p-value when defined. These
values are descriptive context only: p-values neither create nor veto drift, and AgentScope does not
invent inferential statistics for latency percentiles, mean duration, token aggregates, or volume.
Drift means only that a configured metric changed between two immutable windows by the configured
practical amount. It does not establish causality, deployment attribution, or global system health.

## Automatic drift checks

An automatic drift configuration links one monitor to one drift policy. The only supported baseline
strategy is `previous_window`: a current completed window is compared with the exact immediately
preceding aligned window from the same monitor and duration. AgentScope never skips a gap. A missing
or incomplete expected baseline produces a terminal check with a persisted reason, never a no-drift
decision. Disabled configurations are not discovered.

The automatic worker calls the same canonical comparison service used by the synchronous API. It
selects and validates the adjacent baseline while holding a fenced check attempt, then commits the
canonical comparison, incident transition, append-only event, and terminal check state in one short
transaction. Existing matching comparisons are linked instead of recreated.

## Incidents and internal events

An incident is an operational grouping of persisted drift evidence for one automatic configuration;
it does not prove a causal root cause. Only `drift_detected` opens an incident. Later drift updates the
same open or acknowledged incident, retains its original opening time, resets its clean count, and
increments its occurrence count. Acknowledgement records that an operator saw the incident;
acknowledgement is not resolution and acknowledged incidents remain active.

`no_drift_detected` increments the active incident's consecutive-clean count. The incident resolves
only after the configuration's required 1–20 clean windows. Insufficient evidence, skipped checks,
and failed checks do not count as clean. Resolution records its comparison and leaves the incident
historical; later drift opens a new incident rather than reopening the resolved row.

The append-only timeline records `incident_opened`, `drift_reoccurred`, `incident_acknowledged`, and
`incident_resolved`. Configured cooldown may suppress only repetitive `drift_reoccurred` events; the
check records that suppression while the incident occurrence still increments. Opening and
resolution events are never suppressed. These are internal persisted events only: no external
email, Slack, SMS, PagerDuty, webhook, or paid notification service is included.

## Product workflow

The browser workflow begins at `/monitoring`. It creates and toggles monitoring definitions, builds
ordered drift policies, filters incidents, and links into monitor and incident detail. Incident
monitor, configuration, and status filters are URL-backed so refresh and browser Back preserve the
operator context; invalid values are ignored safely and the page provides an explicit clear action. A monitor page
shows canonical snapshots, explicit closed-window materialization, manual comparisons, automatic
previous-window configurations, check lifecycle and suppression evidence, and related incidents.
Only queued or running snapshots/checks poll, at approximately five seconds; immutable terminal
evidence does not poll. Incident detail provides the open-only acknowledge action and the ordered
append-only evidence timeline. Acknowledgement records that the incident was seen and is not manual
resolution.

The UI displays server values directly: it does not recompute aggregates, statistics, or
classifications. Missing nullable metrics and descriptive statistics render as unavailable, never as
zero. Existing trace and evaluation routes provide evidence drill-down without a new raw-data API.

## Prometheus-compatible operations

`GET /metrics` exposes four dependency-free gauge families: snapshot and automatic-check counts by
lifecycle, incident counts by lifecycle, and comparisons by canonical classification. Labels come
only from fixed enums. The surface includes no UUIDs, trace identifiers, prompts, outputs, repository
paths, secrets, or other high-cardinality/user payload data. It can be scraped by a local Prometheus
or compatible collector; hosted Prometheus/Grafana is not required or bundled.

> Drift and incidents identify changes in persisted production evidence under configured policies.
> They do not by themselves prove causal root cause.
