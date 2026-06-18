# Implementation Status

Status date: 2026-06-13

## Completed

### v0.1.0 Foundation

- Product, architecture, migration, roadmap, and acceptance specifications.
- Unified package and artifact schema contracts.
- Declarative domain registry and inert request compiler.
- Artifact, encoding, and release validation.

### v0.2.0 Optimization Kernel

- Model adapter protocol and deterministic mock adapter.
- Behavioral-contract recovery and architecture selection.
- Prompt parser with one bounded repair attempt.
- End-to-end optimization artifact generation.

### v0.3.0 Static Audit and Evidence

- Injection, authority override, hidden encoding, context exfiltration, schema
  hijack, fake evidence, forced score, coercion, and overclaim checks.
- Template-variable preservation, output conflict, high-risk boundary,
  excessive-size, and repeated-instruction checks.
- Twenty migrated adversarial regression cases.
- E0/E1 evidence assignment based on optimized-Prompt audit results.
- Canonical artifact payload hashes and deterministic file manifests.

## Current Evidence

- Unit and behavior suite: 177 tests passing after the v23 multi-candidate
  evidence, CLI, service, and structured adapter-error batch.
- Adversarial regression: 20 of 20 cases passing.
- Release validator: passing for package 0.3.0 and schema 1.0.0.
- Domain definitions: 12 profiles, 60 cases, 12 adversarial cases.
- Software case verification: all five cases have authoritative machine checks.
  Four Python artifacts use restricted AST extraction plus trusted hidden
  harnesses in digest-pinned Docker containers; the migration case uses a
  formal JSON compatibility contract. Active probes verify network,
  filesystem, identity, timeout, and memory boundaries.
- Provider adapters: local contract tests cover request shape, retries,
  cancellation, timeout, command permissions, and usage capture.
- Service: local integration tests cover HTTP auth, persistence, idempotency,
  atomic artifacts, restart recovery, and validated multi-candidate requests.
- CLI: known quota and adapter failures return sanitized JSON with stable exit
  codes and no traceback; quota failures are explicitly retryable.
- Packaging: wheel installation into both a clean target directory and a
  standard virtual environment loads the packaged optimizer Prompt, all 13
  profiles, compiler, and CLI audit successfully.
- Runtime superiority: protocol v16 completed all 60 cases with 30 wins, 9
  ties, and 21 losses. Coverage passes, but the aggregate release claim remains
  `optimized_candidate`.

## Real Provider Trial Results

On 2026-06-13, authenticated local Codex CLI trials used `gpt-5.5` with low
reasoning effort on the five software-engineering cases.

- v1 exposed an invalid "do not use tools" measurement constraint: 1W/0T/4L.
- v2 fixed target-surface contamination: 4W/0T/1L, one fatal flaw.
- v3 preserved design deliverables: 4W/0T/1L, one fatal flaw.
- v4 strengthened non-blocking fallback: 2W/2T/1L, one fatal flaw.
- experimental v5 three-candidate selection: 2W/0T/3L, two fatal flaws.

All trials failed the domain gate. These negative results are retained under
`artifacts/codex-benchmark*`; they are diagnostic evidence, not proof of
improvement. v7 adds a configuration-locked run manifest, restores one
candidate as the default, and records unsupported Codex CLI generation
  controls as null instead of claiming temperature, token, or seed settings.
  A fresh v7 trial again produced 4W/0T/1L with one fatal migration rollback
  flaw. Protocol v8 binds the optimizer Prompt hash and package version and
  adds the failed migration invariant as an optimizer regression rule.
- The first v8 trial produced 2W/1T/2L with two fatal flaws: an invented
  replacement CLI and stale data from unsynchronized old-version writes.
  Protocol v9 promotes both boundaries into explicit software-domain
  guardrails and also binds the domain-profile hash.
- The v9 trial produced 4W/0T/1L, zero judge-reported fatal flaws, and one
  deterministic critical regression. The critical regression was diagnosed as
  a checker false positive: prose beginning with "Pass" matched the standalone
  Python `pass` placeholder rule. Protocol v10 fixes and regression-tests that
  matcher.
- v10 produced 4W/0T/1L but exposed an unsafe migration contraction and
  incomplete old-writer synchronization.
- v11 produced 3W/1T/1L and exposed verifier gaps for safe generic helper
  classes and `startswith`.
- v12 produced 4W/0T/1L and exposed ambiguity in the pagination return-type
  contract.
- v13 reported a passing gate, but that result was invalidated when review
  found both migration outputs could fail hard checks while still aggregating
  as a tie. The gate now requires zero optimized-output hard failures.
- v14 remains the latest passing scoped software run: 3W/1T/1L, zero critical
  regressions, zero fatal flaws, zero optimized hard failures, and a passing E2
  software-domain gate.
- Protocol v15 additionally binds the evaluation/verifier implementation hash,
  Python version, and platform. Protocol v16 expands that binding to the whole
  Python package and runner, adds concurrency-safe atomic summaries, and emits
  hashed quota/adapter failure evidence.
- On 2026-06-15, v16 completed all 12 domains and 60 cases using 240 real model
  calls: 30W/9T/21L, 15% net improvement. Four domains passed their local gate.
  The aggregate gate failed with three reported critical regressions, one fatal
  flaw, and five optimized hard failures.
