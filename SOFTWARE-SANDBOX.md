# Software Sandbox Contract

## Purpose

Four benchmark cases execute narrowly extracted Python definitions against
trusted case-owned harnesses. Candidate code is never treated as a general
program and must pass the AST allowlist before reaching the sandbox.

## Fixed Boundary

The Docker backend requires an immutable image reference containing
`@sha256:<64 hexadecimal characters>` and launches containers with:

- networking disabled;
- a read-only root filesystem;
- a writable, size-limited `/tmp` tmpfs whose option set is exactly
  `rw,noexec,nosuid,nodev,size=<limit>`;
- all Linux capabilities dropped with no capability additions;
- exactly the required `no-new-privileges` security option, with no unconfined
  seccomp/AppArmor override;
- fixed non-root UID and GID with no added supplementary groups;
- fixed PID, memory, swap, and CPU limits;
- no effective bind mounts or volumes other than the expected `/tmp` tmpfs, no
  host devices or privileged mode, and no host or peer-container PID/IPC
  namespace;
- `--pull never`, so the inspected local digest is the executed image.

The implementation rejects policy objects that weaken these defaults.

There is no production host-Python fallback. `evaluate-recorded`, the Codex
benchmark runner, and `build-code-evidence` require a digest-pinned image when
executable software cases are in scope. Library calls without a real
`DockerSandbox` return an authoritative failure before candidate execution.

## Verification

Before executable evidence can set `sandboxed: true`, the backend:

1. inspects the live container configuration and matches every required field;
2. proves outbound network access is blocked;
3. proves the root filesystem is read-only;
4. proves `/tmp` remains writable;
5. proves the effective UID, GID, and every supplementary group are non-root;
6. terminates a non-cooperative infinite loop at the configured timeout;
7. confirms a memory-exhaustion probe is killed by the memory limit;
8. attempts named removal after every create attempt, including a nonzero or
   uncertain create result, and treats unproven removal as a failed sandbox
   result.

The evidence records the immutable image reference, resolved image ID, verified
policy, active-probe facts, verifier implementation hash, Python runtime,
timeout/memory probe outcomes, and per-case pass/reverification details.
Candidate stdout and stderr are deliberately not copied into the readiness
report.

## R05 Authority Replay

Stable R05 requires more than a self-consistent code-evidence JSON file. The
readiness manifest must bind `authority_sources.code_execution_plan`. That
strict plan names the report ID, source evaluation, and digest-pinned sandbox
image. An authority-bearing host must supply an explicit live `DockerSandbox`
whose image exactly matches the plan, reload the evaluation, rerun isolation,
timeout, memory, and all eligible case-owned checks, and reproduce the complete
report exactly.

The ordinary `build-code-evidence` command can create the local report through
Docker, but a detached report, copied probe facts, or offline rehash cannot
satisfy stable readiness. Evidence created under the removed host backend or
without the current source plan is not directly migratable and must be rebuilt
from the source evaluation.

Four live Docker integration tests exercise isolation, timeout, memory, and a
software contract. They may be skipped when no configured immutable
`PPE_TEST_DOCKER_SANDBOX_IMAGE` or usable daemon is available. Such a skip is an
environment limitation, not a passing R05 result; this local review did not
produce live Docker R05 evidence.

## Trust and Scope

The Docker daemon, container runtime, host kernel, pinned image contents, and
repository-owned harness are trusted components. This boundary is designed for
case-limited benchmark evaluation, not for exposing arbitrary multi-tenant code
execution as a network service.

Independent reproduction remains required before the project can claim stable
release completion.
