# Applying edits to design-system.pen (Step 5 reference)

## Safety gate

Run this before any `.pen` write. It pins the target to `design-system.pen`; product `.pen` files have different schemas and would be corrupted. Its scope is the `.pen` source only. A repo may also register a hook blocking direct writes to the generated artifacts (`design-system.md`, `brand.css`, `source.css`); the context file declares it. The two guard different mistakes, so where both exist keep both.

```python
from pathlib import Path

target = Path("docs/designs/design-system.pen")
assert target.name == "design-system.pen", "do-design-system only edits design-system.pen — refuse"
assert target.exists(), f"design-system.pen not found at {target}"
```

## Edit the JSON directly

Pen MCP edits apply to an in-memory editor session and are silently discarded unless the Pen desktop app has the file open and saves it: the tool reports success, and the file on disk is unchanged. `.pen` on disk is plain JSON (indent=2), so edit it with Python:

```python
import json
from pathlib import Path

p = Path("docs/designs/design-system.pen")
doc = json.loads(p.read_text())

doc.setdefault("variables", {})
doc["variables"]["--font-serif"] = {"type": "string", "value": "Lora"}

components_frame = next(c for c in doc["children"] if c["id"] == "JFbpV")
components_frame["children"].append({
    "type": "frame", "id": "wiM0R", "name": "Annotation/Crosshair", "reusable": True,
    "width": 16, "height": 16, "layout": "none",
    "children": [
        {"type": "rectangle", "id": "h", "fill": "$--accent", "width": 16, "height": 1.5, "x": 0, "y": 7.25},
        {"type": "rectangle", "id": "v", "fill": "$--accent", "width": 1.5, "height": 16, "x": 7.25, "y": 0},
    ],
})

p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
```

Conventions: keep `indent=2` and the trailing newline; IDs are any unique string (5 mixed-case chars is typical); reference colors, fonts, and spacing as `$--name`, never a literal hex or family; reusable components set `"reusable": true`, sit at the top level of their parent frame's children, and are named `Category/Variant` per the charter. Re-read the file after writing and confirm the new variables and IDs are present.

## Gotchas

| Symptom | Cause | Fix |
|---|---|---|
| Pen MCP reports success, file unchanged | MCP edits don't persist without a desktop-app save | Edit the JSON directly |
| New `@theme` token doesn't work in templates | Tailwind token name differs from the brand file | Use the same name in both |
| `$--font-mono` "invalid" warning | False positive: variable refs in `fontFamily` resolve | Ignore |
