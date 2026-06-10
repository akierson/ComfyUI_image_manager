# ADR-0012: Port-based drag-and-drop for lineage tree reassignment

## Status
Accepted

## Context
The lineage tree view renders a zoomable SVG tree but had no mutation actions. Users needed a way to reassign parent-child relationships directly from the tree without returning to the grid's Pick-Parent Phase. Two distinct operations were needed: reassigning a single image's parent, and bulk-reparenting all children of a node to a different parent.

## Decision
Add two **port circles** to each non-cross-chain tree node:

- **Parent port** (top-center, blue) — drag to any other node to reassign *this image's* parent. Commits immediately on drop with no confirmation dialog; the drag gesture itself communicates intent.
- **Child port** (bottom-center, amber) — drag to any other node to reassign *all direct children* of this image to that target. Always shows a confirmation dialog before committing, because the operation bulk-reparents N subtrees.

Both ports are scoped to nodes visible in the currently open tree (cross-chain targeting is deferred). Dropping on an invalid target (self, own descendants, or a collapsed node) cancels silently. After a successful drop the tree re-renders in place. The existing `POST /api/set-parent` and `POST /api/set-parents-batch` endpoints are reused.

## Alternatives considered
**Right-click context menu** — lower discoverability, doesn't communicate edge semantics visually. Rejected.

**Single port / unified drag** — ambiguous about whether the dragged image or its children move. Rejected.

## Consequences
The parent port and child port have radically different blast radii (one image vs. N subtrees). The amber color and mandatory confirmation on the child port signal this asymmetry. Users familiar with ComfyUI's own port-drag model will find the interaction familiar.
