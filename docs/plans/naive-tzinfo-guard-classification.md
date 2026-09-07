---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3181
last_comment_id: 5564069727
---

# Classify the remaining naive-tzinfo guards by the #3173 rule

## Problem

**Thirty-two** naive-tzinfo coercion sites are scattered across seventeen files outside `models/job.py` (sixteen from the main sweep, plus `reflections/crash_recovery.py` from the `getattr` shape). Every one of them was written against popoto 1.8.0, which round-tripped `DatetimeField` values as naive datetimes. popoto 1.9.0 decodes stored datetimes as aware UTC, so some of those guards now defend against a state that cannot occur, and nine carry comments that assert the old behaviour as fact.

**The measured pre-state**, all numbers taken with `/usr/bin/grep` (never the interactive shell's `grep`, which is `ugrep` here and honours `.gitignore`, so it can legitimately return a smaller set):

| Measurement | Command | Count |
|---|---|---|
| Issue's own sweep, 7 declared directories | the sweep command below over `agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/` | **26** across 14 files |
| Same sweep widened to `utils/ ui/` | same command, 9 directories | **31** across 16 files |
| The `getattr` shape the sweep regex misses | `getattr\([a-z_.]+, "tzinfo", None\) is None` | **1** (`reflections/crash_recovery.py:186`) |
| **Total work set** | | **32** |
| Stale 1.8.0 prose, one phrasing | `strips tzinfo` | **7** |
| Stale 1.8.0 prose, all three phrasings | `strips tzinfo\|SortedField stores them\|round-trips values as NAIVE` | **9** across 9 files |

The authoritative sweep is the issue's, run verbatim:

```bash
/usr/bin/grep -rn --include='*.py' -E "tzinfo is None|not [a-z_.]+\.tzinfo\b|\.tzinfo else [a-z_.]+\.replace\(tzinfo=UTC\)" \
  agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ | /usr/bin/grep -v /tests/
```

This plan works from the widened 9-directory form plus the `getattr` shape, because `utils/utc.py` is the coercer the rest of the repo defers to and `ui/data/sdlc.py` is named in the doc this plan rewrites. Every count in this document is a piped `wc -l`, never a `head` window read as a complete set.

**Current behavior:**
Nothing is broken. The cost is that every lane touching one of these files has to re-derive, from scratch, whether the guard in front of it is load-bearing. Worse, nine of the sites carry prose that is now actively wrong ("Popoto strips tzinfo on load", "matching how Popoto SortedField stores them", "round-trips values as NAIVE"), so a reader who trusts the comment reaches the wrong conclusion. One site (`reflections/audits/redis_quality_audit.py:61`) guards a field that has never been a datetime at all.

**Desired outcome:**
Every one of the 32 sites carries a settled verdict. Popoto-only guards are gone, with a test that would have caught the deletion if it were wrong. Mixed-input guards stay, each with a one-line reason that a future reader can trust. A re-run of both sweep shapes returns exactly the surviving keeps and no delete-site.

**This plan also discharges the audit asked for in #3207** (closed as a duplicate into #3199): the `updated_at` / `created_at` / `completed_at` / `scheduled_at` consumers that do naive comparisons. See the **#3207 Consumer Audit** section.

## Freshness Check

**Baseline commit:** `de229ee46` (recon), re-anchored at revision time to `4b5a13184`
**Grep binary pinned for every measurement in this plan:** `/usr/bin/grep`. The interactive shell's `grep -r` here is `ugrep` and honours `.gitignore`, so two sweeps can legitimately disagree on counts. Any Verification row re-run with a different binary is not a re-run of this row.
**Issue filed at:** 2026-09-05 (follow-up to #3173 / PR #3180, merged `2aecc7c93`)
**Disposition:** Minor drift

**File:line references re-verified:** all 32 sites were re-derived from live runs of the issue's own grep (plus the `getattr` shape) at `4b5a13184` with `/usr/bin/grep`. Every path the issue names still exists; the per-site line numbers in this plan are the measured ones, not the issue's.

**Cited sibling issues/PRs re-checked:**
- #3173 / PR #3180 — closed, merged at `2aecc7c93`. It is the source of the classification rule, and it left `agent/session_health.py` and `agent/session_pickup.py` classified as keeps.
- #3199 — **open**, and it edits `models/agent_session.py` in the `repair_indexes` region (~:2476-2528). This plan edits `:1089-1093`, `:2225` and `:2569` of the same file. Disjoint regions, but the lanes will both be writing `models/agent_session.py`; whichever lands second rebases.
- #3207 — closed as a **duplicate into #3199**, not "split out of" it. Its first suggested next step — audit the `updated_at` / `created_at` / `completed_at` / `scheduled_at` consumers for naive comparison sites — was never discharged by #3199, which is scoped to index repair and quarantine counters. This plan discharges it; see **#3207 Consumer Audit**.

**Commits on main since issue was filed (touching referenced files):** none. `git log 2aecc7c93..de229ee46` is 131 commits and not one of them touches any file in the sweep set. The code the issue describes is byte-identical to the code this plan changes.

**Active plans in `docs/plans/` overlapping this area:** `agentsession-quarantine-counter-divergence.md` (the #3199 lane) touches `models/agent_session.py`. Overlap is file-level, not region-level.

**Notes:** The drift is in the issue's own measurements, not in the code. See Prior Art for the two corrections.

## Prior Art

- **PR #3180 / issue #3173** — "Remove Job UTC-reattach and 1.8.0-era naive-tzinfo guards". Succeeded. It established the rule this plan applies and demonstrated the failure mode to avoid: its review caught a test that asserted the deletion without ever reaching the deleted line. That is the single most important lesson carried forward here.
- **PR #787 / issue #777** — "Fix watchdog UTC duration: `_to_timestamp` treats naive datetimes as local time". Succeeded. It is *why* `monitoring/session_watchdog.py:65` exists: without the guard, a non-UTC host inflated every duration by its offset and fired false `LIFECYCLE_STALL` events. That guard stays; only its stale rationale changes.
- **Issue #1653 / #1645** — "AgentSession.updated_at stamped 7h in the future: popoto `auto_now` uses naive local `datetime.now()`". Succeeded upstream (popoto #421). It is the origin of `_heal_future_updated_at`, one of this plan's deletion sites — the healer stays, only its naive-input guard goes.
- **Issue #3199** — open, adjacent. Its recon independently confirmed that popoto 1.9.0 changed decode semantics on this exact code path, which corroborates the premise here.

No prior attempt to classify *this* set exists. #3173 deliberately scoped away from it.

## Research

No external research was needed, and none would have been authoritative. The one load-bearing external premise — "popoto 1.9.0 decodes every stored datetime as aware UTC" — was verified directly against the installed package rather than against documentation, because the installed version is what the code runs on.

**What was read:** `.venv/lib/python3.14/site-packages/popoto/models/encoding.py` (`_decode_datetime`, `_LEGACY_DATETIME_RE`, `_LEGACY_DATETIME_FORMAT`) and `popoto/fields/datetime_field.py` (`format_value_pre_save`).

**Key findings:**
- Values written from popoto #521 onward are stored via `obj.isoformat()`, which carries the UTC offset for an aware value, so they decode aware. Confirmed at `encoding.py:207`.
- Pre-#521 legacy rows were stored offset-free as `%Y%m%dT%H:%M:%S.%f`. `_decode_datetime` matches that shape with an anchored regex and re-attaches `timezone.utc` (`encoding.py:159`), so **legacy rows also decode aware**.
- That legacy re-attach is gated on `Defaults.DATETIME_KEY_LEGACY` (`encoding.py:153`). With the switch on, legacy rows decode **naive** again. The switch is read from `POPOTO_DATETIME_KEY_LEGACY`, which appears nowhere in this repo or in `.env.example`, so it is off.
- **A deliberately naive value written post-#521 round-trips naive.** `isoformat()` renders it as `2026-08-07T12:00:00.123456`, which the anchored legacy regex is explicitly written not to match (`encoding.py:92`). This is the one way a guard deletion could bite, and it is why the writer audit below is a prerequisite rather than a nicety.
- `DatetimeField.format_value_pre_save` stamps `datetime.now(timezone.utc)` for `auto_now` / `auto_now_add` fields.

## Data Flow

The only flow that matters is how a datetime reaches a guard. There are exactly two shapes, and the verdict for every site follows from which one it is.

**Shape A — popoto-only (guard is dead):**
1. **Entry point**: application code assigns an aware `datetime` (or a float) to a `DatetimeField` on an `AgentSession`.
2. **`AgentSession.__setattr__` (`models/agent_session.py:795-812`)**: a float becomes `datetime.fromtimestamp(value, tz=UTC)`; an ISO string is parsed and stamped UTC; a datetime passes through untouched.
3. **`DatetimeField.format_value_pre_save` → `encoding.py` encoder**: stored as `obj.isoformat()`, offset included.
4. **`_decode_datetime` on read**: aware, for both the modern and the legacy stored shapes.
5. **Output**: `record.updated_at` is aware. `if record.updated_at.tzinfo is None` can never be true.

**Two corrections to the choke-point story, both load-bearing** (raised as a blocker in critique round 1 and verified at `4b5a13184`):

- **`__setattr__` gates on `if name in self._DATETIME_FIELDS` (`:795`), and `created_at` is not in that set** (`_DATETIME_FIELDS` at `:744-757` lists `scheduled_at`, `started_at`, `updated_at`, `completed_at`, `response_delivered_at`, `last_authored_at`, `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`). `created_at` is `SortedField(type=datetime, partition_by="project_key")` (`:165`), and **nothing in `__setattr__` touches it**. Its only coercion is `_normalize_kwargs` (`:885-891`): it defaults to `datetime.now(tz=UTC)` when absent and converts `int | float` via `fromtimestamp(tz=UTC)` — at construction only, and never for a datetime that is already a datetime.
- **Construction bypasses `__setattr__` entirely.** popoto's `Model.__init__` does `self.__dict__.update(kwargs)` (noted in this repo at `models/agent_session.py:880-884`), so `_normalize_kwargs` is the *whole* of the ingress coercion for a constructor kwarg, for every field — not just `created_at`.

**Shape A-prime — the archive restore leg.** `agent/session_archive.py::_rehydrate_row` (`:433-449`) reconstructs a session from a SQLite payload: `_deserialize_payload` (`:206-225`) calls `datetime.fromisoformat(value)` on each archived timestamp string **with no offset stamping**, and the result is handed to `AgentSession(id=..., **fields)` — a constructor kwarg, so `__setattr__` never sees it and `_normalize_kwargs` passes a datetime straight through. `save(preserve_updated_at=isinstance(ts, datetime))` then writes it verbatim rather than re-stamping.

The loop is nonetheless closed for the deletions this plan makes: the archived string is `.isoformat()` of a live value, live values come from a popoto read (aware on 1.9.0, kill switch off) or from one of the five construction sites (all aware — see Risk 1), so the restore leg can only *preserve* naiveness, never manufacture it. That is why it is a longer derivation, not a blocker — and it is why the `created_at` fallback at `:2225` is reclassified as a **keep** below: `created_at` is the one field in the sweep with no `__setattr__` coercion at all, so it has no choke point to close the loop against a future caller.

**Shape B — mixed input (guard is load-bearing):**
1. **Entry point**: a timestamp arrives from outside popoto — an ISO string in a lock file or a flag file, a `gh` API response, a raw Redis string, a `TelegramMessage.date`, an epoch float, a CLI argument.
2. **`datetime.fromisoformat` / `strptime`**: produces a **naive** datetime whenever the source string carried no offset. Nothing in this path stamps a zone.
3. **Comparison against `datetime.now(UTC)`**: raises `TypeError` on naive-vs-aware, which in every one of these call sites is swallowed by a surrounding `except` and turns the feature into a silent no-op.
4. **Output**: correct only because the guard is there.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. Every deletion is inside a function body; no signature, return type, or contract moves.
- **Coupling**: unchanged. The one structural improvement available (routing bare one-liners through a module's general-purpose coercer) does not apply — the two candidate modules, `agent/session_runner/liveness.py` and `tools/session_progress.py`, already *are* the general-purpose coercers, and `tools/session_progress.py` already delegates to `liveness._as_unix_ts` with a local fallback.
- **Data ownership**: unchanged. No writes are added or removed; `reflections/audits/redis_quality_audit.py` is read-only by contract.
- **Reversibility**: trivial. Every change is a deleted branch or a reworded comment; `git revert` restores it exactly.

## Appetite

**Size:** Small

**Team:** One builder, one mutation validator. Critique round 1 was right that five named roles is a Medium-appetite roster on a Small-appetite change, and that a three-way builder split existed only to enforce a file-ownership rule that a single builder makes moot. Collapsed to two.

The one split that stays is builder / validator, and it is not organisational: a builder mutation-checking its own tests is precisely the #3173 failure this plan exists to avoid. The validator also needs sole ownership of its checkout while it mutates, so it runs after the builder's work is committed, never concurrently with author edits.

**Interactions:**
- PM check-ins: 0 (the classification rule is already settled by #3173; nothing here needs a scope call)
- Review rounds: 1

The coding is an hour. The review is the expensive part, because a reviewer has to independently confirm the input source of each of the four deleted guards, and confirm that each new test actually reaches the line it claims to cover.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto >= 1.9.0 installed | `.venv/bin/python -c "import popoto,pathlib,re;p=pathlib.Path(popoto.__file__).parent/'models/encoding.py';assert '_LEGACY_DATETIME_RE' in p.read_text()"` | The aware-decode contract every deletion rests on |
| `POPOTO_DATETIME_KEY_LEGACY` unset | `.venv/bin/python -c "from popoto.fields.constants import Defaults; assert not Defaults.DATETIME_KEY_LEGACY"` | With the kill switch on, legacy rows decode naive and the deletions are unsafe |

## Solution

### Key Elements

- **The verdict table**: one row per site, with its input source and its disposition. It is the deliverable; the code change follows from it mechanically. 32 rows, all of which land in the PR body.
- **Four deletions**: guards whose only inbound source is a popoto model field.
- **Twenty-eight keeps**, of which **nineteen** get a new one-line reason and nine are already settled or out of shape (see the breakdown below).
- **Nine prose corrections**: comments and docstrings that assert popoto 1.8.0 behaviour as fact, across three phrasings. Six sit on keep-sites and must be rewritten; three sit on delete-sites and go away with the guard.
- **Four non-vacuous tests**: one per deletion, each constructed so that it fails if the deletion is wrong, and each mutation-checked individually.

### Flow

Sweep (both shapes, `/usr/bin/grep`) → classify each of the 32 sites by inbound source → delete / keep+annotate → add a reaching test per deletion → mutation-check each test on its own → re-run both sweep shapes → the survivors are exactly the 28 keeps.

### Technical Approach

**The rule, restated (from #3173).** **Delete** the guard where popoto is provably the only inbound source of the value. **Keep** it, routed through the module's own general-purpose coercer, where inputs are genuinely mixed — Telegram `message.date`, ISO strings, epoch floats, file mtimes, `datetime.fromisoformat` of external data.

**Delete (4).**

| Site | Function | Inbound source | Why the guard is dead |
|---|---|---|---|
| `models/agent_session.py:1092` | `_heal_future_updated_at` | `record.updated_at` from `cls.query.all()` | Pure popoto read. `updated_at` **is** in `_DATETIME_FIELDS`, so every assignment coerces; the constructor-kwarg and archive-restore legs are covered by the Risk 1 writer audit. The `# Popoto strips tzinfo on load` comment at `:1089` goes too. |
| `reflections/pm_briefings/daily_log.py:382` | `_collect_sessions` | `s.completed_at` from `AgentSession.query.filter(status="completed")` | Pure popoto read; `completed_at` is in `_DATETIME_FIELDS`. Its `# Popoto strips tzinfo on save` comment at `:380` goes too. The surrounding `else` branch that coerces a float via `datetime.fromtimestamp` is a *type* branch and stays. |
| `reflections/crash_recovery.py:186` | resumable-session filter | `s.updated_at` from the resumable query | Pure popoto read. The four-line comment block at `:176-179` asserting "round-trips values as NAIVE" goes with it. |
| `reflections/audits/redis_quality_audit.py:61` | dead-channel scan | `chat.updated_at` | **Dead code, not a stale guard.** `Chat.updated_at` is `SortedField(type=float)` (`models/chat.py:23`) and line 55 already compares it against the float `month_ago`. The `isinstance(_ua, datetime)` branch has never been reachable. Delete the whole branch, leaving `days_inactive = int((_time.time() - (chat.updated_at or 0)) / 86400)`. |

**Reclassified from delete to keep: `models/agent_session.py:2225`** (`log_lifecycle_transition`, `prev_time = self.started_at or self.created_at`). Critique round 1 established that the original rationale — "both are `DatetimeField`" — is false. `created_at` is `SortedField(type=datetime, partition_by="project_key")` (`:165`) and is absent from `_DATETIME_FIELDS` (`:744-757`), so `__setattr__`'s coercion at `:795` never fires for it. Its only coercion is `_normalize_kwargs` (`:885-891`), which runs at construction and only for `int | float`. Because `prev_time` falls back to `created_at` whenever a session never started, this site reads the one field in the whole sweep with **no ingress choke point at all** — a naive datetime handed as a constructor kwarg, today or by a future caller, lands naive with nothing to catch it, and the archive-restore leg (`agent/session_archive.py:441-449`) passes exactly that shape. Under the #3173 rule popoto is therefore not provably the sole inbound source, so the guard **stays** and gets a one-line reason naming the two ingress paths. The sibling `isinstance(prev_time, int | float)` branch is a *type* branch and also stays.

**Keep (28).** Grouped by why:

*Nineteen keeps that get a new one-line reason:*

- *ISO strings from files and raw Redis* (6): `agent/agent_session_queue.py:1364` (restart-flag file), `monitoring/bridge_watchdog.py:943` (recovery-lock JSON), `bridge/telegram_bridge.py:343` (last-connected file), `bridge/poll_registry.py:299`, `bridge/poll_reconcile.py:53`, `bridge/poll_reconcile.py:250`.
- *ISO strings from an external API* (1): `reflections/pm_briefings/daily_log.py:352` (`_iso_in_window`, parsing `gh` output).
- *General-purpose coercers accepting `datetime | int | float | str`* (7): `agent/session_runner/liveness.py:77` and `:87`, `monitoring/session_watchdog.py:65`, `tools/session_progress.py:214` and `:224`, `agent/agent_session_queue.py:2994`, `tools/valor_session.py:419`.
- *The repo's canonical coercer* (2): `utils/utc.py:54` and `:64` (`to_unix_ts`). **Outside the issue's declared directory set** — the sweep never looked at `utils/` — but it is the single source of truth every read-path caller is pointed at, and its docstring still says "Popoto strips tzinfo on save". Guards stay; docstring is corrected.
- *Model ingress normalisation* (2): `models/agent_session.py:801` (`__setattr__`) and `:906` (`_normalize_kwargs`). These two are why the four deletions are safe and must be called out as such — deleting them would invalidate this plan. Their reasons must also record what they do **not** cover: `__setattr__` only fires for `_DATETIME_FIELDS` (so never for `created_at`), and neither fires for a constructor kwarg that is already a datetime.
- *No ingress choke point* (1): `models/agent_session.py:2225` (`log_lifecycle_transition`) — the reclassification above.

*Nine keeps verified and left untouched:*

- *Already settled by #3173* (6): `agent/session_health.py:403`, `:730`, `:6302`; `agent/session_pickup.py:52`, `:387`, `:600`. Re-litigating them burns review time and produces no diff.
- *Already annotated by PR #3180* (2): `ui/data/sdlc.py:823` and `:838` (`_safe_float`) — outside the declared directory set, and already carrying the correct mixed-input reason. Verified, no change.
- *Out of shape* (1): `utils/utc.py:28` (`to_local`) — it *raises* on a naive input as a validation contract rather than coercing one. It is not the guard this classification is about, and it is counted here only so the sweep arithmetic closes.

**Prose corrections (9 sites, 3 phrasings).** Measured with one alternation, `/usr/bin/grep -rniE "strips tzinfo|SortedField stores them|round-trips values as NAIVE"`. Every one of the nine is owned by a task in this plan — that is what makes the Verification row reachable.

Six sit on keep-sites and are rewritten:
- `utils/utc.py:44` (`to_unix_ts` docstring) — "Naive datetimes are treated as UTC (Popoto strips tzinfo on save)". This is the docstring the others defer to, so correcting it is what actually retires the claim.
- `agent/session_runner/liveness.py:69` — same claim. Rewrite: popoto 1.9.0 decodes aware; the guard exists for the ISO-string and float inputs this coercer also accepts.
- `tools/session_progress.py:201` — same claim, same correction, keeping the "one definition" delegation note.
- `tools/agent_session_scheduler.py:47` (`_to_ts` docstring) — "(Popoto strips tzinfo on save)". Same correction; keep the real reason it states, which is that `.timestamp()` on a naive value reads machine-local.
- `models/agent_session.py:2569` (`cleanup_expired`) — "to_unix_ts treats naive datetimes as UTC (Popoto strips tzinfo)." Same correction. **These last two were owned by no task in critique round 1**, which is exactly why the old Verification row could never reach zero.
- `monitoring/session_watchdog.py:57` — "matching how Popoto SortedField stores them". Rewrite to name the real reason (#777): the float and naive-string inputs, on a non-UTC host.

Three sit on delete-sites and are removed with the guard: `models/agent_session.py:1089`, `reflections/pm_briefings/daily_log.py:380`, `reflections/crash_recovery.py:176-179`.

**Testing the deletions non-vacuously.** This is where #3173's review found the defect, so each test states its own falsifiability, and **mutation-checking each test individually is a non-optional build task, not a review nicety** (task 4):
- Build the fixture by *writing through popoto and reading back*, never by constructing the object in memory — an in-memory `AgentSession(...)` never exercises decode and would pass with or without the guard.
- **Assert on the function's observable result, never merely on "it did not raise."** In three of the four sites the exception path is swallowed, so "no raise" is satisfied by the failure mode itself. Specifically: `_heal_future_updated_at` wraps its per-record body in `except Exception` and continues (`:1085-1108`), so a naive value makes it *silently skip the very rows it exists to repair* — the test must assert on the **returned heal count** (a future-dated fixture must produce `count == 1`), not on absence of a raise. `_collect_sessions` must assert the session appears in the returned items. `crash_recovery`'s filter must assert the session appears in `recent`.
- For `redis_quality_audit.py`, the test asserts `Chat.updated_at` is a `float` after a popoto round-trip — that is the claim that makes the branch dead, and it is checkable without touching the audit at all.
- **Mutation-check each test on its own**, one at a time, re-measuring after each: force a naive value into that one test's fixture and confirm that test goes red. A test that stays green under its own mutation is vacuous and does not ship. Mutating all four at once and reading a single red run proves nothing about any individual test.

## #3207 Consumer Audit

#3207 was closed as a duplicate into #3199, but #3199 is scoped to index repair and quarantine counters and does not touch the audit #3207 asked for. **This plan discharges it.** The ask: audit the `updated_at` / `created_at` / `completed_at` / `scheduled_at` consumers for naive comparison sites, because a surviving comparison against a naive `datetime.utcnow()` now raises `TypeError` on aware popoto values, and age arithmetic that assumed naive-local shifts by the host offset.

The named consumer classes and their pre-state, measured at `4b5a13184` with `/usr/bin/grep`:

| #3207 consumer | Site | Pre-state verdict |
|---|---|---|
| Watchdog liveness / staleness | `monitoring/session_watchdog.py::_to_timestamp` (`:52-70`) | Routes every read through one coercer. Aware-safe. Stale prose only (`:57`) — task 3. |
| Session-recovery drip | `reflections/agents/session_recovery_drip.py:71-75` | Delegates to `utils.utc.to_unix_ts`. Aware-safe, no naive comparison. |
| `agent/session_health.py` | `_ts` (`:398-408`), `_at_rest_coerce_ts` (`:722-736`), heartbeat age (`:6294-6308`) | Settled by #3173. Aware-safe. |
| Stale cleanup | `monitoring/session_tracker.py::cleanup_stale_sessions` (`:174-189`) | Compares against `utc_now()`, and its `Session` dataclass is stamped from `utc_now()` at `:84-91` — in-memory, never popoto. Aware-on-aware. |
| Stale cleanup (persisted) | `models/agent_session.py::cleanup_expired` (`:2560-2578`) | Delegates to `to_unix_ts`. Aware-safe. Stale prose only (`:2569`) — task 3. |
| Dashboard age columns | `ui/data/sdlc.py` — every field read goes through `_safe_float` (`:823`, `:838`); ages computed from floats (`:461-464`, `:1007-1012`) | Aware-safe, already annotated by PR #3180. |

**Audit result to record in the PR body:** no naive comparison site survives. Three corroborating repo-wide measurements, all pinned to `/usr/bin/grep` over `agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/ scripts/` excluding tests:

- `datetime\.utcnow\(\)` — **1** hit, `agent/session_telemetry.py:111`, which formats an ISO string for a log line and is compared against nothing. Out of shape; recorded, not changed. (A looser `utcnow()` matches 4, because `agent/build_pipeline.py` defines and calls a local `_utcnow()` that returns `datetime.now(UTC).strftime(...)` — aware, and a string. Use the anchored form.)
- bare `datetime\.now\(\)` with no `tz=` — **0** hits across `agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ ui/ scripts/`. Two hits inside `utils/utc.py` are prose telling callers not to, which is why `utils/` is excluded from this one row.
- `replace(tzinfo=None)` — **1** hit, `tools/valor_telegram.py:310`, which strips *both* sides of its own comparison symmetrically. Out of shape; recorded, not changed.
- `\.created_at\s*=` attribute assignments — **0** hits, which is what closes leg 1 of the Risk 1 writer audit for the field that has no `__setattr__` coercion.

The build re-runs these three as Verification rows so the audit is a checked claim rather than a remembered one, and task 6 confirms the audit table reaches the PR body.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/crash_recovery.py:191-198` wraps the guard in `except Exception` and logs a warning naming the bad `updated_at`. Removing the guard must not remove that handler; add an assertion that a genuinely bad value still produces the warning and the session is skipped rather than crashing the reflection.
- [ ] `reflections/audits/redis_quality_audit.py` runs entirely inside one `try` whose `except` appends the error as a finding and keeps `status: "ok"`. The dead-branch removal must keep that shape; assert the audit still returns `status == "ok"` with the branch gone.
- [ ] `models/agent_session.py:1085-1108` (`_heal_future_updated_at`) wraps its **per-record** body in `except Exception` and continues to the next record. The failure mode of a wrong deletion here is therefore a silent skip of exactly the corrupted rows the healer exists to repair — the #1645 failure class. The new test must assert the **returned heal count**, never "did not raise".
- [ ] `models/agent_session.py::log_lifecycle_transition` is no longer a delete site; its guard stays and needs no new failure-path coverage.
- [ ] `bridge/poll_reconcile.py:249-255` and `bridge/telegram_bridge.py:351-353` keep their broad handlers unchanged — they are keep-sites, untouched.

### Empty/Invalid Input Handling
- [ ] `_heal_future_updated_at` already `continue`s on `record.updated_at is None`; that branch is untouched and must stay covered.
- [ ] `_collect_sessions` already `continue`s on `ca is None` and on an unparseable float; both branches stay.
- [ ] `redis_quality_audit` uses `(_ua or 0)` for the missing case; after the deletion that becomes `(chat.updated_at or 0)` and must still yield a finite `days_inactive`.
- [ ] No agent-output processing is in scope, so the empty-output silent-loop class does not apply.

### Error State Rendering
- [ ] `reflections/pm_briefings/daily_log.py` renders to a Markdown briefing. Assert a session with a real `completed_at` still lands in the rendered day window after the deletion — a silently-empty briefing is the failure mode this section exists to catch.
- [ ] No other site in scope has user-visible output.

## Test Impact

- [ ] `tests/integration/test_updated_at_heal.py` — UPDATE: it is the closest existing coverage of `_heal_future_updated_at`. Confirm it round-trips through popoto rather than constructing in memory; if it constructs in memory, that is the vacuous shape and it gets rewritten, not extended. Its assertions must key on the returned heal count.
- [ ] `tests/unit/test_agent_session_updated_at_utc.py` — UPDATE: re-anchor its assertions on the aware-decode contract, and drop any assertion that depends on the deleted naive branch.
- [ ] `tests/unit/test_session_health_trusted_clock.py` — UPDATE: it references `_heal_future_updated_at`; verify it is unaffected and, if it asserts naive handling, re-anchor it.
- [ ] `tests/unit/reflections/test_daily_log_aggregator.py` — UPDATE: add the reaching test for `_collect_sessions` here rather than in a new file; it already owns this collector.
- [ ] `tests/unit/test_crash_recovery_gates.py` — UPDATE: add the reaching test for the resumable-session filter here.
- [ ] `tests/unit/test_reflection_pool_bulkhead.py` — UPDATE: it is the only test naming `redis_quality_audit`; add the `Chat.updated_at` float round-trip assertion alongside it, or in a new `tests/unit/reflections/test_redis_quality_audit.py` if the bulkhead test's fixtures do not fit.
- [ ] `tests/integration/test_lifecycle_transition.py` — NO CHANGE: `models/agent_session.py:2225` is now a keep, so `log_lifecycle_transition`'s duration math is untouched and needs no new test.
- [ ] `tests/unit/test_session_archive.py::test_restore_preserves_a_real_datetime_byte_identically` — NO CHANGE HERE, flagged only: this is the #3207 node, and it is owned by the #3199 lane. This plan does not touch `agent/session_archive.py` or its tests; it reads the restore path as evidence and records the finding.
- [ ] The other files that merely call `log_lifecycle_transition` incidentally need no change — nothing in that function moves.

## Rabbit Holes

- **Rewriting the sweep regex into something rigorous.** It already under-counts by one shape and skips two directories; the temptation is to build a proper AST-based finder. Do not. Run both shapes as two `/usr/bin/grep` lines and move on — a one-off classification does not earn a tool.
- **Migrating the keeps onto `utils.utc.to_unix_ts`.** See No-Gos for the argument; it is rejected there, not deferred here.
- **Backfilling a naive-write regression test into popoto.** The "post-#521 naive write round-trips naive" hazard is real, but it is a property of the library, and `~/src/popoto` is a different repo with its own pipeline. Record it as a risk, do not chase it.
- **Settling the archive-restore datetime contract.** The `_deserialize_payload` → `__dict__.update` ingress this plan documents is real and is #3207's second suggested next step. It belongs to whoever owns `agent/session_archive.py`, which is the #3199 lane. Record the finding in the PR body; do not change the archive here.
- **Auditing every remaining `datetime` comparison in the repo.** The scope is the guard shape the two sweeps match, plus the named #3207 consumer classes — not tz-correctness in general.
- **Touching `agent/session_health.py` or `agent/session_pickup.py`.** #3173 settled those six sites. Re-litigating them burns review time and produces no diff.

## Risks

### Risk 1: A deletion is wrong because some writer stores a naive datetime
**Impact:** The naive value survives the round-trip (popoto only re-attaches UTC for the pre-#521 stored shape, never for a post-#521 naive `isoformat()`), reaches a comparison against `datetime.now(UTC)`, and raises `TypeError`. In three of the four deletion sites that exception is swallowed by a surrounding handler, so the visible symptom is a reflection or healer that silently does nothing — the exact failure class that made #1653 take months to notice.
**Mitigation:** The writer audit is a prerequisite, not a review item, and critique round 1 established that it must cover **three** ingress shapes, not just one:

1. *Attribute assignment* (`session.updated_at = X`) — covered by `__setattr__` for every field in `_DATETIME_FIELDS`; **not** covered for `created_at`.
2. *Constructor kwargs* — popoto's `Model.__init__` does `self.__dict__.update(kwargs)`, so `__setattr__` never fires. `_normalize_kwargs` is the whole coercion and it converts only `int | float`. Measured at `4b5a13184`: five `AgentSession(...)` / `async_create(...)` construction sites outside tests (`agent/agent_session_queue.py:374`, `models/agent_session.py:1833`, `:1922`, `:1972`, plus `agent/session_archive.py:449`), and every one that names `created_at` passes `datetime.now(tz=UTC)`. Zero `.created_at =` assignments exist anywhere in the nine directories plus `scripts/`.
3. *The archive-restore leg* — `agent/session_archive.py:441-449` passes `datetime.fromisoformat(...)` output as constructor kwargs with no offset stamping, and `save(preserve_updated_at=isinstance(ts, datetime))` writes it verbatim. It can only preserve naiveness, never manufacture it, because the archived string is `.isoformat()` of a value that was itself aware. The loop is closed only as long as legs 1 and 2 hold.

The build re-runs the leg-1 and leg-2 enumerations as Verification rows so the claim is checked mechanically, not remembered. `models/agent_session.py:2225` is a keep precisely because leg 1 does not cover `created_at`.

### Risk 2: The new tests are vacuous
**Impact:** The deletions ship unverified and the plan's central deliverable — evidence, not just a diff — is not delivered. This is not hypothetical: #3173's review caught exactly this. On a deletion-heavy change the specific shape is a test that no longer *reaches* the deleted code and passes for that reason.
**Mitigation:** Every test builds its fixture by writing through popoto and reading back, asserts on an observable result rather than absence of a raise, and is mutation-checked **individually**, re-measuring after each mutation. The red output for each of the four goes in the PR body, one block per test.

A second shape of false green is a run in which no test executed at all. `scripts/pytest-clean.sh` currently exits 0 when zero tests ran (#3195), so every test run in this lane reads the **passed count off the pytest summary line**. "0 passed" is a failed verification, not a pass.

### Risk 3: `POPOTO_DATETIME_KEY_LEGACY` gets set on some machine later
**Impact:** Legacy rows start decoding naive again and every deleted guard becomes load-bearing at once, on whichever machine set it.
**Mitigation:** The switch is absent from the repo and from `.env.example` today. The plan records the dependency explicitly in `docs/features/utc-timestamps.md` so the next person to consider that switch finds out what it costs. A Prerequisites check asserts it is off at build time.

### Risk 4: File-level collision with the open #3199 lane
**Impact:** Both lanes write `models/agent_session.py`; the second to land hits a rebase.
**Mitigation:** The regions are disjoint (`:1089-1093`, `:2225`, `:2569` here; `~:2476-2528` there). Whoever lands second rebases; no coordination beyond that is warranted.

### Risk 5: A parallel lane's test run is mistaken for this lane's
**Impact:** Six lanes run here at once sharing a 15-slot Redis test-DB pool. A full-suite run from this lane starves the others and produces failures nobody can attribute.
**Mitigation:** **Scoped node ids only, never the full suite.** Run only the node ids covering the touched sites, through `scripts/pytest-clean.sh` (never bare `pytest`), in small batches. Every Verification row in this plan names node ids rather than a directory.

## Race Conditions

No race conditions identified. Every change is the removal or rewording of a synchronous, pure-branch guard inside a single function body. No new shared state, no new concurrency, no ordering dependency between the sites, and no write path is touched — `reflections/audits/redis_quality_audit.py` is read-only by its own module contract, and the other four deletions sit on read paths whose surrounding write behaviour is unchanged.

## No-Gos (Out of Scope)

- `models/job.py`. **Already done under #3173** and out of scope. It contributes zero hits to either sweep shape, so no arithmetic in this plan depends on it.
- [SEPARATE-SLUG #3199] `models/agent_session.py` index-repair and identityless-quarantine work. That lane owns `~:2476-2528`; this plan does not touch it.
- [SEPARATE-SLUG #3199] `agent/session_archive.py` and `tests/unit/test_session_archive.py` — the #3207 node and the archive datetime contract. This plan reads the restore path as evidence and records the ingress finding; it changes nothing there.
- Consolidating the nineteen annotated keep-sites onto `utils.utc.to_unix_ts`. **Not deferred to a ticket — deliberately rejected**, on an argument that stands without appeal to any document this plan edits: `to_unix_ts` returns a `float`, and ten of the keeps need an aware `datetime` for subtraction, so the consolidation is simply unavailable for most of them. (`docs/features/utc-timestamps.md` recorded a compatible standing decision, but that sentence is one this plan's task 5 rewrites, so it corroborates rather than justifies.)
- `utils/utc.py:28` (`to_local`). **Not deferred — out of shape**: it *raises* on a naive input as a validation contract rather than coercing one. It is not the guard this classification is about, and it is the one sweep hit counted among the 32 that receives no change.
- `tools/memory_search/cli.py:389,396`. **Not deferred — out of shape**: it unconditionally stamps UTC on `strptime` output of a CLI argument. There is no conditional guard to classify, and it matches neither sweep shape.
- `agent/session_telemetry.py:111` (`datetime.utcnow().isoformat()`) and `tools/valor_telegram.py:310` (`replace(tzinfo=None)` on both sides of one comparison). **Recorded by the #3207 audit, not changed**: neither compares a popoto value against a naive `now`.

## Update System

No update system changes required. This plan adds no dependency, no config file, no migration, and no new file that `/update` would need to propagate. The popoto floor it relies on (`>=1.9.0`) is already pinned in `pyproject.toml:21` and already asserted by `config/popoto_floor.py`, both landed by the `8c1a36ad1` bump.

One machine-state assumption is worth naming even though it needs no script change: `POPOTO_DATETIME_KEY_LEGACY` must stay unset fleet-wide. It is absent from `.env.example`, so no machine acquires it through the normal update path.

## Agent Integration

No agent integration required. Every change is internal to functions the bridge and the reflections runner already call; no new CLI entry point in `pyproject.toml [project.scripts]`, no new import for `bridge/telegram_bridge.py`, no MCP surface.

The one agent-visible surface in scope is indirect: `reflections/pm_briefings/daily_log.py` renders the PM briefing the agent posts, and `reflections/crash_recovery.py` feeds the crash-recovery reflection. Both are already wired; this plan only changes a guard inside them, and the Failure Path tests assert their output is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/utc-timestamps.md:87` — it currently names `monitoring/session_watchdog._to_timestamp`, `agent/session_health._ts`, and `ui/data/sdlc._safe_float` as "three older helpers ... intentionally left untouched". After this plan the inventory is larger and the reason is different: replace that clause with the settled verdict — which guards remain, and that they remain for non-popoto producers, not for popoto.
- [ ] Add to the same doc a short subsection recording the two escape hatches that bound the aware-decode contract: `POPOTO_DATETIME_KEY_LEGACY` must stay unset, and a deliberately-naive write still round-trips naive. This is the durable artifact — without it the next lane re-derives the same popoto source reading.
- [ ] No `docs/features/README.md` index entry is needed; `utc-timestamps.md` is already indexed.

### External Documentation Site
Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Rewrite the six stale rationale blocks on keep-sites named in the Solution section: `utils/utc.py:44`, `agent/session_runner/liveness.py:69`, `tools/session_progress.py:201`, `tools/agent_session_scheduler.py:47`, `models/agent_session.py:2569`, `monitoring/session_watchdog.py:57`.
- [ ] Give every one of the nineteen annotated keep-sites a one-line reason naming its non-popoto input source, per the issue's closing condition.
- [ ] Delete the three now-false comment blocks that accompany deletions: `models/agent_session.py:1089`, `reflections/pm_briefings/daily_log.py:380`, and the four-line block at `reflections/crash_recovery.py:176-179`.

## Success Criteria

Every number below is a measured pre-state at `4b5a13184` with `/usr/bin/grep`, so each criterion is checkable by arithmetic rather than by judgement.

- [ ] **The main sweep goes 31 → 28** and **the `getattr` sweep goes 1 → 0**, over the nine directories `agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/`, excluding tests. That is the four deletions and nothing else. Total work set 32 → 28.
- [ ] **The stale-prose sweep goes 9 → 0** under the three-phrasing alternation `strips tzinfo|SortedField stores them|round-trips values as NAIVE`, over the same nine directories. Every one of the nine is owned by a task in this plan.
- [ ] Every one of the 28 surviving guards carries a one-line reason naming its non-popoto input source, or is one of the nine verified-untouched sites listed in the Solution.
- [ ] All four deletions have a test that reads the value back through popoto and asserts an observable result, and **each test has been shown individually to go red** when a naive value is forced into its own fixture. Four separate red blocks are pasted in the PR body.
- [ ] The PR body carries a **per-site verdict table with one row per site — 32 rows** — each giving the classification and its reason, plus the exact total site count and the re-run sweep output.
- [ ] The PR body carries the **#3207 consumer audit table** and states that this lane discharges #3207's audit ask.
- [ ] `docs/features/utc-timestamps.md` no longer describes the inline guards as "intentionally left untouched" and does record the two escape hatches.
- [ ] No behaviour change: `reflections/pm_briefings/daily_log.py` still collects a session with a real `completed_at`, `redis_quality_audit` still returns `status: "ok"`, `crash_recovery` still filters by `updated_at`, `_heal_future_updated_at` still returns a non-zero count for a future-dated row.
- [ ] Scoped tests pass, with a **non-zero passed count read off the pytest summary line** for every batch. Exit 0 alone is not a pass (#3195).
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lane is small and the risk is concentrated in one place — proving the tests are not vacuous. Two roles, and the split between them is the one that carries risk rather than the one that carries files.

### Team Members

- **Builder**
  - Name: `tz-builder`
  - Role: all four deletions, all nineteen keep-site reasons, the six stale-prose rewrites, the new tests, and the doc update. One agent owns every file, so no file-ownership rule is needed.
  - Agent Type: builder
  - Resume: true

- **Validator (mutation check)**
  - Name: `mutation-validator`
  - Role: for each of the four new tests, individually force a naive value into that test's fixture, re-run that node, confirm it goes red, revert, and move to the next. Capture each red block separately.
  - Agent Type: validator
  - Resume: true

The builder and the validator never run concurrently, and the validator gets sole ownership of the checkout while it mutates. An author edit landing during a mutation run corrupts the measurement in both directions: the run can go red for the author's reason, or green because the mutation was overwritten. The builder commits and stops; the validator then runs alone.

A builder that mutation-checks its own tests is the #3173 failure this plan exists to avoid, which is why this one split survives the roster collapse.

## Step by Step Tasks

### 0. Re-measure the pre-state and build the 32-row verdict table
- **Task ID**: measure-prestate
- **Depends On**: none
- **Validates**: the numbers every later task and Verification row is checked against
- **Assigned To**: tz-builder
- **Agent Type**: builder
- **Parallel**: false
- Run both sweep shapes and the three-phrasing prose alternation with `/usr/bin/grep`, each piped to `wc -l`. **Count, never `head`.** Expect 31, 1, and 9. A different number means the tree moved; reconcile before changing anything.
- Write the 32-row verdict table (site, classification, reason) into the working notes. It is the PR body's centrepiece, so it is built first, not reconstructed at the end.
- Confirm the two Prerequisites rows pass.

### 1. Delete the `_heal_future_updated_at` guard and annotate the `models/agent_session.py` keeps
- **Task ID**: build-models
- **Depends On**: measure-prestate
- **Validates**: `tests/integration/test_updated_at_heal.py`, `tests/unit/test_agent_session_updated_at_utc.py`, `tests/unit/test_session_health_trusted_clock.py`
- **Assigned To**: tz-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete the `updated_at_utc.tzinfo is None` branch in `_heal_future_updated_at` (`:1092`) and the now-false comment at `:1089`.
- **Leave `log_lifecycle_transition` (`:2225`) alone** — it is a keep. Add its one-line reason: `created_at` sits outside `_DATETIME_FIELDS`, so `__setattr__` never coerces it, and the archive-restore leg passes a `fromisoformat` result as a constructor kwarg.
- Add a one-line reason to `__setattr__` (`:801`) and `_normalize_kwargs` (`:906`) naming ISO-string and float ingress as the non-popoto sources, **and recording what each does not cover**: `__setattr__` fires only for `_DATETIME_FIELDS`; neither fires for a constructor kwarg already typed as a datetime.
- Rewrite the stale prose at `:2569` (`cleanup_expired`).
- Add one test for the deletion: persist an `AgentSession` with a future-dated `updated_at` through popoto, re-read it, call `_heal_future_updated_at`, and **assert the returned count is 1**. Do not construct the session in memory; do not assert merely "no raise" — the per-record `except` swallows it.

### 2. Delete the three reflections guards
- **Task ID**: build-reflections
- **Depends On**: build-models
- **Validates**: `tests/unit/reflections/test_daily_log_aggregator.py`, `tests/unit/test_crash_recovery_gates.py`, `tests/unit/test_reflection_pool_bulkhead.py`
- **Assigned To**: tz-builder
- **Agent Type**: builder
- **Parallel**: false
- `daily_log.py:382`: collapse the ternary to `ts = ca`, delete the false comment on `:380`, keep the `else` float branch. Keep the mixed-input guard at `:352` and give it its reason.
- `crash_recovery.py:186`: delete the `getattr(updated, "tzinfo", None) is None` branch and the four-line comment block at `:176-179`. Keep the surrounding `except Exception` handler and its warning.
- `redis_quality_audit.py:60-62`: delete the whole `isinstance(_ua, datetime)` block as dead code and simplify to `days_inactive = int((_time.time() - (chat.updated_at or 0)) / 86400)`. Justify it in the diff as a dead-branch removal — `Chat.updated_at` is `SortedField(type=float)` (`models/chat.py:23`) and line 55 already compares it to a float. Drop the now-unused `datetime`/`UTC` imports if nothing else in the file needs them.
- Add a reaching test per deletion, each asserting an observable result: `_collect_sessions` returns the session in its items; the crash-recovery filter puts the session in `recent`; and for `redis_quality_audit`, `Chat.updated_at` is a `float` after a popoto round-trip — that is the claim the dead-branch verdict rests on.

### 3. Annotate the remaining keep-sites and correct the stale prose
- **Task ID**: build-annotations
- **Depends On**: build-reflections
- **Validates**: `python -m ruff check .`, `python -m ruff format --check .`
- **Assigned To**: tz-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite the remaining stale rationale blocks: `utils/utc.py:44`, `agent/session_runner/liveness.py:69`, `tools/session_progress.py:201`, `tools/agent_session_scheduler.py:47`, `monitoring/session_watchdog.py:57`. Say what is true now (popoto 1.9.0 decodes aware; the guard exists for the ISO-string / float / non-popoto producers the coercer also accepts) rather than what is no longer true. For `session_watchdog.py`, name the real reason from #777: float and naive-string inputs on a non-UTC host.
- Add a one-line reason to each remaining keep-site naming its actual input source: the restart-flag file, the recovery-lock JSON, the last-connected file, the raw Redis strings, the `gh` API output, the CLI argument.
- Do not edit `agent/session_health.py`, `agent/session_pickup.py`, or `ui/data/sdlc.py` — already settled. Do not edit `agent/session_archive.py` — #3199 owns it.
- Re-run the three-phrasing prose sweep; it must now be 0.

### 4. Mutation-check the four new tests, one at a time
- **Task ID**: validate-mutation
- **Depends On**: build-models, build-reflections
- **Assigned To**: mutation-validator
- **Agent Type**: validator
- **Parallel**: false
- **Non-optional.** Runs alone, after the builder has committed and stopped. No author edits during this task.
- For each of the four new tests, **individually**: force a naive datetime into that one test's fixture (write the field with `datetime.now()` rather than `datetime.now(UTC)`), re-run **that node alone** via `scripts/pytest-clean.sh`, confirm it fails, revert that mutation, then move to the next. Re-measure after each. Mutating all four at once and reading one red run proves nothing about any individual test.
- Read the result off the pytest summary line. A run reporting "0 passed" or collecting nothing is a failed measurement, not a pass (#3195).
- Capture each failing output verbatim as its own block; the four blocks go in the PR body as the non-vacuity proof.
- Report any test that stayed green — that test does not ship as-is.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-annotations
- **Assigned To**: tz-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `docs/features/utc-timestamps.md:87` per the Documentation section: replace the "three older helpers ... intentionally left untouched" clause with the settled verdict.
- Add the escape-hatch subsection (`POPOTO_DATETIME_KEY_LEGACY` must stay unset; a deliberately naive write still round-trips naive) and a note that `created_at` has no `__setattr__` coercion.

### 6. Final validation and PR body assembly
- **Task ID**: validate-all
- **Depends On**: validate-mutation, document-feature
- **Assigned To**: mutation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row, with `/usr/bin/grep` for every grep row.
- Confirm the PR body carries: the **32-row** per-site verdict table, the exact total site count, the re-run sweep output, the four separate mutation red blocks, and the #3207 consumer audit table with the statement that this lane discharges #3207's audit ask.

## Verification

**Every grep row uses `/usr/bin/grep`.** The interactive shell's `grep` is `ugrep` and honours `.gitignore`; a row re-run with it is a different measurement. `$DIRS9` below is `agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/`, and every sweep row appends `| /usr/bin/grep -v /tests/ | wc -l`.

**Scoped tests only, never the full suite.** Six lanes share a 15-slot Redis test-DB pool here. Run the node ids below in two batches through `scripts/pytest-clean.sh`, and **read the passed count off the pytest summary line** — the wrapper exits 0 when zero tests ran (#3195), so "0 passed" is a failed verification, and on a deletion-heavy change that is the failure mode to expect.

| Check | Command | Expected |
|-------|---------|----------|
| Scoped tests, batch 1 | `./scripts/pytest-clean.sh tests/integration/test_updated_at_heal.py tests/unit/test_agent_session_updated_at_utc.py tests/unit/test_session_health_trusted_clock.py` | summary line reports a **non-zero** passed count, 0 failed |
| Scoped tests, batch 2 | `./scripts/pytest-clean.sh tests/unit/reflections/test_daily_log_aggregator.py tests/unit/test_crash_recovery_gates.py tests/unit/test_reflection_pool_bulkhead.py` | summary line reports a **non-zero** passed count, 0 failed |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Heal guard gone | `/usr/bin/grep -c "updated_at_utc.tzinfo is None" models/agent_session.py` | pre-state 1 → **0** |
| Lifecycle guard **retained** | `/usr/bin/grep -c "prev_time if prev_time.tzinfo else" models/agent_session.py` | pre-state 1 → **1** (it is a keep; a 0 here is a regression) |
| daily_log ternary gone | `/usr/bin/grep -c "ca if ca.tzinfo else" reflections/pm_briefings/daily_log.py` | pre-state 1 → **0** |
| crash_recovery guard gone | `/usr/bin/grep -c 'getattr(updated, "tzinfo", None) is None' reflections/crash_recovery.py` | pre-state 1 → **0** |
| Dead datetime branch gone | `/usr/bin/grep -c "_ua.timestamp() if _ua.tzinfo else" reflections/audits/redis_quality_audit.py` | pre-state 1 → **0** |
| Main sweep shrinks by exactly the deletions | `/usr/bin/grep -rn --include='*.py' -E "tzinfo is None\|not [a-z_.]+\.tzinfo\b\|\.tzinfo else [a-z_.]+\.replace\(tzinfo=UTC\)" $DIRS9 \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **31** → **28** |
| `getattr` shape gone | `/usr/bin/grep -rn --include='*.py' -E 'getattr\([a-z_.]+, "tzinfo", None\) is None' $DIRS9 \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **1** → **0** |
| No stale 1.8.0 prose, all three phrasings | `/usr/bin/grep -rniE "strips tzinfo\|SortedField stores them\|round-trips values as NAIVE" --include='*.py' $DIRS9 \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **9** → **0**. All nine sites are owned by tasks 1-3; that ownership is what makes 0 reachable. |
| No naive writer reintroduced (leg 1) | `/usr/bin/grep -rnE "\.(updated_at\|started_at\|completed_at\|created_at\|scheduled_at)\s*=\s*datetime\.(now\(\)\|utcnow\(\))" --include='*.py' $DIRS9 \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **0** → **0**. Widened from critique round 1 to add `created_at`, `scheduled_at`, and `utcnow()`. |
| `created_at` has no attribute writer at all | `/usr/bin/grep -rnE "\.created_at\s*=" --include='*.py' $DIRS9 scripts/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **0** → **0**. This is the leg the missing `__setattr__` coercion makes load-bearing. |
| No naive `now` anywhere in scope | `/usr/bin/grep -rnE "datetime\.now\(\)\|datetime\.utcnow\(\)" --include='*.py' agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ ui/ scripts/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **1** → **1** (`agent/session_telemetry.py:111` only, out of shape and unchanged) |
| Legacy kill switch off | `.venv/bin/python -c "from popoto.fields.constants import Defaults; assert not Defaults.DATETIME_KEY_LEGACY"` | exit code 0 |
| Doc records the escape hatches | `/usr/bin/grep -c "POPOTO_DATETIME_KEY_LEGACY" docs/features/utc-timestamps.md` | output > 0 |
| Doc no longer says "intentionally left untouched" | `/usr/bin/grep -c "intentionally left untouched" docs/features/utc-timestamps.md` | pre-state 1 → **0** |
| No #3199 region touched | `git diff origin/main --unified=0 -- models/agent_session.py \| /usr/bin/grep -cE "repair_indexes\|_last_quarantined_identityless"` | match count == 0 |
| Archive untouched | `git diff --name-only origin/main \| /usr/bin/grep -c "session_archive"` | match count == 0 |

## Critique Results

Round 1 — FULL roster (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses. **NEEDS REVISION**: 2 blockers, 5 concerns, 2 nits.

**Revision pass complete.** All nine findings are addressed; the two blockers changed the plan's substance rather than its wording. The first reversed a verdict (`models/agent_session.py:2225` moves from delete to keep), which dropped the deletion count from five to four and the new-test count from five to four. The second widened a Verification row that could never have gone green and brought two orphan sites into an owning task. Every count in the plan was re-measured at `4b5a13184` with `/usr/bin/grep`.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | `created_at` is `SortedField(type=datetime, partition_by="project_key")` (`models/agent_session.py:165`), not a `DatetimeField`, and is absent from `_DATETIME_FIELDS` (`:744-757`) — so the `__setattr__` choke point the Data Flow section names does not cover it. The `:2225` deletion's stated premise is false. | **RESOLVED — verdict reversed.** Data Flow now carries the corrected facts and a new Shape A-prime for the archive-restore leg; `:2225` is **reclassified from delete to keep** in the Solution, on the grounds that `created_at` is the one field in the sweep with no ingress choke point. Task 1 leaves the guard in place and annotates it; task 4 no longer mutation-checks a test for it; the Verification table asserts the guard is **retained** (a 0 there is now a regression). | `__setattr__` gates on `if name in self._DATETIME_FIELDS` (`:795`); the real coercion for `created_at` is `_normalize_kwargs` (`:885-891`), and only at construction. Any test for this deletion must leave `started_at` unset so it reaches the `created_at` fallback. |
| BLOCKER | History & Consistency | Verification row "No stale popoto-strips claim" is wrong in both directions. Over-reach: 7 `strips tzinfo` hits exist and two are owned by no task (`models/agent_session.py:2569`, `tools/agent_session_scheduler.py:47`), so the row is guaranteed red. Under-detection: `monitoring/session_watchdog.py:57` and `reflections/crash_recovery.py:176` carry the stale claim in other words and are invisible to it. | **RESOLVED.** Row widened to the three-phrasing alternation `strips tzinfo\|SortedField stores them\|round-trips values as NAIVE`, measured pre-state **9** across 9 files, stated in the row. Both orphans are now **owned**: `models/agent_session.py:2569` by task 1, `tools/agent_session_scheduler.py:47` by task 3. All nine sites appear in the Solution's prose-correction list. | Measured pre-state: 7 for `strips tzinfo`, plus 2 for `SortedField stores them` / `round-trips values as NAIVE`, across 8 files. Widen to an alternation over all three phrasings and either own the two extra sites or scope the grep to owned files. |
| CONCERN | Risk & Robustness | `_heal_future_updated_at` is the healer #1645 motivated; its loop `except` means a naive value would make it silently skip exactly the corrupted rows it exists to repair. | **RESOLVED.** Failure Path Test Strategy now names the per-record `except`/continue at `:1085-1108` as the failure mode; task 1's test asserts the **returned heal count is 1** for a future-dated fixture. The Technical Approach adds a general rule: assert an observable result, never absence of a raise, at all three swallowed sites. | The handler wraps `for record in all_sessions:` (`:1085-1105`) and continues. Assert on the returned heal count, not on absence of a raise. |
| CONCERN | Risk & Robustness | Verification row "No naive writer reintroduced" omits `created_at` entirely and matches only the literal `datetime.now()` — missing `utcnow()`, `replace(tzinfo=None)`, and naive `fromisoformat` results. | **RESOLVED.** The row now covers `created_at` and `scheduled_at` and matches `datetime\.(now\(\)\|utcnow\(\))`; measured pre-state 0. Two further rows were added: a `\.created_at\s*=` attribute-writer row (pre-state 0, load-bearing because `__setattr__` skips the field) and a repo-wide naive-`now` row (pre-state 1, `agent/session_telemetry.py:111`, out of shape). `replace(tzinfo=None)` and `fromisoformat` are covered by the #3207 Consumer Audit section instead, which is where they belong. | `datetime.utcnow()` is the highest-value addition; it reads as correct and is how #1645 shipped. |
| CONCERN | Scope & Value | Success Criteria bullet 1 states no checkable number, and its implied arithmetic ignores `utils/utc.py:28` (excluded by shape) and `ui/data/sdlc.py:823,838` (keeps not counted among the eighteen). | **RESOLVED.** Success Criteria now state three integer transitions measured at `4b5a13184`: main sweep **31 → 28**, `getattr` sweep **1 → 0**, prose sweep **9 → 0**. The arithmetic closes explicitly: 32 sites = 4 deletions + 19 annotated keeps + 6 settled by #3173 + 2 annotated by #3180 + 1 out of shape (`utils/utc.py:28`). | Measured pre-state: 31 hits for the widened sweep across the 9 directories, 1 for the `getattr` shape. State the post-state as an integer. |
| CONCERN | Scope & Value | Five named roles for a Small-appetite change of five deleted branches, eighteen comments, and four docstrings. The three-way builder split exists only to enforce a file-ownership rule one builder makes moot. | **RESOLVED.** Roster collapsed to two: `tz-builder` and `mutation-validator`. The documentarian folded into the builder as task 5. The builder/validator split is kept and its reason restated, along with a sole-ownership rule for the mutation run. | Keep `validate-mutation` as a separate agent regardless — a builder mutation-checking its own tests is the #3173 failure. |
| CONCERN | History & Consistency | `docs/features/utc-timestamps.md:87` is cited three times as authority for not consolidating, while task 5 rewrites that exact sentence. The justification does not survive the plan's own doc edit. | **RESOLVED.** No-Gos now leads with the independent reason (`to_unix_ts` returns a float; ten keeps need an aware `datetime`) and cites the doc only as corroboration, flagging that this plan rewrites the sentence. The two other citations were removed from Rabbit Holes and the Solution. | Lead with the independent reason (`to_unix_ts` returns a float; most keeps need an aware datetime) and cite the doc only as corroboration. |
| NIT | History & Consistency | Rabbit Holes bullet 2 says "sixteen keeps"; every other count says eighteen. | **RESOLVED.** Every count in the plan is now derived from one measured set: 32 sites, 4 deletions, 28 keeps, 19 of them newly annotated. Rabbit Holes no longer states a keep count at all. | |
| NIT | Scope & Value | Rabbit Holes bullet 2 and No-Gos bullet 3 make the same argument in nearly the same words. | **RESOLVED.** The argument lives once, in No-Gos. Rabbit Holes bullet 2 is now a one-line pointer to it. | |

---

## Open Questions

None. The classification rule was settled by #3173, every verdict in this plan was derived from a read of the actual input source rather than a judgement call, and the one premise that could have needed a human — whether popoto 1.9.0's aware-decode really holds for legacy rows — was answered by reading the installed package. The scope widening to `utils/utc.py` is the only discretionary call, and it is a four-line docstring correction on the coercer the rest of the repo defers to.
