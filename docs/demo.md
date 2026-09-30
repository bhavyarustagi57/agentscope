# AgentScope demonstration

This walkthrough presents a deterministic **synthetic demonstration**, not production evidence. It
follows one evidence story in 10–15 minutes and keeps observation, statistical evidence, policy
classification, and causal explanation separate.

## Prepare the evidence

For the development stack, apply migrations, start the existing services, and run:

```powershell
uv run --project apps/api python apps/api/scripts/seed_demo.py
docker compose exec -T api python scripts/run_bisection_demo.py
```

The seed command prints canonical evidence routes. The bisection command prints its session route
because that session belongs to a marker-owned temporary Git repository and is intentionally separate
from the database seed. Run bisection inside the API container so repository validation and probing
share its filesystem. Both commands are safe to rerun, make no paid-provider calls, and do not reset
existing data.

For the production-style local package, follow [the deployment sequence](deployment.md), then run:

```text
docker compose --env-file .env.production -f compose.production.yaml --profile tools run --rm demo-seed
docker compose --env-file .env.production -f compose.production.yaml exec -T api python scripts/run_bisection_demo.py
```

Open `http://localhost:3000` for the development stack. If host ports were changed, use the configured
web origin. Keep the seed output beside the browser; its route manifest is the shortest path to each
record.

## 10–15 minute walkthrough

### 1. Overview — `/`

- **Purpose:** establish the evidence workflow and product boundaries.
- **Point out:** the synthetic-evidence banner, bounded factual counts, recent records, and links into
  trace, evaluation, investigation, and monitoring evidence.
- **Interpretation:** these counts describe the loaded page; they are not a health or trust score.
- **Talking point:** AgentScope is a modular monolith that keeps raw execution evidence and derived
  decisions connected by durable identifiers.

### 2. Trace Explorer — `/traces`

- **Purpose:** start from observed agent execution rather than a derived score.
- **Point out:** filters, statuses, durations, span kinds, and `AgentScope Demo` labels.
- **Interpretation:** a trace records what happened. It does not establish why an outcome happened.
- **Talking point:** the same immutable trace evidence can support evaluation, calibration, experiment,
  and monitoring workflows without copying it into a second demo system.

### 3. Rich nested trace — seed route `representative_trace`

- **Purpose:** inspect one concrete execution before discussing aggregate evidence.
- **Point out:** the parent/child span tree, tool and LLM attributes, events, error/status evidence,
  tokens where measured, and synthetic provenance metadata.
- **Interpretation:** unknown values remain distinct from measured zero; nested chronology is evidence,
  not a causal model.
- **Talking point:** ingestion is versioned and transactional, while the detail view reconstructs the
  hierarchy from persisted span relationships.

### 4. Deterministic evaluation — seed route `evaluation_run`

- **Purpose:** show a frozen evaluator applied to a fixed trace population.
- **Point out:** definition, run, and per-trace results are separate durable records; the sample uses an
  exact-match evaluator over the fixed `approved` output.
- **Interpretation:** an evaluation result means the configured rule matched or did not match. It is not
  a universal quality judgment.
- **Talking point:** durable submission, claims, retries, and recovery keep asynchronous execution
  idempotent without making Redis the source of truth.

### 5. Signature capability: human-vs-LLM calibration — seed route `calibration_report`

- **Purpose:** measure a judge against explicit human ground truth.
- **Point out:** the immutable four-subject study, authoritative adjudicated labels, deterministic judge
  decisions, one false positive, one false negative, the confusion matrix, agreement metrics, and
  Cohen’s kappa (`0.0` in this synthetic scenario).
- **Interpretation:** raw annotations are not silently majority-voted into truth. Accuracy alone can hide
  the direction of disagreement, while kappa exposes agreement beyond chance. No fabricated judge
  “trust score” is produced.
- **Talking point:** calibration requires a stable human reference standard; a judge cannot validate
  itself.

### 6. Paired experiment — seed route `experiment_report`

- **Purpose:** compare baseline A and candidate B on the same subjects and frozen evaluation condition.
- **Point out:** explicit A/B orientation and provenance, both-pass, both-fail, A-only-pass, and
  B-only-pass pairs; changed-subject links; exact McNemar evidence; paired bootstrap interval; and
  practical effect size.
- **Interpretation:** pairing controls for subject mix better than comparing independent aggregates.
  Statistical uncertainty and practical importance are separate, and the report does not declare a
  winner.
- **Talking point:** canonical statistics are computed and persisted by the backend, not recomputed in
  browser code.

### 7. Regression evidence — seed route `regression_check`

- **Purpose:** apply an explicit practical policy to the completed paired report.
- **Point out:** baseline A, candidate B, frozen provenance, the minimum pass-rate-drop threshold, each
  condition’s evidence, and `regression_detected`.
