---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3181
last_comment_id: 5564069727
revision_applied: true
revision_applied_at: 2026-09-07T03:14:08Z
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
| Stale 1.8.0 prose, all three phrasings | `strips tzinfo\|SortedField stores them\|round-trips values as NAIVE` | **9** across 8 files (`models/agent_session.py` carries two) |

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
- **Issue #3207** — closed as a duplicate into #3199. Its first suggested next step is a consumer audit that #3199's scope does not reach; this plan discharges it, and its second (settle the archive datetime contract) is left with #3199, which owns `agent/session_archive.py`. Reading its failure output is what surfaced the archive-restore ingress leg documented in Data Flow.

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
- **Mutation-check each test on its own**, one at a time, re-measuring after each. A test that stays green under its own mutation is vacuous and does not ship. Mutating all four at once and reading a single red run proves nothing about any individual test.
- **The mutation shape is per-test, not uniform** — a uniform "force a naive datetime" recipe is a silent no-op on three of the four, for two independent reasons, both measured on this tree rather than assumed:

  1. **`save()` re-stamps `updated_at`.** `AgentSession.save()` runs `self.updated_at = utc_now()` at `models/agent_session.py:1020` on every call unless `preserve_updated_at=True`, which returns early at `:996-1004`. Measured: a naive `updated_at` written with a plain `save()` came back aware (`2026-09-07 10:08:02` naive in → `2026-09-07 03:08:02+00:00` out). Written with `save(preserve_updated_at=True)`, the same value came back byte-identical and naive. Only the `updated_at` tests (1 and 3) meet this mechanism; `save()` stamps no other field, so test 2's `completed_at` needs no flag.
  2. **The pre-#521 legacy encoding re-stamps UTC on decode.** `popoto/models/encoding.py:151-159` matches the anchored `_LEGACY_DATETIME_RE` (`:80`, `%Y%m%dT%H:%M:%S.%f`) and returns `legacy.replace(tzinfo=utc)` whenever `POPOTO_DATETIME_KEY_LEGACY` is off — which it is, fleet-wide. So **`tests/integration/test_updated_at_heal.py::_popoto_encode_datetime` (`:61-70`) and its caller `_seed_future_updated_at` (`:73-89`) must not be reused or imitated**: their payload decodes *aware* no matter what tzinfo went in, which makes any mutation through them a no-op. They are also a raw `hset` against a Popoto-managed key, which this repo forbids. Every fixture here writes through the ORM and reads back through `AgentSession.query`.

  What does **not** interfere: `AgentSession.__setattr__` (`:783-810`) coerces only `int | float`, `str`, and non-`datetime` types on `_DATETIME_FIELDS`. A `datetime` instance passes through untouched, naive or aware — measured, `tzinfo` was still `None` immediately after assignment. And popoto's forward encoder writes `obj.isoformat()` (`encoding.py:204-208`), which for a naive value renders `2026-09-07T10:08:02.649817` with date separators — a shape the anchored legacy regex deliberately cannot match (`encoding.py:92`) — so it decodes back through `fromisoformat` and stays naive. That is the property every mutation below relies on, and it is the same property the plan's popoto-1.9.0 premise rests on.

  Per-test shapes, each with the mechanism it defeats and the measured outcome, are in **task 4**.

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
- [ ] `tests/unit/reflections/test_redis_quality_audit.py` — CREATE (path pinned): the `Chat.updated_at` float round-trip assertion and its mutation target live here. `tests/unit/reflections/` already exists as a package. The path is fixed rather than optional because the Verification batch-2 row names this node id; an "either here or there" choice puts the fourth test somewhere no Verification row runs, while batch 2 still reports a healthy non-zero passed count from the other three files and the omission is invisible.
- [ ] `tests/unit/test_reflection_pool_bulkhead.py` — NO CHANGE: it is the only existing test naming `redis_quality_audit`, but it covers the bulkhead, not the field type. It stays in batch 2 as a regression check that the dead-branch removal did not disturb the audit's `status: "ok"` shape.
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

