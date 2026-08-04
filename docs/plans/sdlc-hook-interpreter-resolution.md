---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-04
tracking: https://github.com/tomcounsell/ai/issues/2503
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-04T05:30:26Z
---

# SDLC Hook Interpreter Resolution

## Problem

Claude Code runs every hook command through a non-interactive `/bin/sh`. Every command
this repo generates starts with a bare `python`, which that shell cannot resolve — no
`~/.zshenv`, no shell aliases, and modern macOS ships no `/usr/bin/python`.

Verified on this machine:

```
$ env -i /bin/sh -c 'python foo.py'
/bin/sh: python: command not found
exit=127
```

Exit 127 is the whole story. Claude Code treats only **exit 2** as a block, so a hook
whose interpreter does not resolve is not a loud failure — it is a **silently disabled
guard** that prints one error line per tool call. The global-scope hooks (registered in
`~/.claude/settings.json`, active in *every* repo on the machine) are therefore inert
everywhere except `~/src/ai`, where the repo venv happens to be on `PATH`.

A second, independent bug hides behind the first. `.claude/hooks/sdlc/validate_commit_message_sdlc.py:32`
declares `def get_current_branch() -> str | None:` with no `from __future__ import annotations`.
Python evaluates that annotation at **module import time**, so under Python 3.9
(`/usr/bin/python3` on macOS) it raises before `main()` runs and the `try/except`
fail-open guard inside `main()` never executes:

```
$ echo '{}' | env -i /usr/bin/python3 ~/.claude/hooks/sdlc/validate_commit_message_sdlc.py
  File ".../validate_commit_message_sdlc.py", line 32, in <module>
    def get_current_branch() -> str | None:
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

**Current behavior:** The commit-message guard and the raw-Redis guard are unenforced in
every repo except `~/src/ai` while appearing installed. Every `Bash`/`Write`/`Edit` tool
call in a foreign repo emits a `hook error` line. Both failure modes are **fail-open**.

**Desired outcome:** Generated hook commands invoke an interpreter that resolves
deterministically under `env -i /bin/sh`, on every machine, in the main checkout **and**
in `.worktrees/` sessions. Global-scope scripts import cleanly under the oldest
interpreter they can land on. Regression tests fail if either property breaks.

## Freshness Check

**Baseline commit:** `9fe58f45d52e5e6d64eb6d10d273cae574750c2c`
**Issue filed at:** 2026-08-01T08:13:19Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/hooks/sdlc/validate_commit_message_sdlc.py:32` — `def get_current_branch() -> str | None:` — still holds, verbatim.
- `.claude/hooks/validators/validate_no_raw_redis_delete.py:107` — `def find_violation(command: str) -> str | None:` — still holds, verbatim.
- `scripts/update/hardlinks.py:707` — `parts = [f"python {script_base}/{decl.script}"]` inside `_build_hook_command` — still holds; it remains the single emission site, and `HookDeclaration` still has no interpreter field.
- `scripts/update/hooks.py:99` — the `uv run` audit — still holds (details corrected below).

**Cited sibling issues/PRs re-checked:**
- #2435 / PR #2453 — merged 2026-07-31. The manifest-driven architecture is intact and is the target for this fix.
- PR #2454 — now **CLOSED** (state `CLOSED`, `mergeStateStatus: DIRTY`, updated 2026-08-04). The issue's acceptance criterion "PR #2454 is closed" is already satisfied; no action needed.
- #2504 — a duplicate of this issue, closed `NOT_PLANNED` on 2026-08-01. No content to merge in.

**Commits on main since issue was filed (touching referenced files):**
`git log --since=2026-08-01T08:13:19Z` over `scripts/update/hardlinks.py`, `scripts/update/hook_manifest.py`, `.claude/hooks/manifest.toml`, `.claude/hooks/sdlc/`, `scripts/update/hooks.py`, `tests/unit/test_update_hardlinks.py`, `tests/unit/test_hook_migration.py` returns **zero commits**. Nothing has moved.

**Active plans in `docs/plans/` overlapping this area:** none. The most recent plans
(`durability-room-job-agentrun`, `pipeline-graph-single-source-of-truth`) touch the SDLC
ledger and pipeline graph, not hook registration.

**Notes:** The issue's Recon Summary over-counted the PEP 604 blast radius for *global*
scope. Spike-2 found exactly **one** global-scope script affected, not two — the second
crasher (`validators/validate_no_raw_redis_delete.py`) is not a declared global hook at
all; it reaches `~/.claude/settings.json` through a stale pre-manifest entry. See
Spike Results 3.

## Prior Art

- **#2435 / PR #2453** (merged 2026-07-31, "Hook registration: per-event dispatcher + manifest-generated scopes") — replaced the hardcoded `_SDLC_HOOK_DEFS` list with manifest-driven generation for both scopes, and deliberately normalized the one `uv run` registration down to bare `python`. This is the architecture the fix targets, and the source of the bare-`python` literal. It also shipped `_migrate_hook_registration_manifest_ids` in `scripts/update/migrations.py`, the only existing legacy-command rewrite path.
- **PR #2454** (closed, branch `hotfix/sdlc-hook-python3`) — an earlier attempt at this exact fix. Patched `_SDLC_HOOK_DEFS` and the pre-manifest `_merge_hook_settings` shape that #2453 deleted, and edited a file that has since been renamed. Unsalvageable; already closed.
- **#2504** (closed NOT_PLANNED) — duplicate framing of the same bare-`python` bug.
- **PR #1957** ("Add OpenCode config sync from Claude Code configuration") — touches config sync but not hook command construction; no interpreter coupling.

No prior attempt ever shipped. This is the first fix to land.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2454 | Swapped the literal `python` for `python3` in `_SDLC_HOOK_DEFS` | Two reasons. (a) It targeted internals that #2453 deleted three days later, so it could never merge. (b) The swap itself is wrong: `python3` is **machine-dependent**. Under `env -i /bin/sh` on this machine it resolves to `/usr/local/bin/python3` (3.12.6, a Homebrew accident), while a clean mac resolves `/usr/bin/python3` (3.9.6). A fix that passes on the developer's box and silently breaks fleet-wide is worse than the bug. |
| #2435 / PR #2453 | Removed the repo's only `uv run` hook registration, normalizing everything to bare `python` | Correct decision (`uv run` in a hook can corrupt the venv and is audited against), but it treated the interpreter as an incidental detail rather than a contract. It centralized the emission site — which is what makes this fix a two-line change — but baked the broken token into the single source of truth. |

**Root cause pattern:** the interpreter token was never treated as a *deployment contract*
with its own requirements per scope. Both prior passes reasoned about it by inspection
inside an environment where `PATH` already had a working `python`. Neither executed a
generated command under a stripped environment. Every empirical claim in this plan was
verified with `env -i`.

## Research

**Queries used:**
- "Claude Code hooks CLAUDE_PROJECT_DIR environment variable hook command shell execution"
- "Claude Code hooks best practice absolute path python interpreter command not found settings.json"

**Key findings:**

