# Experiments and A/B identity

AgentScope provides reproducible experiment configuration plus durable execution and raw results. It
does not claim that an observed difference is causal or statistically significant.

## Controlled comparison semantics

An experiment has exactly two variants, `A` and `B`. A subject position represents one logical test
case, with two distinct trace executions: one produced by A and one by B. The ordered pair is stored
explicitly. A trace may occur only once anywhere in an experiment, so the system never invents a
paired relationship from unrelated results or reuses one execution as evidence for both variants.

The ordered population is supplied by the caller. AgentScope never substitutes “latest traces” at
execution time.

## Provenance and evaluation conditions

Each variant has a stable UUID, display name, key, and JSONB provenance snapshot. Provenance supports
optional agent, model, prompt, workflow, Git commit, and deployment identifiers plus bounded custom
metadata. It is descriptive evidence only: APIs do not run Git or interpret stored values. Do not put
credentials, tokens, prompts containing secrets, or other sensitive values in provenance metadata.

Evaluation conditions reuse canonical evaluation definitions. AgentScope stores their ordered IDs and
copies the definition name, evaluator kind, and validated evaluator configuration. Later edits to an
evaluation definition therefore cannot change what the experiment says it will compare.

## Lifecycle and immutability

New experiments start in `draft`. `POST /api/v1/experiments/{id}/configuration` atomically sets the
name, description, both variants, the ordered trace pairs, and ordered evaluation conditions. Draft
configuration may be replaced; A/B UUIDs remain stable. PostgreSQL row locks serialize competing
configuration and ready requests.

`POST /api/v1/experiments/{id}/ready` validates that both variants, at least one complete pair, and at
least one evaluation condition exist. The transition is idempotent. Once ready, variant identity and
provenance, subject membership and order, pairing, and evaluation conditions are immutable in the
service layer. Database foreign keys, check constraints, and unique constraints independently guard
important identity and ordering invariants.

Lifecycle transitions are service-controlled; the API does not expose arbitrary status mutation.

## API

- `POST /api/v1/experiments` creates a draft.
- `GET /api/v1/experiments` returns a bounded, deterministic newest-first list and accepts `status`,
  `page_size`, and `offset`.
- `GET /api/v1/experiments/{id}` returns the full ordered snapshot.
- `POST /api/v1/experiments/{id}/configuration` atomically configures a draft.
- `POST /api/v1/experiments/{id}/ready` validates and locks the snapshot.

Repeated identical draft configuration is safe, and repeated ready requests return the same locked
experiment. A competing draft update that resumes after another request locks the experiment fails as
immutable.

## Reproducibility boundary and limitations

The record proves which trace executions, provenance claims, ordering, and evaluation configuration
were selected. It does not prove that variant assignment was randomized, that external runtime
conditions were controlled, or that a difference was caused by the variant. Automatic winner
selection or external runtime control. Regression and bisection consume this evidence without
changing those causal limitations; remote alerts remain outside the local product boundary.

## Durable runs and raw decisions

A ready, completed, or failed experiment can create a new explicit run. Runs are historical records:
creating another run never overwrites prior results. A partial unique index permits only one
`pending`, `queued`, or `running` run per experiment, avoiding contradictory experiment status.

Run lifecycle is `pending -> queued -> running -> completed`, with `failed` for execution failures.
The experiment becomes `running` when a worker claims a run, then reflects that run's terminal
`completed` or `failed` state. A later explicit run may move the experiment through those states
again while earlier runs remain inspectable.

For `N` paired logical subjects and `M` frozen evaluation conditions, a complete run contains exactly
`N × 2 × M` decisions. Raw identity is the composite of run ID, subject position, variant key, and
condition position. Each row also records the trace ID, original definition ID, evaluator outcome,
score, bounded details, and owning attempt. Reads join the immutable condition snapshot to expose its
name, evaluator kind, and configuration.

Results are ordered by subject position, then `A` before `B`, then condition position. List endpoints
are bounded and may filter by variant or condition position.

## Delivery, retries, and recovery

`POST /api/v1/experiment-runs/{id}/execute` persists `queued` intent before attempting Redis
delivery. A broker failure therefore returns a deferred delivery while PostgreSQL retains the work.
The existing periodic recovery loop scans a bounded batch of stale queued runs and expired running
leases with `SKIP LOCKED`, reserving each candidate before enqueueing it.

