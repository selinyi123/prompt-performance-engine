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

`register-image-generations` verifies every PNG before creating a manifest:

- valid PNG signature, chunk boundaries, and CRC values;
- valid IHDR, IDAT, decompression, and IEND termination;
- at least 256 pixels on each edge;
- a supported non-interlaced 8-bit raster format;
- a complete pixel stream with substantive visual variation;
- immutable file and Prompt SHA-256 hashes.

## Blind Review

`create-visual-review-packet` independently randomizes baseline and optimized
images to labels A and B for each reviewer. It copies the review assets to
reviewer-specific opaque filenames so baseline/optimized source names cannot
leak the mapping. The public packet contains the brief, rubric, opaque image
paths, dimensions, and hashes, but not the source paths or hidden mapping.
The private key contains the source-to-delivery mapping and must not be shared
until all reviewer submissions have been hash-locked. The generation manifest
must also remain unavailable to reviewers during judging because its asset
hashes could otherwise be used to reverse-map the public packet.

Each submission must:

- cover all five cases exactly once;
- select A, B, or tie;
- score A and B from 1 to 5 on every case rubric criterion;
- provide a substantive written reason;
- carry a valid content hash.

Each reviewer profile must attest at least two years of visual-review
experience, image-generation domain relevance, independence, and conflict
disclosure. These attestations are hash-bound but still require external
identity verification.

## Aggregation

`aggregate-visual-review` validates all assets, packets, secret keys,
submissions, and reviewer profiles before revealing mappings. It emits:

- per-case baseline and optimized image hashes;
- provider call identifiers and image dimensions;
- review counts and consensus outcomes;
- optimized score deltas;
- wins, ties, losses, and unresolved cases;
- reviewer, packet, submission, and generation-manifest hashes.

R06 passes only when five cases have two real images each, all five receive
three qualified blind reviews, every external asset hash revalidates, and no
case remains unresolved.

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
