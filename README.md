# Prompt Performance Engine

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

[![CI](https://github.com/selinyi123/prompt-performance-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/selinyi123/prompt-performance-engine/actions/workflows/ci.yml)

Prompt Performance Engine is a clean successor to the existing Universal Prompt
Optimizer and Prompt Evidence-Based Audit Engine experiments.

The primary user outcome is simple:

1. paste an original Prompt;
2. receive the complete optimized Prompt first;
3. optionally receive an audit and an executable comparison package;
4. make only evidence-bounded quality claims.

Package contract `0.4.0` includes deterministic audit and evidence enforcement
plus the versioned frontier schemas, canonical artifact I/O, fail-closed
preflight, budgeted execution host, report/claim validation, and deterministic
offline replay. Those local implementations do not supply real independent
authorities or execute an external campaign. The current frontier machine result
is therefore `not_evaluable`, not `top_tier_scoped`.

## Quick Check

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
python -m unittest discover -s tests -v
python scripts/validate_release.py
```

Keep that `PYTHONPATH` setting in the same shell when running source-checkout
CLI examples below. An installed wheel does not require it.

The wheel is self-contained: its version, optimizer Prompt, and domain
profiles are installed with the package rather than read from the source tree.

## Compile an Optimization Request

```powershell
python -m prompt_performance_engine compile path\to\original-prompt.txt
```

The command emits a JSON envelope containing:

- the optimizer system Prompt;
- the inert source Prompt;
- the resolved domain profile;
- explicit optimization controls;
- an `E0` evidence status.

## Run the Optimization Kernel

The current release ships a deterministic mock adapter for integration testing:

```powershell
python -m prompt_performance_engine optimize original-prompt.txt `
  --mock-response response-from-model.md `
  --artifact optimization-artifact.json
```

The response file must contain one JSON object with exactly one
`optimized_prompt` string field. The CLI prints that decoded Prompt, not the
model transport wrapper. The generated artifact includes the source Prompt so
its E1 audit can be replayed; handle artifacts as sensitive application data.

Real OpenAI, external-command, and Codex optimization commands accept
`--candidate-count 1..5`. Values above one generate independent candidates,
invoke a dedicated selector, and record every candidate, hash, selected index,
selection method, and selector-response hash in the artifact. For example:

```powershell
python -m prompt_performance_engine optimize-codex original-prompt.txt `
  --model gpt-5.5 --candidate-count 3 `
  --artifact optimization-artifact.json
```

Multiple candidates increase cost and do not by themselves raise the evidence
level or prove higher quality.

Known provider quota failures are emitted as one structured JSON object on
standard error with exit code `75` and `retryable: true`; sanitized non-quota
adapter failures use exit code `1`. Neither path prints a Python traceback.

## Run with OpenAI

```powershell
python -m prompt_performance_engine optimize-openai original-prompt.txt `
  --model YOUR_PINNED_MODEL `
  --artifact optimization-artifact.json
```

The adapter uses the Responses API, reads `OPENAI_API_KEY` from the environment,
and records sanitized response metadata and usage. No credential is stored in
the artifact.

## Run with an External Model Command

```powershell
python -m prompt_performance_engine optimize-command original-prompt.txt `
  --permissions tool-permissions.json `
  --artifact optimization-artifact.json `
  --command path\to\model-command.exe
```

The command receives JSON on standard input and returns JSON containing
`output_text`; that string must itself be the exact `optimized_prompt` JSON
transport used by other model adapters. Shell execution is disabled and the
executable must be allowlisted.

## Audit a Prompt

```powershell
python -m prompt_performance_engine audit path\to\prompt.txt
```

To verify that an optimized Prompt preserves source template variables:

```powershell
python -m prompt_performance_engine audit optimized.txt --source original.txt
```

## Create an Immutable Manifest

```powershell
python -m prompt_performance_engine manifest artifact.json report.json `
  --root . --output manifest.json
python -m prompt_performance_engine verify-manifest manifest.json --root .
```

## Benchmark and Human Review

```powershell
$sandboxImage = "python:3.13-alpine@sha256:YOUR_VERIFIED_DIGEST"
python -m prompt_performance_engine validate-benchmark benchmark\catalog-60.json
python scripts\run_codex_benchmark.py --domains software_engineering `
  --sandbox-image $sandboxImage

# Three complete, configuration-compatible runs are the diagnostic baseline.
python scripts\run_codex_benchmark.py --replicate-id run-a `
  --sandbox-image $sandboxImage `
  --output-directory artifacts\benchmark-run-a
python scripts\run_codex_benchmark.py --replicate-id run-b `
  --sandbox-image $sandboxImage `
  --output-directory artifacts\benchmark-run-b
python scripts\run_codex_benchmark.py --replicate-id run-c `
  --sandbox-image $sandboxImage `
  --output-directory artifacts\benchmark-run-c
python -m prompt_performance_engine aggregate-benchmark-replicates `
  artifacts\benchmark-run-a artifacts\benchmark-run-b artifacts\benchmark-run-c `
  --output evidence\benchmark-replicates.json
python -m prompt_performance_engine validate-benchmark-replicates `
  evidence\benchmark-replicates.json `
  artifacts\benchmark-run-a artifacts\benchmark-run-b artifacts\benchmark-run-c
```

These CLI aggregation and validation commands deliberately run without a trust
adapter. They can reproduce a diagnostic report from its source directories,
but cannot grant E3 authority. The current CLI also cannot create or aggregate
an authoritative human-review workflow, because doing so would silently trust
self-reported provider and reviewer data.

An authority-bearing host integration must call the Python API and inject both
trust boundaries: a `ModelCallReceiptVerifier`, which independently verifies
each provider call against the bound request/response/context digest and returns
a unique canonical receipt digest, and a `ReviewerSubmissionVerifier`, which
attests reviewer identity, qualification/independence, and the bound packet and
submission. Missing, invalid, or reused receipts fail closed. A local SHA-256
value is an integrity identifier, not a signature or a trust root.

The human-review plan contains `replicate_report`, the same three
`run_directories`, the selected evaluation paths, and every packet/key/submission
triple. Paths are relative to the plan file and may not escape its directory.
Every packet/key pair uses `balanced_round_robin_hmac_sha256_v3`. A fresh
256-bit coordinator key blinds review IDs and A/B assignments; the public
packet carries only its SHA-256 commitment and sample size, while the sampling
seed, probe count, blinding key, source case identity, probe markers, and
optimized labels remain in the coordinator-only key. Aggregate authority hashes
remain public integrity commitments, not reviewer identity data. Authority
validation rebuilds both artifacts and requires
every reviewer to complete every probe consistently, make at least one base A
and one base B selection, cover the same 24 or more base cases, carry a unique
trusted reviewer receipt, reach direct reviewer consensus on every case, and
confirm more direct human wins than losses. Coordinator-only adjudications are
reported for diagnostics but never qualify E4 or count toward improvement.
Because a static packet still contains repeated output content for a
probe pair, the review coordinator remains responsible for controlled
presentation when resistance to deliberate pair recognition is required.

R06 image review has separate trust boundaries. Each visual-review packet/key
pair uses `balanced_hmac_sha256_v2` and a newly generated 256-bit secret. The
public packet protocol carries only the key commitment; the secret, seed,
source-to-delivery mapping, and optimized labels remain in the private key.
Stable-release validation reloads the strict `visual-review-plan`, replays every
A/B assignment and opaque delivery path, and rebuilds the complete report with
an `ImageGenerationReceiptVerifier` and a
`VisualReviewerSubmissionVerifier`. Generation and reviewer receipts must each
be valid and unique. The ordinary `aggregate-visual-review` CLI injects neither
verifier, so its report is useful for diagnostics but cannot satisfy R06.

`run_codex_benchmark.py` is not a local smoke test: it invokes the authenticated
Codex CLI, sends benchmark payloads to the configured model, writes durable
artifacts, and may consume substantial provider quota. Review the benchmark
inputs and choose an explicit output directory before running it.

The `cross-domain-60-v2` catalog contains 12 domains, 60 cases, and 12
adversarial cases.
Payload-dependent cases must include their actual evidence packet, source
document, schema, localization content, or simulated tool trace; abstract task
descriptions fail validation. Marketing cases must include a product brief,
verified facts, audience, channel, CTA, and evidence boundary. Definitions
alone are not performance evidence.

Each benchmark run also stores a canonical `benchmark-definition.json` snapshot
resolved from the selected catalog. E3 source replay recomputes its digest and
requires every domain source Prompt, case set, and case hash to match that
snapshot; repeating a claimed definition hash in the manifest and summary is
not sufficient.

The Codex runner creates a configuration-locked `run-manifest.json`, durable
call caches, per-domain artifacts, and a summary. Protocol v26 binds the
benchmark definition, optimizer Prompt hash, domain-profile hash, package
version, the complete Python implementation and runner hash, Python runtime,
model, and supported runtime controls. Quota failures are written as hashed,
retryable evidence. Runs containing executable software cases also bind the
digest-pinned Docker image into that immutable configuration. v17 added
source-language and scope preservation, suppressed
unrequested variants and placeholders, and fixed measured hard-check false
positives. v18 narrows agent approval behavior and restores concrete,
audience-specific marketing depth. v19 adds concrete marketing payloads and
binds readiness to the exact benchmark suite, definition hash, and run
manifest. v20-v22 add explicit objection, segment, channel, proof-relationship,
and deceptive-request handling for marketing, plus rejection-aware hard checks.
v23 adds artifact-bound multi-candidate selection evidence across CLI and API.
v24 binds the selector to the same domain guardrails, required behaviors,
forbidden changes, recovered contract, and architecture used for generation.
v25 assigns distinct, recorded strategies to multi-candidate generation rather
than relying on repeated sampling of the same optimization request. v26 caps
every single run at E2 and adds hash-verified aggregation for at least three
uniquely identified, configuration-compatible runs. It validates every manifest,
summary, optimization artifact, Prompt-to-evaluation binding, domain evaluation,
case identity, model-call provenance, consensus outcome, stability metric, and
derived release gate. Detached reports are structure-only until validation
reloads the three source run directories and reproduces the report exactly.
Provider/model names, response IDs, status, and usage are validated provenance
signals, but they are self-reported and cannot authorize E3. E3 additionally
requires a trusted `ModelCallReceiptVerifier` to verify every bound call and
return unique receipts. The aggregator rejects reused call identities or
receipts and duplicate semantic Prompt/output/judge payloads, so changing only
hashes, elapsed time, or other non-semantic metadata cannot turn a copied run
into an independent replicate. Without the verifier, the report remains a
diagnostic below E3 even when the provider-looking metadata is complete.
Its default is one optimization candidate.
`--candidate-count 2..5` is experimental and does not by itself raise the
evidence level.
The fallback output directory is derived from the active protocol version
(`artifacts/codex-benchmark-v26` for v26), but evidence runs should continue to
choose a fresh explicit directory for every replicate.

All five software cases have case-owned verification. Release-grade evidence
for four cases extracts narrowly permitted Python definitions and runs trusted
hidden harnesses in digest-pinned Docker containers. The container backend
disables networking, uses a read-only root filesystem, drops all capabilities,
enables `no-new-privileges`, runs as a non-root user, and enforces PID, memory,
and CPU limits. The migration case validates an exact JSON compatibility
contract without executing candidate code. Every executable software check now
requires an explicit, policy-verified `DockerSandbox`; missing Docker, a mutable
image reference, or failed live policy inspection stops the check before
candidate execution. The R05 code-evidence command additionally runs isolation,
timeout, and memory probes before re-executing any candidate. There is no
production host-subprocess fallback. Stable-release validation also requires a
strict `code-execution-plan` that binds the source evaluation, report ID, and
immutable sandbox image. The authority validator must receive an explicit live
`DockerSandbox`, rerun the probes and all eligible case checks, and reproduce
the complete evidence report exactly. A detached report or offline self-hashed
facts cannot satisfy R05.

Create readiness evidence directly from a validated software evaluation. The
command re-verifies all five optimized outputs with the current verifier: four
Python outputs execute inside Docker, while the migration output is checked by
its formal JSON compatibility contract. It records the verifier implementation
hash:

```powershell
python -m prompt_performance_engine build-code-evidence `
  artifacts\codex-benchmark-v14\software_engineering\evaluation.json `
  --report-id codex-software-exec-v14-gpt-5.5 `
  --sandbox-image python:3.13-alpine@sha256:YOUR_VERIFIED_DIGEST `
  --output evidence\code-execution.json
```

See `SOFTWARE-SANDBOX.md` for the enforced boundary and verification probes.

## Local Service

```powershell
$env:PROMPT_PERFORMANCE_SERVICE_TOKEN = "<strong-random-token>"
python -m prompt_performance_engine serve-openai `
  --model YOUR_PINNED_MODEL `
  --auth-token-env PROMPT_PERFORMANCE_SERVICE_TOKEN
```

The service is local-only, persistent, idempotent, restart-safe, and exposes
`/health`, `/metrics`, `/v1/optimize`, `/v1/jobs/{id}`, and
`/v1/artifacts/{id}`. Direct non-loopback binding is rejected. Omitting
`--auth-token-env` explicitly selects unauthenticated loopback-only mode. Once
the option is supplied, the named environment variable must exist and contain
a non-whitespace bearer token; a missing, empty, or whitespace-only value
aborts startup instead of falling back to local-only mode.

## Assess Stable-Release Readiness

```powershell
python -m prompt_performance_engine assess-readiness `
  evidence\readiness-manifest.json `
  --require-complete `
  --output evidence\readiness-report.json
python -m prompt_performance_engine validate-readiness `
  evidence\readiness-report.json `
  evidence\readiness-manifest.json
```

The readiness manifest's `authority_sources` must bind the three benchmark run
directories plus `human_review_plan`, `code_execution_plan`, and
`visual_review_plan`. Authority validation reloads those sources and
reconstructs the E3, E4, R05, R06, and readiness reports with the same trusted
model-call, human-review, image-generation, and visual-review receipt verifiers
and an explicit live `DockerSandbox`. The standalone CLI does not inject these
authorities and therefore correctly remains diagnostic/incomplete. A detached
self-consistent JSON report is not an authority artifact. Custom readiness
evidence uses an exact kind-specific facts contract; independent reproduction
machine/operator identities are lowercase 64-character SHA-256 digests. The
readiness gate checks ten mandatory evidence-backed requirements. Missing
software execution, actual image review, expert review, independent
reproduction, defect closure, or claims evidence blocks stable completion even
when text benchmarks pass.

## Project Documents

- `PRODUCT-SPEC.md`: final product definition and scope.
- `ARCHITECTURE.md`: component boundaries and data flow.
- `ROADMAP.md`: version-by-version implementation sequence.
- `ACCEPTANCE-CRITERIA.md`: evidence required for stable completion.
- `MIGRATION-PLAN.md`: what is retained or rejected from prior projects.
- `DECISIONS.md`: binding architecture decisions.
- `IMPLEMENTATION-STATUS.md`: current gates and evidence.
- `CHANGELOG.md`: behavior delivered by each release.
- `SECURITY.md`: supported deployment and repository-grounded risks.
- `SOFTWARE-SANDBOX.md`: executable-evaluation isolation contract.
- `IMAGE-REVIEW-PROTOCOL.md`: actual image generation and blind visual-review
  evidence contract.
- `MIGRATION.md`: legacy Prompt and audit import.
- `WORLD-CLASS-DELIVERY-PLAN.md`: remaining architecture, implementation, and
  evidence work required for stable completion.
- `QUALITY-GATE-SPEC.md`: scoped frontier-performance definition, strong
  baselines, statistical validity, robustness, and quality-cost-latency gates.
- `FRONTIER-EVIDENCE-CAMPAIGN.md`: ordered authorization, sealed-data,
  comparator, execution, judge, statistics, human-review, and reproduction plan.

## Honest Status

Current package contract: `0.4.0`. Current frontier machine result:
`not_evaluable`.

The frontier contract, preflight, budgeted execution host, report/claim
validation, and offline replay are implemented and tested locally. Test doubles
and locally generated hashes do not establish independent provider, custodian,
judge, human, execution, or replay authority. No authority-bearing external
frontier campaign or three-independent-operator replay has been executed.

The working tree has contract-tested later-stage capabilities. On 2026-06-15,
protocol v16 completed all 60 cases across all 12 domains with `gpt-5.5` at low
reasoning effort: 30 wins, 9 ties, and 21 losses, for 15% net improvement. It
used 240 real model calls. The aggregate gate remained false because not every
domain passed and the v16 evaluator reported three critical regressions, one
fatal flaw, and five optimized hard failures.

Protocol v17 fixes five confirmed measurement defects in those hard failures:
three rejected or warning-context phrases were treated as positive claims, one
case-sensitive required-text check rejected equivalent uppercase JSON, and the
restricted software verifier omitted safe literal constants used by an
otherwise runnable function. Rechecking the stored v16 outputs with the v17
verifier yields zero hard failures and zero hard regressions, but this does not
replace a fresh matched v17 model run.

A four-domain v17 diagnostic run then showed that the generic proportionality
rules were insufficient: education held at 3W/0T/2L and image generation
improved to 2W/0T/3L, but agents and marketing each fell to 0W/0T/5L. Protocol
v18 therefore removes invented approval gates and fixed process templates from
agent prompts, and requires marketing prompts to preserve concrete audience
workflows, deliverable depth, and supplied CTAs. A 42-call v18 diagnostic then
produced agents 3W/2T/0L with a passing domain gate, but marketing remained
0W/0T/5L. The remaining marketing failure is now treated as a benchmark-brief
and domain-strategy defect rather than a reason to add more global ceremony.

Protocol v19 replaces all five abstract marketing tasks with concrete
evidence-bearing briefs and upgrades the release benchmark to
`cross-domain-60-v2`. Readiness now rejects the old v1 summary as stale.
Accordingly, R03 is partial again and readiness is 4 of 10 mandatory gates
until fresh v26 repeated-run evidence passes the release gates. The first
concrete v19 marketing run
produced 1W/0T/4L with no optimized hard failures; v20 also produced 1W/0T/4L
and exposed a hard-check false positive on an explicitly rejected scarcity
claim. The completed v21 real-provider run produced 1W/1T/3L; its apparent hard
regression was another rejection-heading measurement false positive. After
that fix, the v22 three-candidate diagnostic produced 2W/0T/3L with zero hard,
critical, or fatal regressions. This improves on the single-candidate diagnostic
but still fails the domain gate.
The v24 full-contract selector diagnostic improved again to 2W/1T/2L with zero
hard, critical, or fatal regressions, but still lacked the required net win.
v25 therefore diversifies candidates across fidelity, coverage, channel-fit,
adversarial, and balanced strategies. Its first three-candidate marketing run
produced 2W/0T/3L: the selector chose the concise-channel strategy, and the
result lost on landing-page completeness, segmented-framework depth, and
existing-customer continuity. Candidate diversity is implemented, but stable
selection improvement is not yet proven. The first image run has all 10
matched assets, but no qualified independent visual-review submissions. No
external image-generation or visual-review receipt authority was exercised in
this local run.
Independent expert review and three-machine reproduction are also missing.
The Docker-only implementation and mocked policy-contract tests pass locally,
but no live Docker R05 evidence artifact was produced in this local run because
no configured immutable test image/daemon was supplied. Live isolation and
independent reproduction remain pending. Therefore the project does not claim stable
v1.0, production certification, universal best, or award equivalence.
