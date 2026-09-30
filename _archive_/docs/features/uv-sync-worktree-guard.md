# uv-sync Worktree Guard

## Problem

SDLC build/patch lanes run in git worktrees under `.worktrees/{slug}/` and
`.claude/worktrees/{agent}/`. Historically these worktrees did **not** get
their own Python environment and shared the single project `.venv` at the repo
root. Since issue #2052, `agent/worktree_manager.create_worktree` eagerly
provisions a per-worktree `.venv` (see
[worktree-venv-isolation.md](worktree-venv-isolation.md)); this guard now
protects the shared env against `uv sync` from worktrees that are still
**unprovisioned** (e.g. harness-created `.claude/worktrees/{agent}/` checkouts
before their bootstrap).

`uv sync` (and `uv sync --frozen`) is **exact by default**: it resolves
against the worktree's own `pyproject.toml`/lock and removes any package not
in that resolved set from the active environment. Run from a worktree, it
silently strips the *shared* `.venv` — dropping `pytest`, `ruff`,
`pytest-xdist`, and any branch-only dependency other concurrent lanes (and the
standalone worker) depend on. This has recurred more than once (see issue
#2050, and its precursor recoveries on the #1925 PydanticAI lane).

## Solution

Two complementary pieces:

1. **A PreToolUse guard** (`.claude/hooks/validators/validate_no_uv_sync_in_worktree.py`)
   blocks the destructive command by construction — not by relying on agents
   remembering an instruction.
2. **A venv-health check** (`tools/venv_health.py`, `valor-venv-health` CLI)
   is a cheap presence probe run at a few points in the pipeline so any
   corruption that slips past the guard is a loud warning instead of a
   silent, confusing `ModuleNotFoundError` somewhere downstream.

### The guard

Wired as a `matcher: "Bash"` PreToolUse hook in `.claude/settings.json`,
alongside the other Bash validators (`validate_commit_message.py`,
`validate_no_inline_timeout.py`, `validate_no_raw_redis_delete.py`, etc.).

On every Bash tool call, it:

1. Reads the hook input JSON from stdin (`tool_name`, `tool_input.command`,
   top-level `cwd`).
2. Determines the *effective* working directory: `cwd`, unless the command's
   first simple command is a `cd <path>` prefix (`cd <path> && ...` or
   `cd <path>; ...`), in which case that resolved path wins.
3. Checks whether that effective directory is inside `.worktrees/` or
   `.claude/worktrees/` — a **path-component** match, not a substring match,
   so a sibling directory like `.worktrees-backup` never false-positives.
4. **Isolation check (issue #2052):** if the worktree root has its own venv
   (`<worktree-root>/.venv/pyvenv.cfg` exists), the command is ALLOWED — `uv
   sync` targets that worktree-local env and cannot strip the shared one. The
   hook emits a non-blocking `{"systemMessage": ...}` notice (warn, not
   block). The probe deliberately keys on `pyvenv.cfg`, not the provisioner's
   `.provisioned` success marker: `uv sync` against a partial worktree venv is
   the *repair* action, and requiring the marker would dead-end the
   `uv venv .venv` → `uv sync` bootstrap path.
5. Otherwise (unprovisioned worktree), scans the command (split on shell
   control operators `&&`, `||`, `;`, `|`, newlines; each simple command
   tokenized with `shlex`) for a `uv sync` invocation, anchored to **command
   position**: the first non-flag, non-env-assignment token must be `uv` and
   the next non-flag token must be `sync`. A bare substring like `uv sync`
   appearing inside an unrelated argument — e.g. `git commit -m "fix uv sync
   bug"` — does **not** match.
6. If a `uv sync` invocation is found in an unprovisioned-worktree context, it
   emits `{"decision": "block", "reason": <message>}` and exits 0. The block
   message teaches both escape paths: the isolation bootstrap

   ```
   uv venv .venv        # then `uv sync` is allowed (worktree now isolated)
   ```

   and the scoped-install alternative into the shared env:

   ```
   uv pip install --python <repo>/.venv/bin/python "<pkg>==<ver>"
   ```

   The latter is additive and does not run project resolution, so it cannot
   strip the shared environment.

The guard **only** blocks `uv sync`. `uv pip install`, `uv run`, `uv lock`,
and every other `uv` subcommand pass through untouched — those are the very
alternatives the block message points to.

**Fail-open:** any parse error (malformed JSON, unparseable shell tokens,
missing fields) results in exit 0 (allow). The guard must never crash a
legitimate Bash call.

There is deliberately no allowlist escape-hatch marker (e.g. a
`# uv-sync-guard: allow` comment) — the isolation check above IS the
structural escape hatch: a worktree with its own `.venv` passes, one without
is always wrong to `uv sync` from. (This replaced the pre-#2052 "always
block" posture.)

#### Hook-resolution hazard

PreToolUse hooks come from whichever `settings.json` Claude Code resolves via
`$CLAUDE_PROJECT_DIR` for the running session — not necessarily main's. A
session whose CWD is inside a worktree can, depending on how it was launched,
load a **stale `.claude/settings.json` copy carried on the worktree's own
branch** (one that predates this guard), or resolve `$CLAUDE_PROJECT_DIR` to
the worktree rather than the main checkout. In either case the new hook entry
is simply absent from what that session loads, and `uv sync` is never
intercepted — adding the hook to main's `settings.json` is necessary but not
sufficient; it also depends on the session resolving `$CLAUDE_PROJECT_DIR` to
the main checkout. This is exactly why the guard alone is not treated as a
complete fix: a real-dispatch integration test exercises `create_worktree()` +
an actual `Bash` tool call with `cwd` inside that worktree to confirm the hook
fires in practice, and the venv-health check below is the required backstop
for the case where it doesn't.

### The venv-health check

`tools/venv_health.py` probes the **running interpreter's** environment for:

- `pytest`, `xdist` (pytest-xdist) — module presence via
  `importlib.util.find_spec`, which does not execute the module.
- `ruff` — checked as a **file's existence** at `<venv-bin-dir>/ruff`, not an
  `import ruff`. Ruff's Python package internals are version-fragile and not
  a stable import target; the CLI binary is what the repo actually invokes,
  and its presence in the venv's `bin/` directory is a stable, consistent
  check both in-process and from a subprocess.

The venv's `bin/` directory is derived from `Path(sys.executable).parent` —
deliberately *not* `.resolve()`d, since a venv's `bin/python` is commonly a
symlink to a system/homebrew interpreter, and fully resolving that symlink
would land outside the venv entirely.

`python -m tools.venv_health` (or the `valor-venv-health` CLI, installed via
the `[project.scripts]` entry) prints the missing set and exits 1 if
anything's missing, 0 otherwise.

**Where it's wired:**

- **Warn-only, post-merge:** `agent/worktree_manager.cleanup_after_merge`
  calls `tools.venv_health.check_health()` at the end of its cleanup and logs
  a `logger.warning` if anything's missing — it never raises, so a stripped
  env never blocks lane exit or merge cleanup itself.
- **Warn-only, stage entry:** the `/do-test` and `/do-patch` SDLC skills
  (repo addenda `docs/sdlc/do-test.md`, `docs/sdlc/do-patch.md`) run
  `"${AI_REPO_ROOT:-$HOME/src/ai}/.venv/bin/python" -m tools.venv_health ||
  true` before their main work, so a stripped shared env surfaces as an
  early, diagnosable warning rather than a confusing wall of test failures.

## Convention note (optional)

Humans and agents should still prefer scoped `uv pip install --python
<repo>/.venv/bin/python "<pkg>==<ver>"` over `uv sync` from a worktree — but
the guard above is the structural fix, not this note. Per the plan's
Documentation directive, this convention is intentionally **not** re-added to
`builder.md`, `dev.md`, or `CLAUDE.md`; instruction-as-mitigation is exactly
what this guard replaces.

## Why not just block all `uv sync`, everywhere?

`uv sync` from the repo root (not inside a worktree) is legitimate — that's
how the shared `.venv` itself gets provisioned/updated. The guard only fires
when the effective working directory is inside `.worktrees/` or
`.claude/worktrees/`.

## Related

- Issue #2050 (this guard).
- Issue #2052 (shipped): per-worktree venv isolation via
  `UV_PROJECT_ENVIRONMENT` + eager per-slug venv provisioning — see
  [worktree-venv-isolation.md](worktree-venv-isolation.md). Its landing is
  what relaxed this guard from block-always to allow-with-notice for isolated
  worktrees.
- `docs/plans/guard-uv-sync-in-worktree.md` — the plan this guard shipped
  from.
