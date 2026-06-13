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
count, blind seed, evaluation/verifier implementation hash, Python version, and
platform. A mismatched rerun is rejected instead of reusing stale evidence.

All five software cases now have authoritative machine verification. Pagination,
concurrency, endpoint-contract, and CLI outputs are reduced to named Python
definitions through a strict AST allowlist and exercised by trusted hidden
harnesses in isolated `python -I -S` subprocesses. The rolling-migration case
must emit an exact JSON compatibility matrix and destructive sequencing
contract.

These are narrow case verifiers, not permission to execute arbitrary generated
Python and not proof of OS-level sandboxing. Image cases remain text-only
proxies until matched image generation and qualified visual review are
recorded.
