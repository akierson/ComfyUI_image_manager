# ADR 0015: Send to Comfy — Parent Injection via Save-Node Swap

## Status
Accepted

## Context
The original Send to Comfy implementation conflated two distinct concerns: *pipeline input* (what image flows through the workflow) and *parent attribution* (what image the saved output is a child of). It did this by swapping the first `LoadImage` node with `ManagedLoadImage` — so the selected image became both the pipeline input and the lineage parent.

This approach has two problems:

1. **Outputs aren't tracked.** If the workflow uses a plain `SaveImage` (the common case), the output is never written to the managed folder. The `ManagedLoadImage` swap only ensures `_detect_parent()` can find a parent; it does nothing if no `ManagedSaveImage` is present to call it.
2. **`LoadImage` is not always the right injection point.** Workflows that don't use the parent image as a direct pipeline input (e.g., style reference via ControlNet, or a fresh generation that should be attributed to a prior image for organisational reasons) have no `LoadImage` to swap. Even when a `LoadImage` is present, replacing it silently changes what the user's workflow actually processes.

The alternatives considered:

- **Keep the `LoadImage` swap, also swap `SaveImage`**: creates two `ManagedLoadImage` nodes in the graph — one swapped from `LoadImage`, one new — causing `_detect_parent()` to log a multi-parent warning and fall back to root. Also still forces the selected image into the pipeline regardless of the user's intent.
- **Add a floating `ManagedLoadImage` (not wired)**: unreliable. ComfyUI's executor excludes nodes not reachable from an `OUTPUT_NODE`, so a floating node never appears in the `prompt` dict that `_detect_parent()` scans.
- **Wire `ManagedLoadImage.managed_name` directly to `ManagedSaveImage.parent_name`**: explicit, bypasses `_detect_parent()` entirely, keeps the node in the execution graph, and doesn't touch the image pipeline.

## Decision
Send to Comfy now performs three graph mutations on the embedded workflow before loading it to canvas:

1. Every `SaveImage` node is swapped to `ManagedSaveImage`.
2. A single new `ManagedLoadImage` node is injected with its widget value set to the selected image's relative filename.
3. `ManagedLoadImage.managed_name` (output slot 2) is wired to the `parent_name` input of every swapped `ManagedSaveImage` node.

Original `LoadImage` nodes are left entirely untouched. If no `SaveImage` nodes are found but `ManagedSaveImage` nodes already exist, the new `ManagedLoadImage` is wired to those. If neither is present, the workflow is loaded as-is with no mutations.

Both the `GET /api/workflow/{uuid}` and `POST /api/send-to-comfy/{uuid}` endpoints apply the same logic.

## Consequences
- Outputs are always tracked: `ManagedSaveImage` is guaranteed to be in the graph after Send to Comfy, so every generation from the loaded workflow lands in the managed folder.
- Parent attribution is explicit: the `parent_name` wire makes the relationship unambiguous at graph level, visible to the user on the canvas, and independent of `_detect_parent()`.
- Pipeline input is the user's responsibility: `LoadImage` nodes keep their original values. The user adjusts them after the canvas loads if needed. This is consistent with ADR 0003 (Send to Comfy loads canvas, does not execute).
- `_detect_parent()` remains in place for manually constructed workflows where the user places a `ManagedLoadImage` in the pipeline without using Send to Comfy.
- LiteGraph graph surgery requires generating collision-free node and link IDs. Node ID is chosen as `max(existing_ids) + 1`; link IDs similarly.
