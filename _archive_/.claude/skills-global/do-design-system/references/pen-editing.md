# Applying edits to design-system.pen (Step 5 reference)

Load this before writing to `design-system.pen` — safety gate, MCP persistence
gotcha, direct-JSON editing pattern, conventions, and troubleshooting.

## Safety-gate update

Before any `.pen` write, paste and execute the inline Python assertion below. It pins the write
target to `design-system.pen` and refuses any other path. **Scope: the `.pen` source only.**

A repo may *also* register a tool-level PreToolUse hook that blocks direct Write/Edit against the
generated artifacts (`design-system.md`, `brand.css`, `source.css`) regardless of whether this
skill is active — the context file declares it if present. The two guards are complementary: the
inline assertion discriminates the correct `.pen` write path from a wrong `.pen` write path; the
hook discriminates a write to a generated artifact from a write to anything else. Where both
exist, do NOT remove either.

```python
from pathlib import Path

target = Path("docs/designs/design-system.pen")
assert target.name == "design-system.pen", \
    "do-design-system only edits design-system.pen — refuse"
assert target.exists(), f"design-system.pen not found at {target}"
```

If the Pen MCP is connected, also verify the open editor is the
system file:

```python
# Pseudocode — via mcp__pen__get_editor_state
state = mcp__pen__get_editor_state()
assert state["activeFile"].endswith("design-system.pen"), \
    "active Pen file is not design-system.pen — switch before editing"
```

Never run `batch_design`, `set_variables`, or direct JSON writes
against any other `.pen` file. Product-team wireframes, flows, and
mockups have different schemas and would be corrupted.

## Critical gotcha — MCP does not persist

The Pen MCP `batch_design` and `set_variables` tools operate on an
**in-memory editor session**. They do NOT persist to disk unless the
Pen desktop app has the file open and triggers a save. If you run
the MCP operations, see "Successfully executed," then close the MCP
session, the edits are **silently discarded**.

Symptoms:

- `get_editor_state` shows your new components after batch_design
  returned success.
- Reopening the document later shows the pre-edit state.
- Reading the `.pen` JSON on disk shows no changes.

## Reliable path: edit the JSON directly

`.pen` is plain JSON (indent=2). Edit it in Python:

```python
import json
from pathlib import Path

p = Path("docs/designs/design-system.pen")
doc = json.loads(p.read_text())

# 1. Variables
doc.setdefault("variables", {})
doc["variables"]["--font-serif"] = {"type": "string", "value": "Lora"}
doc["variables"]["--status-operational"] = {"type": "color", "value": "#5C7A3E"}

# 2. New component — append to the right parent frame's children
components_frame = next(c for c in doc["children"] if c["id"] == "JFbpV")
components_frame["children"].append({
    "type": "frame",
    "id": "wiM0R",  # any 5-char unique string
    "name": "Annotation/Crosshair",
    "reusable": True,
    "width": 16, "height": 16, "layout": "none",
    "children": [
        {"type": "rectangle", "id": "h", "fill": "$--accent",
         "width": 16, "height": 1.5, "x": 0, "y": 7.25},
        {"type": "rectangle", "id": "v", "fill": "$--accent",
         "width": 1.5, "height": 16, "x": 7.25, "y": 0},
    ],
})

p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
```

## Conventions

- Preserve `indent=2` and trailing newline.
- IDs are arbitrary unique strings — 5 mixed-case chars is typical.
- Colors, fonts, spacing: always reference the variable with `$--name`,
  never hardcode a hex or font family.
- Reusable components: set `"reusable": True`, top-level in their
  parent frame's children list. `name` must follow the charter's
  `Category/Variant` taxonomy.
- **Never edit `product/*.pen` from this skill.** Different schema,
  different owner.

After the write, verify:

```python
doc2 = json.loads(p.read_text())
# count reusable components, check specific IDs exist, check variable values
```

You can then re-open in Pen (`mcp__pen__open_document`) — the
editor will reload the on-disk state.

## Gotchas reference

| Symptom | Cause | Fix |
|---|---|---|
| WebFetch returns "no images found" on Cosmos | JS-rendered SPA | Use BYOB MCP (`mcp__byob__browser_*`) |
| `mcp__pen__batch_design` reports success but file unchanged | MCP edits don't persist without Pen UI save | Edit `.pen` JSON directly with Python |
| `get_screenshot` returns blank for newly-added Pen nodes | Render cache | Not a real problem — verify via `batch_get` or `Read` the JSON |
| New `@theme` token doesn't work in templates | Tailwind name doesn't match brand file | Ensure both files use the same token name |
| `$--font-mono` "invalid" warning | False positive — variable refs in `fontFamily` do resolve | Ignore |
| Skill tries to edit a product wireframe | Scope violation | Safety gate — only `design-system.pen` is editable |
| Charter missing, skill won't proceed | By design | Scaffold `charter-template.md` and fill it before moodboard pass |
| Edit proposed with no principle citation | Skipped Step 3 grounding | Reject; require the citation |
