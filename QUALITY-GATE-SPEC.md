# Frontier Quality Gate Specification

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

Status: normative quality policy backed by the implemented package `0.4.0`
frontier contracts, preflight, execution host, reporting, and offline replay.
No authority-bearing external campaign has been executed, so the current
frontier machine result is `not_evaluable`.

## 1. Claim Boundary

The product goal is not an unbounded claim that one Prompt is universally best.
The strongest defensible target is:

> As of a stated date, for the exact models, provider versions, tools, task
> distribution, evaluation harness, and budget bound into the evidence bundle,
> the candidate is frontier-competitive against budget-matched strong baselines
> on a sealed test set, has no prohibited safety regression, and is not dominated
> on the quality-cost-latency Pareto frontier.

Every such claim expires when a bound model version, provider behavior,
benchmark definition, evaluator, optimizer implementation, or release package
changes. `universal_best`, unspecified `SOTA`, and award-equivalence claims are
never supported.

## 2. Separate Status Axes

Release maturity, evidence authority, and performance must not share one label.
The target model has three independent axes:

```text
release_status    = incomplete | stable_v1
evidence_level    = E0 | E1 | E2 | E3 | E4 | E5
performance_claim = optimized_candidate
                  | verified_improvement
                  | top_tier_scoped
```

`stable_v1` says the release process is complete. It does not by itself say the
optimizer is top-tier. `top_tier_scoped` requires stable release, authoritative
evidence, and every gate in this specification.

## 3. Campaign Binding and Preregistration

Before a sealed test set is opened, a campaign must bind and hash:

- campaign id, creation date, evidence-expiry policy, and responsible operator;
- candidate package, source revision, optimizer Prompt, profiles, and code hash;
- exact domain ids, task distributions, data licenses, and exclusion rules;
- every comparator, its version, configuration, and permitted search space;
- target model/provider snapshots, generation settings, retry policy, and seeds;
- evaluator models, families, providers, rubrics, and human-calibration set;
- optimizer, inference, evaluator, human-review, time, token, and money budgets;
- primary metric, minimum meaningful effect, tie handling, confidence level,
  statistical power target, multiplicity correction, and stopping rule;
- sealed-set commitment, contamination checks, and the analysis implementation.

Changing any bound item creates a new campaign. Results from an exploratory
campaign may not be relabeled as confirmatory evidence.

## 4. Four Data Tiers

1. **Development regression set.** The current 12-domain, 60-case catalog stays
   visible to developers and is used for fast diagnostics and regression tests.
2. **Sealed claim set.** New or private cases remain unavailable to optimizer,
   selector, evaluator-Prompt development, and candidate selection until the
   campaign is frozen. Sample size is determined by preregistered power analysis.
3. **Stress and safety set.** Covers semantic-preserving perturbations, Unicode
   and formatting noise, long context, missing or contradictory inputs, Prompt
   injection, tool failure, retries, timeouts, over-refusal, and distribution
   shift.
4. **External reproduction set.** Independent operators rerun a previously
   unseen set on different machines and at least one model family that did not
   participate in candidate development.

The current five cases per domain are a coverage smoke test, not statistically
adequate frontier evidence. The historical `30W/9T/21L` result is also
diagnostic: excluding ties gives 30 wins in 51 decisions, whose one-sided exact
sign-test p-value is about 0.131 and whose 95% Wilson interval includes 50%.

A frontier campaign must use a provider path that cannot read the benchmark,
rubric, evaluator implementation, sealed cases, or prior outputs except for the
single bound payload supplied to that call. An empty working directory is a
defense-in-depth measure, not proof that a tool-capable runtime is sealed.

## 5. Budget-Matched Baselines

Every claimed track must compare against, at minimum:

- the original Prompt (`identity`);
- a semantic-preserving format-only/no-op rewrite;
- an expert Prompt following the target provider's current guidance;
- a random rewrite or random-search baseline;
- at least one strong public automatic optimizer appropriate to the track.

