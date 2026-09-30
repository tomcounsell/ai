---
name: pen-design
description: >
  Create high-quality visual designs — websites, app screens, dashboards, slides, marketing materials, social media graphics — using the pen.dev CLI tool. Use this skill whenever the user wants to create, generate, or visualize any kind of UI design, mockup, wireframe, layout, webpage, app screen, presentation slide, poster, banner, or marketing asset. Also use it when the user says things like "design me a...", "make a visual for...", "create a mockup of...", "what would X look like?", or wants to turn an idea into a visual. Even if the user doesn't mention "pen.dev" or "design tool" explicitly — if they want something visual created, this is the skill to use. Also covers working with existing .pen files via the Pen MCP server (mcp__pen__*) — see the Local Addendum for MCP, save, and schema gotchas.
---

# pen.dev Design

Create professional visual designs from natural language descriptions using the pen.dev CLI. pen.dev is a headless design tool that generates `.pen` files (a structured JSON design format) and can export them as images.

## Setup

Before designing, make sure the pen.dev CLI is available.

### Check installation

```bash
which pen || npx pen version
```

If `pen` is not found, install it:

```bash
npm install -g @pen.dev/cli
```

If global install fails due to permissions, install locally instead:

```bash
npm install @pen.dev/cli
```

Then run it via `npx pen` (or `./node_modules/.bin/pen`) instead of `pen`.
You can learn about the available commands via the `pen --help` command.

### Authentication

#### pen.dev user

To use the CLI, an authenticated user logged in to pen.dev is required. First, check
the current user configuration on the machine with the `pen status` command.

If not logged in, there are the following options:

- use `pen signup --email you@example.com --username johndoe --name "John Doe"` command, to create a new user.
- use `pen login --email you@example.com [--code abc123]` to authenticate an existing or newly created user.
- optionally, the `PEN_CLI_KEY` env var can also be used for authentication if its set in your session.

#### Claude Code agent

The CLI needs auth to run its AI agent for which Claude Code is required. For that
there needs to be an authenticated Claude Code user set in the system configuration
either via env var or a user subscription.

If none of these are available, tell the user what options they have and help them set one up.

### Staying up to date

This skill stays in sync with the **pen.dev CLI npm package** (`@pen.dev/cli`). The published package includes `SKILL.md` at its root; the package version is the skill version.

**Check for a newer CLI / skill**

- Latest version on the registry: `npm view @pen.dev/cli version`
- Installed CLI: `pen version`, or `npm list -g @pen.dev/cli` (global) / `npm list @pen.dev/cli` (project)

**Upgrade the CLI**, then refresh your copied skill file (agents do not auto-update skill files you placed in config folders):

```bash
npm install -g @pen.dev/cli
```

**Where to copy the skill from after installing**

- From a dependency tree: `node_modules/@pen.dev/cli/SKILL.md` (path is the same for global and local installs; resolve from your project root or global `node_modules` prefix).

**Fetch the same file without cloning the repo** (mirrors the npm tarball; optional third-party CDNs):

- `https://unpkg.com/@pen.dev/cli@latest/SKILL.md`
- `https://cdn.jsdelivr.net/npm/@pen.dev/cli@latest/SKILL.md`

Use `@latest` for the newest publish, or pin (e.g. `@0.3.0`) for a reproducible snapshot.

**When to check for an update**

- **Early in the session**, before the first pen.dev design run (compare `npm view @pen.dev/cli version` to the installed CLI), so you aren't following stale instructions.
- **Again** if the user says they upgraded the CLI, or if behavior doesn't match this doc (flags, auth, timing).
- **Not** before every single command — once per session is enough unless something changed or errors suggest a version mismatch.

When refreshing from upstream, replace everything ABOVE the "Local Addendum" marker below and keep the addendum intact.

## Creating a Design

The core command:

```bash
pen --out <output.pen> --prompt "<design description>" --export <output.png> --export-scale 2
```

Key flags:
- `--out, -o` — where to save the `.pen` file (required)
- `--prompt, -p` — what to design (required)
- `--prompt-file, -f` — attach an image or text file to send with the prompt (repeatable). Same idea as attaching reference images in the pen.dev editor chat; not for loading the prompt text from a file.
- `--export, -e` — export an image of the result
- `--export-scale` — image resolution multiplier (use 2 for crisp output)
- `--export-type` — format: `png` (default), `jpeg`, `webp`, `pdf`
- `--in, -i` — start from an existing `.pen` file (for iteration)
- `--model, -m` — Claude model to use (defaults to Opus)

