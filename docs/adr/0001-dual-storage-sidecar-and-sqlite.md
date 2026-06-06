# ADR 0001: Dual Storage — Sidecar JSON + SQLite

## Status
Accepted

## Context
Managed images need persistent lineage metadata (UUID, parent UUID, timestamps). Two competing needs: portability (metadata travels with the image file) and queryability (fast tree lookups across thousands of images).

## Decision
Store lineage metadata in both a sidecar JSON file (beside each image) and a SQLite database (in the custom node folder). Both are written on every save. The sidecar is the source of truth; the SQLite DB is a derived index that can be fully rebuilt by scanning sidecar files.

## Consequences
- Images remain self-contained: copy a folder elsewhere and lineage is preserved via sidecars.
- Tree queries (all children of X, full chain to root) run against SQLite — no need to scan files.
- Every save has two write operations, but both are fast local disk writes with negligible overhead.
- A "rebuild index" operation is possible at any time if the DB is lost or corrupted.
