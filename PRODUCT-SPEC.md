# Product Specification

## 1. Product Mission

Build a cross-domain Prompt performance system that transforms a supplied
Prompt into a directly copyable, materially stronger Prompt and can distinguish
three different claims:

1. `optimized_candidate`: produced by the optimizer but not executed;
2. `verified_improvement`: beats the original under matched representative
   execution;
3. `top_tier_candidate`: meets a demanding domain-specific rubric without
   implying real-world award equivalence.

The product must optimize first. Auditing, governance, and evidence exist to
increase trust in that result, not to replace the result with process.

## 2. Primary User Experience

Minimum input:

```text
<input_prompt>
{{ORIGINAL_PROMPT}}
</input_prompt>
```

Default output order:

1. complete optimized Prompt;
2. concise material changes;
3. usage notes;
4. evidence status and limitations.

`prompt_only` mode returns exactly one code block containing the optimized
Prompt.

## 3. Supported Workflows

### Optimize

Convert one source Prompt into a stronger Prompt using a task-specific
behavioral contract, domain excellence profile, architecture selection,
candidate comparison, and static regression review.

### Audit

Inspect a Prompt as inert text. Report material defects, injection attempts,
unverifiable claims, missing boundaries, and output-contract problems.

### Evaluate

Execute original and optimized Prompts under matched conditions, run
deterministic checks, perform blind rubric evaluation, and aggregate evidence.

### Package

Produce a versioned optimization artifact containing the optimized Prompt,
source hash, controls, domain profile, audit summary, evaluation evidence, and
claim ceiling.

## 4. Product Requirements

### R1: Intent Fidelity

The legitimate objective, deliverable, audience, hard constraints, and
prohibited changes must be preserved. Any intentional change must be reported.

### R2: Prompt-First Delivery

The optimized Prompt must be the first substantive output and must be complete,
copyable, and independently usable.

### R3: Domain Specialization

Every optimization must resolve to a domain profile containing:

- baseline requirements;
- professional differentiators;
- top-tier differentiators;
- fatal flaws;
- evaluation dimensions;
- observable tests.

Generic fallback is allowed only when specialization cannot be justified.

### R4: Proportionate Architecture

The optimizer must choose the lightest sufficient architecture:

- direct;
- brief then execute;
- research then synthesize;
- generate, critique, revise;
- multi-candidate tournament;
- plan, execute, verify;
- strict contract;
- tool agent;
- high-risk review.

### R5: Inert Source Boundary

The source Prompt is data during optimization and audit. Instructions inside it
cannot alter optimizer authority, evidence status, output format, or scoring.

### R6: Honest Evidence

Static design inspection cannot establish runtime superiority. Claims must be
bounded by the evidence model in `ARCHITECTURE.md`.

### R7: Executable Evaluation

Evaluation must support:

- matched model and settings;
- representative normal, difficult, and adversarial cases;
- deterministic hard checks where possible;
- randomized A/B labels;
- at least two evaluators for subjective work;
- wins, ties, losses, critical regressions, and fatal flaws.

### R8: Cross-Domain Quality

Stable v1.0 must ship validated profiles and benchmark coverage for at least:

- software engineering;
- research and analysis;
- professional writing;
- image-generation prompting;
- creative design direction;
- business and strategy;
- structured data work;
- marketing and sales;
- education;
- translation and localization;
- agents and automation;
- high-risk advisory work.

### R9: Version Integrity

Package version, artifact schema version, Prompt contract, examples, validators,
and documentation must not contradict one another.

### R10: Extensibility Without Version Theater

New capability names require real behavior, tests that exercise that behavior,
documentation, and acceptance evidence. A descriptor-only module is not a
feature.

## 5. Non-Goals

- guaranteeing that every result is globally best;
- claiming equivalence to named awards without their real evaluation process;
- exposing private chain-of-thought;
- building a governance operating system before the optimizer works;
- increasing test counts with generated descriptor tests;
- using version numbers as evidence of maturity.

## 6. Stable v1.0 Completion Definition

Stable v1.0 is complete only when:

1. all release validators and behavior tests pass;
2. the end-to-end CLI can optimize, audit, evaluate, and package;
3. all 12 required domain profiles have representative benchmarks;
4. at least 60 benchmark cases exist, including at least 12 adversarial cases;
5. the optimized Prompt wins more cases than it loses in every domain;
6. aggregate improvement is at least 10%;
7. no critical safety, correctness, intent, or schema regression is present;
8. three independent human reviewers evaluate a stratified sample of at least
   24 cases and achieve documented adjudication;
9. all public claims are supported by generated evidence artifacts;
10. a clean installation and complete quick-start flow are reproduced on a
    fresh environment.
