# Architecture Decisions

## ADR-001: Clean Successor

Decision: build a clean successor rather than patching the cumulative v23.9
architecture.

Reason: its version contracts conflict and its release-specific modules obscure
the small set of real behaviors.

## ADR-002: Optimization First

Decision: the optimized Prompt is the primary artifact and first output.

Reason: this is the user's actual job to be done. Audit JSON is optional support.

## ADR-003: Semantic Versioning

Decision: restart at `0.1.0` and use semantic versioning.

Reason: historical numbering is not evidence of product maturity.

## ADR-004: Declarative Domain Profiles

Decision: domain packs are validated data records.

Reason: quality criteria vary by domain, but runtime code should not be cloned
for every profile or release.

## ADR-005: Separate Static and Runtime Claims

Decision: static optimization and executed comparison produce different
evidence levels.

Reason: good Prompt structure does not prove better outputs.

## ADR-006: No Feature Without Behavior

Decision: a feature requires implementation, behavior tests, documentation, and
acceptance evidence.

Reason: descriptors, manifests, and file counts created misleading maturity in
the legacy project.

## ADR-007: Stable Completion Is Machine-Gated

Decision: stable completion requires a hash-linked readiness manifest whose ten
mandatory requirements all pass.

Reason: implementation breadth, local tests, and favorable partial benchmarks
cannot substitute for code execution, image review, expert review, independent
reproduction, defect closure, and evidence-bound claims.

## ADR-008: Untrusted Software Evaluation Is Docker-Only

Decision: executable model output must fail closed unless a digest-pinned,
policy-verified Docker sandbox is explicitly supplied. Production verifier code
has no host-Python fallback.

Reason: AST filtering and timeouts reduce attack surface but are not an OS
isolation boundary. A missing or failed sandbox must remove capability, not
silently weaken it.

## ADR-009: Evidence Levels Have One Authority Each

Decision: optimization artifacts may claim only E0/E1, a single matched
evaluation may claim at most E2, and E3/E4 authority requires exact
reconstruction from the bound source run directories or human-review plan.
E1 validation replays source and optimized audits from the bound source Prompt;
E3 requires the canonical resolved benchmark-definition source, unique call
identities, distinct semantic run payloads, and unique
receipts from a host-supplied `ModelCallReceiptVerifier` that independently
binds every request, response, and evaluation context. E4 reconstructs the
HMAC-v3 balanced sampling and blind-placement protocol, derives per-reviewer
probe and direct-consensus improvement gates, and requires unique receipts from a host-supplied
`ReviewerSubmissionVerifier`. Detached aggregate and review reports, provider
metadata, reviewer IDs, and local hashes provide structural diagnostics only.

Reason: caller flags, self-reported hashes, coordinator-only adjudications, and
disjoint reviewer samples cannot prove repetition or independent expert coverage.

The standalone CLI does not manufacture a local trust adapter. Authority-bearing
hosts inject the two verifier protocols through the Python API; without them the
system fails closed below E3/E4. SHA-256 links establish integrity relationships,
not signatures, provider provenance, or human identity.

## ADR-010: Model Transports Are Unique and Fail Closed

Decision: optimized Prompt responses use one JSON object containing only the
`optimized_prompt` string, selectors return one JSON object containing only the
selected index, and untrusted Codex payloads are JSON-string encoded inside
their wrapper.

Reason: choosing the first tag, longest code fence, or a JSON-looking substring
lets echoed source material displace the authoritative result. JSON string
escaping also makes literal delimiter-like text representable inside the Prompt.

## ADR-011: v0.4.0 Scope Expanded Before Publication

Decision: v0.4.0 retains the comparative-evaluation runtime originally assigned
to the milestone and adds the Frontier Evidence Contract, including frozen
campaign, preflight, budgeted-host, reporting, claim, replay, and reproduction
boundaries. Capabilities implemented ahead of later roadmap milestones remain
subject to their own stable-release and external-evidence gates.

Reason: the comparative runtime needed an explicit authority boundary before it
could safely feed performance claims. Recording the expansion preserves the
historical roadmap while preventing implementation breadth, local tests, or an
unexecuted campaign contract from being mistaken for release maturity or
frontier evidence.
