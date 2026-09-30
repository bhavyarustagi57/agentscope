# Implementation Plan: AgentScope

## Overview

Build the smallest production-quality monorepo foundation that proves the web, API, database, Redis, migration, and worker boundaries without implementing future product domains.

## Architecture Decisions

- Use npm workspaces only for the web package; Python remains an independent `pyproject.toml` package.
- Keep one deployable FastAPI control plane and one Dramatiq worker process sharing the same Python package.
- Use PostgreSQL as the system of record and Redis only as the Dramatiq broker/readiness dependency.
- Add one `app_metadata` table solely to prove typed models and reversible migrations.
- Use Docker Compose and package scripts instead of adding a task runner.

## Task List

1. Establish root repository conventions and task tracking.
2. Build and test the API health/readiness contract, persistence model, migration, and worker task.
3. Build the accessible responsive web shell and pass lint/type/build checks.
4. Wire the local stack and CI, then document architecture and workflows.
5. Run every locally available verification and record environmental gaps.

## Risks and Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Windows cloud reparse points are rejected by BuildKit | Builds fail before Dockerfiles are read | Use a normal local checkout or disposable snapshot outside the synchronized folder |
| No system Python is installed | Backend checks need a runtime | Use the installed `uv` tool to provision an isolated Python runtime |
| Framework defaults change | Stale configuration | Use current official documentation and locked dependency files |

## Phase Gate

Phase 1 integration and hardening are complete. Phase 2 — Agent Tracing SDK — is the next implementation phase.

## Phase 2 — Agent Tracing SDK

1. Define the vendor-neutral trace/span schema and raw Python instrumentation API.
2. Add a future transport/export implementation and server ingestion boundary.
3. Add framework/provider adapters without coupling them to the core package.
4. Complete Phase 2 integration, compatibility, and hardening gates.

Prompt 1 established item 1. Prompt 2 adds client-side HTTP delivery, bounded retries, stable delivery
identity, bounded batching, explicit shutdown, sanitized failure observation, and concurrency tests.
It does not add the server ingestion boundary or persistence.

Prompt 3 adds an explicit optional OpenAI Chat Completions adapter for sync and async raw
function-calling loops. It uses the existing generic LLM/tool contracts, defaults to metadata-only
capture, and deliberately excludes streaming, monkey-patching, tool execution, and server work.

Prompt 4 adds an explicit optional LangGraph 1.2 adapter for local `invoke` and `ainvoke`, maps graph
steps to existing workflow/custom spans through public per-run callbacks, verifies sync/async parallel
context isolation and OpenAI/tool composition, freezes schema `"1"` client expectations, and completes
the Phase 2 packaging, security, documentation, and regression gates. Streaming and cross-invocation
interrupt aggregation remain intentional limitations.

## Scope Boundary

Phase 1 contains no trace domain behavior. Phase 2 does not include server HTTP ingestion,
persistence, exploration UI, evaluation, experiments, authentication, monitoring, or alerting.

## Phase Gate

Phase 4 — Evaluation Engine — is complete and verified. Phase 5 is next but has not started.

## Phase 3 — Trace Ingestion & Explorer

Prompt 1 adds the frozen schema-1 `POST /api/v1/traces` contract, normalized trace/span persistence,
transactional batches, canonical payload fingerprints, PostgreSQL-authoritative concurrent
deduplication, hierarchy integrity, request bounds, safe errors, and privacy-preserving outcome logs.
Prompt 2 adds bounded cursor-paginated trace summaries, typed filters, server-side aggregates, and
complete deterministic trace detail retrieval. Prompt 3 is limited to the Trace Explorer UI on these
contracts: URL-backed filters, cursor loading, deep-linked detail, hierarchy inspection, and local
demo data through real ingestion. Neither prompt introduces evaluation behavior.

Prompt 4 hardens the integrated SDK → exporter → ingestion → PostgreSQL → query → Trace Explorer
path. It adds golden semantic round-trip coverage, adversarial batch/concurrency/pagination/detail
tests, linear deep-hierarchy handling, unknown-token preservation, clean-database and restart checks,
and final documentation/CI consistency. The separate human/local audit subsequently passed and
unblocked Phase 4.

