---
name: pen-design
description: "Create or edit .pen designs with available Pen tools or the pen.dev CLI, preserving design files and editor state."
---

# Pen Design

Use this for .pen artifacts or a specifically requested Pen workflow. Generic websites and decks should use their established code or artifact tooling; a visual request alone does not require pen.dev.
Inspect the installed CLI's version/help or available Pen MCP schema before use. Confirm authentication without printing credentials, and read the target file and editor state. A CLI generated preview is not proof the editable source was saved.
Before editing an existing design, inventory top-level nodes and IDs and save a scoped backup. Verify the editor's loaded path and top-level inventory match the on-disk file; stale editor caches can overwrite newer content. Keep one writer per design. After each batch, compare the saved JSON: node counts, IDs, deleted nodes, and changes outside the requested area. Restore only your own damaged changes from the backup if needed.
Prefer observed component instances and schema-supported properties. Preserve reusable component references, theme variables, and layout semantics. Verify rendered output and actual saved file existence. Use current documentation for cloud generation; do not reuse Claude-specific authentication recipes or assume a tool installed for Claude exists in Codex.