- **Interpretation:** this is a policy classification over experiment evidence. A p-value does not define
  the policy, and the classification does not identify a causal commit.
- **Talking point:** “no regression” and “insufficient evidence” are modeled separately; absence of proof
  is not proof of absence.

### 8. Signature capability: Git bisection — route printed by `run_bisection_demo.py`

- **Purpose:** narrow the observed transition after a detected regression.
- **Point out:** the real local five-commit range (`PASS, PASS, PASS, REGRESSION, REGRESSION`), frozen
  probe configuration, persisted midpoint decisions and execution attempts, previous-good commit, and
  first-regressed commit.
- **Interpretation:** the result is the **first observed regression boundary under the frozen probe and
  range**, not a proven causal root cause.
- **Talking point:** the target is a marker-owned temporary repository; probes use argv execution with
  bounded timeouts in AgentScope-owned worktrees. The AgentScope checkout is never the target and
  remains unchanged.

### 9. Monitoring, drift, and incident — seed routes `monitor` and `incident`

- **Purpose:** connect closed-window operational evidence to a durable incident lifecycle.
- **Point out:** seven fixed UTC windows, canonical snapshots, absolute failure-rate drift comparisons,
  automatic checks, incident opening, recurrence events, clean-window resolution, and later incident
  evidence where present.
- **Interpretation:** drift is a comparison classification; an incident is durable lifecycle state.
  Missing evaluation evidence remains unknown rather than zero. This scenario is synthetic, not live
  production monitoring.
- **Talking point:** fixed timestamps make the lifecycle reproducible while PostgreSQL preserves every
  state transition and event.

### 10. Production-readiness proof — `/health`, `/ready`, and deployment tooling

- **Purpose:** close the story with operational boundaries rather than marketing claims.
- **Point out:** liveness versus dependency readiness, explicit Alembic migration, preflight, bounded
  deployment smoke, non-root images, worker recovery logs, and the PostgreSQL named volume.
- **Interpretation:** a running process is not necessarily ready. OpenAI is optional and is not required
  for the deterministic demo or frontend availability.
- **Talking point:** PostgreSQL is the durable system of record; Redis is replaceable queue/runtime
  coordination. Lease and fencing state in PostgreSQL allows interrupted async work to recover.

## Five-minute interview path

Use the printed deep links; do not create records interactively.

1. **Trace (40 seconds):** open `representative_trace`; show the nested execution and synthetic
   provenance.
2. **Calibration (70 seconds):** open `calibration_report`; show authoritative labels, FP/FN, and kappa.
3. **Experiment (60 seconds):** open `experiment_report`; show paired outcome combinations and changed
   subjects without declaring a winner.
4. **Regression (40 seconds):** open `regression_check`; show orientation and the practical threshold.
5. **Bisection (60 seconds):** open the separately printed session route; state “first observed
   regression boundary,” never “root cause.”
6. **Monitoring (30 seconds):** open `incident`; show durable event history and resolution/reopening
   semantics.

## Compact architecture

```text
Agent SDK / trace exporters
            |
            v
Next.js --> FastAPI --> PostgreSQL (traces, evidence, leases, incidents)
               |
               +---- Redis / Dramatiq <---- workers
                         ephemeral          durable recovery via PostgreSQL
```

The deployment is a modular monolith: one API domain boundary and one PostgreSQL system of record,
with worker processes executing durable jobs. Redis can be replaced after interruption; canonical
evidence and recovery authority remain in PostgreSQL.

## Short technical answers

- **Why PostgreSQL?** Evidence, provenance, leases, attempts, and lifecycle transitions require
  transactional durability and relational constraints.
- **Why Redis?** It provides fast queue coordination; losing it must not erase canonical evidence.
- **Why human calibration?** A judge needs an external reference standard. Self-consistency is not
  ground truth.
- **Why paired experiments?** The same subjects are observed under A and B, making discordant pairs
  explicit and avoiding subject-mix confounding from independent aggregates.
- **Why separate practical thresholds from p-values?** Statistical compatibility does not say whether
  an effect is operationally important; policy needs an explicit magnitude rule.
- **What does bisection prove?** It identifies the first observed boundary for one frozen probe and
  commit range. It does not prove causal mechanism.
- **Why distinguish missing from zero?** “No evidence was collected” and “evidence measured zero” imply
  different operational actions.
- **How does async work survive interruption?** PostgreSQL claims, renewable leases, fencing tokens,
  bounded attempts, and recovery scans prevent Redis delivery state from becoming authoritative.

## Scientific language guardrails

Say **evidence**, **observation**, **calibration**, **practical threshold**, **detected regression**, and
**first observed regression boundary**. Do not claim that the judge is trustworthy, a variant is best,
a commit is the root cause, or synthetic monitoring is live production behavior.
