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

- Unit and behavior suite: 103 tests passing after the readiness and benchmark
  hardening batch.
- Adversarial regression: 20 of 20 cases passing.
- Release validator: passing for package 0.3.0 and schema 1.0.0.
- Domain definitions: 12 profiles, 60 cases, 12 adversarial cases.
- Software case verification: the Python pagination case uses restricted AST
  extraction plus deterministic behavior and validation vectors; arbitrary
  generated code is not executed.
- Provider adapters: local contract tests cover request shape, retries,
  cancellation, timeout, command permissions, and usage capture.
- Service: local integration tests cover HTTP auth, persistence, idempotency,
  atomic artifacts, and restart recovery.
- Packaging: wheel installation into both a clean target directory and a
  standard virtual environment loads the packaged optimizer Prompt, all 13
  profiles, compiler, and CLI audit successfully.
- Runtime superiority: tested in one text-only domain but not proven; claim
  remains `optimized_candidate`.

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
  matcher. A fresh v10 run is still required before the domain gate can pass.

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
- Experimental multi-candidate generation and blind Prompt selection.
- Self-contained wheel data for version, optimizer Prompt, and domain profiles.
- Machine-readable ten-requirement readiness assessment with immutable evidence
  references and a fail-closed stable-release gate.

These capabilities remain unreleased because roadmap versions advance only
after their evidence gates, not merely after implementation.

## Blocking External Evidence

- A real pinned provider must execute the 60-case benchmark under matched
  settings.
- Wins must exceed losses in every domain, aggregate improvement must reach
  10%, and critical regressions must be zero.
- Three independent qualified reviewers must complete at least 24 cases and
  resolve disagreements.
- A fresh-environment installation and replay must be independently reproduced.
- The local fresh-target wheel smoke test is complete, but it is not an
  independent-machine or independent-operator reproduction.
- Code cases need executable sandbox verification, and image cases need actual
  image generation plus qualified visual review; text-only proxies are
  insufficient for top-tier domain claims.
- Four remaining software cases still lack safe executable or formal
  case-specific verification.
- No complete readiness evidence manifest exists yet; current status therefore
  remains incomplete regardless of implementation breadth.
