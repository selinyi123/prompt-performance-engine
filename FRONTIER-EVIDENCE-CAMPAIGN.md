# Frontier Evidence Campaign

## Frontier Contract Status

frontier_contract_package: 0.4.0
frontier_machine_claim: not_evaluable
frontier_target_claim: top_tier_scoped
frontier_stable_gate: R01-R10
frontier_design_gate_sufficient_for_claim: false
frontier_quality_spec: QUALITY-GATE-SPEC.md
frontier_campaign: FRONTIER-EVIDENCE-CAMPAIGN.md
frontier_contract_implemented: true
frontier_preflight_contract_implemented: true
frontier_execution_host_implemented: true
frontier_offline_replay_implemented: true
frontier_independent_authority_executed: false
frontier_external_campaign_executed: false

Status: package `0.4.0` implements the versioned campaign contracts, canonical
artifact I/O, full-freeze preflight, budgeted execution host, reporting, and
offline replay. No real independent authority or external frontier campaign has
executed this workflow; the current frontier machine result is `not_evaluable`.

This document turns `QUALITY-GATE-SPEC.md` into an ordered evidence activity.
It does not replace the stable R01-R10 gate and does not grant a performance
claim by itself.

## 1. Campaign Outcome

The campaign answers one bounded question:

> For the exact target model snapshots, task distributions, harness, date, and
> budget in the frozen campaign plan, is Prompt Performance Engine statistically
> superior to every eligible budget-matched baseline, safe on the registered
> stress set, and non-dominated on quality, cost, and p95 latency?

The result is one of:

- `not_evaluable`: source, authority, sample, or budget requirements failed;
- `not_superior`: evidence is valid but one or more quality gates failed;
- `verified_improvement`: superior to the original Prompt within the scope;
- `top_tier_scoped`: superior to all registered strong baselines and passes all
  safety, robustness, efficiency, human-validity, and reproduction gates.

These are derived campaign outputs. Package `0.4.0` currently has no qualifying
authority-bearing campaign inputs and therefore remains `not_evaluable`.

## 2. Roles and Trust Boundaries

The same person or model may hold more than one operational role during a
diagnostic, but an authoritative campaign separates:

- campaign owner: freezes policy, budget, systems, and stopping rules;
- dataset custodian: holds sealed tasks and releases only one task payload per
  execution call;
- system operators: run PPE and comparators without access to sealed answers;
- provider-receipt verifier: verifies every bound generation and judge call;
- evaluator operators: run independent model-family judges with no system label;
- expert reviewers: create human gold and adjudicate high-risk disagreements;
- reproduction operators: rerun the frozen bundle on independent machines;
- claims auditor: maps every proposed public statement to exact evidence.

No local SHA-256 value proves identity or independence. External attestations
bind identities to the request, response, configuration, and campaign digest.

## 3. Frozen Directory Layout

The implemented host and canonical artifact contracts support this logical
layout; it becomes authority-bearing only when real independent verifiers and
operators execute the frozen campaign:

```text
frontier-evidence/<campaign-id>/
  campaign-plan.json
  release-quality-policy.json
  source-commitment.json
  datasets/
    development-manifest.json
    sealed-claim-manifest.json
    stress-safety-manifest.json
    external-reproduction-manifest.json
  systems/
    candidate.json
    identity.json
    no-op.json
    expert.json
    random-search.json
    <public-optimizer>.json
  runs/<system>/<target-model>/<replicate>/
    run-manifest.json
    raw-calls/
    outputs/
    execution-results/
    summary.json
  judgments/<judge-family>/<position>/
  human-calibration/
  software-r05/
  visual-r06/
  robustness/
  safety/
  statistics/
  efficiency/
  reproductions/<operator>/<machine>/
  defect-register.json
  claim-inventory.json
  readiness-report.json
  frontier-report.json
```

Every manifest uses exact fields, relative contained paths, SHA-256 content
digests, producer identity, limitations, and a versioned Schema. Raw provider
responses and sensitive source Prompts follow the campaign's access policy and
are not automatically public.

## 4. Phase 0: Authorization and Budget

Before any external work, obtain separate approval for:

- new benchmark/optimizer dependencies and their licenses;
- provider accounts, model families, maximum calls, tokens, money, and time;
- the digest-pinned Docker image and daemon used for R05;
- image-generation calls and storage used for R06;
- reviewer recruitment, compensation, privacy, and conflict handling;
- external machines/operators and evidence retention;
- any commit, push, CI, artifact upload, deployment, or publication.

Each track chooses one primary normalization budget, normally money or
target-model tokens, and records equal hard ceilings for candidate count, calls,
tokens, money, and wall-clock time. Algorithms need not consume identical
amounts of every resource, but every attempt, repair, retry, selector, judge,
timeout, and failed call is charged. Exceeding any ceiling stops the campaign;
it does not silently omit expensive baselines or failed observations.

## 5. Phase 1: Dataset Construction and Freeze

