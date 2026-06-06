# ComfyUI Image Manager

Track every image you generate as a lineage chain — see where each image came from, refine it again from the same workflow, and visually cluster your output to find what's worth keeping.

## What it does

- **Lineage tracking** — every saved image gets a UUID and an optional parent link. If your workflow loads a managed image and then saves a new one, the save is automatically recorded as a child of the loaded image.
- **Web UI** — a browsable grid at `/image-manager` showing your images grouped by date and folder. Click any image to open its lineage tree, drag-and-drop to import external images, and use "Send to Comfy" to reload the original workflow onto the canvas with one click.
- **Filmstrip view** — see each lineage chain as a scrollable row, newest-first, so you can follow a refinement sequence at a glance.
- **Folder sidebar** — filter the grid by date or root name folder.
- **Visual clustering** — group images by visual similarity (k-means over CLIP embeddings) to spot patterns across a large output folder. Cluster quality improves automatically if `open_clip` or `transformers` is installed.
- **Lineage linking** — manually assign parent-child relationships between any two images via the web UI.
- **Folder import** — move an entire external folder of images into the managed folder in one operation.

## Installation

### Via ComfyUI Manager (recommended)

Search for **ComfyUI Image Manager** in the ComfyUI Manager node browser and click Install.

### Manual

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/akierson/ComfyUI_image_manager
pip install -r ComfyUI_image_manager/requirements.txt
```

Restart ComfyUI after installing.

### Optional: better cluster quality

Install either `open_clip_torch` or `transformers` to use CLIP embeddings instead of the default color histogram:

```bash
pip install open_clip_torch
# or
pip install transformers
```

Without either, clustering still works but groups by color similarity rather than semantic content.

## Nodes

### Save Image (Managed)

Replaces the built-in Save Image node. Saves to the managed folder (`managed_output/` by default), writes a sidecar JSON with lineage metadata, and automatically detects the parent image if a **Load Image (Managed)** node is present in the same workflow.

| Input | Type | Description |
|---|---|---|
| `images` | IMAGE | Batch of images to save |
| `filename_prefix` | STRING | Folder/filename prefix. Supports `%date:YYYY-MM-DD%` tokens. |

### Load Image (Managed)

Loads an image from the managed folder. Its presence in a workflow signals to the next Save that the output is a child of the loaded image.

| Input | Type | Description |
|---|---|---|
| `image` | dropdown | File path relative to the managed folder |

## Web UI

Open **Image Manager** from the ComfyUI toolbar (or navigate to `/image-manager`).

- **Grid** — images grouped by date. Click a card to open the lightbox and lineage tree.
- **Send to Comfy** — extracts the original workflow from the image's PNG metadata, wires up a Load Image (Managed) node pointing to the selected image, and loads it onto the canvas ready to run.
- **Filmstrip** — toggle from the toolbar to see each lineage chain as a scrollable row.
- **Cluster** — toggle from the toolbar to group images by visual similarity.
- **Link** — select two images to assign a manual parent-child relationship.
- **Import** — drag-and-drop images or paste a folder path to bulk-import external images.
- **Rebuild Index** — rescans the managed folder and rebuilds the SQLite database from sidecar files. Use if the database gets out of sync.

## Configuration

In the ComfyUI settings panel, set **ImageManager.managed_folder** to any absolute path to change where managed images are stored. Defaults to `ComfyUI/managed_output/`. Requires a server restart to take effect.

## How lineage is stored

Each image gets a sidecar `.json` file stored beside it. The sidecar is the source of truth — the SQLite index is derived from it and can be rebuilt at any time. This means you can copy a folder of managed images elsewhere and lineage is preserved.

## License

MIT
