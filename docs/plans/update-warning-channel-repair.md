---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-17
tracking: https://github.com/tomcounsell/ai/issues/2845
last_comment_id: 5317246173
revision_applied: true
revision_applied_at: 2026-08-19T05:27:35Z
---

# /update Warning Channel Repair

## Problem

`/update` runs every 30 minutes under `com.valor.update` on every machine in the fleet. When it finds something wrong, four independent defects conspire so that nobody — human or agent — reliably learns what it found, and the one warning that *is* legible tells the operator to do something that would take the fleet down.

**Current behavior:**

A run on "Valor the Bald" at `4d9118125` reported 3 warnings. The auto-spawned fix session received a payload cut off mid-word at exactly 500 characters (`(or: gws auth setup && gws`), showing part of warning #1 and none of #2 or #3. The same SHA on "Valor the Captain" reports 2 of the same warnings (`gws auth` is clean there), confirming the condition is fleet-wide, not machine-local.

1. **`_queue_fix_session` truncates at 500 chars.** `bridge/update.py:120-121` builds the fix session's whole brief from `stdout[:500]` / `stderr[:500]`. The npm/git-pull preamble eats ~370 of those 500 characters, so the warning list — the entire reason the session exists — is what gets cut.
2. **`has_warnings` cannot see the cron summary.** `bridge/update.py:291` scans for line prefixes `("[update] WARN", "WARNING:", "ERROR", "RESTART FAILED")`. `run.py --cron` emits `up to date at <sha> (N warnings)` followed by indented `  ⚠️ <text>` bullets (`scripts/update/run.py:2487-2492`), and on failure `update failed at <sha>` followed by `  - <err>`. **None of those four forms matches any prefix.** In the observed run a fix session was queued only because `scripts/update/verify_release.py:147` incidentally printed `WARNING: <name> release could not be confirmed (unknown)`. An `/update` with real warnings and a clean release-verify queues no fix session at all.
3. **`env-completeness` floods the operator with false positives, and its documented remediation is destructive.** `check_env_completeness` (`scripts/update/verify.py:1088-1134`) does a flat `declared_keys - present` set difference with no notion of required vs optional. `.env.example` declares 89 keys; how many the check flags depends on the machine's vault `.env` (27 on the machine that filed the issue, 64 on the machine holding this lane's checkout — see Freshness Check). On every machine the flagged set is dominated by behaviour toggles with in-code defaults rather than by anything a machine actually needs. Worse, `docs/features/env-completeness-validation.md:61` instructs the operator to *"edit `~/Desktop/Valor/.env` directly"* — and a large fraction of the flagged keys are declared in `.env.example` with an **empty** value (11 of the 27 there, 19 of the 64 here), so following that instruction literally means writing `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS=` into the shared iCloud vault, which fails `Settings()` construction at import:

   ```
   pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
   features.crash_autoresume_max_attempts
     Input should be a valid integer, unable to parse string as an integer
     [type=int_parsing, input_value='', input_type=str]
   ```

   `config/settings.py` is imported by the bridge, the worker, and every `tools.*` CLI, and `.env` is a symlink to the one vault file every machine shares. The check's own published remediation, applied to a key the check itself names, takes the fleet down. Two of the flagged keys (`DISK_RECLAIM_APPLY`, `MEMORY_DECAY_PRUNE_APPLY`) are irreversible-destructive arming flags whose own descriptions read `Default: unset/false (dry-run)` — the warning is nagging the operator to set a key that documents itself as "leave unset".

   The emitted text is also unusable: `_parse_env_example` takes the **last** comment line of a block as the description, which for wrapped multi-line comments is a sentence fragment (`HOOK_DETACH_MAX_INFLIGHT (foreign repos too.)`), and the flagged keys concatenate into a single-line warning running to roughly 2,100 characters at 27 keys and proportionally worse on a machine flagging more.

