# ADR 0003: Send to Comfy Loads Canvas, Does Not Execute

## Status
Accepted

## Context
The "Send to Comfy" action reconstructs a workflow from an image's embedded PNG metadata and hands it back to ComfyUI. It could either execute the workflow immediately (POST /prompt) or load it into the canvas for editing.

## Decision
Send to Comfy loads the reconstructed workflow into the ComfyUI canvas via the frontend JS `app.loadGraphData()` API. It does not execute. The user reviews and adjusts the workflow before running it.

## Consequences
- Users can adjust denoise strength, swap LoRAs, or tweak prompts before committing a generation — the primary use case for iterative refinement.
- Requires a custom JavaScript extension (registered via ComfyUI's web extension system) to call `app.loadGraphData()` from the web UI panel.
- Adds one extra click (the Queue Prompt button) compared to execute-immediately, which is an acceptable cost given the refinement workflow.