The initial research baseline set should evaluate MIPROv2 and GEPA, with APE,
OPRO, ProTeGi, or TextGrad added when their task and optimization surfaces match.
Instruction-only and instruction-plus-demonstration/tool tracks remain separate.
Each track preregisters one primary normalization budget, normally monetary
cost or target-model tokens, and gives every method the same hard ceilings for
candidate count, calls, tokens, money, and wall-clock time. Exact resource use
need not be identical across algorithms; every attempt, repair, retry, selector,
judge, and failed call is charged to the method that caused it. A method that
exceeds any ceiling is a failed observation, not an omitted result.

## 6. Quality and Statistical Gates

Reports preserve raw paired observations and compute:

- per-case utility delta and raw wins, ties, and losses;
- `win_rate = (wins + 0.5 * ties) / N`;
- equal-domain macro delta, micro delta, worst-domain delta, and bottom-20% CVaR;
- results against every strong baseline, not only the original Prompt;
- clustered uncertainty by case so stochastic replicates are not treated as new
  independent tasks;
- corrected per-domain comparisons when multiple domains or metrics are claimed.

The measurement contract is fixed before unblinding:

- Each case rubric maps observable anchors to a utility in `[0, 1]`; incomparable
  rubric scales are not pooled. A paired delta is `candidate_utility -
  baseline_utility`, so it lies in `[-1, 1]`.
- A malformed response, timeout, refusal, or failed execution remains a terminal
  observation and receives the rubric's registered failure utility, normally
  zero. It is never removed from quality, cost, or latency denominators.
- Stochastic calls for one case are averaged inside that case cluster. The
  independent sample size is the number of distinct sealed cases, never the
  number of repeated calls.
- `micro_delta` is the mean of all case deltas. `macro_delta` is the unweighted
  mean of domain means. `worst_domain_delta` is their minimum. Bottom-20% CVaR
  is the mean of the lowest `ceil(0.20 * domain_count)` domain means.
- A single-domain or micro interval uses a seeded percentile bootstrap that
  resamples distinct case clusters with replacement. The equal-domain macro
  interval instead resamples cases within each domain and then gives every
  domain equal weight; unequal domain sizes must not silently turn it into a
  micro estimate. Confidence level, seed, iteration count, tie policy, and
  missing-observation policy are frozen campaign fields.

The campaign policy chooses the exact resampling method, but the default design
uses paired case-cluster bootstrap confidence intervals and Holm step-down
decisions for the family of one-sided domain/metric tests. Holm decisions do not
create "corrected confidence bounds"; ordinary intervals are reported alongside
the adjusted hypothesis decisions unless the policy separately preregisters a
simultaneous-confidence procedure.
The aggregate gate requires the confidence-interval lower bound against the
original to exceed the preregistered meaningful margin. If the project retains a
10% target, the lower bound—not merely the point estimate—must exceed 10%.
Against the strongest eligible baseline, every domain named in a superiority
claim must have a lower bound above zero and its preregistered one-sided test
must survive Holm correction. Domains that only establish non-inferiority must
be reported as such.

Sample size is determined before the run from alpha, power, minimum detectable
effect, expected ties, clustering, and evaluator error. Repeated calls on the
same five cases do not repair inadequate task-level sample size.

`src/prompt_performance_engine/frontier_statistics.py` implements strict,
dependency-free diagnostics for exact sign tests, Wilson intervals, Holm
decisions, normalized cross-domain summaries, seeded case-cluster bootstrap,
domain-stratified macro bootstrap, exact binomial risk upper bounds,
case-cluster safety aggregation, clean/perturbed robustness summaries, Type-7
latency percentiles, fully charged cost-per-success summaries, and descriptive
quality-cost-latency Pareto membership.
They are tested building blocks used by the versioned Frontier Evidence
contract, not claim authority. Authority still requires the frozen policy/data
binding to be executed through real independent receipt verifiers and replay
operators.

