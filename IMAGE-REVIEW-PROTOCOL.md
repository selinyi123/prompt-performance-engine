# Actual Image Review Protocol

## Scope

R06 evaluates five `image_generation` benchmark cases using matched image
generation and qualified blind visual review. Text-only Prompt judging cannot
substitute for this protocol.

## Generation

Each case requires two independently generated PNG assets:

- `baseline`: image Prompt produced by the original Prompt;
- `optimized`: image Prompt produced by the optimized Prompt.

Both variants must use the same image-generation provider and model family.
The generation plan records the exact Prompt, provider, model, settings,
provider call identifier, and relative asset path.

Stable R06 authority does not trust those identifiers merely because they are
present in a local manifest. A host-supplied `ImageGenerationReceiptVerifier`
must bind every baseline and optimized asset to its suite, case, variant,
provider, model, settings hash, Prompt hash, asset hash, and call identifier,
and must return one valid unique receipt digest per generation.

`register-image-generations` verifies every PNG before creating a manifest:

- valid PNG signature, chunk boundaries, and CRC values;
- valid IHDR, IDAT, decompression, and IEND termination;
- at least 256 pixels on each edge;
- a supported non-interlaced 8-bit raster format;
- a complete pixel stream with substantive visual variation;
- immutable file and Prompt SHA-256 hashes.

## Blind Review

`create-visual-review-packet` uses `balanced_hmac_sha256_v2`. It generates a
fresh 256-bit secret for every packet/key pair and uses HMAC-SHA256 to assign
baseline and optimized images to labels A and B and to derive reviewer-specific
opaque delivery paths. The public packet contains the brief, rubric, opaque
image paths, dimensions, and hashes; its public `protocol` block contains only
the protocol name and SHA-256 key commitment. It contains neither the secret,
seed, source paths, nor hidden mapping. The private key contains the secret,
seed, source-to-delivery mapping, and optimized labels and must not be shared
until all reviewer submissions have been hash-locked. The generation manifest
must also remain unavailable to reviewers during judging because its asset
hashes could otherwise be used to reverse-map the public packet.

The private key records the packet-creation seed, generation-manifest
commitment, secret, and its public commitment. During aggregation, the verifier
deterministically rebuilds every A/B assignment, optimized label, opaque
delivery path, source path, and asset hash from the manifest, public packet,
reviewer identifier, saved seed, and secret. Recomputing `key_sha256` after
editing a hidden mapping therefore does not make the edited key authoritative.

Each submission must:

- cover all five cases exactly once;
- select A, B, or tie;
- score A and B from 1 to 5 on every case rubric criterion;
- provide a substantive written reason;
- carry a valid content hash.

Each reviewer profile must attest at least two years of visual-review
experience, image-generation domain relevance, independence, and conflict
disclosure. These attestations are hash-bound but still require external
identity verification. Stable R06 requires a host-supplied
`VisualReviewerSubmissionVerifier` to bind reviewer identity and qualification
to the exact profile, generation manifest, packet, and submission and to return
a valid unique receipt digest for every reviewer.

## Aggregation

`aggregate-visual-review` validates all assets, packets, secret keys,
submissions, and reviewer profiles before revealing mappings. It emits:

- per-case baseline and optimized image hashes;
- provider call identifiers and image dimensions;
- review counts and consensus outcomes;
- optimized score deltas;
- wins, ties, losses, and unresolved cases;
- reviewer, packet, submission, and generation-manifest hashes.

The ordinary CLI calls this aggregator without either receipt verifier. It can
produce a strict, source-linked diagnostic report, but its generation and
reviewer receipt gates remain false and it cannot satisfy R06.

## Stable R06 Authority

The readiness manifest must bind `authority_sources.visual_review_plan`. That
strict plan names one generation manifest and, for every reviewer, exactly one
packet, private key, submission, and profile. All paths are relative,
contained, and unique. An authority-bearing host must reload the complete plan,
inject both receipt verifiers, replay every private/public mapping, rebuild the
report, and require exact structured equality.

R06 passes only when five cases have two real images each, all five receive
three qualified blind reviews, every external asset hash revalidates, no case
remains unresolved, all generation receipts are unique and verified, all
visual-reviewer receipts are unique and verified, and the report is reproduced
exactly from the bound plan. A detached report, locally generated receipt hash,
or rehashed key is not authority. Visual evidence produced before
`balanced_hmac_sha256_v2` and strict plan replay is not directly migratable; it
must be regenerated from current source assets.

## Commands

```powershell
python -m prompt_performance_engine register-image-generations `
  evidence\image\generation-plan.json `
  --output evidence\image\generation-manifest.json

python -m prompt_performance_engine create-visual-review-packet `
  evidence\image\generation-manifest.json `
  --reviewer reviewer-1 --seed 20260614 `
  --packet evidence\image\reviewer-1-packet.json `
  --key evidence\image\reviewer-1-key.json

python -m prompt_performance_engine validate-visual-review-submission `
  evidence\image\reviewer-1-packet.json `
  evidence\image\reviewer-1-submission.json

python -m prompt_performance_engine aggregate-visual-review `
  evidence\image\review-plan.json `
  --output evidence\image\image-review.json
```

These commands create and validate local artifacts. The last command is
diagnostic because the CLI has no trusted receipt-verifier injection. Stable
R06 assessment must use an authority-bearing Python host with the two verifiers
and the same bound `visual-review-plan`.
