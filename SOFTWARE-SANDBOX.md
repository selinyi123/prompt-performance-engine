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
- a writable, size-limited `/tmp` tmpfs;
- all Linux capabilities dropped;
- `no-new-privileges`;
- non-root UID and GID;
- fixed PID, memory, swap, and CPU limits;
- no bind mounts, volumes, host devices, privileged mode, or host namespaces;
- `--pull never`, so the inspected local digest is the executed image.

The implementation rejects policy objects that weaken these defaults.

## Verification

Before executable evidence can set `sandboxed: true`, the backend:

1. inspects the live container configuration and matches every required field;
2. proves outbound network access is blocked;
3. proves the root filesystem is read-only;
4. proves `/tmp` remains writable;
5. proves the process is non-root;
6. terminates a non-cooperative infinite loop at the configured timeout;
7. confirms a memory-exhaustion probe is killed by the memory limit;
8. removes every probe and evaluation container in a `finally` path.

The evidence records the immutable image reference, resolved image ID, verified
policy, active-probe facts, verifier implementation hash, Python runtime, exit
status, duration, stdout, and stderr.

## Trust and Scope

The Docker daemon, container runtime, host kernel, pinned image contents, and
repository-owned harness are trusted components. This boundary is designed for
case-limited benchmark evaluation, not for exposing arbitrary multi-tenant code
execution as a network service.

Independent reproduction remains required before the project can claim stable
release completion.
