# Migration Plan

## 1. Inputs

### Universal Prompt Optimizer v3.0

Retain:

- prompt-first output;
- domain excellence profiles;
- architecture selection;
- candidate comparison;
- static red-team review;
- matched A/B evaluation;
- deterministic hard checks;
- six-domain benchmark evidence.

Repair:

- mojibake in output templates and validation markers;
- dependence on one large free-form Prompt;
- weak package validation;
- limited benchmark size and judge independence.

### Prompt Evidence-Based Audit Engine v23.9

Retain:

- inert source principle;
- exact JSON parsing;
- deterministic validator patterns;
- evidence ceilings;
- artifact hashing concepts;
- adversarial fixtures;
- explicit prohibition on self-certification.

Reject:

- v3.0/v4.0/v23.9 contract conflicts;
- cumulative 73 KB Prompt appendices;
- release-number modules;
- descriptor and status functions presented as product capabilities;
- benchmark readiness checks that only count files or fields;
- production, LTS, or trusted labels unsupported by behavior.

## 2. Migration Sequence

1. Freeze legacy packages as read-only evidence.
2. Re-express useful behavior as requirements and fixtures.
3. Implement the clean kernel without importing legacy version modules.
4. Migrate adversarial fixtures and validator rules selectively.
5. Reproduce the six-domain v3 benchmark in the new evaluation engine.
6. Compare results before retiring any legacy path.
7. Publish a compatibility report listing retained, changed, and removed behavior.

## 3. Compatibility Policy

Legacy artifacts may be imported only through explicit migration functions.
They are never silently treated as current-schema artifacts.

The v23.9 package version is historical metadata. The new package starts at
`0.1.0` because semantic maturity must be earned by current behavior.

## 4. Deprecation

The legacy systems remain available for comparison until v1.0.0. They are not
the default runtime after v0.4.0 reproduces their useful benchmark and audit
coverage.
