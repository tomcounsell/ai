---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-29
tracking: https://github.com/tomcounsell/ai/issues/2435
last_comment_id:
---

# Hook registration: per-event dispatcher + manifest-generated scopes

## Problem

Claude Code hooks block the agent loop while they run. Measured over the 50 most-recently-modified session transcripts (2026-07-23 → 2026-07-28), `stop.py` timed out **126 of 131 runs** (median 10,034 ms against a 10,000 ms wall), and `user_prompt_submit.py`, `pre_tool_use.py`, and four validators time out regularly too. The medians sit exactly on the configured timeout walls — the hooks routinely never finish; the harness SIGKILLs them. Because every layer of the Stop path is wrapped in bare `except: pass`, the killed work is **lost with no log line**.

Two structural facts make this un-patchable in place:

1. **`stop.py` does synchronous network I/O on the critical path.** Up to three Haiku round-trips plus two 10s-budgeted `gh` calls must fit inside a 10s wall, with zero backgrounding anywhere.
2. **Hook registration is 100% hand-maintained across two files.** `.claude/settings.json` holds 23 entries across 10 matcher groups (7 PreToolUse Bash validators alone → 7 processes per Bash call), and `~/.claude/settings.json` is written by a separate hardcoded `_SDLC_HOOK_DEFS` list. The two must stay in sync by discipline alone — and that discipline has already failed in four measurable ways (see Pre-requisite Bugs).

**Current behavior:** expensive hooks block ~15s/prompt, ~7s/Bash call, ~10s/turn-end; the most expensive one is killed before finishing and drops its work silently. Adding, removing, or making a hook non-blocking requires hand-editing two files that no test covers.

**Desired outcome:**
1. Blocking hooks stop blocking: expensive Stop work is detached and completes off the critical path; any drop is logged, not swallowed.
2. Hook registration is generated from a single declaration; neither `settings.json` `hooks` block is hand-edited.
3. One dispatcher entry per event (PreToolUse Bash spawns one process, not seven).
4. `/update`'s project→user propagation covers hooks the same way it covers skills, including removals.

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
- **Impact on plan**: **Detach** the Stop Haiku/`gh` extraction to a subprocess that owns its own logging (writes to `logs/hooks.log`). `stop.py` exits 0 immediately. Any failure inside the worker is logged by the worker, satisfying the criterion. Remove the bare `except: pass` swallowing at `stop.py:225,242,262` and `memory_bridge.py:922` in the detached path — replace with logged handlers.

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
4. **User generator**: `sync_user_hooks(manifest)` hardlinks `scope == global` scripts into `~/.claude/hooks/` and projects their entries into `~/.claude/settings.json` via a rewritten `_merge_hook_settings` that now has a **removal pass** (deletes manifest-owned entries no longer declared) keyed by a stable `manifest_id`, not exact command string.
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
- **Migration**: rewrites/removes the legacy unguarded Stop command string in `~/.claude/settings.json` (Pre-requisite Bug 1) rather than additively appending a second entry.

### Flow

`/update` runs → `load_hook_manifest()` → `generate_project_hooks()` rewrites `.claude/settings.json` → `sync_user_hooks()` hardlinks global scripts + rewrites `~/.claude/settings.json` (add/update/remove) → migration sweeps legacy unguarded entry → `hooks_audit` (both scopes) passes → services restart.

At runtime: Bash tool call → **one** dispatcher process runs 7 predicates → first block wins (or all allow). Turn ends → `stop.py` spawns detached extraction, exits 0 → worker completes Haiku extraction off-path, logs result.

### Technical Approach

