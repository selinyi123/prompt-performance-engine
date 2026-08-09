# Acceptance Criteria

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

## Release-Wide Invariants

- One package version source exists in `VERSION`.
- One artifact schema version is used across schemas, examples, and code.
- UTF-8 validation reports no mojibake in user-facing files.
- No feature is represented only by a descriptor or version-named status module.
- Public documentation distinguishes implemented, tested, and planned behavior.
- Source Prompts remain inert during optimization and audit.
- Optimized Prompt is the first substantive user-facing deliverable.
- No unsupported award, universal-best, or production-certification claim appears.

## Functional Gates

### Optimize

- empty input fails with a stable machine-readable error;
- a valid source compiles to an inert optimization request;
- explicit controls override inference;
- output modes are deterministic;
- generated artifacts preserve the source hash;
- parser returns the complete optimized Prompt without truncation.

### Audit

- injection, fake evidence, forced scoring, schema hijack, and context
  exfiltration fixtures are detected;
- undefined variables and contradictory output rules are reported;
- findings include severity, evidence location, and remediation;
- static audit cannot elevate runtime evidence.

### Evaluate

- original and optimized runs use matched settings;
- A/B identities are hidden from judges;
- deterministic hard checks are authoritative;
- score aggregation is reproducible;
- a critical regression blocks verified-improvement status;
- all outputs and decisions are hash-linked.

### Package

- request, source, optimized Prompt, profile, findings, evidence, and evaluation
  references validate;
- a package can be replayed from its manifest;
- corrupt or missing artifacts fail validation.

## Quality Gates for v1.0

| Gate | Required evidence |
|---|---|
| Domain coverage | 12 profiles, each with at least 5 cases |
| Case coverage | At least 60 total and 12 adversarial cases |
| Aggregate improvement | At least 10% over original baselines |
| Per-domain result | Wins greater than losses in every domain |
| Critical regressions | Zero |
| Fatal flaws | Zero in optimized outputs accepted for release |
| Authoritative hard checks | Zero optimized-output hard-check failures |
| Software isolation | All executable cases run in a digest-pinned container; inspected policy and active network, filesystem, identity, timeout, and memory probes pass; the bound `code_execution_plan` is rerun through an explicit live `DockerSandbox` and reproduces the complete R05 report |
| Actual image review | Five matched baseline/optimized image pairs pass file and pixel verification, receive complete blind rubric scoring from three qualified independent reviewers, show more optimized wins than losses, carry unique externally verified generation/reviewer receipts, and reproduce exactly from the bound `visual_review_plan` |
| Human review | At least 3 independently receipt-verified reviewers cover the same 24 or more stratified cases; every reviewer passes at least two reversed position probes and non-degenerate base-position checks; all cases reach direct reviewer consensus and direct human wins exceed losses; coordinator-only adjudication cannot qualify E4 |
| E3/E4 authority | Detached reports are reconstructed exactly from three source run directories, their canonical resolved benchmark definition, and the complete human-review plan using trusted model-call and reviewer-submission verifiers; self-reported IDs and hashes do not grant authority |
| R05/R06 authority | Readiness binds the code-execution and visual-review plans; R05 receives a matching live sandbox, R06 receives `ImageGenerationReceiptVerifier` and `VisualReviewerSubmissionVerifier`, and detached/offline self-hashed reports remain diagnostic |
| Reproducibility | Clean install and replay on three independently attested machines and operators |
| Security | No unresolved critical threat-model finding |
| Documentation | All commands and claims verified against current release |

The machine-readable completion gate is:

```powershell
python -m prompt_performance_engine assess-readiness `
  evidence\readiness-manifest.json --require-complete
```

Stable completion requires all R01-R10 requirements to pass without evidence
errors. The readiness manifest's `authority_sources` must bind the benchmark run
directories, `human_review_plan`, `code_execution_plan`, and
`visual_review_plan`. Authority validation must reproduce every source-derived
report with the required model, human-review, image-generation, and
visual-review receipt verifiers plus an explicit live Docker sandbox. A partial
benchmark, detached report, offline self-hashed facts, receipt-free CLI
aggregate, or missing evidence artifact fails closed.

Stable-release completion and frontier performance are separate claims. Passing
R01-R10 is necessary but not sufficient for `top_tier_scoped`; the additional
strong-baseline, statistical, robustness, safety, cost, latency, human-validity,
freshness, and Pareto gates are defined in `QUALITY-GATE-SPEC.md`.

## Frontier Contract Implementation Gate

The package `0.4.0` local frontier implementation is complete only when its
schemas, validators, host, replay path, and tests represent all of the following
requirements:

- `top_tier_scoped` is bounded to exact models, providers, data, harness, date,
  and budget and remains separate from release maturity and evidence level;
- quality uses normalized `[0, 1]` utility, paired case clusters, macro/micro,
  worst-domain, bottom-CVaR, uncertainty, and multiplicity-aware decisions;
- critical, fatal, sandbox, and unauthorized-environment events are hard stops,
  while lower-severity safety categories use registered denominators and exact
  risk upper bounds;
- robustness requires complete clean/perturbed pairs and separate fixed-Prompt
  portability versus per-model reoptimization tracks;
- all attempts and failures are charged to one primary budget plus equal hard
  ceilings; Type-7 p50/p95/p99 and quality-cost-latency Pareto dominance are
  unambiguous;
- identity, no-op, expert, random-search, and at least one qualified strong
  public optimizer are preregistered before sealed reveal;
- independent Judges bind provider/family/version/receipt identity, evaluate
  both A/B orders, and pass human-gold calibration;
- Phase 3.5 preflight binds the sealed-data commitment, systems, Judges,
  authority routes, analysis, thresholds, budgets, and stopping rules before
  any sealed payload is revealed;
- offline byte-identical authority replay and new stochastic experimental
  reproduction have separate manifests and pass criteria;
- versioned campaign, policy, commitment, preflight, execution, report, claim,
  and reproduction contracts are implemented with canonical contained I/O, a
  fail-closed budgeted host, and deterministic offline replay;
- dependency-free diagnostic helpers and fail-closed tests cover the local
  formulas, while documentation explicitly denies them claim authority;
- the final work tree passes the full unit suite, bytecode compilation, release
  validator, and patch-format check with package/version/schema/document
  identities aligned and without paid-call, commit, push, deployment, or
  publication changes.

This gate proves only that the versioned local contract, host, diagnostics, and
replay machinery are implemented. Simulated verifiers and local tests do not
prove independent authority. Until a real sealed external campaign and required
independent reproduction are executed, the frontier machine result is
`not_evaluable`. This gate does not prove Stable R01-R10, `top_tier_scoped`, live
Docker, real-provider superiority, human review, or independent-machine
evidence.

## Non-Evidence

The following do not prove product quality by themselves:

- number of files;
- number of versions;
- number of generated tests;
- schema validity alone;
- a model grading its own output;
- a single favorable example;
- labels such as LTS, enterprise, production, trusted, or world-class.
