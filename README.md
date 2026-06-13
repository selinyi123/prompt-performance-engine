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

The catalog contains 12 domains, 60 substantive cases, and 12 adversarial
cases. Definitions alone are not performance evidence.

The Codex runner creates a configuration-locked `run-manifest.json`, durable
call caches, per-domain artifacts, and a summary. Protocol v10 binds the
benchmark definition, optimizer Prompt hash, domain-profile hash, package
version, model, and supported runtime controls. Its default is one optimization
candidate.
`--candidate-count 2..5` is experimental and does not by itself raise the
evidence level.

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
- `MIGRATION.md`: legacy Prompt and audit import.
- `WORLD-CLASS-DELIVERY-PLAN.md`: remaining architecture, implementation, and
  evidence work required for stable completion.

## Honest Status

Current released status: `static_audit_and_evidence`.

The working tree has contract-tested later-stage capabilities. Real
`gpt-5.5` text-only software-engineering trials were run on 2026-06-13, but
none passed the zero-fatal-flaw domain gate. The best trial was 4 wins and
1 loss, with one fatal flaw. No independent human-review packet has been
completed.

The current benchmark evaluates generated text. It does not yet execute
generated code or render and review generated images. Therefore the project
still does not claim runtime superiority, stable v1.0, production
certification, universal best, or award equivalence.