## Phase 4 — Evaluation Engine

Prompt 1 adds the evaluation domain foundation as three explicit resources: reusable definitions,
immutable run snapshots, and per-trace results. The slice uses bounded typed contracts, PostgreSQL
constraints, deterministic bounded reads, and internal-only result creation. It deliberately does not
execute evaluators, enqueue jobs, expose result writes, or add the evaluation UI.

Implementation order:

1. Define and test evaluator configuration, lifecycle, outcome, score, and pagination contracts.
2. Add the three-table migration and matching SQLAlchemy models with trace referential integrity.
3. Add definition/run HTTP creation and reads plus result reads backed by one evaluation service.
4. Verify migration upgrade/downgrade, Phase 3 regressions, static checks, and real API behavior.
5. Document the durable contracts and the Prompt 1 boundary.

Prompt 2 adds the deterministic execution core without changing the HTTP surface or introducing a
queue. A small explicit evaluator registry consumes the run snapshot and a trace-output input
contract. Synchronous execution locks one run, batch-loads requested trace outputs, persists the
complete result set, and completes the run atomically; subject-level evaluation errors remain durable
results while invalid snapshots fail the run safely.

Prompt 2 implementation order:

1. Specify evaluator/input/output semantics through focused pure tests.
2. Implement exact-match, contains, and inclusive numeric-threshold evaluators plus dispatcher.
3. Add centrally-validated lifecycle transitions and atomic multi-trace execution tests/service.
4. Strengthen material Prompt 1 validation gaps and rerun the complete regression suite.
5. Document execution, transaction, privacy, idempotency, and future worker boundaries.

Prompt 3 adds durable asynchronous orchestration. PostgreSQL stores an immutable ordered subject set
and claim generation; Redis carries only a run-ID wake-up. Submission commits before enqueue, worker
claim and finalization use separate short transactions, and bounded startup recovery re-enqueues
eligible queued work or stale running work without allowing an older claimant to finalize.

Prompt 3 implementation order:

1. Add and verify the normalized subject/claim metadata migration.
2. Add the bounded submission API and explicit commit-before-enqueue behavior.
3. Add short claim, pure evaluation, atomic finalization, retry, and stale recovery services.
4. Wire the existing Dramatiq worker with bounded retries and startup recovery.
5. Verify concurrency/crash boundaries, real async Docker processing, regressions, and docs.

Prompt 4 adds the browser-facing evaluation product surface without changing orchestration or
duplicating aggregate state. PostgreSQL derives run summaries from the normalized subjects/results
tables, existing reads gain bounded outcome filtering and trace display context, and the trace list
adds only a safe output-availability flag. The Next.js surface reuses the current shell, API parsing,
URL-state, accessibility, and responsive conventions.

Prompt 4 implementation order:

1. Specify and implement server-derived run summaries, result filtering/trace names, and safe trace
   output availability with focused query-count integration tests.
2. Add a typed evaluation API client and pure URL/form/polling helpers with behavior tests.
3. Build the evaluations landing workflow for definition creation, run creation, and trace selection.
4. Build the run detail workflow with bounded polling, aggregates, filtered paginated results, and
   safe trace navigation.
5. Verify browser workflows and responsive behavior, then complete regression, Docker, docs, and
   worktree hygiene gates.

Prompt 5 closes Phase 4 by auditing the complete deterministic evaluation path and replacing the
startup-only recovery limitation with a bounded periodic Dramatiq middleware fork. Recovery remains
PostgreSQL-authoritative: scanners reserve eligible rows inside the locking transaction before
enqueue, so concurrent worker instances are harmless and Redis remains only a wake-up transport.

Prompt 5 implementation order:

1. Re-establish API, SDK, web, migration, runtime, and Git baselines and audit Phase 4 contracts.
2. Add failing tests for periodic execution, concurrent recovery reservation, retryability, and
   configuration bounds.
3. Implement the smallest periodic recovery loop using existing Dramatiq middleware/process support.
4. Re-run adversarial orchestration/evaluator/aggregate tests and the complete quality gates.
5. Verify no-restart recovery, restart persistence, browser workflows, privacy, documentation, and
   final Phase 4 worktree hygiene.

