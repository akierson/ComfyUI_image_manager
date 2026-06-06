# Visual Clustering

## Goal
Add on-demand visual similarity clustering to the Image Manager web UI. The user toggles Cluster View, images are grouped by visual similarity, and they use the existing Link mode to assign parent-child relationships informed by the groups.

## Phase 1 — Embedding backend (`clustering.py`)

- [x] Create `clustering.py` in the package root
- [x] Implement `detect_backend()` → returns `"open_clip"`, `"transformers"`, or `"histogram"`
- [x] Implement `embed_images(abs_paths: list[str]) -> np.ndarray` using the detected backend
  - `open_clip`: load `ViT-B-32` pretrained `openai`, encode images in batches
  - `transformers`: load `openai/clip-vit-base-patch32`, encode images in batches
  - `histogram`: compute 64-bin RGB histogram per image, concatenate channels, L2-normalise
- [x] Implement `auto_cluster(embeddings: np.ndarray) -> list[int]`
  - Try K = 2…min(10, n//2), pick K with best silhouette score
  - Return cluster label per image (list aligned to input)
- [x] Unit tests in `tests/test_clustering.py` covering histogram backend (no optional deps required)

## Phase 2 — API endpoint (`api.py`)

- [x] Add `POST /image-manager/api/cluster` route
- [x] Fetch all `(uuid, abs_path)` from SQLite
- [x] Stream SSE progress events:
  - `{"status": "backend", "msg": "Using CLIP (open_clip)"}` (or histogram)
  - `{"status": "progress", "current": N, "total": M}` during embedding
  - `{"status": "done", "clusters": {"0": [uuid, …], "1": [uuid, …], …}}`
- [x] Return 200 with `Content-Type: text/event-stream`

## Phase 3 — UI toggle (`web/index.html`)

- [x] Add "Cluster" toggle button to the toolbar (beside Link / Rebuild)
- [x] On toggle-on: POST `/image-manager/api/cluster`, read SSE stream
  - Show progress bar replacing the grid while streaming `progress` events
  - Show backend note ("Using CLIP" / "Using color histogram") below the bar
  - On `done` event: render cluster-grouped grid (same `.card` markup, cluster headers instead of date headers)
- [x] On toggle-off: restore date-grouped view (re-call `load()`)
- [x] Button visual state: active style while cluster view is on (match existing `.active` pattern from Link button)
- [x] Cluster headers format: `Cluster 1 · N images`

## Acceptance criteria

- Toggling on replaces date groups with cluster groups; toggling off restores date view
- Progress bar and backend label appear while clustering runs
- Cluster View works correctly when no images exist (shows empty state, no crash)
- All three backends produce valid cluster assignments (histogram always works; CLIP paths covered if deps available)
- Existing Link mode, Send to Comfy, Lineage tree, and Import Folder are unaffected
- No changes to sidecar JSON schema or SQLite schema
