# ADR 0004: Embedding Backend Falls Back to Color Histogram

## Status
Accepted

## Context
Visual clustering requires image embeddings. A CLIP vision encoder produces semantically rich embeddings (similar subjects and styles cluster together), but CLIP is not a ComfyUI dependency — `open_clip` and `transformers` may or may not be installed in the user's environment. A color histogram is always available (pure NumPy/PIL) but only clusters on color palette similarity, missing semantic content entirely.

## Decision
The embedding backend tries backends in priority order: `open_clip` → `transformers` → RGB color histogram. The first available backend wins. No error is raised if CLIP is unavailable; clustering proceeds with histograms silently.

## Consequences
- Clustering works out of the box for every user with no required installs.
- Cluster quality varies silently depending on what is installed. A user without `open_clip` or `transformers` gets histogram-based clusters and may not realize why results feel shallow.
- The backend in use should be surfaced in the UI (e.g. a small note in the progress stream: "Using CLIP" or "Using color histogram") so the user understands what they're getting.
- Embeddings are not persisted, so switching backends (e.g. after installing `open_clip`) costs nothing — the next clustering run simply uses the better backend.