## Phase 5 — Human-vs-LLM Judge Calibration

Prompt 1 establishes reproducible human-reference inputs without adding an LLM evaluator or
statistics. A reference set owns a bounded ordered trace selection, raw per-annotator judgments, and
one explicit authoritative label per subject. Labeling is mutable only between the explicit
`draft -> labeling -> frozen` transitions. Creating a calibration study requires a complete frozen
set and snapshots only ordered trace identities plus authoritative labels.

Prompt 1 implementation order:

1. Specify bounded contracts and focused lifecycle, annotation, snapshot, and conflict tests.
2. Add normalized reference-set, subject, annotation, study, and study-subject persistence.
3. Implement one transaction-focused service and bounded typed HTTP routes.
4. Verify concurrency, migration reversibility, Phase 4 regressions, and real Docker behavior.
5. Document the calibration boundary, privacy/reproducibility guarantees, and deferred Prompt 2 work.

Prompt 2 adds immutable OpenAI judge configurations and run snapshots, structured-output execution,
per-subject durable results, and lease-fenced asynchronous processing. OpenAI SDK retries stay disabled;
the PostgreSQL run attempt is the single bounded retry budget, while periodic recovery reclaims only
expired leases or abandoned queued work. The existing deterministic evaluation path is unchanged.

Prompt 2 implementation order:

1. Specify provider request/response, configuration, result, lease, and recovery behavior in tests.
2. Add the normalized configuration/run/result schema and reversible migration 0007.
3. Implement the narrow OpenAI structured-output adapter and human-data-isolated work loader.
4. Add create/read/list/submit APIs, one-subject-at-a-time durable execution, and fenced recovery.
5. Verify migrations, regressions, Docker runtime, security, documentation, and repository hygiene.

Prompt 3 adds one reproducible statistical analysis per completed judge run. A pure dependency-free
calculator defines the confusion matrix, derived metrics, and Cohen's kappa; a separate persistence
service requires exact study/result population alignment and exposes bounded disagreement identities.

Prompt 3 implementation order:

1. Fix statistical and undefined-value semantics with pure failing tests.
2. Add the constrained canonical-analysis table and reversible migration 0008.
3. Add idempotent analysis creation/read APIs and paginated FP/FN drill-down.
4. Verify incomplete populations, concurrency, privacy, and persistence invariants.
5. Run full migration, API, SDK, web, Docker, data-preservation, and documentation gates.

Prompt 4 productizes the existing calibration contracts without changing their statistical or
persistence semantics. One responsive `/calibration` workspace guides reference-set labeling,
study/configuration/run creation, while a run detail page polls only active work and renders the
canonical server analysis, operational failures, disagreements, and descriptive run comparison.

Prompt 4 implementation order:

1. Mirror the existing calibration contracts in one bounded frontend API module and pure tested UI
   helpers; keep all canonical statistics server-authored.
2. Add accessible reference-set lifecycle, raw-annotation/adjudication, study, configuration, and
   judge-run creation workflows to the existing application shell.
3. Add active-only run polling, progress and failure separation, analysis creation/reporting, and
   deterministic disagreement paging with trace navigation.
4. Add side-by-side same/different-study comparison with explicit population warnings and no rank,
   trust score, or significance claim.
5. Run frontend, browser, runtime/data-preservation, documentation, and repository hygiene gates.

Prompt 5 closes Phase 5 through audit and proof rather than another feature. The existing contracts,
schema, and product workflow remain unchanged unless a demonstrated defect requires repair.

Prompt 5 verification order:

1. Audit human-truth separation, immutable snapshots, provider isolation, lease fencing, statistics,
   bounded queries, and frontend/backend contracts.
2. Add one deterministic fake-provider integration test that crosses the actual API, services,
   persistence, analysis, and disagreement boundaries without making a paid provider call.
3. Run focused and full API gates plus static analysis and migration drift checks.
4. Reuse the accepted unchanged SDK/frontend gates, then smoke-test the composed runtime and browser.
5. Confirm worker recovery health, preserved 28-trace/98-span evidence, documentation, and Git hygiene.

## Phase 7 Prompt 1 — Regression Detection Foundation

