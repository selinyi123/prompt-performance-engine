# Benchmark

`core-18.json` is the first non-placeholder benchmark definition. It contains
six jobs and eighteen cases across software engineering, research, writing,
image generation, creative design, and business strategy.

Each domain includes normal, difficult, and adversarial coverage. The suite is
a test definition, not evidence that the optimizer wins. E2 evidence requires
recorded matched executions, hard-check results, and at least two blind judges.

The legacy project's generated cases were not migrated because most repeated
the same generic sentence with changed identifiers and domains.

`catalog-60.json` combines the core suite with `extension-42.json`. The catalog
contains twelve domains, five cases per domain, and twelve adversarial cases.
It meets the definition-coverage gate, but it is not performance evidence until
the recorded executions and blind judgments exist.

Run a resumable real-model trial with:

```powershell
python scripts\run_codex_benchmark.py --domains software_engineering
```

Each output directory is bound to an immutable configuration manifest covering
the parsed benchmark definition, model, reasoning effort, protocol, candidate
count, and blind seed. A mismatched rerun is rejected instead of reusing stale
evidence.

Current code and image cases are text-only proxies. Stable evidence for those
domains additionally requires executable code checks and actual image
generation with qualified visual review.
