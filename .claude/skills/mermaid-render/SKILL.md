---
name: mermaid-render
description: "Render Mermaid .mmd diagrams as hand-drawn style PNG exports via Excalidraw. Use when converting .mmd files to presentation-quality images with a sketch/hand-drawn aesthetic. Triggered by 'render this diagram', 'export mermaid as PNG', 'convert mmd to image', or 'excalidraw export'. Also handles .excalidraw → PNG directly."
argument-hint: "<file.mmd|file.excalidraw> [file2 ...] [--out <dir>]"
allowed-tools: Bash
user-invocable: true
---

# mermaid-render

Convert Mermaid `.mmd` source files (or `.excalidraw` JSON files) to hand-drawn style PNGs.

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

### Step 1: Read the Mermaid source

```bash
cat <file.mmd>
```

### Step 2: Open excalidraw.com

```bash
agent-browser open https://excalidraw.com
agent-browser snapshot -i
```

Wait for the app to load — you should see toolbar buttons including "More tools".

### Step 3: Open the Mermaid import dialog

Click "More tools" (look for it in the snapshot by label), which expands a menu:

```bash
agent-browser click @<more-tools-ref>
agent-browser snapshot -i
# Look for menuitem "Mermaid to Excalidraw"
agent-browser click @<mermaid-ref>
agent-browser snapshot -i
# Dialog opens with a textbox and an "Insert" button
```

### Step 4: Paste the Mermaid content

Use `fill` to replace the textbox contents entirely (works even with multiline content):

```bash
agent-browser fill @<textbox-ref> "$(cat <file.mmd>)"
```

### Step 5: Insert

```bash
agent-browser click @<insert-button-ref>
agent-browser screenshot   # verify diagram rendered on canvas
```

### Step 6: Extract scene data from localStorage

Excalidraw auto-saves the scene to `localStorage`. Extract it directly — this is more reliable than triggering a file download through the headless browser:

```bash
agent-browser eval "localStorage.getItem('excalidraw')"
```

Save the output to disk. The eval result is a JSON-encoded string (double-encoded), so unwrap it:

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

Or as a one-liner:

```bash
agent-browser eval "localStorage.getItem('excalidraw')" > /tmp/scene_raw.txt
python3 -c "
import json
raw = open('/tmp/scene_raw.txt').read().strip()
elements = json.loads(json.loads(raw))
doc = {'type':'excalidraw','version':2,'source':'https://excalidraw.com','elements':elements,'appState':{'viewBackgroundColor':'#ffffff','gridSize':None},'files':{}}
open('/tmp/output.excalidraw','w').write(json.dumps(doc))
print(f'{len(elements)} elements saved')
"
```

### Step 7: Render to PNG

```bash
excalidraw-export /tmp/output.excalidraw -o <final-output.png> --scale 2
file <final-output.png>
```

### Step 8: Close the browser

**Always close when done.** This is not optional — leaving the browser open wastes resources and leaves state behind for the next task.

```bash
agent-browser close
```

---

## Export dialog (optional — use for quick screenshot verification)

To preview the diagram before extracting scene data, use `Cmd+Shift+E`:

```bash
agent-browser key "Meta+Shift+E"
agent-browser snapshot -i
# Look for "Export to PNG", "Export to SVG", "Only selected" checkbox
# Uncheck "Only selected" to export the full canvas
agent-browser click @<export-png-ref>
```

Note: the PNG download goes to the system Downloads folder, which is not accessible from a headless browser. Use this for visual verification only — the localStorage extraction (Step 6) is the correct path for saving the file.

---

## Batch rendering

For multiple `.mmd` files, process sequentially. Clear the canvas between diagrams:

```bash
agent-browser key "Meta+a"      # select all
agent-browser key "Backspace"   # delete
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

**`agent-browser` not found:** This is the primary browser tool. Check `which agent-browser`. Alternative: `playwright-cli` (if installed) uses the same command pattern but with `-s=<session-name>` flags.

**"More tools" not in snapshot:** Take a screenshot to see current state. The button may be labelled differently after Excalidraw UI updates. Look for a button that reveals Frame, Mermaid, and Laser pointer options.

**`fill` not replacing content:** Some versions of Excalidraw use a CodeMirror editor in the Mermaid dialog. If `fill` doesn't work, try clicking the textbox first, then `agent-browser key "Meta+a"` and `agent-browser type "<content>"`.

**Canvas blank after insert:** Mermaid rendering takes a moment. Take a screenshot and wait 2–3s. If still blank, validate the mermaid syntax first: `mmdc --input <file.mmd> --output /tmp/test.svg`.

**localStorage returns null:** The canvas hasn't been saved yet. Insert a diagram first. If elements were inserted but localStorage is still empty, try scrolling or clicking on the canvas to trigger an auto-save, then re-eval.

**`json.loads` fails on scene_raw.txt:** The `eval` output is double-JSON-encoded (a JSON string whose value is a JSON array). Always `json.loads(json.loads(raw))` — two unwrap steps.

**`excalidraw-export` outputs blank PNG:** Open the `.excalidraw` file and check that `elements` is non-empty. An empty elements array produces a blank image.

**`excalidraw-export` not found:** Install via `npm install -g @moona3k/excalidraw-export`. If the global npm bin isn't on PATH, use `npx @moona3k/excalidraw-export`.
