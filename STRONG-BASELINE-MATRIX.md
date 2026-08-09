# Strong Baseline Qualification Matrix

## Document Status

research_cutoff: 2026-07-21
document_created: 2026-07-21
scope: Phase 2 comparator qualification and Phase 3 judge calibration
execution_status: research_only_not_installed_not_run
current_frontier_result: not_evaluable

This matrix turns the strong-optimizer research into an executable registration
plan for the Frontier Evidence Campaign. It does not install an optimizer,
authorize a paid or external call, qualify a judge, create human-gold labels, or
establish a performance claim. Versions below are the latest stable or source
states observed at the research cutoff; every real campaign must pin the exact
package version, source commit, lockfile, and artifact digest again at freeze.

The immediate decision is:

- GEPA and MIPROv2 are the two primary strong public optimizer baselines.
- Instruction-only and instruction-plus-demonstration/program optimization are
  different search spaces and remain separate leaderboards.
- APE, OPRO, ProTeGi, and TextGrad are added only when the registered task and
  optimization surface match; their inclusion never replaces a compatible
  primary baseline.
- Historical APE, OPRO, and ProTeGi repositories must not be run unchanged
  against current provider APIs.
- The current project evidence does not bind two qualifying independent judge
  families or a locked expert-human gold set. Phase 3 and the full-freeze
  preflight therefore fail closed, and no frontier result is evaluable.

## 1. Track and Comparator Matrix

Every claimed track also includes the original Prompt, a semantic-preserving
format-only/no-op rewrite, a provider-guidance expert Prompt, budget-matched
random rewrite/search, PPE single-candidate, and PPE multi-candidate systems
required by QUALITY-GATE-SPEC.md and FRONTIER-EVIDENCE-CAMPAIGN.md.

| Track | Frozen search surface | Required primary strong baselines | Conditional baselines | Separation rule |
| --- | --- | --- | --- | --- |
| I: instruction-only | Instruction/system text only; no generated or selected demonstrations; no tool-schema mutation | GEPA text/default adapter; MIPROv2 configured for zero demonstrations | OPRO as an orthogonal meta-prompt loop; APE as a historical pool-generation/selection ablation | Results may be compared only with systems using the same instruction-only visibility and deployment payload |
| P: instruction plus demonstrations/program/tools | Instruction plus registered demonstrations, program fields, or tool descriptions | MIPROv2 with its instruction-and-demo search enabled; GEPA with the compatible DSPy/program adapter | TextGrad for a registered multi-component computation graph | Never pool with Track I; demo/tool generation, selection, storage, and deployment tokens are charged |
| C: labeled classification | Instruction or template optimized against labeled classification examples | The compatible Track I or P primary baselines | ProTeGi; optionally GEPA ConfidenceAdapter after separate registration | Classification-only results cannot support a general cross-domain optimizer claim |
| G: compound computation graph | Multiple textual variables and registered differentiable-style loss/backward/update roles | GEPA on the same mutable fields; MIPROv2 only when the program can be represented without changing semantics | TextGrad | The exact mutable variables, forward model, loss model, backward model, and update rule are part of the system identity |

Both GEPA and MIPROv2 should be registered for Tracks I and P when they can
operate on the exact frozen surface. If either cannot be adapted without
changing task semantics or visibility, its ineligibility must be documented
before unblinding. At least one appropriate strong public optimizer remains
mandatory for every claimed track.

## 2. Optimizer Qualification Matrix

Maintenance is an observed repository signal, not a warranty. Upstream code
licenses do not grant rights to provider models, benchmark data, or generated
artifacts; those require separate review.

