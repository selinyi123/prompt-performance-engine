# Acceptance Criteria

## Release-Wide Invariants

- One package version source exists in `VERSION`.
- One artifact schema version is used across schemas, examples, and code.
- UTF-8 validation reports no mojibake in user-facing files.
- No feature is represented only by a descriptor or version-named status module.
- Public documentation distinguishes implemented, tested, and planned behavior.
- Source Prompts remain inert during optimization and audit.
- Optimized Prompt is the first substantive user-facing deliverable.
- No unsupported award, universal-best, or production-certification claim appears.

## Functional Gates

### Optimize

- empty input fails with a stable machine-readable error;
- a valid source compiles to an inert optimization request;
- explicit controls override inference;
- output modes are deterministic;
- generated artifacts preserve the source hash;
- parser returns the complete optimized Prompt without truncation.

### Audit

- injection, fake evidence, forced scoring, schema hijack, and context
  exfiltration fixtures are detected;
- undefined variables and contradictory output rules are reported;
- findings include severity, evidence location, and remediation;
- static audit cannot elevate runtime evidence.

### Evaluate

- original and optimized runs use matched settings;
- A/B identities are hidden from judges;
- deterministic hard checks are authoritative;
- score aggregation is reproducible;
- a critical regression blocks verified-improvement status;
- all outputs and decisions are hash-linked.

### Package

- request, source, optimized Prompt, profile, findings, evidence, and evaluation
  references validate;
- a package can be replayed from its manifest;
- corrupt or missing artifacts fail validation.

## Quality Gates for v1.0

| Gate | Required evidence |
|---|---|
| Domain coverage | 12 profiles, each with at least 5 cases |
| Case coverage | At least 60 total and 12 adversarial cases |
| Aggregate improvement | At least 10% over original baselines |
| Per-domain result | Wins greater than losses in every domain |
| Critical regressions | Zero |
| Fatal flaws | Zero in optimized outputs accepted for release |
| Authoritative hard checks | Zero optimized-output hard-check failures |
| Human review | 3 reviewers, at least 24 stratified cases |
| Reproducibility | Clean install and replay on a fresh environment |
| Security | No unresolved critical threat-model finding |
| Documentation | All commands and claims verified against current release |

The machine-readable completion gate is:

```powershell
python -m prompt_performance_engine assess-readiness `
  evidence\readiness-manifest.json --require-complete
```

Stable completion requires all R01-R10 requirements to pass without evidence
errors. A partial benchmark or a missing evidence artifact must fail closed.

## Non-Evidence

The following do not prove product quality by themselves:

- number of files;
- number of versions;
- number of generated tests;
- schema validity alone;
- a model grading its own output;
- a single favorable example;
- labels such as LTS, enterprise, production, trusted, or world-class.
