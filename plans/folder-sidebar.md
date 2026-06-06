# Plan: Folder Sidebar

> Source: grill-with-docs session — add a collapsible left sidebar with folder tree navigation

## Architectural decisions

- **New route**: `GET /image-manager/api/folders` — returns the folder tree derived from SQLite: array of `{date, count, root_names: [{name, count}]}`, sorted date descending
- **Filter params**: existing `GET /image-manager/api/roots` gains optional `?date=YYYY-MM-DD` and `?root_name=<name>` query params; either alone or both together narrow the result set
- **Layout**: `index.html` body becomes a flex-row — `#sidebar` (fixed ~220px wide, collapsible) on the left, `#content` (flex-grow) on the right; the existing header and drop-zone stay above the row
- **Hide-dates state**: persisted in `localStorage` under key `imageManager.hideDates`; defaults to `false`

---

## Phase 1: Sidebar + folder filtering

**Covers**: folder tree visible in the sidebar, clicking any node filters the main grid, collapse/expand toggle works end-to-end

### What to build

Add `GET /image-manager/api/folders` that queries SQLite for all root images, groups them by date and root_name, and returns counts. No filesystem walk needed — dates come from `created_at`, root names from `root_name`.

Restructure `index.html` layout into a sidebar + content flex-row. The sidebar renders the date→root_name tree with counts from `/folders`. Clicking a date node filters to that date; clicking a root_name node filters to that date+root_name. Active selection is highlighted. A "All Images" link at the top of the sidebar clears the filter.

When a folder is selected, `load()` passes the active filter as query params to `/roots`. If the filtered result is empty, render "No images in this folder" instead of the normal empty state.

A collapse toggle button (e.g. `‹` / `›`) at the top of the sidebar hides it and expands the content area to full width. Toggle state is not persisted — resets to open on page load.

### Acceptance criteria

- [ ] `GET /image-manager/api/folders` returns date-grouped tree with per-node counts
- [ ] Sidebar renders on the left; content grid takes remaining width
- [ ] Clicking a date node filters the grid to roots created on that date
- [ ] Clicking a root_name node filters the grid to roots with that date + root_name
- [ ] "All Images" link clears the filter and restores the full grid
- [ ] Selecting an empty folder shows "No images in this folder"
- [ ] Sidebar collapse toggle hides/shows the sidebar; grid expands to fill
- [ ] Existing features (lightbox, lineage, link mode, import drawer) are unaffected

---

## Phase 2: Hide dates toggle

**Covers**: single toggle that simultaneously flattens the sidebar tree to root names only and removes date group headers from the grid; state survives page reload

### What to build

Add a "Hide dates" toggle button in the sidebar header (or top bar). When active:
- Sidebar renders a flat list of root_names (counts summed across all dates) instead of the date→root_name tree; clicking a root_name filters by root_name only (no date param)
- Main grid renders images without date group headers — one flat grid or groups by root_name only

`GET /image-manager/api/roots` already supports `?root_name=` without `?date=`, so no backend change is needed for the flat filter.

Persist the toggle state in `localStorage` under `imageManager.hideDates` and restore it on page load.

### Acceptance criteria

- [ ] "Hide dates" toggle is visible and clearly labeled
- [ ] When on: sidebar shows flat root_name list with aggregate counts; date nodes are gone
- [ ] When on: grid renders without date group headers
- [ ] Clicking a root_name in flat mode filters correctly (root_name param only, no date)
- [ ] Toggle state survives page reload
- [ ] Switching the toggle while a folder is selected resets selection to "All Images" (avoids a date+name filter becoming stale when dates are hidden)
