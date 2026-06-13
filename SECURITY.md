# Security Model

## Supported Deployment

The supported `v0.x` deployment is single-operator and local:

- the HTTP service binds to `127.0.0.1`, `::1`, or `localhost`;
- remote access terminates TLS and authentication in a same-host reverse proxy;
- external command adapters are administrator-configured trusted programs;
- benchmark and human-review artifacts may contain sensitive model outputs and
  must be protected as application data.

Direct non-loopback binding is rejected by the CLI.

## Assets

- OpenAI or provider credentials held only in process environment;
- source Prompts stored in the local SQLite job database;
- optimized Prompts, audit results, and evaluation outputs in artifact storage;
- human-review blind keys, which reveal A/B identity;
- integrity of evidence levels, hashes, and release claims.

## Trust Boundaries

1. HTTP client to local service: JSON body, optional bearer token, request-size
   and rate limits.
2. Service to provider: HTTPS Responses API request; credentials are not stored
   in artifacts or exception messages.
3. Service to external command: fixed startup command, no shell, executable
   allowlist, minimal environment, timeout, cancellation.
4. Model output to parser/auditor: untrusted text parsed, statically audited,
   hash-linked, and prevented from self-elevating evidence.
5. Reviewer packet to blind key: packet is distributable; key is retained by
   the coordinator and must not be sent to reviewers.

## Principal Abuse Paths

| Threat | Existing mitigation | Residual risk |
|---|---|---|
| Prompt injection changes optimizer authority | Source is JSON-encoded inert data; deterministic adversarial audit | Novel semantic attacks may evade static patterns |
| Credential leakage through logs/artifacts | Secrets read from environment; metadata excludes headers and response text; provider errors sanitized | A trusted external command can read files available to its OS account |
| Arbitrary command execution through API | Command is fixed at service startup; shell is disabled; executable is allowlisted | Executable allowlisting is not an OS sandbox |
| Model-generated code execution during evaluation | Four software cases extract only named definitions through a strict AST allowlist, reject dangerous constructs, and run trusted harnesses under `python -I -S` with minimal builtins and environment | Process isolation does not enforce kernel-level network, filesystem, CPU, or memory boundaries and is not a general Python sandbox |
| Remote interception or brute force | Direct non-loopback binding rejected; optional constant-time bearer auth; rate limit | Reverse proxy configuration remains operator responsibility |
| Artifact or evidence tampering | Canonical hashes, audit consistency checks, evaluation hashes, immutable manifests | Local administrator can replace both data and unsigned hashes |
| Resource exhaustion | 1 MB request limit, bounded provider timeout/retry, command timeout, per-client rate limit | One worker can still be occupied by a permitted long request |
| Cross-review identity leakage | Public packet and private key are separate, hash-linked artifacts | Coordinator mishandling can unblind reviewers |
| Restart loses or duplicates work | SQLite WAL, idempotency keys, queued/running recovery, atomic artifact replace | Provider-side effects are outside this optimizer's current scope |

## Security Acceptance

No unresolved critical finding is known for the supported local,
single-operator deployment. Internet-facing multi-user or multi-tenant
operation is out of scope for `v0.x` and would require stronger authentication,
authorization, TLS configuration, OS sandboxing, encrypted storage, quotas, and
an independent penetration test.

The software checkers permit no candidate imports, dunder access, dynamic code,
filesystem APIs, subprocesses, or unrestricted builtins. Allowed syntax and
method calls are case-bounded, and trusted harnesses own all test inputs.
Ordinary subprocess isolation is still insufficient for R05 completion; a real
OS/container sandbox must enforce network, filesystem, CPU, and memory limits.
