# ADR 0014: Nested Folder Sidebar via `/`-Separated root_name

## Status
Accepted

## Context
The sidebar lists every `root_name` as a flat row. With many folders the list becomes unscrollable, and the hover-reveal of rename/delete actions causes row height to shift, making folder labels wrap.

Two problems required separate decisions:

**Nesting mechanism**: options were (a) a new `parent_folder` DB column with explicit drag-into-folder UI and schema migration, or (b) treating `/` in `root_name` as a path separator, deriving hierarchy purely at render time from the existing flat API response.

**Date prefix in filesystem path**: new saves currently land at `managed_root/YYYY-MM-DD/root_name/`. Removing the date folder level was requested to simplify paths. Options were (a) migrate all existing files, or (b) treat both layouts as valid indefinitely and leave existing files in place.

## Decision

**`/` as folder separator**: hierarchy is derived client-side by splitting `root_name` on `/`. No new DB column, no schema migration. Intermediate path segments (e.g. `portraits` in `portraits/session_a`) are rendered as collapsible groups in the sidebar — clicking a group expands/collapses it, it does not filter. Only leaf nodes filter the main view when clicked.

**Flat filesystem for new saves**: new images are written to `managed_root/root_name/filename.png` — no date prefix in the path. `created_at` (ISO 8601) in the sidecar remains the authoritative date; the main grid still groups by date from this field.

**Dual-layout compatibility**: `_create_missing_sidecars()` and `scan_and_import()` detect layout by inspecting whether the first path segment matches `YYYY-MM-DD` (regex `\d{4}-\d{2}-\d{2}`). If it does, the old layout is assumed: `root_name` is derived from all path segments between the date prefix and the filename, joined with `/`. Otherwise the new layout is assumed: `root_name` is all segments except the filename, joined with `/`. Existing images under the old layout are never moved.

**Hover wrapping fix**: sidebar action buttons switch from `display: none` to `visibility: hidden`, reserving their space at all times so folder label width never changes on hover. The folder label gains `white-space: nowrap; overflow: hidden; text-overflow: ellipsis`.

## Consequences
- Folder hierarchy is purely a naming convention. A folder named `a/b/c` is a leaf three levels deep; there is no object representing `a` or `a/b` in the DB.
- Renaming a group (e.g. `portraits` → `characters`) requires renaming every `root_name` that starts with `portraits/` — the API already cascades renames through all sidecar JSONs and DB records; the UI needs to detect group-level renames and issue one `PATCH` per affected leaf folder.
- Deleting a group deletes all images in all descendant leaf folders — the confirmation dialog must show the total count across all descendants.
- The managed folder on disk may contain a mix of `YYYY-MM-DD/root_name/` (old) and `root_name/` (new) directories. This is permanent — no migration is planned.
- The `%date:...%` token in `filename_prefix` still resolves into the `root_name` string itself (e.g. `portraits/%date:yyyy-MM-dd%` → `portraits/2026-06-09`), not into a separate folder level.