### Passing the Prompt

Pass the user's request directly as the prompt — do not expand, or add detail beyond what the user actually said. The pen.dev CLI has its own AI designer agent that handles creative decisions like layout structure, color palettes, typography, spacing, and content. Adding your own design specifics on top of the user's request will conflict with the CLI agent's own judgment and produce worse results.

If the user says "make me a landing page for a coffee shop", the prompt should be exactly that — not a paragraph with hero sections, color palettes, and font choices you invented.

### Timing Expectations

Design generation is not instant — the CLI runs an AI agent that plans the layout, creates each element, and validates the result visually. Expect:

- **Simple designs** (a card, a single component): 1-2 minutes
- **Medium designs** (an app screen, a landing page section): 2-3 minutes
- **Complex designs** (full landing page, detailed dashboard): 3-5+ minutes

Let the user know upfront that generation will take a few minutes so they're not left wondering. Use a generous timeout (at least 600000ms / 10 minutes) when running the command.

### Showing the Result

After the command completes, read the exported image to show it to the user:

```bash
# The command exports to the path you specified
pen --out design.pen --prompt "..." --export design.png --export-scale 2
```

Then use the Read tool on the exported PNG — it will render visually since you're a multimodal model.

Always show the image to the user after creating it. This is the whole point — they want to see the visual.

## Iterating on a Design

When the user wants changes to an existing design, use the `--in` flag to load the previous `.pen` file:

```bash
pen --in design.pen --out design-v2.pen --prompt "Make the header larger and change the accent color to green" --export design-v2.png --export-scale 2
```

The agent will read the existing design and apply modifications rather than starting from scratch.

For quick successive iterations, keep a consistent naming pattern:
- `design.pen` → `design-v2.pen` → `design-v3.pen`
- Or use a single file: `--in design.pen --out design.pen` (overwrites)

## Working Directory

Save design files in the user's current working directory or a subdirectory like `designs/`. Don't use temp directories — the user will want to find and iterate on these files later.

---

# Local Addendum: Pen MCP + hard-won gotchas

Everything below is local knowledge, not part of the upstream skill. It applies when working with `.pen` files via the Pen MCP server (`mcp__pen__*`) or driving the CLI in non-interactive sessions. If a project-specific `pen-design` skill exists (brand rules, component inventories, design-system paths), prefer it.

## Reading .pen files

- `.pen` files are encrypted JSON. **Never** use `Read`, `Grep`, or `cat` on them — you get binary garbage and waste a tool turn.
- ALWAYS use `mcp__pen__batch_get` to inspect node structure.
- Use `mcp__pen__get_screenshot` to visually verify the result.
- `mcp__pen__get_editor_state` lists open documents, available reusable components, and selection.

## Saving new .pen files (the gotcha that always bites)

`mcp__pen__open_document` on a non-existent path opens the file **in the desktop editor's memory only** — it does NOT create the file on disk. Existing files auto-save on edits, but **new files never touch disk until you Cmd+S**. The MCP has no save tool.

Workaround — trigger Cmd+S via AppleScript after the first edit batch:

```bash
osascript -e 'tell application "Pen" to activate'
osascript -e 'tell application "System Events" to keystroke "s" using command down'
```

After saving, verify with `ls` before committing — otherwise pre-commit fails with `no such file`. This bites worst when you `open_document` a fresh path, run a batch of `batch_design` ops, and assume disk reflects the editor state. It doesn't.

Auto-save also misfires after `git checkout` on a file Pen has open. Pen's auto-save trigger compares against its in-memory snapshot, not disk. If you `git checkout` an open file (e.g. to recover from a corrupted batch), Pen's snapshot still matches its tree — subsequent `batch_design` ops modify memory but never persist. Symptom: `git status` shows "working tree clean" even after multiple successful `batch_design` calls. Recovery: trigger Cmd+S via the same `osascript` call as for new files before the next read or before commit. After saving, verify with `ls -la` that mtime advanced and `git status` shows the file as modified.

## Stale editor cache (the silent corruption gotcha)

`mcp__pen__open_document` on a file that's already loaded in the desktop app **returns Pen's in-memory tree, not what's on disk**. If the desktop app holds a stale snapshot from a prior session — predating commits made via git, the CLI, or another machine — the next `batch_design` call serializes that stale tree (plus your edits) and overwrites disk. No tool surfaces the drift: there's no version stamp, no mtime check, no reload command. The first sign of trouble is a `git diff` showing deletions you never asked for.

