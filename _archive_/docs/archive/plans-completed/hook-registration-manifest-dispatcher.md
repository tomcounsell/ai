---
status: docs_complete
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-29
tracking: https://github.com/tomcounsell/ai/issues/2435
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-29T08:28:30Z
---

# Hook registration: per-event dispatcher + manifest-generated scopes

## Problem

Claude Code hooks block the agent loop while they run. Measured over the 50 most-recently-modified session transcripts (2026-07-23 → 2026-07-28), `stop.py` timed out **126 of 131 runs** (median 10,034 ms against a 10,000 ms wall), and `user_prompt_submit.py`, `pre_tool_use.py`, and four validators time out regularly too. The medians sit exactly on the configured timeout walls — the hooks routinely never finish; the harness SIGKILLs them. **The killed work is lost with no log line, but the root cause is SIGKILL truncation, not a swallowing handler.** A SIGKILL runs no `finally`, no `except`: the in-flight extraction is severed mid-round-trip, so `memory_bridge`'s own logging (`memory_bridge.py:922` is `except Exception as e: logger.warning(...)`, already a real log handler) never gets to fire. Separately, the outer `stop.py` wrappers (`:225,242,262`) are genuine bare `except Exception: pass` swallows — but even removing those would not surface the killed work, because the process is dead before any handler runs. The fix must move extraction off the 10s wall (detach), not just improve the handlers.

Two structural facts make this un-patchable in place:

1. **`stop.py` does synchronous network I/O on the critical path.** Up to three Haiku round-trips plus two 10s-budgeted `gh` calls must fit inside a 10s wall, with zero backgrounding anywhere.
2. **Hook registration is 100% hand-maintained across two files.** `.claude/settings.json` holds 23 entries across 10 matcher groups (7 PreToolUse Bash validators alone → 7 processes per Bash call), and `~/.claude/settings.json` is written by a separate hardcoded `_SDLC_HOOK_DEFS` list. The two must stay in sync by discipline alone — and that discipline has already failed in four measurable ways (see Pre-requisite Bugs).

**Current behavior:** expensive hooks block ~15s/prompt, ~7s/Bash call, ~10s/turn-end; the most expensive one is killed before finishing and drops its work silently. Adding, removing, or making a hook non-blocking requires hand-editing two files that no test covers.

**Desired outcome:**
1. Blocking hooks stop blocking: expensive Stop work is detached and completes off the critical path; any drop is logged, not swallowed.
2. Hook registration is generated from a single declaration; neither `settings.json` `hooks` block is hand-edited.
3. One dispatcher entry per event (PreToolUse Bash spawns one process, not seven).
4. `/update`'s project→user propagation covers hooks the same way it covers skills, including removals.

## Pre-requisite Bugs

The "sync by discipline alone" failure in the Problem is not abstract — it has already broken in **four measurable, enumerable ways**. These are the concrete migration targets: the manifest + generators + audit + migration this plan builds must fix each one, and the Success Criteria below trace back to these four IDs. Each is grounded in a re-verified file:line at the Freshness Check baseline (`060e2f791`).

### Pre-requisite Bug 1 — user-scope Stop hook is registered without `|| true`

- **Evidence:** `scripts/update/hardlinks.py:725` builds every user-scope entry as `command = f"python {hooks_dir / script_name}"` — **no `|| true` suffix**. The `_SDLC_HOOK_DEFS` list (`hardlinks.py:691-696`) includes the Stop entry `("Stop", "", "validate_sdlc_on_stop.py", 15)`, so the generated `~/.claude/settings.json` Stop hook runs a bare `python …validate_sdlc_on_stop.py` with no guard.
- **Contrast:** the **project** twin at `.claude/settings.json` *does* carry the guard (`validate_sdlc_on_stop.py || true`, verified at `.claude/settings.json` Stop block). The two scopes have drifted: project is guarded, user is not.
- **Impact:** on every machine that has run `/update`, a non-zero exit from the user-scope Stop hook (an exception, a timeout SIGKILL) **blocks the agent's turn-end** instead of being swallowed. This is the single most fleet-wide latent failure.
- **Fix owner:** the migration (`scripts/update/migrations.py` `MIGRATIONS`) rewrites the deployed unguarded string; the rewritten user generator emits the guard from the manifest going forward.

### Pre-requisite Bug 2 — `hooks_audit.py` only audits the project scope, never the user scope

- **Evidence:** `reflections/audits/hooks_audit.py:70` reads only `settings_path = repo_root / ".claude" / "settings.json"`. There is **no read of `~/.claude/settings.json`** anywhere in the audit. The `|| true` FAIL check at `hooks_audit.py:82-83` therefore only ever inspects the project file.
- **Impact:** Bug 1 (the missing user-scope guard) is **structurally invisible** to the very audit meant to catch a missing `|| true`. The audit gives a false all-clear while the user scope is broken.
- **Fix owner:** the audit is extended to read and validate **both** scopes (and to scan `.claude/agents/*.md` hooks blocks as an additional declared surface).

### Pre-requisite Bug 3 — `RENAMED_REMOVALS` has no `"hooks"` kind

- **Evidence:** `scripts/update/hardlinks.py:14` declares `RENAMED_REMOVALS: list[tuple[str, str]]` whose kinds are only `"commands"` and `"skills"` (docstring line 13: *"kind is 'commands' or 'skills'"*). There is no `"hooks"` kind, and `src_for_kind` (`hardlinks.py:473-477`) has no `hooks` mapping.
- **Impact:** when a hook script is renamed or removed, its stale **hardlink in `~/.claude/hooks/`** and its stale **registration in `~/.claude/settings.json`** are never swept. Hooks are the one synced `.claude/` surface with **no removal propagation** — the exact asymmetry desired-outcome #4 targets.
- **Fix owner:** add a `"hooks"` kind to `RENAMED_REMOVALS` + `src_for_kind`, and give the user generator's merge a removal pass keyed on `manifest_id`.

### Pre-requisite Bug 4 — `uv run` is registered inside a hook (venv-corruption risk the repo's own audit forbids)

- **Evidence:** `.claude/settings.json:114` registers `validate_file_contains.py` via **`uv run "$CLAUDE_PROJECT_DIR"/.claude/hooks/validators/validate_file_contains.py …`** — the **only** hook invoked with `uv run`; every sibling validator uses bare `python`. Meanwhile `scripts/update/hooks.py:99` contains an audit that **explicitly flags `uv run` in a hook** as *"can corrupt venv or trigger slow dependency resolution"* and fails the check. The repo forbids the exact pattern it ships in its own settings.
- **Secondary evidence:** 11 validators under `.claude/hooks/validators/` carry a `uv run` shebang on line 1 (`validate_claude_config.py`, `validate_claude_md_updated.py`, `validate_documentation_section.py`, `validate_file_contains.py`, `validate_knowledge_base_section.py`, `validate_new_file.py`, `validate_no_gos_justification.py`, `validate_race_conditions.py`, `validate_test_impact_section.py`, `validate_tool_structure.py`, `validate_verification_section.py`), yet the *registered* ones are invoked with bare `python` — so the shebang is dead, misleading weight that invites exactly the `uv run` registration this bug is about.
- **Impact:** a slow/failed `uv` dependency resolution on the plan-doc PreToolUse path adds latency or a spurious non-zero exit to hook execution; the inconsistency also means the manifest cannot naively copy the current command strings verbatim (it must normalize `validate_file_contains.py` to bare `python`).
- **Fix owner:** the manifest declares `validate_file_contains.py` with a bare-`python` invocation (matching its siblings); the generator emits bare `python`; the misleading `uv run` shebangs on the surviving validators are normalized (dead ones are deleted per the Dead Code table).

**Enumerable migration target:** Bugs 1 and 4 are *deployed-state* fixes (rewrite what is already on disk in both `settings.json` files) — handled by the migration + regeneration. Bugs 2 and 3 are *mechanism* fixes (the audit reads both scopes; removal propagation gains a `hooks` kind) — handled by the audit extension and `RENAMED_REMOVALS`. All four are covered by Success Criteria and Step-by-Step tasks below, and cited by ID throughout this plan.

## Freshness Check

**Baseline commit:** `060e2f791113d1fd28f6f78c8a4080a03d0f9790`
**Issue filed at:** 2026-07-28T09:39:57Z
**Disposition:** Unchanged

**File:line references re-verified (all still hold at baseline):**
- `scripts/update/hardlinks.py:14` — `RENAMED_REMOVALS: list[tuple[str, str]]`, kinds `commands`/`skills` only — confirmed, no `hooks` kind.
- `scripts/update/hardlinks.py:691-696` — `_SDLC_HOOK_DEFS` with `("Stop", "", "validate_sdlc_on_stop.py", 15)` — confirmed.
- `scripts/update/hardlinks.py:725` — `command = f"python {hooks_dir / script_name}"` with no `|| true` — confirmed.
- `.claude/settings.json:152,156` — Stop `stop.py --chat || true` timeout 10; `validate_sdlc_on_stop.py || true` timeout 15 — confirmed (project twin carries `|| true`).
- `.claude/settings.json:114` — `validate_file_contains.py` registered with `uv run` — confirmed.
- `agent/session_runner/hook_edge.py:173` — `generate_hook_settings(...)` — confirmed.
- `reflections/audits/hooks_audit.py:70` — reads only `repo_root / ".claude" / "settings.json"` — confirmed.

