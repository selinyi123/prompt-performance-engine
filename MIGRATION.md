# Legacy Migration

## Prompt Migration

```powershell
python -m prompt_performance_engine migrate-legacy-prompt legacy-prompt.md `
  --legacy-version 3.0 --output migrated-request.json
```

The source Prompt, domain hint, and source hash are preserved. Historical
scores and maturity labels are deliberately discarded.

## Audit Reference Import

```powershell
python -m prompt_performance_engine import-legacy-audit legacy-audit.json `
  --output legacy-reference.json
```

Imported audits remain `E0` references. The importer records all discovered
version fields and flags conflicts, but never accepts legacy E1-E5,
production-certified, award-level, or universal-best claims as current
evidence.

## 0.4.0 Contract Hardening

The 2026-07-21 security and evidence changes intentionally fail closed for old
gate-pending artifacts and invocations:

- The stable artifact/request/review/readiness Schema family is now `2.0.0`.
  Package `0.3.0` optimization artifacts retain their original `1.0.0`
  identity and remain readable through the explicit legacy validator path, but
  they cannot claim current authority. Other changed `1.0.0` evidence formats
  must be regenerated as `2.0.0`; re-labeling old JSON is not migration.

- HTTP optimization JSON must include `schema_version`, `source_prompt`, `mode`,
  and `output_format`; unknown fields and non-string array elements are rejected.
  `candidate_count` is now part of the request contract.
- Model responses must be one strict JSON object containing only the
  `optimized_prompt` string. Tag blocks, fences, plain text, duplicate fields,
  extra commentary, and selector-commentary responses must be regenerated.
- Optimization artifacts now include the hash-bound source Prompt and replay
  both source and optimized audits. Every model call must also carry its
  `purpose`, `request_sha256`, and `response_sha256`, and canonical candidates
  must carry `strategy` and `strategy_focus`. Old E1 artifacts missing any of
  those facts, or with audit facts that cannot be replayed, must be regenerated.
- Executable software evaluation requires `--sandbox-image` with an immutable
  digest. The former host backend is not migratable evidence. R05 readiness now
  also requires `authority_sources.code_execution_plan` and an explicit live
  `DockerSandbox` that matches the plan, reruns the bound evaluation and all
  probes, and reproduces the complete report. Existing detached or offline
  self-hashed code-evidence reports cannot be promoted or directly migrated;
  regenerate them from the source evaluation under the current plan.
- A single evaluation is capped at E2. E3 consumers must use a validated
  `benchmark_replicate` report together with all source run directories;
  readiness manifests must add `authority_sources.benchmark_run_directories`.
  Provider/model names, response IDs, status, and usage remain diagnostic.
  Authority now also requires a host-supplied `ModelCallReceiptVerifier` that
  independently verifies every request/response/context binding and returns a
  unique receipt digest. Old reports without verified receipt bundles must be
  regenerated; the standalone CLI intentionally cannot self-authorize E3. Each
  run must now contain the canonical resolved `benchmark-definition.json` used
  to create it. Old runs that only repeat a claimed definition hash cannot be
  migrated into E3 evidence and must be regenerated.
- Human-review plans must add `replicate_report` and `run_directories`, and
  readiness manifests must bind that plan through
  `authority_sources.human_review_plan`. Packets, keys, and submissions must be
  regenerated under `balanced_round_robin_hmac_sha256_v3`; v1/v2 packet and key
  pairs are not migratable. Public items now contain only opaque IDs, rubrics,
  A/B outputs, and a blinding-key commitment; the sampling seed, fresh 256-bit
  blinding key, source identities, probe markers, and optimized labels exist
  only in the key. At least two reversed
  probes, complete and consistent per-reviewer protocol results, non-degenerate
  base A/B selections, full overlap, direct reviewer consensus, positive human
  improvement, and unique receipts from a host-supplied
  `ReviewerSubmissionVerifier` are required. v1, disjoint, or receipt-free
  packets/reports cannot reach E4. Coordinator-only adjudications remain
  diagnostic and make the affected report ineligible for E4.
- Visual-review packet/key pairs must be regenerated under
  `balanced_hmac_sha256_v2`. Each pair now uses a fresh 256-bit secret; the
  public protocol retains only its commitment, while the private key retains
  the secret, seed, source-to-delivery mapping, and optimized labels. R06
  readiness must add `authority_sources.visual_review_plan`, exactly replay the
  generation manifest and every packet/key/submission/profile tuple, and use
  host-supplied `ImageGenerationReceiptVerifier` and
  `VisualReviewerSubmissionVerifier` implementations that return unique bound
  receipts. Older visual packets, keys, and detached image reports are not
  directly migratable and must be regenerated from their current source assets.
- `validate-benchmark-replicates`, `validate-human-review`, and
  `validate-readiness` now require their source bundle or source manifest;
  detached report-only validation no longer grants authority. Ordinary CLI
  aggregation has no implicit receipt verifier or live readiness sandbox, so it
  remains diagnostic unless an authority-bearing host calls the Python API.
