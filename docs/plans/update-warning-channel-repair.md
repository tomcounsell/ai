---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2845
last_comment_id: 5317246173
---

# /update Warning Channel Repair

## Problem

`/update` runs every 30 minutes under `com.valor.update` on every machine in the fleet. When it finds something wrong, four independent defects conspire so that nobody — human or agent — reliably learns what it found, and the one warning that *is* legible tells the operator to do something that would take the fleet down.

**Current behavior:**

A run on "Valor the Bald" at `4d9118125` reported 3 warnings. The auto-spawned fix session received a payload cut off mid-word at exactly 500 characters (`(or: gws auth setup && gws`), showing part of warning #1 and none of #2 or #3. The same SHA on "Valor the Captain" reports 2 of the same warnings (`gws auth` is clean there), confirming the condition is fleet-wide, not machine-local.

1. **`_queue_fix_session` truncates at 500 chars.** `bridge/update.py:120-121` builds the fix session's whole brief from `stdout[:500]` / `stderr[:500]`. The npm/git-pull preamble eats ~370 of those 500 characters, so the warning list — the entire reason the session exists — is what gets cut.
2. **`has_warnings` cannot see the cron summary.** `bridge/update.py:291` scans for line prefixes `("[update] WARN", "WARNING:", "ERROR", "RESTART FAILED")`. `run.py --cron` emits `up to date at <sha> (N warnings)` followed by indented `  ⚠️ <text>` bullets (`scripts/update/run.py:2487-2492`), and on failure `update failed at <sha>` followed by `  - <err>`. **None of those four forms matches any prefix.** In the observed run a fix session was queued only because `scripts/update/verify_release.py:147` incidentally printed `WARNING: <name> release could not be confirmed (unknown)`. An `/update` with real warnings and a clean release-verify queues no fix session at all.
3. **`env-completeness` reports 27 false positives, and its documented remediation is destructive.** `check_env_completeness` (`scripts/update/verify.py:1088-1134`) does a flat `declared_keys - present` set difference with no notion of required vs optional. All 27 flagged keys are behaviour toggles with in-code defaults, not secrets. Worse, `docs/features/env-completeness-validation.md:61` instructs the operator to *"edit `~/Desktop/Valor/.env` directly"* — and 11 of the 27 are declared in `.env.example` with an **empty** value, so following that instruction literally means writing `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS=` into the shared iCloud vault, which fails `Settings()` construction at import:

   ```
   pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
   features.crash_autoresume_max_attempts
     Input should be a valid integer, unable to parse string as an integer
     [type=int_parsing, input_value='', input_type=str]
   ```

   `config/settings.py` is imported by the bridge, the worker, and every `tools.*` CLI, and `.env` is a symlink to the one vault file every machine shares. The check's own published remediation, applied to a key the check itself names, takes the fleet down. Two more of the 27 (`DISK_RECLAIM_APPLY`, `MEMORY_DECAY_PRUNE_APPLY`) are irreversible-destructive arming flags whose own descriptions read `Default: unset/false (dry-run)` — the warning is nagging the operator to set a key that documents itself as "leave unset".

   The emitted text is also unusable: `_parse_env_example` takes the **last** comment line of a block as the description, which for wrapped multi-line comments is a sentence fragment (`HOOK_DETACH_MAX_INFLIGHT (foreign repos too.)`), and 27 of them concatenate into a ~2100-character single-line warning.