| Optimizer | Version and maintenance observed at cutoff | License | Matching surface | Integration decision | Budget and reproducibility risks | Phase 2 disposition |
| --- | --- | --- | --- | --- | --- | --- |
| GEPA | Official gepa-ai/gepa v0.1.4, released 2026-07-15; active release and repository activity | MIT | Instruction text, DSPy programs, tool descriptions, and custom textual components through adapters | Reuse the small GEPA core behind a PPE adapter. Prefer the core package to the full extra. Pin v0.1.4 plus commit and lock digest | max_metric_calls covers metric/evaluator work, not every reflection/provider call. v0.1.4 can overshoot an internal metric budget by one proposal iteration, so PPE must enforce a pre-call hard guard. Reflection, proposal, refiner, cache misses, and batch contents remain separately chargeable | Primary |
| MIPROv2 | Implemented in DSPy; latest stable release observed was DSPy 3.2.1 on 2026-05-05. DSPy main also exposed a 3.3.0b1 prerelease, which is not the default campaign pin | MIT | Joint instruction and few-shot demonstration optimization; zero-demo configuration for Track I; multistage DSPy programs for Track P | Run DSPy 3.2.1 with the Optuna extra in an isolated environment. Set auto to none and explicitly freeze candidate count, trial count, demonstrations, seeds, prompt model, and task model | num_trials is not a total-call limit. Bootstrapping, instruction proposal, minibatch/full evaluation, selector work, retries, and failed calls must be counted outside DSPy. Runtime permission prompts are not a budget authority | Primary |
| TextGrad | Latest GitHub release observed was v0.1.6 from 2024-12-15 while main setup metadata reported 0.1.8; last observed repository activity was 2025-07-25; package classifier was Pre-Alpha | MIT | Multi-component textual computation graphs, system prompts, and examples | Optional isolated adapter only. Pin an exact commit because release and source versions drift. Treat LiteLLM support and engine compatibility as experimental until locally smoke-tested | No campaign-wide hard ceiling should be inferred from its optimizer loop. Charge forward target calls, loss/evaluator calls, backward/gradient aggregation, update/proposer calls, retries, and failures. A qualifying judge family must not also serve as its loss/backward/update engine | Conditional |
| OPRO | Official DeepMind research repository; no packaged release observed; small history with last observed activity on 2024-12-04 | Apache-2.0 | Instruction-only iterative meta-prompt optimization | Reimplement the minimal history/meta-prompt algorithm on PPE provider and budget contracts. Do not make the old research environment a runtime dependency | Official environment references Python 3.10.13, google.generativeai 0.1, openai 0.27.2, text-bison, and GPT-era examples. These do not define a current reproducible API or price | Conditional historical/orthogonal |
| ProTeGi | Official code is a subdirectory of Microsoft LMOps; last observed module activity on 2024-05-10 | MIT at LMOps root | Labeled binary classification and beam/bandit search over textual gradients | Reimplement error-minibatch critique, prompt transformation, and registered beam/UCB or successive-reject selection on PPE contracts | Official code hard-codes GPT-3.5 REST patterns and retired text-davinci-003 completion/logprob behavior, stores a key placeholder in config, and contains unbounded retry loops. It has no self-contained locked requirements | Conditional classification only |
| APE | Official research repository; setup metadata version 1.0, no GitHub release observed, and last observed activity on 2023-05-25 | MIT | Instruction candidate generation followed by evaluation/selection | Reimplement a bounded candidate-pool baseline using PPE provider, evaluator, and receipt contracts | Official code uses legacy openai.Completion.create, text-davinci-002 defaults, unpinned dependencies, and an obsolete price estimator. Historical default pool/UCB sizes cannot be treated as a fair current budget | Historical ablation only |

## 3. Installation and Process Isolation

The PPE package currently declares Python 3.11 or later and no runtime
dependencies. Strong optimizer libraries must therefore not be inserted into
the core dependency set merely to run the campaign.

The required integration pattern is:

1. Create one isolated virtual environment, container layer, or controlled
   subprocess environment per third-party optimizer.
2. Freeze the Python interpreter, operating system, exact package versions,
   source commit, dependency lock, hashes, and license inventory.
