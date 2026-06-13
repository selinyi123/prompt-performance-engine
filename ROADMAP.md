# Version Roadmap

The roadmap uses semantic versioning. A version advances only after its gate is
met. Patch releases fix behavior; they do not invent new maturity levels.

## v0.1.0: Foundation

Deliver:

- product specification and acceptance criteria;
- architecture and migration plan;
- unified package and schema versions;
- domain profile registry;
- inert request compiler;
- artifact and evidence validators;
- release validation script.

Gate:

- all unit tests pass;
- release validator reports no version or encoding conflict;
- six initial profiles load;
- a source Prompt can be compiled without executing it.

## v0.2.0: Optimization Kernel

Deliver:

- model adapter protocol and deterministic mock;
- candidate generation and parsing;
- architecture selector;
- behavioral-contract recovery;
- prompt-only, standard, and evaluation-package output modes;
- bounded repair for malformed model output.

Gate:

- end-to-end mock optimization passes;
- exact optimized Prompt can be extracted reliably;
- legitimate source requirements are preserved in regression fixtures;
- source injection cannot override the optimizer contract.

## v0.3.0: Static Audit and Evidence

Deliver:

- static injection scanner;
- template-variable and contradiction checks;
- overclaim detection;
- high-risk boundary checks;
- E0/E1 evidence packet generation;
- artifact hashing and immutable manifests.

Gate:

- all known v23.9 adversarial cases are migrated and pass;
- no Prompt/Schema version conflicts;
- corrupted artifacts and unsupported claims fail closed.

## v0.4.0: Comparative Evaluation Runtime

Deliver:

- original-versus-optimized execution;
- matched settings and replay metadata;
- deterministic hard checks;
- randomized A/B judging;
- dual-judge aggregation;
- regression and improvement gates.

Gate:

- prior six-domain, 18-case benchmark is reproduced;
- every case stores replayable artifacts;
- hard checks override subjective judges;
- no aggregate claim can be created without complete execution evidence.

## v0.5.0: Domain Pack Expansion

Deliver:

- 12 required domain profiles;
- profile schema and authoring guide;
- domain-specific fatal flaws and hard-check plugins;
- at least five representative cases per domain.

Gate:

- at least 60 total cases and 12 adversarial cases;
- every profile passes schema and coverage checks;
- every domain has normal, difficult, and adversarial coverage.

## v0.6.0: Provider and Tool Runtime

Deliver:

- OpenAI-compatible adapter;
- configurable external command adapter;
- tool permission manifest;
- timeout, retry, cancellation, and usage capture;
- structured-output support where available.

Gate:

- adapter contract suite passes;
- credentials never appear in logs or artifacts;
- cancellation and timeout are deterministic;
- replay metadata is complete.

## v0.7.0: Human Evaluation

Deliver:

- reviewer packet and blind review UI or form;
- conflict adjudication;
- judge-human agreement metrics;
- position and verbosity bias probes;
- E4 evidence generation.

Gate:

- three independent reviewers complete at least 24 stratified cases;
- disagreements are adjudicated;
- reliability and bias metrics are published with limitations.

## v0.8.0: Production Service

Deliver:

- real HTTP API;
- persistent job store;
- local artifact store;
- structured logs and metrics;
- idempotent requests and recovery;
- secure deployment configuration.

Gate:

- API integration tests pass;
- restart and recovery tests pass;
- health check verifies dependencies;
- threat model and security review have no unresolved critical findings.

## v0.9.0: Release Candidate

Deliver:

- documentation freeze;
- clean installation flow;
- migration utility from Universal Prompt Optimizer v3;
- compatibility import for useful v23.9 audit evidence;
- performance and cost report;
- release candidate benchmark.

Gate:

- fresh-machine installation reproduced;
- all stable v1.0 acceptance criteria have evidence;
- no known P0 or P1 defect remains.

## v1.0.0: Stable

Deliver:

- complete CLI, API, optimizer, audit, evaluation, and packaging workflow;
- 12 validated domain packs;
- public benchmark and human review report;
- signed release manifest and checksums;
- honest evidence statement.

Gate:

- every item in `ACCEPTANCE-CRITERIA.md` is proven by current artifacts.

## Post-v1.0

### v1.1: Optimization Learning Loop

Add regression-case ingestion, prompt diff analysis, and candidate selection
analytics without automatically changing stable Prompts.

### v1.2: Team Registry

Add versioned Prompt registry, review workflow, role-based access, and audit
history after single-user reliability is proven.

### v1.3: Advanced Multimodal Evaluation

Add statistically meaningful image, audio, and video evaluation using actual
generated artifacts and qualified review.

### v2.0: Distributed Service

Consider multi-tenant and distributed execution only after v1.x production
evidence justifies the operational complexity.
