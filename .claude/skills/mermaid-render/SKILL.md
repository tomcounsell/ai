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

Playwright is required for the `.mmd` → `.excalidraw` step:
```bash
npm install -g playwright-cli   # if not already installed
```

## Input types

| Input | Pipeline |
|-------|----------|
| `.excalidraw` | Direct: `excalidraw-export` → PNG (fast, no browser) |
| `.mmd` | Browser: excalidraw.com import → save `.excalidraw` → `excalidraw-export` → PNG |

---

## Workflow A: `.excalidraw` → PNG (no browser needed)

```bash
excalidraw-export <file.excalidraw> --output <output.png>
```

If `--output` is not supported, check the CLI help:
```bash
excalidraw-export --help
```

Common invocation patterns:
```bash
excalidraw-export input.excalidraw                    # outputs input.png alongside
excalidraw-export input.excalidraw -o output.png
excalidraw-export input.excalidraw --format png --out ./renders/
```

Verify the output is a valid PNG:
```bash
file <output.png>   # should show "PNG image data"
```

---

## Workflow B: `.mmd` → `.excalidraw` → PNG

### Step 1: Read the Mermaid source

```bash
cat <file.mmd>
```

### Step 2: Open excalidraw.com and import Mermaid

Use headed mode — excalidraw.com renders on canvas and needs a real browser environment:

```bash
PLAYWRIGHT_MCP_VIEWPORT_SIZE=1440x900 playwright-cli -s=mermaid-render open https://excalidraw.com --persistent --headed
playwright-cli -s=mermaid-render screenshot
```

Wait 2 seconds for the app to fully load, then take a snapshot:

```bash
playwright-cli -s=mermaid-render snapshot
```

#### Find and use the Mermaid import option

Excalidraw's UI has a "+" insert button or a toolbar icon that opens an "Insert" menu containing "Mermaid to Excalidraw". The exact UI may vary — use the snapshot to locate elements by visible text.

```bash
# Look for insert/plus button in snapshot, click it
playwright-cli -s=mermaid-render click <ref>
playwright-cli -s=mermaid-render snapshot
# Look for "Mermaid" or "Mermaid to Excalidraw" option
playwright-cli -s=mermaid-render click <ref>
playwright-cli -s=mermaid-render screenshot
```

Once the Mermaid dialog is open, clear any placeholder text and paste the mermaid content:

```bash
playwright-cli -s=mermaid-render snapshot
# Find the code editor / textarea
playwright-cli -s=mermaid-render triple-click <textarea-ref>
playwright-cli -s=mermaid-render type "<full mermaid content>"
playwright-cli -s=mermaid-render screenshot
```

Click Insert/Convert:

```bash
playwright-cli -s=mermaid-render snapshot
playwright-cli -s=mermaid-render click <insert-button-ref>
playwright-cli -s=mermaid-render screenshot
```

### Step 3: Save as `.excalidraw` file (more reliable than PNG export)

Select all, then use the main menu → "Save to disk" (saves `.excalidraw` JSON to `~/Downloads/`):

```bash
playwright-cli -s=mermaid-render press "ctrl+a"
playwright-cli -s=mermaid-render snapshot
# Open main menu (≡ hamburger)
playwright-cli -s=mermaid-render click <menu-ref>
playwright-cli -s=mermaid-render snapshot
# Click "Save to disk" or "Save as..."
playwright-cli -s=mermaid-render click <save-ref>
```

Move the downloaded file to the working directory:

```bash
# Find the most recently downloaded .excalidraw file
ls -t ~/Downloads/*.excalidraw | head -1
mv "$(ls -t ~/Downloads/*.excalidraw | head -1)" <target-path.excalidraw>
```

### Step 4: Close browser

```bash
playwright-cli -s=mermaid-render close
```

### Step 5: Render `.excalidraw` → PNG

```bash
excalidraw-export <target-path.excalidraw> -o <output.png>
file <output.png>
```

---

## Batch rendering

For multiple `.mmd` files, process sequentially. Between diagrams, clear the canvas:

```bash
playwright-cli -s=mermaid-render press "ctrl+a"
playwright-cli -s=mermaid-render press "Backspace"
```

Then repeat Step 2 (Mermaid import) for the next file.

For `.excalidraw` files, `excalidraw-export` can typically accept multiple files or a glob:

```bash
excalidraw-export docs/design/journeys/*.excalidraw --out docs/design/journeys/png/
```

---

## Output naming convention

| Input | Output |
|-------|--------|
| `docs/design/auth-flow.mmd` | `docs/design/auth-flow.png` |
| `auth-flow.mmd` (with `--out png/`) | `png/auth-flow.png` |
| `auth-flow.excalidraw` | `auth-flow.png` (alongside) |

---

## Troubleshooting

**`excalidraw-export` not found:** Install via `npm install -g @moona3k/excalidraw-export`. If global npm path isn't on PATH, use `npx @moona3k/excalidraw-export`.

**Canvas blank after import:** Mermaid rendering takes a moment. Take a screenshot and wait 2–3s before selecting all. If still blank, the mermaid syntax may have errors — validate first with `mmdc --input file.mmd --output /tmp/test.svg`.

**Dialog not accepting typed content:** Try `fill <ref> "<content>"` instead of `type`. If mermaid content has quotes, escape them or write the content to a temp file and paste via clipboard.

**Download not appearing in `~/Downloads/`:** Check if the browser is configured with a different download directory. Try `ls -t ~/Downloads/ | head -5` right after clicking Save.

**`excalidraw-export` outputs blank PNG:** The `.excalidraw` file may be empty or malformed. Open it in a text editor and verify it contains `elements` with actual shapes (not just an empty array).

**Multiple diagrams overlapping:** After each import, always `Ctrl+A` then `Backspace` to clear before importing the next one.
