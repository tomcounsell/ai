---
name: mermaid-render
description: "Render .mmd (and .excalidraw) files as hand-drawn PNGs via Excalidraw. Triggered by 'render this diagram', 'export mermaid as PNG', 'convert mmd to image', or 'excalidraw export'."
argument-hint: "<file.mmd|file.excalidraw> [file2 ...] [--out <dir>]"
allowed-tools: Bash, mcp__byob__browser_navigate, mcp__byob__browser_read, mcp__byob__browser_click, mcp__byob__browser_type, mcp__byob__browser_press_key, mcp__byob__browser_eval, mcp__byob__browser_screenshot, mcp__byob__browser_close_tab
user-invocable: true
disable-model-invocation: true
---

# mermaid-render

Convert Mermaid `.mmd` source files (or `.excalidraw` JSON files) to hand-drawn style PNGs.

## Browser surface

The `.mmd → PNG` flow drives `excalidraw.com` via BYOB MCP
(`mcp__byob__browser_*`) — the user's real Chrome. The
`.excalidraw → PNG` flow does not touch a browser at all.

The `.mmd` flow uses `mcp__byob__browser_eval` to extract scene JSON
from `localStorage`. BYOB blocks `browser_eval` by default; set
`BYOB_ALLOW_EVAL=1` before invoking BYOB if you need this flow. The
gate is documented in
[`docs/features/byob-browser-control.md`](../../../docs/features/byob-browser-control.md).

## Dependencies (install once)

```bash
npm install -g @moona3k/excalidraw-export
```

This handles `.excalidraw` → PNG headlessly (pure Node, no browser, ~100ms/file, authentic roughjs hand-drawn aesthetic).

## Input types

| Input | Pipeline |
|-------|----------|
| `.excalidraw` | Direct: `excalidraw-export` → PNG (fast, no browser) |
| `.mmd` | Browser: excalidraw.com import → extract scene from localStorage → `excalidraw-export` → PNG |

---

## Workflow A: `.excalidraw` → PNG (no browser needed)

```bash
excalidraw-export <file.excalidraw> -o <output.png> --scale 2
file <output.png>   # verify: should show "PNG image data"
```

---

## Workflow B: `.mmd` → PNG

### Step 1: Read and validate the Mermaid source

```bash
cat <file.mmd>
```

**Before proceeding, check for diagram topologies that produce bad renders:**

| Pattern | Problem | Fix |
|---------|---------|-----|
| Cross-subgraph edges (nodes in subgraph A connect to nodes in subgraph B) | Spaghetti lines, overlapping edges | Flatten into a single graph, or use a simpler topology |
| More than 7 nodes | Cluttered, unreadable at slide scale | Split into multiple diagrams or abstract into groups |
| Bidirectional edges between 3+ nodes | Edge crossings multiply | Simplify to one-directional flow or use a table instead |
| Subgraphs with connections to a shared "overlap" group | Venn-like layouts don't work in flowcharts | Replace with a side-by-side table or a simple list diagram |
| `flowchart TB` with wide subgraphs | Horizontal overflow, cramped layout | Switch to `flowchart LR` or reduce subgraph width |

**If any of these patterns are present, fix the `.mmd` source FIRST:**
- Restructure the diagram to avoid the problematic topology
- Consider whether a **table slide** would communicate the idea better than a diagram
- For comparison/overlap diagrams: use two columns with shared items highlighted, not a flowchart

### Step 2: Open excalidraw.com

```text
mcp__byob__browser_navigate(url="https://excalidraw.com", waitUntil="networkidle")
mcp__byob__browser_read(url="https://excalidraw.com", reuseTab=true, screens=1)
```

Wait for the app to load — you should see toolbar buttons including "More tools".

### Step 3: Open the Mermaid import dialog

Click "More tools" (look for it in the IE list by `name: "More tools"`), which expands a menu:

```text
mcp__byob__browser_click(tabId=<tab>, selector="byob:idx=<more_tools_idx>")
mcp__byob__browser_read(url="https://excalidraw.com", reuseTab=true, screens=1)
# Look for menuitem name: "Mermaid to Excalidraw"
mcp__byob__browser_click(tabId=<tab>, selector="byob:idx=<mermaid_idx>")
mcp__byob__browser_read(url="https://excalidraw.com", reuseTab=true, screens=1)
# Dialog opens with a textbox and an "Insert" button
```

### Step 4: Paste the Mermaid content

Type into the dialog textbox (clear=true to replace existing content):

```text
mcp__byob__browser_type(tabId=<tab>, selector="byob:idx=<textbox_idx>", text="<contents of file.mmd>", clear=true)
```

### Step 5: Insert

```text
mcp__byob__browser_click(tabId=<tab>, selector="byob:idx=<insert_button_idx>")
mcp__byob__browser_screenshot(tabId=<tab>, savePath="/tmp/excalidraw-canvas.png")  # verify diagram rendered
```

### Step 6: Extract scene data from localStorage

Excalidraw auto-saves the scene to `localStorage`. Extract it directly — this is more reliable than triggering a file download. Requires `BYOB_ALLOW_EVAL=1` in the agent's environment:

```text
mcp__byob__browser_eval(tabId=<tab>, expression="localStorage.getItem('excalidraw')")
```