3. Exchange only a versioned, canonical request/result envelope with PPE. The
   envelope binds the optimizer identity, mutable fields, training/development
   case identities, target and auxiliary models, seeds, stopping rule, budgets,
   raw call ledger, candidate digests, and terminal status.
4. Keep provider access behind the PPE receipt and budget boundary. Upstream
   caches, retry loops, concurrency settings, and telemetry are disabled or
   explicitly bound in the campaign configuration.
5. Run a local no-provider import and protocol smoke test before any authorized
   live call. A successful import is not optimizer qualification.

Intended dependency selections for a future, separately authorized setup are:

- GEPA: gepa 0.1.4 core only unless a registered adapter requires a reviewed
  extra. Its core metadata reported no mandatory third-party dependencies and
  Python 3.10 through 3.14 support.
- MIPROv2: dspy with the optuna extra at stable version 3.2.1. DSPy has a
  substantially larger dependency surface and must remain outside PPE core.
- TextGrad: exact commit pin in its own environment; do not rely on the mismatch
  between the latest release tag and main setup version.
- OPRO, ProTeGi, and APE: no direct installation of the historical research
  stacks. Implement only the minimum algorithm needed over existing PPE
  interfaces, preserving paper semantics and registering every deviation.

No dependency in this section has been installed or executed by this research
task.

## 4. Required Comparator Registration

Before a comparator can see a sealed case, its registration must bind:

- algorithm name, upstream repository, paper, license, package version, commit,
  dependency lock, and environment digest;
- track, mutable fields, input visibility, training/development examples,
  demonstrations, tools, and any reference answers visible to the optimizer;
- target model, proposer/prompt model, reflection/backward/loss model, selector,
  provider, family, snapshot, parameters, retry policy, and cache policy;
- candidate and trial ceilings, batch semantics, concurrency, random seeds,
  stopping rule, and behavior when a hard limit would be exceeded;
- primary normalization budget and hard ceilings for logical candidate-case
  evaluations, physical provider calls, input/output/cached/reasoning tokens,
  money, and wall-clock time;
- canonical candidate digests, every attempted candidate, raw metric values,
  failures, timeouts, malformed results, and externally verifiable receipts.

An adapter that silently exposes additional demonstrations, reference answers,
tools, evaluator feedback, sealed content, or prior outputs changes the system
and invalidates the comparison.

## 5. Fully Charged Budget Contract

Each track freezes one primary normalization budget, normally provider cost or
target-model tokens, and equal hard ceilings for all methods. The minimum
fully-charged ledger is:

method total =
proposal and instruction-generation calls
+ reflection, critique, loss, backward, and update calls
+ target-model rollouts
+ metric, selector, and candidate-validation calls
+ final pairwise judge calls attributed to that method
+ repair, retry, timeout, malformed-output, refusal, and failed calls

The ledger also records human work and final deployment cost caused by longer
instructions, demonstrations, or tool descriptions.

Operational rules:

- GEPA max_metric_calls and MIPROv2 num_trials are optimizer-local controls, not
  campaign totals.
- PPE checks the remaining worst-case call, token, money, and time allowance
  before dispatch. An upstream optimizer's permissible overshoot is not
  permissible campaign overshoot.
- Exceeding any ceiling creates a failed observation. The attempt and its
  partial cost remain in all denominators.
- Batched APIs record both physical provider requests and logical
  candidate-case evaluations; batching cannot erase evaluated work.
- Cached responses are usable only under a frozen digest-bound cache policy.
  Cache hits, misses, original producing receipts, and whether cost is
  re-attributed are explicit campaign fields.
- Provider price snapshots and reported usage are frozen with the evidence.
  An algorithm has no timeless fixed dollar cost.
- Quality-versus-budget curves report the best sealed-set quality available at
  each cumulative fully charged budget, not only the final selected candidate.

