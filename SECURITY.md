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
- source Prompts stored in the local SQLite job database and optimization
  artifacts so deterministic E1 audit replay is self-contained;
- optimized Prompts, audit results, and evaluation outputs in artifact storage;
- human-review blind keys, which reveal A/B identity;
- visual-review blind keys, which reveal image A/B identity and source paths;
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
6. Evidence engine to provider receipt authority: the host-supplied
   `ModelCallReceiptVerifier` must independently bind each recorded call to its
   request, response, and evaluation context. Artifact metadata is not trusted.
7. Evidence engine to reviewer authority: the host-supplied
   `ReviewerSubmissionVerifier` must attest identity, qualification,
   independence, packet, and submission without exposing private identity data.
8. Image evidence to provider and reviewer authorities: host-supplied
   `ImageGenerationReceiptVerifier` and `VisualReviewerSubmissionVerifier`
   implementations must independently bind every generated asset and every
   qualified visual-review submission to their exact source context.

## Principal Abuse Paths

| Threat | Existing mitigation | Residual risk |
|---|---|---|
| Prompt injection changes optimizer authority | Source is JSON-encoded inert data; deterministic adversarial audit | Novel semantic attacks may evade static patterns |
| Credential leakage through logs/artifacts | Secrets read from environment; metadata excludes headers and response text; provider errors sanitized | A trusted external command can read files available to its OS account |
| Arbitrary command execution through API | Command is fixed at service startup; shell is disabled; executable is allowlisted | Executable allowlisting is not an OS sandbox |
| Model-generated code execution during evaluation | Every executable software check requires a case-owned harness in a digest-pinned, policy-inspected Docker container with no network, read-only root, non-root identity, dropped capabilities, and PID/memory/CPU limits; missing or failed sandbox verification stops before candidate execution | Docker daemon, container runtime, pinned image, and host-kernel security remain trusted; this is not a multi-tenant arbitrary-code service |
| Remote interception or brute force | Direct non-loopback binding rejected; optional constant-time bearer auth; rate limit | Reverse proxy configuration remains operator responsibility |
| Artifact or evidence tampering | Canonical hashes, exact source/optimized audit replay, source-reconstructed reports, externally verified unique model/reviewer receipts, semantic replicate fingerprints, and blind-packet protocol replay | SHA-256 links are not signatures; the external receipt authorities and their host integration remain trusted |
| Resource exhaustion | 1 MB request limit, bounded provider timeout/retry, command timeout, per-client rate limit | One worker can still be occupied by a permitted long request |
| Cross-review identity leakage or probe gaming | Public HMAC-v3 packets use a fresh 256-bit coordinator key for opaque IDs and A/B assignment, expose only its commitment, and keep the sampling seed, key, source case IDs, probe markers, difficulty, and optimized labels private; per-reviewer reversed probes are derived and fail closed | Coordinator mishandling can unblind reviewers, and a static packet cannot prevent a deliberate reviewer from recognizing repeated output content |
| Visual-review unblinding or fabricated image authority | `balanced_hmac_sha256_v2` creates a fresh 256-bit secret per packet/key pair; the public protocol exposes only its commitment, while the private key retains the seed, source mapping, and optimized labels. R06 replays the strict visual-review plan and requires unique external generation and reviewer receipts | The coordinator, receipt authorities, image provider, and identity-verification integration remain trusted |
| Restart loses or duplicates work | SQLite WAL, idempotency keys, queued/running recovery, atomic artifact replace | Provider-side effects are outside this optimizer's current scope |

## Security Acceptance

The 2026-07-21 hardening removed the production host-Python fallback from
software evaluation. Extracted model-produced Python reaches execution only
through an explicit `DockerSandbox`; the CLI and benchmark runner require a
digest-pinned image whenever executable software cases are selected. Missing
Docker or live policy mismatch is a closed execution failure. R05 evidence also
fails closed when its pre-execution isolation, timeout, or memory probes fail.
Internet-facing multi-user or multi-tenant operation remains
out of scope for `v0.x` and would require stronger authentication,
authorization, TLS configuration, OS sandboxing, encrypted storage, quotas, and
an independent penetration test.

The software checkers permit no candidate imports, dunder access, dynamic code,
filesystem APIs, subprocesses, or unrestricted builtins. Allowed syntax and
method calls are case-bounded, and trusted harnesses own all test inputs.
R05 evidence is accepted only when Docker's inspected policy matches the fixed
contract and active probes confirm network denial, read-only root, writable
temporary storage, non-root UID/GID/supplementary groups, timeout termination,
and out-of-memory enforcement. Runtime inspection rejects unexpected effective
bind or volume mounts, added capabilities or groups, joined PID/IPC namespaces,
extra or unconfined security options, and conflicting or extra `/tmp` mount
options. Every create attempt enters named cleanup; an unproven removal
invalidates the run rather than returning a passing result.

R05 does not trust a detached code-evidence report. Readiness must load the
bound `code_execution_plan`, receive an explicit `DockerSandbox` whose immutable
image matches that plan, rerun live policy/probe checks and all case-owned
verification, and reproduce the complete report. Offline self-hashed facts do
not cross this authority boundary. R06 likewise requires the bound
`visual_review_plan`, exact replay of its manifest/packet/key/submission/profile
bundle, and unique receipts from both image-generation and visual-reviewer
authorities. The ordinary visual aggregation CLI has no verifier injection and
is diagnostic only.

E3 is unavailable without a configured `ModelCallReceiptVerifier`. E4 requires
that verifier plus a configured `ReviewerSubmissionVerifier`; coordinator-only
adjudications cannot substitute for direct reviewer consensus. The
standalone CLI intentionally has no implicit trust adapter: replicate commands
can produce reproducible diagnostics, while authoritative aggregation and
readiness must be hosted through the Python API with independently configured
verifiers. Self-reported response IDs, reviewer IDs, usage, or locally generated
hashes cannot substitute for those receipts.