Save the returned string to disk. The eval result is a JSON-encoded string (double-encoded), so unwrap it:

```python
import json

raw = open('/tmp/scene_raw.txt').read().strip()
elements = json.loads(json.loads(raw))   # unwrap the double encoding

excalidraw_doc = {
    "type": "excalidraw",
    "version": 2,
    "source": "https://excalidraw.com",
    "elements": elements,
    "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
    "files": {}
}

with open('/tmp/output.excalidraw', 'w') as f:
    json.dump(excalidraw_doc, f)
```

### Step 7: Render to PNG

```bash
excalidraw-export /tmp/output.excalidraw -o <final-output.png> --scale 2
file <final-output.png>
```

### Step 8: Validate the rendered output

**Read the PNG and check for visual quality problems.** This is not optional — a PNG that exists but looks broken is worse than no PNG.

Use the Read tool on the output PNG (Claude is multimodal and can evaluate images). Check for:

- [ ] **Spaghetti lines**: edges crossing in a tangled mess → Go back to Step 1, restructure the `.mmd`
- [ ] **Overlapping labels**: text on top of edges or other text → Simplify node labels or reduce connections
- [ ] **Cramped layout**: nodes too close together, text truncated → Reduce node count or switch orientation (TB↔LR)
- [ ] **Blank or nearly blank output**: rendering failed silently → Check the `.excalidraw` elements array
- [ ] **Unreadable at slide scale**: diagram too detailed for a presentation slide → Split into multiple diagrams

**If any check fails, do NOT proceed.** Fix the source `.mmd` and re-render. A bad diagram undermines the entire slide.

### Step 9: Close the Excalidraw tab

When done, close the tab so the user's Chrome isn't left with an extra tab open:

```text
mcp__byob__browser_close_tab(tabId=<tab>)
```

---

## Export dialog (optional — use for quick screenshot verification)

To preview the diagram before extracting scene data, use `Cmd+Shift+E`:

```text
mcp__byob__browser_press_key(tabId=<tab>, key="Meta+Shift+E")
mcp__byob__browser_read(url="https://excalidraw.com", reuseTab=true, screens=1)
# Look for "Export to PNG", "Export to SVG", "Only selected" checkbox
# Uncheck "Only selected" to export the full canvas
mcp__byob__browser_click(tabId=<tab>, selector="byob:idx=<export_png_idx>")
```

Note: the PNG download goes to the user's Downloads folder. Use this for visual verification only — the localStorage extraction (Step 6) is the correct path for saving the file.

---

## Batch rendering

For multiple `.mmd` files, process sequentially. Clear the canvas between diagrams:

```text
mcp__byob__browser_press_key(tabId=<tab>, key="Meta+a")          # select all
mcp__byob__browser_press_key(tabId=<tab>, key="Backspace")       # delete
```

Then repeat Steps 3–7 for the next file. The localStorage key `excalidraw` always holds the current canvas state.

---

## Output naming convention

| Input | Output |
|-------|--------|
| `docs/design/auth-flow.mmd` | `docs/design/auth-flow.png` |
| `auth-flow.mmd` (with `--out png/`) | `png/auth-flow.png` |
| `auth-flow.excalidraw` | `auth-flow.png` (alongside) |

---

## Troubleshooting

**`mcp__byob__browser_eval` returns "browser_eval is disabled":** BYOB blocks `browser_eval` by default. Set `BYOB_ALLOW_EVAL=1` in the agent's environment and restart the BYOB MCP server (`cd ~/.byob && bun run doctor`).

**BYOB transport error:** Run `cd ~/.byob && bun run doctor` to confirm the extension is loaded and the native bridge is running. If red, re-run the machine's BYOB install/opt-in flow (in repos with a setup skill, that flow handles it; otherwise reinstall under `~/.byob` and re-run `bun run doctor`).

**"More tools" not in IE list:** Take a screenshot to see current state. The button may be labelled differently after Excalidraw UI updates. Look for a button that reveals Frame, Mermaid, and Laser pointer options.

**`browser_type` not replacing content:** Some versions of Excalidraw use a CodeMirror editor in the Mermaid dialog. If `clear=true` doesn't work, click the textbox first, then `mcp__byob__browser_press_key(key="Meta+a")` followed by `mcp__byob__browser_type` with the new content.

**Canvas blank after insert:** Mermaid rendering takes a moment. Screenshot and wait 2–3s. If still blank, validate the mermaid syntax first: `mmdc --input <file.mmd> --output /tmp/test.svg`.

**localStorage returns null:** The canvas hasn't been saved yet. Insert a diagram first. If elements were inserted but localStorage is still empty, click on the canvas to trigger an auto-save, then re-eval.

**`json.loads` fails on scene_raw.txt:** The `eval` output is double-JSON-encoded (a JSON string whose value is a JSON array). Always `json.loads(json.loads(raw))` — two unwrap steps.

**`excalidraw-export` outputs blank PNG:** Open the `.excalidraw` file and check that `elements` is non-empty. An empty elements array produces a blank image.

**`excalidraw-export` not found:** Install via `npm install -g @moona3k/excalidraw-export`. If the global npm bin isn't on PATH, use `npx @moona3k/excalidraw-export`.
