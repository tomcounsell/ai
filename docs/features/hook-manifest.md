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

## Per-Event Dispatcher

`.claude/hooks/dispatch/pre_tool_use_bash.py` collapses what used to be 7 separate interpreter starts — one process per PreToolUse/Bash validator — into a single process that reads the hook JSON from stdin once and calls each validator's predicate function in-process:

1. `validate_commit_message`
2. `validate_no_inline_timeout`
3. `validate_merge_guard`
4. `validate_no_raw_redis_delete`
5. `validate_no_uv_sync_in_worktree`
6. `validate_no_destructive_git_in_worktree`
7. `validate_design_system_sync` (out-of-process — see below)

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

1. Finds each of the three legacy entries by its literal pre-manifest command string, matched on command **path** (ignoring the on-disk matcher):
   - `validate_commit_message.py` — `PreToolUse`/`Bash`
   - `sdlc_reminder.py` — `PostToolUse`/`Edit` (single block; the intended `Write` coverage was never there)
   - `validate_sdlc_on_stop.py` — `Stop`/`""`
2. Rewrites each in place: embeds the `# hook:<manifest_id>` marker (so future runs update instead of appending a duplicate), applies the `\|\| true` guard the manifest's `blocking` flag calls for (restoring the long-missing guard on the Stop hook — this was Pre-requisite Bug 1), and upgrades `sdlc_reminder.py`'s matcher from `Edit` to `"Write|Edit"` (restoring the coverage the old dedupe quirk had silently dropped).
3. Collapses any accidental duplicate for the same `manifest_id` down to one entry, defending against ordering within a single `/update` invocation.
4. Writes a timestamped `.bak` snapshot of `~/.claude/settings.json` before any change, and asserts JSON validity on the serialized result before ever touching disk — an invalid result is never written.
5. Never touches any pre-existing, non-SDLC user hook.

The migration is idempotent (a settings.json where every legacy entry already carries its marker is a no-op) and recorded once in `data/migrations_completed.json` per the standard migration contract.

## Both-Scope Audit

`reflections/audits/hooks_audit.py` (the `hooks-audit` daily reflection) validates **both** scopes — previously it read only the project `.claude/settings.json`, which meant the missing user-scope `\|\| true` guard (Pre-requisite Bug 1) was structurally invisible to the audit meant to catch exactly that. `_validate_hook_settings()` now runs against both `project_settings_path` and `Path.home() / ".claude" / "settings.json"`, prefixing findings with `[project]` or `[user]` so a reader can tell which scope a FAIL/WARN belongs to. It also scans `.claude/agents/*.md` for agent-level `hooks:` frontmatter blocks via `_scan_agent_hooks()` and reports them informationally — that surface is declared per-agent-file and is not owned or generated by the manifest, but must not be silently omitted from the audit report.

## Key Files

| File | Role |
|------|------|
| `.claude/hooks/manifest.toml` | The manifest itself — every static hook declaration, both scopes. |
| `scripts/update/hook_manifest.py` | `load_hook_manifest()`, `HookDeclaration`, `HookManifestError`. |
| `.claude/hooks/dispatch/pre_tool_use_bash.py` | In-process PreToolUse/Bash dispatcher. |
| `scripts/update/hardlinks.py` | `generate_project_hooks()`, `sync_project_hooks()`, `sync_user_hooks()`, `_merge_hook_settings()`, `RENAMED_REMOVALS` `"hooks"` kind. |
| `scripts/update/migrations.py` | `_migrate_hook_registration_manifest_ids()` — the one-time legacy-entry rewrite. |
| `.claude/hooks/stop.py` | Detached-extraction spawn point; genuine bare-except swallows replaced with logged handlers. |
| `.claude/hooks/hook_utils/detach_lock.py` | Absolute log path, absolute state dir, deadline/max-inflight env readers, slot reservation. |
| `.claude/hooks/hook_utils/stop_detach_worker.py` | The detached worker: self-deadline via `SIGALRM`, releases its slot in `finally`. |
| `reflections/audits/hooks_audit.py` | Both-scope audit + agent-hooks informational scan. |

## Related

- [Hooks Best Practices & Audit](hooks-best-practices.md) — the manifest is now the source of truth this doc describes.
- [Claude Code Memory](claude-code-memory.md) — how the detached worker fits into memory extraction.
- [Memory Hook Performance](memory-hook-performance.md) — Stop-hook timeout root cause and the detach fix.
- [SDLC Enforcement](sdlc-enforcement.md) — the three SDLC-fork hooks this migration targets.