Phase 7 Prompt 1 adds synchronous, immutable regression-policy checks derived only from the persisted
Phase 6 canonical analysis. A check snapshots policy parameters, explicit baseline/candidate
orientation, frozen variant provenance, and frozen condition evidence. It classifies each condition
by practical pass-rate drop and minimum sample size, then applies conservative overall semantics.
It does not execute Git operations or claim causal attribution.

Implementation order:

1. Add failing domain/API tests for policy validation, orientation, threshold boundaries,
   insufficient evidence, snapshots, transaction completeness, and pagination.
2. Add reversible migration `0012` and matching SQLAlchemy records for policies, checks, and
   per-condition findings with bounded constraints and historical references.
3. Add typed schemas, deterministic classification logic, transactional services, and versioned
   create/list/detail APIs using the existing Phase 6 analysis rows without recomputation.
4. Document the policy semantics and non-causal boundary; run focused Phase 7, Phase 6, full API,
   migration, Ruff, and strict touched-module checks.

Risks and mitigations:

- Floating-point threshold edges: derive the effect from persisted Phase 6 rates and use inclusive
  `<= -threshold` semantics covered at an exact representable boundary.
- Partial evidence: create the check and all findings in one database transaction and constrain
  finding identity by check plus condition position.
- Historical drift: store JSONB provenance/config snapshots and scalar policy/evidence values on the
  Phase 7 records; keep source foreign keys restrictive rather than cascading historical evidence.

## Phase 7 Prompt 2 — Immutable Bisection Planning

Prompt 2 converts a detected regression check with frozen `git_commit_sha` provenance into a
synchronous, immutable investigation plan. A narrow Git CLI adapter resolves full commits, validates
the known-good ancestor relationship, and freezes the bounded ancestry-path commit universe without
checking out or executing any commit.

Implementation order:

1. Add failing adapter and API tests using isolated temporary Git repositories, including ancestry,
   ordering, immutability, range limits, timeouts, and atomic failure behavior.
2. Add migration `20260924_0013` and matching session/commit records with historical references,
   SHA, ordering, status, JSON, and range constraints.
3. Add the bounded read-only Git adapter, typed schemas, transactional planning service, and
   create/list/detail API.
4. Document the non-causal planning boundary and run Prompt 2, Prompt 1, Phase 6, full API, migration,
   Ruff, formatting, and strict typing gates.

Risks and mitigations:

- Untrusted paths/revisions: canonicalize the path and pass revisions only as individual argv values
  with `shell=False`, timeouts, and bounded output.
- Moving branches: resolve and persist full SHAs plus commit metadata once; never regenerate a plan.
- Merge-heavy ranges: persist Git topological ancestry order and every parent SHA; defer narrowing.
- Partial plans: inspect before writing, then persist the READY parent and all commit rows in one
  transaction.

## Phase 7 Prompt 3 — Durable Commit Execution

Prompt 3 adds an explicit, durable probe runner for selected commits from an immutable bisection
session. It reuses the Phase 6 queue, lease, heartbeat, fencing, and recovery conventions while
executing only inside detached worktrees beneath an AgentScope-owned configured root. It does not
select midpoints or infer a culprit.

Architecture decisions:

- A run snapshots one strict argv probe configuration and the Prompt 2 repository identity. It is
  created `pending`, then a single submit request atomically freezes 1–50 selected planned commits
  and moves it to `queued` before broker delivery.
- Durable target rows track requested commit progress and current lease state. Separate attempt rows
  retain every attempt's bounded output and failure evidence rather than overwriting history.
- Exit `0` is `pass`, `1` is `regression`, `2` and all other completed exit codes are
  `indeterminate`; setup, worktree, spawn, and timeout failures are infrastructure failures and only
  become terminal `execution_failed` after three attempts.
- The worker creates a detached worktree under a native temporary default or
  `BISECTION_WORK_ROOT`, passes a minimal allowlisted environment, uses `shell=False`, and cleans up
  only a derived path carrying matching AgentScope ownership evidence.

Implementation order:

1. Add failing contract and isolated-worktree tests for validation, commit membership, argv
   semantics, exit mapping, output bounds, containment, repository safety, and cleanup behavior.
