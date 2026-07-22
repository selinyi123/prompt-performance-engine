# World-Class Delivery and Implementation Plan

## 1. Executive Determination

The project is already usable as an evidence-conscious local Prompt optimizer,
but it is not yet a stable cross-domain quality system. Its strongest completed
assets are the optimization kernel, static audit, artifact contracts, provider
adapters, persistent local service, benchmark catalog, blind judging, and
human-review workflow.

The remaining distance is dominated by external and domain-grounded evidence,
not by adding more generic Prompt wording. Stable completion requires actual
60-case execution, executable software verification, rendered image review,
qualified independent reviewers, independent-machine reproduction, and
artifact-backed defect and claim closure.

Two statuses must remain separate:

- `daily-use status`: limited beta for supervised local use;
- `stable-release status`: incomplete until all ten readiness requirements pass.

Performance is a third independent axis. `QUALITY-GATE-SPEC.md` defines the
additional sealed-data, strong-baseline, uncertainty, robustness, safety, and
quality-cost-latency evidence required before any scoped frontier claim.

## 2. Binding Definition of Done

The release is complete only when the machine-generated readiness report marks
R01 through R10 as `passed` with no evidence errors:

1. release validators and behavior tests pass;
2. CLI, API, service, package installation, and documentation are verified;
3. at least three unique, configuration-compatible, receipt-verified
   real-provider runs each cover at least 60 cases across all 12 domains and
   reproduce the v26 aggregate from their bound source directories;
4. every domain passes, net improvement is at least 10%, and critical and fatal
   regressions are zero;
5. all eligible software cases receive sandboxed executable verification and
   the complete R05 report is rebuilt from a bound `code-execution-plan` with a
   matching live `DockerSandbox`;
6. all image cases generate actual images, receive qualified visual review and
   unique external receipts, and the complete R06 report is rebuilt from a
   bound `visual-review-plan`;
7. at least three qualified independent reviewers complete blind stratified
   review with direct consensus; any coordinator adjudication remains diagnostic;
8. installation and replay pass on three independent machines and operators;
9. no open P0 or P1 defect remains;
10. all public quality claims are bound to current immutable evidence.

No file count, version label, test count, benchmark definition, or model
self-score can substitute for these conditions.

## 3. Current Evidence Baseline

Implemented and locally tested:

- 12 domain profiles and 60 benchmark cases, including 12 adversarial cases;
- deterministic static audit and E0/E1 evidence boundaries;
- matched A/B execution, blind dual judging, and hard-check precedence;
- OpenAI, external-command, and authenticated Codex adapters;
- blind human-review packets, bias probes, diagnostic adjudication, and E4 logic;
- persistent local HTTP service and replayable artifacts;
- wheel build and clean-environment installation checks;
- case-owned machine-verification implementation for all five software cases;
- strict R05/R06 source-plan replay and readiness authority injection points;
- a passing E2 software-domain run under protocol v14.

Not yet proven:

- a passing real-provider 60-case benchmark beyond the current 5-case domain;
- wins greater than losses in every domain;
- zero fatal flaws across the release benchmark;
- independent reproduction of the local OS/container sandbox evidence;
- live R05 report reproduction from the code-execution plan (four live Docker
  tests may be skipped when no immutable integration image/daemon is supplied);
- qualified, externally receipt-verified review of the completed matched image
  generation and exact R06 visual-review-plan replay;
- completed independent expert review;
- three-machine independent reproduction;
- a fully artifact-bound public-claim audit.

## 4. Product Boundary and User Journeys

The product must optimize an inert source Prompt and return a directly copyable
optimized Prompt first. Optional outputs are audit findings, comparative
evaluation, evidence packages, and replay manifests.

Primary journeys:

- paste a Prompt and receive a complete optimized Prompt;
- select or infer a domain without losing source requirements;
- run a matched original-versus-optimized comparison;
- inspect deterministic failures before subjective judging;
- create a blind human-review packet;
- package all artifacts for reproduction;
- assess release readiness without relying on narrative claims.

The product does not guarantee universal superiority, award equivalence, or
correctness outside the recorded models, cases, tools, and environments.

## 5. Target Architecture

The target architecture has six independently testable layers:

1. `compiler`: inert input analysis, domain resolution, and contract recovery;
2. `optimizer`: candidate generation, parsing, bounded repair, and audit;
3. `evaluation`: matched execution, deterministic checks, blind judging, and
   aggregation;
4. `domain verification`: software execution, image rendering review, and
   domain-specific validators;
