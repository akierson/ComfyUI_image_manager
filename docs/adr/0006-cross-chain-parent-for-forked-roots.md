# ADR 0006: Cross-Chain Parent Link for Forked Root Images

**Status:** Accepted

## Context

A Chain Fork detaches an image and its descendants into a new `root_uuid`/`root_name`. The user still wants to know where the forked image came from. Two options were considered:

**Option A (chosen):** Preserve `parent_uuid` across chains. The forked image becomes a root image (`uuid == root_uuid`) but retains a `parent_uuid` pointing to an image in the source chain. The link is traversable in both directions.

**Option B:** Clear `parent_uuid` on fork; record origin in a separate metadata field as a non-traversable annotation ("forked from…"). The forked image is a clean root with no structural link to its origin.

## Decision

Option A. The `parent_uuid` field is preserved across chain boundaries after a fork.

## Consequences

- The invariant *"a root image has no parent_uuid"* no longer holds. Root image is now defined as `uuid == root_uuid`, independent of whether `parent_uuid` is null.
- Cycle detection must account for cross-chain edges (a fork chain must not be forked back into its own ancestry).
- The lineage tree UI must handle rendering a child that belongs to a different chain — the fork branch is visually distinct (e.g. labelled with the target chain's root name) rather than a normal in-chain branch.
- Option B was rejected because it introduces a second, weaker relationship concept alongside `parent_uuid`, requiring consumers to check two fields to understand full provenance.
