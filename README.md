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

The catalog contains 12 domains, 60 cases, and 12 adversarial cases.
Payload-dependent cases must include their actual evidence packet, source
document, schema, localization content, or simulated tool trace; abstract task
descriptions fail validation. Definitions alone are not performance evidence.

The Codex runner creates a configuration-locked `run-manifest.json`, durable
call caches, per-domain artifacts, and a summary. Protocol v16 binds the
benchmark definition, optimizer Prompt hash, domain-profile hash, package
version, the complete Python implementation and runner hash, Python runtime,
model, and supported runtime controls. Quota failures are written as hashed,
retryable evidence. Its default is one optimization candidate.
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

The working tree has contract-tested later-stage capabilities. On 2026-06-13,
protocol v14 ran five real `gpt-5.5` software-engineering cases and produced
3 wins, 1 tie, and 1 loss. All five optimized outputs passed their authoritative
machine checks, with zero critical regressions and zero fatal flaws. The scoped
software-domain evaluation reached E2 and passed its domain gate.

Protocol v15 adds source and runtime binding. Its first real run was blocked
before generation by the Codex account usage limit, so v14 remains the latest
completed model result rather than being relabeled as v15.

The aggregate release gate remains false because only 1 of 12 domains and 5 of
60 cases have completed real-provider evidence. The first image run has all 10
matched assets, but no qualified independent visual-review submissions yet.
Independent expert review and three-machine reproduction are also missing.
Local OS/container-sandbox evidence exists for the software cases, but has not
yet been independently reproduced. Therefore the project does not claim stable
v1.0, production certification, universal best, or award equivalence.