## 6. Minimum Judge and Human-Gold Qualification

### 6.1 Deterministic checks first

Schema, execution, citation, safety, environment-state, and other
machine-verifiable requirements are evaluated before subjective comparison.
Hard-check failures receive their preregistered terminal utility and are never
discarded to improve judge agreement.

### 6.2 Locked human gold

The minimum credible protocol is:

1. Maintain separate rubric-development gold and locked qualification gold. No
   judge prompt, parser, optimizer, or threshold may tune on locked labels.
2. Stratify the locked set over all twelve registered domains and representative
   normal, difficult, adversarial, long-context, incomplete-input, tool-failure,
   near-tie, verbosity, and known-critical-defect cases. System identity and
   position are blinded.
3. Obtain at least two independent domain-expert labels per ordinary item.
   Route disagreements to a third blinded expert. Use three independent labels
   up front plus senior adjudication for critical/high-risk items.
4. Preserve each individual label, confidence, rationale, expertise metadata,
   conflicts, timestamps, and the adjudication trail. Do not replace them with
   a coordinator's undocumented consensus.
5. Determine the final sample from preregistered alpha, power, minimum effect,
   ties, clustering, evaluator error, and domain claims.

A pragmatic initial coverage floor is 240 locked pairs, twenty per domain, but
this is not sufficient evidence for a domain-specific superiority claim by
itself. Critical-recall qualification needs its own denominator. With zero
missed critical cases, at least 59 independent human-critical items are required
for a one-sided exact 95% lower confidence bound above 0.95 because
0.05 raised to the power 1/59 is approximately 0.9505. Any miss requires a new
exact-binomial calculation and normally a larger sample. These are planning
floors, not substitutes for the campaign power analysis.

### 6.3 Independent model judges

Qualification requires:

- at least two distinct evaluator model families, preferably across providers,
  neither sharing the principal generation/optimization family;
- as a conservative independence policy, a family used as an optimizer
  proposer, reflection model, TextGrad loss/backward model, or selector does not
  also count as an independent qualifying judge;
- the exact provider, family, model snapshot, operator, rubric hash, judge
  Prompt hash, parser/schema, temperature, retry policy, and receipt authority;
- evaluation of every gold and campaign pair in both A/B and B/A positions;
- frozen tie, abstention, malformed-output, disagreement, and human-escalation
  rules.

Separate calls or caches from the same model are replicates, not independent
judges. With exactly two qualifying judges, the conservative default is to award
a candidate win only when both judges are position-consistent and agree;
otherwise the item is a tie, not_evaluable, or follows the preregistered human
adjudication route. A three-family jury may use a preregistered majority rule,
but critical cases and unresolved disagreements still require expert handling.

Qualification is computed on the same complete locked gold items and reports:

- order consistency;
- exact agreement with the blinded human-majority label;
- a preregistered agreement coefficient such as Krippendorff alpha or weighted
  kappa for the actual label scale;
- critical recall and false-negative rate;
- parser/error/abstention rates;
- matched short-versus-verbose, duplicated-content, position, self-style, and
  critical-flaw-insertion bias probes;
- overall, per-domain, and worst-slice estimates with exact or case-cluster-aware
  uncertainty.

Thresholds are risk-calibrated and frozen in campaign policy rather than copied
from another paper. A judge, model snapshot, Prompt, rubric, parser, or tie-rule
change creates a new configuration and invalidates prior calibration. Missing
identity, either missing A/B order, a zero denominator, or a failed threshold
makes the comparison not_evaluable.

## 7. Current-Machine Fail-Closed Decision

At document creation, the repository states that strong optimizer dependencies
and real provider budget, sealed datasets and independent custodians, experts,
independent machines/operators, separate generation/judge family metadata, and
full-freeze external execution remain outstanding. The invoking Python
environment also did not expose GEPA, DSPy, TextGrad, Optuna, OpenAI, or LiteLLM
as installed packages during the local name-only check. That package observation
is diagnostic only and may change; it is not the main authority.