## 7. Deterministic Checks and Judge Validity

Machine-verifiable requirements take precedence as disqualifying failures. A
passing narrow hard check is necessary evidence but is not, by itself, proof
that an output is better overall; when the optimized output passes, blind quality
evaluation still decides between otherwise eligible outputs.

Open-ended evaluation requires:

- paired, blinded rubrics with both A/B orders evaluated;
- at least two evaluator model families that are independent of the principal
  generation/optimization family;
- deterministic checks instead of model judgment wherever possible;
- a stratified expert-human gold set and recalibration after evaluator changes;
- order consistency, human-majority agreement, agreement coefficients, critical
  recall, false-negative rate, and verbosity/length-bias probes;
- expert adjudication for high-risk cases and unresolved evaluator disagreement.

Separate calls or caches from the same model are replicates, not independent
judges. Suggested initial policy values such as human-agreement lower bound,
agreement coefficient, and critical recall must be risk-calibrated and stored in
the campaign policy rather than hard-coded into the evaluator. Independence is
machine-checkable only when each judge records provider, model family, version,
operator, rubric hash, and receipt authority and no qualifying judge shares the
principal generation/optimization family. Missing identity or either A/B order
makes the comparison `not_evaluable`; disagreement cannot be converted into a
candidate win without the preregistered human-adjudication path.

Calibration denominators and formulas are fixed: `order_consistency` is the
fraction of complete A/B pairs whose winner is unchanged after label reversal;
`human_majority_agreement` is exact agreement with the blinded human-majority
label; `critical_recall = detected_human_critical / human_critical`; and the
registered agreement coefficient is computed over the same complete gold
items. A zero denominator, missing label, or incomplete position pair is
`not_evaluable`. The policy binds minimum values for all four metrics and a
maximum length-bias effect before Judge qualification.

## 8. Robustness and Safety

The report includes perturbation retention, model-family transfer, temporal
holdout, and leave-one-domain-out results. Every perturbation has one registered
clean case and one semantics-preserving perturbed case. The report computes
`degradation = clean_utility - perturbed_utility` and, when clean utility is
positive, `retention = perturbed_utility / clean_utility`. A missing pair is
`not_evaluable`; worst-slice degradation must stay within a preregistered limit.
Portability keeps one Prompt fixed across model families, while the separate
model-scoped track reoptimizes under equal budgets; the two results are never
pooled.

Safety is not averaged into quality. Any critical or fatal regression blocks the
claim. Sandbox authority/isolation failure and unauthorized tool or environment
mutation are hard failures and stop the campaign. Lower-severity categories,
including unsafe compliance, benign over-refusal, contained Prompt injection,
and non-critical secret/PII handling failures, use one registered Bernoulli event
unit per distinct eligible sealed case. Repeated or correlated calls inside that
case are aggregated by the frozen policy, with any category failure marking the
case as failed; they never increase the independent safety trial count. The
estimand is the probability that one independently sampled case has at least one
category failure under the registered replicate/deployment policy. An exact
binomial bound is used only inside a preregistered IID stratum whose cases share
one failure probability; heterogeneous domains, red-team strata, or adaptive
samples are reported separately and never pooled as though they were binomial.
The bound uses a registered confidence level in `[0.5, 1)` and must remain below
the category limit, with family-wise decisions corrected as registered. Zero
eligible independent trials is `not_evaluable`; observing zero failures in a
small sample does not prove zero risk.

## 9. Cost, Latency, and Pareto Gates

Optimization and deployment costs are reported separately. For optimizer,
target, selector, and judge calls, evidence records:

- call count, attempts, input/output tokens, provider-price snapshot, and cost;
- wall-clock time, p50/p95/p99 latency, timeout, retry, and failure rates;
- deployment-Prompt token overhead and output-length change;
- expected cost per successful case and optimization break-even volume;
- best sealed-set quality reached as cumulative budget increases.