This is independent of the new-file save gotcha and the `git checkout` auto-save misfire above. It bites **existing** files that auto-save fine — the auto-save just commits the wrong base.

### Pre-flight inventory check (mandatory before any batch_design on an existing file)

1. `mcp__pen__get_editor_state` — note top-level frame count and reusable component count.
2. `mcp__pen__batch_get` on 2–3 sentinel nodes whose properties recently changed (the most recent commit's named-node deltas are good candidates).
3. Compare against a baseline (last committed `.pen`, a checked-in manifest, or a known-good screenshot). If the in-memory state doesn't match the baseline — abort, reload, do not flush.

Pick sentinels that change in normal work — typography roots, recently-added components, recently-renamed nodes. A sentinel that never changes won't catch a stale cache.

### Post-batch diff verification

After every `batch_design` (or batch group), run from the repo root:

```bash
git diff path/to/design.pen | grep -E "^[+-]\s+\"name\"" | head -40
```

The output should ONLY show names from the section you intended to touch. Any other named-node deltas — especially deletions — mean the editor flushed a stale tree. Stop, revert (`git checkout path/to/design.pen`), reload the file in Pen, re-run with a pre-flight check.

### Recovery runbook

Symptom: `git diff` after a batch shows extra deletions or property reversions in nodes you didn't touch.

1. `git checkout path/to/design.pen` — discard the corrupted flush.
2. In the Pen desktop app: File → Close, then reopen the file from disk. (Pen has no in-app reload; close+reopen is the only way to drop the stale tree.)
3. Run the pre-flight inventory check above to confirm the reload worked.
4. Replay your batches.

Note: this recovery path interacts with the `git checkout` auto-save misfire above — after `git checkout`, the close+reopen step is what guarantees Pen drops the stale tree. Skip it and you're back in misfire territory.

### Subagent isolation

When delegating multi-batch `.pen` work to a subagent, prefer `isolation: "worktree"`. Cache-regression corruption stays in the throwaway worktree and never touches your working tree. Worth the overhead for any session involving more than a handful of `batch_design` calls.

### Project-specific hardening

Project-specific `pen-design` skills should codify, on top of the above:

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

The Pen MCP requires the desktop app's WebSocket bridge to be up. When it isn't (or in non-interactive sessions), use the headless `pen` CLI (usage above in the upstream section). The CLI runs the same AI agent against the same `.pen` schema — no GUI required.

Key distinction:

- **MCP** (`mcp__pen__open_document`) — can create new files in editor memory, but won't persist them until you Cmd+S (see "Saving new .pen files" above). Also vulnerable to the stale editor cache (see "Stale editor cache" above).
- **Headless CLI** (`pen --in ... --out ...`) — `--in` is **optional**. Omit it to start from an empty canvas; provide it to iterate on an existing file. `--out` is required (unless you only `--export`) and writes/overwrites disk directly — no Cmd+S dance, no editor cache to drift from. The CLI is the safer path for any `.pen` work that doesn't need the GUI.

### Setup gotchas

- **Binary name collisions.** The npm CLI installs `pen` (plus a legacy `pencil` symlink to the same `@pen.dev/cli` entrypoint), and older desktop-app installs left a GUI shim at `~/.local/bin/pencil`. When in doubt, invoke by absolute path and check with `which -a pen pencil` or `npm ls -g @pen.dev/cli --parseable`.
- **Auth via `PEN_CLI_KEY`** lives in `.env.local` (or wherever the project keeps it). Bash subprocesses don't inherit it unless you source first:
  ```bash
  set -a && source .env.local && set +a && /path/to/pen status
  ```
- **Desktop WebSocket bridge does not auto-expose.** `pen interactive -a desktop` and the MCP tools both fail with `WebSocket not connected to app: desktop` until the desktop app starts its local listener (the trigger is undocumented). Headless mode is the reliable path; reload the file in the desktop app afterward to view.

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

Run `mcp__pen__get_guidelines` early in a session — it returns the live schema reference and is more current than this skill.

## Pre-commit hygiene

- Pre-commit's `end-of-file-fixer` modifies `.pen` files on first commit (they lack a trailing newline). The hook fails, fixes the file, and you re-stage + re-commit. Expected; not an error.
- Commit `.pen` and `.png` exports together — the `.pen` is the editable source; the `.png` lets reviewers see the diagram in the PR diff without launching Pen.
