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

**Thirty-two** naive-tzinfo coercion sites are scattered across sixteen files outside `models/job.py`. Every one of them was written against popoto 1.8.0, which round-tripped `DatetimeField` values as naive datetimes. popoto 1.9.0 decodes stored datetimes as aware UTC, so some of those guards now defend against a state that cannot occur, and nine carry comments that assert the old behaviour as fact.

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

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 (the classification rule is already settled by #3173; nothing here needs a scope call)
- Review rounds: 1

The coding is an hour. The review is the expensive part, because a reviewer has to independently confirm the input source of each of the five deleted guards, and confirm that each new test actually reaches the line it claims to cover.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| popoto >= 1.9.0 installed | `.venv/bin/python -c "import popoto,pathlib,re;p=pathlib.Path(popoto.__file__).parent/'models/encoding.py';assert '_LEGACY_DATETIME_RE' in p.read_text()"` | The aware-decode contract every deletion rests on |
| `POPOTO_DATETIME_KEY_LEGACY` unset | `.venv/bin/python -c "from popoto.fields.constants import Defaults; assert not Defaults.DATETIME_KEY_LEGACY"` | With the kill switch on, legacy rows decode naive and the deletions are unsafe |

## Solution

### Key Elements

- **The verdict table**: one row per site, with its input source and its disposition. It is the deliverable; the code change follows from it mechanically.
- **Five deletions**: guards whose only inbound source is a popoto model field.
- **Eighteen keeps**: guards with at least one non-popoto source, each given a one-line docstring reason.
- **Five prose corrections**: comments and docstrings that assert popoto 1.8.0 behaviour as fact. Three sit on keep-sites and must be rewritten; two sit on delete-sites and go away with the guard.
- **Five non-vacuous tests**: one per deletion, each constructed so that it fails if the deletion is wrong.

### Flow

Sweep → classify each site by inbound source → delete / keep+annotate → add a reaching test per deletion → re-run the sweep and the `getattr`-shaped variant → the survivors are exactly the eighteen keeps.

### Technical Approach

**The rule, restated.** A `tzinfo is None` coercion goes when a popoto model field is provably its only inbound source. It stays when the coercer also accepts floats, ISO strings, Telegram timestamps, or file mtimes.

**Delete (5).**

| Site | Function | Inbound source | Why the guard is dead |
|---|---|---|---|
| `models/agent_session.py:1092` | `_heal_future_updated_at` | `record.updated_at` from `cls.query.all()` | Pure popoto read. The `# Popoto strips tzinfo on load` comment above it goes too. |
| `models/agent_session.py:2225` | `log_lifecycle_transition` | `self.started_at or self.created_at` | Both are `DatetimeField`. The sibling `isinstance(prev_time, int \| float)` branch is a *type* branch, not a tz guard — **it stays**. |
| `reflections/pm_briefings/daily_log.py:382` | `_collect_sessions` | `s.completed_at` from `AgentSession.query.filter(status="completed")` | Pure popoto read. Its `# Popoto strips tzinfo on save` comment goes too. The surrounding `else` branch that coerces a float via `datetime.fromtimestamp` is a type branch and stays. |
| `reflections/crash_recovery.py:186` | resumable-session filter | `s.updated_at` from the resumable query | Pure popoto read. The four-line comment block at `:176` asserting "tzinfo stripped on read" goes with it. |
| `reflections/audits/redis_quality_audit.py:61` | dead-channel scan | `chat.updated_at` | **Dead code, not a stale guard.** `Chat.updated_at` is `SortedField(type=float)` (`models/chat.py:23`) and line 55 already compares it against the float `month_ago`. The `isinstance(_ua, datetime)` branch has never been reachable. Delete the whole branch, leaving `days_inactive = int((_time.time() - (chat.updated_at or 0)) / 86400)`. |

**Keep (18).** Grouped by why:

- *ISO strings from files and raw Redis*: `agent/agent_session_queue.py:1364` (restart-flag file), `monitoring/bridge_watchdog.py:943` (recovery-lock JSON), `bridge/telegram_bridge.py:343` (last-connected file), `bridge/poll_registry.py:299`, `bridge/poll_reconcile.py:53`, `bridge/poll_reconcile.py:250`.
- *ISO strings from an external API*: `reflections/pm_briefings/daily_log.py:352` (`_iso_in_window`, parsing `gh` output).
- *General-purpose coercers accepting `datetime | int | float | str`*: `agent/session_runner/liveness.py:77` and `:87`, `monitoring/session_watchdog.py:65`, `tools/session_progress.py:214` and `:224`, `agent/agent_session_queue.py:2994`, `tools/valor_session.py:419`.
- *The repo's canonical coercer*: `utils/utc.py:54` and `:64` (`to_unix_ts`). **Outside the issue's declared directory set** — the sweep never looked at `utils/` — but it is the single source of truth `docs/features/utc-timestamps.md:87` points every read-path caller at, and its docstring still says "Popoto strips tzinfo on save". Guards stay; docstring is corrected.
- *Model ingress normalisation*: `models/agent_session.py:801` (`__setattr__`) and `:906` (`_normalize_kwargs`). These two are the reason the five deletions are safe and must be called out as such in their docstrings — deleting them would invalidate this entire plan.
- *Already settled, untouched here*: `ui/data/sdlc.py:823` and `:838` (`_safe_float`) — also outside the declared directory set, but PR #3180 already annotated it with the correct mixed-input reason. Verified, no change.
- *Already settled by #3173, untouched here*: `agent/session_health.py:403`, `:730`, `:6302`; `agent/session_pickup.py:52`, `:387`, `:600`.

**Prose corrections on keep-sites (4).** Each currently states popoto 1.8.0 behaviour as present tense:
- `agent/session_runner/liveness.py:66-72` — "naive datetimes are treated as UTC — Popoto strips tzinfo on save". Rewrite: popoto 1.9.0 decodes aware; the guard exists for the ISO-string and float inputs this coercer also accepts.
- `tools/session_progress.py:200-203` — same claim, same correction, keeping the "one definition" delegation note.
- `monitoring/session_watchdog.py:54-59` — "matching how Popoto SortedField stores them". Rewrite to name the real reason (#777): the float and naive-string inputs, on a non-UTC host.
- `utils/utc.py:41-49` (`to_unix_ts`) — "Naive datetimes are treated as UTC (Popoto strips tzinfo on save)". This is the docstring the other three defer to, so correcting it is what actually retires the claim; the others merely stop repeating it.

**Testing the deletions non-vacuously.** This is where #3173's review found the defect, so each test states its own falsifiability:
- Build the fixture by *writing through popoto and reading back*, never by constructing the object in memory — an in-memory `AgentSession(...)` never exercises decode and would pass with or without the guard.
- Assert on the aware value the function produces, and assert the function does not raise. A naive value reaching the deleted line would raise `TypeError` on the naive/aware comparison downstream, which is the signal the test is really watching for.
- For `redis_quality_audit.py`, the test asserts `Chat.updated_at` is a float after a round-trip — that is the claim that makes the branch dead, and it is checkable without touching the audit at all.
- Mutation-check each test: re-insert the guard's inverse (force a naive value into the fixture) and confirm the test goes red. A test that stays green under that mutation is vacuous and does not ship.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/crash_recovery.py:191-198` wraps the guard in `except Exception` and logs a warning naming the bad `updated_at`. Removing the guard must not remove that handler; add an assertion that a genuinely bad value still produces the warning and the session is skipped rather than crashing the reflection.
- [ ] `reflections/audits/redis_quality_audit.py` runs entirely inside one `try` whose `except` appends the error as a finding and keeps `status: "ok"`. The dead-branch removal must keep that shape; assert the audit still returns `status == "ok"` with the branch gone.
- [ ] `models/agent_session.py::log_lifecycle_transition` has no handler around the deleted line; a naive value would propagate a `TypeError` to the caller. That is exactly the signal the new test watches for.
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

- [ ] `tests/integration/test_updated_at_heal.py` — UPDATE: it is the closest existing coverage of `_heal_future_updated_at`. Confirm it round-trips through popoto rather than constructing in memory; if it constructs in memory, that is the vacuous shape and it gets rewritten, not extended.
- [ ] `tests/unit/test_agent_session_updated_at_utc.py` — UPDATE: re-anchor its assertions on the aware-decode contract, and drop any assertion that depends on the deleted naive branch.
- [ ] `tests/unit/test_session_health_trusted_clock.py` — UPDATE: it references `_heal_future_updated_at`; verify it is unaffected and, if it asserts naive handling, re-anchor it.
- [ ] `tests/unit/reflections/test_daily_log_aggregator.py` — UPDATE: add the reaching test for `_collect_sessions` here rather than in a new file; it already owns this collector.
- [ ] `tests/unit/test_crash_recovery_gates.py` — UPDATE: add the reaching test for the resumable-session filter here.
- [ ] `tests/unit/test_reflection_pool_bulkhead.py` — UPDATE: it is the only test naming `redis_quality_audit`; add the `Chat.updated_at` float round-trip assertion alongside it, or in a new `tests/unit/reflections/test_redis_quality_audit.py` if the bulkhead test's fixtures do not fit.
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE: add the reaching test for `log_lifecycle_transition`'s duration math here; it already exercises the transition path end to end.
- [ ] The nineteen other files that merely call `log_lifecycle_transition` incidentally need no change — the deletion is behaviour-preserving for every aware input, which is all of them.

## Rabbit Holes

- **Rewriting the sweep regex into something rigorous.** It already under-counts by one shape and skips two directories; the temptation is to build a proper AST-based finder. Do not. Run both shapes as two grep lines and move on — a one-off classification does not earn a tool.
- **Migrating the sixteen keeps onto `utils.utc.to_unix_ts`.** The issue's instruction to "route the bare one-liners through the general-purpose coercer" reads like it applies here. It mostly does not: `to_unix_ts` returns a `float`, while ten of the keeps need an aware `datetime` for subtraction, and `docs/features/utc-timestamps.md:87` records a deliberate decision to leave three older inline helpers alone. Consolidation is a real idea and a separate one.
- **Backfilling a naive-write regression test into popoto.** The "post-#521 naive write round-trips naive" hazard is real, but it is a property of the library, and `~/src/popoto` is a different repo with its own pipeline. Record it as a risk, do not chase it.
- **Auditing every remaining `datetime` comparison in the repo.** The scope is the guard shape the sweep matches, not tz-correctness in general.
- **Touching `agent/session_health.py` or `agent/session_pickup.py`.** #3173 settled those six sites. Re-litigating them burns review time and produces no diff.

## Risks

### Risk 1: A deletion is wrong because some writer stores a naive datetime
**Impact:** The naive value survives the round-trip (popoto only re-attaches UTC for the pre-#521 stored shape, never for a post-#521 naive `isoformat()`), reaches a comparison against `datetime.now(UTC)`, and raises `TypeError`. In four of the five sites that exception is swallowed by a surrounding handler, so the visible symptom is a reflection or healer that silently does nothing — the exact failure class that made #1653 take months to notice.
**Mitigation:** The writer audit is a prerequisite, not a review item. All fifteen assignment sites for `updated_at` / `started_at` / `completed_at` were enumerated and every one is aware or a float that `AgentSession.__setattr__` converts. The build re-runs that enumeration as a Verification row so the claim is checked mechanically, not remembered.

### Risk 2: The new tests are vacuous
**Impact:** The deletions ship unverified and the plan's central deliverable — evidence, not just a diff — is not delivered. This is not hypothetical: #3173's review caught exactly this.
**Mitigation:** Every test builds its fixture by writing through popoto and reading back, and every test is mutation-checked by forcing a naive value into the fixture and confirming it goes red. The red output goes in the PR body.

### Risk 3: `POPOTO_DATETIME_KEY_LEGACY` gets set on some machine later
**Impact:** Legacy rows start decoding naive again and every deleted guard becomes load-bearing at once, on whichever machine set it.
**Mitigation:** The switch is absent from the repo and from `.env.example` today. The plan records the dependency explicitly in `docs/features/utc-timestamps.md` so the next person to consider that switch finds out what it costs. A Prerequisites check asserts it is off at build time.

### Risk 4: File-level collision with the open #3199 lane
**Impact:** Both lanes write `models/agent_session.py`; the second to land hits a rebase.
**Mitigation:** The regions are disjoint (`:1092` / `:2225` here, `~:2476-2528` there). Whoever lands second rebases; no coordination beyond that is warranted.

## Race Conditions

No race conditions identified. Every change is the removal or rewording of a synchronous, pure-branch guard inside a single function body. No new shared state, no new concurrency, no ordering dependency between the sites, and no write path is touched — `reflections/audits/redis_quality_audit.py` is read-only by its own module contract, and the other four deletions sit on read paths whose surrounding write behaviour is unchanged.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3199] `models/agent_session.py` index-repair and identityless-quarantine work. That lane owns `~:2476-2528`; this plan does not touch it.
- [SEPARATE-SLUG #3199] The `test_session_archive` naive-round-trip node and the quarantine-counter trio. Same lane, same file, different failure.
- Consolidating the eighteen keep-sites onto `utils.utc.to_unix_ts`. **Not deferred to a ticket — deliberately rejected**: `to_unix_ts` returns a float and ten of the keeps need an aware `datetime`, so the consolidation is not available for most of them, and `docs/features/utc-timestamps.md:87` records the standing decision to leave the remaining inline helpers alone.
- `utils/utc.py:28` (`to_local`). **Not deferred — out of shape**: it *raises* on a naive input as a validation contract rather than coercing one. It is not the guard this classification is about.
- `tools/memory_search/cli.py:389,396`. **Not deferred — out of shape**: it unconditionally stamps UTC on `strptime` output of a CLI argument. There is no conditional guard to classify.

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
- [ ] Rewrite the four stale rationale blocks named in the Solution section (`utils/utc.py:41-49`, `agent/session_runner/liveness.py:66-72`, `tools/session_progress.py:200-203`, `monitoring/session_watchdog.py:54-59`).
- [ ] Give every other keep-site a one-line reason naming its non-popoto input source, per the issue's closing condition.
- [ ] Delete the two now-false comment blocks that accompany deletions (`models/agent_session.py:1088`, `reflections/pm_briefings/daily_log.py:381`) and the four-line block at `reflections/crash_recovery.py:176-179`.

## Success Criteria

- [ ] The sweep, re-run in both shapes and across all directories including `utils/` and `ui/`, returns exactly the eighteen keep-sites plus the six #3173 survivors — and no delete-site.
- [ ] Every surviving guard carries a one-line reason naming its non-popoto input source.
- [ ] All five deletions have a test that reads the value back through popoto, and each test has been shown to go red when a naive value is forced into its fixture. The red output is pasted in the PR body.
- [ ] The PR body records the verdict for every site, delete and keep alike.
- [ ] `docs/features/utc-timestamps.md` no longer describes the inline guards as "intentionally left untouched" and does record the two escape hatches.
- [ ] No behaviour change: `reflections/pm_briefings/daily_log.py` still collects a session with a real `completed_at`, `redis_quality_audit` still returns `status: "ok"`, `crash_recovery` still filters by `updated_at`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lane is small and the risk is concentrated in one place — proving the tests are not vacuous. The split is by file-ownership so two builders never write the same file.

### Team Members

- **Builder (models + agent)**
  - Name: `models-builder`
  - Role: the two `models/agent_session.py` deletions and their tests
  - Agent Type: builder
  - Resume: true

- **Builder (reflections)**
  - Name: `reflections-builder`
  - Role: the three reflections deletions (`daily_log.py`, `crash_recovery.py`, `redis_quality_audit.py`) and their tests
  - Agent Type: builder
  - Resume: true

- **Builder (keep-site annotation)**
  - Name: `annotation-builder`
  - Role: the eighteen keep-site reasons and the four stale-prose rewrites, across `utils/`, `agent/`, `bridge/`, `monitoring/`, `tools/`
  - Agent Type: builder
  - Resume: true

- **Validator (mutation check)**
  - Name: `mutation-validator`
  - Role: for each of the five new tests, force a naive value into the fixture and confirm the test goes red; capture the output
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `utc-documentarian`
  - Role: `docs/features/utc-timestamps.md`
  - Agent Type: documentarian
  - Resume: true

File ownership is disjoint by construction: `models-builder` owns `models/agent_session.py`, `reflections-builder` owns `reflections/**`, `annotation-builder` owns everything else. `annotation-builder` must not edit the two files the other builders own, even for a keep-site comment — the two `models/agent_session.py` keeps (`:801`, `:906`) belong to `models-builder`.

## Step by Step Tasks

### 1. Delete the two `models/agent_session.py` guards
- **Task ID**: build-models
- **Depends On**: none
- **Validates**: `tests/integration/test_updated_at_heal.py`, `tests/unit/test_agent_session_updated_at_utc.py`, `tests/integration/test_lifecycle_transition.py`
- **Assigned To**: models-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete the `updated_at_utc.tzinfo is None` branch in `_heal_future_updated_at` (`:1092`) and the now-false comment above it.
- Replace the `prev_time if prev_time.tzinfo else prev_time.replace(tzinfo=UTC)` ternary in `log_lifecycle_transition` (`:2225`) with `prev_time`. Leave the sibling `isinstance(prev_time, int | float)` branch alone — it is a type branch.
- Add a one-line reason to the two keep-sites in this file (`__setattr__` at `:801`, `_normalize_kwargs` at `:906`) naming ISO-string ingress as the non-popoto source, and noting these two are what make the deletions elsewhere safe.
- Add a test per deletion that persists an `AgentSession` through popoto, re-reads it, and asserts the aware value flows through without raising. Do not construct the session in memory.

### 2. Delete the three reflections guards
- **Task ID**: build-reflections
- **Depends On**: none
- **Validates**: `tests/unit/reflections/test_daily_log_aggregator.py`, `tests/unit/test_crash_recovery_gates.py`, `tests/unit/test_reflection_pool_bulkhead.py`
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- `daily_log.py:382`: collapse the ternary to `ts = ca`, delete the false comment on `:381`, keep the `else` float branch.
- `crash_recovery.py:186`: delete the `getattr(updated, "tzinfo", None) is None` branch and the four-line comment block at `:176-179`. Keep the surrounding `except Exception` handler and its warning.
- `redis_quality_audit.py:60-62`: delete the whole `isinstance(_ua, datetime)` block as dead code and simplify to `days_inactive = int((_time.time() - (chat.updated_at or 0)) / 86400)`. Justify it in the diff as a dead-branch removal — `Chat.updated_at` is `SortedField(type=float)` (`models/chat.py:23`) and line 55 already compares it to a float. Drop the now-unused `datetime`/`UTC` imports if nothing else in the file needs them.
- Add a reaching test per deletion. For `redis_quality_audit`, the test asserts `Chat.updated_at` is a `float` after a popoto round-trip — that is the claim the dead-branch verdict rests on.

### 3. Annotate the keep-sites and correct the stale prose
- **Task ID**: build-annotations
- **Depends On**: none
- **Validates**: `python -m ruff check .`
- **Assigned To**: annotation-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite the four stale rationale blocks: `utils/utc.py:41-49`, `agent/session_runner/liveness.py:66-72`, `tools/session_progress.py:200-203`, `monitoring/session_watchdog.py:54-59`. Say what is true now (popoto 1.9.0 decodes aware; the guard exists for the ISO-string / float / non-popoto producers the coercer also accepts) rather than what is no longer true.
- Add a one-line reason to each remaining keep-site naming its actual input source: the restart-flag file, the recovery-lock JSON, the last-connected file, the raw Redis strings, the `gh` API output, the CLI argument.
- Do not edit `models/agent_session.py` or anything under `reflections/` — those belong to tasks 1 and 2.
- Do not edit `agent/session_health.py`, `agent/session_pickup.py`, or `ui/data/sdlc.py` — already settled.

### 4. Mutation-check the five new tests
- **Task ID**: validate-mutation
- **Depends On**: build-models, build-reflections
- **Assigned To**: mutation-validator
- **Agent Type**: validator
- **Parallel**: false
- For each of the five new tests: force a naive datetime into its fixture (write the field with `datetime.now()` rather than `datetime.now(UTC)`), re-run the node, and confirm it fails.
- Capture the failing output verbatim; it goes in the PR body as the non-vacuity proof.
- Revert every mutation. Report any test that stayed green — that test does not ship as-is.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-models, build-reflections, build-annotations
- **Assigned To**: utc-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/utc-timestamps.md:87` per the Documentation section.
- Add the escape-hatch subsection (`POPOTO_DATETIME_KEY_LEGACY`; naive writes round-trip naive).

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-mutation, document-feature
- **Assigned To**: mutation-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm the PR body carries a verdict for all twenty-three sites and the mutation-check red output.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `./scripts/pytest-clean.sh tests/unit -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Heal guard gone | `grep -c "updated_at_utc.tzinfo is None" models/agent_session.py` | match count == 0 |
| Lifecycle ternary gone | `grep -c "prev_time if prev_time.tzinfo else" models/agent_session.py` | match count == 0 |
| daily_log ternary gone | `grep -c "ca if ca.tzinfo else" reflections/pm_briefings/daily_log.py` | match count == 0 |
| crash_recovery guard gone | `grep -c 'getattr(updated, "tzinfo", None) is None' reflections/crash_recovery.py` | match count == 0 |
| Dead datetime branch gone | `grep -c "_ua.timestamp() if _ua.tzinfo else" reflections/audits/redis_quality_audit.py` | match count == 0 |
| No stale popoto-strips claim | `grep -rn "strips tzinfo" --include='*.py' agent/ models/ monitoring/ reflections/ bridge/ tools/ utils/ worker/ \| wc -l` | match count == 0 |
| No naive writer reintroduced | `grep -rnE "\.(updated_at\|started_at\|completed_at) = datetime\.now\(\)" --include='*.py' agent/ models/ bridge/ worker/ tools/ monitoring/ reflections/ \| wc -l` | match count == 0 |
| Legacy kill switch off | `.venv/bin/python -c "from popoto.fields.constants import Defaults; assert not Defaults.DATETIME_KEY_LEGACY"` | exit code 0 |
| Doc records the escape hatches | `grep -c "POPOTO_DATETIME_KEY_LEGACY" docs/features/utc-timestamps.md` | output > 0 |
| No #3199 region touched | `git diff origin/main --unified=0 -- models/agent_session.py \| grep -cE "repair_indexes\|_last_quarantined_identityless"` | match count == 0 |

## Critique Results

Round 1 — FULL roster (Risk & Robustness, Scope & Value, History & Consistency), sequential lenses. **NEEDS REVISION**: 2 blockers, 5 concerns, 2 nits.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | `created_at` is `SortedField(type=datetime, partition_by="project_key")` (`models/agent_session.py:165`), not a `DatetimeField`, and is absent from `_DATETIME_FIELDS` (`:744-757`) — so the `__setattr__` choke point the Data Flow section names does not cover it. The `:2225` deletion's stated premise is false. | | `__setattr__` gates on `if name in self._DATETIME_FIELDS` (`:795`); the real coercion for `created_at` is `_normalize_kwargs` (`:885-891`), and only at construction. Any test for this deletion must leave `started_at` unset so it reaches the `created_at` fallback. |
| BLOCKER | History & Consistency | Verification row "No stale popoto-strips claim" is wrong in both directions. Over-reach: 7 `strips tzinfo` hits exist and two are owned by no task (`models/agent_session.py:2569`, `tools/agent_session_scheduler.py:47`), so the row is guaranteed red. Under-detection: `monitoring/session_watchdog.py:57` and `reflections/crash_recovery.py:176` carry the stale claim in other words and are invisible to it. | | Measured pre-state: 7 for `strips tzinfo`, plus 2 for `SortedField stores them` / `round-trips values as NAIVE`, across 8 files. Widen to an alternation over all three phrasings and either own the two extra sites or scope the grep to owned files. |
| CONCERN | Risk & Robustness | `_heal_future_updated_at` is the healer #1645 motivated; its loop `except` means a naive value would make it silently skip exactly the corrupted rows it exists to repair. | | The handler wraps `for record in all_sessions:` (`:1085-1105`) and continues. Assert on the returned heal count, not on absence of a raise. |
| CONCERN | Risk & Robustness | Verification row "No naive writer reintroduced" omits `created_at` entirely and matches only the literal `datetime.now()` — missing `utcnow()`, `replace(tzinfo=None)`, and naive `fromisoformat` results. | | `datetime.utcnow()` is the highest-value addition; it reads as correct and is how #1645 shipped. |
| CONCERN | Scope & Value | Success Criteria bullet 1 states no checkable number, and its implied arithmetic ignores `utils/utc.py:28` (excluded by shape) and `ui/data/sdlc.py:823,838` (keeps not counted among the eighteen). | | Measured pre-state: 31 hits for the widened sweep across the 9 directories, 1 for the `getattr` shape. State the post-state as an integer. |
| CONCERN | Scope & Value | Five named roles for a Small-appetite change of five deleted branches, eighteen comments, and four docstrings. The three-way builder split exists only to enforce a file-ownership rule one builder makes moot. | | Keep `validate-mutation` as a separate agent regardless — a builder mutation-checking its own tests is the #3173 failure. |
| CONCERN | History & Consistency | `docs/features/utc-timestamps.md:87` is cited three times as authority for not consolidating, while task 5 rewrites that exact sentence. The justification does not survive the plan's own doc edit. | | Lead with the independent reason (`to_unix_ts` returns a float; most keeps need an aware datetime) and cite the doc only as corroboration. |
| NIT | History & Consistency | Rabbit Holes bullet 2 says "sixteen keeps"; every other count says eighteen. | | |
| NIT | Scope & Value | Rabbit Holes bullet 2 and No-Gos bullet 3 make the same argument in nearly the same words. | | |

---

## Open Questions

None. The classification rule was settled by #3173, every verdict in this plan was derived from a read of the actual input source rather than a judgement call, and the one premise that could have needed a human — whether popoto 1.9.0's aware-decode really holds for legacy rows — was answered by reading the installed package. The scope widening to `utils/utc.py` is the only discretionary call, and it is a four-line docstring correction on the coercer the rest of the repo defers to.