- v17 fixes all five confirmed hard-check measurement defects and adds
  source-language, scope, single-deliverable, state-fidelity, and
  no-placeholder optimizer rules. Rechecking the immutable v16 outputs with the
  v17 verifier changes optimized hard failures from 5 to 0 and hard regressions
  from 3 to 0.
- A four-domain v17 diagnostic run produced agents 0W/0T/5L, marketing
  0W/0T/5L, image generation 2W/0T/3L, and education 3W/0T/2L, with zero hard
  failures or critical regressions. v18 narrows agent approval behavior,
  removes fixed visible process templates, and restores concrete marketing
  depth and CTA fidelity.
- The v18 priority run used 42 real model calls. Agents improved to 3W/2T/0L
  and passed its domain gate with zero hard failures, critical regressions, or
  fatal flaws. Marketing remained 0W/0T/5L. The next benchmark revision must
  replace its five abstract marketing tasks with concrete product, audience,
  proof, channel, and CTA briefs before further prompt tuning.
- v19 replaces those five marketing tasks with evidence-bearing payloads and
  advances the release suite to `cross-domain-60-v2`. Benchmark summaries now
  record the definition hash and run-manifest hash. Readiness manifests bind
  the intended suite and definition, so stale v1 coverage cannot satisfy R03
  or R04.
- The first concrete v19 marketing run produced 1W/0T/4L with zero optimized
  hard failures. v20 remained at 1W/0T/4L and revealed that the marketing
  hard-check treated a visibly rejected `"only 2 left"` claim as if it had
  been operationalized. v21 distinguishes rejection from execution, catches
  reversal and longer fabricated-scarcity phrasing, and requires explicit
  objection handling plus distinct segment and channel treatment without
  repetitive proof or CTAs.
- The completed v21 real marketing run produced 1W/1T/3L. Its one apparent hard
  regression was a second measurement false positive caused by treating a
  rejection-heading colon as a clause boundary; all five stored v21 optimized
  outputs pass after the v22 correction.
- A v22 three-candidate real diagnostic produced 2W/0T/3L with zero optimized
  hard failures, critical regressions, or fatal flaws. It improved on the
  single-candidate result but still failed the marketing domain gate.
- v23 records all candidate Prompts, hashes, selected index, selection method,
  and selector-response hash in the optimization artifact. Candidate count is
  now available through real-model CLIs and the persistent HTTP service, with
  fail-closed validation and idempotency binding.
- The Docker execution backend now creates and policy-inspects the container
  before attaching execution. This closes a timeout race where the container
  could disappear before evidence inspection. All three live Docker isolation,
  timeout, and memory tests pass after the change.

## Implemented After v0.3, Gate Pending

- Matched original-versus-optimized evaluation with hard-check precedence,
  randomized A/B mapping, two-judge aggregation, and E2/E3 ceilings.
- Twelve domain profiles with observable checks and domain hard-check plugins.
- OpenAI Responses API and external-command adapters.
- Blind human-review packets, position probes, adjudication, agreement metrics,
  and an E4 gate.
- Persistent local HTTP service with SQLite jobs and atomic artifact storage.
- Legacy Prompt migration and untrusted audit-reference import.
- Target-surface capability contracts and deliverable-kind recovery.
- Resumable Codex benchmark execution with durable caches and immutable run
  configuration.
- Concrete benchmark payload contracts: all research, structured-data,
  translation/localization, and agent-automation cases, plus four
  source-dependent writing cases, now include executable source packets or
  simulated tool traces. Abstract placeholder descriptions fail validation.
- Experimental multi-candidate generation and blind Prompt selection.
- Self-contained wheel data for version, optimizer Prompt, and domain profiles.
- Machine-readable ten-requirement readiness assessment with immutable evidence
  references and a fail-closed stable-release gate.
- A `build-code-evidence` command that derives hashed R05 evidence from
  validated authoritative software hard checks.
- A fixed Docker sandbox policy plus a dedicated CI job that executes its
  isolation, timeout, and out-of-memory integration tests.
- Actual-image registration with PNG structural/pixel verification, matched
  baseline-versus-optimized assets, randomized blind visual-review packets,
  qualified-reviewer profiles, rubric scoring, and hash-linked R06 evidence.

These capabilities remain unreleased because roadmap versions advance only
after their evidence gates, not merely after implementation.

## Blocking External Evidence

- All 12 domains require a fresh v23 pinned-provider run against
  `cross-domain-60-v2`.
- Wins must exceed losses in every domain, aggregate improvement must reach
  10%, and critical regressions must be zero.
- Three independent qualified reviewers must complete at least 24 cases and
  resolve disagreements.
- A fresh-environment installation and replay must be independently reproduced.
- The local fresh-target wheel smoke test is complete, but it is not an
  independent-machine or independent-operator reproduction.
- Local R05 Docker isolation evidence is complete; independent-machine and
  independent-operator reproduction remains pending.
- The first matched image run has all 10 required assets generated and
  validated. Three qualified independent visual reviewers remain before R06
  can pass.
- The current readiness manifest validates, but only 4 of 10 mandatory gates
  pass; stable-release status therefore remains incomplete.
