# AgentScope Tasks

## Foundation

- [x] Root Git/config/environment/workspace conventions are present.
- [x] Backend package exposes tested `/health` and dependency-aware `/ready` endpoints.
- [x] SQLAlchemy model and reversible Alembic migration are present.
- [x] Dramatiq broker and demonstration job are runnable.

## Application and Infrastructure

- [x] Next.js application shell is accessible, responsive, linted, typed, and buildable.
- [x] Docker Compose wires PostgreSQL, Redis, API, worker, and web with health checks.
- [x] CI runs backend and frontend quality gates.

## Documentation and Verification

- [x] README and architecture documentation cover current workflows and boundaries.
- [x] Real PostgreSQL, Redis, API, worker, web, migration, readiness-failure, and browser integration checks pass.
- [x] Final security, reproducibility, CI, and documentation hardening is complete.

## Phase 2 — Agent Tracing SDK

- [x] Define typed trace, span, error, event, and optional LLM contracts.
- [x] Implement sync/async lifecycle handling and context-isolated automatic nesting.
- [x] Implement bounded JSON serialization and round-trip restoration.
- [x] Define the exporter protocol and in-memory exporter.
- [x] Add independent packaging, quality gates, documentation, and a runnable example.
- [x] Add bounded HTTP delivery, retries, batching, delivery identity, and failure observation.
- [x] Verify transport concurrency, async compatibility, secret safety, and close/flush lifecycle.
- [x] Add explicit sync/async OpenAI Chat Completions instrumentation and capture policy.
- [x] Verify offline function-calling hierarchy, normalization, optional packaging, and secret safety.
- [x] Add explicit sync/async LangGraph instrumentation with conservative capture and trace ownership.
- [x] Verify LangGraph concurrency, subgraphs, OpenAI/tool composition, packaging, and final hardening.
- [x] Complete Phase 2 integration and hardening in Prompt 4.

## Phase 3 — Trace Ingestion & Explorer

- [x] Implement the frozen schema `"1"` `POST /api/v1/traces` ingestion contract.
- [x] Persist trace/span records transactionally with hierarchy integrity and PostgreSQL deduplication.
- [x] Add trace list/query and detail APIs for the Trace Explorer.
- [x] Build the Trace Explorer UI without adding evaluation behavior.
- [x] Complete Phase 3 contract, hierarchy, token, batch, concurrency, pagination, migration, Docker,
      browser, security, CI, and documentation hardening.
- [x] Pass the separate mandatory human/local Phase 3 audit before beginning Phase 4.

## Phase 4 — Evaluation Engine

- [x] Define bounded evaluator configuration, run lifecycle, result outcome, and score contracts.
- [x] Persist evaluation definitions, immutable run snapshots, and trace-linked results via Alembic.
- [x] Add bounded create/read/list APIs for definitions and runs and read APIs for results.
- [x] Verify migration integrity, Phase 3 regression safety, API behavior, and static quality gates.
- [x] Document Prompt 1 domain semantics, API, schema, security decisions, and deferred execution work.
- [x] Define and test the trace-output evaluation input and typed evaluator result contracts.
- [x] Implement exact-match, contains, and inclusive numeric-threshold evaluation semantics.
- [x] Execute pending/queued runs atomically against batch-loaded traces using run snapshots.
- [x] Enforce centralized lifecycle, repeated-execution, duplicate-result, and failure behavior.
- [x] Strengthen material Prompt 1 validation gaps and pass repository-wide verification.
- [x] Document Prompt 2 execution semantics and deferred Dramatiq orchestration.
- [x] Persist immutable ordered run subjects and bounded claim/recovery metadata.
- [x] Add a validated public execution-submission operation with commit-before-enqueue semantics.
- [x] Implement short worker claim and atomic token-guarded finalization transactions.
- [x] Add bounded Dramatiq retries, duplicate-message safety, and startup recovery.
- [x] Verify async execution, crash boundaries, migration reversibility, and all regressions.
- [x] Document Prompt 3 orchestration, recovery, privacy, and deferred work.
- [x] Add server-derived run summaries, result outcome filtering, and safe trace selection metadata.
- [x] Add typed evaluation frontend contracts and tested URL/form/polling helpers.
- [x] Build definition, run creation, trace selection, and async submission workflows.
- [x] Build run status, aggregate, filtered result, and trace-navigation workflows.
- [x] Verify accessibility, responsive browser workflows, Docker integration, and all regressions.
- [x] Document Prompt 4 product workflow, aggregate semantics, polling, privacy, and deferred work.
- [x] Audit the complete Phase 4 domain, evaluator, orchestration, aggregate, API, and UI contracts.
- [x] Add bounded PostgreSQL-authoritative periodic recovery without a new dependency.
- [x] Verify concurrent scans, enqueue failure, stale/fresh work, fencing, and attempt exhaustion.
- [x] Run full API, SDK, web, migration, dependency, Docker, restart, and browser verification.
- [x] Document the final Phase 4 architecture and explicit slow-judge requirements for Phase 5.