1. Keep `cross-domain-60-v2` as the visible development/regression suite.
2. Define the exact twelve Product Spec domains and target task distributions.
3. Build new claim cases with representative normal, difficult, adversarial,
   long-context, incomplete-input, and tool-failure strata.
4. Build a separate adaptive safety/red-team set and a temporal holdout.
5. Run license review, near-duplicate detection, contamination checks, and
   manual task-quality review.
6. Perform preregistered power analysis using expected effect, ties, evaluator
   error, clustering, alpha, and desired power. Five cases per domain is not a
   permitted frontier sample justification.
7. Hash the ordered case identities, strata, scoring anchors, and analysis plan.
8. Seal task contents from optimizers, selectors, evaluator-Prompt authors, and
   target runtimes until each bound call is created.

The dataset custodian signs the commitment before systems are optimized.
Phase 1 is incomplete unless the signed commitment binds the canonical ordered
case/stratum hashes, licenses, exclusion and contamination rules, target task
distribution, utility anchors, power-analysis inputs/output, analysis hash, and
sealed-content access policy.

## 6. Phase 2: Comparator Qualification

Register all systems before opening the sealed set:

1. original Prompt (`identity`);
2. semantic-preserving format-only (`no-op`);
3. provider-guidance expert Prompt;
4. budget-matched random rewrite/search;
5. PPE single-candidate release default;
6. PPE multi-candidate track, reported separately;
7. at least one strong public optimizer appropriate to the track, initially
   evaluating MIPROv2 and GEPA, with ProTeGi/APE/OPRO/TextGrad where applicable.

For each system, bind implementation/version, input visibility, search space,
target model, demonstrations/tools, candidate count, stopping rule, and all
budgets. Instruction-only and program/demonstration optimization are separate
leaderboards.

## 7. Phase 3: Judge Calibration

1. Prefer deterministic execution, Schema, citation, and environment-state
   checks whenever the task permits them.
2. Create a stratified human-gold set with domain experts and explicit scoring
   anchors.
3. Qualify at least two judge model families not used as the principal
   generation/optimization family.
4. Evaluate every pair in both A/B positions. Inconsistent position results are
   ties or human-adjudication candidates, never silently majority-voted away.
5. Measure human-majority agreement, agreement coefficient, critical recall,
   false-negative rate, order consistency, and length/verbosity bias.
6. Reject judge versions that miss the preregistered calibration policy.

Changing a judge model, Prompt, rubric, or parser invalidates its calibration
and creates a new campaign configuration.

## 8. Phase 3.5: Full Freeze and Local Preflight

Before the first sealed payload is revealed, a fail-closed preflight must
validate and hash all of the following without reading sealed task contents:

- exact campaign, release-policy, dataset-commitment, target-model, and stopping
  rule versions;
- complete comparator registrations, track compatibility, licenses, data
  visibility, primary budget, and equal hard ceilings;
- judge family/provider independence, both A/B orders, human-gold calibration,
  rubric hashes, thresholds, and receipt authorities;
- analysis implementation, normalized utility anchors, bootstrap seed and
  iterations, multiplicity family, safety event units/limits, robustness pairs,
  percentile method, and Pareto rule;
- one authority route for every case: deterministic check, R05 Docker, R06
  visual review, or an explicitly authorized environment-state verifier;
- contained relative paths, unique identifiers, canonical hashes, required
  signatures, available storage, and worst-case call/token/money/time totals;
- confirmation from the dataset custodian that no sealed content or answer has
  reached an optimizer, selector, Judge-Prompt author, or target runtime.

Preflight emits only a pass/fail result, failures, campaign digest, policy
digest, commitment digest, analysis digest, timestamp, and owner/custodian
attestations. Any frozen-field change invalidates that result and requires a new
preflight before reveal. Package `0.4.0` implements the public versioned Schema,
strict bundle validation, and fail-closed preflight verifier boundary for this
checkpoint. Test doubles prove local behavior only; they do not attest a real
custodian, clock, capability, or campaign pass.

## 9. Phase 4: Execution Matrix

For each registered target model, system, task, and replicate:

- reveal only the single bound task payload;
- record provider/model snapshot, request/response digests, usage, attempts,
  elapsed time, status, and externally verified receipt;
- run candidate-generated software only through the R05 live Docker authority;
- run tool/agent cases against a controlled simulator or real authorized test
  environment and record final environment state;
- generate and verify real image assets for R06;
- retain all timeouts, refusals, quota failures, malformed outputs, and budget
  failures as observations;
- forbid best-of-k cherry-picking unless best-of-k is the registered deployment
  policy and its full cost is charged.

Simulator results qualify only the explicitly registered simulator/robustness
scope. A claim about real tool or environment behavior requires the authorized
real environment-state verifier; simulator success cannot be relabeled as real
execution evidence. Failed, refused, malformed, timed-out, and quota-limited
terminal calls remain in quality, cost, latency, and reliability denominators.

The preregistered call estimate is calculated before approval:

