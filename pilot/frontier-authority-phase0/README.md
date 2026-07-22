# Frontier authority pilot: Phase 0

This package starts the frontier authority pilot at its honest, pre-authority
boundary. It runs the real preflight loader and execution-host gate against a
public synthetic placeholder. It does not contain or simulate a sealed
benchmark, independent authority receipt, provider, judge, reviewer, replay
operator, production credential, or paid API call.

The two status dimensions are intentionally separate:

- `machine_claim: not_evaluable` means no frontier performance conclusion is
  supported.
- `pilot_state: blocked` means authority-bearing execution must not begin until
  the listed external inputs exist and pass preflight.

## Run the dry-run

From the repository root in PowerShell:

```powershell
$env:PYTHONPATH = "src"
$outputRoot = Join-Path $env:TEMP "ppe-frontier-phase0"
python scripts/run_frontier_phase0.py --output-root $outputRoot
```

Exit code `3` is the expected Phase 0 result: the check ran successfully and
proved that the pilot is blocked. The canonical, write-once record is written
to `$outputRoot/phase0-status.json`. Repeating the same run is idempotent;
different bytes cannot overwrite an existing record. Keep this non-authority
diagnostic record outside the source tree.

The output must show all of the following:

- preflight `passed` is `false`, while the preflight report itself passes its
  structural validator;
- the real `FrontierExecutionHost` rejects the report before any runner can be
  called;
- provider, judge, reviewer, credential, external-write, and sealed-data
  activity are all `false`;
- frontier report, claim inventory, and offline replay remain unconstructed or
  not started, because local self-hashes cannot create authority.

Do not reinterpret a successful dry-run as campaign evidence. The bundled
fixture is deliberately not a frontier campaign contract and contains no
sealed content. Its only purpose is to exercise fail-closed behavior.

## Recorded Phase 0 kickoff

The pilot kickoff was executed on 2026-07-22 from release candidate commit
`c1a210fa1f63a9fff750123b877a9ff8eb18daf6`. The canonical diagnostic record
had SHA-256
`2ab819cd9b40860d26444a7e69cded95a97973b97e9d53b1578ea9c3f6efe99c` and
reported:

- `machine_claim: not_evaluable`;
- `pilot_state: blocked`;
- preflight `passed: false`;
- execution host `rejected`;
- seven explicit blockers;
- zero external activity flags.

The generated JSON is intentionally not committed: it is a reproducible local
diagnostic, not an authority artifact. This section records the kickoff and its
evidence boundary without promoting a self-produced file into campaign proof.

## Gate to Phase 1

Phase 1 remains blocked until an accountable operator supplies, through
approved local boundaries:

1. a contract-valid frozen campaign bundle and sealed-source commitments;
2. independent clock, owner, dataset-custodian, human-gold, host-capability,
   and storage verification;
3. approved provider and judge adapters with unique receipt verification;
4. independent human reviewers and adjudication under the frozen protocol;
5. three independent replay operators and machines with attestation
   verification.

Those inputs are intentionally absent from this repository. When they exist,
run the standard full-freeze preflight first. Only a structurally valid report
with `passed: true` may be handed to the execution host. Final reporting, claim
publication, and replay remain downstream gates, not Phase 0 activities.
