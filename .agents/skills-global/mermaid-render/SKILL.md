---
name: mermaid-render
description: Render Mermaid or Excalidraw files to verified PNG or SVG assets, including hand-drawn diagrams and batch exports.
---

# Mermaid and Excalidraw rendering

Use inline Mermaid when it serves the conversation. For exported assets, preserve
the input format and requested visual style. Resolve actual CLI/browser capabilities;
do not assume Claude/BYOB tools or DOM evaluation are available.

## Excalidraw input and hand-drawn exports

For an existing `.excalidraw` file, inspect its JSON and confirm it has scene elements.
Use the installed `excalidraw-export` CLI after checking `--help`. Its PNG workflow is:

```sh
excalidraw-export input.excalidraw -o output.png --scale 2
```

If absent, check the documented `@moona3k/excalidraw-export` package or use the
available Excalidraw UI export. Do not silently substitute a conventional Mermaid
render when the user wants the hand-drawn style.

For Mermaid-to-hand-drawn output, use Excalidraw's Mermaid import through the
available browser surface. Start a fresh scene so unrelated canvas content is
preserved. Read the `.mmd`, import its source, inspect the inserted scene, and export
PNG/SVG or save the editable `.excalidraw` scene through the observed UI. A supported
scene-export API is also suitable. Do not depend on undocumented localStorage access
or assume browser tools permit reading it. If no export path works, retain the
source and report the missing capability instead of claiming a finished asset.

## Conventional Mermaid output

For ordinary Mermaid SVG/PNG, resolve an installed renderer such as `mmdc`, inspect
its help, and render the `.mmd` at suitable dimensions, theme, and background. Keep
editable source alongside the requested result. If the renderer is unavailable,
use an available supported rendering surface or report the dependency.

## Batch and visual verification

Accept multiple `.mmd` and `.excalidraw` inputs, writing each output alongside its
source or to the requested output directory with the same stem. Detect colliding
output names before writing. For browser batches isolate each scene; clear only
the temporary scene created for this task, never the user's existing drawing.

Open each rendered image and check labels, edge crossings, clipping, contrast,
blank output, and readability at its intended size. Simplify cluttered diagrams
without changing their meaning. A successful command is not visual verification.
Return absolute links to the source and verified exports; close only tabs created
for the task when they are no longer needed.
