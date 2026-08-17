---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2845
last_comment_id: none
---

# /update Warning Channel Integrity

## Problem

On 2026-08-17 a `/update` on "Valor the Bald" finished at `4d9118125` and reported **3 warnings**. The system auto-spawned a fix session (`update_fix_6a33df3e`) to deal with them. That session received this, and nothing more:

```
...
found 0 vulnerabilities
up to date at 4d9118125 (3 warnings)
  ⚠️ gws auth: gws is installed but not authenticated — first use needs a one-time human OAuth step: gws auth setup --login   (or: gws auth setup && gws
```

The payload stops mid-token. Two of the three warnings were never delivered to the agent whose entire job was to fix them.

Worse, that session was queued **by accident**. The trigger that decides whether warnings exist does not recognize the format in which `/update` reports warnings. It fired on an unrelated line about beacon classification. A warning-bearing `/update` with a clean release verify escalates to nobody.

And all three warnings are structurally unresolvable by any automated cycle, so they re-fire on every 30-minute `com.valor.update` run, forever.

**Current behavior:**

1. `bridge/update.py:121` slices the update output to `stdout[:500]`. Measured: the delivered payload is exactly 500 characters. The git-pull and npm preamble consumes ~370 of them, so the warning list is structurally the part that gets discarded.
2. `bridge/update.py:291` decides `has_warnings` by matching line prefixes `("[update] WARN", "WARNING:", "ERROR", "RESTART FAILED")`. `run.py --cron` reports warnings as `up to date at <sha> (N warnings)` followed by `  ⚠️ <text>` lines. Neither form matches. The gate has been blind to the summary since it was written.
3. `env-completeness` reports 27 "missing" keys that are all optional toggles with in-code defaults. Several are apply-gates (`MEMORY_DECAY_PRUNE_APPLY`, `DISK_RECLAIM_APPLY`) where writing a value into the shared iCloud vault `.env` would be actively wrong. The only way to silence the warning is to do the wrong thing.
4. `gws auth` and `Redis ACL drift` are real, permanent, human-gated conditions that warn on every cycle. `warn_state.py` exists to collapse exactly this class to one emission per state transition, and neither uses it.

**Desired outcome:**

A fix session receives the complete warning list. The trigger that spawns it recognizes the format `/update` actually emits. `env-completeness` reports genuine gaps only. Human-gated conditions warn once per state transition — and stay retrievable on demand, because a suppressed warning nobody can look up is worse than the nag.

## Freshness Check

**Baseline commit:** `4d9118125`
**Issue filed at:** 2026-08-17T15:04:19Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `bridge/update.py:121` — `stdout[:500]` slice in `_queue_fix_session` — still holds; payload measured at exactly 500 chars.
- `bridge/update.py:291` — `_warning_prefixes` tuple — still holds.
- `scripts/update/run.py:2486-2492` — cron summary emission (`(N warnings)` + `⚠️`) — still holds.
- `scripts/update/run.py:1029` — gws auth warning append, no `warn_state` — still holds.
- `scripts/update/run.py:1494` — Redis ACL drift warning append, no `warn_state` — still holds.
- `scripts/update/run.py:2275-2291` — `human_gated_tools` set routing `google-token`/`sms_reader` through `warn_state` — still holds; the pattern to copy.
- `scripts/update/verify.py:1088-1141` — `check_env_completeness` — still holds.
- `scripts/update/verify_release.py:147` — the `WARNING:` line that coincidentally triggered escalation — still holds.
- `scripts/update/warn_state.py` — exports `should_emit` only; no reader — still holds.

**Cited sibling issues/PRs re-checked:**
- #2328 — open ("No Full Disk Access granted on this machine"). One of the two motivating issues for `warn_state`; its `sms_reader` entry is currently the sole key in `data/update_warn_state.json`.
- #2329 — the google-token OAuth spam issue that `warn_state` was built for. Its entry was cleared by the 21:56 run (`google-token: resolved (human grant now present)`).
- #2645 — Redis flush hardening. `redis_acl.py`'s Risk 8 forbids `/update` from applying the ACL. Constrains this plan: report-only stays report-only.
- #2661 — open ("Rotate production REDIS_URL to the valor-app ACL credential"). This is the human runbook that will eventually clear the Redis ACL drift. Not blocked by, and does not block, this plan.
- #1140 — closed. Created the `.env.example` per-variable comments + completeness check being fixed here.