4. **Two permanently-unresolvable warnings bypass `warn_state`.** `warn_state.should_emit` collapses a human-gated check to one emission per state transition. Three checks use it today, not two: `google-token` and `sms_reader` through the `human_gated_tools` set (`scripts/update/run.py:2275`), plus **`calendar-config`, wired bespoke** at `run.py:2354` / `:2365` outside that set. The bespoke third consumer matters twice over — it is evidence that the "remember to opt in at the call site" pattern keeps producing one-offs, and it means `active()` on a live machine already returns keys this plan does not otherwise discuss, so the `suppressed:` line must be written to enumerate whatever is in the state file rather than a hardcoded list of the keys this plan adds. `gws auth` (`run.py:1029`) and `Redis ACL drift` (`run.py:1494`) do not, so they re-emit 48×/day forever. Both are genuinely human-gated: `gws` needs browser OAuth consent, and the Redis ACL apply is double-gated behind `data/redis-acl-enabled` + `REDIS_ACL_APPLY=true` which `/update` never supplies by design (#2645 Risk 8).

Beyond the filed issue, the diagnosis surfaced a fifth, adjacent defect: **three `.env.example` declarations have no reader anywhere in the codebase**, and two feature docs describe them as live controls. A required/optional split alone would silently relabel this cruft as legitimate configuration.

**Desired outcome:**

An `/update` warning reliably reaches a human or an agent, in full, exactly once per state transition; `env-completeness` reports only genuinely missing required values; and no declared env key exists without a reader.

## Freshness Check

**Baseline commit (recon rounds 1–4):** `4d9118125`
**Re-verified at:** `f491306c5` — 2026-08-19, 71 commits after the baseline
**Issue filed at:** `2026-08-17T15:04:19Z`
**Disposition:** Minor drift

**The repo surface this plan modifies is byte-identical to the baseline.**

```
git log --oneline 4d9118125..f491306c5 -- \
  bridge/update.py scripts/update/run.py scripts/update/verify.py \
  scripts/update/warn_state.py .env.example
```

returns **empty**. None of the 71 intervening commits touched a file this plan
changes, and `git diff --stat` across the same range for `.env.example` and
`verify.py` is likewise empty. Every line number below is therefore re-confirmed
**exact at `f491306c5`**, not merely "close enough".

**File:line references re-verified at `f491306c5`:**

- `bridge/update.py:291-292` — `_warning_prefixes` tuple and the `has_warnings` scan — **exact, verbatim**. The tuple is still `("[update] WARN", "WARNING:", "ERROR", "RESTART FAILED")` and the scan is still `any(line.strip().startswith(_warning_prefixes) for line in status_lines)`.
- `bridge/update.py:120-121` — `stdout[:500]` / `stderr[:500]` in `_queue_fix_session` — **exact, verbatim**.
- `bridge/update.py:214` — the `<<FILE:` marker parse site (currently used only to discard) — **exact**.
- `scripts/update/run.py:2482-2494` — the `--cron` summary render — **exact**, and still a mutually-exclusive `if not result.success: / elif result.warnings: / else:`. `update failed at {sha}` + `  - {err}`; `{updated to|up to date at} {sha} ({N} warning{s})` + `  ⚠️ {warn}`; `update successful`.
- `scripts/update/run.py:2519` — `print(f"<<FILE:{log_file}>>")` — **exact**.
- `scripts/update/verify.py:1088-1134` — `check_env_completeness`, still a flat `declared_keys - present` set difference with no required/optional axis; `except OSError` at `1136-1141`. `_parse_env_example` at `1049`, still returning `list[tuple[str, str]]`. **All exact.**
- `scripts/update/run.py:1029` — gws auth `result.warnings.append` — **exact**, and still bypasses `warn_state` (a second bare append sits at `1032`).
- `scripts/update/run.py:1480-1497` — Redis ACL drift block; the `result.warnings.append` at `1494-1497` — **exact**, still bypassing `warn_state`, still calling `apply_redis_acl()` argument-free.
- `scripts/update/run.py:2275` — `human_gated_tools = {"google-token", "sms_reader"}` — **exact**; the loop and its resolution branch run to `2291` with `should_emit` at `2280` and `2290`.
- `scripts/update/run.py:2354` / `:2365` — the bespoke `calendar-config` `should_emit` wiring outside the `human_gated_tools` set — **exact, both lines**.
- `scripts/update/warn_state.py` — 79 lines, exporting `_state_path`, `_load`, `_save`, `should_emit` and **nothing else**. No `active()`, no `_main()`, no reader surface of any kind. **Confirmed unchanged.**
- `.env.example:197` `OLLAMA_URL`, `:201` `OLLAMA_VISION_MODEL`, `:446` `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES` — **exact**, and all three still have zero readers (`git grep` outside `.env.example` and `docs/plans/` returns only the two doc lines this plan already corrects).
- `docs/features/env-completeness-validation.md:61` (destructive remediation), `docs/features/headless-session-runner.md:165`, `docs/features/config-architecture.md:46` — **all three exact**.
- `scripts/update/redis_acl.py:459` — `def _main()`, the pattern Task 4's `warn_state` CLI mirrors — **exact**.
- Measured counts in `scripts/update/run.py`: `result.warnings.append` = **82**, `result.warnings.append(f` = **48**. **Both unchanged.**
- Every test file named in Test Impact and Verification exists; `tests/unit/test_env_completeness.py` still holds **14** tests; #1898's regression test is at `tests/unit/test_bridge_update.py:186`. No test references `_warning_prefixes` or `has_warnings` by name, so the Test Impact row that hedged on a grep resolves to "none found".
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

**Commits on main since issue was filed (touching referenced files):** none, across both windows. The original check ran at `4d9118125`; the re-verification at `f491306c5` covers 71 further commits and finds the same answer.

**One material change, and it is environmental rather than committed: the `env-completeness` flagged count is per-machine, and 27 was never a repo fact.**

`check_env_completeness` computes `declared_keys - present`, where `present` comes from the vault `.env` — a per-machine, un-versioned file. `.env.example` declares **89** keys and has not changed. On the machine where rounds 1–4 were written the difference was **27**. On the machine holding this lane's checkout it is **64** (77 keys present in that vault, 89 declared). Neither number is wrong; the *quantity itself* is not a property of the repository, and the plan previously read as though it were.

This is not a cosmetic recount, because the wider set changes what the work means:

- **The extra keys flagged here include genuine credentials** — `OP_SERVICE_ACCOUNT_TOKEN`, `SDLC_AGENT_GH_TOKEN`, `LINEAR_API_KEY`, `SENTRY_DSN`, `STRIPE_API_KEY`, `REDIS_URL`, `HEADSCALE_PREAUTH_KEY`. A builder who derives the `@optional` set by running the check on whatever machine they happen to be on would mark all of these optional. That is **Risk 1 realised on the first commit** — the check goes permanently quiet for exactly the secrets it exists to catch.
- **The `@optional` set must therefore be derived from a criterion, not from a live set difference.** Rounds 1–4 established the criterion (a traced read site with an in-code default, and not a credential); they simply expressed its result as a number taken from one machine. The Technical Approach and Task 2 below now state the criterion and drop the machine-local counts. No design decision changes.
- The plan's claim that `OLLAMA_URL` and `OLLAMA_VISION_MODEL` "are present in the vault `.env`, so the completeness check never flags them" was true on the authoring machine and is **false here** — both are flagged. The deletion of those two declarations never depended on that claim (they are deleted because nothing reads them), so the conclusion is unaffected and the reasoning is corrected in place.
- The "11 of the 27 are declared with an empty value" hazard re-measures to **19 of the 64** here, and the hazard is unchanged and reconfirmed: `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS` and its siblings are still declared empty, so the remediation at `docs/features/env-completeness-validation.md:61` still breaks `Settings()` construction fleet-wide if followed.

**Suppressed-key state on this machine has grown from two keys to three:** `data/update_warn_state.json` now holds `calendar-config`, `google-token`, **and** `sms_reader`. This is the Prior Art evidence for Risk 4 getting stronger, not weaker, and it is exactly why Task 4's trailer test asserts *membership* rather than set equality.

**Active plans in `docs/plans/` overlapping this area:** **one, and it claimed the same issue.** `docs/plans/update-warning-channel-integrity.md` (created by `76fd31e52`, ten seconds before this plan's `c60473fca`) carried the identical `tracking: .../issues/2845` with a materially different design. Because `tools/lane_identity.py::find_plan_path` returns the first alphabetical `tracking:` match and `"...integrity.md" < "...repair.md"`, every SDLC stage tool resolved to the sibling and this plan was unreachable to automation. That sibling has been **folded into this document and deleted** (revision round 1, critique BLOCKER 1); `find_plan_path(2845)` now returns this file. The original claim in this section — that no plan overlapped — was false when written and is corrected here rather than quietly edited away.

**Notes:** Every claim in the issue survives re-verification, and at `f491306c5` the code claims survive it *exactly* — no line number in this plan needed correcting on the re-check. The only substantive correction is the machine-dependence of the `env-completeness` count, handled above and propagated into the Technical Approach, Task 2, Success Criteria, and the Verification table. Two claims were *strengthened* during earlier re-verification: the empty-value hazard (Finding A, reproduced live) and the three dead declarations (Findings B/C), both recorded in issue comment 5317246173.

## Prior Art

- **#1140**: `.env.example`: add per-variable comments + update-time completeness check — **this is the check being fixed**. It shipped the comment convention and `check_env_completeness`. It succeeded at its stated goal (surfacing keys declared but absent) but encoded a premise that was true then and false now: that everything in `.env.example` is a value a machine ought to hold. The file has since accumulated behaviour toggles, apply-gates, and per-machine switches. This plan does not revert #1140; it adds the missing required/optional axis.
- **#2329**: Missing Google OAuth token — `/update` warns every 30 minutes with no resolution path — **closed by building `scripts/update/warn_state.py`**. Established the "one emission per state transition" pattern for human-gated checks. This plan extends the same mechanism to two more checks rather than inventing a second one.
- **#2328**: No Full Disk Access granted on this machine — the sibling of #2329, second consumer of `warn_state`.
  - **Live evidence that suppression is already invisible.** `data/update_warn_state.json` on this machine currently holds three suppressed keys — `calendar-config`, `google-token`, and `sms_reader` — and the *only* record of that fact anywhere is a gitignored JSON file. The mechanism provably works; it has no retrieval surface. The set is per-machine and it moves: at rounds 1–4 this same file held two keys, and the run that opened #2845 was on a different machine showing a different key again. Do not hard-code any of these sets — the point is that no surface exists on any machine, and that the population is live. This is the empirical case for Risk 4 and for the `active()` / `suppressed:` work below.
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

**Root cause pattern:** in all three, the fix was applied at a specific call site instead of at the boundary every producer crosses. `/update` has no single "emit a warning" seam — warnings are appended to `result.warnings` from **82** places (measured) and rendered in two different formats — so each new producer silently re-opens whichever hole the last fix closed. This plan does not attempt to build that seam (see No-Gos); it makes the two *consumers* (`has_warnings`, `_queue_fix_session`) format-aware and parses the rendered output, which is the one surface every producer provably reaches.

## Architectural Impact

- **New dependencies**: none. `warn_state`, `redis_acl`, and `verify` are all existing first-party modules.
- **Interface changes**: `_parse_env_example` returns `list[tuple[str, str, bool]]` — `(key, description, optional)` — instead of today's `list[tuple[str, str]]`. The 3-tuple, not a record type: Task 2 and Test Impact already specify "the 3-element return", and two Verification rows unpack it positionally, so the shape is pinned rather than left to builder choice. It is an internal helper with two callers, both in `verify.py` (`:1108`) and its tests (`tests/unit/test_env_completeness.py:149`). `bridge/update.py` gains one pure module-level helper, `extract_update_warnings(status_lines) -> list[str]`, exported for tests.
- **Coupling**: decreases slightly. Today `bridge/update.py` knows an ad-hoc prefix tuple that duplicates knowledge of `run.py`'s output format implicitly; after this, the two summary formats are named and parsed in one place with a test that pins them against `run.py`'s actual emission.
- **Data ownership**: unchanged. `warn_state`'s JSON state file (`data/`, gitignored) gains two more keys.
- **Reversibility**: high. Every change is additive or a marker convention; reverting the `# @optional` annotations restores the old (noisy) behaviour with no data migration.

## Appetite

**Size:** Medium

**Team:** Six agents over eight tasks — three builders on disjoint files, a code reviewer, a documentarian, and a validator (see Team Orchestration). The three builders are parallel; everything after them is sequential, including Task 5, where `update-channel-builder` returns to `bridge/update.py` once `warn_state` exports the shared trailer constant. This is larger than the "solo dev" the first draft assumed, because folding the suppression-visibility work in added a third build surface (`warn_state` retrieval + the summary trailer) and a cross-cutting reviewer to cover the seam between builders 1 and 3. The appetite stays **Medium**: the roster is wider, not longer, and no task grew.

**Interactions:**
- PM check-ins: 3 — one per Open Question below (scope confirmation, the `@optional` sigil convention, and whether any key the criterion marks optional is genuinely required somewhere). Each is a decision only a human can make, so they are check-ins rather than spikes.
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Vault `.env` readable | `python -c "from pathlib import Path; assert (Path.home()/'Desktop'/'Valor'/'.env').exists()"` | `check_env_completeness` tests read the real declaration surface |
| `redis-cli` on PATH | `command -v redis-cli` | The Redis ACL drift path is exercised report-only in tests |
| Pinned-interpreter venv | `.venv/bin/python -c "import sys,pathlib; pin=pathlib.Path('.python-version').read_text().strip(); v='.'.join(map(str,sys.version_info[:2])); assert pin.startswith(v), f'venv {v} != pin {pin}'"` | `scripts/pytest-clean.sh` aborts outright on an off-pin venv, so every Verification row that shells out to it fails for a reason unrelated to this work. Note the explicit `.venv/bin/python`: a bare `python` resolves to the system interpreter and would report the *checker's* version, not the venv's — which is the drift this row exists to catch |

## Data Flow

1. **Entry point**: `com.valor.update` launchd cron (every 30 min) or a Telegram `/update` message, both landing in `bridge/update.py::handle_update_command`.
2. **`scripts/remote-update.sh` → `scripts/update/run.py --cron`**: runs ~30 steps, each appending human-readable strings to `result.warnings`. Two of those appends are the ones this plan gates (`run.py:1029` gws auth, `run.py:1480-1497` Redis ACL drift); one is the `valor_tools` loop at `run.py:2266-2290` that surfaces `check_env_completeness`.
3. **`run.py:2482-2494` renders the summary**: `update failed at {sha}` + `  - {err}` lines, or `{updated to|up to date at} {sha} ({N} warning{s})` + `  ⚠️ {warn}` lines, or `update successful`. This rendered text is stdout.
4. **`bridge/update.py:291` classifies**: `has_warnings` prefix-scans `status_lines`. *This is where the signal is currently lost.*
5. **`bridge/update.py::_queue_fix_session`**: builds a `message_text` from `stdout[:500]`/`stderr[:500]` and enqueues an AgentSession with `project_key="ai"`, `priority="low"`. *This is where the surviving signal is truncated.*
6. **Output**: an Eng session wakes with the brief. Separately, on the interactive path only, `handle_update_command` sends a Telegram message carrying **the bridge's own status** — SHA, reload states, stale details, session count — which is composed independently and never quotes `run.py`'s stdout or any warning text (`bridge/update.py:270-297`). The unattended cron path has no chat at all: launchd runs `scripts/remote-update.sh` directly into `logs/update.log`. This asymmetry is why the `suppressed:` trailer has to be forwarded explicitly rather than assumed to ride along.

The two lossy hops (4 and 5) are adjacent and consume the same `status_lines`/`stdout` values, which is why one shared parser fixes both.

## Solution

### Key Elements

- **`extract_update_warnings(status_lines)`** — a pure helper in `bridge/update.py` that recognises every format `run.py` actually emits (the `(N warnings)` summary and its `⚠️` bullets, the `update failed at` summary and its `-` bullets, and the four legacy line prefixes) and returns the warning texts. Both lossy hops consume it.
- **Warning-first fix-session payload** — `_queue_fix_session` leads with the complete extracted warning list, never truncated; the raw stdout/stderr tail follows under a much larger cap, cut on line boundaries and marked with an explicit `[... N characters elided ...]` so a cap can never again *silently* swallow a tail. The log path comes from the `<<FILE:...>>` marker the producer already emits, not from an existence check on a guessed path.
- **A required/optional axis for `.env.example`** — an explicit `# @optional` marker in a key's comment block. Unmarked means required, so a newly-added secret still warns: the fail-closed direction.
- **Deletion of dead declarations** — three keys with no reader are removed from `.env.example`, and the two feature docs that describe them as live are corrected.
- **A recurrence guard** — a test asserting every `.env.example` declaration has a reader, so the dead-key class cannot silently return.
- **`warn_state` for the two human-gated warnings** — `gws auth` and Redis ACL drift emit once per state transition and once again on resolution, mirroring the `human_gated_tools` pair `google-token` / `sms_reader`. (`calendar-config` is a third existing consumer but is wired bespoke outside that set, so it is not the shape to copy — see Problem point 4.)
- **A retrieval surface for suppressed warnings** — `warn_state.active()` plus a `python -m scripts.update.warn_state` CLI, and an always-on `suppressed:` line in every run's output. Suppression without retrieval is how a real condition goes dark; this plan will not ship the fourth and fifth suppression keys without it (Risk 4).

### Flow

`/update` cron fires → run.py renders a summary → **`extract_update_warnings` parses it** → warnings found → **fix session receives the full list up front** → agent reads warnings → agent fixes or reports → next run's state differs → **`warn_state` lets the changed warning through**

### Technical Approach

**Defect 2 first** (it is the dependency): add `extract_update_warnings(status_lines: list[str]) -> list[str]` to `bridge/update.py` as a module-level pure function. It must recognise:

- `  ⚠️ <text>` bullets (strip, then match the sentinel) — the cron warning form.
- The `(N warnings)` / `(N warning)` summary line, matched with the anchored pattern
  `^(?:updated to|up to date at)\s+\S+\s+\((\d+)\s+warnings?\)$` and **requiring N > 0**. It is used to cross-check the bullet count; a mismatch is itself worth surfacing rather than silently trusting either. The `N > 0` requirement is load-bearing and separately tested: a parser that matches the summary shape but ignores the captured count treats a perfectly healthy `(0 warnings)` line as a warning and spawns a fix session 48×/day. That bug passes every other test in this plan.
- `  - <text>` bullets that follow an `update failed at <sha>` line — the cron failure form. These are *state-dependent*: a bare `  - ` line elsewhere in the log (npm output, git diffstat) must not match, so the parser tracks whether it is inside a failure block rather than matching the bullet in isolation.
- The four existing prefixes `("[update] WARN", "WARNING:", "ERROR", "RESTART FAILED")`, kept verbatim — the non-cron `--verify` path still emits them and #1898's over-match analysis still applies (line-anchored, never bare substring).

`has_warnings` becomes `bool(extract_update_warnings(status_lines))`. Keep the existing `failed` short-circuit untouched.

**Defect 1**: rewrite `_queue_fix_session`'s message assembly to put the extracted warning list first, in full, with no cap. The stdout/stderr tail moves below it under a cap raised to a few thousand characters and applied by whole lines (`"\n".join(lines[-K:])` shape) so nothing is ever cut mid-word, and any elision is marked in-band with `[... N characters elided ...]`. A silent cut is what produced this defect; a marked cut is recoverable, because the reader can tell there is more and go get it. Pass the warning list in as a parameter rather than re-deriving it — the caller already has it.

For the log pointer, parse the `<<FILE:<path>>>` marker `run.py:2519` already prints rather than testing for a hardcoded `data/update.txt`. The marker is the producer's own statement of where it wrote, so it stays correct if the path convention changes, and `bridge/update.py:214` already recognises the marker in order to strip it from status lines — the parse site exists and is currently used only to discard the information.

**Multi-line warning texts** (critique CONCERN). The parser is specified against single physical lines, but `result.warnings` entries are built from arbitrary interpolated values — including exception `str()`s — at **82** `result.warnings.append(...)` sites in `run.py`, 48 of them f-strings (measured, not estimated — earlier drafts of this plan carried two different hand-waved approximations in different sections, each understating the real figure by more than fourfold). An exception string can embed newlines — this plan's own Problem section quotes a multi-line pydantic `ValidationError` as exactly that shape. Since `run.py:2482-2494` renders with `status += f"\n  ⚠️ {warn}"`, only the first physical line carries the sentinel and a naive parser drops the tail, reproducing the very truncation defect this plan exists to fix through a different producer. Fix it by normalising the text before it is appended, so one warning is one line by construction. That is preferred over teaching the parser to accumulate continuation lines, because a continuation rule cannot distinguish a wrapped warning from unrelated indented output and would re-open Risk 2.

Add `_append_warning(result, text)`, which collapses newlines, and convert **all 82** call sites to it — not just the three groups this plan otherwise touches.

The narrower scope was tried in revision round 2 and is wrong, for a reason worth recording because it is not obvious: the `(N warnings)` vs bullet-count cross-check that this plan offers as its safety net **cannot detect this failure at all**. `run.py:2486-2492` sets `w_count = len(result.warnings)` and then emits exactly one `⚠️` bullet per entry regardless of that entry's content, so the two numbers agree even when an entry embeds a newline. A multi-line exception appended raw at any unconverted site therefore renders as one bullet whose tail the parser drops, while the cross-check reports a clean match. A "known, bounded gap" is only acceptable when something can see it; this one is invisible by construction, which makes it an unbounded gap wearing a bound.

Converting everything is also cheap and in-scope: `result` is in scope at all 82 sites, so it is a mechanical rename, and *text normalisation is not the typed-record redesign* the No-Go excludes — that No-Go is about giving warnings structure (severity, human-gated flags), not about whether a string may contain a newline. The recurrence guard is a test asserting no entry in `result.warnings` contains a newline at render time, so a future unconverted site fails loudly rather than silently truncating. Test with a warning whose source text contains an embedded newline, asserting the COMPLETE text survives extraction.

**Defect 3**, in three parts:

1. *Parser.* `_parse_env_example` gains optional-marker recognition. The marker is the literal token `@optional` appearing on its own comment line in a key's block. It must be a token that cannot occur in prose — `.env.example` contains **seven** comment lines using the word "optional" as prose (`# OpenAI API Key (Optional - ...)` at `:73`, `# Optional. GitHub PAT for ...` at `:135`, and five more; re-counted at `f491306c5`), and the `:135` one begins the line directly above the `SDLC_AGENT_GH_TOKEN` credential, so a bare `^optional\b` match would silently mark a real secret optional. The `@` sigil is what makes the match safe. The marker line must be excluded from description candidates so it never becomes the printed description.

2. *Description quality.* Switch the description to the **first** non-empty comment line of the block instead of the last. For single-line comments (the common case) this is identical; for wrapped blocks it yields the topic sentence instead of a fragment. Update `docs/features/env-completeness-validation.md`, which currently specifies "last", and the tests that pin it. Cap the rendered warning on **both** axes: at most 5 keys inline with a `(+N more — run <command> for the full list)` suffix, **and** a per-description character cap with an ellipsis. The key cap alone bounds how many descriptions appear, not how long any one of them is, so a single 400-character description still reproduces the unusable-warning symptom.

   *Optional keys stay counted, not silently dropped.* Filtering them out entirely leaves no trace that the axis exists. Instead the `version` string carries the residual: `all N required vars present (M optional unset)`. This is also what makes the Verification row assertable — see the `CLEAN` row's note below.

3. *Annotation and deletion.* Mark every verified-optional declaration with `# @optional`.

   **The set is defined by a criterion, never by a live set difference** (critique BLOCKER 3, re-resolved at `f491306c5`). A declaration gets `# @optional` when **both** hold, each traced and recorded in the PR body as a `file:line` with the default quoted:

   - it has a read site in tracked code, and that read site supplies an **in-code default** so an unset value is a defined, safe state; and
   - it is **not a credential** — not an API key, token, password, DSN, connection URL, or account identifier. A credential is required by definition even when some code path tolerates its absence.

   Anything failing either test stays unmarked, which means required. Unmarked-is-required is the fail-closed direction: forgetting a marker costs one noisy warning, while a wrong marker silences a real secret forever.

   **Do not derive the marked set by running `check_env_completeness` and annotating whatever it reports.** That method looks rigorous and is the single most dangerous thing a builder could do here. The check's output is `declared_keys - present`, and `present` comes from the machine's own vault `.env` — earlier rounds measured 27 keys on one machine and the re-verification measures **64** on the machine holding this checkout (Freshness Check). The wider set is not more thorough; it sweeps in `OP_SERVICE_ACCOUNT_TOKEN`, `SDLC_AGENT_GH_TOKEN`, `LINEAR_API_KEY`, `SENTRY_DSN`, `STRIPE_API_KEY`, `REDIS_URL`, and `HEADSCALE_PREAUTH_KEY`, every one of which the second test rejects outright. Marking those is Risk 1 landing on the first commit. `SDLC_AGENT_GH_TOKEN` makes the point sharpest: its comment block opens `# Optional. GitHub PAT for a dedicated SDLC bot account` (`.env.example:135`), so prose-matching and machine-diffing would *both* mark a live credential optional, and the criterion above is what rejects it.

   The check's flagged set stays useful as a **starting worklist** — it is where the un-annotated toggles surface — but each candidate passes the two tests on its own merits before a marker is written.

   *Three declarations are deleted rather than marked*, on the independent ground that nothing reads them, which fails the first test at its first clause: `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES` (`.env.example:446`; #2494 removed the count-based trim it configured), `OLLAMA_URL` (`:197`), and `OLLAMA_VISION_MODEL` (`:201`). Re-confirmed at `f491306c5`: `git grep` outside `.env.example` and `docs/plans/` returns no reader for any of the three, only the two stale doc lines this plan corrects. Whether a given machine's check happens to flag them is irrelevant to that decision — and it does vary, which is why the earlier draft's parenthetical about the two `OLLAMA_*` keys being present in the vault has been dropped rather than re-measured. Also correct `docs/features/headless-session-runner.md:165` (documents a cap #2494 deleted) and `docs/features/config-architecture.md:46` (lists `OLLAMA_VISION_MODEL` as live with a default that disagrees with `.env.example`'s). Per Development Principle 1 these are deletions, not deprecations. Document the `@optional` convention in `.env.example`'s header block and in CLAUDE.md's Secrets section, and fix the destructive remediation text at `docs/features/env-completeness-validation.md:61`.

**The recurrence guard**: a test that every key declared in `.env.example` is read somewhere. The subtlety is pydantic-settings nested keys: `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS` never appears literally in the source — it resolves to `FeatureSettings.crash_autoresume_max_attempts` via the `FEATURES__` env prefix. So "has a reader" means: the literal key appears in a tracked file outside `.env.example` itself, **or** the key decomposes as `<PREFIX>__<FIELD>` where a settings model registered under `env_prefix="<PREFIX>__"` declares `field`. Resolve the second case by introspecting the `Settings` model's nested submodels rather than by maintaining a hand-written allowlist — an allowlist is how this defect class survives.

**Defect 4**: route `run.py:1029` (gws auth) and `run.py:1480-1497` (Redis ACL drift) through `warn_state.should_emit`, mirroring the `human_gated_tools` block at `run.py:2266-2290` including its resolution branch. The signature must encode the *content* of the drift, not just its presence, so that a change in drift re-warns: for the ACL use a stable digest of the planned commands with the password placeholder already substituted (the placeholder is what the report path emits, so no secret can reach the state file); for gws use the auth-method string. `python -m tools.doctor`'s `redis_acl` and `gws` checks remain unconditional on-demand surfaces, but a pull-only answer is not sufficient — it requires an operator who already suspects something is suppressed. Retrieval therefore has four parts, the last of which is the only one that reaches a reader without being asked:

- **`warn_state.active(project_dir) -> dict[str, str]`** returning the currently-suppressed key → signature map, fail-soft (`{}` on any error, matching the module's existing contract). The module today exports `_state_path`, `_load`, `_save`, and `should_emit` — it can write suppression state but nothing can read it back.
- **`python -m scripts.update.warn_state`** — a `_main()` mirroring `redis_acl.py::_main`, so there is a one-command answer to "what is suppressed right now?".
- **An always-on `suppressed:` line.** Whenever `active()` is non-empty, `run.py` emits one line in both the verbose log and the `--cron` summary:

  `suppressed (unchanged since first warning): gws-auth, redis-acl-drift — details: python -m scripts.update.warn_state`

  It deliberately carries neither `⚠️` nor any of the four legacy prefixes, so it is inert to `extract_update_warnings` and cannot re-trip the very detector this plan is fixing. That inertness is a tested property, not a hope (see Risk 2 and the Race 3 ordering constraint).

  Its leading token is a **named constant, `SUPPRESSED_PREFIX`, defined once in `scripts/update/warn_state.py`** and imported by both the `run.py` composer and the `bridge/update.py` reader below. `bridge/update.py` already imports from `scripts.update.*` lazily inside functions (`:46`, `:72-74`, `:337-339`), so this costs no new coupling. An independently-spelled prefix on each side is precisely the producer/consumer drift that produced Defect 2 in the first place, and it must not be reintroduced by the fix for Defect 4.

  **The trailer is emitted outside the summary's `if/elif/else`, not inside any branch.** `run.py:2482-2494` is mutually exclusive — `if not result.success` / `elif result.warnings` / `else: status = "update successful"` — and the *modal* case for a suppressed key is precisely the `else` branch: `should_emit` returned False, so nothing was appended and `result.warnings` is empty. A builder who nests the trailer inside `elif result.warnings:` still passes a test that suppresses one key while another warns, and the trailer silently disappears on exactly the common path Risk 4 exists to cover. Append it to `status` after the branch selection, unconditionally on `active()` being non-empty. The pinning test is **empty `result.warnings` plus non-empty `active()` must still render the `suppressed:` line** — not the both-present case, which is the one that passes either way.

- **The trailer is forwarded to the Telegram reply on the interactive path.** Composing the line into `run.py`'s stdout is necessary and, on its own, not sufficient: `bridge/update.py::handle_update_command` builds its Telegram message entirely from `sha`, `reload_states`, `stale_details`, and `running_count` (`bridge/update.py:270-297`) and never quotes `run.py`'s stdout, so a trailer that stops at stdout is read by nobody who did not go looking. So `handle_update_command` picks the line out of the `status_lines` it already computes at `:211-215` and appends it to `status` — **outside** the `if failed or has_warnings:` branch at `:293`, before the `send_message` at `:297`:

  ```
  suppressed_line = next(
      (l.strip() for l in status_lines if l.strip().startswith(SUPPRESSED_PREFIX)), None
  )
  ```

  Outside the branch is load-bearing for the same reason it is load-bearing in `run.py`: the modal suppressed case is a run with nothing else wrong, where `failed` and `has_warnings` are both False. The pinning test is a clean run — empty `result.warnings`, non-empty `active()` — asserting the trailer text appears **in the string passed to `tg_client.send_message`**, not merely in stdout.

  The plumbing for this already works: `scripts/remote-update.sh:167` runs `run.py --cron --no-pull` with stdout unredirected, so on the interactive path it lands in the `capture_output=True` subprocess at `bridge/update.py:197-204` and then in `status_lines`. What is missing is only the forwarding. Worth noting because `remote-update.sh:165` carries the comment *"Output goes directly to Telegram - keep it clean for PM-style summary"*, which is false today and is the belief that let this gap sit unnoticed — correct that comment as part of this work.

**Where the push lands, and where it cannot** (critique BLOCKER, round 5). Stating this exactly, because the previous draft claimed a suppressed condition would be "visible in every single run's output" and that claim was false on the path Risk 4 exists to cover.

| Path | Trigger | Reader at emission time | Surface after this plan |
|------|---------|------------------------|-------------------------|
| Interactive | A human types `/update` in Telegram | A human, already watching for the reply | **Push.** The trailer rides the Telegram reply, unconditionally — no warning need be present. |
| Unattended cron | `com.valor.update`, every 30 min | **None.** launchd runs `scripts/remote-update.sh` directly with `StandardOutPath` = `logs/update.log`; `bridge/update.py` is never entered, and `remote-update.sh:429` requires `UPDATE_REPORT_CHAT_ID`, which only `bridge/update.py:192` ever sets. There is no chat to send to. | **Pull.** The trailer lands in `logs/update.log` and `data/update.txt`, and `python -m scripts.update.warn_state` and `python -m tools.doctor` answer on demand. |

Pull-only is the right answer on the unattended path, and not merely the available one. A push there would have to invent a destination — a hardcoded chat id in a script that runs on every machine in the fleet, or a fix session enqueued 48×/day for a condition defined as unresolvable-without-a-human. The second is the respam this plan exists to end, re-spent as agent time; the first is a notification with no owner, and the watchdog precedent in CLAUDE.md is explicit that it "pushes no notification anywhere" and records to a log instead. What Risk 4 actually needs is that a suppressed condition stays *discoverable* by an operator who missed the one emission, and `/update` in Telegram is the gesture such an operator makes. After this change that gesture answers the suppression question every time.

**Recurrence guard for Defect 4** (critique CONCERN). This plan's own root-cause table blames #2329/#2328 for "wiring at the call site rather than at the warning-emission boundary," and Defect 4 then wires `should_emit` at two call sites. That is a real repetition of the pattern and this plan does **not** close it: building the emission seam is the No-Go below. What it adds instead is a cheap containment — the `suppressed:` line is generated from `active()`, which enumerates whatever is in the state file, so a future human-gated check that wires `should_emit` and forgets everything else still shows up in the trailer automatically. A check that forgets to opt in at all remains undetected, and that is the accepted, scoped exception, stated here rather than left as an implied fix.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/update.py::_queue_fix_session` wraps its body in `except Exception` and logs a warning (`bridge/update.py:136-137`). The rewritten body must keep that contract; add a test asserting that a raising `enqueue_agent_session` produces a `logger.warning` and does not propagate.
- [ ] `scripts/update/run.py:1508-1511` catches every exception around the Redis ACL step and appends a warning. The `warn_state` wiring must not move work outside that guard; add a test that a raising `should_emit` still leaves the update run successful.
- [ ] `check_env_completeness` catches `OSError` and returns `skipped (read error)` (`verify.py:1136-1141`). The new marker parsing must stay inside that guard.

### Empty/Invalid Input Handling
- [ ] `extract_update_warnings([])` returns `[]`; `extract_update_warnings` on whitespace-only lines returns `[]`; on a `update successful` summary returns `[]`.
- [ ] `_parse_env_example` on a key with no comment block at all yields `description=""` and `optional=False` (required — the fail-closed default).
- [ ] `_queue_fix_session` with an empty warning list still produces a usable brief (the `failed` path can have zero parsed warnings).
- [ ] `_queue_fix_session` with empty stdout **and** empty stderr produces a coherent message, not a bare header with nothing under it.
- [ ] `warn_state.active()` returns `{}` on a corrupt state file — `tests/unit/test_update_warn_state.py` already has the corrupt-file precedent to mirror.
- [ ] `extract_update_warnings` cannot raise on malformed stdout: no `<<FILE:` marker, no summary line, an empty string, and a summary line with a non-numeric count.

### Error State Rendering
- [ ] The `(N warnings)` count and the number of extracted bullets must agree; a test covers the mismatch path and asserts the discrepancy is surfaced rather than swallowed.
- [ ] A test asserts the fix-session `message_text` contains the *last* warning of a 27-warning list — the specific regression from the issue, where only warning #1 partially survived.
- [ ] `test_fix_session_brief_is_legible` asserts the three *shape* properties containment cannot reach: warning list before the tail (index comparison), an explicit `characters elided` marker on a cut whose every emitted line is a whole input line, and the `<<FILE:>>`-derived path present. Containment alone passes on a brief that buries the warnings under 4,000 characters of npm output, which is the legibility half of the original defect.
- [ ] `test_suppressed_trailer_reaches_telegram_on_clean_run` asserts that a run with no warnings and a non-empty `active()` still puts the trailer in the string passed to `tg_client.send_message`. The both-present case passes either way and proves nothing.
- [ ] **`(0 warnings)` must NOT trigger a fix session.** A summary line that is well-formed but reports zero is the inverse case, and it is the one a count-blind parser gets wrong while passing everything else here.
- [ ] The `suppressed:` trailer must NOT be extracted as a warning — fed through `extract_update_warnings`, a transcript containing it yields `[]`.
- [ ] A warning whose source text contains an embedded newline survives extraction **complete**, not truncated at the first physical line.

## Test Impact

- [ ] `tests/unit/test_env_completeness.py` — UPDATE: the 14 existing tests pin `_parse_env_example`'s 2-tuple return and the last-comment-line description rule. Both change. Update the tuple unpacking and flip the multi-line-comment-block test's expectation from last line to first line.
- [ ] `tests/unit/test_review_judge_env_docs.py` — UPDATE: it guards the `SDLC_REVIEW_*` slice of the dead-key class. Keep it, and add the general guard alongside rather than replacing it, so the narrow assertions stay as documentation of #2831.
- [ ] `tests/unit/test_ollama_consolidation.py` — no change expected; its `assert not hasattr(settings.models, "ollama_vision_model")` is what proves `OLLAMA_VISION_MODEL` is dead. Verify it still passes after the `.env.example` deletion.
- [ ] `tests/unit/test_bridge_update.py::test_warning_on_non_first_stdout_line_detected` — UPDATE (verify-only): this is #1898's regression test, the guard against re-narrowing detection to the first stdout line. It must stay green through the `has_warnings` → `extract_update_warnings` swap. Named explicitly rather than left to a grep, because it is the one existing test whose failure would mean this plan re-broke a fixed bug.
- [ ] `tests/unit/test_update_cron_summary.py` — UPDATE: it is the other consumer of the `run.py` summary format (`up to date at <sha>` / `updated to <sha>`), and this plan makes that format load-bearing for detection and adds the `suppressed:` trailer to the same output. Any change to the summary must keep this file green; it and `bridge/update.py` are the only two consumers, verified by grep.
- [ ] Any remaining test asserting on `bridge/update.py::_warning_prefixes` or `has_warnings` — locate via `grep -rn "_warning_prefixes\|has_warnings" tests/`; disposition UPDATE if found, since the tuple moves inside the new helper.
- [ ] `tests/unit/test_redis_acl.py` — no change expected: the apply-gate matrix is untouched. Verify the report-only call site assertion (which pins the literal `apply_redis_acl()` call with no arguments) still passes after the `warn_state` wrapping, since the wrapping is around the *warning emission*, not the call.

## Rabbit Holes

- **Building a unified "emit a warning" seam in `run.py`.** 82 call sites append to `result.warnings` with free-form strings. This plan routes all 82 through `_append_warning` for newline normalisation, which is text hygiene, not structure. Structuring them into typed warning records with severity and a human-gated flag is the architecturally correct fix and is a separate project. This plan parses the rendered output instead, which is the surface every producer already reaches.
- **Auditing all 89 `.env.example` declarations for optionality.** Only the keys the criterion actually clears need a marker; every other declaration stays unmarked, and unmarked-is-required means leaving it that way is correct rather than a half-migration. Resist the urge to sweep the whole file. Note this cuts the other way too now that the flagged set is known to be machine-dependent: the goal is not "annotate until the check goes quiet on my laptop", it is "annotate exactly what the criterion clears". A machine with an incomplete vault will still report missing *required* keys afterwards, and that is the check working.
- **Making `@optional` inferable from prose.** Most of the toggle-class declarations say "Provisional/tunable" or "default: N" in their comments. Regex-matching that prose would be keyword matching (Development Principle 3) and would misfire on the seven existing prose uses of "optional" — one of which sits directly above the `SDLC_AGENT_GH_TOKEN` credential. The explicit sigil is the whole point.
- **Removing `OLLAMA_URL` / `OLLAMA_VISION_MODEL` from the vault `.env` itself.** They are inert there. The vault is a private, iCloud-synced, human-owned file; this plan removes the *declarations* that advertise them as real.
- **Fixing the `gws auth` state on Valor the Bald.** It is genuinely unauthenticated and needs browser OAuth consent. Out of scope by CLAUDE.md; this plan only stops the respam.
- **Inferring optionality from `config/settings.py`.** A settings-derived heuristic ("has a pydantic field with a default ⇒ optional") looks tempting and is wrong. `HOOK_DETACH_DEADLINE_SECONDS` is explicitly documented as deliberately *not* wired into `config/settings.py` — it reads its own constant — so any such heuristic misclassifies it. This matters structurally, not just as advice: the dead-key guard in Task 3 resolves readers by introspecting the `Settings` model, so `HOOK_DETACH_DEADLINE_SECONDS` is the named case that guard must handle through the literal-occurrence leg, and it goes into `test_env_declaration_readers.py` as an explicit test case.
- **Rewriting the `/update` summary into JSON.** A structured summary would make parsing trivial and is still the wrong trade. The summary's readers are: a human opening `data/update.txt` or `logs/update.log` after something looks off, and exactly two code consumers (`bridge/update.py` and `tests/unit/test_update_cron_summary.py`, verified by grep). JSON would buy one regex in one of those two consumers and charge for it in the log a human actually opens — the artifact that, on the unattended path, is the *only* record the run leaves anywhere. (An earlier draft justified this No-Go by saying the summary is read by humans in Telegram; that was false, and the correction is recorded rather than quietly swapped: `handle_update_command` composes its own status string and never forwards `run.py`'s rendered summary. The conclusion stands on the readers that do exist.)
- **"Fixing" `scripts/update/verify_release.py:147`'s `WARNING:` line.** It is a legitimate warning. Its role in this story is incidental — it is the accident that caused a fix session to be queued at all, masking Defect 2. Once Defect 2 is fixed the accident stops mattering, and the warning itself should keep warning. Leave it alone.

## Risks

### Risk 1: `# @optional` is applied to a key that is genuinely required
**Impact:** The check goes quiet for a real missing secret — the exact failure #1140 existed to prevent, now silent instead of noisy.
**Mitigation:** Only declarations clearing **both** legs of the Defect 3 criterion — a traced read site with an in-code default, **and** not a credential — get the marker, each recorded in the PR body as a `file:line` with its default quoted. Unmarked is required, so the failure mode of *forgetting* the marker is a spurious warning (loud, cheap) rather than a missed secret (silent, expensive). The recurrence-guard test additionally proves every declared key has a reader, so a marker can never be hiding a key that does nothing.

**Re-verification at `f491306c5` sharpened this risk from hypothetical to demonstrated.** The flagged set is a function of the local vault, and on the machine holding this checkout it is 64 keys including `OP_SERVICE_ACCOUNT_TOKEN`, `SDLC_AGENT_GH_TOKEN`, `LINEAR_API_KEY`, `SENTRY_DSN`, `STRIPE_API_KEY`, `REDIS_URL`, and `HEADSCALE_PREAUTH_KEY`. Annotating "whatever the check reports" is therefore not a shortcut to the right answer; it is the concrete mechanism by which this risk fires. The second leg of the criterion exists specifically to reject that set, and the reviewer's checklist includes confirming no credential carries a marker.

### Risk 2: `extract_update_warnings` over-matches and spawns spurious fix sessions
**Impact:** Every green `/update` queues a low-priority Eng session — 48/day/machine of wasted agent time. This is exactly the regression #1898 fixed.
**Mitigation:** Keep every match line-anchored, never a bare substring scan. The `  - <text>` failure bullets match only inside a failure block, tracked by parser state, because that bullet shape occurs freely in npm and git output. A test feeds a realistic green `/update` transcript (npm output, git diffstat with filenames containing "error" and "warning") and asserts zero extracted warnings.

### Risk 3: `warn_state` suppression hides a *newly regressed* Redis ACL state
**Impact:** The ACL is applied, later reverted or drifts differently, and `/update` stays silent because it already warned once.
**Mitigation:** The signature is a digest of the planned commands, not a constant, so any change in the drift's *content* is a new signature and re-emits. The resolution branch (mirroring `run.py:2266-2290`) clears stored state and emits one resolved note, so a later regression warns again. `tools.doctor` remains an unconditional on-demand check.

### Risk 4: Suppression hides a real condition from an operator who never saw the one emission
**Impact:** Distinct from Risk 3, which covers the drift *changing* after suppression. This is the case where the drift never changes and the single emission was missed — scrolled past, sent to a chat nobody was reading, or emitted before the operator started. The machine is then unauthenticated, or production Redis still grants unrestricted flush to `default`, and `/update` says nothing about it ever again. That is strictly worse than the 48×/day nag it replaces, because the nag at least kept the condition on screen. Live evidence that this is real, not theoretical: two keys are suppressed on this machine right now and the only record is a gitignored JSON file (see Prior Art).
**Mitigation:** Retrieval is a **hard requirement of this plan, not a follow-up**, and the plan does not ship the fourth and fifth suppression keys without it. `warn_state.active()`, the `python -m scripts.update.warn_state` CLI, and the `suppressed:` trailer in `logs/update.log` / `data/update.txt` make the state answerable on demand; forwarding the trailer into the Telegram reply makes it *arrive unasked* on the interactive path, where a reader exists. The unattended cron path stays pull-only, because it has no chat context by construction — see the surface table in Technical Approach → Defect 4 for why inventing one would be worse than the gap it closes. `tools.doctor` remains the independent unconditional check.

### Risk 5: The `.env.example` edit collides with the merge-gate shape classifier
**Impact:** `.env.example` is in the docs-only allowlist (#1934). A PR touching it plus real Python could be misclassified.
**Mitigation:** This PR touches Python, tests, and docs together, so it will not classify as docs-only. Noted so a reviewer does not mistake the classification for a bypass attempt.

## Race Conditions

### Race 1: `warn_state` read-modify-write across concurrent `/update` runs
**Location:** `scripts/update/warn_state.py:30-60` (`_load` / `_save` / `should_emit`)
**Trigger:** `scripts/remote-update.sh` holds `data/update.lock`, so two *full* runs are already excluded — the concurrency this race needs comes from `run.py --verify`, which takes no lock and can run alongside a cron cycle. Both call `should_emit` for the same key and both read the pre-write state.
**Data prerequisite:** The JSON state file must reflect the prior emission before the second caller reads it.
**State prerequisite:** None beyond the file existing (it is created on first write).
**Mitigation:** The failure mode is a duplicate warning, not a lost one — strictly better than the status quo of 48 duplicates/day, and the existing `google-token` / `sms_reader` consumers already accept it. This plan adds no new concurrency and deliberately does not add locking; a lock file here would be a new failure mode (stale lock wedging `/update`) traded for a cosmetic duplicate. Recorded rather than "fixed".

### Race 2: `data/update.txt` is rewritten between the fix session being queued and being read
**Location:** `bridge/update.py::_queue_fix_session` (new path pointer) and the cron log writer.
**Trigger:** A low-priority fix session sits in the queue past the next 30-minute cron tick, which overwrites `data/update.txt`; the session then reads a *different* run's log.
**Data prerequisite:** The warning text the session was told to fix must still be recoverable.
**State prerequisite:** None.
**Mitigation:** This is precisely why the warning list is embedded in the message in full rather than only referenced. The log pointer is supplementary context, and the message states it may have been superseded, so a stale read is recognisable rather than misleading.

### Race 3: The `suppressed:` trailer is composed before the checks that populate it
**Location:** `scripts/update/run.py` — the `suppressed:` line's composition point versus the `should_emit` call sites at `run.py:1029` and `run.py:1480-1497`.
**Trigger:** If the trailer is built inline at any point before every human-gated check has run, it enumerates a partially-populated state file and under-reports — naming one suppressed key while a second is suppressed in the same run.
**Data prerequisite:** Every `should_emit` call for the run must have completed and written its state before `active()` is read.
**State prerequisite:** None beyond the state file existing.
**Mitigation:** Compose the trailer at the summary-rendering site (`run.py:2482-2494`), which by construction runs after all steps, never inline next to a check. A test asserts that a run suppressing two keys names both in the trailer — an under-reporting trailer is exactly the silent-invisibility failure Risk 4 exists to prevent, so this ordering is load-bearing rather than cosmetic.

## No-Gos (Out of Scope)

- [EXTERNAL] **Authenticating `gws` on Valor the Bald.** Requires a human at a browser completing OAuth consent; CLAUDE.md forbids automating it. This plan stops the respam only.
- [EXTERNAL] **Provisioning `REDIS_APP_PASSWORD` into the vault `.env`.** A credential a person must mint and record, on the specific machine, per the #2645 runbook.
- [ORDERED] **Applying the Redis ACL on any machine.** Double-gated behind `data/redis-acl-enabled` + `REDIS_ACL_APPLY=true`, human-signed per #2645 Risk 8, and fleet-ordered ahead of the #2661 `REDIS_URL` rotation — rotating before every machine has the ACL takes the worker, bridge, and dashboard down fleet-wide. `/update` must continue to call `apply_redis_acl()` with no arguments.
- [EXTERNAL] **Adding the flagged keys to the vault `.env`.** This is the destructive remediation `docs/features/env-completeness-validation.md:61` currently recommends, and it is the wrong fix in two independent ways: many of the flagged declarations carry an empty value (11 of 27 on the issue's machine, 19 of 64 on this one), so copying them in breaks `Settings()` construction fleet-wide (reproduced in the Problem section); and two of them (`DISK_RECLAIM_APPLY`, `MEMORY_DECAY_PRUNE_APPLY`) are irreversible-destructive apply-gates where a set value is *dangerous*. Stated as a No-Go rather than left implicit, because the plan's own Problem section describes the hazard and a builder reading only the task list could still reach for it.
- [EXTERNAL] **Granting Full Disk Access to clear a suppressed FDA warning (#2328).** A human at the machine's System Settings. This plan only makes the suppressed state visible; it does not resolve any suppressed condition.
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
- [ ] Create `docs/features/update-warning-channel.md` — the warning grammar `run.py` emits, the detection contract `extract_update_warnings` implements against it, the fix-session payload shape, and the suppression/retrieval model (`warn_state`, `active()`, the `suppressed:` line). It must carry the push/pull surface table from Technical Approach → Defect 4 verbatim in substance — which path pushes, which is pull-only, and why — and pin `SUPPRESSED_PREFIX`'s exact bytes as the shared producer/consumer contract. None of this is documented anywhere today, and it is now a contract between two modules and two test files rather than an implementation detail.
- [ ] Add the new feature doc to the `docs/features/README.md` index table.
- [ ] Update `.claude/skills/update/SKILL.md` — it is the operator-facing description of what `/update` reports, and this plan changes those semantics: warnings are detected from the cron summary, a `suppressed:` line appears, and the fix-session brief leads with the full warning list.
- [ ] Update `docs/features/env-completeness-validation.md` — document the `@optional` marker, the required-by-default rule, the first-comment-line description rule (currently documented as last), the inline-key cap, and **replace the remediation section at line 61**, whose current instruction to copy declarations into the vault `.env` is the destructive path proven in Finding A.
- [ ] Update `docs/features/headless-session-runner.md:165` — remove the `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES` cap description; #2494 deleted the trim and `agent/session_runner/adapter.py:370` documents the replacement (TTL-bounded, not count-bounded).
- [ ] Update `docs/features/config-architecture.md:46` — remove the `OLLAMA_VISION_MODEL` row; the live knobs are `MODELS__OLLAMA_HOST` / `MODELS__OLLAMA_GENERATION_MODEL`.
- [ ] Update `docs/features/redis-flush-hardening.md` — note that the `/update` drift warning now emits once per state transition, and that `python -m tools.doctor` is the unconditional on-demand check.
- [ ] Verify `docs/features/README.md`'s env-completeness row still describes the check accurately after the semantics change.

### Inline Documentation
- [ ] Header comment block in `.env.example` documenting the `@optional` convention, with the required-by-default rule stated explicitly.
- [ ] CLAUDE.md § Secrets — add the required/optional distinction to the existing "how to add a secret" instructions, since that section is what tells a future author to add a `.env.example` placeholder.
- [ ] Docstrings on `extract_update_warnings` naming each `run.py` output format it parses, with a pointer to `scripts/update/run.py:2482-2494` as the authority.
- [ ] `scripts/update/warn_state.py` module docstring — it currently describes suppression as write-only and names only the two original keys. After this plan it has a reader (`active()`), a CLI, a shared `SUPPRESSED_PREFIX` constant, and four keys.
- [ ] `scripts/remote-update.sh:165` — the comment "Output goes directly to Telegram" is false: `bridge/update.py` composes its own status and never quotes this stdout, and the launchd path has no chat at all. Replace it with what actually happens (stdout → the bridge's captured `status_lines` on the interactive path, `logs/update.log` on the cron path), since that belief is what made the round-5 blocker easy to miss.

## Success Criteria

- [ ] A fix session spawned by `/update` receives the complete warning list — verified by a test that composes a 27-warning payload and asserts the last warning's text is present in `message_text`.
- [ ] `has_warnings` detects the `(N warnings)` cron summary and the `update failed at` summary, and returns false for a realistic green transcript containing "error"/"warning" substrings in git and npm output.
- [ ] `env-completeness` reports **zero missing declarations that carry `# @optional`**, and still reports a missing *unmarked* key when one is introduced — both directions tested against a fixture `.env` / `.env.example` pair, not against the machine's own vault. Stated this way because the raw missing count is a property of the local vault rather than of the change: the checkout running this lane is missing 64 declarations, several of them genuine credentials that must keep warning, so "0 missing on this machine" is neither achievable nor desirable here.
- [ ] No key declared in `.env.example` lacks a reader — enforced by a test, with the three dead declarations deleted.
- [ ] `gws auth` and Redis ACL drift emit once per state transition, and re-emit when the drift content changes or resolves.
- [ ] A well-formed `(0 warnings)` summary line queues **no** fix session.
- [ ] Every suppressed key is retrievable: `python -m scripts.update.warn_state` prints all of them with their signatures, and a run with a non-empty `active()` emits the `suppressed:` line in both the verbose log and the `--cron` summary — while that line itself extracts as zero warnings.
- [ ] On the interactive path the suppression state arrives without being asked for: a clean run (empty `result.warnings`, non-empty `active()`) puts the trailer into the string passed to `tg_client.send_message` — pinned by `test_suppressed_trailer_reaches_telegram_on_clean_run` in `tests/unit/test_bridge_update.py`. A second test, `test_suppressed_prefix_constant_is_shared`, asserts `bridge/update.py` matches on the `SUPPRESSED_PREFIX` imported from `scripts.update.warn_state` rather than on a locally-spelled literal.
- [ ] The fix-session brief is *legible*, not merely complete. All three properties are asserted directly by `test_fix_session_brief_is_legible` in `tests/unit/test_update_warning_extraction.py`, against the exact string handed to `enqueue_agent_session`: (a) **ordering** — `message_text.index(last_warning) < message_text.index("[... ")` on a payload long enough to force truncation; (b) **marked, line-boundary cut** — the literal substring `characters elided` is present when the tail exceeds the raised cap, and every line of the emitted tail is a whole line of the input; (c) **log pointer** — the path captured from a `<<FILE:/path>>` input line appears in `message_text`. This closes the Problem statement's actual loop — that a human or agent reliably *learns* what `/update` found — and it is now falsifiable property by property rather than by containment alone.
- [ ] The red-state output for `test_cron_summary_warnings_trigger_fix_session` against unmodified `bridge/update.py` is captured in the PR body.
- [ ] `python scripts/update/run.py --verify` on the build machine names **no `@optional` declaration** in its env-completeness warning (they are classified, not suppressed — the check may still name genuinely-missing required keys, and on a machine with an incomplete vault it should), and emits the Redis ACL drift warning **at most once** across consecutive runs — the first run has no stored signature, so `should_emit` returns True and it warns by design; the second identical run is silent. Stating this as "0 warnings" would contradict the plan's own `warn_state` semantics. The drift stays visible on demand via `python -m tools.doctor` and in the `suppressed:` line thereafter.
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

- **Code reviewer**
  - Name: `update-channel-reviewer`
  - Role: cross-cutting review before documentation, since the three builders' files interlock through the warning grammar. Specifically checks: (1) every key any `should_emit` call site can write appears in `active()`'s output and therefore in the `suppressed:` line — no unreachable suppressed state; (2) the detector cannot over-fire on the `suppressed:` trailer or on a `(0 warnings)` summary; (3) `redis_acl.apply_redis_acl()` is still called with no arguments (#2645 Risk 8 regression); (4) **every `# @optional` marker added to `.env.example` clears both legs of the Defect 3 criterion, and no credential — token, key, password, DSN, connection URL, account id — carries one.** This is the check that catches a builder who derived the set from `check_env_completeness` on their own machine, which on this checkout's machine would mark `OP_SERVICE_ACCOUNT_TOKEN`, `SDLC_AGENT_GH_TOKEN`, `LINEAR_API_KEY`, `SENTRY_DSN`, `STRIPE_API_KEY`, and `REDIS_URL` optional; (5) the `suppressed:` trailer is matched on both sides through the single `SUPPRESSED_PREFIX` constant exported by `scripts/update/warn_state.py`, never a locally-spelled literal, and both its composition in `run.py` and its forwarding in `bridge/update.py` sit **outside** their respective conditional branches — the two places a passing test on the both-present case would hide a hole on the clean-except-suppressed path; (6) no `Co-Authored-By` trailers and no commented-out legacy code (Development Principle 1).
  - Agent Type: code-reviewer
  - Resume: true

- **Validator**
  - Name: `update-channel-validator`
  - Role: verifies every Success Criterion and every Verification row
  - Agent Type: validator
  - Resume: true

The three builders touch disjoint files — `bridge/update.py`; `scripts/update/verify.py` + `.env.example`; `scripts/update/run.py` — and can run in parallel in the single session worktree without interleaving commits. **This is now true as written:** an earlier revision put a `run.py` edit in Task 1 while Task 4 edited the same lines, which would have had two parallel builders writing one file. All `run.py` work, including every `_append_warning` conversion, belongs to `warn-state-builder`; `update-channel-builder` never opens it. Task 5 is the one cross-builder seam and it is handled by sequencing, not by sharing: `update-channel-builder` reopens `bridge/update.py` only after Task 4 has landed the `SUPPRESSED_PREFIX` constant, so the file still has exactly one writer and never two at once.

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
- Rewrite `_queue_fix_session` to take the warning list as a parameter, lead with it uncapped, and append a line-boundary-truncated stdout/stderr tail carrying an explicit `[... N characters elided ...]` marker, plus the log path parsed from the `<<FILE:...>>` marker.
- **Write `test_cron_summary_warnings_trigger_fix_session` FIRST and capture its failure against unmodified `bridge/update.py`.** This is the plan's central defect and the red-state output goes in the PR body — a green-only test here proves nothing about whether detection was actually broken.
- Add `test_fix_session_brief_is_legible`, a single composition test over the exact string handed to `enqueue_agent_session` (reach it via `bridge_update._queue_fix_session.await_args`, the idiom the existing tests already use). It asserts the three properties Success Criteria calls out and containment cannot: `message_text.index(last_warning) < message_text.index("[... ")` on a payload long enough to force truncation; the literal `characters elided` present on that cut, with every line of the emitted tail a whole line of the input; and the path from a `<<FILE:/path>>` input line present in `message_text`.
- Tests: green transcript with adversarial "error"/"warning" filenames yields `[]`; a well-formed `(0 warnings)` summary yields `[]`; the `suppressed:` trailer yields `[]`; 27-warning payload round-trips with the last warning present; a warning with an embedded newline survives complete; empty input; failure-block bullets matched only inside the block.

### 2. env-completeness required/optional split
- **Task ID**: build-env-completeness
- **Depends On**: none
- **Validates**: `tests/unit/test_env_completeness.py` (update)
- **Assigned To**: `env-completeness-builder`
- **Agent Type**: builder
- **Parallel**: true
- Teach `_parse_env_example` the `@optional` marker; exclude the marker line from description candidates; switch the description to the first non-empty comment line. Widening the return to a 3-tuple breaks two positional unpack sites inside `check_env_completeness` that must change with it — `verify.py:1118` (`declared_keys = {k for k, _ in declared}`) and `:1128` (`desc_map = dict(declared)`, which silently produces a `{key: (desc, optional)}` mapping instead of erroring). Verified live: feeding a 3-tuple to today's `check_env_completeness` raises `ValueError: too many values to unpack (expected 2, got 3)` at `:1118`, so the first site fails loudly and the second does not. `:1128` is the one to watch.
- Filter optional keys out of the missing set in `check_env_completeness`, but surface the residual as a count in `version`: `all N required vars present (M optional unset)`. Cap the inline key list at 5 with a `(+N more)` suffix, and cap each individual description with an ellipsis.
- Annotate the verified-optional declarations in `.env.example` and add the convention to its header block. **Derive the set from the two-leg criterion in Technical Approach → Defect 3 part 3 (traced read site with an in-code default, AND not a credential), never from `check_env_completeness`'s output on the build machine.** That output is a per-machine measurement — 27 keys where this plan was written, 64 on this checkout's machine, and the wider set includes `OP_SERVICE_ACCOUNT_TOKEN`, `SDLC_AGENT_GH_TOKEN`, `LINEAR_API_KEY`, `SENTRY_DSN`, `STRIPE_API_KEY`, and `REDIS_URL`. Marking any of those is Risk 1 firing on the first commit. Record each marker's justifying `file:line` and default in the PR body.
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
- Add `warn_state.active(project_dir) -> dict[str, str]` (fail-soft `{}`) and a `_main()` mirroring `redis_acl.py::_main`, so `python -m scripts.update.warn_state` works.
- Emit the always-on `suppressed:` line whenever `active()` is non-empty, composed at the summary-rendering site `run.py:2482-2494` — never inline beside a check, or it under-reports (Race 3), and **outside** the `if/elif/else`, not nested in a branch. It must carry no `⚠️` and none of the four legacy prefixes.
- The trailer enumerates **whatever `active()` holds**, including keys this plan never touches: a live machine's state file already carries `calendar-config` and `google-token`. Assert *membership* in the trailer test, never exact-set equality — an equality assertion passes on a clean fixture and fails on every real machine.
- Add `_append_warning(result, text)` and convert **all 82** `result.warnings.append(X)` call sites to it. `result` is in scope at every one, so this is a mechanical rename, not a redesign. All of `run.py` belongs to this task — `update-channel-builder` never opens the file.
- Add the guard test: at the point `run.py:2482-2494` renders, no entry in `result.warnings` contains a newline. This is the recurrence guard for the whole class; without it an unconverted site fails silently instead of loudly.
- ACL signature: a digest of the planned commands (which already carry the `<REDIS_APP_PASSWORD>` placeholder, never a real secret). gws signature: the auth-method string.
- Keep the `apply_redis_acl()` call literally argument-free; keep both sites inside their existing `except Exception` guards.
- Tests: first run emits, second identical run stays silent, changed drift re-emits, resolution emits one note and clears state, a raising `should_emit` leaves the run successful, `active()` returns `{}` on a corrupt state file, and a run suppressing two keys names **both** in the trailer.

### 5. Suppression trailer reaches the Telegram reply
- **Task ID**: route-suppressed-trailer
- **Depends On**: build-update-channel, build-warn-state
- **Validates**: `tests/unit/test_bridge_update.py` (extend)
- **Assigned To**: `update-channel-builder`
- **Agent Type**: builder
- **Parallel**: false
- Sequenced after both because it consumes `SUPPRESSED_PREFIX` from Task 4's `warn_state` and edits the `bridge/update.py` Task 1 owns. Same builder as Task 1, so `bridge/update.py` still has exactly one writer and no two builders hold it at once.
- In `handle_update_command`, select the trailer out of the already-computed `status_lines` (`bridge/update.py:211-215`) and append it to `status` **outside** the `if failed or has_warnings:` branch at `:293`, before the `send_message` at `:297`. Match on `SUPPRESSED_PREFIX` imported from `scripts.update.warn_state` — never a literal spelled locally, which is the producer/consumer drift that caused Defect 2.
- Add `test_suppressed_trailer_reaches_telegram_on_clean_run`: empty `result.warnings`, non-empty `active()`, assert the trailer is in the string passed to `tg_client.send_message`. Add `test_suppressed_prefix_constant_is_shared`: the bridge-side match resolves through the imported constant.
- Do **not** add any push on the unattended cron path. It has no chat context (`scripts/remote-update.sh:429` needs `UPDATE_REPORT_CHAT_ID`, set only at `bridge/update.py:192`), and the surface table in Technical Approach → Defect 4 states why `logs/update.log` plus the CLI is the deliberate answer there.

### 6. Cross-cutting review
- **Task ID**: review-channel
- **Depends On**: build-update-channel, build-env-completeness, build-dead-key-guard, build-warn-state, route-suppressed-trailer
- **Validates**: the six checks named in the `update-channel-reviewer` role
- **Assigned To**: `update-channel-reviewer`
- **Agent Type**: code-reviewer
- **Parallel**: false
- The three builders touch disjoint files but interlock through the warning grammar: builder 3 composes a line into the same stdout builder 1 parses and then forwards to Telegram in Task 5. Nobody else in this team reads both sides of that contract.
- Run the six role checks; any failure routes back to the owning builder before documentation starts.

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: review-channel
- **Validates**: `grep -c 'edit `~/Desktop/Valor/.env` directly' docs/features/env-completeness-validation.md` == 0, plus the new feature doc existing and being indexed
- **Assigned To**: `update-channel-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Every item in the Documentation section, including creating `docs/features/update-warning-channel.md`, indexing it, updating `.claude/skills/update/SKILL.md`, and replacing the destructive remediation text in `docs/features/env-completeness-validation.md`.
- CLAUDE.md § Secrets: the required/optional distinction.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Validates**: the full Verification table below, plus every Success Criterion
- **Assigned To**: `update-channel-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row and every Success Criterion.
- Run `python scripts/update/run.py --verify` and confirm no `@optional` declaration is named in the env-completeness warning and the ACL warning is emitted at most once. Do **not** expect the warning to vanish outright on a machine whose vault is genuinely missing required keys — this checkout's machine is one of them.
- Confirm `python -m tools.doctor` still reports the Redis ACL drift unconditionally.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/test_env_completeness.py tests/unit/test_env_declaration_readers.py tests/unit/test_update_warning_extraction.py tests/unit/test_update_warn_state.py tests/unit/test_redis_acl.py tests/unit/test_ollama_consolidation.py tests/unit/test_review_judge_env_docs.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No `@optional` declaration is ever reported missing | `.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from pathlib import Path; from scripts.update.verify import check_env_completeness, _parse_env_example, _parse_env_keys; opt={k for k,_,o in _parse_env_example(Path('.env.example')) if o}; miss=opt - _parse_env_keys(Path('.env')); r=check_env_completeness(Path('.')); leak=sorted(k for k in miss if k in (r.error or '')); print('CLEAN' if not leak else 'LEAK: '+', '.join(leak))"` | output contains CLEAN |
| No credential carries an `@optional` marker (anti-criterion for Risk 1) | `.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from pathlib import Path; from scripts.update.verify import _parse_env_example; bad=[k for k,_,o in _parse_env_example(Path('.env.example')) if o and any(t in k for t in ('TOKEN','KEY','PASSWORD','SECRET','DSN','_URL','CREDENTIAL'))]; print('CLEAN' if not bad else 'CREDENTIAL MARKED OPTIONAL: '+', '.join(bad))"` | output contains CLEAN |
| Optional residual is surfaced, not silently dropped | `.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from pathlib import Path; from scripts.update.verify import check_env_completeness; r=check_env_completeness(Path('.')); print(r.version or r.error)"` | output names an optional-unset count |
| Dead declarations gone | `grep -c 'SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES\|^OLLAMA_URL=\|^OLLAMA_VISION_MODEL=' .env.example` | match count == 0 |
| ACL call site still argument-free (anti-criterion for the [ORDERED] No-Go) | `grep -n 'apply_redis_acl(' scripts/update/run.py` | output does not contain apply=True |
| No hand-written allowlist in the dead-key guard (anti-criterion for the Rabbit Hole) | `grep -ci 'ALLOWLIST\|KNOWN_UNREAD\|EXEMPT' tests/unit/test_env_declaration_readers.py` | match count == 0 |
| Optional marker is sigil-based, not prose-based (anti-criterion for Risk 1) | `grep -c "startswith(\"optional\")\|lower() == \"optional\"" scripts/update/verify.py` | match count == 0 |
| Warning extraction is line-anchored, not substring (anti-criterion for Risk 2) | `awk '/^def extract_update_warnings/,/^def /' bridge/update.py \| grep -c 'in line\b'` | match count == 0 |
| No 500-char slice survives anywhere (direct anti-criterion for Defect 1) | `grep -c 'stdout\[:500\]\|stderr\[:500\]' bridge/update.py` | match count == 0 |
| Suppressed warnings are retrievable | `.venv/bin/python -m scripts.update.warn_state` | exit code 0, prints the suppressed map |
| ACL call site still present, not merely un-armed | `grep -c 'apply_redis_acl()' scripts/update/run.py` | match count > 0 |
| #1898 regression + bridge update behaviour intact | `scripts/pytest-clean.sh tests/unit/test_bridge_update.py -q` | exit code 0 |
| Cron summary format contract intact | `scripts/pytest-clean.sh tests/unit/test_update_cron_summary.py -q` | exit code 0 |
| gws auth contract intact | `scripts/pytest-clean.sh tests/unit/test_gws_auth.py -q` | exit code 0 |
| Trailer prefix is one shared constant, not two literals (anti-criterion for the Defect 2 drift class) | `grep -c 'SUPPRESSED_PREFIX' bridge/update.py scripts/update/run.py scripts/update/warn_state.py` | match count > 0 in all three files |
| Telegram forwarding is not gated on a warning being present | `scripts/pytest-clean.sh tests/unit/test_bridge_update.py -q -k "suppressed_trailer or suppressed_prefix"` | exit code 0, 2 tests pass |
| Fix-session brief legibility is asserted, not assumed | `scripts/pytest-clean.sh tests/unit/test_update_warning_extraction.py -q -k legible` | exit code 0, 1 test passes |

**Builder note on `grep` in this table (from the round-5 critique's live dry-run).** The local `grep` is **`ugrep`**. It supports `\|` alternation and `\b`, so the patterns above run as written — but `grep -c` **exits 1 when the count is 0**. Every row whose Expected is "match count == 0" therefore *exits non-zero on success*, and a runner that gates on the exit code alone will read a passing row as a failure. Gate those rows on the printed count, not on `$?` (e.g. `[ "$(grep -c ... || true)" = 0 ]`). One further wrinkle: the `awk`-range row for `extract_update_warnings` yields an empty range until Task 1 lands, so it reads 0 vacuously before the function exists and only becomes meaningful afterwards.

**Two notes on the anti-criterion rows.** The Risk 2 row is scoped to `extract_update_warnings`'s body, not the whole file: a whole-file grep returns **2** on unmodified `bridge/update.py` (`:250` `any("Worker restarted" in line ...)` and `:490` `if any(k in line for k in [...])`), both pre-existing and load-bearing, so the row as originally written could only pass by deleting working code (critique BLOCKER 2). Second, these rows grep *source*, so a docstring or comment quoting the forbidden pattern trips the gate as surely as real code — describe the constraint in prose rather than quoting the token.

## Critique Results

**Critique round 6, 2026-08-19.** Run against plan hash `sha256:1546762b`, repo HEAD `f491306c5`.
War room depth: **FULL** — force-FULL, because the plan edits `.claude/skills/update/SKILL.md`, a
doctrine path. Roster gate: **3/3 complete, 3/3 grounded** (`critique-roster-check --plan-path`).
The three lenses were executed by the critique driver rather than by dispatched sub-agents (no
sub-agent dispatch surface was available in the executing session); each lens wrote its own
grounded result file and the membership gate verified all three on the filesystem as usual.
Findings: **8 total (1 blocker, 7 concerns, 0 nits)**. Every claim below was re-read live at
`f491306c5`.

| Severity | Critic(s) | Finding | Addressed By | Implementation Note |
|----------|-----------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The dead-key recurrence guard is unsatisfiable as specified. Six `.env.example` declarations have no reader in tracked code, not three — measured live across all 89. Beyond the three this plan deletes, `OP_SERVICE_ACCOUNT_TOKEN` (only `CLAUDE.md:130`), `HEADSCALE_SERVER_URL` and `HEADSCALE_PREAUTH_KEY` (only `.claude/skills/setup/SKILL.md` and `.claude/skills/setup/references/mesh-network.md`) have none either. Those three are not dead: they are read out of the ambient environment by external binaries — `op` reads `OP_SERVICE_ACCOUNT_TOKEN` itself, and `config/settings.py` declares no field for it. The guard has no leg for that class and the plan bans the only stated escape (`grep -ci 'ALLOWLIST\|KNOWN_UNREAD\|EXEMPT'` expected 0), so a faithful Task 3 lands red on `main` with three wrong exits: delete a live credential's declaration, add the banned allowlist, or widen the corpus to `.md` files. Widening also breaks Task 3's own red-state demo: restoring `OLLAMA_VISION_MODEL` would not go red, because `docs/features/config-architecture.md:46` still names it and Task 7 has not run yet. | pending | Give the guard three legs, not two: (1) literal occurrence in a tracked **non-markdown** file outside `.env.example`; (2) `<PREFIX>__<FIELD>` decomposition against a nested settings model registered with `env_prefix="<PREFIX>__"`; (3) a **passthrough** leg keyed on a machine-readable sigil in the declaration's own comment block, e.g. `# @passthrough <binary>`, parsed by the same `_parse_env_example` that learns `@optional`. Leg 3 is not the banned allowlist: it lives in the declaration, is reviewed in the same diff, and names who reads it — but pick its token so the anti-criterion regex `ALLOWLIST\|KNOWN_UNREAD\|EXEMPT` does not trip. Mark exactly `OP_SERVICE_ACCOUNT_TOKEN` (`op` CLI, `CLAUDE.md:130`), `HEADSCALE_SERVER_URL`, `HEADSCALE_PREAUTH_KEY` (headscale CLI, `.claude/skills/setup/references/mesh-network.md`). Excluding `*.md` from leg 1 also dissolves the ordering problem; keeping `.md` forces Task 3 to `Depends On: document-feature`, which contradicts Task 7's `Depends On: review-channel`. |
| CONCERN | Scope & Value | Every gate in the plan is armed against **over**-marking `@optional` and none against **under**-marking, so an implementation that annotates **zero** keys passes the whole Verification table and every Success Criterion while leaving the operator-facing noise exactly as it is. The CLEAN row iterates an empty optional set and prints CLEAN; the credential row does the same; the residual row prints `(0 optional unset)`, which "names an optional-unset count"; Success Criterion 11 is vacuously true. The plan is right to refuse a target number (the flagged set is per-machine) but left no machine-independent floor in its place, and its two live forces — Risk 1's fail-closed framing and the Rabbit Hole's "resist the urge to sweep" — both push toward marking fewer keys. | pending | Add a floor over a **named set**, never a count, so machine-dependence is not reintroduced. The six keys this plan has already traced to a read site with a quoted in-code default, none a credential: `CONTEXT_RECALL_HISTORY_DEPTH` (`bridge/context_recall.py:48`, `"10"`), `WORKER_SUPERVISOR_MAX_RESTARTS` (`worker/__main__.py:90`), `SDLC_REVIEW_CROSS_VENDOR` (`config/settings.py:974`), `HOOK_DETACH_DEADLINE_SECONDS` (`.claude/hooks/hook_utils/detach_lock.py`, `DEFAULT_DEADLINE_SECONDS = 120`), `DISK_RECLAIM_APPLY` (`tools/disk_reclaim.py:50`), `MEMORY_DECAY_PRUNE_APPLY`. Verification row shape: `opt={k for k,_,o in _parse_env_example(Path('.env.example')) if o}; floor={...}; print('CLEAN' if floor<=opt else 'UNMARKED: '+', '.join(sorted(floor-opt)))`. Add the matching Success Criterion bullet so the floor is a stated outcome, not just a gate. |
| CONCERN | Risk & Robustness, History & Consistency | The `suppressed:` trailer reaches one sink, not the three the plan names. `data/update.txt` is **never written** on the clean-except-suppressed run — the exact shape Risk 4 exists to cover — because `scripts/update/run.py:2514` gates the write on `if not result.success or result.warnings:` and a suppressed key means nothing was appended. Independently, the trailer as specified is appended to `status` at the summary-render site and never passes through `log()` (`run.py:176-190`), so it cannot enter `_log_buffer` and therefore cannot reach `data/update.txt` even on a run where that file **is** written. Success Criterion 7 demands "both the verbose log and the `--cron` summary"; Task 4 and Race 3 specify one composition point. Only `logs/update.log` (the launchd `StandardOutPath`) actually receives it. | pending | Two independent edits if you keep both sinks. (a) Emit twice at the composition site: `log(trailer, v, always=True)` for the buffer **and** `status += "\n" + trailer` for stdout. `log()` prepends `[update] ` (`run.py:180`), so define `SUPPRESSED_PREFIX` as the **bare** stdout spelling — `bridge/update.py` matches against stdout-derived `status_lines` — and record the `[update] ` offset in `docs/features/update-warning-channel.md` so a future grep of `data/update.txt` is written against the right bytes. (b) Relax the write gate to `if not result.success or result.warnings or warn_state.active(args.project_dir):`, or the file is absent exactly when the trailer matters. If you narrow the claim instead, change all four sites together: the surface table's "Unattended cron" cell, Risk 4's mitigation, Solution → Key Elements, and Success Criterion 7. Race 3's ordering is unaffected either way. |
| CONCERN | History & Consistency | `tools/doctor.py` has **no gws check** — `grep -ci gws tools/doctor.py` returns 0 and the registry at `tools/doctor.py:1820-1852` lists `_check_redis_acl` with no counterpart. The plan asserts three times that doctor is an unconditional on-demand surface for **both** suppressed conditions (Technical Approach → Defect 4 opening, Risk 3 mitigation, Risk 4 mitigation, and the surface table's cron row). After this plan an unauthenticated `gws` warns once and is thereafter answerable only by the trailer and the new CLI. The plan already contradicts itself here: Task 8 correctly scopes its check to the **Redis ACL drift** alone. This is round 5's defect class — an asserted retrieval surface that is empty when reached — in a plan whose Freshness Check opens "nothing here is inferred". | pending | Cheapest honest fix is to add the check under this plan: `scripts/update/gws_auth.py` already parses `gws auth status`'s `auth_method` field, so `_check_gws_auth` mirrors `_check_redis_acl` (`tools/doctor.py:977-1020`) — lazy import inside the function, WARN not FAIL when `auth_method == "none"`, PASS otherwise, skip result when the import raises so a machine without `gws` degrades rather than errors; register beside `_check_redis_acl` at `tools/doctor.py:1836`. If you correct the prose instead, all four sites change together and Success Criteria gains the gws-specific retrieval claim so the narrowing is recorded rather than dropped. |
| CONCERN | Scope & Value | The Verification row "No credential carries an `@optional` marker" fails on a **correct** implementation. Its filter is a bare substring test over `('TOKEN','KEY','PASSWORD','SECRET','DSN','_URL','CREDENTIAL')`, which matches **25 of the 89** declarations live — including `SDLC_REVIEW_CROSS_VENDOR_MAX_DIFF_TOKENS` (`.env.example:85`, comment "Provisional token cap — tune based on cost tolerance"), a plain integer whose sibling `SDLC_REVIEW_CROSS_VENDOR` this plan cites as the paradigm optional toggle. It also matches `GOOGLE_CREDENTIALS_DIR` (a directory path, `:596`) and `VALOR_PROJECT_KEY` (`:39`, literal value `valor`). Marking the first is exactly what the Defect 3 criterion prescribes, so Task 8 reads a red gate on correct work and the cheapest exits are the two worst: leave a legitimately-optional key unmarked, or edit the gate under time pressure. | pending | Suffix-anchoring does not rescue it — the key ends `_TOKENS`, so `(?:^\|_)(TOKEN\|KEY\|PASSWORD\|SECRET\|DSN\|URL\|CREDENTIAL)S?$` still matches. Either (a) keep the substring screen and subtract a named, comment-justified exception tuple in the row itself — `NOT_A_CREDENTIAL = ('SDLC_REVIEW_CROSS_VENDOR_MAX_DIFF_TOKENS', 'GOOGLE_CREDENTIALS_DIR', 'VALOR_PROJECT_KEY')`, small enough to review and each entry falsifiable by reading the declaration — or (b) reframe the row to print the intersection for `update-channel-reviewer`'s check 4 to sign off, dropping the CLEAN/FAIL wording. Either way state in Task 8 that a non-empty intersection is a reviewer decision, never a licence to remove the marker. |
| CONCERN | History & Consistency | Test Impact scopes `tests/unit/test_bridge_update.py` to one test with disposition "UPDATE (verify-only)". That file holds 26 tests, and `tests/unit/test_bridge_update.py:116` is positionally coupled to the exact signature Task 1 changes: it asserts `bridge_update._queue_fix_session.await_args.args[-1] is True`, where `args[-1]` is today's `failed: bool` (`bridge/update.py:112`). If the builder appends the warning list — the natural reading of "take the warning list as a parameter" — `args[-1]` becomes a list and `test_stale_bridge_reports_failed_not_ok` fails, surfacing as an unrelated red test rather than a known consequence. | pending | Pin the parameter order in Task 1: `_queue_fix_session(event, machine, stdout, stderr, warnings, failed)`, keeping `failed` last, and say so explicitly. Better, remove the coupling permanently — pass `failed=` at the call site (`bridge/update.py:296`) and change `tests/unit/test_bridge_update.py:116` to `await_args.kwargs.get("failed", await_args.args[-1]) is True`. Re-disposition the Test Impact row from "UPDATE (verify-only)" to "UPDATE", naming both `test_warning_on_non_first_stdout_line_detected` (the #1898 guard, genuinely verify-only) and `test_stale_bridge_reports_failed_not_ok` (a real edit). |
| CONCERN | Risk & Robustness | The embedded-newline hole is closed for `result.warnings` at all 82 sites and guarded at render time, but its twin on the failure path is untouched. `run.py:2482-2485` renders `result.errors` as `status += f"\n  - {err}"`, and both `result.errors.append` sites interpolate newline-capable strings: `run.py:758` (`f"Git pull failed: {result.git_result.error}"`) and `run.py:1300` (`f"Migration failed: {err}"`, an exception `str()` — the very shape the Problem section quotes as a multi-line pydantic `ValidationError`). Because failure bullets match only inside a state-tracked failure block, a multi-line error's continuation lines are dropped exactly as an unconverted warning's would be, and the plan's guard test — scoped to `result.warnings` — cannot see it. | pending | Add `_append_error(result, text)` beside `_append_warning` with the same `" ".join(text.splitlines())` collapse and convert the two sites (`run.py:758`, `run.py:1300`) — a two-line change, not a sweep. Widen Task 4's render-time guard test to assert no entry in **either** `result.warnings` or `result.errors` contains `"\n"` at the point `run.py:2482-2494` renders. Do not defer this as the rarer path: it is the path where the dropped tail is a stack trace. |
| CONCERN | Scope & Value | The `(N warnings)` cross-check is promoted to a mandatory failure-path test asserting "the discrepancy is surfaced rather than swallowed", but the plan never says **where** it is surfaced. `extract_update_warnings` is specified as `(status_lines: list[str]) -> list[str]`, which has no channel for a parse diagnostic. The builder must invent one, and the three obvious choices behave differently: a synthetic list entry makes `has_warnings` True and spawns a fix session on every mismatch; a `logger.warning` reaches only `logs/bridge.log`, which nobody reads on the cron path; a second return value breaks the pure-helper signature two Verification rows and the docstring requirement lean on. The mandated test will pin whichever is chosen, making it a contract by accident. | pending | Name the channel: prefer the synthetic-entry form. On a mismatch append one extra string to the returned list, e.g. `f"[update] WARN: summary declared {n} warning(s) but {len(bullets)} were parsed"`. It preserves the pure `list[str]` signature, makes the mismatch loud on the one path with a reader (the fix-session brief), and deliberately reuses the `[update] WARN` legacy prefix so the entry is self-consistent with the four prefixes the parser already honours. Assert it by content in Task 1's test list, not by list length — a length-only assertion passes if the builder appends an empty string. |

**Structural check results (driver, run live at `f491306c5`):**

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation (8 checkboxes, 5 `docs/features/` paths), Update System, Agent Integration, Test Impact all present and substantive with dispositions. |
| Task numbering | PASS | Tasks 1-8 contiguous; every task carries `Task ID`, `Validates`, `Assigned To`, `Agent Type`, and `Parallel`. |
| Dependencies valid | PASS | Every `Depends On` resolves to a real Task ID; no cycles. One ordering hazard is recorded as part of the blocker (Task 3's red-state demo versus Task 7's doc deletions), not as a broken reference. |
| File paths exist | PASS | Every cited source, test, and doc path exists except the three this plan creates (`tests/unit/test_env_declaration_readers.py`, `tests/unit/test_update_warning_extraction.py`, `docs/features/update-warning-channel.md`). |
| Prerequisites met | PASS | Vault `.env` readable; `redis-cli` at `/opt/homebrew/bin/redis-cli`; `.venv` on the `3.14` pin. |
| Cross-references | PASS | No No-Go contradicts the Solution. Every Success Criterion maps to a task. Three criteria are unsatisfiable or vacuous as written — bullets 4, 7, and the `@optional` pair — each raised as a finding above. |
| Cited claims re-verified | PASS | All confirmed live: 82 `result.warnings.append` / 48 f-string sites, 89 declared keys, 7 prose "optional" comment lines, `find_plan_path(2845)` resolving here, and every cited line number in `bridge/update.py` (`:112`, `:120-121`, `:211-214`, `:291-293`, `:297`), `scripts/update/run.py` (`:1029`, `:1480-1497`, `:2275`, `:2354`, `:2365`, `:2482-2494`, `:2519`), `scripts/update/verify.py` (`:1049`, `:1118`, `:1128`, `:1136-1141`), and `scripts/update/warn_state.py` (79 lines, exports `_state_path`, `_load`, `_save`, `should_emit` only). Two claims did **not** survive and are findings: `tools/doctor.py` has no gws check, and three further declarations lack a reader. |
| Verification-row dry-run | PASS | Rows re-executed against unmodified code. The round-5 `ugrep` note still holds. Two rows are defective on their own terms and are raised as findings: the credential anti-criterion (false-positives on three non-credentials) and the CLEAN row (vacuously green on a zero-annotation implementation). |

**Convergence:** 8 findings / 3 blockers → 7 / 0 → 3 / 1 → 0 / 0 → 3 / 1 → **8 / 1**. The count rose
because this round probed surfaces earlier rounds asserted rather than measured: the reader status of
all 89 `.env.example` declarations, the contents of `tools/doctor.py`, the `data/update.txt` write
gate, and the 26 tests in `test_bridge_update.py`. Six of the eight findings are consequences of the
suppression and required/optional work being specified but never executed end to end against the real
files. None is a regression introduced by the round-5 revision, which stands.

---

## Open Questions

1. **Scope confirmation.** The routed task was "diagnose and fix the 2 warnings this machine reported." Issue #2845 is broader — it also covers the fix-session truncation and the `has_warnings` blindness, which are the reasons nobody saw these warnings properly in the first place. This plan implements the whole issue on the grounds that half-implementing it leaves it open and its acceptance criteria unmet. Confirm that is the intended scope, or say the word and I will split Defects 1–2 into a separate issue and land only 3–4.
2. **`@optional` sigil.** I chose `@optional` over a bare `# optional` because `.env.example` contains seven prose uses of the word, one of them line-initial directly above the `SDLC_AGENT_GH_TOKEN` credential (`# Optional. GitHub PAT for ...`, `:135`), so a prose match would silently mark a real secret optional. If there is an existing annotation convention in this repo I should be reusing instead, name it.
3. **Anything genuinely required among the keys the criterion clears?** Each one gets traced to a read site with an in-code default and screened against the not-a-credential leg. If you know of a machine where one of these is load-bearing and unset would be wrong, say which — that is the one thing the code cannot tell me. (This question got sharper on re-verification: the flagged set turns out to vary by machine, so the criterion, not the check's output, is what decides. If you disagree with either leg of it, now is the moment.)
