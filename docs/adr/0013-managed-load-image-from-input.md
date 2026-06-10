# ADR 0013: ManagedLoadImageFromInput — Auto-Import of Input/ Base Images

## Status
Accepted

## Context
Users doing img2img workflows often have base images in ComfyUI's `input/` folder. The existing `ManagedLoadImage` node only reads from the managed folder, so any `input/` image used as an img2img seed is invisible to `_detect_parent()` — the saved output is treated as a root image with no lineage link to its actual source.

The alternatives considered:

- **Extend `_detect_parent()` to also scan plain `LoadImage` nodes**: rejected because it fires silently on every `LoadImage` in the workflow (ControlNet references, etc.), not just the img2img base, and gives the user no signal that their file is being imported and moved.
- **A single `ManagedLoadImage` node with a `source` toggle**: rejected because `INPUT_TYPES` is evaluated at server startup — swapping the file list based on a toggle value requires custom frontend JS; two nodes avoids that complexity entirely.
- **Copy vs. move**: `input/` is ComfyUI's general-purpose drop zone; other nodes and workflows may reference the same file. Moving silently breaks those references. Copy is chosen so the managed system owns its file without side-effects.

## Decision
Add a new `ManagedLoadImageFromInput` node. Its dropdown is populated from `folder_paths.get_filename_list("input")`. On execution, `load()` calls `import_from_input()`, which:

1. Checks the DB for an existing record with `original_filename` matching the selected filename — returns it unchanged if found (dedup by original filename).
2. Otherwise copies the file into the managed folder, writes a sidecar JSON with an `original_filename` field, and upserts the DB.

`_detect_parent()` is extended to also detect `ManagedLoadImageFromInput` nodes and look up their parent record by `original_filename` rather than `filename`.

A new `original_filename TEXT` column is added to the `images` table (null for all other image types). `scan_and_import()` reads this field from sidecars on rebuild so dedup survives a DB wipe.

## Consequences
- `input/` images used as img2img bases are tracked as proper Managed Images — they appear as root images in the lineage tree, and all img2img outputs hang off them as children.
- The node name `ManagedLoadImageFromInput` is permanent; workflows that use it cannot be migrated if the name changes.
- Using the same `input/` file in multiple workflows produces one managed record, not N duplicates.
- The original file in `input/` is preserved; nothing in the existing workflow breaks.
- `_detect_parent()` now does two different lookups depending on the node class type — this is the trade-off for keeping the two nodes clean rather than merging them.