- **Manifest is authoritative for static registration only.** `hook_edge` (session scope) and agent-declared hooks (`.claude/agents/*.md`) are separate declared surfaces the generator/audit *reads* but does not *own*.
- **Dedupe/removal key = `manifest_id`.** Today `_merge_hook_settings` dedupes by exact command string (`hardlinks.py:742`), which is why Bug 1's fix can't be a simple string append (it would leave the old entry and add a second). Switch the key to a stable `manifest_id` embedded in each generated command (e.g. a trailing `# hook:<id>` comment or a companion index) so add/update/remove all key off identity, not the volatile command string.
- **Dispatcher preserves each validator's existing fail posture.** Standalone validators fail-open on internal error (log + continue); `validate_merge_guard` stays fail-closed (its block must survive). Encode this per-validator, not as a blanket policy.
- **Detach must be a subprocess/double-fork**, not a thread — verified in spike-3.
- **Timeouts live in the manifest**, not `TimeoutSettings` — verified in spike-4A.
- **Dead-code removal** (see table) and the `validate_commit_message.py` name collision resolution ride along.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `stop.py:225,242,262` and `memory_bridge.py:922` bare `except Exception: pass` blocks: in the detached worker path, replace with handlers that log to `logs/hooks.log`. Add a test asserting a forced extraction failure produces a log line (observable behavior), not silence.
- [ ] Dispatcher per-validator `try/except`: add a test that injects a raising validator and asserts (a) the remaining validators still run, (b) the crash is logged via `log_hook_error`, (c) a fail-closed validator (merge-guard) raising still blocks.

### Empty/Invalid Input Handling
- [ ] `load_hook_manifest()` on an empty/malformed `manifest.toml`: assert it raises a clear error (fail-closed for generation — a broken manifest must not silently wipe the hooks block).
- [ ] Dispatcher receiving empty/whitespace stdin JSON: assert it allows (no spurious block) and does not crash.
- [ ] `_merge_hook_settings` with an empty existing `~/.claude/settings.json`: assert it writes a valid block.

### Error State Rendering
- [ ] Detached extraction worker failure is user-invisible by design, but MUST appear in `logs/hooks.log`. Test asserts the log line exists on failure.
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
**Mitigation:** Migration is idempotent, recorded once in `data/migrations_completed.json` (register in `scripts/update/migrations.py` `MIGRATIONS`). It removes only the specific legacy unguarded command string (identified by `manifest_id`/known literal), preserves all non-manifest blocks (the `hardlinks.py:706` "never clobbers non-SDLC hooks" promise), and writes via a parse→modify→serialize round-trip with a JSON-validity assertion before write. Unit test proves a pre-existing non-SDLC user hook survives.

### Risk 2: In-process dispatcher converts 7 isolated fail-opens into one shared crash surface
**Impact:** A validator raising mid-dispatch could skip every validator after it, silently losing merge-guard/redis-guard enforcement.
**Mitigation:** Per-validator `try/except` (spike-1). Test injects a raising validator and asserts the rest still run and the fail-closed merge-guard still blocks.

### Risk 3: Detached Stop subprocess orphans or races the next turn
**Impact:** A wedged detached worker could linger; extraction may still run when the next turn starts.
**Mitigation:** Worker reads the already-persisted `transcript.jsonl` (stable input, no race). Worker carries its own internal deadline so it self-terminates rather than lingering. Detach via `Popen`/double-fork with `start_new_session=True` and redirected streams (calendar-hook pattern), never a daemon thread.

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
- `RENAMED_REMOVALS` gains a `"hooks"` kind; `src_for_kind` (`hardlinks.py:473-477`) gains a `hooks` mapping.
- A migration in `scripts/update/migrations.py` (`MIGRATIONS` dict) rewrites the legacy unguarded Stop entry in `~/.claude/settings.json`, idempotent and recorded in `data/migrations_completed.json`.
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

Per recon of the 11 unregistered scripts under `.claude/hooks/` (10 confirmed dead; `format_file.py` is live via `builder.md:10` — keep):

| Verdict | Files |
|---|---|
| **Safe to delete** | `validate_claude_config.py`, `validate_new_file.py`, `validate_tool_structure.py`, `validate_claude_md_updated.py` (last two: one-line edit to `docs/guides/valor-name-references.md`) |
| **Delete + fix docs** | `validate_race_conditions.py`, `validate_plan_label.py`, `validate_type_immutability.py` — also fix `.claude/skill-context/reclassify.md:15,22` (it falsely claims two of these block edits) |
| **Do NOT delete** | `format_file.py` (live agent hook), `validate_issue_recon.py` (invoked by `docs/sdlc/do-plan.md:31`), `validate_verification_section.py` (passing unit test), `validate_knowledge_base_section.py` (documented manual soft validator) |

