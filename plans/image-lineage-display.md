# Plan: Image & Lineage Display

> Source: grill-with-docs session — chain tip on default grid, filmstrip view mode

## Architectural decisions

- **Chain tip query**: for each root, the chain tip is the image with `MAX(created_at)` sharing the same `root_uuid`; resolved server-side, not client-side
- **Modified route**: `GET /image-manager/api/roots` gains two new fields per result: `latest_uuid` (UUID of chain tip) and `latest_created_at`; existing fields unchanged — no breaking change
- **New route**: `GET /image-manager/api/chains` returns every lineage chain as an array of images ordered `created_at DESC` (newest first); each chain object: `{root_uuid, root_name, images: [{uuid, filename, created_at}, ...]}`
- **View mode state**: active view (`grid` | `filmstrip`) stored in `localStorage` under key `imageManager.viewMode`; defaults to `grid`
- **Thumbnail sizes**: chain tip ~144px, regular filmstrip thumbnails ~120px; controlled by CSS classes

---

## Phase 1: Chain tip on default grid

**Covers**: default grid cards show the chain tip thumbnail; clicking opens the lightbox for the chain tip image with its own workflow

### What to build

Extend the `/roots` query to find each chain's tip — the image with the highest `created_at` within the same `root_uuid` group. Return `latest_uuid` alongside existing root fields. When `latest_uuid` differs from `uuid` (i.e. the root has descendants), the card thumbnail src and lightbox target both use `latest_uuid`. The root name, descendant count, and all other card metadata continue to come from the root record. "Send to Comfy" in the lightbox loads the chain tip's workflow.

If the root has no descendants, `latest_uuid == uuid` and behavior is identical to today.

### Acceptance criteria

- [x] `GET /image-manager/api/roots` includes `latest_uuid` in every result
- [x] Card thumbnail displays the chain tip image (`/api/image/<latest_uuid>`)
- [x] Clicking a card opens the lightbox for `latest_uuid`, not the root
- [x] "Send to Comfy" in the lightbox loads the chain tip's workflow
- [x] "View Lineage" in the lightbox still opens the tree rooted at the root image (uses root uuid, not latest)
- [x] Roots with no descendants are unaffected (latest_uuid == uuid)

---

## Phase 2: Filmstrip view

**Covers**: toolbar toggle switches to filmstrip mode; each lineage chain renders as a newest-left horizontal scrollable row; chain tip is visually distinguished

### What to build

Add `GET /image-manager/api/chains` that returns all lineage chains with their full image arrays ordered newest-first. Each entry includes `root_uuid`, `root_name`, and an `images` array of `{uuid, filename, created_at}` objects.

Add a "Filmstrip" toolbar button alongside the existing controls. Toggling it switches the content area from the date-grouped card grid to the filmstrip layout and saves the choice to `localStorage`.

In filmstrip mode, each chain occupies one row. The row header shows `root_name · N images`. Images render as thumbnails in a horizontally scrollable strip, newest on the left. The chain tip (first/leftmost image) is rendered at ~144px with a visible border; all others at ~120px. Clicking any thumbnail opens the lightbox for that specific image. Date grouping (and the hide-dates toggle from the folder sidebar) applies to filmstrip rows the same as to the grid. The folder sidebar filter applies in filmstrip mode too.

Toggling back to grid view restores the default card layout.

### Acceptance criteria

- [ ] `GET /image-manager/api/chains` returns chains with full image arrays newest-first
- [ ] Toolbar "Filmstrip" button toggles the view; active state is visually indicated
- [ ] View mode persists across page reloads via `localStorage`
- [ ] Each chain renders as a labelled row (`root_name · N images`)
- [ ] Row scrolls horizontally when the chain has more images than fit the viewport
- [ ] Chain tip is ~144px with a border; other thumbnails are ~120px
- [ ] Clicking any thumbnail opens the correct image in the lightbox
- [ ] Date group headers appear/disappear based on the hide-dates toggle (Phase 2 of folder-sidebar plan)
- [ ] Folder sidebar filter narrows which chains appear
- [ ] Single-image chains render correctly (one chain tip thumbnail, no scroll)
