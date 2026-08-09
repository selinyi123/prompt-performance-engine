# Changelog

## Unreleased

## 0.4.0 - 2026-07-22

- Advanced the stable artifact, request, evaluation, review, and readiness
  Schema family to `2.0.0` because the fail-closed contracts are not backward
  compatible. Genuine package `0.3.0` optimization artifacts retain their
  `1.0.0` identity on the explicit read-only legacy validation path; the new
  frontier campaign Schema family remains independently versioned at `1.0.0`.
- Added `QUALITY-GATE-SPEC.md` to separate stable-release maturity from scoped
  frontier performance and define sealed data, budget-matched strong baselines,
  uncertainty, robustness, safety, and quality-cost-latency Pareto evidence.
- Retired the ambiguous aspirational `top_tier_candidate` wording in favor of
  the single future, scope-bound `top_tier_scoped` campaign vocabulary. Package
  `0.4.0` reports `not_evaluable` until an authority-bearing external campaign
  and independent reproduction actually pass.
- Added `FRONTIER-EVIDENCE-CAMPAIGN.md` with the frozen roles, artifacts,
  budgets, execution matrix, invalidation rules, and independent reproduction
  sequence needed to turn the quality policy into an authority-bearing run.
- Added the frontier Schema family `1.0.0`, canonical write-once artifact I/O,
  strict campaign-bundle validation, full-freeze preflight, atomic budget
  accounting, a preflight-authorized provider-attempt host, fail-closed
  report/claim derivation, and deterministic offline replay with
  three-independent-replay aggregation. These are implemented contract
  boundaries, not evidence that real independent authorities executed them.
- Bound every public claim metric to one exact report gate or strong-baseline
  comparison and made its evidence summary reproducible from the complete
  report source. Re-signing invented values or unrelated evidence digests no
  longer validates.
- Updated Windows Codex CLI discovery to resolve the installed `codex`
  executable instead of assuming the legacy npm `codex.cmd` launcher.
- Added dependency-free paired-outcome statistics for exact directional sign
  tests, non-tie Wilson intervals, and Holm multiple-comparison decisions. The
  helpers are explicitly diagnostic and cannot grant a frontier claim.
- Added normalized macro/micro/worst/bottom-CVaR summaries, deterministic
  case-cluster and domain-stratified macro bootstrap intervals, exact binomial
  safety bounds that reject zero-trial or invalid-confidence input as not
  evaluable, plus an any-failure case-cluster aggregator that prevents repeated
  calls from increasing the independent trial count. Exact-binomial authority is
  restricted to preregistered IID/common-risk strata;
  clean/perturbed robustness summaries, fully charged cost-per-success, Type-7
  latency percentiles, and descriptive quality-cost-latency Pareto membership.
  The implemented campaign contract requires a full-freeze preflight and
  distinguishes offline byte replay from new stochastic experimental
  reproduction.
- Removed the narrow-hard-check automatic-win path: an optimized output that
  merely passes a required-token check still faces blind quality judgment.
  Critical optimized regressions remain authoritative losses, and two outputs
  that both fail hard checks remain ties.
- R06 now requires visual-review outcome counts to match reviewed coverage and
  optimized images to win more cases than they lose; complete but losing image
  reviews cannot satisfy readiness.
- R03/R04 readiness now require the exact twelve Product Spec domain ids rather
  than accepting any arbitrary collection whose count happens to be twelve.
- The Codex benchmark runner now gives model calls an empty temporary working
  directory outside the checkout. This reduces accidental benchmark/file
  exposure but is only defense in depth; tool-capable runs remain diagnostic
  rather than sealed frontier evidence.
- Bound the benchmark runner's fallback artifact directory to its active
  protocol version and made temporary model-workspace cleanup deterministic on
  usage, adapter, and unexpected failure paths. A cleanup failure is attached to
  rather than allowed to mask an existing quota/adapter failure.
- Made frontier-document boundary validation require one exact visible status
  block so hidden examples cannot satisfy the release contract. The block now
  distinguishes implemented contract/host/replay machinery from the unexecuted
  independent authority and external campaign, whose machine result remains
  `not_evaluable`.
- Closed JSON and scalar-contract aliasing at authority boundaries: file loads
  reject lossy Decimal-to-float conversion, benchmark/run/evaluation/summary
  fields reject boolean-as-integer values, migration plans reject duplicate
  keys, and the exact model transport preserves Prompt whitespace.
