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
