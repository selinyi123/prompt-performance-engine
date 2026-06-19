# Changelog

## Unreleased

- Advanced to v26 by capping every single benchmark run at E2 and requiring at
  least three configuration-compatible, uniquely identified complete runs for
  E3 eligibility.
- Added hash-verified repeated-run aggregation with per-case consensus,
  exact-agreement stability, per-domain and per-replicate gates, a standalone
  tamper-resistant validator, optimization-to-evaluation Prompt binding,
  copied-run fingerprint rejection, CLI commands, JSON Schema, and
  180-observation full-release tests.
- Completed the first full real-provider run across all 12 domains and 60 cases:
  30 wins, 9 ties, 21 losses, and 15% net improvement from 240 model calls.
- Advanced the evaluation protocol to v17 with source-language, scope,
  single-deliverable, state-fidelity, and no-unrequested-placeholder rules.
- Recorded a four-domain v17 diagnostic run; zero hard failures remained, but
  agents and marketing each regressed to 0W/0T/5L.
- Advanced to v18 with task-owned agent approvals, no fixed agent report
  template, concrete audience workflows, deliverable-depth preservation, and
  CTA fidelity for marketing.
- Recorded the v18 priority run: agents improved to 3W/2T/0L and passed, while
  marketing remained 0W/0T/5L. The next revision will replace abstract
  marketing tasks with concrete evidence-bearing briefs.
- Advanced to v19 and `cross-domain-60-v2` by replacing all five abstract
  marketing tasks with concrete product facts, audiences, channels, CTAs, and
  evidence boundaries.
- Bound benchmark summaries and readiness manifests to the exact suite,
  benchmark-definition hash, and run-manifest hash so stale coverage fails
  closed.
- Split Docker sandbox execution into create, pre-execution policy inspection,
  and attached start phases, eliminating an inspect-after-timeout race.
- Recorded the first concrete v19 marketing run at 1W/0T/4L with zero
  optimized hard failures, confirming that concrete briefs alone did not
  resolve the domain-quality gap.
- Advanced through v20 and v21 with explicit objection handling, distinct
  segment and channel treatment, proof-relationship preservation, concise
  visible rejection of deceptive tactics, and anti-repetition guidance.
- Made marketing deception checks rejection-aware while still failing
  reversal into execution and longer fabricated-scarcity phrasing. All five
  stored v20 optimized outputs pass the v21 hard-check on offline replay.
- Attempted the v21 real marketing rerun, but the provider usage limit blocked
  generation before any case result. No v21 performance claim is recorded.
- Completed that v21 rerun at 1W/1T/3L and fixed a rejection-heading colon
  false positive in the authoritative marketing hard-check.
- Ran a v22 three-candidate marketing diagnostic: 2W/0T/3L with zero hard,
  critical, or fatal regressions. The domain gate still failed.
- Added v23 artifact-bound tournament evidence: all candidate Prompts and
  hashes, selected index, selector method, and selector-response hash.
- Exposed `candidate_count` through mock, OpenAI, external-command, and Codex
  CLIs plus the persistent HTTP service, with validation and idempotency binding.
- Replaced user-facing adapter tracebacks with structured sanitized CLI errors;
  quota exhaustion now returns retryable JSON and exit code 75.
- Advanced to v24 by binding the selector to the complete compiled contract,
  including domain guardrails, required behaviors, forbidden changes, target
  surface, recovered behavior, and architecture; verbosity is not rewarded.
- Recorded the v24 full-contract selector marketing run at 2W/1T/2L with zero
  optimized hard failures, critical regressions, or fatal flaws.
- Advanced to v25 with recorded, distinct candidate strategies for fidelity,
  coverage, channel fit, adversarial review, and balanced synthesis; strategy
  context is preserved through generation, selection, and artifact evidence.
- Recorded the first v25 differentiated-candidate marketing run at 2W/0T/3L.
  It had no hard, critical, or fatal regressions, but the selected concise
  strategy lost three cases on completeness or continuity; the gate failed.
- Fixed forbidden-substring checks for rejected requests, warning contexts, and
  Chinese refusal language, plus case-insensitive required-text matching.
- Extended restricted Python verification to include referenced safe literal
  module constants without executing dynamic module expressions.
- Rechecked the immutable v16 outputs with the v17 verifier: optimized hard
  failures fell from 5 to 0 and hard regressions from 3 to 0.
- Updated readiness with complete 12-domain coverage; R03 now passes and overall
  readiness is 5 of 10 mandatory gates.
- Upgraded GitHub artifact upload from `actions/upload-artifact@v4` to v7.
- Replaced 24 abstract benchmark descriptions with concrete evidence packets,
  source documents, schemas, localization strings, and simulated tool traces.
- Added fail-closed benchmark validation for payload-dependent and
  source-dependent cases.
- Bound benchmark runs to the complete Python implementation, made concurrent
  summary writes atomic on Windows, and added hashed quota-failure evidence.
- Added structured Codex usage-limit diagnostics instead of opaque exit-code
  failures.
- Added actual PNG registration, matched image generation manifests,
  randomized blind visual-review packets, qualified-reviewer profiles, rubric
  scoring, and hash-linked R06 evidence aggregation.
- Hardened image evidence against corrupt pixels, replaced files, incomplete
  reviews, unqualified reviewers, and tampered submissions.
- Fixed forbidden-content and aspect-ratio checks so explicit negative image
  constraints such as `no logos` and `no square crop` are not false failures.
- Fixed all optimization CLI paths to create artifact parent directories before
  writing completed model results.
- Added a digest-pinned Docker execution backend for software benchmark
  harnesses with no network, read-only root, non-root identity, dropped
  capabilities, `no-new-privileges`, and PID/memory/CPU limits.
- Added active network, filesystem, identity, timeout, and out-of-memory probes
  plus a dedicated Docker integration job in GitHub Actions.
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
- Added authoritative machine verification for all five software cases: four
  restricted Python subprocess harnesses and one formal migration JSON contract.
- Added strict AST rejection for imports, dangerous builtins, dynamic calls,
  dunder access and method definitions, and unapproved methods before candidate
  execution.
- Fixed the evaluation gate so optimized outputs with authoritative hard-check
  failures cannot pass by tying equally broken original and optimized outputs.
- Added aggregate `optimized_hard_failures` accounting to benchmark summaries,
  validation, and stable-readiness assessment.
- Added `build-code-evidence` to derive hashed R05 evidence directly from a
  validated software evaluation.
- Advanced the Codex software protocol through v14 and recorded a valid E2
  software-domain result of 3W/1T/1L with zero optimized hard failures.
- Advanced the runner to protocol v15 by binding the evaluation/verifier source
  hash, Python version, and platform into immutable run manifests and summaries.
- Changed code-evidence generation to re-execute all five optimized software
  outputs with the current verifier and record its implementation hash.

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
