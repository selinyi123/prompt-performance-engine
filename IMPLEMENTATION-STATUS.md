# Implementation Status

Status date: 2026-07-22

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

### v0.4.0 Frontier Evidence Contract

ADR-011 records that the unpublished v0.4.0 milestone retained its original
comparative-runtime scope and expanded to include the fail-closed frontier
contract. This is a pre-publication scope evolution, not evidence that the
external campaign or later stable-release gates have passed.

- Public frontier Schema family `1.0.0` for frozen campaign, policy,
  commitment, preflight, execution, report, claim, replay, and reproduction
  artifacts.
- Canonical, contained, write-once authority-artifact I/O and strict bundle
  validation.
- Fail-closed full-freeze preflight, atomic budget accounting, and a
  preflight-authorized provider-attempt host.
- Final report and claim-inventory validation plus deterministic offline replay
  and three-replay reproduction-set validation.
- Explicit independent verifier boundaries; local test verifiers do not grant
  real campaign authority.

## Current Evidence

- The public 60-case catalog is a development/regression suite, not a sealed
  frontier claim set. `QUALITY-GATE-SPEC.md` defines the additional strong
  baselines, statistics, robustness, safety, cost, latency, and independent
  evaluator evidence required for a scoped top-tier claim.
- Dependency-free frontier diagnostics now cover normalized macro/micro/worst
  and bottom-CVaR quality, seeded case-cluster and domain-stratified macro
  intervals, case-cluster safety aggregation and exact safety bounds,
  clean/perturbed robustness, fully charged cost summaries, Type-7 latency
  percentiles, and descriptive Pareto membership.
  They are local building blocks, not claim authority. The versioned frontier
  contract, full-freeze preflight, budgeted execution host, reporting, and
  offline replay are implemented; the real independent authorities and external
  frontier campaign have not been executed.
- Unit and behavior suite: 539 tests passed locally in the final 0.4.0 run;
  one case-sensitive-path test was skipped on Windows. The same run configured
  the immutable `python:3.13-alpine` image by digest and passed all four live
  Docker isolation, timeout, memory, and software-contract integration tests.
  This validates the local sandbox implementation, not an external R05 or
  frontier authority claim.
- Adversarial regression: 20 of 20 cases passing.
- Release validator: passing for package 0.4.0, current stable artifact schema
  2.0.0, read-only legacy artifact schema 1.0.0, and frontier contract schema
  1.0.0.
- Domain definitions: 12 profiles, 60 cases, 12 adversarial cases.
- Software case verification: all five cases have authoritative machine checks.
  Four Python artifacts use restricted AST extraction plus trusted hidden
  harnesses in digest-pinned Docker containers; the migration case uses a
  formal JSON compatibility contract. Active probes verify network,
  filesystem, identity, timeout, and memory boundaries.
- Provider adapters: local contract tests cover request shape, retries,
  cancellation, timeout, command permissions, and usage capture.
- Current-host Codex smoke: after fixing Windows launcher discovery to accept the
  installed `codex.exe`, one real optimization call and one real benchmark
  execution call completed with response identities, usage, and bound request/
  response digests. The next evaluation call entered repeated connection
  timeouts, so the bounded run was stopped and emitted a hash-valid structured
  failure artifact. It produced no evaluation summary and grants no performance
  claim.
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
- v24 gives the selector the complete compiled contract: domain guardrails,
  required behaviors, forbidden changes, recovered behavior, target surface,
  and architecture. Selection explicitly rejects guardrail violations and
  does not reward verbosity.
- The v24 three-candidate marketing run produced 2W/1T/2L with zero optimized
  hard failures, critical regressions, or fatal flaws. Full-contract selection
  improved the prior result but did not create a net win.
- v25 replaces repeated identical sampling with five recorded candidate
  strategies: fidelity guardrail, coverage matrix, concise channel fit,
  adversarial red team, and balanced synthesis. Strategy and focus are bound
  into generation, selection, and artifact evidence; single-candidate behavior
  remains unchanged.
