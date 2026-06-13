# Changelog

## Unreleased

- Added a 12-domain, 60-case benchmark catalog with 12 adversarial cases.
- Added domain-specific deterministic hard-check plugins.
- Added matched comparative execution, blind dual judging, and E2/E3 gates.
- Added OpenAI Responses API and allowlisted external-command adapters.
- Added timeout, retry, cancellation, structured-output configuration, and
  sanitized usage metadata.
- Added blind human-review packets, position probes, adjudication, agreement
  metrics, and an E4 gate.
- Added a persistent local HTTP service with idempotency and restart recovery.
- Added legacy Prompt and untrusted audit-reference migration.
- Replaced fragile fenced extraction with nested-safe transport tags.
- Added target-surface capability contracts and deliverable-kind recovery.
- Added authenticated Codex benchmark execution with durable response caches,
  actual usage accounting, and configuration-locked run manifests.
- Added an experimental multi-candidate optimizer path while retaining
  single-candidate generation as the benchmark default.
- Recorded failed software-engineering trials without elevating evidence.
- Fixed wheel imports by packaging the runtime version, optimizer Prompt, and
  domain profiles as installable data files.
- Stopped recording unsupported Codex CLI temperature, maximum-token, and
  generation-seed controls as if they had been applied.
- Added GitHub Actions CI across Python 3.11, 3.12, and 3.13, including release
  validation, wheel construction, standard-venv installation, and artifact
  upload.
- Added Dependabot, a pull request template, and contribution guidance.
- Fixed installed data discovery for standard virtual environments where wheel
  data files are placed under the environment data prefix.
- Added a case-specific restricted Python verifier for the pagination benchmark,
  including one-based behavior vectors and validation-error checks.
- Made pagination behavior regressions authoritative over model judges without
  enabling general execution of generated code.
- Added machine-readable R01-R10 stable-release readiness assessment, immutable
  evidence references, report validation, and a fail-closed
  `--require-complete` CLI gate.
- Added the 15-section world-class delivery and implementation plan.
- Added a rolling-migration compatibility invariant after a real v7 trial
  exposed premature constraint validation that broke old-version rollback.
- Upgraded the Codex evaluation protocol to v8 and bound run manifests to the
  optimizer Prompt hash and package version.
- Prevented release validation from scanning generated artifacts, build output,
  virtual environments, and third-party package files as project source.
- Added explicit software-domain guardrails for missing-repository CLI work and
  mixed-version migration writes after the v8 regressions.
- Upgraded the Codex protocol to v9 and bound domain-profile content into the
  immutable run configuration.
- Fixed a software hard-check false positive that treated prose beginning with
  "Pass" as a standalone placeholder statement, and advanced the protocol to
  v10.

## 0.3.0 - 2026-06-12

- Added deterministic static Prompt audit with stable rule IDs.
- Migrated and passed 20 legacy adversarial regression cases.
- Added source-variable preservation and output-contract conflict checks.
- Added high-risk boundary, overclaim, size, and repetition checks.
- Added E0/E1 evidence assignment tied to optimized-Prompt audit results.
- Added canonical artifact hashes and deterministic file manifests.
- Added `audit`, `manifest`, and `verify-manifest` CLI commands.

## 0.2.0 - 2026-06-12

- Added model adapter protocol and deterministic mock adapter.
- Added behavioral-contract recovery and architecture selection.
- Added response parsing and one bounded repair attempt.
- Added end-to-end optimization artifact generation.

## 0.1.0 - 2026-06-12

- Established clean product, architecture, roadmap, and acceptance contracts.
- Added versioned requests, profiles, compiler, evidence rules, and validation.