Cost is the sum of every bound call at the frozen provider-price snapshot;
`expected_cost_per_success = total_cost / successful_terminal_cases` and is
undefined, therefore failing, when there are no successes. Type-7 linear sample
quantiles define p50/p95/p99. All terminal calls, including failures and
timeouts at their recorded elapsed time, remain in the latency distribution.

A top-tier candidate must be non-dominated in quality, per-request cost, and p95
latency among all budget-eligible systems and must independently pass the
preregistered quality margin against every required strong baseline. One system
dominates another only when quality is no lower, cost and p95 latency are no
higher, and at least one comparison is strict. Reports should expose
high-quality, balanced, and low-cost Pareto candidates instead of hiding
tradeoffs in one composite score.

## 10. Authority, Reproduction, and Claim Inventory

Frontier evidence additionally requires:

- provider, evaluator, human, image, and execution receipts verified by
  independent trust boundaries;
- exact source-plan replay for benchmark, human, software, and visual evidence;
- three-machine and independent-operator reproduction bound to commands, exit
  codes, platform facts, package checksums, and output hashes;
- a claim inventory mapping each public statement to scope, metric, confidence
  interval, evidence hash, limitations, and expiry date;
- a defect register whose closed P0/P1 entries link to severity rationale,
  regression tests, and closure evidence.

R01-R10 remain the stable-release evidence gate. They must all pass before a
frontier campaign can qualify, but they are not sufficient for a performance
claim.

Reproduction has two distinct tracks. Offline authority replay reloads the same
recorded inputs and outputs and must reproduce canonical report bytes and hashes.
New experimental reproduction invokes the frozen systems again on an unseen
external set; provider outputs may differ, so it must reproduce the registered
statistical conclusions and gates rather than byte-identical model text. Neither
track can substitute for the other.

## 11. Current Implementation Status

Package `0.4.0` implements the public frontier Schema family `1.0.0`, canonical
authority-artifact I/O, full-freeze preflight, atomic budget accounting, a
preflight-authorized execution host, final report and claim-inventory
validation, and deterministic offline replay with three-replay aggregation.
These components fail closed at explicit verifier boundaries.

No real independent custodian, provider, Judge, human reviewer, execution, or
replay authority has completed the bound workflow, and no sealed external
frontier campaign has been executed. Local tests and simulated verifiers prove
contract behavior only; they do not prove identity, independence, superiority,
or reproduction. Therefore:

- the current frontier machine result is `not_evaluable`;
- `cross-domain-60-v2` remains the development/regression suite;
- same-model and historical provider runs remain diagnostic;
- no current result may be called `top_tier_scoped` or frontier-competitive.

## 12. Research Basis

The design draws on primary sources reviewed on 2026-07-21:

- [HELM](https://crfm.stanford.edu/helm/) for broad, multi-metric,
  reproducible evaluation;
- [OpenAI evaluation guidance](https://openai.com/index/evals-drive-next-chapter-of-ai/)
  for objective-driven evals and human audit of model graders;
- [MIPROv2](https://arxiv.org/abs/2406.11695) and
  [GEPA](https://arxiv.org/abs/2507.19457) as strong prompt-optimization
  research baselines;
- [PromptRobust](https://arxiv.org/abs/2306.04528) for semantic-preserving
  perturbation testing;
- [Judging LLM-as-a-Judge](https://proceedings.neurips.cc/paper_files/paper/2023/file/91f18a1287b398d378ef22505bf41832-Paper-Datasets_and_Benchmarks.pdf)
  for position, verbosity, and self-preference bias;
- [ICML 2025 evaluation uncertainty](https://proceedings.mlr.press/v267/bowyer25a.html)
  for uncertainty-aware evaluation at small sample sizes;
- [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence)
  for generative-AI risk evaluation.
