# Design-system tooling (`tools.design_system_sync`)

The `do-design-system` skill used to drive CSS and gap-audit changes by
hand: the agent retyped tokens from `.pen` into `brand.css` and
`source.css`, and hand-composed a changelog table every pass. This
feature replaces that with a deterministic one-way pipeline.

`.pen` (authored in Pencil) is the only human-editable file. A Python
generator (`tools/design_system_sync.py`) reads the JSON and emits:

- `design-system.md` (DESIGN.md-compliant, YAML frontmatter + prose body)
- `brand.css` (`:root { ... }`)
- `source.css` (Tailwind `@theme { ... }`)
- `exports/tokens.dtcg.json` and `exports/tailwind.theme.json` via
  `npx @google/design.md export`

`@google/design.md` is pinned to `0.1.1` in `package.json` at the ai/
repo root and invoked via `npx`. The skill lives in ai/; the pipeline
runs against consumer repos (e.g. `yudame/cuttlefish`) whose
`docs/designs/` holds the real `.pen`. In ai/ the generator is exercised
against a fixture at `tests/fixtures/design_system/design-system.pen`.

## Pipeline diagram

```
Pencil desktop app (consumer repo)
        │
        ▼
<pen-path>/design-system.pen    (JSON — canonical ground truth)
        │
        ▼  python -m tools.design_system_sync --all --pen <pen-path> --css-root <css-root>
┌─────────────────────────────────────────────────────────────┐
│ Generator (deterministic, idempotent)                       │
│   1. Load .pen JSON                                         │
│   2. Categorize variables (longest-prefix-wins)             │
│   3. Sort component children before fill-ref scan           │
│   4. Emit design-system.md (sorted YAML frontmatter)        │
│   5. Emit brand.css (:root) + source.css (@theme)           │
│   6. npx @google/design.md lint  (exit 0 required)          │
│   7. npx export --format dtcg  / --format tailwind          │
└──────┬──────────┬──────────┬──────────┬──────────┬──────────┘
       ▼          ▼          ▼          ▼          ▼
<pen-dir>/    <css-root>/ <css-root>/ <pen-dir>/  <pen-dir>/
design-       brand.css   source.css  exports/    exports/
system.md                             tokens.     tailwind.
                                      dtcg.json   theme.json
       │
       ▼  (commit)
       │
       ▼  PreToolUse hook on Bash (git commit / git add, when run in ai/)
validate_design_system_sync.py
  → matches only when the command references a .pen path OR
    a <css-root>/{brand,source}.css path (regex anchored on filenames)
  → re-runs generator to tempdir
  → diffs against working-tree artifacts
  → exit 1 (block) if any artifact drifted
       │
       ▼
git commit proceeds
```

## Schema mapping (`.pen` → DESIGN.md)

Variable names are categorized by prefix. Because `--text-*` (colors)
overlaps `--text-size-*` / `--text-weight-*` / `--text-lh-*`
(typography), **longest-prefix-wins is a hard rule**, not an
optimization: prefixes are stored as a list sorted by `len()` descending,
and the generator breaks on the first match. A module-level assertion
verifies the order at import time.

| `.pen` variable prefix | DESIGN.md category | Example |
|---|---|---|
| `--color-*`, `--accent`, `--status-*`, `--surface-*`, `--text-*`, `--border-*` | `colors` | `--color-primary` → `colors.primary` |
| `--font-*`, `--text-size-*`, `--text-weight-*`, `--text-lh-*` | `typography` | `--text-size-body` → `typography.body.fontSize` |
| `--radius-*`, `--rounded-*` | `rounded` | `--radius-md` → `rounded.md` |
| `--space-*`, `--gap-*`, `--pad-*` | `spacing` | `--space-md` → `spacing.md` |
| anything else | unmapped (warning + error) | fails unless `--drop-unmapped` is passed |

Typography presets are aggregated from the suffix after the prefix:
`--font-body`, `--text-size-body`, `--text-weight-body`, `--text-lh-body`
all land in the `body` preset. Missing fields on non-`base` presets
inherit from a `base` preset if one exists.

Components come from Pencil frames with `"reusable": true` and
`name: "Category/Variant"`. Children are **sorted by variable reference
or attribute key before scanning**, so the "first child with a `fill`
ref → `backgroundColor`" rule is stable across `.pen` saves that reorder
JSON keys.

## CLI reference

All commands accept `--pen <path>` and `--css-root <path>`; both can be
supplied via `design-system-sync.toml` adjacent to the `.pen` (or at
`$CWD/docs/designs/design-system-sync.toml`). Explicit CLI flags
override TOML.