2. Add migration `20260924_0014`, execution records, typed contracts, create/list/detail/submit APIs,
   and active-run uniqueness.
3. Add the worktree/probe adapter and worker orchestration with per-target leases, heartbeats,
   attempt history, retries, fencing, resumable progress, and Dramatiq delivery.
4. Extend periodic recovery, document the trust boundary, and run Prompt 3 plus all compatibility,
   migration, lint, format, and type gates.

Risks and mitigations:

- Host command execution: accept only typed executable/argv fields, never a shell string, forward
  only safe process essentials, bound time/output, and document that this is trusted local code—not
  a hostile-code sandbox.
- Filesystem cleanup: derive paths solely from persisted UUID/SHA/token values, resolve beneath the
  configured root, require matching ownership markers, and use Git worktree removal plus nonrecursive
  empty-directory removal only.
- Worker interruption: persist each completed attempt/result before selecting the next target;
  expired leases are reclaimed and stale lease tokens cannot write.
- Repository state: all checkout activity occurs in detached worktrees; tests assert target HEAD,
  branch, and porcelain status remain unchanged.

## Phase 7 Prompt 4 — Persisted Automated Bisection

Prompt 4 adds a durable coordinator above Prompt 3. It freezes one probe configuration, confirms the
frozen candidate through Prompt 3, then narrows the immutable Prompt 2 positions with midpoint
selection. The baseline is the Phase 7 known-good boundary at position `-1`; the candidate is the
last frozen planned position and must produce probe REGRESSION before narrowing.

Architecture decisions:

- Persist analyses separately from Prompt 3 execution runs so the coordinator can survive restarts
  without duplicating worktree or subprocess logic.
- Use `floor((good_position + bad_position) / 2)`. If that position is unusable, choose the nearest
  unobserved position in the open interval, breaking equal-distance ties toward the lower position.
- Persist every consumed observation as an append-only step. PASS advances the good boundary;
  REGRESSION retreats the bad boundary; INDETERMINATE and EXECUTION_FAILED leave boundaries unchanged
  and exclude that position from future selection.
- Reuse only terminal PASS/REGRESSION targets from the same session and byte-for-byte equivalent
  versioned probe configuration. Prior unusable outcomes never become domain evidence.
- Detect conflicting outcomes for one commit and any observed earlier REGRESSION/later PASS pair.
  Such evidence is inconclusive rather than forcibly attributed.
- Use short lease-owned analysis iterations. A worker persists the next action, requests or observes
  one Prompt 3 run, then exits or re-enqueues; it never executes a probe itself or holds a database
  transaction during Prompt 3 work.
- Allow historical analyses but enforce one active analysis per session in PostgreSQL.

Implementation order:

1. Prove the pure midpoint, nearest-skip, boundary-update, and contradiction rules with failing unit
   tests.
2. Add migration `0015`, analysis/step models, immutable response contracts, and database constraints.
3. Add create/start/list/detail services and APIs with durable intent-before-enqueue.
4. Add the lease-fenced state-machine worker and exact Prompt 3 evidence/request integration.
5. Extend periodic recovery and prove duplicate delivery, stale leases, broker failure, and resume.
6. Add deterministic end-to-end scenarios for attribution, indeterminate gaps, failures, incompatible
   configuration, non-monotonic evidence, history, and repository safety.
7. Document semantics and run focused, compatibility, full-backend, static, and migration gates.

Risks and mitigations:

- Prompt 2 excludes the baseline from planned execution targets: treat it as the frozen semantic
  known-good boundary and explicitly confirm the candidate under the probe before midpoint narrowing.
- Indeterminate gaps can destroy logarithmic guarantees: bound selection to the finite frozen set and
  persist every unusable position so no loop can repeat it.
- Concurrent orchestration and execution delivery can duplicate requests: use the active-run database
  constraints, stored waiting execution identity, row locks, and lease-fenced writes.
- Non-monotonic histories can fool classic bisection: inspect all exact-compatible persisted binary
  evidence for contradictions before every attribution.

## Phase 7 Prompt 5 — Regression and Bisection Product Workflow

