# ADR 0002: Parentage Detected via ManagedLoadImage Only

## Status
Accepted

## Context
When saving a new image, the system needs to determine whether it is a child of an existing managed image. ComfyUI's standard `LoadImage` node could also reference managed images, but its presence is ambiguous — it might be loading an unrelated reference image, not the "parent" being refined.

## Decision
Parentage is only established when the current workflow contains a `ManagedLoadImage` node referencing a managed image. Standard `LoadImage` nodes are ignored for lineage purposes. Using `ManagedLoadImage` is the explicit opt-in signal that this generation is a refinement of that image.

## Consequences
- No false parent assignments from reference images, ControlNet inputs, or other incidental LoadImage uses.
- Users must use `ManagedLoadImage` (not `LoadImage`) when refining a managed image — otherwise the output is saved as a root with no parent.
- "Send to Comfy" replaces the source node with `ManagedLoadImage` automatically, so the round-trip workflow always preserves lineage.