- The first v25 differentiated three-candidate marketing run produced
  2W/0T/3L with zero hard, critical, or fatal regressions. The selector chose
  `concise_channel_fit`; three losses cited insufficient hierarchy, segment
  depth, or continuity. Diversity alone did not prove stable improvement.
- The v26 Codex runner and replicate aggregator cap a single 60-case run at E2.
  The E3 path requires at least three unique, configuration-compatible runs whose
  manifests, summaries, optimization artifacts, Prompt bindings, per-domain
  evaluations, case definitions, consensus, stability metrics, and release
  gates all validate, plus a trusted `ModelCallReceiptVerifier` that binds every
  provider call to its request, response, and evaluation context. Copied run
  fingerprints, reused receipts, self-reported provider metadata, and rehashed
  derived-field tampering fail closed. Generic evaluation is permanently capped
  at E2; human-review and readiness now require and validate this replicate authority.
- The Docker execution backend now creates and policy-inspects the container
  before attaching execution. This closes a timeout race where the container
  could disappear before evidence inspection. The live Docker suite covers
  isolation, timeout, memory, and a software contract; it remains an
  environment-specific CI gate and was not run in the local no-image check.

## Implemented in v0.4.0, External Gates Pending

- Matched original-versus-optimized evaluation with hard-check precedence,
  randomized A/B mapping, two-judge aggregation, a single-run E2 ceiling,
  and a validated three-replicate E3 gate.
- Twelve domain profiles with observable checks and domain hard-check plugins.
- OpenAI Responses API and external-command adapters.
- HMAC-v3 blind human-review packets with fresh coordinator-only blinding keys,
  public key commitments, at least two reversed position probes, per-reviewer
  protocol metrics, agreement metrics, trusted reviewer receipts, a direct
  human win/loss gate, and an E4 gate. Coordinator adjudication is diagnostic
  and cannot qualify E4.
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
  validated authoritative software hard checks through an explicit
  `DockerSandbox`, plus strict `code-execution-plan` source replay for stable
  R05 authority. Standalone report creation is not itself authority.
- A fixed Docker sandbox policy plus a dedicated CI job that executes its
  isolation, timeout, and out-of-memory integration tests.
- Actual-image registration with PNG structural/pixel verification, matched
  baseline-versus-optimized assets, randomized blind visual-review packets,
  qualified-reviewer profiles, rubric scoring, and hash-linked R06 evidence.
  Visual packets use `balanced_hmac_sha256_v2` with a fresh per-packet 256-bit
  secret and public commitment; strict `visual-review-plan` replay and the two
  external receipt-verifier boundaries are implemented.
- Canonical frontier artifact I/O, full-freeze campaign validation, authoritative
  preflight interfaces, atomic multi-axis budget accounting, and a
  preflight-bound provider-attempt host.
- Strict frontier report and claim-inventory derivation plus byte-identical
  offline replay and three-independent-replay aggregation contracts.

These local capabilities do not satisfy their external evidence gates merely by
being implemented. The frontier machine result remains `not_evaluable` until
real independent authorities execute a sealed campaign and the required
reproduction workflows.

## Blocking Engineering Work

- **Output-mode naming debt:** `standard`, `prompt_only`, and
  `evaluation_package` are retained request values for schema compatibility,
  but the current contract intentionally gives all three the same strict JSON
  model transport and decoded-Prompt CLI output. Distinct presentation bundles
  are not implemented and must not be advertised without a versioned public
  contract and migration plan.
- **Release publication:** the source package is labeled `0.4.0`, but artifact
  publication, migration approval, and release promotion remain separate. Do
  not describe this package as stable or frontier-qualified without the required
  evidence.

## Closed Engineering Work (2026-07-21)

- Prevented a passing narrow hard check from automatically winning an otherwise
  subjective comparison; eligible optimized outputs still require blind quality
  judgment. R06 also rejects complete visual reviews whose optimized images do
  not win more cases than they lose.
- Moved benchmark model calls to an empty temporary working directory outside
  the checkout. This is defense in depth only: a sealed frontier campaign must
  use a provider boundary that cannot read the benchmark or evaluator sources.