- Made custom readiness evidence an exact kind-specific contract, including
  SHA-256 machine/operator identities for R08, and made explicit service-auth
  environment variables plus OpenAI/external-command JSON wrappers fail closed
  instead of silently degrading.
- Bound stable R05 to a strict `code-execution-plan` and an explicit live
  `DockerSandbox`. Authority validation reloads the source evaluation, reruns
  isolation/resource probes and every eligible software check, and requires
  exact report reproduction; detached or offline self-hashed facts fail closed.
- Advanced visual review to `balanced_hmac_sha256_v2`: every packet/key pair
  receives a fresh 256-bit secret, the public protocol exposes only its
  commitment, and the private key retains the secret, seed, source mappings,
  and optimized labels.
- Bound stable R06 to a strict `visual-review-plan`, exact source replay, unique
  image-generation receipts from an `ImageGenerationReceiptVerifier`, and
  unique visual-reviewer receipts from a
  `VisualReviewerSubmissionVerifier`. Receipt-free CLI aggregation remains
  diagnostic.
- Added `code_execution_plan` and `visual_review_plan` to readiness
  `authority_sources`; legacy detached code/image evidence is not directly
  migratable into stable-release authority.
- Removed the production host-Python fallback for model-generated software
  checks. Executable cases now require a digest-pinned, policy-verified Docker
  sandbox; runner, recorded-evaluation, and code-evidence CLIs fail closed when
  it is absent.
- Closed evidence-level authority bypasses: optimization artifacts are capped
  at E1, single evaluations at E2, E3 comes only from a validated three-run
  aggregate with independently verified model-call receipts, and E4 requires
  every counted case to be covered by the same three or more artifact-bound,
  receipt-verified reviewers. E3, E4, and readiness validators reload the bound
  source directories/plans with the same verifiers and require exact report
  reconstruction.
- Made E1 authority replayable: artifacts now bind the source Prompt, reject
  unknown or malformed Schema fields, rerun both deterministic audits, and
  derive evidence from those replayed facts instead of self-reported findings.
- Strengthened E3 independence with accepted-provider provenance, non-empty and
  unique provider response IDs, positive usage, semantic payload fingerprints,
  and externally verified request/response/context receipts. Self-reported
  metadata without a trusted `ModelCallReceiptVerifier` remains below E3; mock,
  unidentified, reused, and metadata-disguised copied runs fail closed.
- Bound E3 to the actual resolved `benchmark-definition.json` copied into every
  run. Source replay recomputes its hash and verifies each domain source Prompt,
  case set, and case digest rather than trusting repeated manifest fields.
- Advanced E4 packets and keys to `balanced_round_robin_hmac_sha256_v3`: fresh
  256-bit coordinator keys blind public review IDs and A/B assignments, while
  the public packet exposes only a commitment. At least two fully consistent probes,
  non-degenerate base selections, full overlap, direct consensus, positive
  direct human improvement, and unique receipts from a trusted
  `ReviewerSubmissionVerifier` are now required.
- Coordinator-only adjudications are explicit diagnostics and cannot count as
  human wins or qualify a report for E4.
- Unified the HTTP decoder, `OptimizationRequest`, and its JSON Schema with
  strict required/unknown/type checks and a contract-bound `candidate_count`.
- Replaced tag, fence, and JSON-substring response guessing with exact JSON
  object transports; JSON string escaping permits literal tag text inside the
  Prompt, and Codex wrapper bodies use escaped string literals so payload data
  cannot close the wrapper. Excessively nested JSON now fails as a bounded
  contract error instead of leaking a recursion failure.
- Made Docker verification reject unexpected effective mounts and invalidate a
  run when forced container removal fails or times out. Runtime inspection also
  rejects capability/group additions, peer or host PID/IPC namespaces, and extra
  or unconfined security options, and requires the exact safe `/tmp` option set;
  the active identity probe checks effective UID, GID, and supplementary groups.
  Every create attempt now enters named cleanup, including nonzero or uncertain
  create results.
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
- Replaced fragile fenced extraction with transport tags (superseded by the
  strict JSON transport in the current hardening work).
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
  restricted Python harnesses (now Docker-only) and one formal migration JSON
  contract.
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
- Changed code-evidence generation to re-verify all five optimized software
  outputs with the current verifier: execute four Python outputs and formally
  validate the migration JSON, while recording the implementation hash.

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