5. `evidence`: immutable artifacts, human review, reproduction, defects, and
   claims;
6. `delivery`: CLI, local API, package, CI, release manifests, and readiness
   gate.

Every quality claim must trace from delivery through evidence to the exact
evaluation and generation artifacts that support it.

## 6. Optimization Kernel Work

The optimization kernel needs targeted improvement driven by failed cases:

- classify benchmark losses by intent loss, constraint loss, excess process,
  target-surface contamination, or unverifiable instruction;
- add regression fixtures before changing optimizer wording;
- preserve requested deliverables while removing only harmful constraints;
- keep tool-aware behavior conditional on the actual target surface;
- retain single-candidate as the release default until multi-candidate runs
  pass domain gates; the first marketing three-candidate diagnostic improved
  from one win to two but still lost three of five cases;
- persist every candidate, candidate hash, selected index, selector method,
  and selector-response hash so tournament behavior is independently auditable;
- bind candidate selection to the same domain guardrails, required behaviors,
  forbidden changes, recovered contract, target surface, and architecture used
  to generate candidates;
- diversify multi-candidate generation through recorded fidelity, coverage,
  channel-fit, adversarial, and balanced strategies instead of repeated
  identical sampling;
- prohibit candidate selection from using the same unblinded signal that later
  judges the result;
- cap every component benchmark run at E2, regardless of case count;
- require at least three unique, configuration-compatible complete runs before
  E3, with hash-linked manifests, summaries, evaluations, per-case consensus,
  exact-agreement stability metrics, and unique externally verified call
  receipts bound to every request, response, and evaluation context;
- recompute all repeatability-report gates so rehashing edited derived fields
  cannot manufacture an E3 claim;
- require authority validators to reload all readiness authority sources:
  benchmark run directories, human-review plan, code-execution plan, and
  visual-review plan. Detached E3/E4/R05/R06 reports must be reproduced exactly
  with the matching receipt verifiers and live Docker sandbox.

Files:

- `prompts/optimizer.md`
- `src/prompt_performance_engine/analysis.py`
- `src/prompt_performance_engine/compiler.py`
- `src/prompt_performance_engine/runtime.py`
- `tests/test_compiler.py`
- `tests/test_runtime.py`

## 7. Benchmark and Evaluation Work

The release benchmark must use one immutable configuration and record:

- exact model identity and provider;
- supported generation settings, with unsupported controls explicitly null;
- source and optimized Prompt hashes;
- all outputs, judge mappings, judge decisions, usage, retries, and failures;
- deterministic hard-check results;
- per-domain and aggregate gates;
- actual model-call accounting.

The runner must support resumable execution without mixing configurations.
Failed runs remain diagnostic artifacts. A later successful run does not erase
them.

Files:

- `scripts/run_codex_benchmark.py`
- `src/prompt_performance_engine/codex_evaluation.py`
- `src/prompt_performance_engine/evaluation.py`
- `src/prompt_performance_engine/domain_checks.py`
- `benchmark/catalog-60.json`
- `tests/test_codex_benchmark_runner.py`
- `tests/test_evaluation.py`

## 8. Software Execution Verification

Text-based judges are insufficient for code-producing tasks. The software
verification layer must:

- extract only the requested executable artifact;
- execute inside a resource-limited, network-disabled sandbox;
- use case-owned tests and hidden edge cases;
- capture stdout, stderr, exit status, duration, and resource limits;
- reject forbidden imports, filesystem access, subprocesses, and dynamic code;
- distinguish compile, behavior, security, and specification failures;
- make deterministic failures authoritative over model judges.

The current implementation covers all five cases with four restricted Python
harnesses in a fixed Docker sandbox and one formal migration contract. It
rejects imports, filesystem APIs, subprocesses, dynamic code, and dunder access
before execution. Docker policy inspection and active probes verify the
required OS boundary. Stable R05 additionally requires a strict
`code-execution-plan`, an explicit live `DockerSandbox` whose immutable image
matches the plan, and exact report reconstruction after rerunning isolation,
timeout, memory, and case-owned checks. A detached report or offline self-hash
cannot substitute for that replay.

Implemented files:

- `src/prompt_performance_engine/software_execution.py`
- `src/prompt_performance_engine/software_sandbox.py`
- `src/prompt_performance_engine/software_evidence.py`
- `schemas/code-execution-plan.schema.json`
- `tests/test_software_execution.py`
- `tests/test_software_sandbox.py`
- `tests/test_software_evidence.py`

