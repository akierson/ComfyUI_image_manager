# ADR 0007: Always-On Card Selection (Single-Click Selects, Double-Click Opens)

## Status
Accepted

## Context
The original interaction model: single-click opens the lightbox; link mode is entered via a toolbar button and temporarily overrides click behavior so cards can be selected as child/parent. Adding mass linking (N children → one parent) required extending link mode to support multi-select, which would have meant a second modal layer on top of an already-modal link flow.

## Decision
Replace the modal click-override with always-on file-manager-style selection. Single-click selects/deselects a card in all view modes. Double-click opens the lightbox. Shift-click extends selection from the anchor in DOM order (skipping headers). Ctrl-click toggles individual cards. The existing link-mode state machine (`_linkMode`) is replaced by Card Selection + Pick-Parent Phase: the user selects children first, then presses L (or the toolbar button) to enter Pick-Parent Phase, then clicks the parent.

## Consequences
- Double-click replaces single-click to open the lightbox — a breaking change to the most common interaction. Users who click to open must learn the new gesture.
- The `_linkMode` / `_linkChild` / `_linkParent` JS state collapses into a single selection array plus a boolean `_pickingParent` flag. Simpler state machine.
- Mass link (N children, one parent) works naturally: select N cards, press L, click parent.
- The Selection Bar generalizes the former `#link-confirm-bar` and becomes the host for future bulk operations (bulk delete, bulk fork) without needing new toolbar controls.
- Selection persists across scroll and view-mode-internal re-renders; it must be cleared on full view-mode switches (grid ↔ filmstrip ↔ cluster ↔ flat) to avoid stale cross-view selections.