## Success Criteria

- [ ] Pre-requisite Bug 1 fixed with a **migration that removes** the legacy unguarded command string from `~/.claude/settings.json` (not an additive rewrite); a test proves the stale entry is gone and non-SDLC hooks survive.
- [ ] `hooks_audit.py` audits user-scope `~/.claude/settings.json` in addition to the project file, and scans `.claude/agents/*.md` hooks blocks.
- [ ] `RENAMED_REMOVALS` gains a `"hooks"` kind with removal propagation verified by test.
- [ ] Both scopes' `hooks` blocks are generated from `.claude/hooks/manifest.toml`; regeneration on an unchanged manifest yields empty `git diff`; neither block is hand-edited.
- [ ] One dispatcher entry per event; PreToolUse Bash spawns one process, not seven; a raising validator does not skip the rest, and merge-guard stays fail-closed.
- [ ] `stop.py` no longer times out: over a session sample, Stop timeout rate near zero; any dropped extraction is logged to `logs/hooks.log`, not swallowed.
- [ ] Tests cover `sync_user_hooks()`, `_merge_hook_settings()` (add/update/**remove**), and the manifest generator (currently untested; `test_update_hardlinks.py:151-163` sidesteps them).
- [ ] A test asserts every registered command path exists and every Stop hook carries `|| true`, in both scopes.
- [ ] Dead validators removed per the table; `.claude/skill-context/reclassify.md` no longer claims enforcement that does not exist; `validate_commit_message.py` name collision resolved.
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
- Spawn detached extraction subprocess (`Popen`/double-fork, `start_new_session=True`, redirected streams); `stop.py` exits 0 immediately; worker logs drops to `logs/hooks.log`; remove bare `except: pass` in the detached path.

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
- Add idempotent migration to `scripts/update/migrations.py` `MIGRATIONS` that removes the legacy unguarded Stop command from `~/.claude/settings.json`; preserve non-SDLC blocks; JSON-validity assertion before write.

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

### 8. Tests
- **Task ID**: build-tests
- **Depends On**: build-manifest, build-dispatcher, build-stop-detach, build-generators, build-migration, build-audit
- **Assigned To**: hook-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Hook-sync tests (add/update/remove, idempotency, preservation), dispatcher crash-isolation + merge-guard-fail-closed, migration stale-entry-gone + non-SDLC-survives, empty-diff regeneration, both-scope path/`|| true` assertion.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
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
| Stop hook keeps guard in generated user scope | `python -c "import json,os; d=json.load(open(os.path.expanduser('~/.claude/settings.json'))); import sys; sys.exit(0 if all('|| true' in h['command'] for g in d.get('hooks',{}).get('Stop',[]) for h in g.get('hooks',[])) else 1)"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

The five open questions the issue deferred to `/do-plan` are all resolved by spikes above (in-process dispatcher with per-validator isolation; keep `hook_edge` separate; detach Stop extraction; manifest owns timeouts; keep the load-bearing SDLC fork). Remaining supervisor-judgment items:

1. **Migration blast radius.** Bug 1's migration rewrites `~/.claude/settings.json` on every machine that runs `/update`. Confirm the migration should run automatically on the next `/update`, or whether it should be gated behind a one-time manual `/update` on a canary machine first.
2. **Manifest format lock-in.** TOML (stdlib `tomllib`) is recommended over per-file frontmatter. Any objection to a single `.claude/hooks/manifest.toml` versus, say, a JSON manifest that mirrors `settings.json` shape more directly?
3. **Dead-code aggressiveness.** The plan deletes 7 unregistered validators. Confirm none are intended as manually-invoked tools you rely on ad hoc (the recon marked the 4 "Do NOT delete" ones as keepers, but the 7 deletions are a judgment call).