Workers claim a run with a UUID lease token and monotonically increasing attempt number. Evaluation
uses the frozen experiment condition—not the mutable source definition—and reuses the deterministic
deterministic evaluator engine. Decisions are evaluated and persisted in bounded chunks; every chunk
renews the six-minute lease. A chunk is CPU-local and bounded to 100 deterministic evaluations, so a
separate background heartbeat is unnecessary. Every write and final transition checks the token and
attempt, preventing an expired worker from changing newer work.

Retries query for missing composite decision identities and preserve already committed results.
Duplicate delivery is ignored when a run is not queued. A run reaches `completed` only when its
durable result count equals its immutable expected count.

Evaluator outcomes such as `passed`, `failed`, or `error` are valid decisions. Infrastructure or
snapshot failures instead set a bounded run-level error category/message and never fabricate a raw
decision. Partial valid results remain inspectable if a run ultimately fails.

The run APIs are:

- `POST /api/v1/experiments/{experiment_id}/runs`
- `GET /api/v1/experiments/{experiment_id}/runs`
- `GET /api/v1/experiment-runs/{run_id}`
- `POST /api/v1/experiment-runs/{run_id}/execute`
- `GET /api/v1/experiment-runs/{run_id}/results`

Raw experiment results alone are not a statistical conclusion and must not describe either variant
as “better.”

## Paired statistical analysis

`POST /api/v1/experiment-runs/{run_id}/analysis` creates or returns the run's one canonical analysis;
`GET` on the same path reads it. Creation requires a completed run whose result identities exactly
match every frozen subject, A/B trace, and condition. Conditions containing an evaluator `error`
outcome are retained as explicitly ineligible with `non_binary_outcome`; AgentScope never coerces the
error to failure or drops that subject. Other eligible conditions remain independently analyzable.

The logical subject is the unit of analysis. Every frozen condition is analyzed separately, with B−A
as the directional convention. The response exposes A and B pass/fail counts and rates, absolute
pass-rate difference, and this paired table:

| | B pass | B fail |
|---|---:|---:|
| A pass | both passed | A-only passed |
| A fail | B-only passed | both failed |

The matched-pairs odds ratio is `B-only passed / A-only passed`. It is `null` when the denominator is
zero rather than emitting infinity. McNemar evidence uses the exact two-sided binomial test for every
sample size. With no discordant pairs its p-value is `1` and its chi-square-style test statistic is
`null`. The `rejects_null` field reports only whether the raw p-value is below the recorded alpha; it
is not a winner or deployment recommendation.

The 95% confidence interval estimates the paired B−A pass-rate difference with a 10,000-iteration
paired percentile bootstrap. Resampling is over logical subjects, represented by their paired
difference category. The deterministic seed for condition position `p` is the persisted base seed
`6003 + p`, so identical frozen input reproduces the same interval. Bootstrap intervals for very
small samples can be degenerate and should be interpreted cautiously.

Each condition has its own effect, interval, and raw p-value. AgentScope does not average p-values or
combine conditions into a universal score. These p-values are unadjusted and therefore do not control
family-wise error when many conditions are examined.

`GET /api/v1/experiment-runs/{run_id}/analysis/changes` requires a condition position and returns only
discordant subjects, deterministically paginated by subject position. Optional direction is
`A_PASS_B_FAIL` or `A_FAIL_B_PASS`; each item includes both trace IDs, both outcomes, and the frozen
condition identity. An ineligible condition returns an explicit conflict instead of a partial list.

Statistical significance does not establish practical importance. Failure to reject the null does
not prove equivalence. A statistically detectable difference does not establish causality unless the
experimental design itself supports causal interpretation. AgentScope does not automatically choose
a winning variant.

## Policy-defined regression checks

Regression detection classifies the observed candidate result against an explicitly selected
baseline; it does not attribute causality. A policy stores a practical pass-rate drop as a fraction
in `(0, 1]` and a minimum paired sample size. Checks must explicitly select either A as baseline and B
as candidate or the reverse—A/B labels alone never imply that orientation.

For every eligible condition, the service reads the persisted paired analysis and orients its
canonical B-minus-A effect as `candidate_pass_rate - baseline_pass_rate`. It does not recompute raw
counts, intervals, or McNemar evidence. With sufficient sample size, an effect less than or equal to
the negative policy threshold is a regression, including exact equality. The p-value remains context
and is not part of this decision rule.

