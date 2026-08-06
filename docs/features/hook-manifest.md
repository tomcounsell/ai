# Hook Registration Manifest

Single declarative source of truth for every statically-registered Claude Code hook, across both the project scope (`.claude/settings.json`) and the user scope (`~/.claude/settings.json`). Replaces two independently hand-maintained surfaces — the project `settings.json` `hooks` block and the hardcoded `_SDLC_HOOK_DEFS` Python list — with one manifest and two generators.

See `docs/plans/hook-registration-manifest-dispatcher.md` for the full design rationale, spikes, and critique history.

## Problem It Solves

Before this manifest, hook registration was hand-maintained in two places that had to stay in sync by discipline alone:

1. `.claude/settings.json`'s `hooks` block (project scope) — 23 entries hand-edited directly.
2. `scripts/update/hardlinks.py`'s `_SDLC_HOOK_DEFS` list (user/global scope) — a hardcoded Python tuple list consumed by `sync_user_hooks`.

That discipline had already failed in four measurable ways by the time this was built (see "Fixed Bugs" below), including one — a missing `|| true` guard on the user-scope Stop hook — that could block every machine's turn-end on a non-zero exit.

## The Manifest

`.claude/hooks/manifest.toml` declares every static hook as a `[[hook]]` TOML table. Field reference (see the file's own header comment for the canonical version):

| Field | Meaning |
|-------|---------|
| `manifest_id` | Stable slug — the add/update/remove identity key used by both generators. Must be unique across the whole manifest. NOT the command string (which is volatile — script renames, timeout tweaks, and matcher edits all change it). |
| `event` | Claude Code hook event name (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`, `PostCompact`). |
| `matcher` | Tool-name matcher (`""` = all tools; supports regex-alternation, e.g. `"Write|Edit"`). |
| `script` | Path to the hook script, relative to `.claude/hooks/`. |
| `timeout` | Seconds, passed through verbatim to the generated command. Hook timeouts are read by the Claude Code harness itself, not by Python — see the plan's spike-4A for why this stays manifest-owned rather than moving into `config/settings.py`'s `TimeoutSettings` catalog. |
| `scope` | `"project"` → projected into the repo's `.claude/settings.json`. `"global"` → hardlinked to `~/.claude/hooks/` and projected into `~/.claude/settings.json`. |
| `blocking` | `true` → the command is registered bare (a non-zero exit can block/signal the harness). `false` → the command is registered with a trailing `\|\| true` guard so a non-zero exit can never block the turn. |
| `args` | Optional list of extra CLI argv tokens appended to the command (e.g. `validate_file_contains.py`'s `-d`/`-e`/`--contains` flags). |

**Declaration order is the canonical ordering.** Both generators emit entries in manifest declaration order and update in place by `manifest_id` without resorting — this is load-bearing for the empty-`git diff`-on-regen guarantee (regenerating from an unchanged manifest must reproduce the existing `settings.json` byte-for-byte).

## Loader

`scripts/update/hook_manifest.py::load_hook_manifest()` parses the manifest with the stdlib `tomllib` (Python 3.11+, no third-party dependency) into a list of `HookDeclaration` dataclasses (`manifest_id`, `event`, `matcher`, `script`, `timeout`, `scope`, `blocking`, `args`).

The loader is **fail-closed**: a missing file, invalid TOML, an empty `[[hook]]` list, a missing required field, an invalid `scope`, a non-integer `timeout`, a non-boolean `blocking`, or a duplicate `manifest_id` all raise `HookManifestError` rather than returning an empty list. This matters because generation must never silently wipe an existing `settings.json` hooks block just because the manifest failed to parse.

## The Two Generators

Both live in `scripts/update/hardlinks.py` and are invoked from `sync_claude_dirs()` during `/update`.

### Project generator — `generate_project_hooks()` / `sync_project_hooks()`

Projects every `scope == "project"` manifest entry into the `event -> [{matcher, hooks: [...]}]` shape `.claude/settings.json` expects, grouping by `(event, matcher)` in first-appearance manifest order. Because this repo owns the project `settings.json` outright, `sync_project_hooks()` does a **full regeneration** each run rather than an incremental merge: the `hooks` key is replaced wholesale (all other top-level keys, e.g. `permissions`, are preserved untouched). If the newly generated block already matches what's on disk, the write is skipped.

### User generator — `sync_user_hooks()` / `_merge_hook_settings()`

Only `scope == "global"` entries — the SDLC-fork scripts that run inside foreign repos' Claude Code sessions — are synced to `~/.claude/`. `sync_user_hooks()` hardlinks each declared global script into `~/.claude/hooks/`, then calls `_merge_hook_settings()` to project entries into `~/.claude/settings.json`.

Unlike the project generator, the user scope is a file this repo does **not** own outright (other tools/projects may also write into it), so the merge is **incremental add/update/remove**, keyed on `manifest_id` rather than the exact command string:

- **Identity marker.** Every generated user-scope command carries a trailing `# hook:<manifest_id>` comment (inert to the shell that executes the command). `_extract_manifest_id()` pulls it back out via `_MANIFEST_ID_MARKER_RE`.
- **Add.** A manifest entry with no existing marked entry gets appended to the appropriate `(event, matcher)` block (creating the block if needed).
- **Update.** A manifest entry whose `manifest_id` marker is already present gets its command, timeout, and matcher updated in place if they changed — no move, no resort.
- **Remove.** Any existing marked entry whose `manifest_id` is no longer declared in the manifest is deleted; blocks and events emptied by the removal pass are pruned.
- **Never touches unmarked entries.** Any hand-added or pre-migration legacy entry with no `# hook:<id>` marker is left completely untouched — the merge only ever manages entries it (or the migration, see below) has explicitly claimed.

## Registration Is Manifest-Granular; Deployment Is Directory-Granular

The manifest is authoritative for **registration** — the `event`/`matcher`/`command` entries `_merge_hook_settings()` writes into `~/.claude/settings.json` are keyed one-to-one with `[[hook]]` declarations, and that stays true after this section's fix.

**Deployment** — which files `sync_user_hooks()` hardlinks into `~/.claude/hooks/` — is not manifest-granular; it is directory-granular. For each directory that contains at least one declared global-scope script, every `*.py` file in that directory is hardlinked, not just the declared ones. (A declaration living directly at the hooks root still deploys only its own file — globbing the root would sweep every project-scope script into the user tree.)

The two differ because a `[[hook]]` declaration names the file that *registers* a hook — the script Claude Code's harness invokes directly — not every file that script needs to run. The manifest has no vocabulary for "this script also imports a sibling module": `HookDeclaration` has no dependency field, and adding one would mean hand-listing transitive imports in TOML, which drifts the moment someone factors out a new helper.

The worked example is `.claude/hooks/sdlc/sdlc_context.py`: it is imported by the global-scope SDLC hook scripts in that directory but registers no hook of its own, so it has no `[[hook]]` entry and no `manifest_id`. It is deployed (hardlinked alongside its importers) but never registered. Treating deployment as manifest-granular — hardlinking only declared scripts — deploys the importers without the helper they need, and every one of them dies with `ModuleNotFoundError` the moment they run in a foreign repo. That regression is exactly why the directory, not the declaration, is the deployment unit; see the deployment loop's comment in `scripts/update/hardlinks.py::sync_user_hooks()` for the mechanics.

**Deletion is out of scope here.** Deployment is additive only — nothing already present under `~/.claude/hooks/` is removed by this directory-granular sync. Stale-file cleanup remains owned by `RENAMED_REMOVALS`'s `"hooks"` kind (see [Removal Propagation](#removal-propagation-renamed_removals-hooks-kind) above), which still operates at declaration granularity and is unaffected by this change.

## The User Hooks Root Must Be a Real Directory

`~/.claude/hooks` is a real user-level directory of hardlinks. On a machine still carrying the pre-migration layout it is a *directory symlink* into a checkout's `.claude/hooks/`, and under that layout there is no separate user tree at all: every path beneath it is tracked source, so a pass that enumerates the directory and removes what it reads as an orphaned user file removes the checkout. That deleted 36 tracked files during the #2521 build.

Two mechanics make the layout easy to misread, and both are worth naming:

- The same inode appears under both paths, which looks like a hardlink pair and invites the conclusion that removing the user copy leaves the repo source intact. It is one file under two names, and that conclusion is false.
- `_ensure_hardlink()` sees matching inodes and reports `exists`, so a sync through the alias registers as success while having written nothing.

`user_hooks_root_is_repo_aliased(project_dir, user_claude=None)` is the single answer to "is this a real user tree?". It returns the resolved target when `~/.claude/hooks` is a symlink or resolves to `<project_dir>/.claude/hooks`, and `None` otherwise. Every writer and deleter of that directory consults it, including the migration itself, so detection and destruction share one definition:

- `_cleanup_renamed()` skips the whole `"hooks"` kind. A renamed hook's old name has no live project source, so the inode guard finds no match and clears it for removal. This runs before the migration, so the guard is live on the production path.
- `sync_user_hooks()` either migrates the alias or declines to write, per the two branches below.

An alias to a *foreign* checkout is the sharper case, an agent in a worktree while `~/.claude/hooks` points at the main checkout. The inodes then differ, and writing through the link would unlink that checkout's tracked file and relink it to this one.

### The migration lives next to its rebuild

`sync_user_hooks()` migrates the layout, at the last possible moment before the deployment loop. The distance between the unlink and the rebuild is the hazard, not the unlink: anything that fails in between leaves `~/.claude/hooks` absent while `~/.claude/settings.json` still registers blocking global hooks against it. `_build_hook_command()` appends `|| true` only for non-blocking declarations, and `/usr/bin/python3` on a missing script exits 2, which is the PreToolUse deny code. A machine in that state denies every Bash call in every repo, including the `/update` that would repair it. So both fallible steps, the manifest load and the interpreter probe, return before the migration point, and the deployment loop follows on the next statement. `test_hooks_symlink_survives_a_manifest_that_fails_to_load` pins this: with a malformed `manifest.toml`, the symlink stands and the scripts stay reachable.

Two branches, keyed on where the alias lives:

- **`~/.claude/hooks` is itself the symlink.** `unlink()` removes the link and never its target, then the rebuild runs immediately. This mirrors the `~/.claude/skills` precedent, which has no such window only because `_sync_skills()` is its very next statement.
- **A parent directory carries the alias** (`~/.claude` itself is a symlink, so `~/.claude/hooks` is a real directory that still resolves into the repo). There is no link at the hooks root to remove and unlinking a real directory would raise, so deployment is declined and only registration runs. Registration is still correct: the scripts really are reachable at the registered paths through the alias.

Migrating at all is safe only because deployment is directory-granular. A real directory holding only the declared scripts and no `sdlc/sdlc_context.py` is the breakage described above, so migrating under per-declaration deployment would convert a working machine into a broken one. `test_sync_claude_dirs_migrates_the_hooks_symlink_and_keeps_the_helper` asserts the migrated directory carries the helper, so that dependency is pinned by test rather than by ordering discipline.

`/update` names the layout it *found*, probed before the sync rather than after (`hooks: ~/.claude/hooks is a real user directory`, or the aliased form with the resolved target). Probing after would only ever report the migrated layout, so the one run where the answer carries information is the one run that could not report it. When the run started aliased, a second line reports what the sync did with it.

## Interpreter Contract

Claude Code runs every hook command through a non-interactive `/bin/sh`: no `PATH`, no `~/.zshenv`, no shell aliases. Modern macOS ships no `/usr/bin/python`, so a generated command that starts with a bare `python` exits 127. Claude Code treats only **exit 2** as a block — a 127 is not a loud failure, it is a **silently disabled guard** that prints one error line per tool call and otherwise lets the tool through.

Both generators supply an explicit `interpreter` argument to `_build_hook_command()` (`scripts/update/hardlinks.py`) rather than hardcoding a token, because the correct answer differs by scope:

| | Project scope | Global scope |
|---|---|---|
| Registered in | `.claude/settings.json` (git-tracked) | `~/.claude/settings.json` (per-machine, generated) |
| Runs in | this repo + its `.worktrees/{slug}/` checkouts | every repo on the machine |
| Token | `"$CLAUDE_PROJECT_DIR"/.claude/hooks/hook_python` | resolved absolute system `python3` |
| Why not the other | an absolute path would be committed to git and wrong on every other machine | the repo venv path embeds a username, can be mid-rebuild, and dereferences into a Homebrew cellar directory `brew upgrade` deletes |

`HookDeclaration` deliberately gains no `interpreter` field: the interpreter is a property of *where* a hook is deployed, not of the individual hook.

### Project scope: the `hook_python` shim

`.claude/hooks/hook_python` (committed, mode 755, extensionless) resolves the repo venv interpreter at hook-execution time. Precedence is **main-checkout-first**, not local-first: it resolves the main checkout via `git -C "$root" rev-parse --path-format=absolute --git-common-dir`, and only falls back to `$root/.venv/bin/python` when git cannot answer at all. This order is a stated decision, not an implementation detail — 9 of 57 worktrees on this machine carry an unmanaged, version-divergent `.venv` (measured: main checkout 3.14.3 vs. one worktree's 3.13.2), so a local-first order would silently exec a stale interpreter from inside a worktree session. From a worktree, the main checkout's venv is the only one this repo manages, so it is the only one a hook should ever run under.

It is extensionless on purpose: `reflections/audits/hooks_audit.py` takes the first whitespace token ending in `.py`/`.sh` as the script path and requires it to exist, so a `.sh`-named shim would make the audit inspect the shim instead of the hook it wraps. It uses `exec` so no extra process lingers and the wrapped hook's exit code passes through unchanged, preserving `blocking = true` semantics.

### Global scope: resolved absolute `python3`

`scripts/update/hardlinks.py::resolve_global_interpreter()` probes `GLOBAL_INTERPRETER_CANDIDATES` (`/usr/bin/python3`, `/usr/local/bin/python3`, `/opt/homebrew/bin/python3`) in order, once per machine at generation time, and accepts the first candidate for which `subprocess.run([candidate, "-V"], env={})` **exits 0**. This probes by execution, deliberately not `os.path.exists`: on macOS, `/usr/bin/python3` exists even without the Command Line Tools installed, where it is a stub that exits non-zero and pops a GUI install dialog — a presence check would select that stub and reproduce the exact silent-breakage class this resolver exists to end. The resolved absolute path is baked into the per-machine `~/.claude/settings.json`; if no candidate runs, `sync_user_hooks()` records an error on `HardlinkSyncResult` and returns rather than emitting a command with an unusable interpreter.

Every global-scope script is held to `MIN_GLOBAL_PYTHON = (3, 9)` — the worst-case version a global hook can land on, matching `/usr/bin/python3` on macOS. Anyone adding a fourth global hook under `.claude/hooks/sdlc/` must keep its script importable and runnable under that floor: stdlib-only imports, no PEP 604 (`X | None`) annotations without `from __future__ import annotations`, and none of `datetime.UTC`, `tomllib`, `typing.Self`, `ExceptionGroup`, or `asyncio.timeout`. This is enforced by an always-on AST test, not by convention — it does not depend on `/usr/bin/python3` being present to run.

### Fail-open behavior and its audit signal

Both failure modes in this contract are fail-open by design (making hooks fail-closed is a separate behavioral decision, out of scope here). The `hook_python` shim's final branch exits **0** with stdout left empty (a hook's stdout is parsed as JSON, so stray output would break the parser), and emits one message to two destinations:

```
hook_python: no repo venv interpreter found under <dir>
```

1. **stderr**, which Claude Code surfaces on its hook-error channel, one line per tool call.
2. **`<root>/logs/hooks.log`**, appended by the shim itself in the exact format `log_hook_error` produces (`%Y-%m-%d %H:%M:%S - hook_python - ERROR - <message>`, UTC), creating `logs/` if absent. The append is guarded so it can never alter the exit code or touch stdout.

The second destination exists because this branch runs **no Python at all** — the shim is the only thing that can write the record, and `logs/hooks.log` is where the audit looks. `reflections/audits/hooks_audit.py` parses that file and raises a **dedicated finding** distinct from the generic aggregate hook-error count, so a machine whose shim has started failing open (main checkout relocated, renamed, or its `.venv` removed) is diagnosable rather than blending into ordinary hook-error noise. Both `tests/unit/test_hook_interpreter.py` and `tests/unit/test_hooks_audit.py` assert against the shim's real execution output, so the emitted record and the audit's marker cannot drift apart.

### Verification recipe

Both tokens can be exercised directly under a stripped environment:

```
env -i CLAUDE_PROJECT_DIR="$PWD" /bin/sh -c '.claude/hooks/hook_python -V'
echo '{}' | env -i /usr/bin/python3 .claude/hooks/sdlc/validate_commit_message_sdlc.py
```

The first must resolve and print a version with exit 0 from both the main checkout and any `.worktrees/{slug}/` checkout. The second is the global-scope floor check: every declared global script must exit 0 on an empty JSON payload under the oldest system interpreter.

## Per-Event Dispatcher

`.claude/hooks/dispatch/pre_tool_use_bash.py` collapses what used to be 7 separate interpreter starts — one process per PreToolUse/Bash validator — into a single process that reads the hook JSON from stdin once and calls each validator's predicate function in-process:

1. `validate_commit_message`
2. `validate_no_inline_timeout`
3. `validate_merge_guard`
4. `validate_no_raw_redis_delete`
5. `validate_no_uv_sync_in_worktree`
6. `validate_no_destructive_git_in_worktree`
7. `validate_no_destructive_git_in_shared_checkout` (#2448)
8. `validate_design_system_sync` (out-of-process — see below)

New in-process predicates are added directly to `_VALIDATORS` in `dispatch/pre_tool_use_bash.py`, not as a new standalone `manifest.toml` entry — the manifest declares exactly one `[[hook]]` for `(PreToolUse, Bash)`, pointing at this dispatcher (see `TestManifestOrderConsistency` in `tests/unit/test_pre_tool_use_dispatcher.py`).

**Outcome is first-block-wins**, in manifest declaration order: the first validator to return a block reason short-circuits the rest, and the dispatcher emits one `{"decision": "block", "reason": <that reason>}` line to stdout. If every validator passes (or fails open), it emits nothing (allow).

**Fail posture is per-validator, not blanket:**
- Every validator except `validate_merge_guard` is wrapped in its own `try/except` and **fails open** on an unexpected exception — the error is logged via `log_hook_error()` and the dispatcher moves on to the next validator. A crash in one validator must never skip the validators after it.
- `validate_merge_guard` is **fail-closed**: an exception while evaluating it produces a synthesized BLOCK decision rather than a silent allow, preserving the pre-existing fail-closed contract for merge enforcement.
- `validate_design_system_sync.py` runs out-of-process as its own subprocess (it already re-invokes `python -m tools.design_system_sync --check` internally and owns its own JSONL observability log, so folding it in-process gains nothing). A `{"decision": "block", ...}` line on its stdout is treated identically to an in-process predicate's block reason; any subprocess failure fails open.

Other events (`PreToolUse` overall, `PostToolUse`, etc.) already had a dispatcher shape from before this work (`pre_tool_use.py`/`post_tool_use.py` fan out internally by `tool_name`); this manifest project only adds the dedicated Bash-matcher dispatcher for the 7 validators above.

## Detached Stop Extraction

Not part of the manifest/generator mechanism directly, but shipped alongside it as the acute fix this plan opened with: `stop.py` no longer runs Haiku/`gh` extraction inline on the harness's 10-second Stop-hook wall. It persists the session transcript synchronously, then spawns `.claude/hooks/hook_utils/stop_detach_worker.py` as a real detached subprocess (`Popen(..., start_new_session=True)`, redirected/closed streams — never a thread, since a thread would be killed the instant `stop.py` exits) and exits 0 immediately.

Two named, env-overridable knobs govern the detached worker (both live in `.claude/hooks/hook_utils/detach_lock.py`):

| Constant | Env override | Default | Purpose |
|----------|---------------|---------|---------|
| `DEFAULT_DEADLINE_SECONDS` | `HOOK_DETACH_DEADLINE_SECONDS` | `120` | Self-deadline enforced by the worker via `SIGALRM`. The raised `DetachDeadlineExceeded` exception subclasses `BaseException` (not `Exception`) so `memory_bridge`'s broad `except Exception` handlers cannot swallow it — on deadline the worker logs `deadline-exceeded` and exits non-zero rather than lingering. |
| `DEFAULT_MAX_INFLIGHT` | `HOOK_DETACH_MAX_INFLIGHT` | `3` | Cap on concurrently in-flight detached workers, enforced by atomically-reserved numbered lock-slot files (`slot-0.lock` .. `slot-{N-1}.lock`) under an absolute, cwd-independent state dir (`~/.claude/hooks-state/stop-detach/`). Reservation is the atomic operation (`os.open(..., O_CREAT | O_EXCL)`) — no separate count-then-write TOCTOU window. Over-cap invocations log `detach-skipped: at capacity` and skip spawning rather than fanning out unbounded Haiku/`gh` workers during an SDLC batch. |

Both constants are marked provisional/tunable in code, matching the project's magic-number convention. The absolute log path (`~/.claude/logs/hooks.log`, via `get_absolute_log_path()`) and state dir are deliberately **not** repo-relative — user-scope hooks run inside foreign repos where a repo-relative `logs/` directory doesn't exist, and a silently-failing log write there would re-swallow the very drops this fix is meant to surface.

See [`docs/features/memory-hook-performance.md`](memory-hook-performance.md) for the timeout root-cause writeup and [`docs/features/claude-code-memory.md`](claude-code-memory.md) for how the detached worker fits into the memory extraction pipeline.

## Removal Propagation (`RENAMED_REMOVALS` "hooks" kind)

`scripts/update/hardlinks.py::RENAMED_REMOVALS` is the mechanism that sweeps stale user-level hardlinks and (for skills/commands) their sourcing when something is renamed or removed. It gained a `"hooks"` kind: `(kind, old_name)` tuples where `kind == "hooks"` use `old_name` as the script path relative to `.claude/hooks/` (e.g. `"sdlc/old_script.py"`). `_cleanup_renamed()`'s `src_for_kind` mapping grew a `"hooks": project_dir / ".claude" / "hooks"` entry so the same inode-guarded cleanup logic (never deletes a target still hardlinked to a live project source — protects a foreign repo that legitimately provides its own same-named user-level hook) now covers hooks the same way it already covered skills and commands.

Before this, hooks were the one synced `.claude/` surface with **no removal propagation at all** — a renamed or deleted hook script would leave both a stale hardlink under `~/.claude/hooks/` and a stale registration in `~/.claude/settings.json` behind forever.

## Migration for the 3 Deployed Legacy Entries

Every machine that had run `/update` before this manifest existed had exactly **three** legacy SDLC-fork hook entries in `~/.claude/settings.json`, written by the old hardcoded `_SDLC_HOOK_DEFS` path — with no `manifest_id` marker, no `\|\| true` guard on the Stop entry, and `sdlc_reminder.py` silently collapsed to a single `Edit`-matched block (a command-string dedupe quirk had silently dropped its intended `Write` coverage, because the `Write` and `Edit` `_SDLC_HOOK_DEFS` tuples emitted a byte-identical command string).

The new `manifest_id`-keyed merge in `_merge_hook_settings()` can't recognize these unmarked legacy entries — without a migration, the first post-manifest `/update` would append three *new* marked entries alongside the three legacy ones, duplicating every SDLC hook fleet-wide.

`scripts/update/migrations.py::_migrate_hook_registration_manifest_ids()` (registered in `MIGRATIONS`) fixes this once, idempotently:

1. Finds each of the three legacy entries by its literal pre-manifest command string, matched on command **path** (ignoring the on-disk matcher). The match key is built by `_legacy_fork_command_prefix()`, which keeps a bare `python` forever: it is matching bytes already on disk from before the interpreter contract above existed, not comparing against what the generators emit today. Generated commands use the shim/resolved-`python3` tokens described in [Interpreter Contract](#interpreter-contract); this is the one place in the codebase where the literal string `python ` legitimately survives, and the bare-`python`-ban regression test exempts it explicitly.
   - `validate_commit_message.py` — `PreToolUse`/`Bash`
   - `sdlc_reminder.py` — `PostToolUse`/`Edit` (single block; the intended `Write` coverage was never there)
   - `validate_sdlc_on_stop.py` — `Stop`/`""`
2. Rewrites each in place: embeds the `# hook:<manifest_id>` marker (so future runs update instead of appending a duplicate), applies the `\|\| true` guard the manifest's `blocking` flag calls for (restoring the long-missing guard on the Stop hook — this was Pre-requisite Bug 1), and upgrades `sdlc_reminder.py`'s matcher from `Edit` to `"Write|Edit"` (restoring the coverage the old dedupe quirk had silently dropped).
3. Collapses any accidental duplicate for the same `manifest_id` down to one entry, defending against ordering within a single `/update` invocation.
4. Writes a timestamped `.bak` snapshot of `~/.claude/settings.json` before any change, and asserts JSON validity on the serialized result before ever touching disk — an invalid result is never written.
5. Never touches any pre-existing, non-SDLC user hook.

The migration is idempotent (a settings.json where every legacy entry already carries its marker is a no-op) and recorded once in `data/migrations_completed.json` per the standard migration contract.

## Migration for Unmarked Pre-Manifest Global Entries

The migration above is **marker-keyed**: it indexes only entries whose command already carries a `# hook:<id>` marker, or that match one of the three known legacy fork command strings. That leaves a second class it is structurally unable to touch — a pre-manifest entry that is unmarked *and* not one of the three forks.

Exactly one such entry was found deployed: `validators/validate_no_raw_redis_delete.py`, registered `PreToolUse`/`Bash` in `~/.claude/settings.json` with a bare `python`. This is the manifest/reality drift the issue called out — the manifest declares that validator `scope = "project"`, and its logic already runs in-process via `dispatch/pre_tool_use_bash.py`, so the stale **global** registration was pure duplication that fired on every `Bash` call in every repo and crashed on import under the oldest system interpreter. The correct fix is removing the registration, not repairing it.

`scripts/update/migrations.py::_migrate_sweep_legacy_unmarked_global_hooks()` (registered in `MIGRATIONS`) removes an entry only when **all three** hold:

1. its command references the expanded `~/.claude/hooks/` directory,
2. it carries **no** `# hook:` marker (unmarked ⇒ pre-manifest; marked entries are already converged by the sibling migration), and
3. its resolved script path is one of `_LEGACY_UNMARKED_SWEEP_TARGETS`.

Condition 3 is an **explicit precise-match allowlist**, deliberately not a `validators/` or `~/.claude/hooks/` prefix test. A prefix gate would silently deregister a hand-added user hook that happens to live under `~/.claude/hooks/`; only the exact scripts named in the tuple are ever removed. Blocks and events emptied by the sweep are pruned.

The migration is **registration-only: it deletes no files.** Once the registration is gone the orphaned pre-manifest files under `~/.claude/hooks/` are inert, so nothing prunes them. Any future prune must call `user_hooks_root_is_repo_aliased()` first (see [The User Hooks Root Must Be a Real Directory](#the-user-hooks-root-must-be-a-real-directory)) and decide what to keep from a static allowlist rather than a negative reference test. Like its sibling it writes a timestamped `.bak` snapshot first, asserts JSON validity before touching disk, and is idempotent — an absent file, an empty `{}`, a missing `hooks` key, or nothing left to sweep are all no-ops that write nothing.

## Both-Scope Audit

`reflections/audits/hooks_audit.py` (the `hooks-audit` daily reflection) validates **both** scopes — previously it read only the project `.claude/settings.json`, which meant the missing user-scope `\|\| true` guard (Pre-requisite Bug 1) was structurally invisible to the audit meant to catch exactly that. `_validate_hook_settings()` now runs against both `project_settings_path` and `Path.home() / ".claude" / "settings.json"`, prefixing findings with `[project]` or `[user]` so a reader can tell which scope a FAIL/WARN belongs to. It also scans `.claude/agents/*.md` for agent-level `hooks:` frontmatter blocks via `_scan_agent_hooks()` and reports them informationally — that surface is declared per-agent-file and is not owned or generated by the manifest, but must not be silently omitted from the audit report.

## Key Files

| File | Role |
|------|------|
| `.claude/hooks/manifest.toml` | The manifest itself — every static hook declaration, both scopes. |
| `scripts/update/hook_manifest.py` | `load_hook_manifest()`, `HookDeclaration`, `HookManifestError`. |
| `.claude/hooks/dispatch/pre_tool_use_bash.py` | In-process PreToolUse/Bash dispatcher. |
| `scripts/update/hardlinks.py` | `generate_project_hooks()`, `sync_project_hooks()`, `sync_user_hooks()`, `_merge_hook_settings()`, `RENAMED_REMOVALS` `"hooks"` kind, and the interpreter contract (`PROJECT_HOOK_INTERPRETER`, `GLOBAL_INTERPRETER_CANDIDATES`, `MIN_GLOBAL_PYTHON`, `resolve_global_interpreter()`). |
| `.claude/hooks/hook_python` | The project-scope interpreter shim (POSIX `sh`, extensionless, mode 755) — resolves the main checkout's venv, fails open with an auditable record. |
| `scripts/update/migrations.py` | `_migrate_hook_registration_manifest_ids()` — the one-time legacy-entry rewrite. `_migrate_sweep_legacy_unmarked_global_hooks()` — the one-time unmarked-global-entry sweep. |
| `.claude/hooks/stop.py` | Detached-extraction spawn point; genuine bare-except swallows replaced with logged handlers. |
| `.claude/hooks/hook_utils/detach_lock.py` | Absolute log path, absolute state dir, deadline/max-inflight env readers, slot reservation. |
| `.claude/hooks/hook_utils/stop_detach_worker.py` | The detached worker: self-deadline via `SIGALRM`, releases its slot in `finally`. |
| `reflections/audits/hooks_audit.py` | Both-scope audit + agent-hooks informational scan. |

## Related

- [Hooks Best Practices & Audit](hooks-best-practices.md) — the manifest is now the source of truth this doc describes.
- [Claude Code Memory](claude-code-memory.md) — how the detached worker fits into memory extraction.
- [Memory Hook Performance](memory-hook-performance.md) — Stop-hook timeout root cause and the detach fix.
- [SDLC Enforcement](sdlc-enforcement.md) — the three SDLC-fork hooks this migration targets.