Prompt 5 exposes the accepted Phase 7 evidence chain without adding browser-side statistics or a new
backend domain. One `Regressions` navigation entry leads from completed experiment runs to policy and
check creation, canonical finding inspection, immutable Git planning, frozen argv probe configuration,
automated analysis progress, execution evidence, and the persisted boundary timeline.

Design contract:

- Reuse the current App Router, shell, controls, five-second active polling, status language, and
  server-error conventions; do not introduce another state or component framework.
- Use native labeled inputs and selects. Probe arguments are newline-delimited argv values and are
  sent as an array; there is no shell-command field or client-side concatenation.
- Render server statistics, classifications, steps, boundary snapshots, and execution outcomes
  verbatim through formatting-only helpers. Preserve null as unavailable rather than zero.
- Use one-column mobile layouts that expand at existing breakpoints; wrap full SHAs and paths, while
  showing short SHAs with an accessible full value.
- Poll only queued/running/waiting analyses every five seconds and stop on attributed,
  inconclusive, or failed.
- Keep the attribution statement explicitly non-causal and treat inconclusive as a first-class
  scientific result.

Implementation order:

1. Add failing frontend contract tests, typed Phase 7 API parsing, validation, formatting, and polling
   helpers.
2. Add navigation, the regression workspace, completed-run entry action, and check-detail/planning
   flow; verify focused frontend tests.
3. Add bisection session, probe, analysis, progress, timeline, terminal attribution, inconclusive,
   failure, and bounded execution-evidence views; verify focused frontend tests and build.
4. Add a deterministic Phase 6-to-Phase 7 backend integration test using a temporary repository and
   the accepted Prompt 3/4 workers; assert repository non-mutation.
5. Update current-state documentation, perform responsive/accessibility browser verification, and run
   every Phase 7/frontend/backend/static gate with Alembic remaining at `20260924_0015`.

## Phase 8 Prompt 1 — Production Monitoring Foundation

Prompt 1 adds reusable monitoring definitions and immutable aggregate snapshots for deterministic,
fully closed UTC windows. PostgreSQL remains authoritative; Redis carries snapshot UUIDs only. Trace
scope is deliberately limited to persisted indexed trace name/status fields, and optional quality
metrics read terminal results from one selected deterministic evaluation definition. This prompt does
not compare windows or detect drift.

Implementation order:

1. Specify aligned half-open window, aggregate, definition, and API contracts with failing tests.
2. Add migration `20260925_0016` plus definition/snapshot models and database invariants.
3. Implement definition reads/writes and idempotent closed-window snapshot intent APIs.
4. Implement bounded aggregation, lease-fenced materialization, recovery, and closed-window discovery.
5. Document semantics and run Phase 8, trace/evaluation, Phase 7, full backend, static, and migration
   gates.

## Phase 8 Prompt 2 — Baseline-vs-Current Drift Detection

Prompt 2 adds immutable synchronous comparisons between two completed Prompt 1 snapshots. Reusable
create/read policies own ordered, explicit metric rules; every comparison freezes those rules and
persists one finding per rule atomically. Practical thresholds remain authoritative, while valid
rate comparisons may include descriptive two-proportion statistics. Missing values, undersized
samples, and undefined relative changes remain insufficient evidence. This prompt adds no incidents,
alerts, scheduled checks, or frontend behavior.

Implementation order:

1. Add failing pure contract/classifier tests for supported metrics, sample mappings, thresholds,
   zero baselines, rate statistics, and conservative overall classification.
2. Add migration `20260925_0017` plus policy, ordered-rule, comparison, and finding records with
   restrictive historical references and bounded database constraints.
3. Add typed create/list/detail policy and comparison APIs backed by one atomic synchronous service.
4. Add integration tests for snapshot eligibility, frozen rule history, ordered findings,
   pagination, and transaction completeness.
5. Document the descriptive, non-causal boundary and run focused, compatibility, full backend,
   static, formatting, and migration gates.

Risks and mitigations:

- Relative comparisons at a zero baseline are undefined: persist null relative evidence and classify
  insufficient rather than substituting an epsilon.
- Rate p-values can be mistaken for the decision rule: calculate them only as nullable descriptive
  evidence after sample/count validation; practical thresholds alone determine drift.