**Cited sibling issues/PRs re-checked:**
- PR #195 (SDLC user-level hooks) — merged 2026-02-26; this is the origin of `sync_user_hooks`/`_SDLC_HOOK_DEFS`. Still the current mechanism.

**Commits on main since issue was filed (touching referenced files):** none. The only two commits since filing (`060e2f791`, `79d70e33b`) are anthropic dependency bumps and touch no hook/update files.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** No drift. All line numbers valid at baseline. The four sibling SDLC plans running in this session (#2436, #2438, #2439) are unrelated code areas.

## Prior Art

- **PR #195**: "SDLC user-level hooks + PR #185 tech debt cleanup" (merged 2026-02-26) — introduced `sync_user_hooks`, `_SDLC_HOOK_DEFS`, and `_merge_hook_settings`. It established the additive-only user-scope propagation this plan refactors. It did **not** add removal support, tests, or project-scope generation — exactly the gaps this issue targets. Not a failed fix; an incomplete foundation.
- No prior issue attempted a hook manifest or per-event dispatcher. This is greenfield architecture on top of #195's plumbing.

## Research

No relevant external findings — proceeding with codebase context and training data. The work is purely internal (Python + a TOML manifest parsed with stdlib `tomllib`, available since Python 3.11). No new libraries, services, or external APIs.

## Spike Results

Four code-read spikes resolved the five open questions the issue deferred to `/do-plan`. All confidence **high** (grounded in file:line evidence).

### spike-1: Per-event dispatcher — in-process vs process isolation (Open Question 1)
- **Assumption**: "In-process fan-out of the 7 PreToolUse Bash validators is feasible without changing block semantics."
- **Method**: code-read (all 7 validators + `pre_tool_use.py`/`post_tool_use.py` + `docs/features/hooks-best-practices.md`)
- **Finding**: All 7 validators use an **identical protocol**: read hook JSON from stdin, signal block by printing `{"decision":"block","reason":...}` to stdout and exiting 0 (none uses exit-code-2). Their logic is already exposed as pure predicate functions (`find_violation`/`find_violations`-style) returning a reason string or `None`. `pre_tool_use.py`/`post_tool_use.py` are **already dispatcher-shaped** — one entry on `matcher: ""` that fans out internally and gates by `tool_name`. Two validators are repo-coupled: `validate_merge_guard.py` (lazy-imports `tools.merge_predicate`, and is **fail-closed by design** — its block must survive) and `validate_design_system_sync.py` (runs `tools.design_system_sync` out-of-process). The harness supports two block channels: JSON-decision-on-stdout (what the 7 use) and exit-code-2-on-stderr (used only by `pre_tool_use._enforce_tool_budget`). A single hook invocation carries **one** decision object → semantics are **first-block-wins** (or joined reasons).
- **Confidence**: high
- **Impact on plan**: Build an **in-process PreToolUse Bash dispatcher** that calls each validator's predicate directly, wrapping **each call in its own `try/except`** (fail-open per-validator with `log_hook_error`, EXCEPT `validate_merge_guard` which stays fail-closed). One crash must not skip the remaining validators. Emit one `{"decision":"block", "reason": <first/joined>}`. This replaces 7 interpreter starts with 1 (design-system/merge-guard keep their internal subprocess/lazy-import, so 1–2 spawns instead of 7 cold starts).

### spike-2: `hook_edge.py::generate_hook_settings` generalizable? (Open Question 2)
- **Assumption**: "The per-session codegen can be unified with the manifest generator to avoid a third mechanism."
- **Method**: code-read (`hook_edge.py:173-240`, `adapter.py:358-364`, `harness/claude.py:380`)
- **Finding**: `generate_hook_settings` is a **per-session runtime channel**, not a registration mechanism. It writes a session-scoped `settings.json` where all 6 events point at ONE forwarder command carrying the per-session edge-file path as a CLI arg; consumed via `claude --settings <path>`. There is nothing per-hook to *declare* — the event set is fixed by the runner protocol and every entry is the same forwarder. Folding it into a policy manifest gains nothing and couples a runtime channel to config.
- **Confidence**: high
- **Impact on plan**: **Do NOT unify.** Collapse only the two *static* surfaces (hand-maintained project `.claude/settings.json` + `_SDLC_HOOK_DEFS`) behind one manifest with two generators (project + user). Leave `hook_edge` as a deliberately-separate `session` scope the manifest does not own. This satisfies "avoid a third mechanism" — the two static mechanisms become one; the dynamic session channel was never a registration mechanism.

### spike-3: `stop.py` detach vs SIGALRM deadline (Open Question 3)
- **Assumption**: "Detaching Haiku extraction is a better fit than an inline SIGALRM deadline for the acceptance criterion 'dropped extraction is logged, not swallowed.'"
- **Method**: code-read (`stop.py`, `memory_bridge.py:862-1010`, `user_prompt_submit.py:31-131`, `scripts/calendar_prompt_hook.sh`, `title_generator.py:128-153`)
- **Finding**: The Stop extraction produces **no output the harness/session consumes** (unlike `user_prompt_submit`'s prefetch `additionalContext`, which must return inline). So keeping it inline-and-bounded via SIGALRM merely *guarantees* loss on every slow turn (cap → drop → log-drop every time). Detach (calendar-hook pattern: background a real subprocess with detached streams, exit 0 immediately) fixes the **root cause** — extraction stops racing the 10s wall and actually completes. The transcript is already persisted to `session_dir/transcript.jsonl` before extraction, so the detached worker reads stable input. Critical caveat: it must be a **real detached subprocess / double-fork**, NOT a daemon thread — `stop.py` exits immediately and would kill an in-process thread before the Haiku call returns. If any inline deadline is ever used, the deadline exception MUST subclass `BaseException` (as `user_prompt_submit.py` does) so `memory_bridge`'s broad `except Exception` can't swallow it.
- **Confidence**: high
- **Impact on plan**: **Detach** the Stop Haiku/`gh` extraction to a subprocess that owns its own logging (writes to `logs/hooks.log`). `stop.py` exits 0 immediately. Any failure inside the worker is logged by the worker, satisfying the criterion. Replace the **genuine** bare `except: pass` swallows at `stop.py:225,242,262` with logged handlers. **Do NOT "replace" `memory_bridge.py:922` — it is already `except Exception as e: logger.warning(...)`, a real log handler, not a bare swallow.** Its failure mode was SIGKILL truncation (the process was killed before the warning could run); detaching removes the 10s wall so that existing log line actually fires. The correct action for `:922` is to leave it intact and let the detach fix its real defect.

### spike-4: Timeouts — manifest-owned vs `TimeoutSettings` (Open Question 4) + forked SDLC scripts (Open Question 5)
- **Assumption A**: "Hook timeouts should move into the `TimeoutSettings` catalog."
- **Assumption B**: "The 4 forked `.claude/hooks/sdlc/` scripts should be unified with their project twins."
- **Method**: code-read (`config/settings.py:184-202`, `docs/features/config-timeout-catalog.md:122-151`, `.claude/hooks/sdlc/*.py` vs twins)
- **Finding A**: Hook timeouts fail all three of the catalog's promote criteria (`config-timeout-catalog.md:141-151`): they are **not** read by Python (`TimeoutSettings` is consumed by `subprocess`/`requests`/`smtplib`/Popoto; the *Claude Code harness itself* reads the hook `timeout` field — Python never does), not cross-module-duplicated in a drifting way, and not per-machine tunable nor a session TTL. Direct precedent: `TOOL_TIMEOUT_*` wedge tiers are explicitly kept as raw knobs, not promoted (`config-timeout-catalog.md:122-124`). **Verdict: manifest owns timeout as a first-class field.** This also fixes the current drift where `.claude/settings.json` and `_SDLC_HOOK_DEFS` each hand-keep `timeout=15` for `validate_sdlc_on_stop.py`.
- **Finding B**: The 4 forks (`sdlc_context.py`, `sdlc_reminder.py`, `validate_commit_message.py`, `validate_sdlc_on_stop.py`) are **import-free of this repo's `hook_utils`** — each does `sys.path.insert(0, dirname(__file__))` and imports only its sibling `sdlc_context`; sources signal from live `git diff` + the harness `transcript_path` instead of `data/sessions/sdlc_state.json`. They are hardlinked to `~/.claude/hooks/sdlc/` and run inside **foreign repos'** sessions, where `hook_utils` and `data/sessions/` do not exist. **Verdict: the fork is LOAD-BEARING — do NOT unify.** The genuine (narrower) cleanup: `.claude/hooks/sdlc/validate_commit_message.py` and `.claude/hooks/validators/validate_commit_message.py` share a name but implement *different* checks (co-author/empty-message vs code-to-main block) — a naming collision worth resolving in this work.
- **Confidence**: high
- **Impact on plan**: Manifest carries `timeout` per entry. Do not touch the fork/twin split except to rename one of the colliding `validate_commit_message.py` files so the two distinct checks have distinct names.

## Data Flow

**Registration generation (`/update`), after this change:**
1. **Entry point**: `scripts/remote-update.sh` / `scripts/update/run.py` invokes `sync_claude_dirs` (`hardlinks.py:187`).
2. **Manifest load**: new `load_hook_manifest()` parses `.claude/hooks/manifest.toml` (stdlib `tomllib`) into a list of typed hook declarations `(event, matcher, script, timeout, scope, blocking)`.
3. **Project generator**: `generate_project_hooks(manifest)` projects the `scope in {project, global}` entries into the `hooks` block of the repo `.claude/settings.json`, preserving intra-matcher order; per-event dispatcher entries collapse N validators into one.
4. **User generator**: `sync_user_hooks(manifest)` hardlinks `scope == global` scripts into `~/.claude/hooks/` and projects their entries into `~/.claude/settings.json` via a rewritten `_merge_hook_settings` that now has a **removal pass** (deletes manifest-owned entries no longer declared) keyed by a stable `manifest_id`, not exact command string. A **legacy exact-command fallback** matches the **three deployed** pre-existing legacy commands (four `_SDLC_HOOK_DEFS` tuples collapse to three on-disk entries — Write/Edit share one command; see Technical Approach) which carry no `manifest_id`, and rewrites them in place, so the first post-migration run never appends duplicates alongside the legacy entries.
5. **Removal propagation**: `RENAMED_REMOVALS` gains a `"hooks"` kind so renamed/removed hook scripts are swept from `~/.claude/hooks/`.
6. **Output**: both `settings.json` `hooks` blocks are byte-for-byte reproducible from the manifest; `git diff` on either after a regen is empty when the manifest is unchanged.

**Stop-hook runtime, after this change:**
1. **Entry point**: harness fires the Stop hook → `stop.py --chat` (exit-0-guarded).
2. `stop.py` persists transcript (already happens) and **spawns a detached extraction subprocess** (`memory_bridge` extraction + post-merge `gh`/Haiku), then exits 0 within ~ms.
3. **Detached worker**: runs the Haiku round-trips off the critical path, logging success/failure to `logs/hooks.log`.
4. **Output**: memory records written asynchronously; no harness SIGKILL; drops logged.

## Architectural Impact

- **New dependencies**: none external. New internal module(s): a hook-manifest loader, a per-event dispatcher module, and a detached-extraction entry point.
- **Interface changes**: `_merge_hook_settings` gains a removal pass and keys on a stable `manifest_id` rather than exact command string; `_SDLC_HOOK_DEFS` is replaced by the manifest. `sync_user_hooks` signature grows a manifest argument.
- **Coupling**: **decreases** — 23 hand-maintained JSON entries + a hardcoded Python list collapse to one declarative manifest. `hook_edge` stays decoupled (deliberate).
- **Data ownership**: the manifest becomes the single source of truth for static hook registration across both scopes. Agent-declared hooks (`.claude/agents/*.md` `hooks:` blocks, e.g. `builder.md:5-10`) remain owned by the agent files but MUST be read by the audit/generator as an additional declared surface (no silent omission, no conflict).
- **Reversibility**: high — the manifest generates the same JSON that exists today; reverting means regenerating from the old hand-maintained values (captured as the manifest's initial content).

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (the migration for Pre-requisite Bug 1 touches every machine's `~/.claude/settings.json`; scope alignment on detach-vs-inline was resolved by spike-3 but the migration blast radius warrants a check-in)
- Review rounds: 2 (one for the dispatcher + stop.py detach, one for the manifest generator + migration)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Python 3.11+ (stdlib `tomllib`) | `python -c "import tomllib"` | Parse the TOML manifest without a third-party dep |
| Clean git worktree before migration test | `git status --porcelain` | Migration rewrites `~/.claude/settings.json`; verify no unrelated drift |

Run via `python scripts/check_prerequisites.py docs/plans/hook-registration-manifest-dispatcher.md`.

## Solution

### Key Elements

- **Hook manifest** (`.claude/hooks/manifest.toml`): the single declaration of every static hook — `event`, `matcher`, `script`, `timeout`, `scope` (`project`|`global`), `blocking`, and a stable `manifest_id`. Chosen over per-file frontmatter because hooks are `.py` files with no natural frontmatter slot, and a single manifest preserves intra-matcher ordering and gives one reviewable diff.
- **Per-event dispatcher** (`.claude/hooks/dispatch/pre_tool_use_bash.py` or extension of the existing `pre_tool_use.py` shape): one entry that runs the validator predicate set in-process, per-validator `try/except` (fail-open, except merge-guard fail-closed), first-block-wins.
- **Two generators in `scripts/update/hardlinks.py`**: `generate_project_hooks(manifest)` rewrites the repo `settings.json` hooks block; `sync_user_hooks(manifest)` (rewritten) hardlinks global scripts and projects their entries into `~/.claude/settings.json` with a removal pass.
- **Detached Stop extraction**: `stop.py` spawns a real detached subprocess for memory/post-merge extraction and exits 0 immediately; the worker logs drops.
- **Audit fix**: `hooks_audit.py` reads both scopes and validates every registered command path exists and every Stop hook carries `|| true`; it also scans `.claude/agents/*.md` hooks blocks.
- **Migration**: rewrites the **three deployed** legacy command strings in `~/.claude/settings.json` (`validate_commit_message.py`, `sdlc_reminder.py` [single `Edit` block], `validate_sdlc_on_stop.py`) in place — attaching each entry's `manifest_id`, upgrading `sdlc_reminder.py`'s matcher to `"Write|Edit"`, and adding the Stop entry's missing `|| true` (Pre-requisite Bug 1) — rather than additively appending second copies. The legacy exact-command fallback (Technical Approach) guarantees none of the three survives as a stale/duplicate entry. (Four `_SDLC_HOOK_DEFS` tuples exist, but the dedup-by-command-string quirk collapsed the two `sdlc_reminder.py` tuples to one on-disk block; the migration operates on deployed reality.)

### Flow

`/update` runs → `load_hook_manifest()` → `generate_project_hooks()` rewrites `.claude/settings.json` → `sync_user_hooks()` hardlinks global scripts + rewrites `~/.claude/settings.json` (add/update/remove) → migration sweeps legacy unguarded entry → `hooks_audit` (both scopes) passes → services restart.

At runtime: Bash tool call → **one** dispatcher process runs 7 predicates → first block wins (or all allow). Turn ends → `stop.py` spawns detached extraction, exits 0 → worker completes Haiku extraction off-path, logs result.

### Technical Approach

- **Manifest is authoritative for static registration only.** `hook_edge` (session scope) and agent-declared hooks (`.claude/agents/*.md`) are separate declared surfaces the generator/audit *reads* but does not *own*.
- **Manifest declaration order is the canonical ordering (tech-debt 1).** Risk 4's byte-for-byte / empty-`git diff`-on-regen promise depends on stable ordering. The manifest's array order is authoritative: the generator emits entries in declaration order, and the `manifest_id`-keyed merge updates entries **in place** without resorting. The empty-diff regen test (Verification table) is the guardrail that ordering never drifts — build must treat position as load-bearing, not incidental.
- **Dedupe/removal key = `manifest_id`.** Today `_merge_hook_settings` dedupes by exact command string (`hardlinks.py:742`), which is why Bug 1's fix can't be a simple string append (it would leave the old entry and add a second). Switch the key to a stable `manifest_id` embedded in each generated command (e.g. a trailing `# hook:<id>` comment or a companion index) so add/update/remove all key off identity, not the volatile command string.

- **Deployed-reality baseline: the user scope has THREE legacy entries, not four, and the plan FIXES the dedup quirk that caused it.** `_SDLC_HOOK_DEFS` declares four tuples, two of which — `("PostToolUse", "Write", "sdlc_reminder.py", 10)` and `("PostToolUse", "Edit", "sdlc_reminder.py", 10)` — emit a **byte-identical** command (`python <hooks_dir>/sdlc_reminder.py`; the matcher is not part of the command). The current dedupe-by-command-string logic (`hardlinks.py` `_merge_hook_settings`) therefore stores only ONE PostToolUse block for `sdlc_reminder.py`: Write is appended first, then Edit matches the existing command and the "update matcher if it changed" branch **overwrites the matcher to `Edit` (last-wins)**. Verified live: the deployed `~/.claude/settings.json` contains exactly three SDLC entries — `PreToolUse/Bash validate_commit_message.py`, `PostToolUse/Edit sdlc_reminder.py`, `Stop/"" validate_sdlc_on_stop.py`. There is **no** `PostToolUse/Write` entry anywhere in the fleet. This is a pre-existing bug: `sdlc_reminder.py` was intended to fire on both Write and Edit and has silently only fired on Edit on every machine.
  - **Decision: FIX, do not inherit.** The manifest declares `sdlc_reminder.py` **once**, with the alternation matcher `"Write|Edit"` — the exact form the project scope already uses at `.claude/settings.json:73` (Claude Code supports regex-alternation matchers; confirmed in the live project settings). Because the new generators key on `manifest_id`, there is no command-string collapse: one manifest entry → one on-disk block with matcher `"Write|Edit"`, restoring the intended Write coverage that the quirk dropped.
  - **Migration/fallback for the "manifest declares one entry, disk holds a differently-matched legacy entry" case.** The migration keys the legacy `sdlc_reminder.py` block by command-path identity (there is only one on disk, matcher `Edit`), maps it to the manifest_id for `sdlc_reminder`, and **rewrites its matcher in place to `"Write|Edit"`** rather than deleting-and-re-adding — this preserves entry order and is idempotent (second run sees the matcher already `"Write|Edit"` and is a no-op). If a machine somehow has *two* legacy `sdlc_reminder.py` blocks (e.g. a hand-edited settings file with distinct commands), the migration collapses them into the single `manifest_id`-keyed `"Write|Edit"` block and removes the duplicate. The general removal pass (keyed on `manifest_id`) covers any manifest-owned entry no longer declared.
- **Legacy exact-command fallback (mandatory — prevents fleet-wide duplication). Keyed to the THREE deployed entries, not four `_SDLC_HOOK_DEFS` tuples.** The manifest_id key only recognizes entries that *this* new code wrote. But every machine's `~/.claude/settings.json` **already** contains the deployed legacy entries, each written by the old code path as a bare `python {hooks_dir}/sdlc/{script}` command with **no `|| true` and no `manifest_id` marker**. Because the old `_merge_hook_settings` dedupes by command string and the two `sdlc_reminder.py` tuples emit an identical command (collapsing to a single `Edit`-matched block, per the Deployed-reality baseline above), **exactly three legacy commands are on disk** — not four:
  1. `python ~/.claude/hooks/sdlc/validate_commit_message.py` — `PreToolUse` / `Bash`
  2. `python ~/.claude/hooks/sdlc/sdlc_reminder.py` — `PostToolUse` / `Edit` (single block; the intended Write matcher was silently dropped by the dedup quirk)
  3. `python ~/.claude/hooks/sdlc/validate_sdlc_on_stop.py` — `Stop` / `""`
  If the new manifest_id-keyed dedupe runs against these, it finds no matching `manifest_id` for any of them and **appends new manifest_id-tagged entries alongside the three legacy ones → every event gets a duplicate hook fleet-wide**. So the removal/merge pass MUST carry a **legacy exact-command fallback**: for each manifest entry whose `manifest_id` is absent from the existing settings, also match the known legacy command string for that `(event, script)` pair (the exact `python {hooks_dir}/sdlc/{script}` form, with and without a trailing `|| true`), keyed on **command path, ignoring the on-disk matcher**, and **rewrite that entry in place** (attaching the `manifest_id`, the guard, and — for `sdlc_reminder.py` — upgrading the matcher from the deployed `Edit` to the manifest's `"Write|Edit"`) rather than appending a new one. The fallback covers all three deployed legacy commands, so **no stale or duplicated entry survives** the first post-migration `/update` on any machine, and the long-missing Write coverage is restored. After the first run every entry carries a `manifest_id`, so the fallback is a one-time bridge; it stays in the code (idempotent) to cover machines that update late.
- **Dispatcher preserves each validator's existing fail posture.** Standalone validators fail-open on internal error (log + continue); `validate_merge_guard` stays fail-closed (its block must survive). Encode this per-validator, not as a blanket policy.
- **Dispatcher outcome is `first-block-wins`, deterministically ordered (tech-debt 3).** spike-1 left this as "first-block-wins *(or joined reasons)*"; this plan **decides first-block-wins**. Validators run in manifest declaration order; the first to return a block reason short-circuits and the single emitted `{"decision":"block","reason":…}` carries that reason. This keeps block messages deterministic and avoids reason-concatenation ambiguity.
- **Detach must be a subprocess/double-fork**, not a thread — verified in spike-3.
- **Timeouts live in the manifest**, not `TimeoutSettings` — verified in spike-4A.
- **Dead-code removal** (see table) and the `validate_commit_message.py` name collision resolution ride along — but as an explicitly **non-gating** track; see Scope Sequencing.

## Scope Sequencing

Re-critique flagged that this Large plan bundles four loosely-coupled workstreams and that the acute fix has no dependency edge. This section makes the sequencing explicit so build ships value early and reviewers can reason about independent tracks. The plan stays a **single plan/issue** (the workstreams share one manifest/audit surface and one coherent "hook registration" thesis), but it builds and reviews in three ordered phases with distinct merge points.

**Phase A — Stop-hook detach (acute fix, ships first, zero dependencies).** Tasks build-stop-detach (Step 3) **and its dedicated test task build-tests-phase-a (Step 8a)**. This is the fix for the measured 126/131 Stop timeouts — the single highest-impact, lowest-blast-radius change. It has **no `Depends On`** edge (does not need the manifest, generators, dispatcher, or migration) and touches only `stop.py` + the detached-worker entry point (Phase A also replaces the genuine `stop.py:225,242,262` bare-except swallows with logged handlers; `memory_bridge.py:922` is left intact — see spike-3). **Phase A's tests do NOT depend on any Phase B task** — build-tests-phase-a `Depends On: build-stop-detach` only, so the whole of Phase A (code + tests + its own Review round 1) can be built, reviewed, and merged on its own branch before Phase B begins.** Review round 1 covers Phase A alone. The prior single monolithic `build-tests` task (which depended on the full Phase B set) is split into build-tests-phase-a (Step 8a, Phase A) and build-tests-phase-b (Step 8b, Phase B) precisely so Phase A's test gate is not hostage to Phase B.

**Phase B — Registration consolidation (the core refactor).** Tasks build-manifest, build-dispatcher, build-generators, build-migration, build-audit (Steps 1, 2, 4, 5, 6). This is the manifest + two generators + per-event dispatcher + migration + both-scope audit — the interdependent heart of the issue. Review round 2 covers Phase B.

**Phase C — Cleanup ride-along (non-gating, may be deferred or split).** Tasks build-cleanup (Step 7): the 7-validator dead-code deletion, the `uv run` shebang normalization on surviving validators, and the `validate_commit_message.py` name-collision rename.
- **Concern acknowledged — why it stays in this plan rather than splitting to a separate issue:** the shebang normalization is the *script-side twin* of Pre-requisite Bug 4's *registration-side* fix (the manifest drops `uv run`; the surviving scripts must not keep an `uv run` shebang that contradicts their bare-`python` registration), and the dead-code deletion is what makes the both-scope `hooks_audit` "every registered command path exists" assertion clean — leaving 10 unregistered validators in place would keep the audit surface noisy. The name-collision rename is coupled to Phase B's migration literal (tech-debt 5). So Phase C is genuinely adjacent, not a grab-bag.
- **But it is explicitly non-gating:** Phase C does NOT block Phase A or Phase B merging. If review or time pressure warrants, Phase C can be deferred to a fast-follow PR (or a separate cleanup issue) without weakening the core deliverable. Its Success Criteria are marked as such below. The only hard coupling is the name-collision literal, which must land with (or before) Phase B's migration if the rename is done at all; if Phase C is deferred, the rename is deferred with it and Phase B's migration keeps the current name on both sides.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `stop.py:225,242,262` **genuine** bare `except Exception: pass` blocks: in the detached worker path, replace with handlers that log to `logs/hooks.log`. **`memory_bridge.py:922` is NOT in this set** — it is already `except Exception as e: logger.warning(...)` and is left as-is; its real defect was SIGKILL truncation (the warning never ran because the process was killed first), which detaching resolves by removing the 10s wall. Add a test asserting a forced extraction failure produces a log line (observable behavior), not silence — the test must exercise the detached worker so `:922`'s existing warning (and the rewritten `stop.py` handlers) actually fire.
- [ ] Dispatcher per-validator `try/except`: add a test that injects a raising validator and asserts (a) the remaining validators still run, (b) the crash is logged via `log_hook_error`, (c) a fail-closed validator (merge-guard) raising still blocks.

### Empty/Invalid Input Handling
- [ ] `load_hook_manifest()` on an empty/malformed `manifest.toml`: assert it raises a clear error (fail-closed for generation — a broken manifest must not silently wipe the hooks block).
- [ ] Dispatcher receiving empty/whitespace stdin JSON: assert it allows (no spurious block) and does not crash.
- [ ] `_merge_hook_settings` with an empty existing `~/.claude/settings.json`: assert it writes a valid block.

### Error State Rendering
- [ ] Detached extraction worker failure is user-invisible by design, but MUST appear in the drop log. Test asserts the log line exists on failure. **Foreign-repo case (tech-debt 2):** a second test runs the worker with cwd set outside this repo (no repo-relative `logs/`) and asserts the drop still lands in the absolute, create-on-write log path — proving drops are logged, not silently lost, in user-scope/foreign-repo sessions.
- [ ] `hooks_audit` FAIL findings (missing `|| true`, missing command path) render in the audit report for both scopes.

## Test Impact

- [ ] `tests/unit/test_update_hardlinks.py:151-163` (`test_sync_claude_dirs_includes_user_scripts`) — REPLACE: it currently sidesteps hook sync (fake project ships no hook files so `sync_user_hooks` early-returns). Rewrite to ship fake `.claude/hooks/` scripts + a fake `manifest.toml` and assert hardlink + registration actually happen.
- [ ] `tests/unit/test_update_hardlinks.py` (idempotency/preservation neighbors, ~lines 142-148) — UPDATE: extend to cover the new removal pass and matcher-update path for hooks.
- [ ] `reflections/audits/hooks_audit.py` tests (if any exist) — UPDATE: add user-scope assertions; if none exist, this is new coverage (see Documentation/Success Criteria).
- [ ] Any test asserting the exact shape of `.claude/settings.json` hooks block — UPDATE: the block is now generated; assert it equals the manifest projection rather than a hand-frozen literal. (Audit `grep -rn 'settings.json' tests/` during build; none found blocking at plan time.)

## Rabbit Holes

- **Unifying `hook_edge.generate_hook_settings` into the manifest generator.** Spike-2 ruled this out — it is a per-session runtime channel with nothing per-hook to declare. Do not attempt; it adds coupling for no gain.
- **Unifying the 4 forked `.claude/hooks/sdlc/` scripts with their twins.** Spike-4B proved the fork is load-bearing for foreign-repo user-scope execution. Only resolve the `validate_commit_message.py` name collision; leave the fork.
- **Promoting hook timeouts into `TimeoutSettings`.** Spike-4A ruled this out against the catalog's own criterion. The harness reads the timeout, not Python.
- **Rewriting the memory extraction logic itself.** This work moves *where* extraction runs (detached), not *how* it extracts. Resist refactoring `memory_bridge`'s Haiku prompts.
- **Restoring the memory corpus (1,991 → 1 record).** Explicitly out of scope and tracked separately; do not assume this fix restores it.

## Risks

### Risk 1: Migration for Pre-requisite Bug 1 corrupts `~/.claude/settings.json` on every machine
**Impact:** A bad rewrite of the user-scope settings could drop legitimate non-SDLC hooks or produce invalid JSON, breaking hooks on every machine that runs `/update`.
**Mitigation:** Migration is idempotent, recorded once in `data/migrations_completed.json` (register in `scripts/update/migrations.py` `MIGRATIONS`). It rewrites only the **three deployed** legacy command strings in place (matched by the legacy exact-command fallback: the `python {hooks_dir}/sdlc/{script}` literal for each of `validate_commit_message.py`, `sdlc_reminder.py` [single `Edit` block], `validate_sdlc_on_stop.py`, with/without a trailing `|| true`), preserves all non-manifest blocks (the `hardlinks.py:706` "never clobbers non-SDLC hooks" promise), and writes via a parse→modify→serialize round-trip with a JSON-validity assertion before write. **A timestamped `.bak` copy of `~/.claude/settings.json` is written before the round-trip** (tech-debt 4) so an unforeseen bad write is recoverable on any fleet machine — this is the documented rollback path. Unit tests seed the **real deployed 3-entry shape** (not a synthetic 4-distinct-command fixture) and prove (a) a pre-existing non-SDLC user hook survives, (b) after migration each event has exactly one entry (no duplicates) and the Stop entry carries `|| true`, and (c) the `sdlc_reminder.py` block's matcher is deliberately asserted to become `"Write|Edit"` — proving the phantom-`Write` case (a Write entry that never existed on disk) is not appended and the intended Write coverage is restored.

### Risk 2: In-process dispatcher converts 7 isolated fail-opens into one shared crash surface
**Impact:** A validator raising mid-dispatch could skip every validator after it, silently losing merge-guard/redis-guard enforcement.
**Mitigation:** Per-validator `try/except` (spike-1). Test injects a raising validator and asserts the rest still run and the fail-closed merge-guard still blocks.

### Risk 3: Detached Stop subprocess orphans, races the next turn, or fans out unbounded
**Impact:** A wedged detached worker could linger; extraction may still run when the next turn starts; and under an SDLC batch, many turns ending at once could spawn an unbounded swarm of Haiku/`gh` workers.
**Mitigation:** Worker reads the already-persisted `transcript.jsonl` (stable input, no race). **Self-deadline (concern 1):** the worker enforces a named, env-overridable `HOOK_DETACH_DEADLINE_SECONDS` wall (default provisional/tunable, comfortably above observed round-trip cost) via `SIGALRM`/monotonic-clock check; the raised deadline exception subclasses `BaseException` (spike-3) so `memory_bridge`'s broad `except Exception` cannot swallow it. On deadline the worker logs `deadline-exceeded` and exits — it self-terminates rather than lingering. Specified in Step 3 and tested by build-tests-phase-a. **Concurrency cap (concern 2):** `stop.py` refuses to spawn beyond a named, env-overridable `HOOK_DETACH_MAX_INFLIGHT` (small default, provisional), enforced by a cwd-independent filesystem-visible in-flight counter (live worker PID/lock files under an absolute state dir) that survives across the independent `stop.py` processes; over-cap invocations log `detach-skipped: at capacity` rather than silently dropping. Detach via `Popen`/double-fork with `start_new_session=True` and redirected streams (calendar-hook pattern), never a daemon thread. **Log-path robustness (tech-debt 2):** the worker's drop log must resolve to an **absolute, always-writable path created on write**, not a repo-relative `logs/hooks.log` — user-scope hooks run inside foreign repos where a repo-relative `logs/` does not exist, and a silently-failing log write would re-swallow the very drops this plan promises to surface. The in-flight-counter state dir follows the same absolute, create-on-write rule.

### Risk 4: Generated `settings.json` diff churn breaks reviewers/tooling
**Impact:** Consumers that read `.claude/settings.json` (`sync_claude_to_opencode.py`, `tools/design_system_sync.py`, `.opencode/SYNC_MANIFEST.json`) could break if the generated shape differs from hand-maintained.
**Mitigation:** First manifest is authored to reproduce the *current* JSON byte-for-byte (order preserved). A test asserts regeneration on an unchanged manifest yields an empty `git diff`. Downstream readers are checked for shape assumptions during build.

## Race Conditions

### Race 1: Detached extraction vs transcript persistence
**Location:** `stop.py` (spawn point) → `memory_bridge.extract` in the detached worker
**Trigger:** Worker starts reading the transcript before `stop.py` finished writing it.
**Data prerequisite:** `session_dir/transcript.jsonl` fully flushed before the worker reads it.
**State prerequisite:** Transcript file handle closed.
**Mitigation:** `stop.py` persists (and closes) the transcript *before* spawning the worker (existing order at `stop.py:110-111`); the worker only reads. No shared mutable state; the worker owns its own Redis writes.

### Race 2: Concurrent `/update` runs regenerating both scopes
**Location:** `generate_project_hooks` / `sync_user_hooks` writing `settings.json`
**Trigger:** Two `/update` invocations racing (e.g. two worktrees).
**Data prerequisite:** A single writer per settings file.
**State prerequisite:** Generation is deterministic from the manifest, so a lost-update is self-healing on the next run.
**Mitigation:** Generation is idempotent and deterministic — a race produces at worst a redundant rewrite, not corruption. The full-suite pytest lock already serializes update-adjacent test runs; no new lock needed for the generation itself.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2438] Memory-corpus collapse (1,991 → 1 record) restoration — filed as its own reliability issue (#2438); this hook fix is not assumed to restore the corpus, and the daily hard-delete prune is a separate root cause.
- Unifying `hook_edge.generate_hook_settings` with the manifest (spike-2: deliberately separate session scope).
- Unifying the 4 forked `.claude/hooks/sdlc/` scripts with their project twins (spike-4B: load-bearing fork).
- Promoting hook timeouts into `TimeoutSettings` (spike-4A: fails the catalog criterion).

## Update System

This work **is** primarily an `/update` change — `scripts/update/hardlinks.py` is the core surface.
- `_SDLC_HOOK_DEFS` is replaced by `load_hook_manifest()`; `sync_user_hooks` and `_merge_hook_settings` are rewritten to consume the manifest and gain a removal pass.
- New config file `.claude/hooks/manifest.toml` propagates via the repo checkout (it lives in `.claude/`, already synced).
- `RENAMED_REMOVALS` gains a `"hooks"` kind (Pre-requisite Bug 3); `src_for_kind` (`hardlinks.py:473-477`) gains a `hooks` mapping.
- A migration in `scripts/update/migrations.py` (`MIGRATIONS` dict) rewrites in place the **three deployed** legacy entries in `~/.claude/settings.json` (Pre-requisite Bug 1 + the legacy exact-command fallback; the two `sdlc_reminder.py` `_SDLC_HOOK_DEFS` tuples are already collapsed to one on-disk block by the dedup quirk), idempotent and recorded in `data/migrations_completed.json`.
- The manifest drops the sole `uv run` hook registration (Pre-requisite Bug 4): the regenerated `.claude/settings.json` invokes `validate_file_contains.py` with bare `python`, and `scripts/update/hooks.py`'s `uv run` audit passes against the generated output.
- `scripts/update/verify.py:141,609` PATH/path-migration checks reviewed for the new manifest.

## Agent Integration

No agent integration required — this is infrastructure for the Claude Code harness (hooks), not a new agent-reachable capability. There is no new CLI entry point or MCP tool, and the bridge does not call this code. The dispatcher and manifest are consumed by the harness and by `/update`, both already-existing surfaces. Existing hooks (memory ingest/recall, SDLC enforcement) continue to reach the agent through the same lifecycle events; this work changes only how they are registered and when the expensive ones run.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/hook-manifest.md` describing the manifest shape, the two generators, the dispatcher, and the removal/propagation model.
- [ ] Add entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/hooks-best-practices.md` (dispatcher pattern; manifest as source of truth; `|| true` still required on Stop).
- [ ] Update `docs/features/claude-code-memory.md` (Stop extraction is now detached).
- [ ] Update `docs/features/sdlc-enforcement.md` (user-scope `|| true` fix + audit both scopes).
- [ ] Create/update `docs/features/memory-hook-performance.md` (timeout root cause + detach fix + before/after timeout rate).

### Inline Documentation
- [ ] `.claude/skill-context/reclassify.md:15,22` — remove the false claim that `validate_plan_label.py`/`validate_type_immutability.py` enforce edits (they are unregistered/dead per the removal table).
- [ ] `docs/guides/valor-name-references.md` — one-line table edit when `validate_tool_structure.py`/`validate_claude_md_updated.py` are deleted.
- [ ] Update the hook description in `CLAUDE.md`.

## Dead Code to Remove

Per recon of the 11 unregistered scripts under `.claude/hooks/`: **7 are confirmed dead (delete); 4 are kept** — `format_file.py` (live via `builder.md:10`), `validate_issue_recon.py` (invoked by `docs/sdlc/do-plan.md:31`), `validate_verification_section.py` (passing unit test), and `validate_knowledge_base_section.py` (documented manual soft validator). The 7 deletions below match the grep-based Verification row (which lists exactly 7 files):

| Verdict | Files |
|---|---|
| **Safe to delete** | `validate_claude_config.py`, `validate_new_file.py`, `validate_tool_structure.py`, `validate_claude_md_updated.py` (last two: one-line edit to `docs/guides/valor-name-references.md`) |
| **Delete + fix docs** | `validate_race_conditions.py`, `validate_plan_label.py`, `validate_type_immutability.py` — also fix `.claude/skill-context/reclassify.md:15,22` (it falsely claims two of these block edits) |
| **Do NOT delete** | `format_file.py` (live agent hook), `validate_issue_recon.py` (invoked by `docs/sdlc/do-plan.md:31`), `validate_verification_section.py` (passing unit test), `validate_knowledge_base_section.py` (documented manual soft validator) |

## Success Criteria

- [ ] **Pre-requisite Bug 1** fixed with a **migration that rewrites in place** the legacy unguarded Stop command in `~/.claude/settings.json` to carry `|| true` (not an additive rewrite); a test proves the stale unguarded entry is gone and non-SDLC hooks survive.
- [ ] **Legacy exact-command fallback covers the three deployed legacy entries** (`validate_commit_message.py`, `sdlc_reminder.py` [single `Edit` block], `validate_sdlc_on_stop.py`): a test seeds a `~/.claude/settings.json` in the **real deployed 3-entry shape** (Write collapsed under Edit — NOT a synthetic 4-distinct-command fixture), runs the new generator/migration, and asserts each event has exactly one entry (no duplicates), every entry carries a `manifest_id`, and the `sdlc_reminder.py` block's matcher is asserted to be `"Write|Edit"` (phantom-Write is never appended; intended Write coverage restored).
- [ ] **Pre-requisite Bug 2** fixed: `hooks_audit.py` audits user-scope `~/.claude/settings.json` in addition to the project file, and scans `.claude/agents/*.md` hooks blocks.
- [ ] **Pre-requisite Bug 3** fixed: `RENAMED_REMOVALS` gains a `"hooks"` kind (+ `src_for_kind` mapping) with removal propagation verified by test.
- [ ] **Pre-requisite Bug 4** fixed: the manifest declares `validate_file_contains.py` with a bare-`python` invocation (no `uv run`); the generated `.claude/settings.json` no longer contains `uv run` for any hook; `scripts/update/hooks.py`'s own `uv run` audit passes against the generated settings; misleading `uv run` shebangs on surviving validators are normalized.
- [ ] Both scopes' `hooks` blocks are generated from `.claude/hooks/manifest.toml`; regeneration on an unchanged manifest yields empty `git diff`; neither block is hand-edited.
- [x] One dispatcher entry per event; PreToolUse Bash spawns one process, not seven; a raising validator does not skip the rest, and merge-guard stays fail-closed.
- [ ] `stop.py` no longer times out: over a session sample, Stop timeout rate near zero; any dropped extraction is logged to `logs/hooks.log`, not swallowed. (`memory_bridge.py:922` already logs; detaching removes the SIGKILL truncation that prevented that log line from firing.)
- [ ] **Detached worker self-deadline (concern 1):** the worker enforces `HOOK_DETACH_DEADLINE_SECONDS` (named, env-overridable) and self-terminates with a `deadline-exceeded` log line when exceeded; a test forces a hang and asserts the worker is reaped by its own deadline (does not linger).
- [ ] **Concurrent detach cap (concern 2):** `stop.py` refuses to spawn beyond `HOOK_DETACH_MAX_INFLIGHT` (named, env-overridable) concurrent workers, logging `detach-skipped: at capacity`; a test with the cap already saturated asserts no additional worker is spawned.
- [ ] Tests cover `sync_user_hooks()`, `_merge_hook_settings()` (add/update/**remove**), and the manifest generator (currently untested; `test_update_hardlinks.py:151-163` sidesteps them).
- [ ] A test asserts every registered command path exists and every Stop hook carries `|| true`, in both scopes.
- [ ] **[Phase C — non-gating]** Dead validators removed per the table; `.claude/skill-context/reclassify.md` no longer claims enforcement that does not exist; `validate_commit_message.py` name collision resolved. Per Scope Sequencing, Phase C does NOT gate Phase A or Phase B merging; if deferred to a fast-follow, this criterion travels with it (the name-collision rename defers too, and Phase B's migration keeps the current name on both sides).
- [ ] Docs updated (all files in the Documentation section).
- [ ] Tests pass (`/do-test`); lint/format clean.

## Team Orchestration

The lead orchestrates; it never builds directly.

### Team Members

- **Builder (dispatcher + stop.py)**
  - Name: `dispatcher-builder`
  - Role: In-process PreToolUse Bash dispatcher + detached Stop extraction
  - Agent Type: builder
  - Domain: async/concurrency (detach), untrusted-input (validator fan-out)
  - Resume: true

- **Builder (manifest + generators + migration)**
  - Name: `manifest-builder`
  - Role: `manifest.toml`, `load_hook_manifest`, project+user generators, removal pass, `RENAMED_REMOVALS` hooks kind, migration
  - Agent Type: builder
  - Resume: true

- **Builder (audit + dead code + docs-inline)**
  - Name: `audit-builder`
  - Role: `hooks_audit.py` both-scope + agent-hooks scan; dead-code deletion; reclassify.md fix; name-collision rename
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `hook-test-engineer`
  - Role: hook-sync tests, dispatcher crash-isolation test, migration test, timeout-rate assertion
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `hook-documentarian`
  - Role: feature docs + updates to hooks/memory/sdlc docs
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `hook-validator`
  - Role: verify all success criteria, both scopes, empty-diff regeneration
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Author the manifest + loader
- **Task ID**: build-manifest
- **Depends On**: none
- **Validates**: tests/unit/test_hook_manifest.py (create)
- **Informed By**: spike-2 (keep hook_edge separate), spike-4A (manifest owns timeout)
- **Assigned To**: manifest-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/manifest.toml` reproducing the current 23 entries + `_SDLC_HOOK_DEFS`, each with `event`, `matcher`, `script`, `timeout`, `scope`, `blocking`, `manifest_id`.
- **Bug 4 normalization:** declare `validate_file_contains.py` with a bare-`python` invocation (NOT `uv run`) so the generated `.claude/settings.json` drops the sole `uv run` registration (`.claude/settings.json:114`). Otherwise the first manifest is authored to reproduce current JSON byte-for-byte (Risk 4).
- Implement `load_hook_manifest()` (stdlib `tomllib`) returning typed declarations; fail-closed on malformed input.

### 2. Per-event dispatcher
- **Task ID**: build-dispatcher
- **Depends On**: none
- **Validates**: tests/unit/test_pre_tool_use_dispatcher.py (create)
- **Informed By**: spike-1 (JSON-decision protocol, per-validator try/except, merge-guard fail-closed)
- **Assigned To**: dispatcher-builder
- **Agent Type**: builder
- **Domain**: untrusted-input
- **Parallel**: true
- Build the in-process PreToolUse Bash dispatcher calling each validator predicate; per-validator `try/except` (fail-open, merge-guard fail-closed); first-block-wins single decision object.

### 3. Detach Stop extraction
- **Task ID**: build-stop-detach
- **Depends On**: none
- **Validates**: tests/unit/test_stop_detach.py (create)
- **Informed By**: spike-3 (real subprocess not thread; worker owns logging; BaseException if any deadline)
- **Assigned To**: dispatcher-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: true
- Spawn detached extraction subprocess (`Popen`/double-fork, `start_new_session=True`, redirected streams); `stop.py` exits 0 immediately; worker logs drops to `logs/hooks.log`; replace the **genuine** bare `except: pass` swallows at `stop.py:225,242,262` with logged handlers. Leave `memory_bridge.py:922`'s existing `except Exception as e: logger.warning(...)` intact — detaching lets it actually fire; its defect was SIGKILL truncation, not swallowing.
- **Worker self-deadline (concern 1).** The detached worker enforces its own wall-clock deadline so it self-terminates rather than lingering (Risk 3). Add a named, env-overridable constant `HOOK_DETACH_DEADLINE_SECONDS` (default provisional/tunable — pick a value comfortably above the observed Haiku+`gh` round-trip cost, e.g. on the order of a couple minutes; mark it provisional per the magic-number convention). Implement the deadline as a `SIGALRM`/`signal.alarm` (or a monotonic-clock check around the round-trips) whose raised exception **subclasses `BaseException`** (spike-3) so `memory_bridge`'s broad `except Exception` cannot swallow it; on deadline the worker logs a `deadline-exceeded` line to `logs/hooks.log` and exits non-zero. Tested by build-tests-phase-a.
- **Concurrent-worker cap (concern 2).** An SDLC batch can end many turns near-simultaneously; without a cap, each `stop.py` would fan out its own Haiku/`gh` worker, unbounded. Before spawning, `stop.py` checks a bounded in-flight counter and refuses to spawn beyond `HOOK_DETACH_MAX_INFLIGHT` (named, env-overridable, small default e.g. 3–4, marked provisional). Implement the counter as a filesystem-visible bound that survives across independent `stop.py` processes (each `stop.py` is a fresh process, so an in-memory counter is useless): e.g. count live worker PID/lock files under an absolute state dir (create-on-write, cwd-independent like the log path) and skip-with-log when at the cap, releasing the slot when the worker exits. Over-cap invocations log a `detach-skipped: at capacity` line rather than silently dropping. Tested by build-tests-phase-a.

### 4. Generators + removal pass + RENAMED_REMOVALS hooks kind
- **Task ID**: build-generators
- **Depends On**: build-manifest
- **Validates**: tests/unit/test_update_hardlinks.py (rewrite 151-163)
- **Informed By**: spike-2, blast-radius (additive-only merge, dedupe-by-command-string)
- **Assigned To**: manifest-builder
- **Agent Type**: builder
- **Parallel**: false
- Implement `generate_project_hooks(manifest)` and rewrite `sync_user_hooks`/`_merge_hook_settings` to key on `manifest_id` with add/update/**remove**; add `"hooks"` kind to `RENAMED_REMOVALS` + `src_for_kind`.

### 5. Migration for Pre-requisite Bug 1
- **Task ID**: build-migration
- **Depends On**: build-generators
- **Validates**: tests/unit/test_hook_migration.py (create)
- **Informed By**: spike-1, Risk 1
- **Assigned To**: manifest-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data (idempotent migration record)
- **Parallel**: false
- Add idempotent migration to `scripts/update/migrations.py` `MIGRATIONS` that rewrites in place the **three deployed** legacy entries in `~/.claude/settings.json` via the legacy exact-command fallback (`validate_commit_message.py`, `sdlc_reminder.py` [single `Edit` block], `validate_sdlc_on_stop.py`) — attaching each `manifest_id`, upgrading `sdlc_reminder.py`'s matcher from `Edit` to `"Write|Edit"`, and adding the Stop `|| true` — so no legacy or duplicate entry survives; preserve non-SDLC blocks; write a timestamped `.bak` (tech-debt 4) then JSON-validity assertion before write. Seed the migration test with the **real deployed 3-entry shape** and assert the `sdlc_reminder.py` matcher outcome (`"Write|Edit"`) deliberately; do NOT synthesize a phantom 4th (Write) entry that never occurs in production.
- **Coordinate with the Step 7 name-collision rename (tech-debt 5):** the fallback's legacy literal for `validate_commit_message.py` must match the string **as currently deployed** (old name) on fleet machines, while the manifest emits the post-rename name — so the migration recognizes what is on disk and the generator writes the new name. Keep the two literals in lockstep.

### 6. Audit both scopes + agent-hooks scan
- **Task ID**: build-audit
- **Depends On**: none
- **Validates**: tests for `reflections/audits/hooks_audit.py`
- **Informed By**: Pre-requisite Bug 2
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend `hooks_audit.py` to read `~/.claude/settings.json` and scan `.claude/agents/*.md` hooks blocks; assert command paths exist and Stop hooks carry `|| true` in both scopes.

### 7. Dead code + reclassify.md + name collision
- **Task ID**: build-cleanup
- **Depends On**: none
- **Validates**: grep-based Verification rows
- **Informed By**: Dead-code table, spike-4B
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete the 7 dead validators per table; fix `.claude/skill-context/reclassify.md:15,22` and `docs/guides/valor-name-references.md`; resolve the `validate_commit_message.py` name collision.
- **Bug 4 shebang normalization:** on the validators that survive deletion, strip the misleading `uv run` shebang (line 1) so no surviving hook script advertises a `uv run` entry point that contradicts its bare-`python` registration. (The registration-side fix lives in the manifest, Step 1; this is the script-side cleanup.)
- **Name-collision rename coordination (tech-debt 5):** when renaming one of the two `validate_commit_message.py` files, update every reference in lockstep — the manifest entry (Step 1) and the migration's legacy exact-command literal (Step 5). The migration literal must keep the **old deployed** name; the manifest emits the **new** name. Grep for the old name across `.claude/`, `scripts/update/`, and `docs/` to confirm no dangling reference remains.

### 8a. Tests — Phase A (Stop detach; ships with Phase A)
- **Task ID**: build-tests-phase-a
- **Depends On**: build-stop-detach
- **Validates**: tests/unit/test_stop_detach.py (create)
- **Assigned To**: hook-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **This is Phase A's own test gate — it depends ONLY on build-stop-detach, so Phase A is independently mergeable (Review round 1) without waiting on any Phase B task.**
- Detach behavior: `stop.py` returns exit 0 immediately (does not block on extraction); the detached worker actually runs off the critical path.
- Drop-logged-not-swallowed: a forced extraction failure in the detached worker produces a log line in `logs/hooks.log` (observable behavior), exercising the rewritten `stop.py:225,242,262` handlers **and** `memory_bridge.py:922`'s existing `logger.warning`.
- **Foreign-repo log-path robustness (tech-debt 2):** run the worker with cwd outside this repo (no repo-relative `logs/`) and assert the drop still lands in the absolute, create-on-write log path.
- **Worker self-deadline (concern 1):** assert the detached worker terminates itself when it exceeds `HOOK_DETACH_DEADLINE_SECONDS` (env-overridable constant — see Step 3 and Risk 3); a worker whose extraction is forced to hang is reaped by its own deadline and logs a deadline-exceeded line, rather than lingering. The deadline exception must subclass `BaseException` (spike-3) so `memory_bridge`'s broad `except Exception` cannot swallow it.
- **Concurrent-worker cap (concern 2):** assert that when `HOOK_DETACH_MAX_INFLIGHT` detached workers are already running, a new `stop.py` invocation refuses to spawn an additional worker (skips extraction with a logged line) rather than fanning out unbounded Haiku/`gh` subprocesses under an SDLC batch.

### 8b. Tests — Phase B (registration consolidation)
- **Task ID**: build-tests-phase-b
- **Depends On**: build-manifest, build-dispatcher, build-generators, build-migration, build-audit
- **Assigned To**: hook-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Hook-sync tests (add/update/remove, idempotency, preservation), dispatcher crash-isolation + merge-guard-fail-closed, migration stale-entry-gone + non-SDLC-survives, empty-diff regeneration, both-scope path/`|| true` assertion.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests-phase-a, build-tests-phase-b
- **Assigned To**: hook-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/hook-manifest.md`; update hooks-best-practices, claude-code-memory, sdlc-enforcement, memory-hook-performance, README index, CLAUDE.md hook description.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all Success Criteria (both scopes), run Verification table, confirm empty-diff regeneration and near-zero Stop timeout rate on a session sample.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_update_hardlinks.py tests/unit/test_hook_manifest.py tests/unit/test_pre_tool_use_dispatcher.py tests/unit/test_stop_detach.py tests/unit/test_hook_migration.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Manifest exists | `test -f .claude/hooks/manifest.toml && echo ok` | output contains ok |
| Project hooks generated, not hand-edited | `python -c "from scripts.update.hardlinks import load_hook_manifest, generate_project_hooks; generate_project_hooks(load_hook_manifest())"` then `git diff --quiet .claude/settings.json && echo clean` | output contains clean |
| RENAMED_REMOVALS has hooks kind | `grep -c '"hooks"' scripts/update/hardlinks.py` | output > 0 |
| Migration registered | `grep -c 'hook' scripts/update/migrations.py` | output > 0 |
| Dead validators removed | `ls .claude/hooks/validators/validate_claude_config.py .claude/hooks/validators/validate_new_file.py .claude/hooks/validators/validate_plan_label.py .claude/hooks/validators/validate_type_immutability.py .claude/hooks/validators/validate_race_conditions.py .claude/hooks/validators/validate_tool_structure.py .claude/hooks/validators/validate_claude_md_updated.py 2>&1 \| grep -c 'No such file'` | output > 0 |
| reclassify.md no longer claims enforcement | `grep -c 'validate_type_immutability.py' .claude/skill-context/reclassify.md` | match count == 0 |
| Stop hook keeps guard in generated user scope (temp path — never reads the live operator file, so it is safe on a fresh checkout/CI) | `pytest tests/unit/test_hook_migration.py -k stop_guard_user_scope -q` — the test generates the user-scope settings into a `tmp_path` HOME and asserts every Stop hook command carries `\|\| true`; it MUST NOT `open(os.path.expanduser('~/.claude/settings.json'))` | exit code 0 |

## Critique Results

Critique verdict: **NEEDS REVISION** — three passes (recorded 2026-07-29). First pass: two build-blocking findings (B1, B2). Second (re-critique) pass: one new blocker (B3) + four concerns (C1–C4). Third (re-critique) pass: one new blocker (B4) + five concerns (C5–C9). This revision resolves all four blockers and addresses each concern below. The plan re-enters critique after this revision.

> Provenance note (**superseded by #2447**): an earlier revision of this plan proposed posting critique finding bodies as a comment on the tracking issue "going forward" to work around the war-room not persisting findings. That workaround is now obsolete — the CRITIQUE stage writes aggregated findings directly into this `## Critique Results` table in the same finalize block as the verdict record (the verdict-findings persistence contract; see `docs/features/sdlc-verdict-fail-closed-persistence.md`). The plan table is the single source of truth for critique findings; there is no separate issue-comment record to reconcile. B1/B2/B3/B4 and the re-critique concerns below are transcribed from the revision-dispatch briefs.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| **BLOCKER (B1)** | revision brief | Dangling "Pre-requisite Bugs" section — ~9 places cite "Pre-requisite Bug 1/2" but the section was never written, so the migration target was not enumerable. | New **## Pre-requisite Bugs** section (after Problem) | Itemizes all 4 sync-discipline bugs with re-verified file:line evidence: (1) unguarded user Stop hook `hardlinks.py:725` / `_SDLC_HOOK_DEFS:691-696`; (2) `hooks_audit.py:70` project-only; (3) no `hooks` kind in `RENAMED_REMOVALS` `hardlinks.py:14`; (4) `uv run` at `.claude/settings.json:114` vs `hooks.py:99` audit + 11 uv-run shebang validators. Success Criteria now trace to all 4 IDs. |
| **BLOCKER (B2)** | revision brief | Migration only fixed the Stop entry; the other legacy `_SDLC_HOOK_DEFS` entries (bare `python …/sdlc/<script>`, no `\|\| true`, no `manifest_id`) would be duplicated fleet-wide by the new `manifest_id`-keyed dedupe, which can't recognize them. | Technical Approach "Legacy exact-command fallback"; migration + Data Flow step 4; Risk 1; Success Criteria; Step 5 | Fallback matches the exact legacy command string for the **three deployed** entries, rewrites in place (attach `manifest_id` + Stop `\|\| true`); test asserts one entry per event, no duplicates. |
| **BLOCKER (B3, re-critique)** | revision brief | The B2 fix asserted **four** legacy entries (incl. both PostToolUse/Write and PostToolUse/Edit for `sdlc_reminder.py`), but `_merge_hook_settings` dedupes by identical command string, collapsing Write/Edit → only **three** entries deploy (matcher last-wins → `Edit`). Confirmed live: deployed `~/.claude/settings.json` has 3, not 4. The phantom-Write fallback found nothing to rewrite, appended, and re-created the fleet-wide duplication B2 was meant to eliminate; the guard test seeded a synthetic 4-distinct-command fixture that never occurs in production. | Technical Approach "Deployed-reality baseline" + rewritten "Legacy exact-command fallback"; Data Flow step 4; Solution; Risk 1; Update System; Success Criteria; Step 5 | Corrected to deployed reality (3 entries, Write collapsed under Edit). **Decision: FIX the root-cause dedup quirk, do not inherit** — manifest declares `sdlc_reminder.py` **once** with matcher `"Write|Edit"` (the form already used at `.claude/settings.json:73`); new generators key on `manifest_id`, so no collapse; migration upgrades the deployed `Edit` block to `"Write|Edit"` in place, restoring long-missing Write coverage. Migration test re-seeded to the real 3-entry shape and asserts the `sdlc_reminder.py` matcher outcome (`"Write|Edit"`) deliberately. |
| Concern C1 (re-critique) | revision brief | Verification row read the live operator `~/.claude/settings.json`, crashing on a fresh checkout/CI. | Verification "Stop hook keeps guard" row | Rewritten to a `pytest … -k stop_guard_user_scope` check that generates user-scope settings into a `tmp_path` HOME; MUST NOT open the live operator file. |
| Concern C2 (re-critique) | revision brief | 10-validator dead-code sweep + `uv run` shebang normalization ride along, uncoupled from the migration — split out or justify. | Scope Sequencing (Phase C) | Justified as adjacent (shebang = script-side twin of Bug 4's registration-side fix; dead-code deletion cleans the both-scope audit surface; rename coupled to migration literal) AND marked explicitly **non-gating** — Phase C may be deferred to a fast-follow without weakening Phases A/B. |
| Concern C3 (re-critique) | revision brief | Four independent workstreams bundled into one Large plan; `stop.py` detach (the acute 126/131 timeout fix) has no `Depends-On` edge and can ship first. | Scope Sequencing (Phase A/B/C) | Explicit 3-phase sequencing with distinct merge points: Phase A (stop.py detach, zero deps, ships/reviewed/merged first), Phase B (registration consolidation), Phase C (cleanup, non-gating). Plan stays one issue (shared manifest/audit surface) but builds in ordered phases. |
| Concern C4 (re-critique) | revision brief | Provenance gap: tech-debt rows are author reconstructions while `revision_applied:true` implies full resolution — persist critique finding bodies durably. | Provenance note (above); superseded by #2447 | The issue-comment workaround is superseded: CRITIQUE now persists finding bodies into this `## Critique Results` table at finalize time (verdict-findings persistence contract, #2447). No separate issue-comment record. |
| **BLOCKER (B4, 3rd pass)** | revision brief | Phase A "ships independently" is contradicted by the task graph: the single `build-tests` task depended on the full Phase B set (build-manifest, build-dispatcher, build-stop-detach, build-generators, build-migration, build-audit), and Steps 9–10 chained off it — gating Phase A's tests (and thus its merge) behind all of Phase B. | Scope Sequencing (Phase A); Step 8 split into 8a/8b; Step 9 deps | `build-tests` is split into **build-tests-phase-a** (`Depends On: build-stop-detach` only) and **build-tests-phase-b** (the Phase B set). Phase A now has its own test gate + Review round 1 merge point, genuinely independent of Phase B. document-feature now depends on both test tasks. |
| Concern C5 (3rd pass) | revision brief | Detached worker self-deadline asserted in Risk 3 but never specified or tested. | Step 3; Risk 3; Success Criteria; build-tests-phase-a | Added named env-overridable `HOOK_DETACH_DEADLINE_SECONDS` (provisional), implemented as a `BaseException`-subclassing `SIGALRM`/monotonic deadline that logs `deadline-exceeded`; test forces a hang and asserts the worker self-reaps. |
| Concern C6 (3rd pass) | revision brief | No cap on concurrent detached extraction subprocesses — an SDLC batch could fan out unbounded Haiku/`gh` workers. | Step 3; Risk 3; Success Criteria; build-tests-phase-a | Added named env-overridable `HOOK_DETACH_MAX_INFLIGHT` (small provisional default) enforced by a cwd-independent filesystem in-flight counter (live worker PID/lock files under an absolute state dir); over-cap invocations log `detach-skipped: at capacity`; test saturates the cap and asserts no extra worker spawns. |
| Concern C7 (3rd pass) | revision brief | `memory_bridge.py:922` mischaracterized as "bare except: pass" — source already logs via `except Exception as e: logger.warning(...)`; the real defect is SIGKILL truncation, and the "replace handler" instruction was wrong. | Problem; spike-3 impact; Failure Path Test Strategy; Step 3; Success Criteria | Corrected everywhere: `:922` is a real `logger.warning` handler, left intact; only `stop.py:225,242,262` are genuine bare swallows to replace; the true defect is SIGKILL truncation (process killed before the warning runs), which detaching resolves by removing the 10s wall. |
| Concern C8 (3rd pass) | revision brief | Success Criteria dead-code bullet not marked Phase-C non-gating. | Success Criteria (dead-code bullet) | Tagged **[Phase C — non-gating]** with an explicit note that it does not gate Phase A/B and defers as a unit (including the name-collision rename) if Phase C is deferred. |
| Concern C9 (3rd pass) | revision brief | Dead Code header said "10 confirmed dead" but the table lists 7 delete + 4 kept (the grep row correctly uses 7). | Dead Code to Remove header | Corrected to "7 confirmed dead (delete); 4 kept" and named all four keepers, matching the 7-file Verification grep row. |

---

## Open Questions

The five open questions the issue deferred to `/do-plan` are all resolved by spikes above (in-process dispatcher with per-validator isolation; keep `hook_edge` separate; detach Stop extraction; manifest owns timeouts; keep the load-bearing SDLC fork). Remaining supervisor-judgment items:

1. **Migration blast radius.** Bug 1's migration rewrites `~/.claude/settings.json` on every machine that runs `/update`. Confirm the migration should run automatically on the next `/update`, or whether it should be gated behind a one-time manual `/update` on a canary machine first.
2. **Manifest format lock-in.** TOML (stdlib `tomllib`) is recommended over per-file frontmatter. Any objection to a single `.claude/hooks/manifest.toml` versus, say, a JSON manifest that mirrors `settings.json` shape more directly?
3. **Dead-code aggressiveness.** The plan deletes 7 unregistered validators. Confirm none are intended as manually-invoked tools you rely on ad hoc (the recon marked the 4 "Do NOT delete" ones as keepers, but the 7 deletions are a judgment call).
