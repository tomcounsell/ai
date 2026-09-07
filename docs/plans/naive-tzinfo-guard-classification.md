---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3181
last_comment_id:
---

# Classify the remaining naive-tzinfo guards by the #3173 rule

## Problem

Twenty-one `tzinfo is None` coercion guards are scattered across twelve files. Every one of them was written against popoto 1.8.0, which round-tripped `DatetimeField` values as naive datetimes. popoto 1.9.0 decodes stored datetimes as aware UTC, so some of those guards now defend against a state that cannot occur, and several carry comments that assert the old behaviour as fact.

**Current behavior:**
Nothing is broken. The cost is that every lane touching one of these files has to re-derive, from scratch, whether the guard in front of it is load-bearing. Worse, five of the guards carry prose that is now actively wrong ("Popoto strips tzinfo on load", "matching how Popoto SortedField stores them"), so a reader who trusts the comment reaches the wrong conclusion. One site (`reflections/audits/redis_quality_audit.py:61`) guards a field that has never been a datetime at all.

**Desired outcome:**
Every site in the sweep carries a settled verdict. Popoto-only guards are gone, with a test that would have caught the deletion if it were wrong. Mixed-input guards stay, each with a one-line docstring reason that a future reader can trust. A re-run of the sweep returns only the mixed-input survivors.

## Freshness Check

**Baseline commit:** `de229ee46` (recon), re-anchored at plan time to `d27b94f2d`
**Issue filed at:** 2026-09-05 (follow-up to #3173 / PR #3180, merged `2aecc7c93`)
**Disposition:** Minor drift

**File:line references re-verified:** all 26 sweep hits were re-derived from a live run of the issue's own grep at `de229ee46`. Every path the issue names still exists; the per-site line numbers in this plan are the measured ones, not the issue's.

**Cited sibling issues/PRs re-checked:**
- #3173 / PR #3180 — closed, merged at `2aecc7c93`. It is the source of the classification rule, and it left `agent/session_health.py` and `agent/session_pickup.py` classified as keeps.
- #3199 — **open**, and it edits `models/agent_session.py` in the `repair_indexes` region (~:2476-2528). This plan edits `:1092` and `:2225` of the same file. Disjoint regions, but the lanes will both be writing `models/agent_session.py`; whichever lands second rebases.
- #3207 — closed. It was the `test_session_archive` naive-round-trip node split out of #3199.

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
2. **`AgentSession.__setattr__` (`models/agent_session.py:795-812`)**: a float becomes `datetime.fromtimestamp(value, tz=UTC)`; an ISO string is parsed and stamped UTC; a datetime passes through untouched. **This is the choke point that makes Shape A safe** — nothing naive gets past it unless the caller hands it a naive datetime object, and no caller does.
3. **`DatetimeField.format_value_pre_save` → `encoding.py` encoder**: stored as `obj.isoformat()`, offset included.
4. **`_decode_datetime` on read**: aware, for both the modern and the legacy stored shapes.
5. **Output**: `record.updated_at` is aware. `if record.updated_at.tzinfo is None` can never be true.

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
| `POPOTO_DATETIME_KEY_LEGACY` unset | `.venv/bin/python -c "from popoto.models.db_key import Defaults; assert not Defaults.DATETIME_KEY_LEGACY"` | With the kill switch on, legacy rows decode naive and the deletions are unsafe |

## Solution

### Key Elements

- **The verdict table**: one row per site, with its input source and its disposition. It is the deliverable; the code change follows from it mechanically.
- **Five deletions**: guards whose only inbound source is a popoto model field.
- **Sixteen keeps**: guards with at least one non-popoto source, each given a one-line docstring reason.
- **Five prose corrections**: comments and docstrings that assert popoto 1.8.0 behaviour as fact. Three sit on keep-sites and must be rewritten; two sit on delete-sites and go away with the guard.
- **Five non-vacuous tests**: one per deletion, each constructed so that it fails if the deletion is wrong.

### Flow

Sweep → classify each site by inbound source → delete / keep+annotate → add a reaching test per deletion → re-run the sweep and the `getattr`-shaped variant → the survivors are exactly the sixteen keeps.

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

**Keep (16).** Grouped by why:

- *ISO strings from files and raw Redis*: `agent/agent_session_queue.py:1364` (restart-flag file), `monitoring/bridge_watchdog.py:943` (recovery-lock JSON), `bridge/telegram_bridge.py:343` (last-connected file), `bridge/poll_registry.py:299`, `bridge/poll_reconcile.py:53`, `bridge/poll_reconcile.py:250`.
- *ISO strings from an external API*: `reflections/pm_briefings/daily_log.py:352` (`_iso_in_window`, parsing `gh` output).
- *General-purpose coercers accepting `datetime | int | float | str`*: `agent/session_runner/liveness.py:77` and `:87`, `monitoring/session_watchdog.py:65`, `tools/session_progress.py:214` and `:224`, `agent/agent_session_queue.py:2994`, `tools/valor_session.py:419`.
- *Model ingress normalisation*: `models/agent_session.py:801` (`__setattr__`) and `:906` (`_normalize_kwargs`). These two are the reason the five deletions are safe and must be called out as such in their docstrings — deleting them would invalidate this entire plan.
- *Already settled by #3173, untouched here*: `agent/session_health.py:403`, `:730`, `:6302`; `agent/session_pickup.py:52`, `:387`, `:600`.

**Prose corrections on keep-sites (3).** Each currently states popoto 1.8.0 behaviour as present tense:
- `agent/session_runner/liveness.py:66-72` — "naive datetimes are treated as UTC — Popoto strips tzinfo on save". Rewrite: popoto 1.9.0 decodes aware; the guard exists for the ISO-string and float inputs this coercer also accepts.
- `tools/session_progress.py:200-203` — same claim, same correction, keeping the "one definition" delegation note.
- `monitoring/session_watchdog.py:54-59` — "matching how Popoto SortedField stores them". Rewrite to name the real reason (#777): the float and naive-string inputs, on a non-UTC host.

**Testing the deletions non-vacuously.** This is where #3173's review found the defect, so each test states its own falsifiability:
- Build the fixture by *writing through popoto and reading back*, never by constructing the object in memory — an in-memory `AgentSession(...)` never exercises decode and would pass with or without the guard.
- Assert on the aware value the function produces, and assert the function does not raise. A naive value reaching the deleted line would raise `TypeError` on the naive/aware comparison downstream, which is the signal the test is really watching for.
- For `redis_quality_audit.py`, the test asserts `Chat.updated_at` is a float after a round-trip — that is the claim that makes the branch dead, and it is checkable without touching the audit at all.
- Mutation-check each test: re-insert the guard's inverse (force a naive value into the fixture) and confirm the test goes red. A test that stays green under that mutation is vacuous and does not ship.

## Failure Path Test Strategy

## Test Impact

## Rabbit Holes

## Risks

## Race Conditions

## No-Gos (Out of Scope)

## Update System

## Agent Integration

## Documentation

## Success Criteria

## Team Orchestration

## Step by Step Tasks

## Verification

## Critique Results

---

## Open Questions