- Historical policy changes can rewrite interpretation: keep policies create/read-only and freeze
  all rule values plus snapshot values/counts into each comparison finding.
- Partial comparisons can appear canonical: insert the comparison and complete ordered finding set
  in one transaction.

## Phase 8 Prompt 3 — Automatic Drift Incidents

Prompt 3 links enabled monitors and drift policies into durable previous-window checks. PostgreSQL
stores check intent before Redis delivery, workers use bounded lease-fenced attempts, and the existing
Prompt 2 comparison engine remains the only drift classifier. Canonical comparisons, incident state,
and append-only incident events are committed together.

Implementation order:

1. Add failing contract, orchestration, incident-lifecycle, cooldown, recovery, and API tests.
2. Add reversible migration `20260925_0018` plus constrained configuration, check, incident, and
   incident-event records.
3. Reuse the Prompt 2 comparison transaction core from one automatic-check orchestration service.
4. Extend the existing Dramatiq monitoring actor/recovery fork with bounded discovery and re-enqueue.
5. Document semantics and run Prompt 3–1, Phase 7, full backend, static, and migration gates.

Risks and mitigations:

- Missing or gapped windows: require the exact adjacent UTC window and persist a skip reason.
- Duplicate/stale workers: fence the coherent comparison/incident/event transaction by check lease.
- Alert noise: suppress only repeated `drift_reoccurred` events during cooldown; always retain
  occurrence counts, open/resolution events, and suppression evidence on the check.

## Phase 8 Prompt 4 — Monitoring Product Completion

Prompt 4 exposes the accepted monitoring, drift, automatic-check, and incident contracts through one
responsive Monitoring product area. The browser renders only canonical server evidence, polls only
queued/running snapshots and checks, and links to existing trace/evaluation routes without adding raw
data APIs. A dependency-free `/metrics` endpoint reports only bounded lifecycle/classification counts.

Implementation order:

1. Add failing frontend contract tests and typed monitoring API/UI helpers for validation, nullable
   evidence, status language, polling, links, ordering, and responsive source requirements.
2. Add the Monitoring navigation entry and `/monitoring` workspace for definition creation/toggling,
   drift-policy creation, and filterable incident discovery.
3. Add `/monitoring/[monitorId]` for canonical snapshots, explicit materialization, manual drift
   comparisons, automatic configuration, automatic checks, and active-only polling.
4. Add `/monitoring/incidents/[incidentId]` for acknowledgement, canonical incident evidence, and the
   append-only event timeline with links back to checks, comparisons, and windows.
5. Add bounded low-cardinality `/metrics`, deterministic Phase 8 lifecycle integration coverage,
   complete documentation, browser/accessibility review, and all frontend/backend/security gates.

Risks and mitigations:

- Client-side reinterpretation of evidence: validate API shapes but never recompute aggregates,
  findings, classifications, occurrence counts, or resolution state.
- Polling load: schedule one five-second refresh only while a loaded snapshot or check is active.
- Metrics disclosure/cardinality: emit fixed metric names and fixed enum labels only; never IDs,
  payloads, paths, prompts, outputs, or secrets.
- Wide evidence on narrow screens: use the existing overflow/card patterns, wrapping IDs and keeping
  every control labeled and keyboard reachable.

## Phase 9 Prompt 1 — Unified Product Information Architecture

Prompt 1 unifies the existing Phase 1–8 product surfaces without changing backend contracts. The
smallest useful slice groups navigation by operator intent, replaces the foundation placeholder at
`/` with a bounded evidence overview, and adds reusable page context and relationship links.

Implementation order:

1. Add failing frontend contract tests for grouped navigation, phase-independent labels, overview
   source isolation, shared context components, status language, and required cross-links.
2. Group the existing routes into Overview, Observe, Evaluate, and Investigate while preserving all
   deep links, active-page semantics, keyboard access, and responsive behavior.
3. Build `/` from six parallel first-page reads of existing APIs with independent loading, empty,
   and failure states; report factual counts only and do not poll or synthesize a health score.
4. Add the minimal reusable page-header, breadcrumb, and status primitives and apply them to the
   highest-value top-level and investigation paths.
5. Verify focused/full frontend tests, lint, typecheck, build, production audit, responsive runtime,
   documentation, migration head, and worktree hygiene.

