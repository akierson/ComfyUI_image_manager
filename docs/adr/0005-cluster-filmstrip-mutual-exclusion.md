# ADR 0005: Cluster View and Filmstrip View are mutually exclusive

## Status
Accepted

## Context
The UI has two non-default view modes: Filmstrip View (chains as horizontal rows) and Cluster View (images grouped by visual similarity). The original implementation was asymmetric: entering Filmstrip exited Cluster, but entering Cluster did not exit Filmstrip — `_filmstripMode` stayed `true` silently so that exiting Cluster could restore Filmstrip. This worked as an "overlay" model but left both flags simultaneously true, making the state machine hard to reason about.

## Decision
Filmstrip View and Cluster View are mutually exclusive. Entering either always exits the other. Exiting Cluster always restores the plain grid (`load()`), never Filmstrip, because Filmstrip is guaranteed off when Cluster is entered.

## Consequences
- State is simpler: at most one of `_filmstripMode` / `_clusterMode` is `true` at any time.
- If the user is in Filmstrip and enters Cluster, exiting Cluster lands in the plain grid, not Filmstrip. This is a deliberate trade-off: re-entering Filmstrip is one click.
- Cluster View re-clusters whenever the visible set changes (sidebar filter, Hide Dates toggle, Rebuild Index, image import). Filmstrip View does not re-fetch on those events beyond the normal `renderFilmstrip()` call.