```bash
# Python-only emission (no Node required). Auto-falls back to --no-node
# when `npx` is missing.
python -m tools.design_system_sync --generate --pen <path> --css-root <dir>

# Full pipeline (lint + DTCG/Tailwind exports). Requires Node.
python -m tools.design_system_sync --all --pen <path>

# Drift check — regenerates to tempdir and byte-diffs against working tree.
# Exit 1 on drift; unified diff printed to stderr.
python -m tools.design_system_sync --check --pen <path>

# Gap-audit diff — produces markdown tables for pasting into gap-audit.md.
# MUST run BEFORE `git commit` so HEAD: still holds the prior pass's
# design-system.md.
python -m tools.design_system_sync --audit --pen <path>
```

Flags:

- `--no-node`: skip every `npx` call. `--generate` falls back to
  Python-only emission with a stderr warning; `--all` / `--audit` /
  `--check` (when exports are part of the comparison) exit 2.
- `--drop-unmapped`: silently drop variables whose prefix doesn't match
  any known category. Default is to error.
- `--repo-root <path>`: override the git-repo walk-up for `--audit`
  (CI / detached-worktree scenarios).

## Node-absent fallback

The `_probe_npx()` precheck runs `npx --version` up front. If it fails
(no `node` / `npx` on PATH, or the subprocess returns non-zero):

- `--generate` auto-enables `--no-node`, emits a stderr warning, and
  produces `design-system.md` / `brand.css` / `source.css` only.
- Every other subcommand exits 2 with
  "Node required for --all (lint + export). Install Node + npm and
  rerun, or use --generate --no-node for Python-only emission."

This means machines without Node still get the core CSS / markdown
emission; they simply don't produce the linted DTCG / Tailwind exports
(which still require Node on a build machine).

## Drift enforcement

Two PreToolUse hooks registered in `.claude/settings.json`:

### `validate_design_system_sync.py` (Bash matcher)

Fires only when a `git add` / `git commit` command path-anchors on a
design-system filename. The path-anchored regex is:

```
git (add|commit).*(?:(?<=^)|(?<=[/\s]))(design-system\.(pen|md)|brand\.css|source\.css)(?![A-Za-z0-9.])
```

Both boundaries are zero-width assertions. The lookbehind
`(?:(?<=^)|(?<=[/\s]))` accepts string-start, `/`, or whitespace, so
both `git add design-system.pen` (bare filename) and
`git add static/css/brand.css` (subdirectory) match while
`git add my-brand.css` (preceded by `-`) does not. The trailing
`(?![A-Za-z0-9.])` rejects letter / digit / dot continuations, so
`git add foo/source.css.bak`, `.tmp`, and `.orig` no longer false-fire
the way the prior `\b` boundary did.

On match, the hook re-runs `python -m tools.design_system_sync --check`
and returns `{"decision": "block", "reason": <diff>}` on drift. **Fails
open on internal error** (exits 0 with stderr warning) so a broken
validator cannot block all commits.

Every invocation appends one JSON line to
`logs/validate_design_system_sync.jsonl`:

```json
{"ts": "...", "tool_name": "Bash", "matched": true, "result": "ok", "duration_ms": 42, "reason": null, "error": null}
```

Operators can `tail -f logs/validate_design_system_sync.jsonl | jq 'select(.result=="error")'`
to detect silently-broken hooks post-hoc. This is the observability
surface that closes the fail-open gap.

### `validate_design_system_readonly.py` (Write|Edit matcher)

