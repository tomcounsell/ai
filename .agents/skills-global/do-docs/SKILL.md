---
name: do-docs
description: "Update repository documentation to match a code change, PR, commit, or new behavior without duplicating sources of truth."
---

# Do Docs

Resolve the change and read full relevant implementation files, related data flow, and existing documentation. Compare intended behavior with actual behavior; document what shipped.
Build a focused list of docs affected by changed contracts, configuration, CLI entrypoints, architecture, or user flows. Search semantically related terms and stale names across the repo, not just filenames in the diff. Add missing documentation only where the feature needs it; preserve useful structure and links.
Make surgical edits and link authoritative details rather than copying them. Markdown is the default for docs unless another format was requested. Update indexes and examples when affected, and verify links and documented commands with appropriate checks.
Report the changes and any unresolved factual conflict. If issue discussions may be affected, identify them locally; do not automatically comment on issues without authorization. In an active managed lane, follow `docs/sdlc/do-docs.md` for its documentation gate and stage marker; otherwise completing the actual docs is sufficient.
