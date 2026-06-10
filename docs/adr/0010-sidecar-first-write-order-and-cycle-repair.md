# ADR 0010: Sidecar-First Write Order and Cycle Detection in scan_and_import

**Status:** Accepted

## Context

Lineage mutations (`set_parent`, `promote_child`, `fork_chain`, `swap_adjacent`) commit changes to SQLite **before** rewriting sidecar JSONs. If the server restarts between the DB commit and the completion of sidecar rewrites, `scan_and_import` wipes the DB and rebuilds from the partially-updated sidecars. Because some sidecars still carry pre-mutation `parent_uuid` values, the rebuild can introduce `parent_uuid` cycles (e.g. A→B in DB, B→A still in sidecar → after rebuild: A.parent_uuid=B and B.parent_uuid=A).

A cycle in `parent_uuid` values causes the cycle-check loop in `set_parent` — which walks ancestor pointers with no visited-set guard — to spin forever in the aiohttp event loop thread, permanently freezing ComfyUI until the process is killed.

CONTEXT.md declares sidecar JSON as the authoritative source of truth and SQLite as a derived index. The current write order inverts this: DB is durably committed before sidecars are durable, making DB the de-facto truth for the crash window.

## Decision

**1. Sidecar-first write order.** All lineage mutations must write (and fsync) every affected sidecar JSON before issuing `con.commit()`. A crash before the DB commit leaves the DB stale; `scan_and_import` corrects it on next startup. A crash after all sidecars are written but before DB commit also leaves DB stale; `scan_and_import` again corrects it. Neither window can produce a cycle.

**2. Cycle detection and repair in `scan_and_import`.** After rebuilding the in-memory record set from sidecars, detect any `parent_uuid` cycles using a visited-set walk. When a cycle is found, break it by nulling the `parent_uuid` of the image whose pointer closes the loop (the "youngest" image in the cycle by `created_at`, or the last one encountered if timestamps tie). Log a `[image_manager] scan_and_import: cycle detected and repaired` warning with the UUIDs involved. The repaired image becomes an orphan root; its data is not lost.

**3. Visited-set guard in `set_parent` cycle check.** The existing `while cursor:` ancestor walk gains a `visited` set. If `cursor` is seen twice, the walk terminates and raises `ValueError("existing cycle in DB: walk terminated")` rather than looping forever. This is a defensive backstop; under correct write ordering this path should never be reached.

## Consequences

- All mutation functions must be audited for the new write order: sidecar writes complete before any `con.commit()`.
- `scan_and_import` now performs a post-load cycle check pass; O(N) over all managed images on startup.
- Any cycles introduced by prior versions of the code are silently repaired on first startup after this change. Repaired images appear as orphan roots in the UI.
- `swap_adjacent` has an additional exposure: it calls `set_parent` twice with raw SQL mutations in between and no enclosing transaction. The sidecar-first rule must be applied to each `set_parent` sub-call atomically; the two-phase structure of `swap_adjacent` should be collapsed into a single transaction with all sidecar writes preceding the commit.
- Option considered and rejected: keep DB-first but add a post-commit sidecar-write retry queue. Rejected because it adds complexity without fixing the window, and contradicts the declared sidecar-as-truth invariant.
