# Prompt Performance Engine

[![CI](https://github.com/selinyi123/prompt-performance-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/selinyi123/prompt-performance-engine/actions/workflows/ci.yml)

Prompt Performance Engine is a clean successor to the existing Universal Prompt
Optimizer and Prompt Evidence-Based Audit Engine experiments.

The primary user outcome is simple:

1. paste an original Prompt;
2. receive the complete optimized Prompt first;
3. optionally receive an audit and an executable comparison package;
4. make only evidence-bounded quality claims.

Released contract version `0.3.0` provides deterministic static audit and E0/E1
evidence enforcement. The current working tree also implements the later
comparison, domain, provider, human-review, and local-service layers, but those
versions are not declared complete until their external evidence gates pass.

## Quick Check

```powershell
python -m unittest discover -s tests -v
python scripts/validate_release.py
```

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

The CLI prints the extracted optimized Prompt, not the model wrapper.

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
`output_text`. Shell execution is disabled and the executable must be
allowlisted.

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
python -m prompt_performance_engine validate-benchmark benchmark\catalog-60.json
python scripts\run_codex_benchmark.py --domains software_engineering
python -m prompt_performance_engine evaluate-recorded ...
python -m prompt_performance_engine create-review-packet ...
python -m prompt_performance_engine aggregate-human-review ...
```

The `cross-domain-60-v2` catalog contains 12 domains, 60 cases, and 12
adversarial cases.
Payload-dependent cases must include their actual evidence packet, source
document, schema, localization content, or simulated tool trace; abstract task
descriptions fail validation. Marketing cases must include a product brief,
verified facts, audience, channel, CTA, and evidence boundary. Definitions
alone are not performance evidence.

The Codex runner creates a configuration-locked `run-manifest.json`, durable
call caches, per-domain artifacts, and a summary. Protocol v23 binds the
benchmark definition, optimizer Prompt hash, domain-profile hash, package
version, the complete Python implementation and runner hash, Python runtime,
model, and supported runtime controls. Quota failures are written as hashed,
retryable evidence. v17 added source-language and scope preservation, suppressed
unrequested variants and placeholders, and fixed measured hard-check false
positives. v18 narrows agent approval behavior and restores concrete,
audience-specific marketing depth. v19 adds concrete marketing payloads and
binds readiness to the exact benchmark suite, definition hash, and run
manifest. v20-v22 add explicit objection, segment, channel, proof-relationship,
and deceptive-request handling for marketing, plus rejection-aware hard checks.
v23 adds artifact-bound multi-candidate selection evidence across CLI and API.
Its default is one optimization candidate.
`--candidate-count 2..5` is experimental and does not by itself raise the
evidence level.

All five software cases have authoritative case-owned verification. Four
extract narrowly permitted Python definitions and run trusted hidden harnesses
in digest-pinned Docker containers. The container backend disables networking,
uses a read-only root filesystem, drops all capabilities, enables
`no-new-privileges`, runs as a non-root user, and enforces PID, memory, and CPU
limits. The migration case validates an exact JSON compatibility contract.
These checks override model judges on failure.

Create readiness evidence directly from a validated software evaluation. The
command re-executes the five optimized outputs with the current verifier and
records the verifier implementation hash:

```powershell
python -m prompt_performance_engine build-code-evidence `
  artifacts\codex-benchmark-v14\software_engineering\evaluation.json `
  --report-id codex-software-exec-v14-gpt-5.5 `
  --sandbox-backend docker `
  --sandbox-image python:3.13-alpine@sha256:YOUR_VERIFIED_DIGEST `
  --output evidence\code-execution.json
```

See `SOFTWARE-SANDBOX.md` for the enforced boundary and verification probes.

## Local Service

```powershell
python -m prompt_performance_engine serve-openai `
  --model YOUR_PINNED_MODEL `
  --auth-token-env PROMPT_PERFORMANCE_SERVICE_TOKEN
```

The service is local-only, persistent, idempotent, restart-safe, and exposes
`/health`, `/metrics`, `/v1/optimize`, `/v1/jobs/{id}`, and
`/v1/artifacts/{id}`. Direct non-loopback binding is rejected.

## Assess Stable-Release Readiness

```powershell
python -m prompt_performance_engine assess-readiness `
  evidence\readiness-manifest.json `
  --require-complete `
  --output evidence\readiness-report.json
python -m prompt_performance_engine validate-readiness `
  evidence\readiness-report.json
```

The readiness gate checks ten mandatory evidence-backed requirements. Missing
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

## Honest Status

Current released status: `static_audit_and_evidence`.

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
until v23 completes a fresh 60/60 run. The first concrete v19 marketing run
produced 1W/0T/4L with no optimized hard failures; v20 also produced 1W/0T/4L
and exposed a hard-check false positive on an explicitly rejected scarcity
claim. The completed v21 real-provider run produced 1W/1T/3L; its apparent hard
regression was another rejection-heading measurement false positive. After
that fix, the v22 three-candidate diagnostic produced 2W/0T/3L with zero hard,
critical, or fatal regressions. This improves on the single-candidate diagnostic
but still fails the domain gate. The first image run has
all 10 matched assets, but no qualified independent visual-review submissions.
Independent expert review and three-machine reproduction are also missing.
Local OS/container-sandbox evidence exists for the software cases, but has not
yet been independently reproduced. Therefore the project does not claim stable
v1.0, production certification, universal best, or award equivalence.