4. **Two permanently-unresolvable warnings bypass `warn_state`.** `warn_state.should_emit` collapses a human-gated check to one emission per state transition; `google-token` and `sms_reader` use it (`scripts/update/run.py:2275`). `gws auth` (`run.py:1029`) and `Redis ACL drift` (`run.py:1494`) do not, so they re-emit 48×/day forever. Both are genuinely human-gated: `gws` needs browser OAuth consent, and the Redis ACL apply is double-gated behind `data/redis-acl-enabled` + `REDIS_ACL_APPLY=true` which `/update` never supplies by design (#2645 Risk 8).

Beyond the filed issue, the diagnosis surfaced a fifth, adjacent defect: **three `.env.example` declarations have no reader anywhere in the codebase**, and two feature docs describe them as live controls. A required/optional split alone would silently relabel this cruft as legitimate configuration.

**Desired outcome:**

An `/update` warning reliably reaches a human or an agent, in full, exactly once per state transition; `env-completeness` reports only genuinely missing required values; and no declared env key exists without a reader.

## Freshness Check

**Baseline commit:** `4d9118125`
**Issue filed at:** `2026-08-17T15:04:19Z`
**Disposition:** Minor drift

**File:line references re-verified:**

- `bridge/update.py:291` — `_warning_prefixes` / `has_warnings` — **still holds, verbatim**.
- `bridge/update.py:120-121` — `stdout[:500]` / `stderr[:500]` in `_queue_fix_session` — **still holds, verbatim**.
- `scripts/update/verify.py:1088-1134` — `check_env_completeness` flat set difference — **still holds**.
- `scripts/update/run.py:1494` — Redis ACL drift warning — **drifted to 1480-1497**; the `result.warnings.append` is at 1494-1497. Claim holds.
- `scripts/update/run.py:2275` — `warn_state` usage for `google-token` / `sms_reader` — **still holds** (the `human_gated_tools` loop at 2266-2290).
- `bridge/context_recall.py:48` — `CONTEXT_RECALL_HISTORY_DEPTH` default `"10"` — **still holds**.
- `config/settings.py:978` — `SDLC_REVIEW_CROSS_VENDOR` — **drifted to 974**. Claim holds.
- `config/settings.py:892` — `WORKER_SUPERVISOR_MAX_RESTARTS` — **drifted to 885**; the live read is `worker/__main__.py:90`, with the settings field a mirror. Claim holds.
- `.claude/hooks/hook_utils/detach_lock.py:28` — `HOOK_DETACH_DEADLINE_SECONDS` — **drifted to 74** (`DEFAULT_DEADLINE_SECONDS = 120` constant near the top, read at 74). Claim holds.
- `tools/disk_reclaim.py:16` — `DISK_RECLAIM_APPLY` — **drifted to 50 and 122** (`apply_armed()`). Claim holds.

**Cited sibling issues/PRs re-checked:**

- **#2645** (Redis flush hardening) — merged; `scripts/update/redis_acl.py` and `docs/features/redis-flush-hardening.md` are present and match the issue's description of the report-only posture.
- **#2661** (`REDIS_URL` rotation) — still open, still fleet-gated on every machine having run the ACL runbook. Unchanged as a blocker for the ACL apply.
- **#2329 / #2328** — closed; `scripts/update/warn_state.py` exists and is wired for exactly two checks. The precedent this plan extends.
- **#2494** (durability plan) — merged; it is what deleted the `session_events` count-based trim, orphaning `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES`.
- **#2831** — the "documented env control with no reader" defect class; its guard (`tests/unit/test_review_judge_env_docs.py`) is scoped to `SDLC_REVIEW_*` only.

**Commits on main since issue was filed (touching referenced files):** none. `git log --since=2026-08-17T15:04:19Z -- scripts/update/verify.py bridge/update.py scripts/update/run.py .env.example` returns empty; the repo is at the same SHA the issue cites.

**Active plans in `docs/plans/` overlapping this area:** none. No plan references #2845, `env-completeness`, `warn_state`, or the update warning channel.

**Notes:** All drift is line-number-only from unrelated edits; every claim in the issue survives re-verification. The corrected line numbers above are what the Technical Approach uses. Two claims were *strengthened* during re-verification: the empty-value hazard (Finding A, reproduced live) and the three dead declarations (Findings B/C), both recorded in issue comment 5317246173.

## Prior Art

- **#1140**: `.env.example`: add per-variable comments + update-time completeness check — **this is the check being fixed**. It shipped the comment convention and `check_env_completeness`. It succeeded at its stated goal (surfacing keys declared but absent) but encoded a premise that was true then and false now: that everything in `.env.example` is a value a machine ought to hold. The file has since accumulated behaviour toggles, apply-gates, and per-machine switches. This plan does not revert #1140; it adds the missing required/optional axis.
- **#2329**: Missing Google OAuth token — `/update` warns every 30 minutes with no resolution path — **closed by building `scripts/update/warn_state.py`**. Established the "one emission per state transition" pattern for human-gated checks. This plan extends the same mechanism to two more checks rather than inventing a second one.
- **#2328**: No Full Disk Access granted on this machine — the sibling of #2329, second consumer of `warn_state`.
- **#1968**: Centralize magic timeout/retry/TTL literals into `config/settings.py`, and audit settings + `.env` for cleanup — the migration that produced most of the `FEATURES__*` and tunable declarations now being flagged. Its convention ("promote a tunable, declare it in `.env.example`") is precisely what collides with #1140's check.
- **#2025**: Clear two npm warnings during `/update` — same "make `/update` quiet so its warnings mean something" motivation, different subsystem. Precedent that a persistently-noisy `/update` is treated as a defect here, not as background.
- **#2073**: `sms_reader` CLI leaks a raw traceback instead of an actionable error (`/update` warning noise) — same class again.
- **#1934**: Merge-gate shape classifier: admit `.env.example` into the docs-only allowlist — relevant because this plan edits `.env.example` substantially; the classifier treats it as docs-shaped.

No merged PR search results for `warn_state env-completeness` — the `gh pr list --search` returned empty, so prior work is tracked through the issues above.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1140 | Added per-variable comments to `.env.example` and the `check_env_completeness` diff | Encoded "declared in `.env.example`" as equivalent to "required in `.env`". That equivalence held when the file held only secrets. It broke silently as #1968 and successors added tunables to the same file, and the check has no vocabulary to express the difference. |
| #2329 / #2328 | Built `warn_state.should_emit` and wired it into `google-token` and `sms_reader` | Solved the respam problem *for two call sites* by wiring at the call site rather than at the warning-emission boundary. Every human-gated check added since (`gws auth`, Redis ACL drift) had to remember to opt in, and neither did. |
| #1898 | Made `_queue_fix_session` scan all stdout lines rather than just the first | Fixed the *detection breadth* but left the *payload width* at 500 chars and left the prefix list frozen against a summary format that `run.py --cron` later introduced. |

**Root cause pattern:** in all three, the fix was applied at a specific call site instead of at the boundary every producer crosses. `/update` has no single "emit a warning" seam — warnings are appended to `result.warnings` from ~20 places and rendered in two different formats — so each new producer silently re-opens whichever hole the last fix closed. This plan does not attempt to build that seam (see No-Gos); it makes the two *consumers* (`has_warnings`, `_queue_fix_session`) format-aware and parses the rendered output, which is the one surface every producer provably reaches.

## Architectural Impact

- **New dependencies**: none. `warn_state`, `redis_acl`, and `verify` are all existing first-party modules.
- **Interface changes**: `_parse_env_example` gains a third tuple element (or returns a small record) carrying the optional flag — an internal helper with two callers, both in `verify.py` and its tests. `bridge/update.py` gains one pure module-level helper, `extract_update_warnings(status_lines) -> list[str]`, exported for tests.
- **Coupling**: decreases slightly. Today `bridge/update.py` knows an ad-hoc prefix tuple that duplicates knowledge of `run.py`'s output format implicitly; after this, the two summary formats are named and parsed in one place with a test that pins them against `run.py`'s actual emission.
- **Data ownership**: unchanged. `warn_state`'s JSON state file (`data/`, gitignored) gains two more keys.
- **Reversibility**: high. Every change is additive or a marker convention; reverting the `# @optional` annotations restores the old (noisy) behaviour with no data migration.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (confirming the scope decision to fix the whole issue rather than only the two warnings observed on this machine)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Vault `.env` readable | `python -c "from pathlib import Path; assert (Path.home()/'Desktop'/'Valor'/'.env').exists()"` | `check_env_completeness` tests read the real declaration surface |
| `redis-cli` on PATH | `command -v redis-cli` | The Redis ACL drift path is exercised report-only in tests |

## Data Flow

1. **Entry point**: `com.valor.update` launchd cron (every 30 min) or a Telegram `/update` message, both landing in `bridge/update.py::handle_update_command`.
2. **`scripts/remote-update.sh` → `scripts/update/run.py --cron`**: runs ~30 steps, each appending human-readable strings to `result.warnings`. Two of those appends are the ones this plan gates (`run.py:1029` gws auth, `run.py:1480-1497` Redis ACL drift); one is the `valor_tools` loop at `run.py:2266-2290` that surfaces `check_env_completeness`.
3. **`run.py:2482-2494` renders the summary**: `update failed at {sha}` + `  - {err}` lines, or `{updated to|up to date at} {sha} ({N} warning{s})` + `  ⚠️ {warn}` lines, or `update successful`. This rendered text is stdout.
4. **`bridge/update.py:291` classifies**: `has_warnings` prefix-scans `status_lines`. *This is where the signal is currently lost.*
5. **`bridge/update.py::_queue_fix_session`**: builds a `message_text` from `stdout[:500]`/`stderr[:500]` and enqueues an AgentSession with `project_key="ai"`, `priority="low"`. *This is where the surviving signal is truncated.*
6. **Output**: an Eng session wakes with the brief, plus a Telegram message to the originating chat.

The two lossy hops (4 and 5) are adjacent and consume the same `status_lines`/`stdout` values, which is why one shared parser fixes both.

## Solution

### Key Elements

- **`extract_update_warnings(status_lines)`** — a pure helper in `bridge/update.py` that recognises every format `run.py` actually emits (the `(N warnings)` summary and its `⚠️` bullets, the `update failed at` summary and its `-` bullets, and the four legacy line prefixes) and returns the warning texts. Both lossy hops consume it.
- **Warning-first fix-session payload** — `_queue_fix_session` leads with the complete extracted warning list, never truncated; the raw stdout/stderr tail follows under a much larger cap and is cut on line boundaries, never mid-word. When `data/update.txt` exists, its path is included so the session can read the full log.
- **A required/optional axis for `.env.example`** — an explicit `# @optional` marker in a key's comment block. Unmarked means required, so a newly-added secret still warns: the fail-closed direction.
- **Deletion of dead declarations** — three keys with no reader are removed from `.env.example`, and the two feature docs that describe them as live are corrected.
- **A recurrence guard** — a test asserting every `.env.example` declaration has a reader, so the dead-key class cannot silently return.
- **`warn_state` for the two human-gated warnings** — `gws auth` and Redis ACL drift emit once per state transition and once again on resolution, matching `google-token` / `sms_reader`.

### Flow

`/update` cron fires → run.py renders a summary → **`extract_update_warnings` parses it** → warnings found → **fix session receives the full list up front** → agent reads warnings → agent fixes or reports → next run's state differs → **`warn_state` lets the changed warning through**

### Technical Approach

**Defect 2 first** (it is the dependency): add `extract_update_warnings(status_lines: list[str]) -> list[str]` to `bridge/update.py` as a module-level pure function. It must recognise:

- `  ⚠️ <text>` bullets (strip, then match the sentinel) — the cron warning form.
- The `(N warnings)` / `(N warning)` summary line, used to cross-check the bullet count; a mismatch is itself worth surfacing rather than silently trusting either.
- `  - <text>` bullets that follow an `update failed at <sha>` line — the cron failure form. These are *state-dependent*: a bare `  - ` line elsewhere in the log (npm output, git diffstat) must not match, so the parser tracks whether it is inside a failure block rather than matching the bullet in isolation.
- The four existing prefixes `("[update] WARN", "WARNING:", "ERROR", "RESTART FAILED")`, kept verbatim — the non-cron `--verify` path still emits them and #1898's over-match analysis still applies (line-anchored, never bare substring).

`has_warnings` becomes `bool(extract_update_warnings(status_lines))`. Keep the existing `failed` short-circuit untouched.

**Defect 1**: rewrite `_queue_fix_session`'s message assembly to put the extracted warning list first, in full, with no cap. The stdout/stderr tail moves below it under a cap raised to a few thousand characters and applied by whole lines (`"\n".join(lines[-K:])` shape) so nothing is ever cut mid-word. Pass the warning list in as a parameter rather than re-deriving it — the caller already has it. Include the `data/update.txt` path when that file exists, so a session that needs more can read the whole log rather than guess.

**Defect 3**, in three parts:

1. *Parser.* `_parse_env_example` gains optional-marker recognition. The marker is the literal token `@optional` appearing on its own comment line in a key's block. It must be a token that cannot occur in prose — `.env.example` already contains six prose uses of the word "optional" (`# OpenAI API Key (Optional - ...)`, `# Optional. GitHub PAT for ...`), and one of those begins the line, so a bare `^optional\b` match would silently mark a required credential optional. The `@` sigil is what makes the match safe. The marker line must be excluded from description candidates so it never becomes the printed description.

2. *Description quality.* Switch the description to the **first** non-empty comment line of the block instead of the last. For single-line comments (the common case) this is identical; for wrapped blocks it yields the topic sentence instead of a fragment. Update `docs/features/env-completeness-validation.md`, which currently specifies "last", and the tests that pin it. Additionally cap the rendered warning: list at most 5 keys inline and append `(+N more — run <command> for the full list)` so a regression can never again produce a 2100-character single-line warning.

3. *Annotation and deletion.* Mark the 26 verified-optional keys with `# @optional`. Delete `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES` (`.env.example:446`), `OLLAMA_URL` (`:197`), and `OLLAMA_VISION_MODEL` (`:201`) outright, and correct `docs/features/headless-session-runner.md:165` (which documents a cap that #2494 deleted) and `docs/features/config-architecture.md:46` (which lists `OLLAMA_VISION_MODEL` as live with a default that disagrees with `.env.example`'s). Per Development Principle 1 these are deletions, not deprecations. Document the `@optional` convention in `.env.example`'s header block and in CLAUDE.md's Secrets section, and fix the destructive remediation text at `docs/features/env-completeness-validation.md:61`.

**The recurrence guard**: a test that every key declared in `.env.example` is read somewhere. The subtlety is pydantic-settings nested keys: `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS` never appears literally in the source — it resolves to `FeatureSettings.crash_autoresume_max_attempts` via the `FEATURES__` env prefix. So "has a reader" means: the literal key appears in a tracked file outside `.env.example` itself, **or** the key decomposes as `<PREFIX>__<FIELD>` where a settings model registered under `env_prefix="<PREFIX>__"` declares `field`. Resolve the second case by introspecting the `Settings` model's nested submodels rather than by maintaining a hand-written allowlist — an allowlist is how this defect class survives.

**Defect 4**: route `run.py:1029` (gws auth) and `run.py:1480-1497` (Redis ACL drift) through `warn_state.should_emit`, mirroring the `human_gated_tools` block at `run.py:2266-2290` including its resolution branch. The signature must encode the *content* of the drift, not just its presence, so that a change in drift re-warns: for the ACL use a stable digest of the planned commands with the password placeholder already substituted (the placeholder is what the report path emits, so no secret can reach the state file); for gws use the auth-method string. Suppression is safe because `python -m tools.doctor`'s `redis_acl` and `gws` checks remain unconditional, on-demand surfaces — the plan reduces *repetition*, never *availability*.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/update.py::_queue_fix_session` wraps its body in `except Exception` and logs a warning (`bridge/update.py:136-137`). The rewritten body must keep that contract; add a test asserting that a raising `enqueue_agent_session` produces a `logger.warning` and does not propagate.
- [ ] `scripts/update/run.py:1508-1511` catches every exception around the Redis ACL step and appends a warning. The `warn_state` wiring must not move work outside that guard; add a test that a raising `should_emit` still leaves the update run successful.
- [ ] `check_env_completeness` catches `OSError` and returns `skipped (read error)` (`verify.py:1136-1141`). The new marker parsing must stay inside that guard.

### Empty/Invalid Input Handling
- [ ] `extract_update_warnings([])` returns `[]`; `extract_update_warnings` on whitespace-only lines returns `[]`; on a `update successful` summary returns `[]`.
- [ ] `_parse_env_example` on a key with no comment block at all yields `description=""` and `optional=False` (required — the fail-closed default).
- [ ] `_queue_fix_session` with an empty warning list still produces a usable brief (the `failed` path can have zero parsed warnings).

### Error State Rendering
- [ ] The `(N warnings)` count and the number of extracted bullets must agree; a test covers the mismatch path and asserts the discrepancy is surfaced rather than swallowed.
- [ ] A test asserts the fix-session `message_text` contains the *last* warning of a 27-warning list — the specific regression from the issue, where only warning #1 partially survived.

## Test Impact

- [ ] `tests/unit/test_env_completeness.py` — UPDATE: the 14 existing tests pin `_parse_env_example`'s 2-tuple return and the last-comment-line description rule. Both change. Update the tuple unpacking and flip the multi-line-comment-block test's expectation from last line to first line.
- [ ] `tests/unit/test_review_judge_env_docs.py` — UPDATE: it guards the `SDLC_REVIEW_*` slice of the dead-key class. Keep it, and add the general guard alongside rather than replacing it, so the narrow assertions stay as documentation of #2831.
- [ ] `tests/unit/test_ollama_consolidation.py` — no change expected; its `assert not hasattr(settings.models, "ollama_vision_model")` is what proves `OLLAMA_VISION_MODEL` is dead. Verify it still passes after the `.env.example` deletion.
- [ ] Any test asserting on `bridge/update.py::_warning_prefixes` or `has_warnings` — locate via `grep -rn "_warning_prefixes\|has_warnings" tests/`; disposition UPDATE if found, since the tuple moves inside the new helper.
- [ ] `tests/unit/test_redis_acl.py` — no change expected: the apply-gate matrix is untouched. Verify the report-only call site assertion (which pins the literal `apply_redis_acl()` call with no arguments) still passes after the `warn_state` wrapping, since the wrapping is around the *warning emission*, not the call.

## Rabbit Holes

- **Building a unified "emit a warning" seam in `run.py`.** ~20 call sites append to `result.warnings` with free-form strings. Structuring them into typed warning records with severity and a human-gated flag is the architecturally correct fix and is a separate project. This plan parses the rendered output instead, which is the surface every producer already reaches.
- **Auditing all 89 `.env.example` declarations for optionality.** Only the 27 currently-flagged keys need classification; the other 62 are present in the vault and the check says nothing about them either way. Unmarked defaults to required, so leaving them unmarked is correct, not a half-migration. Resist the urge to sweep.
- **Making `@optional` inferable from prose.** Every one of the 27 says "Provisional/tunable" or "default: N" in its comment. Regex-matching that prose would be keyword matching (Development Principle 3) and would misfire on the six existing prose uses of "optional". The explicit sigil is the whole point.
- **Removing `OLLAMA_URL` / `OLLAMA_VISION_MODEL` from the vault `.env` itself.** They are inert there. The vault is a private, iCloud-synced, human-owned file; this plan removes the *declarations* that advertise them as real.
- **Fixing the `gws auth` state on Valor the Bald.** It is genuinely unauthenticated and needs browser OAuth consent. Out of scope by CLAUDE.md; this plan only stops the respam.

## Risks

### Risk 1: `# @optional` is applied to a key that is genuinely required
**Impact:** The check goes quiet for a real missing secret — the exact failure #1140 existed to prevent, now silent instead of noisy.
**Mitigation:** Only the 26 keys with a verified read site and an in-code default get the marker; each was traced to a `file:line` with its default quoted. Unmarked is required, so the failure mode of *forgetting* the marker is a spurious warning (loud, cheap) rather than a missed secret (silent, expensive). The recurrence-guard test additionally proves every declared key has a reader, so a marker can never be hiding a key that does nothing.

### Risk 2: `extract_update_warnings` over-matches and spawns spurious fix sessions
**Impact:** Every green `/update` queues a low-priority Eng session — 48/day/machine of wasted agent time. This is exactly the regression #1898 fixed.
**Mitigation:** Keep every match line-anchored, never a bare substring scan. The `  - <text>` failure bullets match only inside a failure block, tracked by parser state, because that bullet shape occurs freely in npm and git output. A test feeds a realistic green `/update` transcript (npm output, git diffstat with filenames containing "error" and "warning") and asserts zero extracted warnings.

### Risk 3: `warn_state` suppression hides a *newly regressed* Redis ACL state
**Impact:** The ACL is applied, later reverted or drifts differently, and `/update` stays silent because it already warned once.
**Mitigation:** The signature is a digest of the planned commands, not a constant, so any change in the drift's *content* is a new signature and re-emits. The resolution branch (mirroring `run.py:2266-2290`) clears stored state and emits one resolved note, so a later regression warns again. `tools.doctor` remains an unconditional on-demand check.

### Risk 4: The `.env.example` edit collides with the merge-gate shape classifier
**Impact:** `.env.example` is in the docs-only allowlist (#1934). A PR touching it plus real Python could be misclassified.
**Mitigation:** This PR touches Python, tests, and docs together, so it will not classify as docs-only. Noted so a reviewer does not mistake the classification for a bypass attempt.

## Race Conditions

### Race 1: `warn_state` read-modify-write across concurrent `/update` runs
**Location:** `scripts/update/warn_state.py:30-60` (`_load` / `_save` / `should_emit`)
**Trigger:** The 30-minute cron fires while a Telegram-triggered `/update` is mid-run on the same machine. Both call `should_emit` for the same key and both read the pre-write state.
**Data prerequisite:** The JSON state file must reflect the prior emission before the second caller reads it.
**State prerequisite:** None beyond the file existing (it is created on first write).
**Mitigation:** The failure mode is a duplicate warning, not a lost one — strictly better than the status quo of 48 duplicates/day, and the existing `google-token` / `sms_reader` consumers already accept it. This plan adds no new concurrency and deliberately does not add locking; a lock file here would be a new failure mode (stale lock wedging `/update`) traded for a cosmetic duplicate. Recorded rather than "fixed".

### Race 2: `data/update.txt` is rewritten between the fix session being queued and being read
**Location:** `bridge/update.py::_queue_fix_session` (new path pointer) and the cron log writer.
**Trigger:** A low-priority fix session sits in the queue past the next 30-minute cron tick, which overwrites `data/update.txt`; the session then reads a *different* run's log.
**Data prerequisite:** The warning text the session was told to fix must still be recoverable.
**State prerequisite:** None.
**Mitigation:** This is precisely why the warning list is embedded in the message in full rather than only referenced. The `data/update.txt` pointer is supplementary context, and the message states it may have been superseded, so a stale read is recognisable rather than misleading.

## No-Gos (Out of Scope)

- [EXTERNAL] **Authenticating `gws` on Valor the Bald.** Requires a human at a browser completing OAuth consent; CLAUDE.md forbids automating it. This plan stops the respam only.
- [EXTERNAL] **Provisioning `REDIS_APP_PASSWORD` into the vault `.env`.** A credential a person must mint and record, on the specific machine, per the #2645 runbook.
- [ORDERED] **Applying the Redis ACL on any machine.** Double-gated behind `data/redis-acl-enabled` + `REDIS_ACL_APPLY=true`, human-signed per #2645 Risk 8, and fleet-ordered ahead of the #2661 `REDIS_URL` rotation — rotating before every machine has the ACL takes the worker, bridge, and dashboard down fleet-wide. `/update` must continue to call `apply_redis_acl()` with no arguments.
- [SEPARATE-SLUG #2845] **Restructuring `result.warnings` into typed warning records.** The architecturally correct fix for the root-cause pattern; tracked as a follow-up on this same issue thread rather than expanded into this plan. See Rabbit Holes.

## Update System

This work *is* the update system. Concretely:

- `scripts/update/verify.py` (`check_env_completeness`, `_parse_env_example`) changes.
- `scripts/update/run.py` gains two `warn_state` call sites; no step is added or reordered.
- `.env.example` gains a documented `@optional` convention and loses three dead declarations.
- No new dependency, config file, or migration. `warn_state`'s state file is created on demand and gitignored.
- No propagation step: every machine picks this up on its next `git pull` inside `/update`. There is no per-machine action required to adopt it — which matters, because the fleet is exactly where the noise lives.

## Agent Integration

No new agent surface. The change *improves* an existing one: the AgentSession queued by `_queue_fix_session` receives a complete brief instead of a truncated one. That path already exists (`agent.agent_session_queue.enqueue_agent_session`, `project_key="ai"`, `priority="low"`); only the `message_text` contents change. No new CLI entry point in `pyproject.toml [project.scripts]` and no new bridge import.

Integration coverage: a test asserting the composed `message_text` (the exact string handed to `enqueue_agent_session`) contains every extracted warning, since that string is the entire interface between `/update` and the agent that fixes it.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/env-completeness-validation.md` — document the `@optional` marker, the required-by-default rule, the first-comment-line description rule (currently documented as last), the inline-key cap, and **replace the remediation section at line 61**, whose current instruction to copy declarations into the vault `.env` is the destructive path proven in Finding A.
- [ ] Update `docs/features/headless-session-runner.md:165` — remove the `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES` cap description; #2494 deleted the trim and `agent/session_runner/adapter.py:370` documents the replacement (TTL-bounded, not count-bounded).
- [ ] Update `docs/features/config-architecture.md:46` — remove the `OLLAMA_VISION_MODEL` row; the live knobs are `MODELS__OLLAMA_HOST` / `MODELS__OLLAMA_GENERATION_MODEL`.
- [ ] Update `docs/features/redis-flush-hardening.md` — note that the `/update` drift warning now emits once per state transition, and that `python -m tools.doctor` is the unconditional on-demand check.
- [ ] Verify `docs/features/README.md`'s env-completeness row still describes the check accurately after the semantics change.

### Inline Documentation
- [ ] Header comment block in `.env.example` documenting the `@optional` convention, with the required-by-default rule stated explicitly.
- [ ] CLAUDE.md § Secrets — add the required/optional distinction to the existing "how to add a secret" instructions, since that section is what tells a future author to add a `.env.example` placeholder.
- [ ] Docstrings on `extract_update_warnings` naming each `run.py` output format it parses, with a pointer to `scripts/update/run.py:2482-2494` as the authority.

## Success Criteria

- [ ] A fix session spawned by `/update` receives the complete warning list — verified by a test that composes a 27-warning payload and asserts the last warning's text is present in `message_text`.
- [ ] `has_warnings` detects the `(N warnings)` cron summary and the `update failed at` summary, and returns false for a realistic green transcript containing "error"/"warning" substrings in git and npm output.
- [ ] `env-completeness` reports 0 missing on this machine's current vault, and still reports a missing *unmarked* key when one is introduced (both directions tested).
- [ ] No key declared in `.env.example` lacks a reader — enforced by a test, with the three dead declarations deleted.
- [ ] `gws auth` and Redis ACL drift emit once per state transition, and re-emit when the drift content changes or resolves.
- [ ] `python scripts/update/run.py --verify` on this machine reports 0 warnings where it previously reported 2, with the Redis ACL drift still visible via `python -m tools.doctor`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (update-channel)**
  - Name: `update-channel-builder`
  - Role: `bridge/update.py` — the warning parser and the fix-session payload (Defects 1 and 2)
  - Agent Type: builder
  - Resume: true

- **Builder (env-completeness)**
  - Name: `env-completeness-builder`
  - Role: `scripts/update/verify.py` + `.env.example` — the required/optional split, the dead-declaration deletions, and the recurrence guard (Defect 3)
  - Agent Type: builder
  - Resume: true

- **Builder (warn-state)**
  - Name: `warn-state-builder`
  - Role: `scripts/update/run.py` — `warn_state` wiring for the two human-gated warnings (Defect 4)
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: `update-channel-documentarian`
  - Role: the five doc updates and the CLAUDE.md Secrets addition
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `update-channel-validator`
  - Role: verifies every Success Criterion and every Verification row
  - Agent Type: validator
  - Resume: true

The three builders touch disjoint files (`bridge/update.py`; `scripts/update/verify.py` + `.env.example`; `scripts/update/run.py`) and can run in parallel in the single session worktree without interleaving commits.

## Step by Step Tasks

### 1. Warning parser and fix-session payload
- **Task ID**: build-update-channel
- **Depends On**: none
- **Validates**: `tests/unit/test_update_warning_extraction.py` (create)
- **Assigned To**: `update-channel-builder`
- **Agent Type**: builder
- **Parallel**: true
- Add `extract_update_warnings(status_lines) -> list[str]` to `bridge/update.py`, parsing the `⚠️` bullets, the `(N warnings)` summary, the `update failed at` block's `-` bullets (state-tracked), and the four legacy prefixes verbatim.
- Replace `has_warnings` with `bool(extract_update_warnings(status_lines))`; leave the `failed` short-circuit alone.
- Rewrite `_queue_fix_session` to take the warning list as a parameter, lead with it uncapped, and append a line-boundary-truncated stdout/stderr tail plus the `data/update.txt` path when present.
- Tests: green transcript with adversarial "error"/"warning" filenames yields `[]`; 27-warning payload round-trips with the last warning present; empty input; failure-block bullets matched only inside the block.

### 2. env-completeness required/optional split
- **Task ID**: build-env-completeness
- **Depends On**: none
- **Validates**: `tests/unit/test_env_completeness.py` (update)
- **Assigned To**: `env-completeness-builder`
- **Agent Type**: builder
- **Parallel**: true
- Teach `_parse_env_example` the `@optional` marker; exclude the marker line from description candidates; switch the description to the first non-empty comment line.
- Filter optional keys out of the missing set in `check_env_completeness`; cap the inline key list at 5 with a `(+N more)` suffix.
- Annotate the 26 verified-optional keys in `.env.example` and add the convention to its header block.
- Delete `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES`, `OLLAMA_URL`, `OLLAMA_VISION_MODEL` declarations.
- Update the existing 14 tests for the 3-element return and the first-line description rule; add tests for the marker, for prose "Optional" NOT matching, for required-by-default, and for the inline cap.

### 3. Dead-declaration recurrence guard
- **Task ID**: build-dead-key-guard
- **Depends On**: build-env-completeness
- **Validates**: `tests/unit/test_env_declaration_readers.py` (create)
- **Assigned To**: `env-completeness-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add a test asserting every `.env.example` declaration has a reader: literal occurrence in a tracked file outside `.env.example`, or resolution as `<PREFIX>__<FIELD>` against a nested settings model's declared field, resolved by introspecting `Settings` — no hand-written allowlist.
- Confirm it fails (red state) if one of the three deleted declarations is restored; paste that output into the PR description.

### 4. warn_state for human-gated warnings
- **Task ID**: build-warn-state
- **Depends On**: none
- **Validates**: `tests/unit/test_update_warn_state.py` (create or extend)
- **Assigned To**: `warn-state-builder`
- **Agent Type**: builder
- **Parallel**: true
- Route `run.py:1029` (gws auth) and `run.py:1480-1497` (Redis ACL drift) through `warn_state.should_emit`, mirroring the `human_gated_tools` block at `run.py:2266-2290` including the resolution branch.
- ACL signature: a digest of the planned commands (which already carry the `<REDIS_APP_PASSWORD>` placeholder, never a real secret). gws signature: the auth-method string.
- Keep the `apply_redis_acl()` call literally argument-free; keep both sites inside their existing `except Exception` guards.
- Tests: first run emits, second identical run stays silent, changed drift re-emits, resolution emits one note and clears state, a raising `should_emit` leaves the run successful.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-update-channel, build-env-completeness, build-dead-key-guard, build-warn-state
- **Assigned To**: `update-channel-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- All five doc updates from the Documentation section, including replacing the destructive remediation text in `docs/features/env-completeness-validation.md`.
- CLAUDE.md § Secrets: the required/optional distinction.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Assigned To**: `update-channel-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and every Success Criterion.
- Run `python scripts/update/run.py --verify` and confirm the env-completeness warning is gone and the ACL warning is emitted at most once.
- Confirm `python -m tools.doctor` still reports the Redis ACL drift unconditionally.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/test_env_completeness.py tests/unit/test_env_declaration_readers.py tests/unit/test_update_warning_extraction.py tests/unit/test_update_warn_state.py tests/unit/test_redis_acl.py tests/unit/test_ollama_consolidation.py tests/unit/test_review_judge_env_docs.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| env-completeness clean on this machine | `python -c "import sys; sys.path.insert(0,'.'); from pathlib import Path; from scripts.update.verify import check_env_completeness; r=check_env_completeness(Path('.')); print('MISSING' if not r.available else 'CLEAN')"` | output contains CLEAN |
| Dead declarations gone | `grep -c 'SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES\|^OLLAMA_URL=\|^OLLAMA_VISION_MODEL=' .env.example` | match count == 0 |
| ACL call site still argument-free (anti-criterion for the [ORDERED] No-Go) | `grep -n 'apply_redis_acl(' scripts/update/run.py` | output does not contain apply=True |
| No hand-written allowlist in the dead-key guard (anti-criterion for the Rabbit Hole) | `grep -ci 'ALLOWLIST\|KNOWN_UNREAD\|EXEMPT' tests/unit/test_env_declaration_readers.py` | match count == 0 |
| Optional marker is sigil-based, not prose-based (anti-criterion for Risk 1) | `grep -c "startswith(\"optional\")\|lower() == \"optional\"" scripts/update/verify.py` | match count == 0 |
| Warning extraction is line-anchored, not substring (anti-criterion for Risk 2) | `grep -c 'in line\b' bridge/update.py` | match count == 0 |

## Critique Results

**Critique round 1, 2026-08-17.** Run against plan commit `c60473fca` (plan hash
`sha256:92b9c48e`). War room depth: **FULL** (3 critics — LLM triage classified FULL on
cross-component scope: `bridge/`, `scripts/update/`, `.env.example`, CLAUDE.md, plus a new
abstraction and a new pydantic-introspecting guard test). Roster gate: **3/3 complete, 3/3
grounded**. Findings: **8 total (3 blockers, 4 concerns, 1 nit)**.

*Column semantics:* **Implementation Note** is the critic's guidance at the time the finding was
raised; **Addressed By** records what the revision pass actually adopted and supersedes the note
where the two differ.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency (3/3) | Two live plan documents both carry `tracking: .../issues/2845` — this plan and `docs/plans/update-warning-channel-integrity.md` (commit `76fd31e52`, 10s earlier, materially different design). `tools/lane_identity.py::find_plan_path` returns the FIRST alphabetical match, so `find_plan_path(2845)` resolves to `integrity.md`; every SDLC stage tool (`sdlc_stage_query`, `sdlc_verdict`, `sdlc_stage_marker`, `sdlc_next_skill`) reads the stale, uncritiqued document. The Freshness Check's claim "Active plans in `docs/plans/` overlapping this area: none" is verifiably false. | pending | `find_plan_path` (`tools/lane_identity.py:170-179`) iterates `sorted(plans_dir.iterdir())` and returns on first `tracking:` match; `"...integrity.md" < "...repair.md"` lexically, so `repair.md` is unreachable by every automated stage tool. Fix = collapse to exactly one live plan for #2845 (fold `integrity.md`'s distinct ideas — `<<FILE:...>>` log-path marker, always-on `suppressed:` visibility line, `optional:` prose marker — into this plan, then `git rm` the sibling), then re-run `python -c "from tools.lane_identity import find_plan_path; print(find_plan_path(2845))"` and require it to print the `repair` path before `/do-build`. |
| BLOCKER | Structural check (driver) | Verification row "Warning extraction is line-anchored, not substring" runs `grep -c 'in line\b' bridge/update.py` and expects `match count == 0`, but that grep already returns **2** on unmodified `bridge/update.py` — at `:250` (`any("Worker restarted" in line for line in status_lines)`) and `:490` (`if any(k in line for k in [...])`). Both are pre-existing, load-bearing, and unrelated to warning extraction. The row cannot pass unless the builder deletes working code. | pending | Verified live at `c60473fca`. Replace the whole-file grep with one scoped to the new helper — e.g. assert `extract_update_warnings`'s body contains no bare `in line` by extracting the function with `awk '/def extract_update_warnings/,/^def /'` and grepping that, or drop the grep row and rely on the behavioural green-transcript test in Task 1 (which already covers Risk 2 directly and is not fooled by unrelated call sites). |
| BLOCKER | History & Consistency | Internal count contradiction on the `@optional` marker set: Problem point 3 and Rabbit Holes say **27** flagged keys ("All 27 flagged keys are behaviour toggles"; "Only the 27 currently-flagged keys need classification"), but Technical Approach part 3, Task 2, and Risk 1's Mitigation all say **26** get the marker ("Mark the 26 verified-optional keys"). Nothing reconciles the 27th. The three dead-declaration deletions are a disjoint set (framed as "a fifth, adjacent defect"). | pending | `check_env_completeness` (`scripts/update/verify.py:1088-1134`) does a flat `declared_keys - present` diff with no per-key exemption beyond the optional filter this plan adds. If one of the 27 stays unmarked and is absent from the vault, the check still reports it missing — breaking Success Criterion 3 ("`env-completeness` reports 0 missing on this machine's current vault") and the Verification table's `CLEAN` row. Either correct 27 to 26 everywhere, or name the 27th key explicitly and state its disposition (deleted / genuinely required / present under another mechanism). |
| CONCERN | Risk & Robustness | `extract_update_warnings` is specified against **single physical lines** (`  ⚠️ <text>`, `  - <text>`), but `result.warnings` entries are built from arbitrary exception `str()` values at ~35 `result.warnings.append(f"...: {e}")` sites in `scripts/update/run.py`, any of which can embed newlines — the plan's own Problem section quotes a multi-line pydantic `ValidationError` as exactly this shape. `run.py:2482-2494` renders via `status += f"\n  ⚠️ {warn}"`, so only the first physical line carries the sentinel and the parser silently drops the tail. That reproduces the truncation defect this plan exists to fix, via a different producer. | pending | Two viable fixes: (a) sanitize at the emission boundary — collapse embedded newlines before `result.warnings.append`; or (b) make the parser accumulate unprefixed continuation lines into the preceding bullet until the next recognized sentinel or a blank line. Add a case to `tests/unit/test_update_warning_extraction.py` where one `⚠️` bullet's source text contains an embedded `\n` and assert the COMPLETE multi-line text survives extraction. Neither the "27-warning payload round-trips" nor the "count mismatch is surfaced" test covers this — both assume one warning equals one line. |
| CONCERN | History & Consistency | Defect 4 re-commits the anti-pattern the plan's own root-cause table names. "Why Previous Fixes Failed" blames #2329/#2328 for "wiring at the call site rather than at the warning-emission boundary," naming `gws auth` and `Redis ACL drift` as the two sites that "had to remember to opt in, and neither did" — and Defect 4's fix is to wire `warn_state.should_emit` at those same two call sites. The "Root cause pattern" paragraph's escape claim covers only Defects 1-2, not Defect 4, and Defect 4 gets no recurrence guard analogous to `build-dead-key-guard`. | pending | `human_gated_tools` at `run.py:2266-2290` is the only existing enforcement and is itself a hand-maintained Python set. Either add a guard test asserting that set (or an equivalent registry covering `run.py:1029` and `run.py:1480-1497` post-landing) is the single place a new human-gated check must register, or add an explicit Rabbit Hole entry acknowledging Defect 4 does NOT close the "next call site forgets to opt in" gap and that this is an accepted, scoped exception. Silence currently reads as an implied fix. |
| CONCERN | Scope & Value | Every Success Criterion (plan lines 280-287) is a unit test or a CLI-output assertion. The Problem statement is that "nobody — human or agent — reliably learns what it found" via the live Telegram / AgentSession path, but no criterion exercises that path end-to-end: no criterion checks that the delivered brief is legible and complete to the operator or the auto-spawned Eng session. | pending | Extend the integration test already named under "Agent Integration" (the composed `message_text` handed to `enqueue_agent_session`) from substring-containment to rendered readability: assert the full warning list appears BEFORE any raw stdout/stderr tail, that the tail is cut on a line boundary (no mid-word truncation), and that the `data/update.txt` pointer line is present when that file exists. Promote that assertion to a Success Criterion so the Problem statement's loop is closed by a check, not by inference. |
| CONCERN | Structural check (driver) | The Verification row "env-completeness clean on this machine" asserts `CLEAN` from `r.available`, but `check_env_completeness` returns `available=True` for **three distinct skipped outcomes** — `skipped (.env not found)`, `skipped (.env.example not found)`, and `skipped (read error)` (`scripts/update/verify.py:1100-1141`). A machine with an evicted iCloud `.env` symlink or a TCC read error passes this row vacuously while the check never ran. | pending | Assert on `version` / `error` rather than the `available` bool: require `r.available and r.version and r.version.startswith("all ")` so the three `skipped (...)` versions fail the row. Same trap applies to the paired negative direction in Success Criterion 3 ("still reports a missing unmarked key when one is introduced") — that half is fine because it asserts `available=False`. |
| NIT | Structural check (driver) | Tasks 5 (`document-feature`) and 6 (`validate-all`) carry no `Validates:` field, unlike Tasks 1-4. Task 6 in particular is the plan's own final gate and has no named artifact proving it ran. | pending | Add `**Validates**:` rows — for Task 5 a docs check (e.g. the `/do-docs` cascade or a grep asserting the destructive remediation text at `docs/features/env-completeness-validation.md:61` is gone), and for Task 6 the full Verification table itself. |

**Structural check results (driver, run at `c60473fca`):**

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation (8 checkboxes, 5 `docs/features/` paths), Update System, Agent Integration, Test Impact all present and substantive. |
| Task numbering | PASS | Tasks 1-6 contiguous, no gaps. |
| Dependencies valid | PASS | Every `Depends On` resolves to a real Task ID; no cycles. Tasks 5-6 have no `Validates` field (NIT above). |
| File paths exist | PASS | 24 of 26 cited paths exist; the 2 missing (`tests/unit/test_update_warning_extraction.py`, `tests/unit/test_env_declaration_readers.py`) are intentionally new. |
| Prerequisites met | PASS | Vault `.env` readable; `redis-cli` at `/opt/homebrew/bin/redis-cli`. |
| Cross-references | FAIL | 27-vs-26 contradiction (blocker 3); Verification row unachievable on unmodified code (blocker 2); Freshness Check "no overlapping plans" claim false (blocker 1). |
| Cited claims re-verified | PASS | `bridge/update.py:120-121` `stdout[:500]`, `:291` `_warning_prefixes`, `verify.py:1088-1134` flat set diff, `run.py:2482-2494` summary render, `run.py:2266-2290` `warn_state` precedent, `run.py:1484` `apply_redis_acl()` argument-free — all confirmed verbatim. |

---

## Open Questions

1. **Scope confirmation.** The routed task was "diagnose and fix the 2 warnings this machine reported." Issue #2845 is broader — it also covers the fix-session truncation and the `has_warnings` blindness, which are the reasons nobody saw these warnings properly in the first place. This plan implements the whole issue on the grounds that half-implementing it leaves it open and its acceptance criteria unmet. Confirm that is the intended scope, or say the word and I will split Defects 1–2 into a separate issue and land only 3–4.
2. **`@optional` sigil.** I chose `@optional` over a bare `# optional` because `.env.example` already contains six prose uses of the word, one of them line-initial above a required credential (`# Optional. GitHub PAT for ...`), so a prose match would silently mark a real secret optional. If there is an existing annotation convention in this repo I should be reusing instead, name it.
3. **Anything genuinely required among the 27?** Every one traced to a read site with an in-code default, and none is a credential. If you know of a machine where one of these is load-bearing and unset would be wrong, say which — that is the one thing the code cannot tell me.
