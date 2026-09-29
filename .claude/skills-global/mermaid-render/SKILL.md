---
name: mermaid-render
description: "Render .mmd (and .excalidraw) files as hand-drawn PNGs via Excalidraw. Triggered by 'render this diagram', 'export mermaid as PNG', 'convert mmd to image', or 'excalidraw export'."
argument-hint: "<file.mmd|file.excalidraw> [file2 ...] [--out <dir>]"
allowed-tools: Bash, Read, mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_click, mcp__byob__browser_type, mcp__byob__browser_press_key, mcp__byob__browser_eval, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab
user-invocable: true
disable-model-invocation: true
---

# mermaid-render

Convert Mermaid `.mmd` files (or `.excalidraw` JSON) to hand-drawn PNGs that read well at slide scale.

**Done:** each input has a PNG next to it (or in `--out <dir>`, same basename), you have opened every PNG with Read and it passes the checks below, and the Excalidraw tab is closed.

## Facts

- Renderer: `npm install -g @moona3k/excalidraw-export` (or `npx @moona3k/excalidraw-export`). `excalidraw-export <in.excalidraw> -o <out.png> --scale 2` is headless, pure Node, and fast; an empty `elements` array yields a blank PNG.
- `.excalidraw` input needs no browser: render it directly.
- `.mmd` input goes through excalidraw.com in the user's real Chrome via BYOB MCP (`mcp__byob__browser_*`). Extracting the scene uses `browser_eval`, which BYOB blocks unless `BYOB_ALLOW_EVAL=1` is set and the BYOB MCP server restarted (see [`docs/features/byob-browser-control.md`](../../../docs/features/byob-browser-control.md)). On a BYOB transport error run `cd ~/.byob && bun run doctor`; if red, re-run the machine's BYOB install/opt-in flow (in repos with a setup skill, that flow handles it; otherwise reinstall under `~/.byob`).

## Fix bad topologies before rendering

The Mermaid-to-Excalidraw converter renders these badly; restructure the `.mmd` first, or use a table when it communicates better:

| Pattern | Problem | Fix |
|---------|---------|-----|
| Edges between nodes in different subgraphs | Spaghetti, overlapping edges | Flatten into one graph or simplify |
| More than 7 nodes | Unreadable at slide scale | Split, or abstract into groups |
| Bidirectional edges among 3+ nodes | Crossings multiply | One-directional flow, or a table |
| Subgraphs linked to a shared "overlap" group | Venn layouts fail in flowcharts | Side-by-side table or list |
| `flowchart TB` with wide subgraphs | Horizontal overflow | `flowchart LR`, or narrower subgraphs |

## `.mmd` to PNG

1. Open `https://excalidraw.com`. Toolbar "More tools" opens a menu with "Mermaid to Excalidraw" (labels drift across Excalidraw releases; look for the menu that also holds Frame and Laser pointer).
2. Type the `.mmd` contents into the dialog's textbox with `clear=true`, then click "Insert". If `clear=true` does not replace the text (the dialog may be a CodeMirror editor), click the box, press `Meta+a`, then type. A blank canvas right after insert usually needs 2-3s; if it stays blank, validate the syntax with `mmdc --input <file.mmd> --output /tmp/test.svg`.
3. Extract the scene: `browser_eval(expression="localStorage.getItem('excalidraw')")`. If it returns null, click the canvas to trigger the auto-save and re-run. The result is double-JSON-encoded; wrap it and render:

```python
import json
elements = json.loads(json.loads(open('/tmp/scene_raw.txt').read().strip()))
json.dump({"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
           "elements": elements,
           "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}},
          open('/tmp/output.excalidraw', 'w'))
```

then `excalidraw-export /tmp/output.excalidraw -o <final.png> --scale 2`.

4. For several files, clear the canvas between them (`Meta+a`, `Backspace`) and repeat; the `excalidraw` localStorage key always holds the current canvas.
5. Close the tab when done.

If `browser_eval` is unavailable, the export dialog (`Meta+Shift+E`, uncheck "Only selected", "Export to PNG") saves a PNG to the user's Downloads folder instead.

## Validate before handing off

Open each PNG with Read. Fail it on: tangled or crossing edges, labels overlapping edges or text, cramped nodes or truncated text, blank or near-blank output, or detail too fine for a slide. On any failure, fix the `.mmd` (fewer nodes, simpler topology, TB and LR swapped) and re-render; never hand off a PNG that fails, since a broken diagram undermines the slide it sits on.
