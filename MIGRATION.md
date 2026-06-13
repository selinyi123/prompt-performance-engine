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