Risks and mitigations:

- Overview fan-out: issue exactly one bounded read per product area and isolate failures with
  `Promise.allSettled` so one unavailable service cannot erase the other evidence.
- Navigation churn: preserve every existing route and active label; change only presentation and
  grouping.
- False confidence: show availability and persisted evidence counts, never a derived trust or health
  score.

## Phase 9 Prompt 3 — Deterministic Local Demo

Prompt 3 adds one local, repeatable sample workspace across the existing evidence lifecycles, plus an
optional owned temporary Git-repository demonstration. It does not add an API endpoint, reset path,
dependency, schema migration, provider call, Prompt 4 work, or Phase 10 behavior.

Architecture decisions:

- Reuse the current HTTP ingestion/API contracts and worker service entry points; do not insert
  convenient terminal rows for evaluation, calibration, experiments, regression, or drift.
- Give the normal workspace fixed trace identities, fixed UTC timestamps, and visible
  `AgentScope Demo` names/metadata. Find existing major entities by those unique names and reuse the
  completed canonical evidence on rerun.
- Keep bisection separate because it creates and executes Git history. Build only beneath a newly
  created tool-owned temporary root, pass argv without a shell, verify the expected commit boundary,
  and leave the AgentScope checkout untouched.
- Keep demo loading CLI-only. The empty Overview prints the exact supported local command instead of
  exposing an unauthenticated mutation endpoint.

Implementation order:

1. Add failing backend tests for complete seeding, deterministic provenance/timestamps, lifecycle
   evidence, no paid provider, collision safety, and idempotent reruns.
2. Implement the smallest standard-library seed command around existing trace builders, public API
   contracts, and worker services; print created/reused counts plus useful routes and IDs.
3. Add and test the separate owned-temp-repository bisection command with exact frozen SHAs,
   deterministic probe outcomes, expected previous-good/first-regressed boundaries, and checkout
   non-mutation proof.
4. Add focused frontend tests and the Overview-only first-run steps, exact CLI instruction, and
   restrained demo identification using existing visible names/provenance.
5. Document `Local Demo`, execute both commands against disposable local state, and run focused/full
   backend and frontend, static, build, audit, migration-head, security, and hygiene gates.

Risks and mitigations:

- Partial first run: every scenario first discovers its visible demo natural key and resumes only
  through accepted lifecycle transitions; canonical analysis creation is already idempotent.
- Existing user data: no truncation or reset is provided, and all lookup/reuse is scoped to exact
  demo names or fixed demo trace identities.
- Queue availability: explicit service processing completes the already-submitted durable intent
  locally, so Redis delivery timing is not a correctness dependency.
- Git safety: the optional flow rejects non-owned paths and never targets the active checkout or a
  remote repository.

## Phase 9 Prompt 4 — Final Product UX Hardening

Prompt 4 closes Phase 9 by hardening the existing product surface for keyboard use, assistive
technology, small screens, resilient loading and error states, and consistent evidence-first copy.
It preserves the current information architecture, routes, backend contracts, dependencies, and
visual language.

Implementation order:

1. Add focused failing source contracts for focus restoration, loading semantics, form errors,
   table structure, route-level headings, copy controls, responsive overflow, and product language.
2. Repair shared interaction and state primitives first, then apply the smallest route-specific
   accessibility fixes to their existing callers.
3. Harden monitoring forms and evidence tables, first-run command copying, long identifiers, and
   remaining phase-labelled copy without redesigning the workflows.
4. Verify keyboard and responsive behavior in a real browser where the local runtime permits it.
5. Run the focused and complete frontend gates, migration-head checks, full backend regression suite,
   security review, documentation consistency, and repository hygiene audit.

Risks and mitigations:

- Static contracts can miss runtime interaction defects: exercise representative routes and keyboard
  flows in the available browser runtime and report any environment limitation explicitly.
- Broad visual cleanup can destabilize accepted workflows: reuse existing components and CSS, make
  only evidence-backed repairs, and add no dependency or backend/schema change.
- Accessibility fixes can create focus traps or duplicate announcements: restore focus only to the
  originating control, use native semantics, and keep live regions scoped to changing status text.