**Commits on main since issue was filed (touching referenced files):** none — `git rev-list --count HEAD..origin/main` is 0. The issue was filed and planned inside the same hour.

**Active plans in `docs/plans/` overlapping this area:** none. `grep -rln "warn_state\|env-completeness\|_queue_fix_session" docs/plans/` returns only `nightly-autonomous-fix-before-alert.md` (which references autonomous-fix dispatch generally, not the `/update` warning channel) and three plans under `completed/`.

**Notes:** Bug reproduction was performed directly rather than inferred — `gws auth status`, `python -m scripts.update.redis_acl --dry-run`, and `verify.check_env_completeness()` were each re-run live and all three still fire.

## Prior Art

- **Issue #2329**: google-token OAuth spam — introduced `scripts/update/warn_state.py` and the `human_gated_tools` routing in `run.py`. Succeeded for its two keys. Relevance: this plan extends the same mechanism to two more keys, and fills the gap #2329 left (no retrieval surface).
- **Issue #2328**: Full Disk Access / `tools/sms_reader` — the second motivating issue for `warn_state`, still open because the human grant has not been performed. Relevance: proves the suppression mechanism works but is unobservable — `sms_reader` has been silently suppressed and the only evidence is a gitignored JSON file.
- **Issue #1140**: `.env.example` per-variable comments + update-time completeness check — closed, shipped the check now producing 27 false positives. Relevance: this plan revises that check's contract rather than replacing it.
- **Issue #2645**: Redis flush hardening — shipped `redis_acl.py` as deliberately report-only. Relevance: a hard constraint. The drift warning must keep reporting; only its emission cadence changes.
- **Issue #1898**: release-verify gating — shipped the all-lines warning scan in `bridge/update.py` that this plan extends. Relevance: `test_warning_on_non_first_stdout_line_detected` is its regression test and must stay green.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1898 | Replaced a first-line-only stdout scan with an all-lines scan using four literal prefixes. | Fixed the *position* blind spot but not the *vocabulary* one. The prefixes were drawn from `remote-update.sh`'s own echoes and the verify CLI; nobody cross-checked them against `run.py --cron`'s summary grammar, which uses `(N warnings)` and `⚠️` and matches none of them. |
| #2329 | Added `warn_state.should_emit` and routed `google-token` + `sms_reader` through it. | Solved the spam for two keys by name, as a hardcoded set (`human_gated_tools`), rather than as a policy any human-gated check opts into. `gws auth` and `Redis ACL drift` were added to `/update` later and nobody connected them. It also shipped write-only: no reader, so suppressed state became invisible. |
| #1140 | Added the `.env.example` completeness check. | Assumed every declared key is a secret that belongs in the vault. `.env.example` subsequently became the documentation home for behaviour toggles too, and the check had no vocabulary for "documented but intentionally unset". |

**Root cause pattern:** each fix hardcoded a snapshot of the world — a prefix list, a key set, a one-population assumption — instead of encoding the *rule*. Every later addition to `/update` silently fell outside the snapshot. This plan replaces all three snapshots with declared contracts: a detector that parses the summary grammar, a `warn_state` opt-in any check can use, and an explicit optional/required tag in `.env.example`.

## Architectural Impact

- **New dependencies**: none. Standard library only.
- **Interface changes**: `warn_state` gains a reader (`active()`) and a `__main__`. `check_env_completeness`'s `ToolCheck` semantics narrow — `available=False` now means "a *required* key is missing", and optional-unset counts move into `version`. `_queue_fix_session` gains an optional `log_path` parameter.
- **Coupling**: decreases. `bridge/update.py` stops depending on an implicit literal-prefix agreement with three separate emitters and instead parses one declared summary grammar. `run.py`'s `human_gated_tools` hardcoded set stops being the only way to get suppression.
- **Data ownership**: unchanged. `data/update_warn_state.json` remains per-machine and gitignored; this plan adds a read path to data that was already being written.
- **Reversibility**: high. Every change is additive or a narrowing of a report; reverting restores the current (noisy) behavior with no data migration. `data/update_warn_state.json` gains two keys which a revert would simply ignore.

## Appetite

**Size:** Medium

**Team:** Solo dev + 3 parallel builders, code reviewer