## Phase 5 — Human-vs-LLM Judge Calibration

- [x] Persist bounded ordered human reference sets, subjects, raw annotations, and authoritative labels.
- [x] Enforce draft, labeling, and frozen lifecycle rules transactionally.
- [x] Snapshot complete frozen reference labels into first-class calibration studies.
- [x] Add bounded typed APIs, server-derived summaries, deterministic pagination, and race coverage.
- [x] Verify migration integrity, regressions, Docker runtime, data preservation, and documentation.
- [x] Persist immutable OpenAI judge configurations, run snapshots, and per-subject results.
- [x] Execute structured judgments without exposing human labels, annotations, traces, or metadata.
- [x] Add lease ownership, heartbeats, bounded durable attempts, and expired-work recovery.
- [x] Add bounded configuration/run/result APIs and identifier-only Dramatiq messages.
- [x] Verify migration, orchestration, provider, regression, Docker, security, and documentation gates.
- [x] Implement dependency-free confusion-matrix, derived-metric, and Cohen's-kappa semantics.
- [x] Persist one constrained and versioned canonical calibration analysis per completed judge run.
- [x] Add idempotent analysis APIs and bounded deterministic FP/FN disagreement drill-down.
- [x] Verify incomplete populations, undefined metrics, concurrency, privacy, and DB invariants.
- [x] Verify migration 0008, all regressions, Docker runtime, data preservation, and documentation.
- [x] Add the discoverable calibration workspace and reference-set labeling lifecycle UX.
- [x] Add immutable study, judge-configuration, and judge-run creation workflows.
- [x] Add active-only run polling, provider-failure separation, and canonical calibration reports.
- [x] Add paginated disagreement inspection, trace navigation, and descriptive run comparison.
- [x] Verify Prompt 4 frontend, browser, runtime, data-preservation, docs, and Git hygiene gates.
- [x] Audit Phase 5 human-label, judge-isolation, recovery, statistics, and cross-layer invariants.
- [x] Add a deterministic fake-provider integration test for the complete calibration workflow.
- [x] Verify Phase 5 API, migration, runtime, worker, browser, and data-preservation gates.

## Phase 6 — Experiments and A/B Testing

- [x] Persist exactly two variants, bounded provenance, ordered paired traces, and frozen conditions.
- [x] Add durable N × 2 × M execution, recovery, raw results, and historical runs.
- [x] Add canonical per-condition paired statistics and changed-subject inspection.
- [x] Build the experiment creation, configuration, READY, execution, and history workflow.
- [x] Add paginated raw evidence, canonical reports, ineligible states, and A/B trace navigation.
- [x] Add a deterministic complete-workflow integration test and final Phase 6 verification gates.

## Phase 7 — Regression Detection + Git Bisection

