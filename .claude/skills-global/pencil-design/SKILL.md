---
name: pencil-design
description: "Work with .pen files via the Pencil MCP or headless CLI. Triggered by '.pen file', 'pencil mcp', 'mcp__pencil__*', 'pencil cli', 'design system pen'. NOT ordinary frontend/CSS work."
---

# Pencil Design (general-purpose)

Hard-won notes for working with `.pen` files via the Pencil MCP server (`mcp__pencil__*`) or the headless `pencil` npm CLI. Project-specific skills (e.g. cuttlefish's `pencil-design`) override this one inside their own repo and add brand rules, component inventories, and design-system paths on top — this skill is the floor that applies everywhere.

If a project-specific `pencil-design` skill exists, prefer it. This skill is intentionally generic. Ordinary frontend / CSS design work is NOT this skill's territory; that belongs in `frontend-design` or `do-design-system`.

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

Auto-save also misfires after `git checkout` on a file Pencil has open. Pencil's auto-save trigger compares against its in-memory snapshot, not disk. If you `git checkout` an open file (e.g. to recover from a corrupted batch), Pencil's snapshot still matches its tree — subsequent `batch_design` ops modify memory but never persist. Symptom: `git status` shows "working tree clean" even after multiple successful `batch_design` calls. Recovery: trigger Cmd+S via the same `osascript` call as for new files before the next read or before commit. After saving, verify with `ls -la` that mtime advanced and `git status` shows the file as modified.

## Stale editor cache (the silent corruption gotcha)

`mcp__pencil__open_document` on a file that's already loaded in the desktop app **returns Pencil's in-memory tree, not what's on disk**. If the desktop app holds a stale snapshot from a prior session — predating commits made via git, the CLI, or another machine — the next `batch_design` call serializes that stale tree (plus your edits) and overwrites disk. No tool surfaces the drift: there's no version stamp, no mtime check, no reload command. The first sign of trouble is a `git diff` showing deletions you never asked for.

This is independent of the new-file save gotcha and the `git checkout` auto-save misfire above. It bites **existing** files that auto-save fine — the auto-save just commits the wrong base.

### Pre-flight inventory check (mandatory before any batch_design on an existing file)

1. `mcp__pencil__get_editor_state` — note top-level frame count and reusable component count.
2. `mcp__pencil__batch_get` on 2–3 sentinel nodes whose properties recently changed (the most recent commit's named-node deltas are good candidates).
3. Compare against a baseline (last committed `.pen`, a checked-in manifest, or a known-good screenshot). If the in-memory state doesn't match the baseline — abort, reload, do not flush.

Pick sentinels that change in normal work — typography roots, recently-added components, recently-renamed nodes. A sentinel that never changes won't catch a stale cache.

### Post-batch diff verification

After every `batch_design` (or batch group), run from the repo root:

```bash
git diff path/to/design.pen | grep -E "^[+-]\s+\"name\"" | head -40
```

The output should ONLY show names from the section you intended to touch. Any other named-node deltas — especially deletions — mean the editor flushed a stale tree. Stop, revert (`git checkout path/to/design.pen`), reload the file in Pencil, re-run with a pre-flight check.

### Recovery runbook

Symptom: `git diff` after a batch shows extra deletions or property reversions in nodes you didn't touch.

1. `git checkout path/to/design.pen` — discard the corrupted flush.
2. In Pencil desktop: File → Close, then reopen the file from disk. (Pencil has no in-app reload; close+reopen is the only way to drop the stale tree.)
3. Run the pre-flight inventory check above to confirm the reload worked.
4. Replay your batches.

Note: this recovery path interacts with the `git checkout` auto-save misfire above — after `git checkout`, the close+reopen step is what guarantees Pencil drops the stale tree. Skip it and you're back in misfire territory.

### Subagent isolation

When delegating multi-batch `.pen` work to a subagent, prefer `isolation: "worktree"`. Cache-regression corruption stays in the throwaway worktree and never touches your working tree. Worth the overhead for any session involving more than a handful of `batch_design` calls.

### Project-specific hardening

Project-specific `pencil-design` skills should codify, on top of the above:

- A **baseline manifest** (e.g. `docs/designs/baselines/design-system.baseline.json`) listing top-level frame IDs, component counts, and sentinel node properties — updated whenever the `.pen` is committed. The pre-flight reads this and asserts.
- A **danger-zone list** of node IDs/names that should NEVER appear in a diff unless explicitly being edited. The post-batch grep checks for these specifically.

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

### Script nodes (code on canvas)

Script nodes execute a `.js` file and render its output as nested layers. They store only a **relative path** to the script (relative to the `.pen` location), not the code itself, and they re-render every load — output is derived state, not persisted.

- `batch_design` ops on a Script node must preserve the `path` attribute; rewriting it points the node at a different (or missing) script.
- Moving the `.pen` requires moving the referenced `.js` files alongside it; broken paths render as empty layers.
- Scripts run in a sandbox: no network, no filesystem, ≤1000 nodes, ≤2s execution. Don't try to do data-fetching from a script — pre-bake inputs.
- "Convert to layers" snapshots the current output into a regular frame and removes the Script node. Useful when you want diff-able children instead of derived output.

## Headless CLI vs MCP

The Pencil MCP requires the desktop app's WebSocket bridge to be up. When it isn't (or in non-interactive sessions), use the headless npm CLI. The CLI runs the same AI agent against the same `.pen` schema — no GUI required.

Key distinction:

- **MCP** (`mcp__pencil__open_document`) — can create new files in editor memory, but won't persist them until you Cmd+S (see "Saving new .pen files" above). Also vulnerable to the stale editor cache (see "Stale editor cache" above).
- **Headless CLI** (`pencil --in ... --out ...`) — `--in` is **optional**. Omit it to start from an empty canvas; provide it to iterate on an existing file. `--out` is required (unless you only `--export`) and writes/overwrites disk directly — no Cmd+S dance, no editor cache to drift from. The CLI is the safer path for any `.pen` work that doesn't need the GUI.

### Setup gotchas

- **Two binaries named `pencil`.** The desktop app installs a shim at `~/.local/bin/pencil` that just `exec`s the GUI binary. The npm CLI lives wherever `npm` put it (commonly `/opt/homebrew/bin/pencil` on Apple Silicon, `~/.nvm/versions/node/<v>/bin/pencil` for nvm installs). PATH order usually puts the GUI shim first. **Always invoke the npm CLI by absolute path.** Find it with `npm ls -g @pencil.dev/cli --parseable` or `which -a pencil`.
- **Auth via `PENCIL_CLI_KEY`** lives in `.env.local` (or wherever the project keeps it). Bash subprocesses don't inherit it unless you source first:
  ```bash
  set -a && source .env.local && set +a && /path/to/pencil status
  ```
- **Desktop WebSocket bridge does not auto-expose.** `pencil interactive -a desktop` and the MCP tools both fail with `WebSocket not connected to app: desktop` until the desktop app starts its local listener (the trigger is undocumented). Headless mode is the reliable path; reload the file in the desktop app afterward to view.

### Working pattern: in-place iteration

```bash
set -a && source .env.local && set +a && /path/to/pencil \
  --in design.pen \
  --out design.pen \
  --prompt "..." \
  --export design.png \
  --export-scale 2
```

Same path for `--in` and `--out` overwrites in place. Subsequent runs read prior state and modify, so prompts can refer to existing structure ("add a sixth tile to the grid").

### Working pattern: create from scratch

Omit `--in` to start with an empty canvas — no need to seed via the MCP, no need for the desktop app at all:

```bash
set -a && source .env.local && set +a && /path/to/pencil \
  --out design.pen \
  --prompt "..."
```

Cleanest path for greenfield `.pen` files in CI / non-interactive sessions. Avoids both the MCP Cmd+S save dance AND the stale editor cache class of bug entirely.

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
