---
name: mermaid-render
description: "Render Mermaid diagrams into verified SVG or PNG assets for documents, slides, or other exported artifacts."
---

# Mermaid Render

Use inline Mermaid when it fully serves the conversation. For a standalone asset, resolve an installed Mermaid renderer (`mmdc` or the repository's documented helper) and inspect its help. Do not assume a browser can display raw Mermaid source inside Markdown exports.
Write the diagram source to a task-specific file, choose output format, theme, background, and dimensions appropriate to the destination, then render. Preserve meaningful node labels and relationships; escape parser-sensitive characters and shorten labels if layout becomes unreadable.
Inspect the resulting image for missing labels, overlap, clipping, contrast, and direction. For slide embedding prefer SVG when supported or a sufficiently large PNG. Keep editable `.mmd` source with the requested deliverable. Use the available file/image preview and report the source and rendered paths; a zero renderer exit alone does not establish visual quality.
