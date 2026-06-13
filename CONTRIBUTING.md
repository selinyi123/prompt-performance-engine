# Contributing

## Development Check

Use Python 3.11 or newer.

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
python scripts/validate_release.py
```

## Change Rules

- Preserve request, artifact, and evidence contracts unless a versioned
  migration is included.
- Add focused tests for changed behavior.
- Keep provider credentials and raw private responses out of the repository.
- Do not turn static inspection or a narrow benchmark into a universal quality
  claim.
- Retain failed benchmark evidence when it materially changes the interpretation
  of a result.

## Pull Requests

Explain the user-visible behavior, verification performed, compatibility
effects, and remaining limitations. Performance claims require matched
original-versus-optimized execution and the evidence gates defined in
`ACCEPTANCE-CRITERIA.md`.