An ineligible experiment condition or an eligible condition below the policy sample minimum becomes
`insufficient_evidence`; undefined statistics remain null. Any regressed condition makes the overall
check `regression_detected`. Otherwise, the overall result is `no_regression_detected` only when every
condition is sufficient, and `insufficient_evidence` when any condition is insufficient.

`POST /api/v1/regression-policies`, `GET /api/v1/regression-policies`, and
`GET /api/v1/regression-policies/{id}` manage create/read policies. `POST
/api/v1/regression-checks` synchronously stores one immutable check and all findings in a transaction;
the list and detail endpoints read those historical snapshots. Policy parameters, orientation,
variant provenance (including a supplied Git SHA), and condition configuration are copied into the
check evidence so later source changes cannot alter its interpretation. Stored Git values remain
descriptive provenance and do not establish causality.

## Immutable bisection planning

A bisection plan may be created only from a persisted `regression_detected` check whose frozen
baseline and candidate provenance both contain `git_commit_sha`. The baseline is the known
non-regressed boundary and must resolve to an ancestor of the known-regressed candidate in the local
target repository. Equal, missing, unresolved, or diverged endpoints are rejected; AgentScope never
silently substitutes a merge base or reverses them.

Planning uses read-only Git inspection and freezes canonical full SHAs. Its candidate universe is
`baseline..candidate`, excluding the known-good baseline and including the candidate, ordered with
Git's reversed topological ancestry traversal. Each row retains its commit timestamp, bounded subject,
and all parent SHAs so later Git-aware selection can account for merges. Plans over the configured
`BISECTION_MAX_COMMIT_RANGE` (default 500) are rejected rather than truncated.

`POST /api/v1/bisection-sessions` creates the complete READY plan atomically. The list endpoint is
bounded and filterable by regression check; detail returns the immutable ordered commit snapshot.
Moving a branch after planning cannot change the stored SHAs or range. Planning does not checkout or
test commits, run `git bisect`, identify a culprit, or establish causal attribution.

## Durable selected-commit execution

Selected-commit execution accepts only explicitly requested full SHAs from a READY session's frozen commit
population. A run snapshots the session, repository identity, planned population size, and versioned
probe configuration. Only one run may be `pending`, `queued`, or `running` for a session, while
terminal historical runs and their attempt evidence remain independent.

The probe contract is an executable plus at most 64 argv values, an optional repository-relative
working directory, a 1–3,600 second timeout, and separate 1–1,048,576 byte stdout/stderr limits.
AgentScope invokes both Git and the probe with argv arrays and `shell=False`; shell metacharacters are
literal data. It supplies only a small process-launch environment and neither persists nor logs that
environment. AgentScope does not infer setup or install dependencies. A user-specified probe is
trusted local host code: detached worktrees protect checkout state but are not a hostile-code,
filesystem, process, or network sandbox.

Each attempt creates a detached worktree for the exact frozen SHA beneath `BISECTION_WORK_ROOT`, in a
run/SHA/lease-token directory with explicit ownership markers. The target repository's HEAD, branch,
index, and working tree are not switched or reset. Cleanup validates both root containment and the
attempt marker before removing the worktree; it never recursively deletes arbitrary paths. Cleanup
failure is retained as operational evidence and does not rewrite a valid probe outcome.

Exit `0` maps to `pass`, exit `1` to `regression`, and every other completed exit—including `2`—to
`indeterminate`. Timeout, spawn, worktree, and internal execution failures are retried up to
`BISECTION_EXECUTION_MAX_ATTEMPTS` and become `execution_failed` after exhaustion; they never become
regressions. Stdout and stderr are decoded with replacement, bounded independently, and annotated
when truncated.

`POST /api/v1/bisection-sessions/{session_id}/execution-runs` creates a pending run. `GET` on that
collection lists historical runs; `GET /api/v1/bisection-execution-runs/{run_id}` returns progress,
targets, and attempts. `POST /api/v1/bisection-execution-runs/{run_id}/execute` accepts 1–50 unique
planned SHAs and durably commits queued intent before broker delivery. A repeated identical submit is
idempotent; a changed submit is rejected.

Workers claim one unfinished target with a monotonically increasing attempt number and UUID lease,
execute outside database transactions, heartbeat the lease, and fence final writes by token, attempt,
and expiry. Broker outages leave queued intent for periodic recovery. Expired work becomes reclaimable,
completed targets remain untouched, and stale workers cannot overwrite newer evidence. Execution does
not select midpoints, run a binary-search state machine, identify a first bad commit, or attribute a
culprit.