## 9. Image Generation and Visual Review

Image-domain evidence requires generated pixels, not text-only Prompt review.
The image pipeline must:

- generate images from original and optimized Prompts under matched settings;
- preserve provider, model, seed, dimensions, safety status, and asset hashes;
- blind image identities and randomize presentation order;
- score brief adherence, composition, legibility, artifact rate, and production
  usability;
- use at least three qualified visual reviewers for release evidence;
- generate a fresh 256-bit secret for each `balanced_hmac_sha256_v2` packet/key
  pair, expose only its commitment in the public protocol, and retain the
  secret, seed, source mapping, and optimized labels in the private key;
- require unique generation and reviewer-submission receipts from host-supplied
  `ImageGenerationReceiptVerifier` and
  `VisualReviewerSubmissionVerifier` implementations;
- reload the strict `visual-review-plan`, replay every blind mapping, and
  reproduce the complete R06 report exactly;
- retain rejected and safety-blocked generations;
- keep aesthetic preference separate from objective brief violations.

Implemented files:

- `src/prompt_performance_engine/image_review.py`
- `schemas/image-generation-manifest.schema.json`
- `schemas/visual-review-plan.schema.json`
- `schemas/visual-review-*.schema.json`
- `tests/test_image_review.py`

## 10. Expert Human Review

Human review must be independent, blind, stratified, and conflict-resolved.
Reviewer qualification and conflicts of interest must be recorded without
publishing personal identifiers.

Minimum release coverage:

- three reviewers;
- at least 24 stratified cases;
- required coverage for creative design, research synthesis, and business
  strategy;
- at least two reversed position-bias probes per reviewer, complete consistency,
  non-degenerate base A/B selections, and agreement metrics;
- direct reviewer consensus for all release-counted cases; coordinator-only
  adjudication may be reported diagnostically but cannot qualify E4;
- more direct human wins than losses;
- unique trusted submission receipts binding reviewer identity, qualification,
  independence, packet, and submission;
- explicit limitations and reviewer qualification evidence.

Implemented files:

- `src/prompt_performance_engine/human_review.py`
- `schemas/human-review-*.schema.json`
- `tests/test_human_review.py`

## 11. Reproduction and Release Operations

Independent reproduction is not the same as another local virtual environment.
Each reproduction record must include privacy-preserving machine and operator
identifiers encoded as lowercase 64-character SHA-256 digests, platform details,
package checksum, commands, install result, replay result, and produced artifact
hashes.

Release operations must cover:

- Python 3.11, 3.12, and 3.13 CI;
- wheel and source distribution;
- clean CLI smoke test;
- local API and restart-recovery test;
- replay of a fixed evaluation fixture;
- three independent machines and operators;
- signed release manifest and checksums.

Implemented contract and verification files:

- `src/prompt_performance_engine/frontier_replay.py`
- `schemas/frontier-reproduction-plan.schema.json`
- `schemas/frontier-replay-report.schema.json`
- `schemas/frontier-reproduction-set.schema.json`
- `.github/workflows/ci.yml`
- `FRONTIER-EVIDENCE-CAMPAIGN.md`
- `tests/test_frontier_replay.py`

## 12. Security, Privacy, and Claim Governance

Security requirements:

- source Prompts remain inert throughout compile and audit;
- provider credentials never enter logs or artifacts;
- external commands remain allowlisted and shell-free;
- generated code runs only in an isolated execution environment;
- image and review artifacts follow retention and privacy rules;
- remote service exposure requires authenticated TLS termination;
- critical threat-model findings block release.

Claim requirements:

- `optimized_candidate` is the default ceiling;
- `verified_improvement` is scoped to recorded evidence;
- `stable_v1` is available only when R01 through R10 pass;
- award-equivalence and universal-best claims remain prohibited.

## 13. Evidence and Traceability Contracts

The readiness system introduced in the current implementation batch adds:

- `readiness-evidence.schema.json` for typed, hash-linked evidence reports;
- `readiness-manifest.schema.json` for bounded artifact references;
- `readiness-report.schema.json` for deterministic gate results;
- `assess-readiness` and `validate-readiness` CLI commands;
- path-containment, file-hash, and internal-hash validation;
- four explicit readiness `authority_sources`: benchmark run directories,
  human-review plan, code-execution plan, and visual-review plan;
- source reconstruction for R05/R06 with an explicit live sandbox and the two
  image-specific receipt authorities;
- a fail-closed `--require-complete` release gate.