- [x] Add regression-policy validation and bounded create/list/get contracts.
- [x] Persist immutable regression checks and complete per-condition evidence snapshots in migration 0012.
- [x] Implement explicit A/B orientation, inclusive practical-drop classification, and conservative overall status.
- [x] Add create/list/detail APIs with deterministic pagination and explicit source-evidence failures.
- [x] Verify snapshot stability, null preservation, transaction completeness, Phase 6 compatibility, and migration integrity.
- [x] Document non-causal regression semantics and confirm Git bisection remains unimplemented.
- [x] Add isolated failing tests for safe Git inspection and immutable bisection planning.
- [x] Persist READY bisection sessions and ordered commit snapshots in migration 0013.
- [x] Add bounded read-only Git resolution, ancestry, range, and metadata inspection.
- [x] Add typed create/list/detail bisection-session APIs and explicit domain errors.
- [x] Verify frozen SHA/provenance linkage, atomic failures, no target mutation, and range limits.
- [x] Document planning semantics and run Prompt 2, Prompt 1, Phase 6, full API, and migration gates.
- [x] Define Prompt 3 execution contracts and failing API/worktree tests; verify focused tests fail for missing behavior. (Depends on Prompt 2; touches schemas/tests.)
- [x] Add migration 0014 and execution run/target/attempt records with lifecycle, lease, history, and active-run constraints; verify migration constraints. (Depends on contracts; touches migration/models.)
- [x] Implement isolated detached-worktree probe execution with containment, ownership, minimal environment, timeout, and bounded output; verify repository safety tests. (Depends on contracts; touches adapter/tests/config.)
- [x] Add run creation/submission/read APIs with frozen-commit membership and durable intent-before-enqueue; verify API tests. (Depends on persistence; touches routes/services/schemas/tests.)
- [x] Add Dramatiq processing, leases, heartbeats, fenced result writes, bounded retries, and resumable per-commit progress; verify orchestration tests. (Depends on adapter and APIs; touches worker/service/tests.)
- [x] Extend periodic recovery and broker-outage handling for bisection execution; verify recovery and stale-worker tests. (Depends on orchestration; touches recovery/service/tests.)
- [x] Document the execution/trust boundary and run Prompt 3, Prompt 2, Prompt 1, Phase 6, full API, migration, Ruff, format, mypy, and security gates. (Depends on all implementation tasks.)
- [x] Prove deterministic midpoint, nearest-skip, boundary updates, and contradiction detection with failing Prompt 4 unit tests. (Depends on Prompt 3; touches analysis tests/service.)
- [x] Add migration 0015 plus analysis/step models and typed contracts with active-run, lifecycle, and immutable-history constraints. (Depends on algorithm contract; touches migration/models/schemas.)
- [x] Add create/start/list/detail APIs with frozen configuration and durable intent-before-enqueue. (Depends on persistence; touches routes/services/tests.)
- [x] Implement lease-fenced one-step orchestration that reuses or requests Prompt 3 evidence without executing probes directly. (Depends on API and Prompt 3; touches service/worker/tests.)
- [x] Add periodic recovery and prove duplicate delivery, stale fencing, broker outage, and resumability. (Depends on orchestration; touches recovery/tests.)
- [x] Verify attribution, skip gaps, execution failures, incompatible evidence, contradictions, historical independence, and repository safety end to end. (Depends on orchestration; touches tests.)
- [x] Document Prompt 4 semantics and run Prompt 4–1, Phase 6, full backend, migration, Ruff, format, mypy, and security gates. (Depends on all implementation tasks.)
- [x] Add failing typed-client/helper tests for Phase 7 validation, null semantics, argv parsing, polling, outcomes, and routes.
- [x] Build the Regressions navigation, policy/check workspace, Phase 6 entry action, canonical check detail, and Git planning workflow.
- [x] Build the READY-session probe form, automated analysis lifecycle/progress, immutable timeline, terminal outcomes, and bounded execution evidence.
- [x] Add a deterministic Phase 6 evidence → regression → Git planning → execution → attribution backend integration test with repository non-mutation proof.
- [x] Document the completed Phase 7 workflow and verify responsive/accessibility/browser behavior.
- [x] Run Prompt 5 frontend, full web, build, Phase 7–6 backend, full backend, static, formatting, security, Git-hygiene, and Alembic-head gates.

## Phase 8 — Production Monitoring / Drift