- Removed the production host-Python fallback. Executable software checks,
  recorded evaluation, the Codex benchmark runner, and code-evidence generation
  require a digest-pinned, policy-verified Docker sandbox and fail before model
  output execution when that boundary is unavailable.
- Closed E3/E4 authority bypasses: single evaluation is capped at E2, E3 comes
  only from a replicate aggregate exactly reproduced from its source run
  directories with a trusted model-call receipt verifier, uses accepted-provider
  call identities, unique externally verified receipts, and semantic run
  fingerprints, and rejects mock, unidentified, reused, or metadata-only copied
  runs. E4 requires exact source-plan reconstruction, artifact binding,
  all-reviewer coverage, opaque public items, replay of balanced sampling and
  blind A/B placement, per-reviewer probe/non-degeneracy gates, positive human
  improvement, and unique trusted reviewer-submission receipts. Readiness replays
  those sources with the same verifiers rather than trusting a detached report.
- Unified HTTP request decoding, `OptimizationRequest`, and the JSON Schema.
  Required fields, unknown fields, scalar types, array element types, and
  `candidate_count` now fail closed before job creation; excessive JSON nesting
  produces a bounded 400 response instead of leaking a recursion failure.
- Replaced tag/fence response guessing with one exact `optimized_prompt` JSON
  object, made selector JSON strict, and encoded Codex wrapper bodies so
  untrusted payloads cannot close their boundaries. Literal tag text is now
  representable inside an optimized Prompt.
- Made E1 artifacts self-contained by binding the source Prompt, strictly
  validating every artifact field, replaying both deterministic audits, and
  deriving evidence from the replayed result rather than self-reported facts.
- Docker verification now rejects unexpected effective mounts and treats any
  container-removal timeout or failure as an authoritative sandbox failure.
- R05 readiness now binds `authority_sources.code_execution_plan`, requires an
  explicit live `DockerSandbox` matching its immutable image, reruns the source
  evaluation and sandbox probes, and compares the complete rebuilt evidence
  report. Detached/offline self-hashed code facts fail closed.
- R06 readiness now binds `authority_sources.visual_review_plan`, replays the
  exact generation manifest and packet/key/submission/profile bundle, and
  requires unique receipts from `ImageGenerationReceiptVerifier` and
  `VisualReviewerSubmissionVerifier`. The ordinary aggregation CLI injects no
  verifier and remains diagnostic.

## Blocking External Evidence

- No real sealed frontier campaign has run through the implemented preflight,
  execution host, independent receipt authorities, reporting, and replay path.
  Local simulated verifiers establish contract behavior only, so the frontier
  machine result remains `not_evaluable`.
- All 12 domains require at least three fresh, configuration-compatible v26
  pinned-provider runs against `cross-domain-60-v2`, followed by validated
  replicate aggregation.
- Wins must exceed losses in every domain, aggregate improvement must reach
  10%, and critical regressions must be zero.
- Three independent qualified reviewers must complete at least 24 cases and
  resolve disagreements.
- A fresh-environment installation and replay must be independently reproduced.
- The local fresh-target wheel smoke test is complete, but it is not an
  independent-machine or independent-operator reproduction.
- Docker-only implementation and policy-contract tests are complete locally.
  Four live Docker tests can be skipped when no configured immutable
  integration image/daemon is supplied. This run did not produce live R05
  Docker isolation evidence; live execution, independent-machine, and
  independent-operator evidence remain pending.
- Fresh E3/E4 evidence also requires host integrations for the independent
  model-call and reviewer-submission receipt authorities. The standalone CLI has
  no implicit verifier and therefore remains diagnostic/below-authority by
  design.
- The first matched image run has all 10 required assets generated and
  validated. Three qualified independent visual reviewers, unique externally
  verified image-generation and reviewer-submission receipts, and an
  authority-replayed `visual-review-plan` remain before R06 can pass.
- The current readiness manifest validates, but only 4 of 10 mandatory gates
  pass; stable-release status therefore remains incomplete.