No race conditions identified. Every change is the removal or rewording of a synchronous, pure-branch guard inside a single function body. No new shared state, no new concurrency, no ordering dependency between the sites, and no write path is touched — `reflections/audits/redis_quality_audit.py` is read-only by its own module contract, and the other three deletions sit on read paths whose surrounding write behaviour is unchanged.

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
- [ ] Every one of the 28 surviving guards carries a one-line reason naming its non-popoto input source, or is one of the nine verified-untouched sites listed in the Solution. **Checked by two Verification rows**, not by inspection: a diff-based comment count across the twelve annotated files with a floor of 19, and task 0's `reason written` column reading `yes` for all nineteen.
- [ ] All four deletions have a test that reads the value back through popoto and asserts an observable result, and **each test has been shown individually to go red** under its own mutation shape from task 4 — naive `updated_at` with `save(preserve_updated_at=True)` for tests 1 and 3, naive `completed_at` with a plain `save()` for test 2, and a `datetime` assigned to the float `Chat.updated_at` for test 4. Four separate red blocks are pasted in the PR body. A uniform "force a naive datetime" recipe does not satisfy this criterion; it is a measured no-op on three of the four.
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
- Write the 32-row verdict table into the working notes with four columns: **site, classification, reason, `reason written`**. It is the PR body's centrepiece, so it is built first, not reconstructed at the end.
- The `reason written` column starts at `no` for the nineteen annotate-keeps and `n/a` for the four deletes and the nine verified-untouched keeps. Tasks 1-3 flip each `no` to `yes` as the comment lands, and task 6 asserts every one of the nineteen reads `yes`. This column is what turns the issue's headline closing condition — a one-line reason on every mixed-input survivor — from an assertion into a count.
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
- Add one test for the deletion, built as task 4's test-1 fixture: `AgentSession.create(...)`, then `s.updated_at = datetime.now(UTC) + timedelta(hours=7)` and `s.save(preserve_updated_at=True)`, re-read through `AgentSession.query.all()`, call `_heal_future_updated_at`, and **assert the returned count is 1**. The `preserve_updated_at=True` is load-bearing — a plain `save()` re-stamps `updated_at` at `:1020` and the fixture stops being future-dated at all. Do not construct the session in memory; do not seed via raw Redis or `_popoto_encode_datetime`; do not assert merely "no raise" — the per-record `except` swallows it.

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
- Add a reaching test per deletion, each built as its task-4 fixture and each asserting an observable result: `_collect_sessions` returns the session in its items (aware `completed_at`, plain `save()` — `save()` never stamps `completed_at`); the crash-recovery filter puts the session in `recent` (`RESUMABLE_STATUSES` status, `save(preserve_updated_at=True)`); and for `redis_quality_audit`, `Chat.updated_at` is a `float` after a popoto round-trip — that is the claim the dead-branch verdict rests on.
- **The `redis_quality_audit` test goes in `tests/unit/reflections/test_redis_quality_audit.py`**, a new file in the existing `tests/unit/reflections/` package. Pinned rather than left open, because the Verification batch-2 row names the node and an unpinned path is a test that no row runs (see Test Impact).

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
- Work **one test at a time**: apply that test's mutation, re-run **that node alone** via `scripts/pytest-clean.sh`, confirm it is red, revert the mutation, re-run the node to confirm it is green again, then move to the next. Mutating all four at once and reading one red run proves nothing about any individual test.
- Read the result off the pytest summary line. A run reporting "0 passed" or collecting nothing is a failed measurement, not a pass (#3195).
- Capture each failing output verbatim as its own block; the four blocks go in the PR body as the non-vacuity proof.
- **There is no uniform mutation.** Each test gets the shape below, chosen for the mechanism that would otherwise swallow it. Every shape was exercised against this tree before it was written down; the measured outcome is the expected red, so a validator run that disagrees with it is a signal about the build, not about the recipe.

**Test 1 — `_heal_future_updated_at` (`models/agent_session.py`), field `updated_at`.**

| | |
|---|---|
| Fixture (green) | `s = AgentSession.create(...)`; `s.updated_at = datetime.now(UTC) + timedelta(hours=7)`; `s.save(preserve_updated_at=True)`; re-read through `AgentSession.query.all()`; assert `AgentSession._heal_future_updated_at() == 1`. |
| Mutation | On that same fixture line only, `.replace(tzinfo=None)` the future value. Keep `preserve_updated_at=True`. |
| Mechanism it defeats | Both. `preserve_updated_at=True` returns at `:996-1004` before `self.updated_at = utc_now()` (`:1020`), so the naive value survives the write; the ORM path encodes `isoformat()` (no legacy `%Y%m%dT` shape), so it survives the read. |
| Measured | Round-trip returned `tzinfo=None`, value byte-identical. With the guard present the heal returned **1**. With the guard removed, `updated_at_utc <= now` raises `TypeError: can't compare offset-naive and offset-aware datetimes`, which the per-record `except Exception` at `:1105-1108` swallows — the record is skipped and the heal returns **0**, so the `== 1` assertion fails. **Red.** |
| Do not | Use `_seed_future_updated_at` / `_popoto_encode_datetime` from `tests/integration/test_updated_at_heal.py`. Their legacy payload decodes aware regardless of input tzinfo, so the mutation is a no-op — and they write raw Redis on a Popoto key. If the existing test uses them, that is the vacuous shape flagged in Test Impact: rewrite it onto the ORM fixture above. |

**Test 2 — `_collect_sessions` (`reflections/pm_briefings/daily_log.py`), field `completed_at`.**

| | |
|---|---|
| Fixture (green) | `s.completed_at = <aware datetime inside the target day>`; plain `s.save()`; assert the session appears in the returned items. |
| Mutation | `.replace(tzinfo=None)` on that `completed_at`. Plain `save()` is correct here — no flag. |
| Mechanism it defeats | The legacy encoder only. `save()` stamps `updated_at` and nothing else, so `completed_at` is never re-stamped and `preserve_updated_at` is irrelevant. |
| Measured | Naive `completed_at` round-tripped naive. With the guard removed, `start <= ts <= end` raises `TypeError`. That comparison sits **outside** every `try` in the function — the inner one wraps only the `datetime.fromtimestamp` float branch, and the two outer ones cover the import and the query — so the error propagates out of `_collect_sessions` and the test **errors**. A pytest error, not an assertion failure. **Red**; record it as red, not as a broken run. |

**Test 3 — resumable-session filter (`reflections/crash_recovery.py`), field `updated_at`.**

| | |
|---|---|
| Fixture (green) | Create a session whose `status` is in `RESUMABLE_STATUSES` (`abandoned`, `completed`, `failed`, `killed`); `s.updated_at = datetime.now(UTC) - timedelta(minutes=5)`; `s.save(preserve_updated_at=True)`; assert the session appears in `recent`. |
| Mutation | `.replace(tzinfo=None)` on that value, `preserve_updated_at=True` retained. |
| Mechanism it defeats | Both, exactly as test 1 — it is the same field on the same model. |
| Measured | Round-trip naive. With the guard, the session is in `recent`. With the guard removed, `updated > cutoff` raises `TypeError`, caught by the per-session `except Exception` at `:191-198`, and the session is dropped from `recent` — the membership assertion fails. **Red.** |

**Test 4 — dead `isinstance(_ua, datetime)` branch (`reflections/audits/redis_quality_audit.py`), field `Chat.updated_at`.**

| | |
|---|---|
| Fixture (green) | `Chat(chat_id=..., chat_name=..., updated_at=<float>)`; `save()`; re-read through `Chat.query`; assert `isinstance(chat.updated_at, float)`. |
| Mutation | **Not a naive datetime** — `Chat.updated_at` is `SortedField(type=float)` (`models/chat.py:23`), so tz-awareness is meaningless on it. The falsifier of the deadness claim is *a datetime in the field at all*: `chat.updated_at = datetime.now(UTC)` then `chat.save()`. |
| Mechanism it defeats | Neither of the two above — this test has no datetime write path to defend. It defeats the different failure mode: an assertion that would hold vacuously if the field could in fact carry a datetime. |
| Measured | The in-memory assignment succeeds (the attribute reads back as a `datetime`), and then `save()` raises `popoto ModelException: Model instance parameters invalid. Failed to save.` The test goes red **at the fixture line, before its own assertion**. That traceback *is* test 4's red block, and it is the stronger result: the field cannot hold a datetime, so the `isinstance(_ua, datetime)` branch at `:60-62` is unreachable by construction, which is precisely the dead-code verdict. Record the `ModelException` as the red evidence; do **not** read it as an inconclusive or broken run. |

- Report any test that stayed green under its own mutation — that test does not ship as-is.

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
- Run every Verification row, with `/usr/bin/grep` for every grep row and the nine directories spelled out literally — never through an unquoted shell variable, which under zsh silently returns 0 and reads as a pass on two of the rows.
- Assert task 0's `reason written` column reads `yes` for all **19** annotate-keeps, and that the diff-based comment-count row clears its floor of 19. Both, not one: the floor row cannot tell nineteen reasons from one long comment block, and the column cannot prove the comments reached the diff.
- For batch 2, read the **four new node ids by name** out of the run's collected output rather than trusting the passed count. Three passing files mask a fourth that was never collected, and the summary line looks identical either way.
- Confirm the PR body carries: the **32-row** per-site verdict table including the `reason written` column, the exact total site count, the re-run sweep output, the four separate mutation red blocks, and the #3207 consumer audit table with the statement that this lane discharges #3207's audit ask.

## Verification

**Every grep row uses `/usr/bin/grep`.** The interactive shell's `grep` is `ugrep` and honours `.gitignore`; a row re-run with it is a different measurement.

**The nine directories are spelled out literally in every row, never held in a shell variable.** The default shell here is zsh, which does *not* word-split an unquoted parameter expansion, so a `DIRS9="agent/ models/ ..."` string followed by an unquoted expansion passes the whole thing as one path, grep finds nothing, and the row prints **0**. Two of the sweep rows below *expect* 0, so that failure reads as a pass. Measured on this tree: the widened sweep returns **31** with the nine directories spelled out and **0** through the unquoted-variable form. Spell them out, or use a zsh array expanded as `"${dirs[@]}"`. Every sweep row appends `| /usr/bin/grep -v /tests/ | wc -l`.

**Scoped tests only, never the full suite.** Six lanes share a 15-slot Redis test-DB pool here. Run the node ids below in two batches through `scripts/pytest-clean.sh`, and **read the passed count off the pytest summary line** — the wrapper exits 0 when zero tests ran (#3195), so "0 passed" is a failed verification, and on a deletion-heavy change that is the failure mode to expect.

| Check | Command | Expected |
|-------|---------|----------|
| Scoped tests, batch 1 | `./scripts/pytest-clean.sh tests/integration/test_updated_at_heal.py tests/unit/test_agent_session_updated_at_utc.py tests/unit/test_session_health_trusted_clock.py` | summary line reports a **non-zero** passed count, 0 failed |
| Scoped tests, batch 2 | `./scripts/pytest-clean.sh tests/unit/reflections/test_daily_log_aggregator.py tests/unit/test_crash_recovery_gates.py tests/unit/reflections/test_redis_quality_audit.py tests/unit/test_reflection_pool_bulkhead.py` | summary line reports a **non-zero** passed count, 0 failed. The four **new** node ids must also appear by name in the run's collected output — a passed count alone cannot distinguish "all four ran" from "three ran and the fourth was never collected". |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Heal guard gone | `/usr/bin/grep -c "updated_at_utc.tzinfo is None" models/agent_session.py` | pre-state 1 → **0** |
| Lifecycle guard **retained** | `/usr/bin/grep -c "prev_time if prev_time.tzinfo else" models/agent_session.py` | pre-state 1 → **1** (it is a keep; a 0 here is a regression) |
| daily_log ternary gone | `/usr/bin/grep -c "ca if ca.tzinfo else" reflections/pm_briefings/daily_log.py` | pre-state 1 → **0** |
| crash_recovery guard gone | `/usr/bin/grep -c 'getattr(updated, "tzinfo", None) is None' reflections/crash_recovery.py` | pre-state 1 → **0** |
| Dead datetime branch gone | `/usr/bin/grep -c "_ua.timestamp() if _ua.tzinfo else" reflections/audits/redis_quality_audit.py` | pre-state 1 → **0** |
| Main sweep shrinks by exactly the deletions | `/usr/bin/grep -rn --include='*.py' -E "tzinfo is None\|not [a-z_.]+\.tzinfo\b\|\.tzinfo else [a-z_.]+\.replace\(tzinfo=UTC\)" agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **31** → **28** |
| `getattr` shape gone | `/usr/bin/grep -rn --include='*.py' -E 'getattr\([a-z_.]+, "tzinfo", None\) is None' agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **1** → **0** |
| No stale 1.8.0 prose, all three phrasings | `/usr/bin/grep -rniE "strips tzinfo\|SortedField stores them\|round-trips values as NAIVE" --include='*.py' agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **9** → **0**. All nine sites are owned by tasks 1-3; that ownership is what makes 0 reachable. |
| No naive writer reintroduced (leg 1) | `/usr/bin/grep -rnE "\.(updated_at\|started_at\|completed_at\|created_at\|scheduled_at)\s*=\s*datetime\.(now\(\)\|utcnow\(\))" --include='*.py' agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **0** → **0**. Widened from critique round 1 to add `created_at`, `scheduled_at`, and `utcnow()`. |
| `created_at` has no attribute writer at all | `/usr/bin/grep -rnE "\.created_at\s*=" --include='*.py' agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ utils/ ui/ scripts/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **0** → **0**. This is the leg the missing `__setattr__` coercion makes load-bearing. |
| No naive `now` anywhere in scope | `/usr/bin/grep -rnE "datetime\.now\(\)\|datetime\.utcnow\(\)" --include='*.py' agent/ models/ monitoring/ reflections/ bridge/ tools/ worker/ ui/ scripts/ \| /usr/bin/grep -v /tests/ \| wc -l` | pre-state **1** → **1** (`agent/session_telemetry.py:111` only, out of shape and unchanged) |
| Legacy kill switch off | `.venv/bin/python -c "from popoto.fields.constants import Defaults; assert not Defaults.DATETIME_KEY_LEGACY"` | exit code 0 |
| Every keep-site reason was actually written | `git diff origin/main --unified=0 -- agent/agent_session_queue.py agent/session_runner/liveness.py bridge/poll_reconcile.py bridge/poll_registry.py bridge/telegram_bridge.py models/agent_session.py monitoring/bridge_watchdog.py monitoring/session_watchdog.py reflections/pm_briefings/daily_log.py tools/session_progress.py tools/valor_session.py utils/utc.py \| /usr/bin/grep -cE '^\+[[:space:]]*#'` | pre-state **0** → **at least 19**. Those twelve files hold all nineteen annotated keep-sites. This row is what makes the issue's headline closing condition — a one-line reason on every mixed-input survivor — a checked claim; before it, twenty rows checked the deletions, the sweeps, the writer-audit legs, the docs and the ownership boundaries, and none checked that a single reason existed. `grep -c` exits 1 on zero matches, so a bare `0` here is the finding, not a tool error. |
| Every keep-site reason is accounted for individually | task 0's verdict table, `reason written` column | all **19** annotate-keeps read `yes`; the nine verified-untouched keeps read `n/a`. The row above is a floor and cannot tell nineteen one-line reasons from one file's twenty-line comment block; this column is the exact per-site count, and task 6 asserts it. |
| Doc records the escape hatches | `/usr/bin/grep -c "POPOTO_DATETIME_KEY_LEGACY" docs/features/utc-timestamps.md` | output > 0 |
| Doc no longer says "intentionally left untouched" | `/usr/bin/grep -c "intentionally left untouched" docs/features/utc-timestamps.md` | pre-state 1 → **0** |
| No #3199 region touched | `git diff origin/main --unified=0 -- models/agent_session.py \| /usr/bin/grep -cE "repair_indexes\|_last_quarantined_identityless"` | match count == 0 |
| Archive untouched | `git diff --name-only origin/main \| /usr/bin/grep -c "session_archive"` | match count == 0 |

## Critique Results

Round 2 — FULL roster (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses (Agent tool unavailable in the stage session). **NEEDS REVISION**: 1 blocker, 3 concerns, 0 nits. **All four are addressed below; this was the final authorized revision round.**

Every mutation shape written into task 4 during round 3 was *exercised against this tree* before it was recorded — the defect being fixed was a recipe asserted but never run, so asserting a replacement would have reproduced it. The probe created real `AgentSession` and `Chat` rows through the ORM on a claimed test db, read them back, and compared the guard-present and guard-removed code paths side by side. Measured outcomes are quoted in task 4 row by row. The probe was deleted after measurement; nothing from it ships.

All nine round-1 findings were re-verified as genuinely resolved, against source rather than against the plan's own word: the `:2225` reversal rests on facts confirmed at `models/agent_session.py:165`, `:744-756` and `:795`; the three-phrasing prose sweep measures exactly 9 hits with every site task-owned (across **8** files, not 9 — `models/agent_session.py` carries two, at `:1089` and `:2569`; corrected in round 3); and every count in the plan reproduces on a fresh `/usr/bin/grep` run of main (26 / 31 / 1 / 32, and 9 for the prose alternation). Round 2's findings are new surface, not residue.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Task 4's uniform mutation recipe ("write the field with `datetime.now()` rather than `datetime.now(UTC)`") is a silent no-op for both `updated_at` tests. `AgentSession.save()` unconditionally executes `self.updated_at = utc_now()` (`models/agent_session.py:1020`) unless `preserve_updated_at=True` (`:996`), so a naive fixture value is overwritten with an aware one; and the existing helper in the file task 1 names, `tests/integration/test_updated_at_heal.py::_popoto_encode_datetime` (`:61-70`), encodes with `dt.strftime("%Y%m%dT%H:%M:%S.%f")` — the pre-#521 legacy shape that `popoto/models/encoding.py:151-159` re-stamps UTC on regardless of the source value's tzinfo. Both mutated tests stay GREEN, and task 4's own rule then condemns two correct tests. The plan's non-negotiable deliverable, four individual red blocks, is unachievable as written. | **ADDRESSED (round 3)** | Task 4 replaced. Both mechanisms confirmed by measurement, then defeated per test. (1) Plain `save()` re-stamped a naive `updated_at` to aware; `save(preserve_updated_at=True)` returned it byte-identical and naive — so tests 1 and 3 carry the flag and test 2's `completed_at` provably does not need it, since `save()` stamps no other field. (2) `_seed_future_updated_at` / `_popoto_encode_datetime` are now explicitly banned in the Technical Approach and in task 4's "Do not" row: their `%Y%m%dT` payload hits `encoding.py:151-159` and decodes aware whatever went in, and it is a raw-Redis write on a Popoto key. The ORM path encodes `obj.isoformat()`, which the anchored legacy regex cannot match (`encoding.py:92`), so a naive value decodes naive — measured. Post-deletion, the naive fixture then produced `TypeError: can't compare offset-naive and offset-aware datetimes` at each guard site: heal count 1 → 0 (swallowed by the per-record `except`), crash-recovery membership true → false (swallowed by the per-session `except`), `_collect_sessions` raising out of the function uncaught. Three reds confirmed, each traced to its own guard. |
| CONCERN | Risk & Robustness | Task 4 applies one mutation shape to all four tests, but the fourth asserts `Chat.updated_at` is a `float` after a popoto round-trip, and the field is `SortedField(type=float)` (`models/chat.py:23`). Forcing a naive datetime into a float field is not a meaningful mutation, so the validator has no defined way to produce the fourth red block Success Criteria demand. The Technical Approach already notes this test is structurally different; task 4 does not carry that through. | **ADDRESSED (round 3)** | Test 4 now has its own row in task 4 with a named, non-datetime-agnostic mutation: assign `datetime.now(UTC)` to `Chat.updated_at` and `save()`. Measured: the in-memory assignment succeeds and the attribute reads back as a `datetime`, then `save()` raises `popoto ModelException: Model instance parameters invalid. Failed to save.` The refusal is exactly the stronger evidence the concern anticipated, so the plan names it as test 4's red block and instructs the validator to record the `ModelException` traceback rather than treat it as an inconclusive or broken run. Success Criteria restates the four shapes so a uniform recipe cannot satisfy the criterion. |
| CONCERN | Scope & Value | The issue's closing condition, "each with a one-line reason in its docstring," is restated as Success Criteria bullet 3 and as an Inline Documentation checkbox, but it is the one success criterion with no Verification row. Twenty rows check the deletions, the three sweep transitions, the writer-audit legs, the doc edits, the #3199 region and the archive; none checks that a single reason comment was written. Task 6 says "Run every Verification row", so nothing forces the nineteen annotations to exist before the lane declares itself done. | **ADDRESSED (round 3)** | Two Verification rows added, because either alone is defeatable. (a) **Every keep-site reason was actually written** — the suggested `git diff origin/main --unified=0 -- <files> \| /usr/bin/grep -cE '^\+[[:space:]]*#'` with a floor of 19, `/usr/bin/grep` pinned; the file list is **twelve** paths, not eleven (the concern's own list holds twelve). Verified to return 0 on clean main, and `grep -c` exits 1 there, which the row calls out so a bare `0` is not misread as a tool error. (b) **Every keep-site reason is accounted for individually** — task 0's verdict table gains a `reason written` column (`yes` for the nineteen annotate-keeps, `n/a` for the four deletes and nine untouched keeps), and task 6 asserts all nineteen read `yes`. The floor row cannot distinguish nineteen reasons from one long comment block; the column cannot prove the comments reached the diff. Success Criteria bullet 3 now names both rows. |
| CONCERN | History & Consistency | Test Impact leaves the fourth test's home open — the bulkhead file "or in a new `tests/unit/reflections/test_redis_quality_audit.py`" — but the Verification table's batch 2 pins three fixed paths and omits the alternative. If the builder takes the new-file branch, the fourth test is executed by no Verification row, and batch 2 still reports a healthy non-zero passed count from the other three files while the new node runs nowhere. | **ADDRESSED (round 3)** | The path is **pinned, not made conditional** — a conditional row is a second way to get this wrong. Test Impact now reads `tests/unit/reflections/test_redis_quality_audit.py` — CREATE, with the bulkhead file demoted to NO CHANGE (it covers the bulkhead, not the field type) and kept in batch 2 as a regression check on the audit's `status: "ok"` shape. Task 2 repeats the pinned path so the builder cannot drift. Batch 2's node-id set now carries all four files, and both that row and task 6 require the **four new node ids to be read by name out of the collected output** rather than inferred from the passed count, since three passing files and four passing files produce indistinguishable summary lines. |

**Found while fixing the blocker, not raised by any critic.** The Verification table held its nine directories in a `$DIRS9` shell variable. The default shell here is zsh, which does not word-split an unquoted parameter expansion, so every one of those five rows passed the whole string as a single path and returned **0** — measured, against a spelled-out form of the same sweep that returns 31. Two of the five rows *expect* 0, so the failure would have read as a pass, and the remaining three would have looked like a wildly over-delivering diff. Fixed by spelling the nine directories out in every row and stating the rule in the Verification preamble. Same defect class as the blocker: a command written down but never run.

---

## Open Questions

None. The classification rule was settled by #3173, every verdict in this plan was derived from a read of the actual input source rather than a judgement call, and the one premise that could have needed a human — whether popoto 1.9.0's aware-decode really holds for legacy rows — was answered by reading the installed package.

Two discretionary calls are recorded here rather than left implicit, both resolved without needing a human:

- **Widening the scope to `utils/utc.py` and `ui/data/sdlc.py`.** `utils/utc.py` holds the coercer the rest of the repo defers to and the docstring the other five stale claims echo, so leaving it out would have retired the repetitions while leaving the original. `ui/data/sdlc.py` contributes two keeps that need no change and are counted only so the arithmetic closes.
- **Reversing the `:2225` verdict from delete to keep** after critique round 1 falsified its premise. The alternative — keeping the deletion on a longer derivation through the archive-restore leg — is defensible but buys nothing: it removes two lines from a field that has no ingress choke point, in exchange for a fifth test and a fifth mutation check on a Small-appetite lane. The conservative reading of the #3173 rule ("popoto must be *provably* the only inbound source") settles it.