```text
generation calls = tasks * target models * systems * replicates
judge calls       = evaluated pairs * judge families * 2 A/B positions
optimization calls, selectors, repairs, retries, images, and human work
                  are recorded as additional explicit budget lines
```

## 10. Phase 5: Statistics and Gates

The immutable analysis implementation computes:

- raw paired W/T/L and per-case utility deltas;
- equal-domain macro, micro, worst-domain, and bottom-20% CVaR results;
- paired case-cluster confidence intervals across stochastic replicates, with
  within-domain resampling for equal-domain macro estimates;
- preregistered minimum-effect tests against original and every strong baseline;
- Holm-corrected domain/metric decisions;
- robustness retention and worst-slice degradation;
- critical/fatal/hard-check and lower-severity risk upper bounds;
- safety event counts aggregated once per distinct sealed case so repeated calls
  cannot inflate the independent trial count, with exact binomial bounds applied
  only inside preregistered IID/common-risk strata rather than pooled across
  heterogeneous or adaptive samples;
- optimizer/target/selector/judge calls, tokens, cost, latency percentiles,
  retries, failures, output expansion, and expected cost per success;
- quality-cost-latency Pareto membership and budget-to-quality curves.

`frontier_statistics.py` provides strict local diagnostics for exact sign-test
and Wilson results, Holm decisions, normalized macro/micro/worst/bottom-CVaR,
seeded case-cluster and domain-stratified macro bootstrap, exact risk upper
bounds, case-cluster safety aggregation, clean/perturbed robustness summaries,
fully charged cost-per-success, Type-7 latency percentiles, and descriptive
Pareto membership. Package `0.4.0` also provides the frozen-contract,
preflight, budgeted-host, reporting, and offline-replay implementations.
Frontier authority additionally requires those implementations to be run over
the full strong-baseline matrix with real sealed inputs, independent roles, and
externally verified receipts.

## 11. Phase 6: Human, Reproduction, and Closure

1. Run E4 expert blind review on the registered stratified sample.
2. Complete R06 qualified visual review with unique generation/reviewer receipts.
3. Rebuild E3/E4/R05/R06 from their source plans and trusted verifiers.
4. On three independently attested machines/operators, perform offline authority
   replay over the same recorded payloads; canonical reports and hashes must be
   byte-identical.
5. Separately rerun the frozen systems on the unseen external-reproduction set.
   Provider text may differ, but preregistered statistical conclusions, safety
   gates, and Pareto eligibility must reproduce.
6. Link every P0/P1 defect to source evidence, severity rationale, fix, test, and
   closure attestation.
7. Generate the claim inventory with exact scope, metrics, intervals,
   limitations, evidence hashes, and expiry.
8. Run R01-R10 readiness and the frontier policy independently. Both must pass
   for `top_tier_scoped`.

## 12. Stop and Invalidation Rules

Stop without a frontier claim when:

- sealed data, evaluator, budget, or stopping rules change after unblinding;
- a required strong baseline is omitted or receives a smaller budget;
- the full-freeze preflight is missing, invalid, or predates any frozen change;
- receipts, raw observations, source plans, or independent roles are missing;
- any critical/fatal regression or sandbox authority failure occurs;
- a robustness clean/perturbed pair, safety event denominator, or terminal
  failure observation is missing;
- a safety denominator is zero or a required IID/common-risk stratum has no
  eligible independent cases;
- sample size or judge calibration is below policy;
- a provider/model version changes without a new frozen configuration;
- a result is selected from repeated attempts not charged to the policy;
- offline reproduction cannot rebuild the exact report, or new experimental
  reproduction fails its registered statistical gates.

A failed or inconclusive campaign is retained as evidence. It informs the next
development cycle but may not be rerun on the same sealed set as though it were
still unseen.

## 13. Present Local Status

Implemented and locally tested in package `0.4.0`:

- scoped claim and multi-axis quality policy;
- the versioned campaign, release-policy, source-commitment, preflight,
  execution, report, claim-inventory, replay, and reproduction Schema family;
- canonical, contained, write-once authority-artifact I/O;
- strict full-freeze bundle validation and fail-closed preflight;
- atomic budget accounting and a preflight-authorized execution host;
- final report/claim validation and deterministic byte-identical offline replay,
  including three-replay reproduction-set validation;
- diagnostic paired statistics, normalized cross-domain summaries,
  case-cluster and domain-stratified macro bootstrap, safety bounds, robustness
  pairs, fully charged cost, latency percentiles, and Pareto helpers;
- a local full regression suite.

Not executed and therefore not authority evidence:

- strong optimizer dependencies and real provider budget;
- sealed datasets and independent custodians;
- real independent provider, Judge, human, image, execution, and receipt
  authorities;
- live Docker, image generation, experts, and the bound external campaign;
- offline replay by three real independent machines/operators and the separate
  new stochastic external reproduction campaign;
- commit, CI, artifact publication, or release.

Consequently, the current frontier machine result is `not_evaluable`. The local
implementation must not be described as `top_tier_scoped`, frontier-competitive,
or independently reproduced.