Native benchmark and human-review artifacts retain their own schemas and hashes.
Custom operational, code, image, expert, reproduction, defect, and claims
reports use the shared exact readiness evidence envelope with kind-specific
fact fields and scalar types.

## 14. Phased File-Level Implementation

### Phase A: Readiness Governance

Status: implemented in the current batch.

- add machine-readable R01-R10 gates;
- add immutable evidence references;
- add CLI assessment and validation;
- add fail-closed tests;
- document the claim ceiling.

### Phase B: Complete Software Verification

Status: implementation and mocked policy-contract tests complete; live local and
independent reproduction pending.

- completed four restricted Python execution validators;
- completed the formal rolling-migration validator;
- completed authoritative hard-check integration;
- completed evaluation-derived code-execution evidence;
- completed Docker-only execution and fail-closed policy implementation;
- completed strict `code-execution-plan` replay and exact R05 report comparison;
- pending a live run against a configured immutable image/daemon to produce R05
  isolation evidence; four integration tests may skip without that environment
  and their skip is not evidence;
- pending independent-machine and independent-operator reproduction.

### Phase C: Actual Image Evaluation

- completed matched generation records and strict PNG verification;
- completed `balanced_hmac_sha256_v2` blinded packets with a fresh per-packet
  256-bit secret, public commitment, and private replay key;
- completed strict visual-review-plan loading, submission validation, mapping
  replay, and exact report comparison;
- generated and validated all 10 assets for the first five-case matched run;
- generated three independently randomized blind-review packets;
- pending three qualified independent visual-review submissions and unique
  externally verified generation/reviewer receipts;
- rebuild qualified R06 evidence from the plan in an authority-bearing host;
  ordinary CLI aggregation remains diagnostic.

### Phase D: Full Real Benchmark

- completed concrete payload hardening for 24 source- or tool-dependent cases;
- completed fail-closed validation against abstract placeholder tasks;
- completed CLI/API exposure and artifact-bound evidence for 1-5 candidate
  generation and automatic selection;
- completed a five-case three-candidate marketing diagnostic at 2W/0T/3L;
- completed a full-contract selector diagnostic at 2W/1T/2L with no hard,
  critical, or fatal regressions;
- completed a differentiated-candidate diagnostic at 2W/0T/3L; treat the
  regression as evidence that repeated matched runs and selection diagnostics
  are required before more optimizer tuning;
- run all 12 domains and 60 cases in at least three v26 replicates;
- integrate an independently trusted `ModelCallReceiptVerifier`; provider-looking
  metadata and locally generated hashes remain diagnostic only;
- aggregate and validate all 180 or more matched observations before any E3
  claim;
- analyze every loss and fatal flaw;
- add regression tests before optimizer changes;
- rerun under a new immutable configuration.

### Phase E: Independent Evidence

- complete expert review;
- integrate a trusted `ReviewerSubmissionVerifier` and verify unique bound
  receipts for every reviewer;
- integrate trusted `ImageGenerationReceiptVerifier` and
  `VisualReviewerSubmissionVerifier` implementations and verify every R06
  source receipt while replaying the visual-review plan;
- complete three-machine reproduction;
- close P0/P1 defects;
- generate claim audit and final readiness report.

## 15. Risks, Exit Criteria, and Immediate Sequence

Primary risks:

- optimizing against the judge rather than user outcomes;
- treating same-model judges as independent;
- using text proxies for code or image quality;
- confusing implemented workflow with completed evidence;
- benchmark overfitting;
- leaking private prompts or reviewer identity;
- declaring completion from a favorable partial run.

Immediate sequence:

1. finish the immutable v0.4.0 release candidate, then authorize campaign roles,
   dependencies, providers, budgets, retention, and external reproduction;
2. construct and independently freeze the sealed claim, safety, and external
   reproduction sets, their source commitment, licenses, contamination checks,
   utility anchors, and power analysis;
3. register the candidate and all five required baseline kinds, qualify two or
   more independent Judge families against locked human gold, and freeze every
   model, rubric, parser, budget, route, and stopping rule;
4. pass the full-freeze preflight before revealing any sealed payload, then run
   the complete execution, judging, statistics, safety, robustness, and
   efficiency matrix without changing the frozen configuration;
5. complete E4, R05, and R06 authority replay, three independent offline
   reproductions, the separate unseen-set rerun, defect closure, claim audit,
   and `assess-readiness --require-complete`.

The project may be called stable only when the final command exits successfully
against a complete, immutable evidence bundle.
