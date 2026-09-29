---
name: pen-design
description: "Create UI mockups, wireframes, app screens, slides, posters, or marketing visuals with pen.dev. Use on 'design me a...', 'mockup of...', 'what would X look like?', or .pen files."
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

When refreshing from upstream, replace everything ABOVE the "Local Addendum" marker below except the frontmatter `description` (kept short for the fleet description budget), and keep the addendum intact.

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

# Local Addendum: Pen MCP and hard-won gotchas

Local knowledge, not part of the upstream skill. It applies when editing `.pen` files through the Pen MCP server (`mcp__pen__*`) or driving the CLI non-interactively. If a project-specific `pen-design` skill exists (brand rules, component inventories, baseline manifests), prefer it.

## Facts

- `.pen` on disk is plain JSON: `Read`, `jq`, and `git diff` work on it. The MCP server's own instructions may call it encrypted; the files are not.
- MCP tool names change across Pen releases. List the server's live tools and load its skill/guidelines tool (for example `read_skill`) early in the session; its schema reference is more current than this file.
- The MCP needs the desktop app's WebSocket bridge (`WebSocket not connected to app: desktop` when it is down; the trigger that starts it is undocumented). The headless CLI needs no GUI, writes `--out` straight to disk, and has no editor cache to drift, so it is the safer path for any `.pen` work that does not need the GUI. `--in` is optional (omit it for an empty canvas).

## Persistence gotchas (each one has lost work)

- **New files are memory-only.** Opening a non-existent path in the editor does not create it on disk, and the MCP has no save tool. Save with `osascript -e 'tell application "Pen" to activate' -e 'tell application "System Events" to keystroke "s" using command down'`, then confirm with `ls` before committing.
- **Auto-save misfires after `git checkout`** of a file Pen has open: edits land in memory and never persist (`git status` stays clean). Save the same way, then confirm the mtime advanced and git sees the change.
- **Stale editor cache overwrites disk.** Opening a file the desktop app already holds returns its in-memory tree, which may predate commits made via git, the CLI, or another machine; the next edit serializes that stale tree over disk. Before editing an existing file, compare the editor's frame and component counts and 2-3 recently changed sentinel nodes against the committed `.pen`; on mismatch, do not edit. After every edit batch, run `git diff <file>.pen | grep -E '^[+-]\s+"name"' | head -40`: only names from the section you meant to touch may appear. Otherwise `git checkout` the file, close and reopen it in Pen (there is no in-app reload), re-check, and replay.
- Delegate multi-batch `.pen` work to a subagent with `isolation: "worktree"` so a stale-cache flush stays in a throwaway tree.

## Schema pitfalls (rejected silently or cryptically)

- `note` does not accept `fill`; use a `frame` with a child `text`.
- `alignItems` accepts only `start`, `center`, `end` (no `baseline`); use explicit y-offsets.
- `fit_content` width on a node with no children rolls the batch back silently; add children first or set a width.
- When an edit "does nothing", suspect a silent schema rejection: reduce to the smallest failing op and screenshot.

## Building designs (MCP)

- Keep edit batches to about 25 operations; larger ones time out or partially apply. Build and commit section by section.
- Set `placeholder: true` on frames under construction; remove it when done.
- `fontFamily` takes literal font names; `$--variable` refs resolve for every other token (colors, spacing, borders) but not fonts.
- Copying a node changes its descendants' IDs, so do not address a just-copied node's descendants by the old path.
- Reusable components: `reusable: true` on the root frame, a `Category/Variant` name, and a `slot: [...]` array on customizable content frames.
- Script nodes store only a path to a `.js` file relative to the `.pen` and re-render on load: preserve `path` in edits, move scripts with the `.pen`, and pre-bake inputs (sandbox: no network or filesystem, 1000 nodes, 2s). "Convert to layers" snapshots the output into a regular frame.

## CLI gotchas

- The npm CLI installs `pen` plus a legacy `pencil` symlink; older desktop installs left a GUI shim at `~/.local/bin/pencil`. Check `which -a pen pencil` and invoke by absolute path when unsure.
- `PEN_CLI_KEY` is often in `.env.local`, which Bash subprocesses do not inherit: `set -a && source .env.local && set +a && pen status`.
- Dense diagrams take 3-5 minutes: run in the background and watch the event stream for progress and failure (`grep -E --line-buffered "saved|exported|complete|error|Error|failed|Failed|Step|step|Generating"`) so a crash does not look like silence.
- Diagram prompts work best as, per section: label or token, contents, persistence semantics (what writes where), and visual emphasis, then style cues. Never dictate coordinates.

## Committing

Pre-commit `end-of-file-fixer` rewrites a `.pen` on its first commit (no trailing newline): re-stage and commit again. Commit the `.png` export alongside the `.pen` so reviewers can see it in the diff.
