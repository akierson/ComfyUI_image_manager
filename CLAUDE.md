# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This package is a ComfyUI custom node (`custom_nodes/ComfyUI_image_manager/`). The parent CLAUDE.md at `ComfyUI/CLAUDE.md` documents the ComfyUI node contract, input types, and key APIs — read it first.

---

## Status

All four phases are implemented. The plan at `../../../plans/image-manager.md` has all acceptance criteria checked.

---

## Running Tests

Tests run from the package directory (`custom_nodes/ComfyUI_image_manager/`), not the repo root. The test files import `lineage` and `api` as top-level modules (not relative imports), so `sys.path` must include the package dir — pytest handles this automatically when run from there.

```bash
# From custom_nodes/ComfyUI_image_manager/
# Use the ComfyUI venv to avoid system numpy binary incompatibility
/path/to/ComfyUI/comfyuienv/bin/python -m pytest tests/ -v
```

`test_api.py` requires `pytest-aiohttp`; `test_clustering.py` requires `scikit-learn`. Both are installed in the ComfyUI venv. System `python3` will fail for clustering tests due to a numpy/pandas ABI mismatch.

---

## Module Map

| File | Responsibility |
|---|---|
| `__init__.py` | Startup: reads config, registers managed folder with `folder_paths`, inits DB, runs startup scan, mounts API routes, exports node mappings |
| `nodes.py` | `ManagedSaveImage`, `ManagedLoadImage` — thin wrappers that delegate to `lineage.py` |
| `lineage.py` | All core business logic: `save_image`, `load_image`, `import_image`, `scan_and_import`, `init_db` |
| `api.py` | aiohttp route handlers for the web UI; `make_app()` returns a standalone `web.Application` |
| `web/index.html` | Single-page UI: root image grid, lineage tree panel, drag-and-drop import, Rebuild Index button |
| `web/js/settings.js` | ComfyUI frontend extension: registers the `ImageManager.managed_folder` setting, injects the menu button (handles both legacy and action-bar APIs), and listens for `postMessage` to call `app.loadGraphData()` |

---

## Architecture

### Data flow for saves

`ManagedSaveImage.save()` → iterates batch → `lineage.save_image()` per image:
1. `_detect_parent()` scans `prompt` JSON for exactly one `ManagedLoadImage` node, looks up its filename in SQLite
2. Assigns UUID v4, resolves date tokens in `filename_prefix` for root name
3. Writes PNG with `lineage_id` and `parent_id` text chunks
4. Writes sidecar JSON beside the PNG
5. Upserts into SQLite

### Startup scan

`scan_and_import()` is called at every startup. It **wipes the `images` table entirely** and repopulates from all sidecar JSON files it finds. This means SQLite is always a derived index; the sidecar is the source of truth. Consequence: any DB record without a matching sidecar is lost on restart.

### Send to Comfy

`GET /image-manager/api/workflow/<uuid>` reads the `workflow` PNG text chunk, replaces the first `LoadImage` node with `ManagedLoadImage`, and returns the modified workflow JSON. The frontend receives it and calls `app.loadGraphData()` via `postMessage` from the image manager page to the parent ComfyUI window.

### Route mounting

`api.py` builds a standalone `web.Application` via `make_app()`. `__init__.py` iterates its router and adds each route individually to `PromptServer.instance.app` — aiohttp subapp mounting is not used because it would add a path prefix.

### JS extension

`web/js/settings.js` is loaded by ComfyUI because `WEB_DIRECTORY = "./web"` is set in `__init__.py`. The extension uses `actionBarButtons` (supported since ComfyUI frontend 1.33.9+) with a `icon-[lucide--images]` icon. Icons must use the `icon-[lucide--<name>]` arbitrary-value format — the older `pi pi-*` PrimeVue format is not rendered by the action bar. The `postMessage` listener on `window` is what receives `loadWorkflow` messages from the image manager page and calls `app.loadGraphData()`.

**Import path depth gotcha**: `WEB_DIRECTORY = "./web"` means JS files are served at `/extensions/ComfyUI_image_manager/<subdir>/file.js` — one level deeper than custom nodes that set `WEB_DIRECTORY` to their JS subdirectory directly. The import to ComfyUI's `app` must be `../../../scripts/app.js` (3 levels up to `/`), not the `../../scripts/app.js` used by most example nodes.