- [x] Specify deterministic closed-window and monitoring API contracts with failing tests.
- [x] Add reversible migration 0016 and constrained definition/snapshot persistence.
- [x] Add definition and idempotent snapshot materialization/read APIs.
- [x] Add trace/evaluation aggregation with explicit missing-evidence semantics.
- [x] Add durable lease-fenced execution, bounded recovery, and catch-up discovery.
- [x] Document Prompt 1's no-drift boundary and pass all backend/migration quality gates.
- [x] Prove drift contracts, metric/sample mappings, threshold boundaries, rate statistics, and conservative classification with failing tests.
- [x] Add reversible migration 0017 and constrained policy/rule/comparison/finding persistence.
- [x] Add create/read policy and synchronous atomic comparison APIs with bounded filtering and pagination.
- [x] Verify snapshot eligibility, immutable rule snapshots, ordered findings, and transaction completeness.
- [x] Document Prompt 2 semantics and run monitoring, Phase 7, full backend, static, formatting, and migration gates.
- [x] Add Prompt 3 failing contracts and focused automatic drift/incident tests.
- [x] Add migration 0018 and constrained automatic configuration/check/incident/event persistence.
- [x] Implement adjacent-window discovery, durable checking, Prompt 2 reuse, incidents, and cooldown.
- [x] Extend the existing monitoring worker/recovery path and add bounded versioned APIs.
- [x] Update monitoring docs and pass Prompt 3, compatibility, full backend, static, and migration gates. (A bounded test-only connection deadline stabilized the sequential suite; 374 tests pass.)
- [x] Add failing Phase 8 frontend contracts plus typed monitoring API/UI helpers.
- [x] Add Monitoring navigation and definition/policy/incident workspace UX.
- [x] Add monitor snapshot/comparison/automation/check detail UX with active-only polling.
- [x] Add incident acknowledgement and append-only evidence timeline UX.
- [x] Add low-cardinality metrics, deterministic Phase 8 E2E coverage, documentation, and final gates.

## Phase 9 — Unified Product Information Architecture

- [x] Add failing frontend contracts for grouped navigation, overview orchestration, shared context, and cross-links.
- [x] Replace phase-labelled flat navigation with responsive operator-intent groups.
- [x] Replace the placeholder home page with a bounded, failure-isolated operational overview.
- [x] Add reusable page headers, breadcrumbs, and factual status presentation where they reduce duplication.
- [x] Add the required experiment, regression, bisection, monitoring, and incident relationship links.
- [x] Complete frontend, browser/runtime, audit, documentation, migration-head, and hygiene verification.

## Phase 9 Prompt 3 — Deterministic Local Demo

- [x] Add deterministic backend acceptance tests for the complete sample workspace, lifecycle evidence,
      provenance, fixed timestamps, provider isolation, collision safety, and idempotent reruns.
- [x] Add one supported local workspace seed command using existing APIs/services and concise
      created/reused plus route/ID output.
- [x] Add the separate owned temporary Git bisection demo and verify its expected boundary without
      mutating the AgentScope checkout.

## Checkpoint: Demo evidence core

- [x] Focused backend demo, Phase 7, and Phase 8 compatibility tests pass.
- [x] No paid provider, external network, destructive reset, dependency, or schema migration exists.

- [x] Add focused Overview tests plus first-run guidance, exact CLI instructions, valid workflow links,
      and restrained demo provenance visibility.
- [x] Add the concise Local Demo prerequisites, commands, runtime, walkthrough, and safety documentation.

## Checkpoint: Phase 9 Prompt 3 complete

- [x] Both demo commands execute successfully against disposable local state and reseeding is idempotent.
- [x] Full backend/frontend, lint, typecheck, build, audit, migration-head, security, and hygiene gates pass.

## Phase 9 Prompt 4 — Final Product UX Hardening

- [x] Add focused failing accessibility, responsive-layout, workflow-state, and product-language contracts.
- [x] Restore destructive-action focus, support Escape dismissal, and harden copy/touch controls.
- [x] Add explicit loading semantics and programmatically associated form errors across existing workflows.
- [x] Add monitoring evidence-table semantics, route-level error headings, long-value handling, and neutral copy.
- [x] Verify representative keyboard, focus, narrow-screen, deep-link, loading, empty, and error behavior in-browser.
- [x] Run focused/full frontend, lint, type, build, audit, migration, backend, security, and hygiene gates.
- [x] Record the Phase 9 gate as ready only for the mandatory local/human audit before Phase 10.
