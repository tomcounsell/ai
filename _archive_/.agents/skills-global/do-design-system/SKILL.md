---
name: do-design-system
description: "Evolve a design-system charter, tokens, and reusable components while keeping design sources and downstream CSS consistent."
---

# Do Design System

Inspect the existing charter, file organization, design source, token exports, and consumers. Treat wireframes, flow diagrams, and design-system files as different schemas; never run a JSON transformation intended for one on another.
When a moodboard is part of the request, inspect its images through the available browser or supplied files, recording source URLs and the motifs that inform decisions. Read the charter and cite the principle behind each proposed change. Prefer a small coherent set of changes over an unrelated visual overhaul.
For .pen work follow $pen-design's inventory, stale-editor, single-writer, and saved-diff checks. Propagate tokens consistently into existing CSS and Tailwind exports; reuse declared generation/lint tools rather than maintaining duplicate values by hand. Verify rendered representative components, contrast, focus, responsive behavior, and exports.
Record the rationale and remaining gaps in the existing design audit artifact. Do the requested design work before asking for any truly necessary expansion of scope; ordinary authorized token/component edits do not need a separate approval ritual.
