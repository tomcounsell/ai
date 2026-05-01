---
name: pencil-design
description: "Use when designing or iterating on .pen files via the Pencil MCP server (or the headless `pencil` npm CLI). Captures cross-project gotchas — save semantics for new files, schema pitfalls that aren't in the schema text, and the MCP-vs-CLI file-existence distinction. Triggered by '.pen file', 'pencil mcp', 'mcp__pencil__*', 'pencil cli', 'design system pen'. NOT triggered by ordinary frontend / CSS design work — that belongs in `frontend-design` or `do-design-system`."
---

# Pencil Design (general-purpose)

Hard-won notes for working with `.pen` files via the Pencil MCP server (`mcp__pencil__*`) or the headless `pencil` npm CLI. Project-specific skills (e.g. cuttlefish's `pencil-design`) override this one inside their own repo and add brand rules, component inventories, and design-system paths on top — this skill is the floor that applies everywhere.

If a project-specific `pencil-design` skill exists, prefer it. This skill is intentionally generic.

## Reading .pen files

- `.pen` files are encrypted JSON. **Never** use `Read`, `Grep`, or `cat` on them — you get binary garbage and waste a tool turn.
- ALWAYS use `mcp__pencil__batch_get` to inspect node structure.
- Use `mcp__pencil__get_screenshot` to visually verify the result.
- `mcp__pencil__get_editor_state` lists open documents, available reusable components, and selection.

## Saving new .pen files (the gotcha that always bites)

`mcp__pencil__open_document` on a non-existent path opens the file **in the desktop editor's memory only** — it does NOT create the file on disk. Existing files auto-save on edits, but **new files never touch disk until you Cmd+S**. The MCP has no save tool.

Workaround — trigger Cmd+S via AppleScript after the first edit batch:

```bash
osascript -e 'tell application "Pencil" to activate'
osascript -e 'tell application "System Events" to keystroke "s" using command down'
```

After saving, verify with `ls` before committing — otherwise pre-commit fails with `no such file`. This bites worst when you `open_document` a fresh path, run a batch of `batch_design` ops, and assume disk reflects the editor state. It doesn't.

## Common schema pitfalls

The schema text doesn't surface these — each one rolled back batches silently or with cryptic errors. Memorize them:

- **`note` does NOT accept `fill`.** Note extends `Entity`, `Size`, `TextStyle` but NOT `CanHaveGraphics`. If you need a filled background behind text, use a `frame` with a child `text` node.
- **`alignItems` accepts only `start` / `center` / `end`.** `baseline` errors out — there is no baseline alignment mode for masthead-style layouts. Compose with explicit y-offsets instead.
- **`fit_content` width with no children → zero-size warning, batch rolls back silently.** Either give the node children before sizing, or set an explicit width. The silent rollback is the worst part — you'll see no error and no diff in `batch_get` until you screenshot.

When a `batch_design` call seems to "do nothing," the schema almost always rejected it silently. Re-check the operation against these rules first; then reduce to the smallest failing op and screenshot to confirm.

## Building designs (MCP)

- Keep `batch_design` calls to **max 25 operations** per call. Larger batches time out or partially apply.
- Split large designs into logical sections (header, content, footer) and commit each before the next.
- Set `placeholder: true` on frames you're actively building so the agent doesn't try to autolayout incomplete content. Remove it when the section is done.
- Use literal font names (e.g. `"Inter"`, `"IBM Plex Mono"`) for `fontFamily` — variable refs (`$--font-body`) don't resolve here.
- Use `$--variable` references for colors, spacing, padding, border, and other tokens — they DO resolve everywhere except `fontFamily`.

### Component instances

```
card = I(parent, {type: "ref", ref: "G9h8r"})    # Insert a ref to a reusable component
U(card+"/title", {content: "New Title"})         # Update a descendant
R(card+"/slot", {type: "text", ...})             # Replace a descendant entirely
```

**Do NOT update descendants of a just-Copied node** — IDs change on copy, and the `parent+"/path"` selector points at the old tree.

### Creating reusable components

- Set `reusable: true` on the root frame.
- Name with a category prefix: `Card/Episode`, `Button/Ghost`, `Input/Search`. Slash-prefixed names group in the editor's component picker.
- Add a `slot: [...]` array on content frames to mark them as customizable from instances.

## Headless CLI vs MCP

The Pencil MCP requires the desktop app's WebSocket bridge to be up. When it isn't (or in non-interactive sessions), use the headless npm CLI. The CLI runs the same AI agent against the same `.pen` schema — no GUI required.

Key distinction:

- **MCP** (`mcp__pencil__open_document`) — can create new files in editor memory, but won't persist them until you Cmd+S (see "Saving new .pen files" above).
- **Headless CLI** (`pencil --in ... --out ...`) — requires the input file to **already exist on disk**. Cannot create from scratch. Seed it via the MCP (and save) first, or create an empty file in the desktop app.

### Setup gotchas

- **Two binaries named `pencil`.** The desktop app installs a shim at `~/.local/bin/pencil` that just `exec`s the GUI binary. The npm CLI lives wherever `npm` put it (commonly `/opt/homebrew/bin/pencil` on Apple Silicon, `~/.nvm/versions/node/<v>/bin/pencil` for nvm installs). PATH order usually puts the GUI shim first. **Always invoke the npm CLI by absolute path.** Find it with `npm ls -g @pencil.dev/cli --parseable` or `which -a pencil`.
- **Auth via `PENCIL_CLI_KEY`** lives in `.env.local` (or wherever the project keeps it). Bash subprocesses don't inherit it unless you source first:
  ```bash
  set -a && source .env.local && set +a && /path/to/pencil status
  ```
- **Desktop WebSocket bridge does not auto-expose.** `pencil interactive -a desktop` and the MCP tools both fail with `WebSocket not connected to app: desktop` until the desktop app starts its local listener (the trigger is undocumented). Headless mode is the reliable path; reload the file in the desktop app afterward to view.

### Working pattern: in-place iteration

```bash
# File MUST exist on disk first (CLI cannot create it)
set -a && source .env.local && set +a && /path/to/pencil \
  --in design.pen \
  --out design.pen \
  --prompt "..." \
  --export design.png \
  --export-scale 2
```

Same path for `--in` and `--out` overwrites in place. Subsequent runs read prior state and modify, so prompts can refer to existing structure ("add a sixth tile to the grid").

### Long runs

Generation typically takes 3–5 minutes for a dense diagram. Run in the background and monitor the output stream — the agent emits structured `text` / `thinking` / `operations` events. Filter for both progress AND failure signatures so a crash doesn't show up as silence:

```
grep -E --line-buffered "saved|exported|complete|error|Error|failed|Failed|Step|step|Generating"
```

Use a generous timeout (≥10 minutes) when invoking through the Bash tool.

### Prompt structure that produces clean diagrams

For each section, give the agent four things — and stop there:

1. **Label / token** — the monospace identifier (e.g. `STEP_03_OF_05`, `ENTRY`)
2. **Contents** — what's in this section, bullet-listed
3. **Persistence semantics** — what writes to which model/store (the agent translates this into badges)
4. **Visual emphasis** — `← COMMIT POINT (red accent)`, `dashed branch`, etc.

End the prompt with style cues (typography, palette, accent color, square corners). **Do not dictate coordinates.** The agent decides layout. Short structured input outperforms paragraphs of design direction.

## Tool reference (MCP)

`get_editor_state`, `open_document`, `get_guidelines`, `batch_get`, `batch_design`, `snapshot_layout`, `get_screenshot`, `get_variables`, `set_variables`, `find_empty_space_on_canvas`, `search_all_unique_properties`, `replace_all_matching_properties`, `export_nodes`.

Run `mcp__pencil__get_guidelines` early in a session — it returns the live schema reference and is more current than this skill.

## Pre-commit hygiene

- Pre-commit's `end-of-file-fixer` modifies `.pen` files on first commit (they lack a trailing newline). The hook fails, fixes the file, and you re-stage + re-commit. Expected; not an error.
- Commit `.pen` and `.png` exports together — the `.pen` is the editable source; the `.png` lets reviewers see the diagram in the PR diff without launching Pencil.

## Distribution

This skill lives at `~/src/ai/.claude/skills/pencil-design/SKILL.md` and is hardlinked to `~/.claude/skills/pencil-design/` by `scripts/update/hardlinks.py` on every `/update` run. Edits in either location update both (shared inode). Project-specific overrides (e.g. cuttlefish's brand rules) live in that project's own `.claude/skills/pencil-design/` and take precedence inside that repo.
