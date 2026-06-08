# ADR 0009: Generation Badge and Chain Color Always in Tandem

## Status
Accepted

## Context
Two visual cues identify an image's place in the lineage system: the **Generation Badge** (depth in chain) and the **Chain Color** (which chain it belongs to). Either alone is misleading: a badge without a color gives depth but no family context; a color without a badge gives family but no depth. As views are added, there is a risk of one being implemented without the other.

## Decision
Any view that renders individual image cards must show both a generation badge and a chain color, or neither. They are always shown together. This applies view-agnostically — flat view, cluster view, and any future view that renders per-image cards.

Orphans (chains of exactly one image) are the single exception: they receive neither a generation badge nor a chain color, rendered with a neutral border instead. This is consistent across all views.

## Consequences
- Flat view must be updated to add chain color borders to non-orphan cards (currently it shows generation badges but no chain color).
- Any future view that introduces generation badges must simultaneously add chain color, and vice versa.
- The Cluster View's orphan treatment (neutral color, no badge) is the canonical reference for all views.