## Persisted automated bisection

Persisted automated bisection coordinates compatible execution evidence over the immutable planned commit population. It does
not recalculate a range from a moving branch and never invokes `git bisect`. The known-good baseline
is position `-1`; the candidate is confirmed as `regression` through the configured probe
before the search narrows. Every analysis freezes the complete versioned probe configuration, so only
terminal `pass` or `regression` evidence from the same session, full SHA, and exact configuration may
be reused.

For boundaries `good` and `bad`, the next position is `floor((good + bad) / 2)`. A `pass` moves the
good boundary to that position; a `regression` moves the bad boundary to it. Each consumed result is
an immutable decision step containing its before/after boundaries and whether evidence was reused or
newly requested. `indeterminate` and retry-exhausted `execution_failed` results are persisted as
unusable, never treated as domain evidence, and skipped by choosing the nearest unobserved position
with ties resolved toward the lower position. Skips remove classic logarithmic-step guarantees.

When unusable commits leave an unresolved gap, endpoint execution disagrees with the configured
orientation, or compatible observations reveal a regression followed by a later pass, the analysis
is `inconclusive` rather than forcing an attribution. This model assumes one monotonic
pass-to-regression boundary; regressions that disappear and reappear violate that assumption.

An attributed result records the adjacent previous-known-good and first-observed-regressed commits
within this frozen range under this exact probe. It is not proof that the latter commit causally
introduced a bug or is a root cause. The API exposes create/start, bounded history, current boundaries,
progress counts, and chronological steps. Durable queued intent, short lease-fenced worker iterations,
and periodic recovery allow the coordinator to resume after broker or worker interruption while the
execution service remains the sole owner of worktree and subprocess execution.

The workflow is exposed through the single **Regressions** navigation area. A completed experiment run links
to a policy-backed regression check without changing its canonical analysis. The regression pages
create and inspect policies and checks, require explicit baseline/candidate orientation, and show
null statistical values as unavailable. Detected checks with both Git SHAs can plan a local bisection
session; planning errors remain explicit and the frozen ordered commit range is inspectable.

The bisection page accepts an executable and newline-delimited argv values, never a free-form shell
command. It starts the persisted coordinator, polls every five seconds only while active, and renders
server-owned boundaries, progress counts, decision steps, reused/requested evidence, bounded process
output, and distinct attributed, inconclusive, and failed terminal states. Managed worktree paths,
lease tokens, process environments, and credentials are not rendered.

AgentScope identifies the first observed regression boundary under a frozen probe and commit range;
this is evidence for investigation, not automatic proof of causal root cause.

## Product workflow

The `/experiments` workspace provides a bounded, status-filtered experiment list and draft creation.
An experiment detail page configures named A/B variants, bounded descriptive provenance, ordered A/B
trace pairs, and existing evaluation definitions. Each row is one logical subject and must name two
distinct trace executions. The UI prevents obvious malformed input, while the API remains the
authoritative validator.

The READY action explicitly warns that variants, provenance, subject order, and conditions will be
frozen. READY and later lifecycle states render that snapshot read-only. Provenance fields are
evidence only and must not contain credentials, tokens, or other secrets.

Eligible experiments can create an explicit run and submit it once. Run pages show expected,
completed, remaining, and evaluator-error decision counts. Only queued and running runs poll, at a
five-second interval with timer and request cleanup on terminal state or navigation. Historical runs
remain linked from the experiment; the product does not statistically compare separate runs.

Raw decisions are fetched 20 at a time and can be filtered server-side by A/B variant or frozen
condition position. Rows link to the underlying trace and retain subject, variant, condition,
outcome, score, and attempt attribution.

For a completed run, the user may create or read the persisted canonical server analysis. The UI
does not reproduce statistical formulas. It presents every condition independently, including
explicitly ineligible conditions, and labels the effect as the observed B-minus-A difference.
Eligible reports show counts, rates, the paired table, paired bootstrap interval, exact McNemar
p-value, and nullable matched-pairs odds ratio. Changed subjects are fetched from the server with
condition, direction, and deterministic pagination controls, with links to both A and B traces.

The report keeps effect magnitude separate from statistical evidence. It states that significance
does not establish practical importance, failure to reject does not establish equivalence, causal
claims require a suitable design, and per-condition p-values are unadjusted for multiple testing.

The experiment domain provides no automatic winner, deployment recommendation, universal causality claim,
regression attribution, Git commit attribution, or Git bisection.