Blocks direct Write/Edit calls to `design-system.md`, `brand.css`,
`source.css`, `*.dtcg.json`, and `tailwind.theme.json`. Permits `.pen`
writes (the skill's inline safety gate handles that).

### Emergency bypass

`DESIGN_SYSTEM_HOOK_DISABLED=1` disables BOTH hooks for one invocation:

```bash
DESIGN_SYSTEM_HOOK_DISABLED=1 git commit -m "emergency hotfix"
```

Use only for genuine hotfixes — broken tooling, Node regressions, or
commits where drift blocking would stall a legitimate fix. Bypasses are
logged as `result: "bypassed"` in the JSONL log so audits remain
possible. **Run `python -m tools.design_system_sync --check` manually
right after bypassing** to confirm any drift was intentional.

## Adopting this in a consumer repo

The ai/-registered hooks do NOT fire in consumer-repo Claude Code
sessions — `.claude/settings.json` is per-project, so a session opened
against e.g. `yudame/cuttlefish` loads that repo's settings, not ai/'s.
Consumer repos opt into the same protection via one of two patterns:

### Option A — `.git/hooks/pre-commit` shell script

```bash
#!/usr/bin/env bash
# .git/hooks/pre-commit
set -euo pipefail
pen=docs/designs/design-system.pen
if [ -f "$pen" ] && git diff --cached --name-only | grep -qE '(design-system\.(pen|md)|brand\.css|source\.css)$'; then
    python -m tools.design_system_sync --check --pen "$pen"
fi
```

Works even when the consumer repo isn't using Claude Code. Install with
`chmod +x .git/hooks/pre-commit`.

### Option B — `.claude/settings.json` fragment

Vendor the hook via a wrapper that resolves ai/'s checkout location from
a `DESIGN_SYSTEM_AI_REPO` env var (or a vendored symlink):

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$DESIGN_SYSTEM_AI_REPO\"/.claude/hooks/validators/validate_design_system_sync.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

## `--audit` temporal ordering

`--audit` diffs against `git show HEAD:<pen-dir>/design-system.md`. It
MUST run BEFORE `git commit` of the regenerated artifacts — after
commit, `HEAD:` holds the CURRENT pass's `design-system.md` and the diff
collapses to empty.

The generator emits a stderr `--stale-warn` when the on-disk
`design-system.md` matches `HEAD`'s copy exactly (either nothing changed
or `--audit` ran post-commit). Canonical sequence:

```bash
python -m tools.design_system_sync --all   --pen <path>   # regenerate
python -m tools.design_system_sync --audit --pen <path>   # diff vs HEAD
# ...paste output into gap-audit.md under a new dated heading...
git add <pen-dir>/ <css-root>/brand.css <css-root>/source.css
git commit -m "design: <theme> pass"
```

When `HEAD:<pen-rel>/design-system.md` is absent (first pass), `--audit`
emits the placeholder `(initial pass — no prior diff)` and exits 0.

## Failure modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `error: design-system.pen not found at <path>` | `--pen` arg wrong or TOML missing | Pass `--pen <path>` or create `design-system-sync.toml` |
| `error: unmapped variable prefixes present` | variable doesn't match a known prefix | Rename to a known prefix or pass `--drop-unmapped` |
| `--all` exits with `check=True` error | `npx` not on PATH | Install Node, or use `--generate --no-node` for CSS-only emission |
| Drift-validator blocks commit after regen | Stale export files in working tree | Re-run `--all`, stage `exports/` updates |
| `--audit` exits 2, "could not locate a git repo" | running from a tempdir outside any git worktree | Pass `--repo-root <path>` or `cd` into the consumer repo |
| `--audit` prints empty diff | ran post-commit | Re-run before next `git commit`; the `--stale-warn` stderr line signals this |
| Hook doesn't fire in consumer repo | hooks are per-project | Use Option A or Option B under "Adopting this in a consumer repo" |

## Version pinning

- `@google/design.md@0.1.1` pinned in `package.json`;
  `package-lock.json` committed.
- The DESIGN.md spec is `alpha`. Minor-version bumps may change emitted
  format; generator refreshes are tracked as separate tickets (out of
  scope for Phase 1).
- `scripts/check_prerequisites.py` asserts the pinned version via
  `npx --no-install @google/design.md --version`.

## Testing

- `tests/unit/tools/test_design_system_sync.py` — prefix
  categorization (longest-prefix-wins lock-in), determinism, component
  children order independence, unmapped-prefix handling, `--no-node`
  fallback, `--audit` exit codes, path resolution precedence.
- `tests/unit/hooks/test_validate_design_system_sync.py` — stdin JSON
  handling, path-anchored regex, drift → block, escape hatch, JSONL log
  records.
- `tests/unit/hooks/test_validate_design_system_readonly.py` —
  Write/Edit blocking for each generated artifact, `.pen` whitelist.
- `tests/integration/test_design_system_pipeline.py` — full `--all`
  pipeline on the fixture (skipped when `npx` absent).

## See also

- `.claude/skills/do-design-system/SKILL.md` — Step 6 and Step 7 invoke
  the generator; Step 5's safety gate carries the Layer-A inline
  assertion.
- `package.json`, `package-lock.json` — Node toolchain pin.
- `scripts/remote-update.sh` — runs `npm ci --only=prod` guarded by
  `package.json` + `command -v npm`.
- `tests/fixtures/design_system/design-system.pen` — Pencil-openable
  fixture exercising every mapping bucket.