**Interactions:**
- PM check-ins: 1-2 (already had one — scope and the `.env.example` tag convention are settled)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Repo venv on the pinned interpreter | `scripts/pytest-clean.sh --collect-only tests/unit/test_bridge_update.py -q` | Test runner is usable in this worktree |
| `redis-cli` present | `command -v redis-cli` | `test_redis_acl.py` fixtures fake the binary, but `run.py`'s step imports the module |

No new secrets, services, or external dependencies.

## Solution

### Key Elements

- **Warning extractor** (`bridge/update.py`): pulls the complete warning block out of `/update` stdout and delivers it to the fix session intact, alongside the path to the full log the cron path already writes.
- **Summary-grammar detector** (`bridge/update.py`): a testable function that recognizes both the legacy line prefixes and `run.py --cron`'s `(N warnings)` / `⚠️` summary form.
- **Optional-key tag** (`.env.example` + `scripts/update/verify.py`): an explicit machine-readable marker declaring a documented key as an intentionally-unset tunable, so the completeness check reports only genuine secret gaps.
- **`warn_state` opt-in + reader** (`scripts/update/warn_state.py`, `run.py`): any human-gated check routes through one-emission-per-transition suppression, and the suppressed set is always visible in the run output and queryable on demand.

### Flow

`/update` runs → warnings computed → **cron summary emits `(N warnings)` + `⚠️` lines + a `suppressed:` trailer** → bridge parses the summary → recognizes warnings → **fix session receives the complete list + log path** → agent has everything it needs.

Suppressed condition, later: human asks "what is `/update` sitting on?" → `python -m scripts.update.warn_state` → full stored signatures with the exact human step for each.

### Technical Approach

**1. Truncation (`bridge/update.py::_queue_fix_session`)**

Stop slicing blindly. Build the fix-session message from three parts, in priority order:

- The **complete warning block**, extracted from stdout: the `(N warnings)` summary line plus every `⚠️` line, uncapped. This is the payload the session exists for and must never be elided.
- The **log file path**, parsed from the `<<FILE:...>>` marker the cron path already emits (`run.py:2519`), so the agent can read the full 106-line buffer itself.
- A **bounded raw excerpt** of stdout/stderr for context. Keep a cap (generously raised) but apply it head+tail with an explicit `[... N characters elided ...]` marker, so a cap can never again silently swallow the tail.

**2. Detection (`bridge/update.py`)**

Extract a module-level `_scan_for_warnings(status_lines) -> bool`. It returns True when **any** of:

- a line starts with one of the existing legacy prefixes (preserves #1898's `test_warning_on_non_first_stdout_line_detected`), or
- a line matches the cron summary grammar `^(?:updated to|up to date at)\s+\S+\s+\((\d+)\s+warnings?\)$` with N > 0, or
- a line's stripped form starts with the `⚠️` marker.

Making it a named function is the point — the current logic is an inline `any()` inside a 150-line coroutine and is only reachable through a full `handle_update_command` call.

**3. `env-completeness` optional/required split (`scripts/update/verify.py`, `.env.example`)**

Convention: a tag line inside the comment block above a declaration marks it optional.

```
# Cross-vendor review judge (issue #1626) — behavior toggles, not secrets.
# Default OFF — enable on the review machine only.
# optional: in-code default (config/settings.py)
SDLC_REVIEW_CROSS_VENDOR=0
```

- `_parse_env_example` recognizes a comment line whose stripped body matches `^optional\b` (case-insensitive) and marks that key optional. The tag line itself is excluded from the description.
- **Untagged means required.** This polarity is deliberate: it is backward-compatible with every existing test in `test_env_completeness.py` (which declares untagged keys and expects them reported), and it fails safe — forgetting to tag a new secret keeps warning, while forgetting to tag a new tunable is a one-line fix.
- `check_env_completeness` reports only missing **required** keys. Missing optional keys become a count in `version`: `all N required vars present (27 optional unset)` — discoverable, not a warning.
- Description quality: join the comment block into one string and cap it (~120 chars + ellipsis) instead of taking only the last line. The current last-line heuristic produces fragments like `HOOK_DETACH_MAX_INFLIGHT (foreign repos too.)`.
- Tag all 27 keys in `.env.example`.

**4. `warn_state` routing + retrieval (`scripts/update/warn_state.py`, `run.py`)**

- Add `active(project_dir) -> dict[str, str]` returning the currently-suppressed key → signature map. Fail-soft (`{}` on any error), matching the module's existing contract.
- Add a `_main()` printing the full stored map, mirroring `redis_acl.py::_main`, so `python -m scripts.update.warn_state` is the on-demand answer to "what is suppressed?".
- Route `gws auth` (`run.py:1029`, key `gws-auth`) and Redis ACL drift (`run.py:1494`, key `redis-acl-drift`) through `should_emit`, clearing on resolve so a regression re-warns.
- **Always-on visibility (non-negotiable — see Risk 1):** whenever `active()` is non-empty, `run.py` emits one non-warning line in both the verbose log and the `--cron` summary:
  `suppressed (unchanged since first warning): gws-auth, redis-acl-drift, sms_reader — details: python -m scripts.update.warn_state`
  This line deliberately carries neither `⚠️` nor a legacy prefix, so it does not re-trip the detector from element 2.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `warn_state._load` / `_save` swallow `OSError` / `JSONDecodeError` by design. The new `active()` inherits this; add a test asserting it returns `{}` on a corrupt state file rather than raising (existing `test_update_warn_state.py` has the corrupt-file precedent at line 65).
- [ ] `bridge/update.py::_queue_fix_session` wraps its body in `except Exception` and logs a warning (line 136). Add a test asserting the warning-extraction path cannot raise on malformed stdout (no `<<FILE:` marker, no summary line, empty string).
- [ ] `run.py`'s Redis ACL step already has a blanket `except Exception` (line 1508). The `warn_state` call is added inside it; assert the step still degrades to a logged warning if `should_emit` raises.

### Empty/Invalid Input Handling
- [ ] `_scan_for_warnings([])` returns False. `_scan_for_warnings` on whitespace-only lines returns False.
- [ ] `(0 warnings)` — a summary claiming zero warnings must NOT trigger a fix session. This is the inverse case and is easy to get wrong with a naive substring match on "warning".
- [ ] `check_env_completeness` with an `.env.example` containing only optional-tagged keys returns `available=True` with a zero required count.
- [ ] `_queue_fix_session` with empty stdout and empty stderr produces a coherent message, not a bare header.

### Error State Rendering
- [ ] The Telegram-visible summary is user-facing output. Assert the `suppressed:` trailer renders in the `--cron` summary when state is non-empty, and is absent when empty.
- [ ] Assert the fix-session message contains all three warnings verbatim for the reproduction fixture (the real 3-warning stdout from `data/update.txt`), not a truncated prefix.

## Test Impact

- [ ] `tests/unit/test_bridge_update.py::test_warning_on_non_first_stdout_line_detected` — UPDATE: keep as-is (legacy prefix must still work), but it no longer covers the summary form. Add siblings rather than modify.
- [ ] `tests/unit/test_bridge_update.py` — ADD: `test_cron_summary_warnings_trigger_fix_session` (**red-state proof required** — must fail against the current prefix-matching code), `test_zero_warning_summary_does_not_trigger`, `test_fix_session_payload_contains_all_warnings`, `test_fix_session_payload_includes_log_path`.
- [ ] `tests/unit/test_env_completeness.py::test_multiline_comment_block_uses_last_line` — REPLACE: the last-line-only heuristic is being replaced by a joined, capped description. Rewrite to assert the joined description contains the block's substantive content.
- [ ] `tests/unit/test_env_completeness.py::test_section_separator_does_not_bleed_into_description` — UPDATE: assertion `"===" not in result.error` must still hold under the joined-description change.
- [ ] `tests/unit/test_env_completeness.py` (`TestMissingKeyReported`, `TestAllPresent`, `TestBlankValueIsPresent`, `TestEnvNotFoundReturnsSkipped`, `TestUnreadableEnvReturnsSkipped`) — UPDATE: no assertion changes expected (all declare untagged keys, which stay required), but re-run to prove the untagged-means-required polarity is genuinely backward-compatible.
- [ ] `tests/unit/test_env_completeness.py` — ADD: `test_optional_tagged_key_not_reported_missing`, `test_optional_count_surfaced_in_version`, `test_optional_tag_line_excluded_from_description`, `test_untagged_key_still_required`.
- [ ] `tests/unit/test_update_warn_state.py` — ADD: `test_active_returns_suppressed_map`, `test_active_returns_empty_on_corrupt_state`, `test_active_empty_after_resolve`.
- [ ] `tests/unit/test_gws_auth.py` — UPDATE: unaffected (the module's detection contract does not change), re-run as a guard.
- [ ] `tests/unit/test_redis_acl.py` — UPDATE: unaffected (report-only contract unchanged, `apply_redis_acl()` still called with no arguments), re-run as a guard. The existing regression test asserting the call site passes no `apply` argument must stay green.
- [ ] `tests/unit/test_update_cron_summary.py` — UPDATE: assert the new `suppressed:` trailer does not disturb the existing `up to date at <sha>` / `updated to <sha>` assertions.

## Rabbit Holes

- **Rewriting the `/update` summary format.** Only `bridge/update.py` and `test_update_cron_summary.py` consume it (verified by grep). It is tempting to restructure it into JSON now that a parser exists. Don't — add a marker, keep the human-readable shape.
- **Making `warn_state` generic across all `/update` checks.** There are ~80 `result.warnings.append` call sites in `run.py`. Routing all of them through state transitions is a different, larger project and would suppress genuinely transient warnings that should re-fire. Only the two human-gated ones are in scope.
- **Inferring optionality from `config/settings.py`.** Superficially elegant, actually broken: `HOOK_DETACH_DEADLINE_SECONDS` is explicitly documented as "deliberately not wired into config/settings.py". Any settings-derived heuristic misclassifies it. The explicit tag is the whole point.
- **Sniffing optionality from existing prose** ("Provisional/tunable", "Default OFF"). This is keyword matching, violates development principle 3, and would silently misclassify the day someone rewords a comment.
- **Fixing `verify_release.py:147`'s `WARNING:` line.** It is a legitimate warning and its accidental role in triggering this fix session is incidental. Leave it.
- **Auditing whether the 27 keys *should* be in `.env.example` at all.** Several arguably belong in `docs/features/config-timeout-catalog.md` instead. Out of scope; tag them and move on.

## Risks

### Risk 1: Suppression hides a real condition
**Impact:** `gws auth` and `Redis ACL drift` are permanent-until-human. Once suppressed, an operator who never saw the single emission has no way to learn the machine is unauthenticated or that production Redis still grants unrestricted flush to `default`. This is strictly worse than the nag it replaces.
**Mitigation:** Retrieval is a hard requirement of this plan, not a follow-up — `active()`, the `python -m scripts.update.warn_state` CLI, and the always-on `suppressed:` trailer in both the verbose log and the Telegram summary. A suppressed condition is never invisible, only quiet. Tests assert the trailer renders whenever state is non-empty.

### Risk 2: The new detector over-fires and spawns fix sessions on clean runs
**Impact:** Every `/update` would queue an agent session, burning tokens and worker slots on nothing.
**Mitigation:** The summary regex is anchored (`^`/`$`) and requires N > 0, so `(0 warnings)` is inert. The `suppressed:` trailer deliberately carries no `⚠️` and no legacy prefix. Explicit negative tests for both cases.

### Risk 3: Untagged-means-required silently misclassifies a future tunable
**Impact:** A developer adds a tunable to `.env.example` without the tag and `/update` warns on every machine.
**Mitigation:** Accepted, and deliberately chosen over the inverse. The failure mode is a visible, one-line-fix nag; the inverse polarity's failure mode is a silently-unreported missing secret. Documented in CLAUDE.md's Secrets section so the tag is part of the add-a-variable routine.

### Risk 4: `.env.example` tagging churn collides with a concurrent lane
**Impact:** 27 comment insertions across a file other lanes may be editing produces a merge conflict.
**Mitigation:** `.env.example` is comment-only churn confined to one builder's file set; conflicts resolve trivially. `grep -rln` over `docs/plans/` found no active plan touching it.

## Race Conditions

### Race 1: Concurrent read-modify-write of `data/update_warn_state.json`
**Location:** `scripts/update/warn_state.py:70-79` (`should_emit` load → mutate → save)
**Trigger:** Two `/update` processes evaluating the same key concurrently. `remote-update.sh` holds `data/update.lock` so two *full* runs are excluded, but `run.py --verify` takes no lock and can run alongside a cron cycle.
**Data prerequisite:** None — each key's entry is independent.
**State prerequisite:** None.
**Mitigation:** Pre-existing behavior, not introduced here, and the worst outcome is one redundant warning emission or one skipped one-time "resolved" note. Both are self-correcting on the next cycle. Adding file locking would be disproportionate to a fail-soft bookkeeping file; `_save` already swallows write errors by design. The new `active()` is read-only and takes the same fail-soft posture. Documented rather than mitigated.

### Race 2: `active()` read interleaved with a `should_emit` write in the same run
**Location:** `scripts/update/run.py` — the `suppressed:` trailer is composed at summary time, after every check has run.
**Trigger:** None within a single process; `run.py` is sequential.
**Data prerequisite:** All `should_emit` calls must have completed before `active()` is read, or the trailer under-reports.
**State prerequisite:** The trailer must be composed at the end of `run_update`, not inline with the checks.
**Mitigation:** Compose the trailer in the summary block (`run.py:2482-2494`), which already runs after all steps. Assert ordering in a test that suppresses a key and checks it appears in the same run's trailer.

## No-Gos (Out of Scope)

- [EXTERNAL] Completing the `gws` OAuth flow. `gws auth login` opens a browser for Google consent and `gws auth setup` additionally requires `gcloud` plus a GCP project. CLAUDE.md forbids automation that depends on a human approving a prompt, and `gws_auth.py`'s docstring makes detection-only an explicit contract. A human must run `gws auth setup --login` once on this machine.
- [SEPARATE-SLUG #2661] Applying the Redis ACL to clear the drift. `redis_acl.py`'s Risk 8 is explicit that a `/update`-issued `ACL SETUSER` would make merging the PR the apply. The rotation is tracked by #2661.
- [EXTERNAL] Granting Full Disk Access to clear the `sms_reader` suppression (tracked separately as #2328). Requires a human in System Settings > Privacy & Security; this plan only makes the suppressed state visible.
- [EXTERNAL] Adding the 27 optional keys to the vault `.env` at `~/Desktop/Valor/.env`. Would be the wrong fix (several are apply-gates where a set value is dangerous) and the vault is outside the repo.

## Update System

This work *is* an `/update` change. Specifically:

- `scripts/update/verify.py`, `scripts/update/run.py`, and `scripts/update/warn_state.py` are all orchestrator modules — changes take effect on the next `/update` because `remote-update.sh` pulls before invoking `run.py`.
- `bridge/update.py` is bridge code. Per CLAUDE.md, a bridge-relevant diff triggers a bridge restart; `remote-update.sh`'s `NEED_BRIDGE_RESTART` gate (line 399) already includes `bridge/`, so propagation is automatic. No install-script change.
- No new dependencies, config files, or secrets to propagate.
- No migration needed for existing installations: `data/update_warn_state.json` gains two new keys organically on the first post-merge run, and its absence is already handled (`_load` returns `{}`).
- `.claude/skills/update/SKILL.md` needs a note documenting the `suppressed:` trailer and the `python -m scripts.update.warn_state` retrieval command, since that file is the operator-facing description of what `/update` reports.

## Agent Integration

No new MCP tool or CLI entry point in `pyproject.toml [project.scripts]`.

- `python -m scripts.update.warn_state` is reachable via the agent's Bash tool as a module invocation, matching the existing `python -m scripts.update.redis_acl` precedent. No `[project.scripts]` entry needed.
- `bridge/telegram_bridge.py` already imports `bridge/update.py`'s handlers; no new wiring.
- The integration that matters is agent-facing in a different sense: the fix session *is* the agent, and its input payload is what this plan repairs. The test asserting the payload contains all three warnings verbatim is the integration test for that path.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/update-warning-channel.md` describing the warning grammar `/update` emits, the detection contract in `bridge/update.py`, the `warn_state` suppression-with-retrieval policy, and the `env-completeness` required/optional split.
- [ ] Add entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Update `scripts/update/warn_state.py`'s module docstring — it currently describes suppression as write-only and names only the two original keys.
- [ ] Docstring for `_scan_for_warnings` naming all three recognized grammars and why the `suppressed:` trailer is deliberately inert.
- [ ] Comment in `.env.example`'s header explaining the `# optional:` tag convention.

### Repo Convention Docs
- [ ] Update CLAUDE.md's **Secrets** section: the add-a-variable routine must state that a tunable with an in-code default gets an `# optional:` tag, and a real secret does not. (Human-reviewed via PR — this changes a documented repo convention.)
- [ ] Update `.claude/skills/update/SKILL.md` with the `suppressed:` trailer and the retrieval command.

## Success Criteria

- [ ] A fix session spawned from a 3-warning `/update` receives all three warnings verbatim, plus the `data/update.txt` path.
- [ ] `_scan_for_warnings` returns True for the cron summary form; the new test fails against the current prefix-matching code (red-state proof captured in the PR body).
- [ ] `(0 warnings)` and the `suppressed:` trailer both return False from `_scan_for_warnings`.
- [ ] `check_env_completeness` against this repo's real `.env.example`/`.env` reports **0 missing required keys** and surfaces 27 as an optional count.
- [ ] `gws auth` and `Redis ACL drift` emit once, then stay silent on a second consecutive run with unchanged state.
- [ ] `python -m scripts.update.warn_state` prints all currently-suppressed keys with their signatures.
- [ ] The `suppressed:` trailer appears in the `--cron` summary whenever state is non-empty.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

Three builders with **disjoint file sets** so their commits never interleave in the single session worktree, then one reviewer.

### Team Members

- **Builder (bridge detection + payload)**
  - Name: `bridge-builder`
  - Role: Fixes 1 and 2 — `bridge/update.py` payload extraction and `_scan_for_warnings`
  - Agent Type: builder
  - Resume: true

- **Builder (env completeness)**
  - Name: `env-builder`
  - Role: Fix 3 — `scripts/update/verify.py` optional/required split, `.env.example` tagging
  - Agent Type: builder
  - Resume: true

- **Builder (warn_state)**
  - Name: `warnstate-builder`
  - Role: Fix 4 — `scripts/update/warn_state.py` reader/CLI, `run.py` routing and trailer
  - Agent Type: builder
  - Resume: true

- **Reviewer**
  - Name: `channel-reviewer`
  - Role: Cross-cutting review, especially that suppression never becomes invisible and the detector cannot over-fire
  - Agent Type: code-reviewer
  - Resume: true

## Step by Step Tasks

### 1. Bridge detection + payload integrity
- **Task ID**: build-bridge
- **Depends On**: none
- **Validates**: `tests/unit/test_bridge_update.py`
- **Assigned To**: bridge-builder
- **Agent Type**: builder
- **Parallel**: true
- Extract `_scan_for_warnings(status_lines) -> bool` as a module-level function in `bridge/update.py`; wire `handle_update_command` to it.
- Recognize all three grammars: legacy prefixes, anchored `(N warnings)` with N > 0, and lines starting with `⚠️`.
- Rewrite `_queue_fix_session` to deliver the complete warning block uncapped, plus the `<<FILE:>>` log path, plus a head+tail-bounded raw excerpt with an explicit elision marker. Remove the `[:500]` slices.
- **Write `test_cron_summary_warnings_trigger_fix_session` FIRST and capture its failure against unmodified `bridge/update.py`** — this red-state output goes in the PR body.
- Add the zero-warning negative test, the payload-completeness test, and the log-path test.

### 2. env-completeness required/optional split
- **Task ID**: build-env
- **Depends On**: none
- **Validates**: `tests/unit/test_env_completeness.py`
- **Assigned To**: env-builder
- **Agent Type**: builder
- **Parallel**: true
- Teach `_parse_env_example` the `# optional:` tag (case-insensitive, `^optional\b`), excluding the tag line from the description.
- Join the comment block into a capped single-line description instead of taking only the last line.
- Narrow `check_env_completeness` to report missing **required** keys only; surface the optional-unset count in `version`.
- Tag all 27 keys in `.env.example` and document the convention in its header.
- Replace `test_multiline_comment_block_uses_last_line`; add the four new optional/required tests.

### 3. warn_state reader, CLI, and routing
- **Task ID**: build-warnstate
- **Depends On**: none
- **Validates**: `tests/unit/test_update_warn_state.py`, `tests/unit/test_update_cron_summary.py`
- **Assigned To**: warnstate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `active(project_dir) -> dict[str, str]` and `_main()` to `scripts/update/warn_state.py`; update the module docstring.
- Route `run.py:1029` (`gws-auth`) and `run.py:1494` (`redis-acl-drift`) through `should_emit`, clearing on resolve.
- Compose the `suppressed:` trailer in the summary block (`run.py:2482-2494`) — **after** all checks, per Race 2 — for both the verbose log and the `--cron` summary.
- Add the `active()` tests and the trailer-ordering test.

### 4. Cross-cutting review
- **Task ID**: review-channel
- **Depends On**: build-bridge, build-env, build-warnstate
- **Assigned To**: channel-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify no suppressed condition is unreachable: every `should_emit` key must appear in `active()` output and the trailer.
- Verify the detector cannot over-fire on the trailer or on `(0 warnings)`.
- Verify `redis_acl.apply_redis_acl()` is still called with no arguments (#2645 Risk 8 regression).
- Verify no `Co-Authored-By` trailers and no commented-out legacy code.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: review-channel
- **Assigned To**: channel-reviewer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/update-warning-channel.md`, add the README index entry.
- Update CLAUDE.md Secrets section and `.claude/skills/update/SKILL.md`.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: channel-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table end to end and confirm every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Bridge update tests | `scripts/pytest-clean.sh tests/unit/test_bridge_update.py -q` | exit code 0 |
| Env completeness tests | `scripts/pytest-clean.sh tests/unit/test_env_completeness.py -q` | exit code 0 |
| warn_state tests | `scripts/pytest-clean.sh tests/unit/test_update_warn_state.py -q` | exit code 0 |
| Cron summary tests | `scripts/pytest-clean.sh tests/unit/test_update_cron_summary.py -q` | exit code 0 |
| Redis ACL contract intact | `scripts/pytest-clean.sh tests/unit/test_redis_acl.py -q` | exit code 0 |
| gws auth contract intact | `scripts/pytest-clean.sh tests/unit/test_gws_auth.py -q` | exit code 0 |
| No 500-char slice remains | `grep -c 'stdout\[:500\]\|stderr\[:500\]' bridge/update.py` | match count == 0 |
| Real env check reports no required gaps | `.venv/bin/python -c "from pathlib import Path; from scripts.update.verify import check_env_completeness as c; r=c(Path('.')); print(r.available)"` | output contains True |
| warn_state CLI works | `.venv/bin/python -m scripts.update.warn_state` | exit code 0 |
| `/update` apply-gate untouched | `grep -c 'apply_redis_acl()' scripts/update/run.py` | output > 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

**Critique round 1, 2026-08-17 — DUPLICATE-PLAN FINDING.** The war room for issue #2845 was routed at
a *different* document, `docs/plans/update-warning-channel-repair.md` (commit `c60473fca`, written 10
seconds after this one at `76fd31e52`). Both documents carry `tracking:
https://github.com/tomcounsell/ai/issues/2845`, and `tools/lane_identity.py::find_plan_path` returns
the first alphabetical match — **this file** — so every SDLC stage tool for #2845 resolves here while
the critiqued design lives in the sibling. All three critics independently flagged the collision as a
BLOCKER. The full 8-finding table for the critiqued design is in
`docs/plans/update-warning-channel-repair.md` § Critique Results; only the finding that applies to
*this* document is reproduced below.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness, Scope & Value, History & Consistency (3/3) | Issue #2845 has two live plan documents with materially different designs (this one proposes `_scan_for_warnings`, an `optional:` prose-prefix marker, a `<<FILE:...>>` log-path marker, and an always-on `suppressed:` visibility line; the sibling proposes `extract_update_warnings`, an `@optional` sigil, and no suppressed-visibility line). Exactly one may survive. Until then the pipeline builds an uncritiqued document. | pending | `find_plan_path` (`tools/lane_identity.py:170-179`) iterates `sorted(plans_dir.iterdir())` and returns on first `tracking:` match, with no tie-break; `"...integrity.md" < "...repair.md"` lexically. Reconcile by folding this document's distinct ideas — the `<<FILE:...>>` log-path marker and the always-on `suppressed:` visibility line, neither of which appears in the sibling — into `update-warning-channel-repair.md`, then `git rm` this file. Verify with `python -c "from tools.lane_identity import find_plan_path; print(find_plan_path(2845))"`, which must print the `repair` path before `/do-build` runs. |

---

## Open Questions

None. Scope, the `.env.example` tag convention (explicit machine-readable tag, untagged-means-required), and the suppression-must-stay-retrievable requirement were all settled with the PM before planning.
