# ADR 0011: Lineage Tree Uses Top-Down Layout

## Status
Accepted

## Context
The lineage tree SVG can be laid out top-down (root at top, children below, generations as rows) or left-to-right (root on left, children rightward, generations as columns). Left-to-right is the conventional direction for version-control DAGs and genealogy charts and maps well to "time flows right."

## Decision
Use top-down layout. Lineage chains in this system are typically short in depth but wide in branching — a single parent commonly has several siblings per generation. Top-down places siblings side-by-side horizontally where screen space is plentiful, and uses vertical space (which is shorter) only for depth. Left-to-right would force the common case (many siblings, few generations) to scroll horizontally while wasting vertical space.

## Consequences
- The horizontal axis encodes siblings; the vertical axis encodes generation depth. Depth is communicated by row position, making a per-node generation label redundant.
- Trees with high sibling counts will be wider than tall. Pan/zoom handles overflow; the SVG clips to the modal bounds.
- Left-to-right would become preferable only if chains regularly exceeded ~8 generations with ≤2 siblings per level — the opposite of the current usage pattern.