- **Identical hook handlers are deduplicated automatically — command hooks by command string and args.** ([hooks reference](https://docs.claude.com/en/docs/claude-code/hooks)) This confirms the issue's dedup claim and pins down the migration requirement: changing the interpreter token produces a *different* string, so an already-deployed legacy entry is **not** superseded — it keeps firing alongside the new one. Legacy entries must be rewritten or removed in place, never merely out-registered.
- **Official troubleshooting for "command not found" in a hook is: use absolute paths or `${CLAUDE_PROJECT_DIR}`.** ([hooks guide](https://code.claude.com/docs/en/hooks-guide)) Hooks do not reliably inherit shell `PATH` or venv activation. The documented shape for a venv interpreter is exactly `${CLAUDE_PROJECT_DIR}/.venv/bin/python ${CLAUDE_PROJECT_DIR}/.claude/hooks/x.py`. This validates the direction for project scope — with one repo-specific caveat the docs cannot know about (see Spike 1).
- **`CLAUDE_PROJECT_DIR` is the stable project root** and does not change when working directories are added mid-session; hooks run non-interactive, in the current directory, with Claude Code's environment. This makes it a safe anchor for project-scope commands, and keeps the committed `.claude/settings.json` free of machine-specific absolute paths.
- **Adding `"args": []` to a hook switches it to exec form** (spawned directly, no shell). Noted but not adopted — it would change the shape of all 17 project entries and the `|| true` guard depends on shell semantics.

Both findings are saved to project memory (`valor`) for reuse.

## Spike Results

### spike-1: Is `"$CLAUDE_PROJECT_DIR"/.venv/bin/python` safe for project scope?
- **Assumption**: "The documented `${CLAUDE_PROJECT_DIR}/.venv/bin/python` shape works for this repo's project-scope hooks."
- **Method**: code-read + direct filesystem probe (**re-probed under `env -i` during critique — the original finding was wrong, see below**)
- **Finding**: **NO — it would break most project hooks inside a worktree, and silently pick a *stale* interpreter in the rest.** `.claude/settings.json` is git-tracked (so a machine-specific absolute path is not an option either), and this repo runs all SDLC build work inside `.worktrees/{slug}/`, where `$CLAUDE_PROJECT_DIR` is the *worktree* root.

  **Corrected measurement (2026-08-04, `[ -x "$d/.venv/bin/python" ]` over all 57 worktrees):** **not all worktrees have a `.venv` — 9 of 57 do**, with divergent versions. The original claim "not one has a `.venv`" was false and was reached by inspection rather than execution — the same root-cause pattern this issue indicts. The 9: `fix-test-isolation-cluster`, `headless-runner-zombie-liveness`, `hybrid-retrieval-eval`, `nightly-triage-02917b62`, `nightly-triage-40d7a187`, `no-progress-tui-liveness-nudge-rung`, `sdlc_1535_pipeline_portability`, `sdlc-1249`, `sdlc-1997`. Version divergence is real and measured: main checkout = 3.14.3, `nightly-triage-02917b62` = 3.13.2. Nothing in `agent/worktree_manager.py` or `scripts/update/` creates or symlinks them (the repo actively guards against it via `validate_no_uv_sync_in_worktree.py`) — they are operator residue, unmanaged and unmaintained.

  So the naive shape has **two** failure modes, not one: in 48 of 57 worktrees it resolves nothing (outage), and in the other 9 it resolves an unmanaged, version-divergent, possibly-stale venv (silent wrong answer). The second is worse, because it looks like it worked.
- **Confidence**: high (re-measured by execution)
- **Impact on plan**: Project scope needs a resolver, not a literal path — and the resolver's **precedence must be main-checkout-first**, not local-first, so the 9 unmanaged worktree venvs are never selected. Drove the `hook_python` shim design in Technical Approach.

### spike-1b: Does a shim resolve correctly from both the main checkout and a worktree?
- **Assumption**: "`git rev-parse --git-common-dir` can recover the main repo root from inside a worktree, under a stripped environment."
- **Method**: prototype (scratchpad shim, executed under `env -i`)
- **Finding**: **Yes, cleanly.** `git -C <worktree> rev-parse --path-format=absolute --git-common-dir` returns `/Users/valorengels/src/ai/.git` from *both* the worktree and the main checkout, and `git` itself resolves under `env -i /bin/sh` at `/usr/bin/git`. A prototype shim was executed three ways:

  | `CLAUDE_PROJECT_DIR` | Result | Exit |
  |---|---|---|
  | `.worktrees/agent-wiki-728` | `Python 3.14.3` | 0 |
  | `/Users/valorengels/src/ai` | `Python 3.14.3` | 0 |
  | `/tmp` (non-git) | stderr: `no repo venv interpreter found` | 0 (fail-open) |

  And a real project hook ran end-to-end through it: `echo '{}' | env -i CLAUDE_PROJECT_DIR=... /bin/sh -c "<shim> .claude/hooks/pre_tool_use.py"` → exit 0.
- **Confidence**: high
- **Impact on plan**: The shim is the project-scope interpreter. No machine-specific path enters git; worktrees are covered. **Amended during critique:** the prototype table above was produced with the original local-first branch order, whose first row happened to hit a worktree with no local `.venv`. After spike-1's correction the order is inverted to main-checkout-first and was **re-executed against a worktree that does have one** — see the precedence table in Technical Approach. The `git rev-parse` mechanism itself is unchanged and still verified.

### spike-2: What interpreter should global scope use, and what does 3.9 actually cost?
- **Assumption**: "Several global-scope scripts need work to run under the oldest interpreter."
- **Method**: prototype in an isolated worktree
- **Finding**: The 3.9 tax is **one line in one file**. Interpreters on this machine: `/usr/bin/python3` → 3.9.6; `/usr/local/bin/python3` → 3.12.6; `env -i /bin/sh -c 'command -v python3'` → `/usr/local/bin/python3` (a Homebrew accident, *not* a guarantee); `.venv/bin/python` → 3.14.3, itself a symlink into `/opt/homebrew/opt/python@3.14/`. Under `env -i /usr/bin/python3` (3.9.6), of the four globally-deployed files, **three already run clean** (`sdlc_context.py`, `sdlc/sdlc_reminder.py`, `sdlc/validate_sdlc_on_stop.py` — all exit 0) and exactly **one fails**: `sdlc/validate_commit_message_sdlc.py`, the sole `blocking = true` script. Adding `from __future__ import annotations` to that one file makes all four clean. Zero matches across `sdlc/` for `datetime.UTC`, `tomllib`, `typing.Self`, `ExceptionGroup`, or `asyncio.timeout`; imports are stdlib-only, no third-party, and the one repo-module import (`models.agent_session` in `sdlc_context.py:45`) is function-local inside `except Exception: pass`, so it cannot reproduce the module-import failure class. The fork discipline held.
- **Confidence**: high
- **Impact on plan**: Global scope pins to an absolute system interpreter (3.9 floor) rather than the venv. The venv path was rejected: it embeds the username, requires the venv to exist, **and** dereferences into a Homebrew cellar directory that `brew upgrade` deletes — three independent silent-breakage sources, each of which would disable the guard in *every* repo on the machine.

### spike-3: The `validate_no_raw_redis_delete.py` scope discrepancy
- **Assumption**: "The manifest and the deployed state disagree; one of them is a generator bug."
- **Method**: code-read + inspection of the live `~/.claude/settings.json`
- **Finding**: **Not a generator bug — stale pre-manifest deployed state the generator structurally refuses to touch.** The live `~/.claude/settings.json` holds 6 entries. The 3 carrying a `# hook:<id>` marker map 1:1 onto the 3 `scope = "global"` manifest declarations; there are **zero orphaned marked entries**, so the generator itself is fully converged. The drift is an **unmarked** entry:

  ```
  PreToolUse / matcher "Bash" / timeout 5
  python /Users/valorengels/.claude/hooks/validators/validate_no_raw_redis_delete.py
  ```

  Its shape (a `validators/` path, no `|| true`, timeout 5) is old `_SDLC_HOOK_DEFS` output. `_merge_hook_settings` indexes only entries where `_extract_manifest_id()` is truthy and its removal loop iterates only that index, so an unmarked entry is unreachable by construction — the docstring states the policy: *"Entries with no marker at all (hand-added or pre-migration legacy entries) are left completely untouched."* Two further leftovers: the same file is a **live hardlink** (shared inode) with the project copy, so the deployed global copy carries the PEP 604 crasher at line 107 and fires on **every Bash call in every repo**; and `~/.claude/hooks/` still holds whole orphaned pre-manifest trees (`validators/` with 20 files, `dispatch/`, `hook_utils/`, plus 8 top-level scripts and a stray `manifest.toml`) that `sync_user_hooks` neither created nor removes.
- **Confidence**: high
- **Impact on plan**: Resolved **in scope**, not deferred. The correct fix is removal, not a 3.9 port: the manifest declares this validator `scope = "project"` and its logic already runs in-process via `dispatch/pre_tool_use_bash.py`. The global entry is an unintended leftover that is currently inert anyway. A new migration removes the **registration entry**. Note the fix **cannot** go through the marker-keyed path — it needs a targeted legacy sweep. **Amended during critique:** pruning the orphaned *files* is deferred to a follow-up issue (see Technical Approach); once the registration is gone they are inert.

### spike-4: Blast radius of changing the interpreter token
- **Assumption**: "A handful of tests hardcode the token; the audit in `scripts/update/hooks.py` may object."
- **Method**: code-read
- **Finding**: Four precise results.
  1. **Tests that must change** (generator output): `tests/unit/test_update_hardlinks.py:251`, `:738-739`; `tests/unit/test_hook_migration.py:268`. Plus all **17** commands in the tracked `.claude/settings.json` (lines 15, 27, 37, 47, 59, 69, 74, 79, 84, 89, 99, 109, 114, 126, 131, 143, 155) — `test_update_hardlinks.py:675` (`test_generate_project_hooks_regen_matches_currently_committed_file`) asserts byte-identity with the committed file, so it **must** be regenerated in the same commit.
  2. **Tests that must NOT change**: `tests/unit/test_hook_migration.py:58`, `:70`, `:82` are deployed-reality fixtures mirroring the migration's exact-match literal at `scripts/update/migrations.py:487`; `:106` is a foreign hook that must survive untouched. Comment/docstring hits at `:11-13`, `:176`, `:195` and the `test_hooks_audit.py` fixtures are unrelated. Combined collection: 41 tests (28 + 13).
  3. **`scripts/update/hooks.py` does no interpreter validation at all.** `audit_skill_hooks()` scans `SKILL.md` **frontmatter only**, never `settings.json`; it early-returns unless the frontmatter contains `"Stop:"`, then checks `if "uv run" in frontmatter` (plain substring) and that any `$CLAUDE_PROJECT_DIR/...py` path exists. A new token cannot trip it unless it literally contains `uv run`. The one place that *does* parse generated commands is `reflections/audits/hooks_audit.py:91-99`, which takes the **first whitespace token ending in `.py`/`.sh`** as the script path and requires it to exist — so an extensionless shim token is safe, and a `.sh` wrapper would **not** be.
  4. **Migration reality**: `_LEGACY_FORK_HOOKS` (`migrations.py:476-481`) covers exactly 3 ids, matched by `_legacy_fork_command_prefix()` → `f"python {hooks_root}/{legacy_script}"`, compared **byte-exactly** after stripping a trailing `|| true`. That literal must keep its bare `python` forever — it is a match key against what is on disk, not generated output. There is **no** generic "rewrite any legacy bare-python command" pass. Ordering is `sync_claude_dirs` (run.py Step 1.5) **before** `run_pending_migrations` (Step 3.6); pass 2 of the existing migration collapses duplicate marked entries per id, so the transient duplicate that ordering creates is already handled.
- **Confidence**: high
- **Impact on plan**: Fixed the test-update list, ruled out an audit conflict, established that the shim must be extensionless, and confirmed a *new* migration is required for the unmarked entry.

## Data Flow

1. **Entry point**: an operator runs `/update` on a machine. `scripts/update/run.py` Step 1.5 calls `sync_claude_dirs`.
2. **`scripts/update/hook_manifest.py`**: parses `.claude/hooks/manifest.toml` into `HookDeclaration`s, fail-closed, in declaration order.
3. **`scripts/update/hardlinks.py`**: splits by `scope`.
   - `generate_project_hooks` → `_build_hook_command(decl, '"$CLAUDE_PROJECT_DIR"/.claude/hooks', interpreter=<project token>)` → whole-block rewrite of the git-tracked `.claude/settings.json`.
   - `sync_user_hooks` → hardlinks each global script into `~/.claude/hooks/`, then `_merge_hook_settings` → `_build_hook_command(..., interpreter=<global token>)`, merged into `~/.claude/settings.json` keyed on the `# hook:<id>` marker.
4. **`scripts/update/migrations.py`** (run.py Step 3.6): rewrites/removes pre-manifest entries that carry no marker and are therefore invisible to step 3.
5. **Output**: two `settings.json` files on disk. Claude Code reads them at session start and executes each `command` through a non-interactive `/bin/sh` on the matching event — **this is the step where the interpreter token is finally resolved**, and the only step the previous fixes never exercised.

## Architectural Impact

- **New dependencies**: none. One new committed file (`.claude/hooks/hook_python`, POSIX `sh`, no Python, no third-party). `git` is a new runtime dependency *of the shim* — verified present at `/usr/bin/git` under `env -i`, and already a hard dependency of every workflow in this repo.
- **Interface changes**: `_build_hook_command` gains a required `interpreter` parameter. `HookDeclaration` is **unchanged** — no per-hook `interpreter` field (see Technical Approach for why).
- **Coupling**: reduced. Today the interpreter is an implicit `PATH` dependency shared by both scopes; after this it is an explicit, scope-owned, per-machine-resolved value.
- **Data ownership**: unchanged. The manifest stays the single source of truth for *which* hooks exist; the generator owns *how* they are invoked.
- **Reversibility**: high. Revert the commit and re-run `/update`; both `settings.json` files regenerate from the manifest. The migration is idempotent and takes a timestamped `.bak` before writing.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the two-tier interpreter decision and the legacy-sweep scope)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| A working system Python 3 for global scope | `env -i /bin/sh -c '/usr/bin/python3 -V'` | Global-scope hook interpreter; must actually execute, not just exist (a bare Command Line Tools stub exits non-zero and prompts) |
| `git` resolvable under a stripped shell | `env -i /bin/sh -c 'command -v git'` | The `hook_python` shim uses `git rev-parse --git-common-dir` to find the main checkout from a worktree |
| Repo venv present in the main checkout | `test -x /Users/valorengels/src/ai/.venv/bin/python` | Project-scope hooks import repo modules and use 3.11+ runtime symbols |

## Solution

### Key Elements

- **`hook_python` shim** (`.claude/hooks/hook_python`, mode 755, extensionless): resolves the repo venv interpreter at hook-execution time. Precedence is **main-checkout-first**: it resolves the main checkout via `git rev-parse --path-format=absolute --git-common-dir` and execs that venv's `python`, falling back to `$CLAUDE_PROJECT_DIR/.venv/bin/python` only when git cannot answer, then fails **open and loud** (stderr message, exit 0). Main-first is deliberate — 9 of 57 worktrees carry an unmanaged, version-divergent `.venv`, so a local-first order would silently exec a stale interpreter. This is the project-scope interpreter.
- **Global interpreter resolver**: resolves an absolute system interpreter once, at *generation* time, on each machine, by probing candidates in order and requiring `-V` to actually execute. The resolved absolute path is baked into the per-machine `~/.claude/settings.json`.
- **`_build_hook_command(interpreter=...)`**: the single emission site stops hardcoding a token and takes it from its caller. Each generator supplies its scope's interpreter.
- **`from __future__ import annotations`** in `sdlc/validate_commit_message_sdlc.py`: one line, fixes the import-time crash for the only affected global script.
- **Legacy sweep migration**: removes the unmarked pre-manifest global **registration entry** for `validate_no_raw_redis_delete.py` from `~/.claude/settings.json`. Registration-only — it deletes no files. Gated so hand-added user hooks are never touched. (Pruning the orphaned *files* under `~/.claude/hooks/` is deferred to a follow-up issue; see Technical Approach.)
- **Regression tests**: a bare-`python` ban on generated commands, an always-on AST check for pre-3.10 annotation syntax in global scripts, a real `env -i` execution test, a shim worktree-precedence test, and a double-`/update` idempotence test.
- **Audit signal for the shim's fail-open branch**: `reflections/audits/hooks_audit.py` gains a check for the shim's stderr message, so a machine whose shim starts failing open produces a distinguishable finding instead of blending into generic hook-error noise.

### Flow

`/update` on a machine → manifest parsed → **project scope** renders `"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python <script>` into the tracked `.claude/settings.json` → **global scope** probes candidate interpreters, renders `<resolved absolute python3> ~/.claude/hooks/sdlc/<script>` into the per-machine `~/.claude/settings.json` → migration sweeps unmarked legacy entries → Claude Code session starts in **any** repo → `/bin/sh` resolves both tokens without `PATH` → guards fire.

### Technical Approach

**Q1 — what token does `_build_hook_command` emit?** Neither `python3` nor a single literal. The parameter is supplied by the caller, because the correct answer differs by scope. `HookDeclaration` deliberately gains **no** `interpreter` field: the interpreter is a property of *where a hook is deployed*, not of the individual hook, and a per-hook field would invite 20 opportunities for drift on a value that has exactly two correct answers. Encode it as two module-level constants in `scripts/update/hardlinks.py` and thread it through as an explicit argument.

**Q2 — do the scopes differ? Yes, and they must.**

| | Project scope | Global scope |
|---|---|---|
| Registered in | `.claude/settings.json` (**git-tracked**) | `~/.claude/settings.json` (per-machine, generated) |
| Runs in | this repo + its `.worktrees/` | every repo on the machine |
| Needs | repo venv (repo imports, `datetime.UTC`, third-party) | stdlib only, by fork discipline |
| Token | `"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python` | resolved absolute system `python3` |
| Why not the other | an absolute path would be committed to git and would be wrong on every other machine | the venv path embeds a username, can be mid-rebuild, and dereferences into a Homebrew cellar dir that `brew upgrade` deletes |

**Q3 — how is the global absolute path made portable?** It is not portable, and does not need to be: `~/.claude/settings.json` is generated per machine by `/update`, so a machine-local absolute path is exactly right there. The tracked `.claude/settings.json` is the file that must stay machine-neutral, and `$CLAUDE_PROJECT_DIR` keeps it so. **Confirmed.**

**The shim, concretely** (`.claude/hooks/hook_python`):

```sh
#!/bin/sh
# Resolve the repo venv interpreter for a Claude Code hook.
# Hooks run under a non-interactive /bin/sh with no PATH guarantees (issue #2503).
#
# PRECEDENCE IS DELIBERATE AND MAIN-CHECKOUT-FIRST. 9 of 57 worktrees carry an
# unmanaged .venv with a divergent Python (measured: main 3.14.3 vs 3.13.2), so a
# local-first order would silently exec a stale interpreter. Resolve the main
# checkout via git FIRST; only fall back to $root when git cannot answer.
root="${CLAUDE_PROJECT_DIR:-$PWD}"
common=$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
if [ -n "$common" ]; then
  main=$(dirname "$common")
  if [ -x "$main/.venv/bin/python" ]; then exec "$main/.venv/bin/python" "$@"; fi
fi
# Non-git checkout, or a main checkout git could not identify.
if [ -x "$root/.venv/bin/python" ]; then exec "$root/.venv/bin/python" "$@"; fi
echo "hook_python: no repo venv interpreter found under $root" >&2
exit 0
```

**Precedence rule (stated decision, not line-order accident).** The shim resolves the
**main checkout's** venv first, via `git rev-parse --path-format=absolute --git-common-dir`,
and uses `$root/.venv/bin/python` only as a fallback for the case where git cannot answer at
all. This is critique option (b) and it removes the untrusted-venv branch outright: from a
worktree, the main checkout's venv is the *only* one the repo manages, so it is the only one a
hook should ever run under. In the main checkout the two paths coincide (`git-common-dir`
resolves to `<main>/.git`, whose dirname is the main checkout), so the fallback is genuinely a
fallback and not a second live path.

Verified by execution under `env -i` (2026-08-04), including from `sdlc-1249`, one of the 9
worktrees that *does* carry its own `.venv`:

| `CLAUDE_PROJECT_DIR` | Resolved `sys.prefix` | Version | Exit |
|---|---|---|---|
| `.worktrees/sdlc-1249` (has local `.venv`) | `/Users/valorengels/src/ai/.venv` | 3.14.3 | 0 |
| `/Users/valorengels/src/ai` (main checkout) | `/Users/valorengels/src/ai/.venv` | 3.14.3 | 0 |
| `/tmp` (non-git, no venv) | — stderr: `no repo venv interpreter found` | — | 0 (fail-open) |

The first row is the load-bearing one: under the old local-first order it would have resolved
`sdlc-1249`'s own venv. This branch is live in production today, so it is pinned by test
(task 4 item (e)), not assumed absent.

Three further deliberate choices. **Extensionless** — `reflections/audits/hooks_audit.py:91-99` takes the first token ending in `.py`/`.sh` as the script path, so a `.sh` name would make the audit inspect the shim instead of the hook. **`exec`** — no extra process lingers, and the hook's exit code passes through unchanged, preserving `blocking = true` semantics. **Fail-open with a stderr message and exit 0** — this matches today's de-facto behavior (a missing interpreter already yields 127, which Claude Code does not treat as a block) while making the failure *visible* instead of silent. Making it fail-closed is a separate behavioral decision and is a No-Go here.

**The global resolver**: probe `("/usr/bin/python3", "/usr/local/bin/python3", "/opt/homebrew/bin/python3")` in order, accepting the first for which `subprocess.run([cand, "-V"], env={}, timeout=...)` exits 0. Probing by execution rather than `os.path.exists` is load-bearing on macOS: `/usr/bin/python3` exists even without Command Line Tools, where it is a stub that exits non-zero and pops an install dialog. If none succeeds, `sync_user_hooks` records an error via `HardlinkSyncResult` — generating a command with an interpreter that cannot run is exactly the failure mode this issue exists to end.

**Minimum-version floor**: declare `MIN_GLOBAL_PYTHON = (3, 9)` in one place. It is the version of `/usr/bin/python3` on macOS and therefore the worst case a global hook can land on. Global scripts are held to it by test, not by convention.

**The migration** (new entry in `MIGRATIONS`, `scripts/update/migrations.py`): it cannot reuse `_migrate_hook_registration_manifest_ids`, which matches byte-exactly on three specific fork ids. Add a separate, idempotent pass that:
1. removes any entry in `~/.claude/settings.json` whose command references `~/.claude/hooks/` and whose resolved script is **not** declared `scope = "global"` in the manifest, *and* which carries no `# hook:` marker (unmarked → pre-manifest; the marked path is already converged);
2. leaves every entry that does not reference `~/.claude/hooks/` completely untouched — including `bash .../calendar_prompt_hook.sh` and any hand-added user hook.

Take the same timestamped `.bak` + JSON-validity guard the existing migration uses.

**The orphaned-file prune is DEFERRED and is not part of this change.** An earlier draft added a
step 2 that deleted files under `~/.claude/hooks/` gated on a "no surviving command references
it" check. That check is unsound and the operation is out of scope, on two independent grounds:

- **Unsound.** The gate is a two-category allowlist (declared global scripts + a hardcoded
  `sdlc/` sibling-import carve-out + command-string references). A file reached only via a
  Python `import` — never named in a command string — is invisible to it and gets deleted.
  "Nothing that anything still points at can be removed" holds only for *command-string*
  references, which is a strictly smaller set than *all* references. Closing the gap properly
  means a transitive AST import walk, which is a third risk class again.
- **Out of scope.** Deleting files from a user's home directory, fleet-wide, is a different
  risk class from fixing an interpreter token, and this plan's own Risk 2 rated the failure
  mode "worse than the bug being fixed."

Step 1 above is fully independent of the prune and is the part that actually fixes the
misfiring guard: it removes the unmarked `~/.claude/settings.json` entry for
`validate_no_raw_redis_delete.py`. Once that registration is gone the orphaned files are inert
— stale bytes on disk, not executing code. Cleaning them up is tracked as **issue #2521** and must be designed with a **static allowlist** of the trees spike-3
enumerated, so an unrecognized file is kept by default rather than deleted by default.

**`_legacy_fork_command_prefix()` keeps its bare `python`.** It is a match key against bytes already on disk, not generated output. Changing it would silently orphan the three fork entries on every machine that has not yet run `/update`. This is the one place in the codebase where the string `python ` must survive, and the new bare-`python` ban test must exempt it explicitly.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `sdlc_context.py:45` wraps its one repo-module import in `except Exception: pass` — spike-2 confirmed this is function-local and cannot cause a module-import failure. Add a test asserting the global scripts still exit 0 under `MIN_GLOBAL_PYTHON` with that import unavailable (stripped env guarantees it).
- [ ] The global interpreter resolver must not swallow a total probe failure: assert it records an error on `HardlinkSyncResult` (observable state change) rather than emitting a command with an unusable interpreter.
- [ ] The shim's final branch is the exception path: assert it writes to **stderr** and exits **0**, and that stdout stays empty (a hook's stdout is parsed as JSON — stray output breaks the parser).

### Empty/Invalid Input Handling
- [ ] Shim with `CLAUDE_PROJECT_DIR` unset → falls back to `$PWD`; with it set to a non-git, non-venv directory → the fail-open branch. Both covered by test.
- [ ] Migration against a `~/.claude/settings.json` that is absent, empty `{}`, or has no `hooks` key → no-op, no crash, no file written.
- [ ] Migration run twice → second run is a no-op (idempotence is a registry requirement, not just a nicety).
- [ ] Global scripts receive `{}` on stdin in the `env -i` execution test — the empty-payload path, which is what a malformed hook event actually delivers.

### Error State Rendering
- [ ] The user-visible symptom being fixed *is* an error-rendering path: assert that a generated command run under `env -i /bin/sh` produces **no** `command not found` on stderr. This is the regression test for the whole issue.
- [ ] Assert the shim's failure message names the directory it searched, so a future occurrence is diagnosable from the one stderr line Claude Code surfaces.

## Test Impact

- [ ] `tests/unit/test_update_hardlinks.py:251` — UPDATE: expected command becomes `"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python "$CLAUDE_PROJECT_DIR"/.claude/hooks/pre_tool_use.py || true`
- [ ] `tests/unit/test_update_hardlinks.py:738-739` — UPDATE: both expected commands take the shim prefix
- [ ] `tests/unit/test_update_hardlinks.py:675` (`test_generate_project_hooks_regen_matches_currently_committed_file`) — no code change, but it **fails until `.claude/settings.json` is regenerated in the same commit**. Treat a failure here as the guard working.
- [ ] `tests/unit/test_hook_migration.py:268` — UPDATE: this string synthesizes generator output ("fresh copy a regular sync appended"), so it moves to the resolved global interpreter
- [ ] `tests/unit/test_hook_migration.py:58`, `:70`, `:82` — **DO NOT CHANGE**: deployed-reality fixtures that mirror `migrations.py:487`'s exact-match literal. Changing them would make the test pass while the migration silently stopped matching real machines.
- [ ] `tests/unit/test_hook_migration.py:106` — **DO NOT CHANGE**: `python /opt/my-own-tool/guard.py`, a foreign hook that must survive untouched; it is also the fixture proving the new sweep does not over-reach.
- [ ] `tests/unit/test_hooks_audit.py` (10 existing fixtures) — no change to the existing fixtures: `scripts/update/hooks.py` audits `SKILL.md` frontmatter, never `settings.json`. ADD one fixture covering the new shim fail-open finding (a `hooks.log` containing `hook_python: no repo venv interpreter found` produces its own dedicated `findings` entry)
- [ ] `tests/unit/test_hook_migration.py` — ADD: a fixture placing an unrecognized file under a fake `~/.claude/hooks/` and asserting the sweep leaves it on disk (registration-only proof)
- [ ] `tests/unit/test_hook_interpreter.py` — CREATE: the five new regression tests (a-e), including (e) the shim worktree-precedence test with both has-local-venv and no-local-venv fixtures

## Rabbit Holes

- **Porting all 35 hook scripts to run under 3.9.** Under 3.9, 27 of 35 fail, and five use `datetime.UTC` which `from __future__ import annotations` cannot fix. This is irrelevant — those are all project-scope and run under the venv. Only the 4 globally-deployed files need the 3.9 floor, and 3 already meet it.
- **Making hooks fail-closed.** Both bugs here are fail-open, which is tempting to "fix" while nearby. It is a distinct behavioral decision affecting every hook on every tool call, and getting it wrong wedges the fleet. Out of scope.
- **Symlinking `.venv` into every worktree.** Superficially simpler than a shim, but it retro-fits 50+ existing worktrees, fights `validate_no_uv_sync_in_worktree.py`, and puts a path-anchored venv behind a symlink. The shim is one committed file with no filesystem side effects.
- **Switching hooks to exec form (`"args": []`).** Would sidestep shell quoting entirely, but changes the shape of all 17 project entries and breaks the `|| true` guard, which is shell syntax. Not this change.
- **A generic "rewrite any legacy bare-python command" migration.** Tempting given the exact-match brittleness at `migrations.py:487`, but a substring-matching sweep over arbitrary user hooks is how you delete someone's tooling. The new sweep is scoped to commands referencing `~/.claude/hooks/` and to unmarked entries only.
- **Pruning the orphaned `~/.claude/hooks/` file trees in this change.** Deferred to **issue #2521**: the "no command references it" gate misses import-only files, and fleet-wide home-directory deletion is a separate risk class. Removing the registration is sufficient to fix the misfiring guard.
- **A transitive AST import walk to make the file prune safe.** The correct-looking escalation once the reference gate is shown unsound — and a third risk class on top of the second. If the prune ships later, it ships as a static keep-by-default allowlist instead.

## Risks

### Risk 1: The regenerated `.claude/settings.json` is committed with a token that is wrong on another machine
**Impact:** Every project hook breaks on every other fleet machine — the exact fleet-wide failure mode that made `python3` the wrong answer.
**Mitigation:** The project token contains no machine-specific text: `$CLAUDE_PROJECT_DIR` is supplied by Claude Code and the shim is a tracked repo file. The verification table asserts the committed file contains no absolute `/Users/` path.

### Risk 2: The legacy sweep unregisters a hook someone actually relies on
**Impact:** Silent loss of a user's own tooling — worse than the bug being fixed.
**Mitigation:** The sweep **deletes no files** — the file prune that carried the real blast radius is deferred out of this change (see Technical Approach). What remains is a registration edit gated on two independent conditions: the command references `~/.claude/hooks/`, **and** the entry carries no `# hook:` marker. `test_hook_migration.py:106`'s foreign `python /opt/my-own-tool/guard.py` fixture is the standing proof of non-over-reach, and remains unchanged. Timestamped `.bak` + JSON-validity check before write, so a bad outcome is recoverable rather than terminal.

### Risk 3: `/usr/bin/python3` is a Command Line Tools stub on a fleet machine
**Impact:** Global hooks stay broken there, in a new and more confusing way.
**Mitigation:** The resolver probes by **executing** `-V`, not by `os.path.exists`, and falls through to the Homebrew candidates. Total failure records an error on `HardlinkSyncResult` instead of emitting a dead command.

### Risk 4: A future global script quietly adopts 3.10+ syntax
**Impact:** Silent regression to exactly this bug, and — because the crash is at import time — the script's own fail-open guard will not catch it.
**Mitigation:** The always-on AST test walks every declared global script for `BinOp(BitOr)` in annotation position without a `__future__` import, and for 3.11+ runtime symbols. It runs without needing `/usr/bin/python3` present, so it cannot be skipped away in CI.

### Risk 5: The shim's fail-open branch is invisible to operations
**Impact:** If the main checkout is relocated, renamed, or its `.venv` removed, every project hook silently stops running — the shim exits 0 and emits one stderr line on whichever tool call happened to trigger it. That is indistinguishable from ordinary hook-error noise, so the machine looks healthy while all 17 project guards are inert. This is the *same* silent-disablement class the whole issue is about, merely relocated.
**Mitigation:** Extend `reflections/audits/hooks_audit.py::_hooks_audit_for_project`'s existing `extract_structured_errors(hooks_log)` pass (lines 172-189) with a substring check for `"hook_python: no repo venv interpreter found"` in recent messages, appending a **dedicated** `findings` entry alongside the existing aggregate hook-error-count line. The message string is asserted verbatim by the shim's fail-open test, so the audit and the shim cannot drift apart silently.

### Risk 6: The transient duplicate window during `/update`
**Impact:** `sync_claude_dirs` (Step 1.5) runs before migrations (Step 3.6), so mid-run a machine can briefly hold both a legacy and a new entry, and Claude Code dedups only on exact string match.
**Mitigation:** Already handled — pass 2 of `_migrate_hook_registration_manifest_ids` collapses duplicate marked entries per id, and the new sweep runs in the same Step 3.6. The idempotence test asserts a double `/update` leaves zero duplicates.

## Race Conditions

### Race 1: Concurrent `/update` runs writing `~/.claude/settings.json`
**Location:** `scripts/update/hardlinks.py::_merge_hook_settings` and the new migration in `scripts/update/migrations.py`
**Trigger:** Two `/update` invocations (or an `/update` racing a hand edit) read-modify-write the same JSON file; the later full-file write wins and silently discards the other's changes.
**Data prerequisite:** The manifest must be fully parsed before any write — `load_hook_manifest` is already fail-closed, so a partial read raises rather than generating an empty block.
**State prerequisite:** The migration must observe the post-`sync_claude_dirs` state; this is guaranteed by run.py's Step 1.5 → Step 3.6 ordering and documented at `migrations.py:471-475`.
**Mitigation:** Not newly introduced by this change and not newly mitigated — the existing read-modify-write pattern is preserved as-is. Practically bounded: `/update` is operator-invoked and single-run per machine. The new migration adds the same timestamped `.bak` the existing one takes, so a clobbered file is recoverable. Widening this to real file locking is a No-Go.

### Race 2: Shim resolution during a venv rebuild
**Location:** `.claude/hooks/hook_python`
**Trigger:** A hook fires while `uv sync` is mid-rebuild of `.venv`; `[ -x .venv/bin/python ]` can be true while site-packages is inconsistent.
**Data prerequisite:** none — the shim only needs an executable interpreter.
**State prerequisite:** none.
**Mitigation:** Accepted, not mitigated. The window is seconds, the failure is a hook error line, and both hooks affected are fail-open. Guarding it would mean the shim reasoning about venv consistency, which is well past its job.

## No-Gos (Out of Scope)

- [EXTERNAL] Running `/update` on the other fleet machines to deploy the regenerated `~/.claude/settings.json` and the new hardlinks. Each machine must run it locally; the agent cannot reach them.
- [EXTERNAL] The issue's manual-verification criterion — opening a Claude Code session in a repo other than `~/src/ai`, confirming no hook error appears on a `Bash` call, and confirming the commit-message guard actually blocks a code commit to `main` there. This needs a live interactive session in another repo; only a human can drive it.
- [DEFERRED] Pruning the orphaned pre-manifest files under `~/.claude/hooks/` (the `validators/`, `dispatch/`, `hook_utils/` trees, 8 top-level scripts, a stray `manifest.toml`). Split to **issue #2521**: the reference-check gate is unsound for import-only files, and fleet-wide home-directory deletion is a different risk class from an interpreter-token fix. Once this change removes the registration entry, those files are inert. The follow-up must use a **static allowlist** (keep-by-default), not a negative reference test.
- [EXTERNAL] Deciding whether hooks should become fail-closed (exit 2 on interpreter failure). A behavioral policy call for the owner, not a code cleanup — and a wrong answer wedges every tool call fleet-wide.
- [EXTERNAL] Adding real file locking around `~/.claude/settings.json` writes (Race 1). Pre-existing, operator-bounded, and a locking design is its own piece of work.

## Update System

This change **is** an update-system change — every effect reaches a machine through `/update`.

- `scripts/update/hardlinks.py` changes: `_build_hook_command` gains an `interpreter` parameter, the two generators supply their scope's token, and `sync_user_hooks` resolves the global interpreter by probe.
- `scripts/update/migrations.py` gains one new migration function, **registered in the `MIGRATIONS` dict** (`run_pending_migrations()` iterates that dict — an unregistered function never runs). Idempotent, recorded once in `data/migrations_completed.json`.
- New propagated file: `.claude/hooks/hook_python` (mode 755). It is project-scope only and is **not** hardlinked to `~/.claude/hooks/`; git preserves the executable bit, so no `chmod` step is needed in `/update`.
- No new dependencies, no new config files, no new secrets.
- Migration path for existing installations: running `/update` once regenerates both `settings.json` files and runs the sweep. Machines that have never run the #2453 migration still get correct behavior — the two migrations are independent and the sweep only touches unmarked entries. The sweep edits registrations only; it writes no files under `~/.claude/hooks/` and deletes none.
- No Popoto model changes, so no schema migration is required.

## Agent Integration

No agent integration required — this is entirely build/deploy tooling.

- No new CLI entry point in `pyproject.toml [project.scripts]`. The shim is invoked by Claude Code's hook runner, never by the agent's Bash tool.
- The bridge (`bridge/telegram_bridge.py`) does not import or call any of this. No MCP surface changes.
- The one agent-visible effect is indirect and desirable: the commit-message and raw-Redis guards begin working in foreign-repo sessions, so an agent that tries to commit code to `main` in another repo will now actually be blocked. That behavior is covered by the `env -i` execution test rather than by an agent-integration test.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/hook-manifest.md` with a new **Interpreter contract** section: there is currently **no** sentence documenting the leading interpreter token at all. Cover the two-tier decision (project → `hook_python` shim, global → resolved absolute system `python3`), why the scopes differ, the `MIN_GLOBAL_PYTHON` floor and what it obliges of `.claude/hooks/sdlc/` authors, and the `env -i` verification recipe.
- [ ] Sharpen `docs/features/hook-manifest.md:112` to state that the legacy match literal in `migrations.py` keeps its bare `python` while generated commands use the new tokens — the one place the old string legitimately survives.
- [ ] `docs/features/README.md` already indexes `hook-manifest.md`; confirm the one-line description still reads true and update it if the interpreter contract changes its scope.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Header comment in `.claude/hooks/hook_python` explaining why it exists (hooks run under a stripped `/bin/sh`; worktrees have no `.venv`) with a pointer to issue #2503.
- [ ] Docstring on the global interpreter resolver stating why it probes by execution rather than `os.path.exists` (the macOS Command Line Tools stub).
- [ ] Comment at `_legacy_fork_command_prefix()` reinforcing that its bare `python` is a match key against bytes on disk and must never be updated to track the generator.
- [ ] Comment in `.claude/hooks/manifest.toml`'s global-scope section recording the `MIN_GLOBAL_PYTHON` obligation for anyone adding a fourth global hook.

## Success Criteria

- [ ] Every command generated by `_build_hook_command`, in both scopes, executes successfully under `env -i /bin/sh` with no inherited `PATH`
- [ ] A project-scope hook command resolves and runs correctly from a `.worktrees/{slug}/` checkout, not just the main checkout — **guarded by an always-on test, not a manual check**
- [ ] From a worktree that carries its own `.venv`, the shim resolves the **main checkout's** interpreter, not the local one (9 of 57 worktrees are in this state today)
- [x] The shim's fail-open branch produces a dedicated finding in `reflections/audits/hooks_audit.py`, distinguishable from generic hook-error noise
- [ ] All four globally-deployed scripts (`sdlc_context.py` + the 3 declared global hooks) run to exit 0 under `MIN_GLOBAL_PYTHON` with `{}` on stdin and a stripped environment
- [ ] A regression test asserts no generated hook command begins with a bare `python` token, exempting `migrations.py`'s legacy match literal explicitly
- [ ] An always-on test asserts every global-scope script parses free of pre-3.10 annotation syntax and 3.11+ runtime symbols, without depending on `/usr/bin/python3` being present
- [ ] `tests/unit/test_update_hardlinks.py` and `tests/unit/test_hook_migration.py` are updated, and `.claude/settings.json` is regenerated in the same commit so the byte-identity test at `:675` passes
- [ ] Running `/update` twice produces no duplicate hook entries in either `settings.json`
- [ ] `scripts/update/hooks.py`'s `uv run` audit passes against the regenerated settings; no `uv run` is introduced anywhere
- [ ] The unmarked legacy `validate_no_raw_redis_delete.py` entry is gone from `~/.claude/settings.json`, and the foreign-hook fixture at `test_hook_migration.py:106` still survives the sweep
- [ ] The migration deletes **no files** — no `os.remove` / `unlink` / `rmtree` against `~/.claude/hooks/` appears in the diff, and an unrecognized file under that directory survives the sweep
- [ ] The deferred orphaned-file prune is tracked at issue #2521
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (generator)**
  - Name: `interpreter-builder`
  - Role: `_build_hook_command` interpreter threading, the `hook_python` shim, the global resolver, the one-line minimum-interpreter compat fix, and regenerating `.claude/settings.json`
  - Agent Type: builder
  - Resume: true

- **Builder (migration)**
  - Name: `migration-builder`
  - Role: the legacy-sweep migration and its registration
  - Agent Type: builder
  - Domain: settings-json — idempotent, `.bak`-guarded, registration-only (deletes no files)
  - Resume: true

- **Test engineer**
  - Name: `hook-test-engineer`
  - Role: the four new regression tests plus updates to the two existing test files
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `hook-documentarian`
  - Role: the interpreter contract in `docs/features/hook-manifest.md`
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `hook-validator`
  - Role: verifies every Success Criterion and runs the Verification table
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Interpreter threading, shim, and global resolver
- **Task ID**: build-interpreter
- **Depends On**: none
- **Validates**: tests/unit/test_update_hardlinks.py, tests/unit/test_hook_interpreter.py (create)
- **Informed By**: spike-1 as **corrected during critique** (9 of 57 worktrees carry an unmanaged, version-divergent `.venv` — precedence must be main-checkout-first), spike-1b (`git rev-parse --git-common-dir` recovers the main checkout under `env -i`), spike-2 (reject the venv path for global scope), spike-4 (the shim must be extensionless because of `reflections/audits/hooks_audit.py:91-99`)
- **Assigned To**: interpreter-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/hook_python` (mode 755, `#!/bin/sh`) exactly as specified in Technical Approach; keep it extensionless, use `exec`, fail open to stderr with exit 0
- **Order the branches main-checkout-first**: resolve via `git rev-parse --path-format=absolute --git-common-dir` before ever considering `$root/.venv/bin/python`. A local-first order silently execs a stale interpreter in the 9 worktrees that carry their own venv. Keep the precedence comment in the shim header so the order is not "cleaned up" later
- Add `PROJECT_HOOK_INTERPRETER` and a global-interpreter resolver to `scripts/update/hardlinks.py`; probe candidates by **executing** `-V` under a stripped env, in the order `/usr/bin/python3`, `/usr/local/bin/python3`, `/opt/homebrew/bin/python3`
- Change `_build_hook_command` to take an explicit `interpreter` argument; do **not** add an `interpreter` field to `HookDeclaration`
- Have `generate_project_hooks` pass the shim token and `_merge_hook_settings` pass the resolved global path; on total probe failure record an error on `HardlinkSyncResult` rather than emitting a command
- Declare `MIN_GLOBAL_PYTHON = (3, 9)` in one place for the tests to import
- Regenerate `.claude/settings.json` and commit it in the same change (`test_update_hardlinks.py:675` asserts byte-identity)
- **Minimum-interpreter compat fix (folded in from the former `build-compat` task):** add `from __future__ import annotations` to `.claude/hooks/sdlc/validate_commit_message_sdlc.py`. Do **not** touch the other three global files — they already run clean under 3.9.6. Do **not** add the import to `validators/validate_no_raw_redis_delete.py`: task 2 removes its global registration, after which it only ever runs under the venv
- Verify by hand: `echo '{}' | env -i CLAUDE_PROJECT_DIR=<a worktree path> /bin/sh -c '<generated command>'`
- Verify the compat fix: `echo '{}' | env -i /usr/bin/python3 .claude/hooks/sdlc/validate_commit_message_sdlc.py` exits 0

### 2. Legacy sweep migration
- **Task ID**: build-migration
- **Depends On**: none
- **Validates**: tests/unit/test_hook_migration.py
- **Informed By**: spike-3 (unmarked pre-manifest entry, structurally unreachable by the marker-keyed path), spike-4 (`_legacy_fork_command_prefix` keeps bare `python`; run.py Step 1.5 precedes Step 3.6), critique BLOCKER 2 (the file prune is deferred — registration edits only)
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Domain**: redis-data — idempotent, `.bak`-guarded, registration-only
- **Parallel**: true
- Add a new migration to `scripts/update/migrations.py` implementing the **two-step registration sweep** in Technical Approach, and **register it in the `MIGRATIONS` dict**
- Gate removals on both conditions: the command references `~/.claude/hooks/` **and** the entry carries no `# hook:` marker
- **Delete no files.** This migration touches `~/.claude/settings.json` only. Any `os.remove` / `unlink` / `shutil.rmtree` against `~/.claude/hooks/` is out of scope and must not appear in the diff
- Take a timestamped `.bak` and validate JSON before writing, matching the existing migration
- Leave `_legacy_fork_command_prefix()` and `_LEGACY_FORK_HOOKS` untouched
- Verify idempotence by running the migration twice against a fixture and asserting the second run is a no-op

### 3. Shim observability in the hooks audit
- **Task ID**: build-audit-signal
- **Depends On**: build-interpreter
- **Validates**: tests/unit/test_hooks_audit.py
- **Informed By**: critique NIT 1 (the fail-open branch is indistinguishable from generic hook-error noise), Risk 5
- **Assigned To**: interpreter-builder
- **Agent Type**: builder
- **Parallel**: false
- In `reflections/audits/hooks_audit.py::_hooks_audit_for_project`, extend the existing `extract_structured_errors(hooks_log)` pass (lines 172-189) with a substring check for `"hook_python: no repo venv interpreter found"` across recent messages
- Append a **dedicated** `findings` entry when matched, surfaced the same way the existing `"{n} hook error(s) in last 24h from: ..."` line is — do not fold it into that count
- Keep the message literal identical to the shim's stderr string; the shim's fail-open test asserts the same literal, so drift is caught

### 4. Regression tests
- **Task ID**: build-tests
- **Depends On**: build-interpreter, build-migration, build-audit-signal
- **Validates**: tests/unit/test_hook_interpreter.py, tests/unit/test_update_hardlinks.py, tests/unit/test_hook_migration.py
- **Informed By**: spike-4 (exact file:line list of what must and must not change)
- **Assigned To**: hook-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_hook_interpreter.py` with: (a) no generated command starts with a bare `python` token, exempting `migrations.py`'s legacy literal; (b) an always-on AST walk over every declared global script for `BinOp(BitOr)` in annotation position without a `__future__` import, and for 3.11+ runtime symbols; (c) a real `env -i` execution of each global script under `MIN_GLOBAL_PYTHON` with `{}` on stdin, `skipif`-guarded on the interpreter being present; (d) double-`/update` idempotence against a fixture
- **(e) Shim worktree-resolution regression test — the standing guard for Success Criterion 2.** Build a `tmp_path` fixture: a `git init`-ed "main checkout" containing a `.venv/bin/python` **stub** (a `#!/bin/sh` script echoing a `MAIN` sentinel), then `git worktree add` a child. Run `subprocess.run(["/bin/sh", "-c", "<repo>/.claude/hooks/hook_python -V"], env={"CLAUDE_PROJECT_DIR": <dir>}, cwd=<dir>)` and assert the sentinel, for **three** cases:
  1. worktree with **no** local `.venv` → resolves `MAIN` (the `git-common-dir` recovery branch)
  2. worktree **with** its own `.venv/bin/python` stub echoing a `LOCAL` sentinel → still resolves `MAIN`. This pins the precedence decision from spike-1; the branch is live on 9 of 57 real worktrees today
  3. main checkout itself → resolves `MAIN`
- Do **not** `skipif` item (e) on interpreter presence — the stubs remove that dependency, so it runs under `./scripts/pytest-clean.sh` on every machine. This is spike-1b's manual prototype promoted to CI; without it the shim's novel logic has no regression guard.
- Update `tests/unit/test_update_hardlinks.py:251` and `:738-739` to the shim token
- Update `tests/unit/test_hook_migration.py:268` to the resolved global interpreter
- Leave `test_hook_migration.py:58`, `:70`, `:82`, `:106` **unchanged** — they are deployed-reality and foreign-hook fixtures
- Add a `test_hook_migration.py` fixture placing an **unrecognized file** under a fake `~/.claude/hooks/` and asserting the sweep leaves it on disk — the standing proof that the migration is registration-only
- Assert the shim writes its failure message to stderr with **empty stdout** and exit 0

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: hook-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Add the **Interpreter contract** section to `docs/features/hook-manifest.md`
- Sharpen `docs/features/hook-manifest.md:112` on the legacy literal
- Confirm the `docs/features/README.md` index entry still reads true

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-interpreter, build-migration, build-audit-signal, build-tests, document-feature
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every command in the Verification table and report each result
- Confirm every Success Criterion, including the worktree case and the double-`/update` idempotence check

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `./scripts/pytest-clean.sh tests/unit/test_hook_interpreter.py tests/unit/test_update_hardlinks.py tests/unit/test_hook_migration.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No bare `python` in generated project commands | `grep -c '"command": "python ' .claude/settings.json` | match count == 0 |
| No machine-specific path committed | `grep -c '/Users/' .claude/settings.json` | match count == 0 |
| Shim exists and is executable | `test -x .claude/hooks/hook_python` | exit code 0 |
| Shim resolves the venv from a worktree | `env -i CLAUDE_PROJECT_DIR="$PWD/.worktrees/_merge_baseline_main" /bin/sh -c '.claude/hooks/hook_python -V'` | output contains `Python 3.` |
| Shim prefers the MAIN venv from a worktree that has its own | `env -i CLAUDE_PROJECT_DIR="$PWD/.worktrees/sdlc-1249" /bin/sh -c ".claude/hooks/hook_python -c 'import sys;print(sys.prefix)'"` | output is the **main checkout** `.venv`, not `.worktrees/sdlc-1249/.venv` |
| Migration deletes no files (anti-criterion) | `grep -cE 'os\.remove\|\.unlink\(\|rmtree' scripts/update/migrations.py` | no new occurrence targeting `~/.claude/hooks/` |
| Shim fail-open emits an auditable record | `env -i CLAUDE_PROJECT_DIR=/tmp/nonrepo /bin/sh -c '.claude/hooks/hook_python -V'; cat /tmp/nonrepo/logs/hooks.log` | exit code 0, empty stdout, and a line matching `<ts> - hook_python - ERROR - hook_python: no repo venv interpreter found under /tmp/nonrepo` |
| Shim fail-open is audited | `grep -c 'no repo venv interpreter found' reflections/audits/hooks_audit.py` | output is 2 (the `shim_marker` literal and the finding text) |
| Global script runs under the minimum interpreter | `echo '{}' \| env -i /usr/bin/python3 .claude/hooks/sdlc/validate_commit_message_sdlc.py` | exit code 0 |
| `__future__` import present in the fixed script | `grep -c 'from __future__ import annotations' .claude/hooks/sdlc/validate_commit_message_sdlc.py` | output contains 1 |
| No `uv run` reintroduced (anti-criterion) | `grep -rc 'uv run' .claude/settings.json` | match count == 0 |
| `_SDLC_HOOK_DEFS` not reintroduced (anti-criterion) | `grep -c '_SDLC_HOOK_DEFS' scripts/update/hardlinks.py` | match count == 0 |
| Legacy match literal preserved | `grep -c 'f"python {hooks_root}/{legacy_script}"' scripts/update/migrations.py` | output contains 1 |
| New migration registered | `python -c "from scripts.update.migrations import MIGRATIONS; print(len([k for k in MIGRATIONS if 'hook' in k]))"` | output > 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Adversary) | The legacy sweep's gate is a directory-prefix test ("command references `~/.claude/hooks/`" + no `# hook:` marker + script not declared `scope = "global"`), not an identity test against the one entry spike-3 actually found. A hand-added user hook placed *inside* `~/.claude/hooks/` matches all three conditions and gets silently deregistered on the next `/update`, contradicting Risk 2's stated invariant. The `test_hook_migration.py:106` fixture cited as proof lives at `/opt/my-own-tool/guard.py` — entirely outside the directory — so it does not cover this case. | addressed | Add a `_LEGACY_UNMARKED_SWEEP_TARGETS` tuple (e.g. `("validators/validate_no_raw_redis_delete.py",)`) next to `_LEGACY_FORK_HOOKS` in `scripts/update/migrations.py` and require the resolved script path to be in that tuple *in addition to* the existing two conditions — mirroring `_LEGACY_FORK_HOOKS`'s precise-match discipline. Add a `test_hook_migration.py` fixture for an unmarked entry under `~/.claude/hooks/` whose script is NOT in the sweep-target tuple, asserting it survives; this is a materially different case from the existing `/opt/` fixture and is currently untested. |
| CONCERN | Scope & Value (Simplifier) + History & Consistency (Consistency Auditor) | Stale scope label surviving the BLOCKER-2 revision. Every operative description of the migration now says registration-only (Solution > Key Elements: "Registration-only — it deletes no files"; task 2: "**Delete no files.**"; the new anti-criterion), but Team Orchestration > Builder (migration) still reads `Domain: redis-data (idempotent, reference-gated, .bak-guarded destructive sweep)` — verbatim the retired file-prune design. The plan asserts two contradictory scopes for the same task, and the builder agent reads Team Orchestration first. | addressed | The literal string "destructive sweep" appears exactly once in the plan (verified by grep), at the Team Orchestration Domain line. Rewrite it to match task 2's revised Domain line so a byte-diff of the two Domain strings for `migration-builder`/`build-migration` is zero, e.g. `Domain: settings-json — idempotent, .bak-guarded, registration-only (deletes no files)`. If any domain-gating harness reads this field, a stale "destructive" tag also over-gates a change deliberately de-risked to a JSON-only edit. |
| NIT | Scope & Value (Simplifier) | Task 3 (`build-audit-signal`) is the same shape of addition that NIT 2 of the prior pass just folded away — one substring check plus one test fixture, same agent as task 1 (`interpreter-builder`), `Depends On: build-interpreter`, `Parallel: false` — but was left as a separate numbered task rather than folded in. | pending | n/a (NIT) |
| NIT | History & Consistency (Consistency Auditor) | The Verification row "Shim resolves the venv from a worktree" targets `.worktrees/_merge_baseline_main`, an ephemeral merge-queue worktree not among spike-1's enumerated set. If it is absent at verification time the shim takes its fail-open branch and the row's `Python 3.` expectation cannot hold. (It exists today, so this is latent, not currently broken.) | pending | n/a (NIT) |

---

### Prior pass (all rows verified closed by this re-critique)

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness (Skeptic) + History & Consistency (Consistency Auditor) | spike-1's high-confidence claim "not one has a `.venv`" is factually false. The critique driver re-probed under `env -i`: 9 of 57 worktrees carry an EXECUTABLE `.venv/bin/python` (`fix-test-isolation-cluster`, `headless-runner-zombie-liveness`, `hybrid-retrieval-eval`, `nightly-triage-02917b62`, `nightly-triage-40d7a187`, `no-progress-tui-liveness-nudge-rung`, `sdlc_1535_pipeline_portability`, `sdlc-1249`, `sdlc-1997`), with measured version divergence (main = 3.14.3; `nightly-triage-02917b62` = 3.13.2; both `import popoto` cleanly). The shim's FIRST branch is `[ -x "$root/.venv/bin/python" ]`, so in those 9 worktrees it silently execs a stale, version-divergent interpreter and never reaches the `git rev-parse --git-common-dir` fallback. The plan's Risks and Race Conditions omit this path entirely because the premise declared it unreachable. This repeats the plan's own stated root-cause pattern: reasoning by inspection instead of executing. | addressed | Correct spike-1 to "not all worktrees have a `.venv` — 9 of 57 do, with divergent versions" and make the precedence rule an explicit, stated decision rather than an accident of line order in `.claude/hooks/hook_python`. Two viable shapes: (a) keep local-first but validate the local venv is the main checkout's (compare `sys.prefix`, or require a stamp file the repo writes); (b) invert — always resolve `main=$(dirname "$(git -C "$root" rev-parse --path-format=absolute --git-common-dir)")` first, and use `$root/.venv/bin/python` only when `[ "$main" = "$root" ]`. Option (b) is the smaller diff and removes the untrusted-venv branch outright. Either way `tests/unit/test_hook_interpreter.py` must seed a fixture worktree carrying its own `.venv/bin/python` stub and assert which interpreter the shim picks — the branch is live in production today, so it must be pinned by test, not assumed absent. |
| BLOCKER | Risk & Robustness (Adversary) + Scope & Value (Simplifier) | The legacy-sweep migration's step 2 (delete files under `~/.claude/hooks/`) is both unsound and out of scope. Unsound: its keep-set is a two-category allowlist (declared global scripts + a hardcoded `sdlc/` sibling-import carve-out + command-string references), so a hand-added user file reached only via a Python `import` — not invoked as a command — is invisible and gets deleted. That directly falsifies Risk 2's mitigation claim "nothing that anything still points at can be removed," which holds only for command-string references. Out of scope: deleting files from a user's home directory fleet-wide is a different risk class from fixing an interpreter token, and the plan's own Risk 2 rates the failure "worse than the bug being fixed." Open Question 2 raises exactly this and defaults to shipping anyway. | addressed | Step 1 of the migration (remove the unmarked `~/.claude/settings.json` entry for `validate_no_raw_redis_delete.py`, gated on: command references `~/.claude/hooks/` AND carries no `# hook:` marker) is fully independent of step 2 and is the part that actually fixes the misfiring guard — keep it with its `.bak` + JSON-validity guard untouched. For step 2 pick one: (a) DEFER the file prune to a follow-up issue and drop the bullet "prunes orphaned files under `~/.claude/hooks/`" from task 3 (`build-migration`) and from Solution > Key Elements; or (b) if it must ship, replace the general negative-reference test with a STATIC allowlist of exactly the trees spike-3 enumerated (`validators/`, `dispatch/`, `hook_utils/`, the 8 named top-level scripts, the stray `manifest.toml`) so an unrecognized file is kept by default rather than deleted by default. Do not attempt a transitive AST import walk here — that is a third risk class again. Whichever is chosen, `test_hook_migration.py:106`'s foreign `python /opt/my-own-tool/guard.py` fixture must still survive, and add a fixture for an unrecognized file under `~/.claude/hooks/` asserting it is NOT deleted. |
| CONCERN | History & Consistency (Consistency Auditor) | The plan's Desired outcome promises "Regression tests fail if either property breaks," and Why Previous Fixes Failed indicts prior attempts for never executing a generated command under a stripped environment. But task 4's automated test list (items a-d) runs `env -i` only against GLOBAL-scope scripts. The `hook_python` shim's `git rev-parse --git-common-dir` worktree-recovery branch — the genuinely novel logic this issue introduces — is covered only by a manual "Verify by hand" bullet in task 1 and a one-shot Verification-table row executed by `hook-validator` in task 6. Neither runs under `./scripts/pytest-clean.sh`, so Success Criterion 2 ("A project-scope hook command resolves and runs correctly from a `.worktrees/{slug}/` checkout") has no standing regression guard. | addressed | Add a fifth item (e) to task 4's `tests/unit/test_hook_interpreter.py`: build a `tmp_path` fixture main checkout containing a `.venv/bin/python` stub (a `#!/bin/sh` script echoing a sentinel), `git init` it, `git worktree add` a child with NO local `.venv`, then run `subprocess.run(["/bin/sh", "-c", "<repo>/.claude/hooks/hook_python -V"], env={}, cwd=worktree)` with `CLAUDE_PROJECT_DIR` set to the worktree and assert the sentinel appears. This is spike-1b's manual prototype promoted to CI. It interlocks with the first BLOCKER — the same fixture harness covers both the has-local-venv and no-local-venv worktree cases. Do not `skipif` this on interpreter presence; the stub removes that dependency. |
| NIT | Risk & Robustness (Operator) | The shim's fail-open branch is visible only as a single stderr line on whichever tool call happened to trigger it. `reflections/audits/hooks_audit.py` checks `\|\| true` presence and script-path existence but never scans for this message, so a machine whose shim starts failing open (e.g. after the main checkout is relocated) produces no audit signal distinguishable from ordinary hook error noise. | addressed | Extend `reflections/audits/hooks_audit.py::_hooks_audit_for_project`'s `extract_structured_errors(hooks_log)` pass with a substring check for `"hook_python: no repo venv interpreter found"` and append a dedicated `findings` entry, surfaced the same way the existing hook-error-count line is. |
| NIT | Scope & Value (Simplifier) | Team Orchestration names six agents for a Medium-appetite plan whose smallest member, `compat-builder`, exists solely to add one `from __future__ import annotations` line to one file. | addressed | Delete the "Builder (compat)" team member and the `build-compat` task; move its single bullet plus its verify command into task 1 (`build-interpreter`). Both are `Depends On: none` / `Parallel: true`, and task 4 already depends on `build-interpreter` alongside `build-compat`, so no ordering is lost. |

---

## Open Questions

Carried into `/do-plan-critique` rather than blocking the plan. Each has a stated default
so the build is not gated on an answer.

1. **Is `/usr/bin/python3` (3.9) the right global floor, or should the resolver prefer the newest working candidate?** Default taken: pin the *first working* candidate in the order `/usr/bin/python3` → `/usr/local/bin/python3` → `/opt/homebrew/bin/python3`, which prefers the most stable over the newest. The cost is holding 4 files to 3.9, currently one line. The alternative — prefer newest — makes the effective floor differ per machine, which is the class of bug this issue is about.
2. ~~**Should the orphaned-file prune ship with this change or be split out?**~~ **RESOLVED by critique (BLOCKER 2): split out.** The reference-check gate is unsound for import-only files, and fleet-wide home-directory deletion is a different risk class from an interpreter-token fix. This change removes the *registration*; the files go in **issue #2521**, designed around a static keep-by-default allowlist.
3. **Should the shim fail open (exit 0, stderr) or fail closed (exit 2, block)?** Default taken: fail open, matching today's de-facto behavior, with the failure now visible. Fail-closed is listed as a No-Go because a wrong answer wedges every tool call fleet-wide — but it is a legitimate owner call.

<!-- Aside, unrelated to this plan and not scoped here: `tools/code_impact_finder.py`
     is currently non-functional. Its embedding pass dies with
     `openai.BadRequestError: Invalid 'input[11]': input cannot be an empty string`
     while indexing, then reports "No results (finder degraded)". The blast radius for
     this plan was mapped by hand and by spike instead. Worth its own issue. -->