More importantly, no current evidence bundle proves:

- two available and receipt-bound evaluator families independent of the
  principal generation/optimization family;
- a locked, stratified expert-human gold set with independent labels and
  adjudication;
- preregistered judge thresholds passing on that gold set;
- authorized optimizer/provider budgets and fully charged live receipts.

Therefore:

- optimizer implementation status: not installed and not qualified;
- judge qualification status: not_evaluable;
- human-gold qualification status: not_evaluable;
- Phase 3.5 full-freeze preflight: fail;
- frontier comparison or top-tier claim: prohibited by the evidence contract.

This is a fail-closed evidence conclusion, not a statement that suitable models
or experts cannot later be provisioned. Provisioning them, changing
dependencies, or running paid/external calls requires the campaign's separate
authorization and freeze process.

## 8. Primary Sources

All sources below were retrieved or rechecked on 2026-07-21. Repository activity
and release status are time-sensitive and must be refreshed at campaign freeze.

### GEPA

- [GEPA paper](https://arxiv.org/abs/2507.19457)
- [Official GEPA repository](https://github.com/gepa-ai/gepa)
- [Official GEPA releases](https://github.com/gepa-ai/gepa/releases)
- [GEPA package metadata](https://github.com/gepa-ai/gepa/blob/main/pyproject.toml)

### MIPROv2 and DSPy

- [MIPROv2 paper](https://arxiv.org/abs/2406.11695)
- [Official MIPROv2 API documentation](https://dspy.ai/api/optimizers/MIPROv2/)
- [Official DSPy repository](https://github.com/stanfordnlp/dspy)
- [Official DSPy releases](https://github.com/stanfordnlp/dspy/releases)
- [DSPy package metadata](https://github.com/stanfordnlp/dspy/blob/main/pyproject.toml)

### TextGrad

- [TextGrad Nature paper](https://www.nature.com/articles/s41586-025-08661-4)
- [TextGrad arXiv paper](https://arxiv.org/abs/2406.07496)
- [Official TextGrad repository](https://github.com/zou-group/textgrad)
- [TextGrad setup metadata](https://github.com/zou-group/textgrad/blob/main/setup.py)
- [TextGrad dependency list](https://github.com/zou-group/textgrad/blob/main/requirements.txt)

### OPRO

- [OPRO paper](https://arxiv.org/abs/2309.03409)
- [Official DeepMind OPRO repository](https://github.com/google-deepmind/opro)

### ProTeGi

- [ProTeGi EMNLP paper](https://aclanthology.org/2023.emnlp-main.494/)
- [Official Microsoft LMOps implementation](https://github.com/microsoft/LMOps/tree/main/prompt_optimization)
- [Official implementation provider utility](https://github.com/microsoft/LMOps/blob/main/prompt_optimization/utils.py)

### APE

- [APE ICLR paper](https://openreview.net/forum?id=92gvk82DE-)
- [Official APE repository](https://github.com/keirp/automatic_prompt_engineer)
- [APE setup metadata](https://github.com/keirp/automatic_prompt_engineer/blob/main/setup.py)
- [APE provider implementation](https://github.com/keirp/automatic_prompt_engineer/blob/main/automatic_prompt_engineer/llm.py)

### Judge and human-gold design

- [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena, NeurIPS 2023](https://proceedings.neurips.cc/paper_files/paper/2023/hash/91f18a1287b398d378ef22505bf41832-Abstract-Datasets_and_Benchmarks.html)
- [Replacing Judges with Juries, PoLL](https://arxiv.org/abs/2404.18796)
- [Confidence intervals for evaluation with limited samples, ICML 2025](https://proceedings.mlr.press/v267/bowyer25a.html)
- [OpenAI GDPval grading methodology](https://evals.openai.com/gdpval/grading)
