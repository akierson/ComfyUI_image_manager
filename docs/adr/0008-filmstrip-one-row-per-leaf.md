# ADR 0008: Filmstrip View Shows One Row Per Same-Chain Leaf

## Status
Accepted

## Context

The original Filmstrip View showed one row per lineage chain (one row per `root_uuid`), with only the tip and its immediate parent displayed. This gave a quick chain overview but lost information when chains branched — only one branch tip was visible, and the "history leading to this image" framing was absent.

The redesign pivots to a leaf-first model: each Same-Chain Leaf (an image with no same-chain children) gets its own row showing its full same-chain ancestor strip.

Two alternatives were considered:

**Option A (chosen):** One row per Same-Chain Leaf, full same-chain ancestry, ancestors repeated when a chain branches.

**Option B:** One row per chain (existing model), but extend the strip to show all ancestors of the tip rather than just the tip+parent. Branches remain collapsed to a single tip.

## Decision

Option A. Filmstrip View shows one row per Same-Chain Leaf. Branched chains produce multiple rows; shared ancestors are repeated without deduplication. Cross-chain links (fork origins) are not followed — strips stop at `root_uuid` boundaries.

## Consequences

- The `/api/chains` endpoint is replaced by `/api/leaf-strips`, which returns one entry per Same-Chain Leaf with its full same-chain ancestry newest-first. All existing `/api/chains` tests are replaced.
- Branched chains appear as multiple rows — users see each branch as a distinct lineage path, which is the intended framing.
- Shared ancestors are repeated across rows. For chains with many branches this increases visual bulk, but deduplication was rejected because it would require a new collapsed-branch UI concept that complicates the row model.
- Cross-chain children (forks) do not disqualify an image from being a Same-Chain Leaf, so a chain origin that spawned a fork but has no same-chain children still appears as its own leaf row.
- Option B was rejected because collapsing all branches to a single tip hides the branching structure that the redesign is meant to expose.
