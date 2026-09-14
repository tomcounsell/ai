---
status: Planning
type: chore
appetite: Small
owner: valor
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2733
last_comment_id: 5550859564
---

# Read-the-Room runs unconditionally

## Problem

Read-the-Room (RTR) is a pre-send pass that inspects a recent chat snapshot alongside a candidate
draft and returns `send`, `trim`, or `suppress`. It shipped in April behind `READ_THE_ROOM_ENABLED`,
which defaults `false` and has never been set in the vault `.env`. The flag was scaffolding for a
four-step canary that nobody ran, so the feature has never executed in production.

Underneath that flag sits a second, quieter defect. RTR's SDLC bypass reads
`getattr(session, "sdlc_slug", None)`. `sdlc_slug` is not a field on `AgentSession`; the real
predicate is the `is_sdlc` `@property`. The `getattr` default turns the typo into a silent `None`,
so the bypass has never fired either. `bridge/message_drafter.py` carries the identical bug one
layer up the same funnel, in the short-output early return's `is_sdlc` local.

The two facts multiply. Today the dead bypass is harmless because the outer flag keeps RTR from
running at all. Removing the flag inverts that: SDLC pipeline status messages reach the Haiku pass
for the first time with the guard that was supposed to exempt them inert.

**Current behavior:**

- `bridge/read_the_room.py:118` reads `READ_THE_ROOM_ENABLED` fresh per call, defaulting `false`.
  `.env.example:279` sets it `false`; the vault `.env` does not declare it. Every outbound message
  short-circuits at `reason="rtr_disabled"`.
- `bridge/read_the_room.py:533` and `bridge/message_drafter.py:1147` both test a field that does not
  exist. Seven test sites set `sdlc_slug` on fakes, so the suite proves the wrong attribute and would
  keep passing straight through the repair without exercising it.
- `docs/features/read-the-room.md` carries an "Enablement decision" flag paragraph, a "Rollout"
  procedure, and a flip criterion describing a decision nobody intends to run. Its bypass list at
  `:126` presents the SDLC bypass as working.
- `config/personas/teammate.md` (57 lines, in-repo, maintained) is shadowed on this machine by
  `~/Desktop/Valor/personas/teammate.md` — a 9-line, 276-byte stub last touched 2026-04-12, which
  `_resolve_overlay_path` prefers. Any edit to the repo overlay is silently inert here.

**Desired outcome:**

RTR runs unconditionally on every eligible Path A send with no env configuration present. The SDLC
bypass actually fires. Every other structural bypass and Path B's caller-type gate keep working. The
docs describe only the new status quo. The teammate overlay gains its ~3-line register nudge on a
path where the lines actually load.

### Accepted tradeoff

Removing the flag removes the kill switch. RTR's failure mode is *silent non-delivery*: a wrong
`suppress` leaves a human with a 👀 reaction and no answer, recoverable only by deploy. The fail-open
guard already covers API errors and malformed responses (every exception path returns `send` and
emits `rtr.failed`), so the exposure is judgment errors, not outages. Accepted. The `rtr.*`
`session_events` become the primary safety mechanism and are load-bearing from the first send.

## Freshness Check

**Baseline commit:** `974eb8d4c018f2f2da75c37030a3ae69e2e87acd`
**Issue filed at:** 2026-08-12T12:08:22Z (24 days before plan time)
**Disposition:** **Minor drift** — every claim in the issue and its scope-correction comment still
holds. Line numbers moved in three files and the test surface was reorganized by three merged
refactors. No premise changed.

**File:line references re-verified:**

| Cited | Claim | Verdict |
|---|---|---|
| `bridge/read_the_room.py:118` | env gate, default `false` | **Exact.** `os.environ.get("READ_THE_ROOM_ENABLED", "false")` inside `_read_enabled()` |
| `bridge/read_the_room.py:36` | module docstring names the flag | **Exact** |
| `bridge/read_the_room.py:481` | `read_the_room` docstring short-circuit list | **Exact** |
| `bridge/read_the_room.py:489` | docstring cites `session.sdlc_slug` | **Exact** |
| `bridge/read_the_room.py:533` | `getattr(session, "sdlc_slug", None)` | **Exact** |
| `bridge/message_drafter.py:1147` | `is_sdlc = bool(session and getattr(session, "sdlc_slug", None))` | **Exact** |
| `bridge/message_drafter.py:60` | `SHORT_OUTPUT_THRESHOLD = 200` | **Exact** |
| `models/agent_session.py:392` | `slug = KeyField(null=True)` | **Exact** |
| `models/agent_session.py:2271` | `is_sdlc` `@property` | **Exact.** The comment's re-derivation is right; `sdlc-1205`'s `:1612` is stale |
| `.env.example:278` | `READ_THE_ROOM_ENABLED=false` | **Drifted to `:279`** (later env additions above it) |
| `tools/valor_telegram.py:743 / :904 / :1448` | three docstring/comment mentions | **Drifted to `:742` / `:903` / `:1453`** |
| `agent/sdk_client.py:878` | `_resolve_overlay_path` | **Drifted to `:770`** (commit `da03701d8`, WORKER prompt diet #3069) |
| `docs/features/read-the-room.md:126` | SDLC bypass bullet implying it works | **Exact** |
| `~/Desktop/Valor/personas/teammate.md` | 9-line stub shadowing the repo copy | **Confirmed.** 9 lines, 276 bytes, mtime 2026-04-12. Repo copy: 57 lines, 2455 bytes |

**Sites the issue's recon did not enumerate** (found by a full-tree sweep, added to scope):

- `agent/output_handler.py:1046` — comment describing RTR as "gated by the `READ_THE_ROOM_ENABLED`
  env var (default off)".
- `agent/session_executor.py:2206` — comment naming `_should_run_rtr` as "also gated by
  `READ_THE_ROOM_ENABLED`".
- Live feature docs beyond `read-the-room.md`: `docs/features/bridge-worker-architecture.md:52`,
  `docs/features/agent-message-delivery.md:114`, `docs/features/telegram-messaging.md:270`,
  `docs/features/README.md:166`, `docs/features/context-recall-advisory.md:111` and `:134`,
  `docs/features/drafter-redundancy-suppression.md:157`.

**Test surface reorganized** (the largest drift). The issue's recon named
`tests/unit/test_valor_telegram.py` and `tests/unit/test_output_handler.py`; both were split into
per-theme packages by `379f39427` (#2879), `6dcbf17f3` (#2941), and `b82313599`. The real files are:

- `tests/unit/test_read_the_room.py` (2 setenv sites, `FakeSession`, the `sdlc_slug` bypass test)
- `tests/unit/valor_telegram/test_valor_telegram_rtr.py` (13 setenv sites)
- `tests/unit/output_handler/test_output_handler_transport.py` (raw `os.environ` set/restore)
- `tests/unit/output_handler/test_output_handler_filters.py` (raw set/restore + a `sdlc_slug` fake)
- `tests/unit/output_handler/test_output_handler_drafter.py` (`sdlc_slug` fake)
- `tests/integration/test_message_drafter_integration.py` (1 setenv, 2 `sdlc_slug` fakes)

**Cited sibling issues/PRs re-checked:** #1193 CLOSED, PR #1204 MERGED, #1203 CLOSED, #1205 CLOSED,
#2199 CLOSED, #1692 CLOSED, #2732 CLOSED. All closed before the issue was filed; none reopened. #2732
(the missing-context half of the prompting exchange) shipped, which is why this plan carries only the
register half.

**Commits on main since the issue was filed (touching referenced files):**

- `9fec0698b` (#3080, ask-me polls) — touched `bridge/read_the_room.py`, `bridge/message_drafter.py`,
  `docs/features/read-the-room.md`. **Irrelevant to the gate**: added poll rendering; the flag,
  bypass list, and `sdlc_slug` read are untouched.
- `3c77e1eab` (newest-wins session resolver) — touched `message_drafter.py`, `valor_telegram.py`,
  `sdk_client.py`. **Irrelevant**: session lookup only.
- `da03701d8` (#3069/#3139, WORKER prompt diet) — moved `_resolve_overlay_path` to `:770`. **Line
  drift only**; the private-first precedence is unchanged.
- `0f070970b` (#2694/#2695, context-recall on both edges) — added
  `CONTEXT_RECALL_PREFILTER_MAX_CHARS=200`. **This is the commit the scope-correction comment refers
  to**; it is the reason the short-output decision needs stating (see Decision D1).
- `379f39427` / `6dcbf17f3` / `b82313599` — the test split described above. **Changes where the work
  lands, not what it is.**
- `afc10ef93` ("describe current status quo, strip historical artifacts") — touched
  `docs/features/read-the-room.md` but left both the "Enablement decision" flag paragraph and the
  "Rollout" section intact. Re-read at `:87` and `:247`: still present, still describing the
  unexecuted canary.

**Active plans in `docs/plans/` overlapping this area:** two mentions, neither a conflict.

- `promise-gate-recorded-obligations.md` — imports `RTR_SDK_TIMEOUT` from `bridge/read_the_room.py`
  and explicitly No-Gos promoting it into `TimeoutSettings`. Does not touch the gate, the bypass
  list, or the flag. **Coordination note:** it adds a second Anthropic caller to `draft_message`'s
  main path, drawing on the same process-wide semaphore RTR uses. Making RTR unconditional raises
  that pool's baseline occupancy — recorded under Risks, not a blocker.
- `durability-room-job-agentrun.md` — proposes eventually re-parenting `bridge/read_the_room.py`
  under `Room`. Structural, future, and unaffected by removing an env read.
- `module-scope-env-reads-migration.md` (status Ready) — greps clean for `READ_THE_ROOM_ENABLED`.
  RTR reads its flag *per call*, not at import, so it was never on that plan's list. Deleting the
  read removes a candidate rather than colliding with one.

**Notes:** `python -m tools.code_impact_finder` was run per `docs/sdlc/do-plan.md` and returned
`WARNING: impact finder degraded (empty_index) — No results`. The blast radius in this plan is
therefore grep-derived and exhaustive rather than embedding-ranked: a full-tree sweep for
`READ_THE_ROOM_ENABLED` and `sdlc_slug` excluding `.venv/` and `.git/`, with archive paths
partitioned out deliberately (see Technical Approach).

## Prior Art

- **#1193 / PR #1204** (merged 2026-04-29) — the original RTR pre-send pass on Path A. Landed
  flag-off at `531e8f4e`. Established the fail-open contract, the 3s `RTR_SDK_TIMEOUT`, and the
  bypass list this plan preserves.
- **#1203** (closed) — Path B coverage and the caller-type gate. Its plan (`docs/plans/done/sdlc-1203.md`)
  explicitly reasoned that "the `sdlc_slug` short-circuit becomes inert" for human sends and treated
  that as correct-by-accident. Relevant because it is the second plan to walk past the bug.
- **#1205** (closed) — drafter redundancy suppression. **Its critique found this exact bug** and
  recorded it as a BLOCKER with the fix named:
  "Replace every `sdlc_slug` reference with `is_sdlc`. Use `getattr(session, "is_sdlc", False)` …
  Note that RTR has the same latent bug — its bypass never fires either. Fixing RTR is **out of
  scope** for this plan (tracked as a separate follow-up)." That follow-up was never filed. This plan
  is it.
- **#2199** (closed) — staleness signal, no-anchor reaction target, group-only decision. Wrote the
  flip criterion that was never exercised, and the `is_group_chat` predicate that must survive the
  doc surgery.
- **#1692** (closed) — moved `teammate.md` into the repo. Directly motivates Decision D2: the file
  was moved in precisely so it could be maintained, and the private stub predates that move.
- **#2732** (closed) — the missing-context half of the exchange that prompted this issue. Shipped, so
  only the register half remains.

## Research

No relevant external findings — proceeding with codebase context. This work removes an environment
read, corrects an attribute name, edits Markdown, and adds three lines to a prompt file. No external
library, API, or ecosystem pattern is involved, so Phase 0.7 was skipped per the skill's own criterion.

## Data Flow

**Path A (worker drafter → outbox), where the flag removal changes behavior:**

1. **Entry point** — worker finishes a turn; `agent/output_handler.py::TelegramRelayOutputHandler.send`
   receives the agent's text.
2. **Drafter** — `bridge/message_drafter.py::draft_message` composes. At `:1147` the short-output
   early return computes `is_sdlc` (today from the phantom `sdlc_slug`, so always `False`), and skips
   composition when the reply is <200 chars, non-SDLC, artifact-free, question-free, and fence-free.
   **After the repair**, real SDLC sessions stop taking the short path and get stage progress plus
   the link footer, which is what the branch was written to do.
3. **Redundancy filter** — `bridge/redundancy_filter.py`, SDLC sessions only. On suppress, returns
   early and RTR is never called.
4. **RTR** — `bridge/read_the_room.py::read_the_room(delivery_text, chat_id, session)`. Short-circuit
   ladder in order: `_read_enabled()` (**deleted**) → empty draft → `chat_id is None` → DM /
   unclassifiable → `len < SHORT_OUTPUT_THRESHOLD` → **SDLC bypass (repaired)** → deterministic
   stale-trigger suppress → snapshot fetch → empty snapshot → Haiku verdict.
5. **Output** — `send` writes `delivery_text`; `trim` writes `revised_text` (coerced to suppress
   under `TRIM_TOO_SHORT_THRESHOLD`); `suppress` queues a 👀 reaction when an anchor exists, else
   falls through to the original text.

**Path B (`valor-telegram send`), where only the flag disappears:**

1. `tools/valor_telegram.py::cmd_send` linkifies and truncates.
2. `_should_run_rtr` decides on caller type: `VALOR_SESSION_ID` set and non-empty → agent → RTR on;
   unset → human → RTR off. `--read-the-room` / `--no-read-the-room` override either way. **This gate
   is unchanged.**
3. On a yes, the same `read_the_room()` entry point runs, now without the env condition.

**Persona overlay flow, which decides whether the nudge exists at all:**

1. `agent/sdk_client.py:770 _resolve_overlay_path("teammate")` checks
   `~/Desktop/Valor/personas/teammate.md`.
2. On this machine that file exists (9-line stub), so it is returned and `config/personas/teammate.md`
   is never read.
3. The stub is prepended by `_base.md` and becomes the teammate session's behavioral prompt. Three
   lines added to the repo copy would reach nothing until step 2's shadow is removed.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1204 (#1193) | Wrote RTR's SDLC bypass as `getattr(session, "sdlc_slug", None)` and the tests that exercise it | Introduced the defect. The `getattr` default meant the wrong attribute name degraded to `None` instead of raising, and the tests set `sdlc_slug` on their own fakes — so the suite confirmed the typo instead of catching it |
| PR for #1203 | Extended RTR to Path B; noted the bypass "becomes inert" for human sends | Read the inertness as intentional for its own path and did not look upstream. Second walk-past |
| Plan `sdlc-1205` | Critique caught the bug, named the exact fix, and routed its own feature around it via `session.is_sdlc` | Deliberately descoped the RTR repair as "a separate follow-up" — and no follow-up issue was ever filed, so the finding died in an archived plan document |
| `docs/features/drafter-redundancy-suppression.md:157` | Documented RTR's bypass as "structurally present but currently a no-op" | Recorded the defect as a known property of the system rather than a bug with an owner. Documentation absorbed the problem |

**Root cause pattern:** `getattr(obj, name, default)` converts a name error into a silent policy
change. Every layer that touched this code chose to route around the dead guard instead of repairing
it, because the outer feature flag made the consequence invisible. The flag was load-bearing for the
bug, not just for the feature — which is exactly why the two must be fixed in one pass.

## Architectural Impact

- **New dependencies:** none. This deletes an env read, corrects an attribute name, and edits prose.
- **Interface changes:** `bridge/read_the_room._read_enabled()` is removed from the module's public
  surface. Nothing outside the module calls it except tests and `docs/plans/done/sdlc-1203.md`'s
  verification snippet (archived, not executed). `read_the_room()`'s signature, `RoomVerdict`, and
  every reason string except `rtr_disabled` are unchanged.
- **Coupling:** net decrease. One fewer env-var coupling between the module and the deploy
  environment; the SDLC bypass stops depending on a phantom field and starts depending on the model's
  own declared property.
- **Data ownership:** unchanged. RTR still owns no state; it reads a snapshot and appends
  `session_events`.
- **Reversibility:** low-to-moderate, and deliberately so. Reverting means a revert commit and a
  deploy — that is the accepted tradeoff, stated in the issue and restated above. The `rtr.*` event
  stream is the compensating control.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully settled by the issue body plus the owner's scope-correction comment)
- Review rounds: 1

The coding is an afternoon. The weight is in the three explicit decisions below and in not letting
the teammate overlay edit grow into a persona rewrite.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worktree venv on the pinned interpreter | `./.venv/bin/python -c "import sys; print(sys.version)"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |
| Anthropic credentials present | `./.venv/bin/python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | RTR's Haiku call; the integration test path needs it |
| Vault `.env` genuinely lacks the flag | `grep -q "^READ_THE_ROOM_ENABLED" $HOME/Desktop/Valor/.env; test $? -ne 0` | Confirms removal from `.env.example` leaves no orphaned vault key |
| Private teammate overlay is the known stub before deletion | `wc -l < ~/Desktop/Valor/personas/teammate.md` | Guards against deleting a file someone has since edited |

## Solution

### Key Elements

- **Flag removal** — `_read_enabled()` and its call site disappear from `bridge/read_the_room.py`;
  `READ_THE_ROOM_ENABLED` disappears from `.env.example`, from four docstring/comment sites in
  `tools/valor_telegram.py`, `agent/output_handler.py`, and `agent/session_executor.py`, from seven
  live feature docs, and from every test.
- **SDLC bypass repair** — both `getattr(session, "sdlc_slug", None)` reads become
  `getattr(session, "is_sdlc", False)`, matching the pattern `sdlc-1205` adopted for exactly this
  reason.
- **Test repair** — the bypass tests are rewritten to expose `is_sdlc` on their fakes, so they fail
  against the unrepaired code and pass against the repaired code. Flag-off test cases are deleted,
  not adapted, because the state they assert no longer exists.
- **Documentation surgery** — the enablement flag paragraph, the Rollout section, and the flip
  criterion are deleted. The `is_group_chat` rationale currently sharing the "Enablement decision"
  heading is preserved under an honest heading.
- **Overlay precedence resolution** — the shadowing private stub is deleted and `/update`'s existing
  persona drift check is generalized to cover `teammate` so no other machine keeps a silent shadow.
- **Teammate register nudge** — three lines in `config/personas/teammate.md`, on the now-live path.

### Flow

Agent finishes a turn → drafter composes (SDLC sessions now correctly take the full path) →
redundancy filter (SDLC only) → **RTR runs, no env condition** → structural bypasses decide → Haiku
verdict → outbox writes `send` / `trim`, or queues 👀 on `suppress`.

Operator flow, replacing the deleted rollout: watch `rtr.suppressed` and `rtr.bypassed` in
`session_events` after deploy → a wrong suppress is fixed by a prompt change or a revert commit,
not by a flag flip.

### Technical Approach

**Decision D1 — the `SHORT_OUTPUT_THRESHOLD` (200-char) bypass: KEEP it, unchanged, and say so.**

This is a stated call, not an inherited default. The scope-correction comment is right that
`bridge/context_recall.py` uses the same 200-char bound in the opposite direction, and that the
apparent disagreement deserves resolution rather than silence. Resolved as follows:

The two bounds are not two opinions about which band carries risk. They gate different mechanisms
answering different questions at different costs. Context-recall's outbound prefilter
(`bridge/context_recall.py:242`) is `len(stripped) <= 200 AND "?" in stripped` — a *shape* gate for
one narrow failure, a short PM message that asks the human to re-identify something the conversation
already named. RTR's `len(draft_text) < 200` bypass avoids paying a `TelegramMessage` snapshot fetch
plus a Haiku round trip on the delivery hot path, in precisely the band where the drafter itself
already skipped composition. Lowering or removing RTR's threshold would put every one-line
acknowledgment through a paid call on the send path, and the drafter/RTR band alignment is a
deliberate, documented invariant.

The band is also not uncovered. `CONTEXT_RECALL_OUTBOUND_ENABLED` defaults `true` and is live today,
and `bridge/promise_gate.py` gates the short path as of #2421. Short outbound messages already pass
two guards; what they skip is the *third and most expensive* one.

Finally, the honest reason to defer: RTR has produced zero production `rtr.*` events, because it has
never run. Choosing a new threshold now would replace one unmeasured guess with another. Making RTR
unconditional is the change that generates the first real data. Revisiting the threshold belongs to a
later issue with `rtr.bypassed reason="short_output"` counts behind it.

**Action:** `SHORT_OUTPUT_THRESHOLD` keeps its value, its single definition, and its import into
`read_the_room.py`. The decision and its reasoning get one short paragraph in
`docs/features/read-the-room.md` so the next reader finds a call rather than an accident.

**Decision D2 — overlay precedence: delete the private stub; do NOT invert `_resolve_overlay_path`.**

Two candidate fixes existed. Syncing the private stub to the repo content leaves two copies drifting
apart again on the next edit — the exact failure #1692 moved the file in to end. Inverting
`_resolve_overlay_path` to prefer the repo copy would change persona loading on every machine for
`engineer.md`, `project-manager.md`, `developer.md`, and `customer-service.md`, which are legitimately
per-machine private overlays. That is a much larger behavior change than this issue's ~3-line
secondary scope can carry.

**Action, in three parts:**

1. Delete `~/Desktop/Valor/personas/teammate.md` on this machine. Its 9 lines are a strict content
   subset of the repo overlay's Role, How I Interact, and Boundaries sections — nothing is lost. The
   file's full content is transcribed into the build task so the deletion is reviewable and
   reversible. This is a per-machine filesystem action, not a repo change.
2. Generalize `scripts/update/persona_drift.py` so `/update` Step 4.10 also warns when a private
   `teammate.md` shadows the repo copy. The module already does exactly this for `engineer.md`; it
   takes a `template_rel` / `overlay_path` pair and returns warnings, so covering a second persona is
   a loop, not a rewrite. Any fleet machine still holding a shadow surfaces it at update time instead
   of silently loading a stub.
3. Leave `_resolve_overlay_path`'s private-first order alone. Recorded as a No-Go with an
   anti-criterion.

**Decision D3 — repair the SDLC bypass, and correct the Scope framing.**

Both sites become `getattr(session, "is_sdlc", False)`:

- `bridge/read_the_room.py:533` — `if bool(session and getattr(session, "is_sdlc", False)):`
- `bridge/message_drafter.py:1147` — `is_sdlc = bool(session and getattr(session, "is_sdlc", False))`

The `getattr` form (rather than a bare `session.is_sdlc`) is deliberate and matches `sdlc-1205`: test
sessions and mocks that do not expose the property default to "no bypass" instead of raising.
`AgentSession.is_sdlc` reads `self.stage_states` through `_get_stage_states_dict` (which catches
`JSONDecodeError` / `TypeError` internally and returns `None`) and `self.classification_type` — both
plain field reads, so the property has no raise path of its own.

The issue's Scope table said "keep this bypass, reasoned as pipeline status messages must never be
blocked." **That framing is corrected here: repair and keep this bypass.** The bypass is not
currently working, and no live doc in this repo may say or imply that it is. Two live docs must be
corrected as part of the change, not left trailing:

- `docs/features/read-the-room.md:126` — drop the implication that the bullet describes working
  behavior.
- `docs/features/drafter-redundancy-suppression.md:157` — currently reads "structurally present but
  currently a no-op (the `sdlc_slug` attribute it reads does not …)". After the repair that sentence
  is false and must state the repaired predicate.

**Blast radius partition — live docs vs. archived plans.**

The full-tree sweep finds `READ_THE_ROOM_ENABLED` in `docs/archive/plans-completed/` and
`docs/plans/done/`. Those are the recorded plans of *shipped* work; rewriting them would falsify the
history of what was actually decided at the time, and the repo's no-legacy rule targets live
descriptions of the system, not its archive. **Scope for removal: `bridge/`, `agent/`, `tools/`,
`tests/`, `config/`, `scripts/`, `.env.example`, and `docs/features/`. Explicitly excluded:
`docs/archive/**` and `docs/plans/done/**`.** Every verification command below is path-scoped to
match, so a passing check means "no live reference remains", not "the grep was narrowed until it
passed".

**Teammate register nudge — the three lines.** Appended to the "How I Interact" list in
`config/personas/teammate.md`, kept to three bullets:

- Capability described in outcomes, never in internal tooling. Skill names, SDLC stage vocabulary,
  and `valor-session` invocations read as bureaucracy to someone outside the pipeline.
- When context is incomplete, name what specifically cannot be seen rather than asking an open-ended
  "what's the thing?" — "I can see there's an attachment but can't open it" is useful; "point me at
  this" hands the work back.
- Third-person mentions still get a first-person reply; being referred to in the third person is not
  a cue to narrate.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `bridge/read_the_room.py` — the fail-open guard around the Haiku call catches
      `anthropic.APITimeoutError`, `APIConnectionError`, `APIStatusError`, `ValueError` on malformed
      verdicts, and a last-resort `Exception`, each returning `send` and emitting `rtr.failed`. This
      is existing, correct behavior and is not modified. Confirm the existing fail-open tests still
      pass unmodified after `_read_enabled()` is gone — they must not have been reaching the fail-open
      path only because the flag short-circuit fired first.
- [x] `agent/output_handler.py:1144` — the `try:` around the `read_the_room` import and call already
      swallows any RTR escape and proceeds with the original `delivery_text`. Covered by
      `test_output_handler_filters.py::TestReadTheRoomWiring::test_rtr_failure_falls_open`, which
      patches `bridge.read_the_room.read_the_room` with an `AsyncMock(side_effect=RuntimeError(...))`
      and asserts the outbox still receives the original `delivery_text`. Mutation-verified: converting
      the `except` at `:1144` to a bare `raise` fails this test; restoring the swallow passes it.
- [x] `bridge/message_drafter.py:1147` — corrected: this call site **is** inside a `try`, one layer up
      the stack. `draft_message` itself is invoked at `agent/output_handler.py:754` inside a `try:`
      opened at `:751`, whose `except Exception as e:` at `:885` logs "Drafter failed ... falling back
      to raw text" and swallows. The two failure shapes at `getattr(session, "is_sdlc", False)` are not
      equivalent and must be tested separately, not as one case: an `AttributeError` from the `is_sdlc`
      property is masked by `getattr`'s default and returns `False` (same as a session with no
      attribute at all); any other exception class propagates out of `getattr` uncaught, up through
      `draft_message`, and is caught by the `:885` handler above — the observable effect is "drafter
      falls back to raw text," not "no bypass." `models/agent_session.py:2271`'s `is_sdlc` reads two
      properties deep (`_get_stage_states_dict()` at `:1779`, which itself swallows
      `JSONDecodeError`/`TypeError`, and `classification_type` at `:1579`, which reads
      `extra_context`), not "plain field reads" as an earlier draft of this plan said — neither has a
      raise path today, so this remains a boundary test documenting intent rather than a live bug.
- [ ] `scripts/update/persona_drift.py` — its `except Exception` converts any error into a warning
      string and never raises. The generalized version must preserve that; add a case where the
      teammate template is unreadable and assert a warning is returned rather than an exception.

### Empty/Invalid Input Handling

- [ ] `read_the_room` with `draft_text=""`, `"   "`, and `None` → `send` with reason `empty_draft`.
      These bypasses previously sat *behind* the flag check and are now the first thing evaluated;
      each needs a test that runs with no env configuration present.
- [ ] `chat_id=None` → `send` / `no_chat_id`. Same reasoning.
- [ ] `session=None` → the SDLC bypass must not fire (the `bool(session and ...)` short-circuit) and
      `_append_event` must no-op. Test with `session=None` explicitly.
- [ ] Empty snapshot → `send` / `empty_snapshot`, no Haiku call.

### Error State Rendering

- [ ] `suppress` with an anchor → 👀 reaction queued, outbox not written. `suppress` without an
      anchor → falls through and writes the original text. Both are user-visible failure renderings
      that now execute for real; each needs a test that asserts the user-observable outcome, not just
      the verdict.
- [ ] `trim` producing text below `TRIM_TOO_SHORT_THRESHOLD` → coerced to suppress. Existing
      behavior; confirm the test does not depend on env setup.

## Test Impact

- [ ] `tests/unit/test_read_the_room.py::FakeSession` — UPDATE: replace the `sdlc_slug` constructor
      parameter and attribute with an `is_sdlc` attribute, so the fake exposes what production reads.
- [ ] `tests/unit/test_read_the_room.py::_enable_rtr` / `::_disable_rtr` — DELETE: both helpers exist
      only to set `READ_THE_ROOM_ENABLED`. Remove them and every call site.
- [ ] `tests/unit/test_read_the_room.py::test_sdlc_session_short_circuits_with_event` — REPLACE:
      currently constructs `FakeSession(sdlc_slug="sdlc-1193")`, which proves the wrong attribute.
      Rewrite as `FakeSession(is_sdlc=True)`. **This test must be shown red against unrepaired
      `bridge/read_the_room.py` before the repair lands** — otherwise it is passing for the same wrong
      reason the old one did.
- [ ] `tests/unit/test_read_the_room.py` — UPDATE: every remaining test loses its `_enable_rtr` call
      and must pass with no RTR env var set. Add one test asserting RTR runs with the environment
      cleared, which is the acceptance criterion's core claim.
- [ ] `tests/unit/valor_telegram/test_valor_telegram_rtr.py` — UPDATE (13 setenv sites): remove every
      `monkeypatch.setenv("READ_THE_ROOM_ENABLED", ...)`. The `..., "true")` cases become unconditional.
- [ ] `tests/unit/valor_telegram/test_valor_telegram_rtr.py:108` (the `"false"` case) and its
      docstrings at `:97` / `:127` — DELETE the flag-off assertion, KEEP the test: its subject is the
      caller-type gate, which survives. Rewrite the docstrings so they describe the caller-type gate
      alone.
- [ ] `tests/unit/output_handler/test_output_handler_filters.py:342-372` — DELETE: the test is
      literally "With `READ_THE_ROOM_ENABLED=false`, the RTR call returns send"; that state no longer
      exists. Its raw `os.environ` set/restore block goes with it.
- [ ] `tests/unit/output_handler/test_output_handler_filters.py:46` — UPDATE: the fake's
      `s.sdlc_slug = kwargs.get("sdlc_slug", None)` becomes `is_sdlc`.
- [ ] `tests/unit/output_handler/test_output_handler_transport.py:359-384` — UPDATE: remove the
      `os.environ["READ_THE_ROOM_ENABLED"] = "1"` set/restore scaffolding; the test's subject is
      transport routing and it should exercise it with no env manipulation.
- [ ] `tests/unit/output_handler/test_output_handler_drafter.py:311` — UPDATE:
      `session.sdlc_slug = None  # keep is_sdlc False so the short-output path fires` becomes
      `session.is_sdlc = False`. The comment already names the real intent; the code did not match it.
- [ ] `tests/integration/test_message_drafter_integration.py:132` — UPDATE: drop the setenv.
- [ ] `tests/integration/test_message_drafter_integration.py:38` and `:147` — UPDATE:
      `mock_session.sdlc_slug = None` becomes `mock_session.is_sdlc = False`.
- [ ] `tests/unit/test_env_completeness.py` — UPDATE if it enumerates keys: removing
      `READ_THE_ROOM_ENABLED` from `.env.example` must leave the completeness check green. Run it
      after the removal even if no edit is needed.
- [ ] `tests/unit/test_update_persona_drift.py` — UPDATE: extend to cover the teammate template/overlay
      pair added to `scripts/update/persona_drift.py`, including the absent-overlay (no warning) case.

## Rabbit Holes

- **Rewriting the teammate persona.** The issue says ~3 lines and says "resist expanding this into a
  persona rewrite" twice. The overlay has a Role, a How I Interact list, a What I Help With list, a
  What I Defer list, a Job Scheduler block, and Boundaries — all of which invite editing once the file
  is open. Three bullets in one list. Nothing else.
- **Inverting `_resolve_overlay_path`.** Genuinely tempting: private-first is arguably the wrong
  default now that four personas live in the repo. It is also a fleet-wide persona-loading change
  riding inside a flag-removal chore. Delete the one shadowing file; leave the resolver.
- **Re-tuning `SHORT_OUTPUT_THRESHOLD` or `RTR_STALE_TRIGGER_SECONDS` while the file is open.** Both
  are provisional/tunable knobs with no production data behind them yet. D1 explains why the data
  arrives *after* this change, not before.
- **Promoting `RTR_SDK_TIMEOUT` into `TimeoutSettings`.** Config hygiene that looks adjacent because
  it lives in the same module and is about to become hot. `promise-gate-recorded-obligations.md`
  already No-Goed it with reasoning; do not relitigate it here.
- **Rewriting archived plan documents to remove the flag name.** They are records of decisions taken.
  See the blast-radius partition in Technical Approach.
- **Auditing every other `getattr(x, "...", default)` in the bridge for the same class of bug.** A
  real and probably worthwhile sweep, and completely unbounded from inside this chore.

## Risks

### Risk 1: A wrong `suppress` silently drops a real reply, with no flag to flip back

**Impact:** A human gets a 👀 reaction and no answer. Recovery requires a revert commit and a deploy
rather than an env edit. This is RTR's first-ever production exposure, so the false-suppression rate
is genuinely unknown.
**Mitigation:** Accepted by the issue, explicitly. The compensating control is observability, and it
is load-bearing: `rtr.suppressed` (including `reason="stale_trigger"`), `rtr.trimmed`,
`rtr.suppress_fallthrough`, `rtr.bypassed`, and `rtr.failed` all land in `session_events` and are
queryable per session. The build must confirm each of those five event types is still emitted after
the flag removal — the flag short-circuit returned before any event was written, so the emission
paths have never run in production either. The deterministic no-anchor fallthrough (suppress with no
reply target falls through to sending the original text) bounds the worst case: a suppressed message
with nowhere to react is still delivered.

### Risk 2: The repaired SDLC bypass changes drafter behavior, not just RTR behavior

**Impact:** `bridge/message_drafter.py:1147` is the *short-output early return*, not an RTR guard.
Today `is_sdlc` is always `False` there, so short SDLC replies take the fast path. After the repair
they stop taking it and get stage progress plus the link footer — which is what the branch was
written to do, but it is a live behavior change to every SDLC status message under 200 characters,
and it is a change to composition, not just to a bypass.
**Mitigation:** Name it as intended behavior in the plan and the PR rather than letting it surface as
a surprise. `tests/unit/output_handler/test_output_handler_drafter.py:311`'s own comment ("keep
is_sdlc False so the short-output path fires") shows the test author already understood the intent.
Add an explicit test for a short SDLC reply asserting it takes the *full* composition path, and one
for a short non-SDLC reply asserting it still takes the short path.

### Risk 3: Repaired tests pass for the wrong reason again

**Impact:** The original defect survived four years of test runs because the fakes set the attribute
production read, and both were wrong. Simply renaming `sdlc_slug` to `is_sdlc` in the fakes reproduces
that shape exactly — a green suite that proves the fake matches the code, not that the guard fires.
**Mitigation:** Mutation-check the repair. Run the rewritten
`test_sdlc_session_short_circuits_with_event` against the *unrepaired* `read_the_room.py` and require
it to fail; paste that red output into the PR. Do the same for the drafter's short-output SDLC test
against the unrepaired `message_drafter.py`. A test that cannot be shown red has not been shown to
reach the code.

### Risk 4: Deleting the private overlay changes teammate behavior on this machine beyond the 3 lines

**Impact:** The teammate persona jumps from a 9-line stub to the 57-line repo overlay in the same
change. That is a real behavioral shift — What I Help With, What I Defer, the Job Scheduler
restriction block, and Boundaries all become live for teammate sessions for the first time on this
machine.
**Mitigation:** Say so plainly in the PR: the deletion is what makes the maintained overlay load, and
loading it is the point. The repo overlay is the reviewed, version-controlled one; the stub is an
unreviewed local artifact from before #1692. The `/update` drift warning added in D2 means any other
machine in the fleet gets a visible notice rather than the same silent swap.

### Risk 5: RTR's semaphore share grows just as another caller is added to the same pool

**Impact:** `semaphore_slot()` is a process-wide `asyncio.Semaphore` (default 5) shared by RTR, router
classification, and memory extraction, acquired with a bare unbounded `await`. RTR moves from zero
acquisitions to one per eligible send. `promise-gate-recorded-obligations.md` plans to add a second
`draft_message` caller to the same pool, and that plan's own critique already flagged the unbounded
acquire as its Risk 1b.
**Mitigation:** Out of scope to fix here, in scope to name. Record the interaction in the PR and in
`docs/features/read-the-room.md`'s cost section so whichever of the two plans lands second inherits a
written warning rather than rediscovering it. RTR's fail-open path already returns `send` on
`APITimeoutError`, so contention degrades to today's behavior plus latency, not to non-delivery.

## Race Conditions

No new race conditions introduced. The change removes a synchronous environment read and corrects an
attribute name on an in-memory object; neither introduces shared mutable state, cross-process
coordination, or new ordering requirements.

One pre-existing timing hazard becomes reachable for the first time and is therefore worth recording:

### Race 1: Snapshot freshness versus draft composition

**Location:** `bridge/read_the_room.py`, between the snapshot fetch and the Haiku verdict.
**Trigger:** A message arrives in the group chat after RTR fetches its `k=10` / 300-second snapshot
but before the verdict returns. RTR judges against a room that has already moved.
**Data prerequisite:** The `TelegramMessage` rows backing the snapshot must be written before the
fetch to be visible to it.
**State prerequisite:** None beyond that.
**Mitigation:** Inherent to any snapshot-based judgment and bounded by the 3-second `RTR_SDK_TIMEOUT`,
so the staleness window is at most a few seconds. No mitigation is added: closing it would require
holding a lock across an LLM call, which is worse than the problem. Recorded because this window has
never actually existed in production before and a future investigator should find it named rather
than deduce it from a mis-verdict.

## No-Gos (Out of Scope)

- **[EXTERNAL] Deleting `~/Desktop/Valor/personas/teammate.md` on other fleet machines.** Each host
  holds its own private overlay directory and this agent can only reach the local one. The `/update`
  drift warning added under D2 is the fleet-wide mechanism; acting on the warning is a human action
  per machine.
- **[EXTERNAL] Removing `READ_THE_ROOM_ENABLED` from any vault `.env`.** Verified absent from this
  machine's vault file. Other machines' vault files are private and unreachable, and the risk is
  smaller than the original draft of this plan claimed: `scripts/update/verify.py::_classify_env_keys`
  computes `required_keys - present` and `optional_keys - present` from the `.env.example`
  declarations alone and never inspects for *extra* keys present in a vault but absent from
  `.env.example`. A machine that still declares the orphaned key gets no warning from `/update`'s
  env-completeness surface — the key simply reads to nothing. Harmless in practice, but the
  env-completeness check is not the safety net that was claimed here.
- **Do NOT invert `_resolve_overlay_path`'s private-first precedence.** A fleet-wide persona-loading
  change riding inside a flag-removal chore. Anti-criterion below asserts `agent/sdk_client.py`'s
  resolver is untouched.
- **Do NOT change `SHORT_OUTPUT_THRESHOLD`'s value or its single definition.** D1 is the decision to
  keep it. Anti-criterion below asserts the constant is unmodified.
- **Do NOT remove or weaken Path B's caller-type gate.** It protects humans from having deliberate
  manual sends judged, which is a different concern from a rollout flag. Anti-criterion below asserts
  `_should_run_rtr` still exists and both CLI flags still parse.
- **Do NOT rewrite `docs/archive/**` or `docs/plans/done/**`.** They record decisions as taken.
  Anti-criterion below asserts the archive is untouched by this PR.
- **Do NOT expand the teammate overlay beyond three added lines.** Anti-criterion below bounds the
  diff.
- **[SEPARATE-SLUG #2866] Migrating other module-scope env reads.** `module-scope-env-reads-migration.md`
  owns that sweep. RTR's flag was a per-call read, so deleting it removes a candidate rather than
  doing that plan's work.

## Update System

Two changes are required, both small.

- **`scripts/update/persona_drift.py`** — generalize `check_pm_persona_drift` (or add a sibling
  entry point) so `/update` Step 4.10 checks the `teammate` template/overlay pair alongside
  `engineer`. The module already takes `template_rel` and `overlay_path` parameters and returns a
  warning list without mutating anything, so this is a loop over a pair list. `scripts/update/run.py:2185`
  is the single call site and must surface teammate warnings the same way it surfaces engineer ones.
  This is the fleet-wide mechanism for D2: machines still holding a shadowing `teammate.md` learn
  about it at update time instead of silently loading a stub.
- **`.env.example`** — removing `READ_THE_ROOM_ENABLED=false` and its five-line comment block changes
  what the env-completeness check enumerates. No machine needs to *add* anything; machines that
  happen to declare the key in their vault `.env` will simply carry a key nothing reads. Verify
  `tests/unit/test_env_completeness.py` stays green after the removal.

No migration is required — no Popoto model changes, no new config files, no new dependencies. No
`scripts/remote-update.sh` change.

## Agent Integration

No new CLI entry point and no new bridge import. `bridge/read_the_room.py` is already called from
both surfaces: `agent/output_handler.py::TelegramRelayOutputHandler.send` (Path A, direct import) and
`tools/valor_telegram.py::cmd_send` (Path B, via the existing `valor-telegram` entry point in
`pyproject.toml [project.scripts]`). This change removes a condition from code both surfaces already
reach.

Two agent-visible consequences are worth naming:

- **The teammate overlay is agent-facing by definition.** The three added lines change how the
  teammate persona describes its own capability. Verification that the change actually loads is the
  point of D2 — an integration check should assert `_resolve_overlay_path("teammate")` resolves to
  `config/personas/teammate.md` after the private stub is deleted.
- **Path B's caller-type gate is the agent-vs-human discriminator and stays intact.** Agent-invoked
  `valor-telegram send` (with `VALOR_SESSION_ID` set) now runs RTR unconditionally; a human shell
  invocation still skips it; `--read-the-room` and `--no-read-the-room` still override per call.

## Documentation

### Feature Documentation

- [x] `docs/features/read-the-room.md` — DELETE the "Rollout" section (`:247`) entirely.
- [x] `docs/features/read-the-room.md` — DELETE the `READ_THE_ROOM_ENABLED` flag paragraph and the
      flip criterion from "Enablement decision" (`:105-112`). **PRESERVE** the section's first half:
      the group-vs-DM rationale and the `is_group_chat` sharing note with `bridge/poll_gating.py`
      (#2701) is live architecture, not rollout scaffolding. Rename the heading to describe what
      remains (e.g. "Group chats only").
- [x] `docs/features/read-the-room.md:117` — DELETE the `READ_THE_ROOM_ENABLED` bullet from the
      bypass-conditions list.
- [x] `docs/features/read-the-room.md:126` — REWRITE the SDLC bypass bullet to cite `session.is_sdlc`
      and drop any implication that the bullet described working behavior before this change.
- [x] `docs/features/read-the-room.md:183/:189/:210/:211/:214` — REWRITE the Coverage section and the
      opt-in/opt-out matrix so the columns describe the caller-type gate and the CLI flags only.
- [x] `docs/features/read-the-room.md:263` — REWRITE the Adjacent-layers claim that "RTR's SDLC bypass
      means RTR is effectively a no-op for SDLC sessions today". After the repair it is true; today it
      is not. State it as current fact, not as a today-qualified aside.
- [x] `docs/features/read-the-room.md` — ADD the D1 paragraph recording the short-output threshold as
      a stated decision, naming `CONTEXT_RECALL_PREFILTER_MAX_CHARS` and why the two bounds do not
      conflict.
- [x] `docs/features/read-the-room.md` — ADD an operations paragraph replacing the deleted Rollout:
      which `rtr.*` events to watch, and that recovery is a revert rather than a flag flip.
- [x] `docs/features/drafter-redundancy-suppression.md:157` — REWRITE: the "structurally present but
      currently a no-op" sentence is false after the repair.
- [x] `docs/features/bridge-worker-architecture.md:52` — UPDATE the Path A flow line; drop
      "opt-in via READ_THE_ROOM_ENABLED".
- [x] `docs/features/agent-message-delivery.md:114` — UPDATE; drop the flag parenthetical.
- [x] `docs/features/telegram-messaging.md:270` — UPDATE; drop "subject to the
      `READ_THE_ROOM_ENABLED` machine-wide gate".
- [x] `docs/features/README.md:166` — UPDATE the index row; drop "opt-in via `READ_THE_ROOM_ENABLED`".
- [x] `docs/features/context-recall-advisory.md:111` and `:134` — UPDATE: `:111` cites
      `READ_THE_ROOM_ENABLED` as a no-`config/settings.py` precedent (the precedent survives, the
      example does not); `:134` lists "enabling `READ_THE_ROOM_ENABLED`" as a future change that no
      longer exists.
- [x] `docs/features/personas.md` — ADD a note that `teammate.md` is repo-maintained and that a
      private copy shadows it, pointing at the `/update` drift check.

### Inline Documentation

- [x] `bridge/read_the_room.py:36` — remove the `READ_THE_ROOM_ENABLED` bullet from the module
      docstring's public surface list.
- [x] `bridge/read_the_room.py:481` — remove the flag bullet from `read_the_room`'s short-circuit list.
- [x] `bridge/read_the_room.py:489` — rewrite the `session.sdlc_slug` bullet as `session.is_sdlc`.
- [x] `tools/valor_telegram.py:742`, `:903`, `:1453` — rewrite so the caller-type gate and the CLI
      flags are the only conditions described.
- [x] `agent/output_handler.py:1046` — rewrite the RTR comment block; it currently says "gated by the
      `READ_THE_ROOM_ENABLED` env var (default off)".
- [x] `agent/session_executor.py:2206` — rewrite the `_should_run_rtr` note; drop "also gated by
      `READ_THE_ROOM_ENABLED`".

### External Documentation Site

Not applicable — this repo has no external docs site.

## Success Criteria

- [x] `READ_THE_ROOM_ENABLED` appears nowhere in `bridge/`, `agent/`, `tools/`, `tests/`, `config/`,
      `scripts/`, `.env.example`, or `docs/features/`.
- [x] `sdlc_slug` appears nowhere in `bridge/`, `agent/`, `tools/`, `tests/`, or `docs/features/`.
- [x] Both call sites read `getattr(session, "is_sdlc", False)`.
- [x] RTR runs on an eligible Path A send with no RTR-related env var set anywhere in the process.
- [x] Every structural bypass still bypasses, each with a test that runs without env setup: DM /
      unclassifiable `chat_id`, `chat_id is None`, empty/whitespace draft, `len < SHORT_OUTPUT_THRESHOLD`,
      SDLC session, empty snapshot, deterministic stale-trigger suppress.
- [x] The SDLC-bypass test fails against the unrepaired code (red-state proof pasted into the PR).
- [x] Path B's caller-type gate is intact: a human shell invocation without `VALOR_SESSION_ID` skips
      RTR; `--read-the-room` and `--no-read-the-room` both still override.
- [x] No test sets or unsets `READ_THE_ROOM_ENABLED`; the flag-off cases are deleted, not adapted.
- [x] `docs/features/read-the-room.md` has no "Rollout" section, no flip criterion, and no flag
      paragraph — while retaining the `is_group_chat` / poll-gating rationale.
- [x] `~/Desktop/Valor/personas/teammate.md` does not exist, and
      `_resolve_overlay_path("teammate")` resolves to `config/personas/teammate.md`.
- [x] `config/personas/teammate.md`'s net line growth is bounded (`insertions - deletions <= 3`).
      The literal "gains at most 3 lines and deletes none" no longer holds: the critique's own
      concern row (`:888`) required rewording the tooling-heavy bullets the nudge's first line
      contradicts, which are edits to existing lines, not pure additions. Measured on the shipped
      diff: 6 insertions / 16 deletions, net -10, well inside the relaxed bound.
- [x] `/update` warns when a private `teammate.md` **drifts from** the repo copy (the mechanism is
      diff-based, not existence-based — a byte-identical shadow produces no warning).
- [x] `docs/archive/**` and `docs/plans/done/**` are untouched by the PR.
- [x] Tests pass (`/do-test`)
- [x] Documentation updated (`/do-docs`)

## Team Orchestration

Small appetite, tightly coupled edits across one module boundary. A single builder owns the code and
tests so the mutation proof and the repair stay in one head; a documentarian takes the doc sweep in
parallel once the code shape is fixed; a validator closes.

### Team Members

- **Builder (rtr-core)**
  - Name: `rtr-core-builder`
  - Role: flag removal, SDLC bypass repair at both sites, and every test change including the
    red-state mutation proof
  - Agent Type: builder
  - Resume: true

- **Builder (overlay)**
  - Name: `overlay-builder`
  - Role: private stub deletion, `scripts/update/persona_drift.py` generalization plus its tests, and
    the three-line teammate overlay edit
  - Agent Type: builder
  - Resume: true

- **Documentarian (docs)**
  - Name: `rtr-documentarian`
  - Role: the live-docs sweep, the read-the-room.md surgery, and the D1 / operations paragraphs
  - Agent Type: documentarian
  - Resume: true

- **Validator (all)**
  - Name: `rtr-validator`
  - Role: verify every Success Criterion and every anti-criterion; confirm the red-state proof exists
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Remove the flag and repair the SDLC bypass

- **Task ID**: build-rtr-core
- **Depends On**: none
- **Validates**: `tests/unit/test_read_the_room.py`, `tests/unit/valor_telegram/test_valor_telegram_rtr.py`,
  `tests/unit/output_handler/test_output_handler_filters.py`,
  `tests/unit/output_handler/test_output_handler_transport.py`,
  `tests/unit/output_handler/test_output_handler_drafter.py`,
  `tests/integration/test_message_drafter_integration.py`, `tests/unit/test_env_completeness.py`
- **Assigned To**: rtr-core-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `_read_enabled()` (`bridge/read_the_room.py:112-123`) and its call site (`:517-518`, the
  first entry under the `# ── Short-circuits ──` banner at `:516`).
  Remove the flag bullets from the module docstring (`:36`) and the function docstring (`:481`).
- Replace `getattr(session, "sdlc_slug", None)` with `getattr(session, "is_sdlc", False)` at
  `bridge/read_the_room.py:533` and `bridge/message_drafter.py:1147`. Rewrite the docstring bullet at
  `bridge/read_the_room.py:489` to name `session.is_sdlc`.
- Delete `READ_THE_ROOM_ENABLED=false` and its comment block from `.env.example` (`:273-279`).
- Rewrite the flag mentions in `tools/valor_telegram.py:742/:903/:1453`, `agent/output_handler.py:1046`,
  and `agent/session_executor.py:2206` so only the caller-type gate and the CLI flags are described.
- **Red-state proof, before the repair lands:** rewrite
  `test_sdlc_session_short_circuits_with_event` to build a session exposing `is_sdlc=True`, run it
  against the unrepaired `read_the_room.py`, and capture the failure output for the PR. Repeat for a
  new short-SDLC-reply drafter test against the unrepaired `message_drafter.py`.
- Apply every Test Impact disposition: `FakeSession` gains `is_sdlc`; `_enable_rtr` / `_disable_rtr`
  are deleted with all call sites; the 13 setenv sites in the Path B RTR tests and the raw
  `os.environ` blocks in the output-handler tests are removed;
  `test_output_handler_filters.py:342-372` is deleted; every `sdlc_slug` assignment on a fake or mock
  becomes `is_sdlc`.
- Add the bypass-coverage tests named in Failure Path Test Strategy, each running with no RTR env var
  set: empty draft, `chat_id is None`, DM, short output, SDLC session, `session=None`, empty snapshot,
  stale trigger.
- Add the Risk 2 pair: a short SDLC reply takes the full composition path; a short non-SDLC reply
  still takes the short path.
- Run `scripts/pytest-clean.sh` over the affected files.

### 2. Resolve overlay precedence and add the register nudge

- **Task ID**: build-overlay
- **Depends On**: none
- **Validates**: `tests/unit/test_update_persona_drift.py`
- **Assigned To**: overlay-builder
- **Agent Type**: builder
- **Parallel**: true
- Transcribe the current contents of `~/Desktop/Valor/personas/teammate.md` into the PR description
  (9 lines: a Communication Style paragraph and a Guidelines paragraph), then delete the file.
  Confirm `_resolve_overlay_path("teammate")` now returns `config/personas/teammate.md`.
- Generalize `scripts/update/persona_drift.py` to check the `teammate` template/overlay pair as well
  as `engineer`, preserving the never-raise contract and the empty-list-when-absent behavior. Wire the
  teammate warnings through the existing `scripts/update/run.py:2185` call site.
- Extend `tests/unit/test_update_persona_drift.py` with teammate cases, including absent-overlay
  (no warning), drifted-overlay (one warning), and unreadable-template (warning, no raise).
- Add exactly three bullets to the "How I Interact" list in `config/personas/teammate.md`: outcomes
  over internal tooling; name the specific missing context rather than asking open-ended; reply in
  first person to third-person mentions. Delete nothing.

### 3. Documentation sweep

- **Task ID**: document-feature
- **Depends On**: build-rtr-core, build-overlay
- **Assigned To**: rtr-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute every checkbox in the Documentation section above.
- On `docs/features/read-the-room.md`: delete "Rollout" outright; split "Enablement decision" so the
  `is_group_chat` / poll-gating rationale survives under an accurate heading and the flag paragraph
  plus flip criterion are gone; remove the flag bullet from the bypass list; rewrite the SDLC bullet
  to `session.is_sdlc`; rewrite the coverage matrix; correct the adjacent-layers no-op claim; add the
  D1 threshold paragraph and the operations paragraph.
- Correct `docs/features/drafter-redundancy-suppression.md:157`.
- Touch nothing under `docs/archive/**` or `docs/plans/done/**`.

### 4. Final validation

- **Task ID**: validate-all
- **Depends On**: build-rtr-core, build-overlay, document-feature
- **Assigned To**: rtr-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every command in the Verification table and report each result.
- Confirm the red-state proof for the SDLC-bypass test is present in the PR description.
- Confirm every Success Criterion checkbox.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Flag gone from live code, tests, config, and live docs | `grep -rn "READ_THE_ROOM_ENABLED" bridge/ agent/ tools/ tests/ config/ scripts/ docs/features/ .env.example` | exit code 1 |
| `sdlc_slug` gone from live code, tests, and live docs | `grep -rn "sdlc_slug" bridge/ agent/ tools/ tests/ docs/features/` | exit code 1 |
| Both call sites read `is_sdlc` | `grep -c 'getattr(session, "is_sdlc", False)' bridge/read_the_room.py bridge/message_drafter.py \| grep -c ":1$"` | output contains 2 |
| `_read_enabled` removed entirely | `grep -rn "_read_enabled" bridge/ tests/` | exit code 1 |
| RTR unit tests pass | `./scripts/pytest-clean.sh tests/unit/test_read_the_room.py -q` | exit code 0 |
| Path B RTR tests pass | `./scripts/pytest-clean.sh tests/unit/valor_telegram/test_valor_telegram_rtr.py -q` | exit code 0 |
| Output-handler tests pass | `./scripts/pytest-clean.sh tests/unit/output_handler/ -q` | exit code 0 |
| Drafter integration tests pass | `./scripts/pytest-clean.sh tests/integration/test_message_drafter_integration.py -q` | exit code 0 |
| Persona drift tests pass | `./scripts/pytest-clean.sh tests/unit/test_update_persona_drift.py -q` | exit code 0 |
| Env completeness still green | `./scripts/pytest-clean.sh tests/unit/test_env_completeness.py -q` | exit code 0 |
| Private teammate stub deleted | `test ! -e "$HOME/Desktop/Valor/personas/teammate.md"` | exit code 0 |
| Repo teammate overlay is the one that loads | `./.venv/bin/python -c "from agent.sdk_client import _resolve_overlay_path; p=_resolve_overlay_path('teammate'); assert 'config/personas' in str(p), p; print('ok')"` | output contains ok |
| `/update` drift check covers teammate — wiring, not just presence | `grep -c "check_all_persona_drift" scripts/update/run.py` | output > 0 |
| Rollout section deleted | `grep -c "^## Rollout" docs/features/read-the-room.md` | match count == 0 |
| Flip criterion deleted | `grep -ci "flip criterion" docs/features/read-the-room.md` | match count == 0 |
| `is_group_chat` rationale preserved | `grep -c "is_group_chat" docs/features/read-the-room.md` | output > 0 |
| D1 decision recorded in docs | `grep -c "CONTEXT_RECALL_PREFILTER_MAX_CHARS" docs/features/read-the-room.md` | output > 0 |
| Lint clean | `./.venv/bin/python -m ruff check .` | exit code 0 |
| Format clean | `./.venv/bin/python -m ruff format --check .` | exit code 0 |
| ANTI: overlay resolver precedence untouched | `git diff origin/main -- agent/sdk_client.py \| grep -c "PERSONAS_OVERLAY_DIR\|PERSONAS_BASE_DIR"` | match count == 0 |
| ANTI: `SHORT_OUTPUT_THRESHOLD` value unchanged | `git diff origin/main -- bridge/message_drafter.py \| grep -c "SHORT_OUTPUT_THRESHOLD ="` | match count == 0 |
| ANTI: Path B caller-type gate intact | `grep -c "_should_run_rtr" tools/valor_telegram.py` | output > 0 |
| ANTI: both CLI flags still parse | `./.venv/bin/python -c "import subprocess,sys; h=subprocess.run([sys.executable,'-m','tools.valor_telegram','send','--help'],capture_output=True,text=True).stdout; assert '--read-the-room' in h and '--no-read-the-room' in h; print('ok')"` | output contains ok |
| ANTI: archived plans untouched | `git diff --name-only origin/main -- docs/archive/ docs/plans/done/ \| wc -l` | output contains 0 |
| ANTI: teammate overlay net growth bounded (relaxed per critique `:888`) | `git diff --numstat origin/main -- config/personas/teammate.md \| ./.venv/bin/python -c "import sys; r=sys.stdin.read().split(); a,d=(int(r[0]),int(r[1])) if r else (0,0); assert a - d <= 3, (a, d); print('ok')"` | output contains ok |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — 0 blockers, 8 concerns, 3 nits.
**Depth:** FULL (force-FULL: the plan edits `config/personas/`, a doctrine path).
**Mode:** independent roster (3 critics) — Risk & Robustness, Scope & Value, History & Consistency.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness + structural check | `_read_enabled` survives the blast-radius sweep in three sites the Documentation section never lists, one of them a live feature doc. `bridge/context_recall.py:136` and `bridge/promise_gate.py:362` cite `bridge/read_the_room.py::_read_enabled` by name in their own docstrings as a design precedent, and `docs/features/promise-gate.md:238` states "RTR's default is `false`, so an empty-string env var matches its default-off state invisibly" — a claim about a deleted symbol and a deleted default. The plan's own removal check `grep -rn "_read_enabled" bridge/ tests/` (expected exit 1) therefore still matches after a fully correct removal, so the validator's gate fires on correct work. | **Resolved** — build. `grep -rn "_read_enabled" bridge/ tests/ docs/features/` exits 1; `bridge/context_recall.py`, `bridge/promise_gate.py`, and `docs/features/promise-gate.md:238` were all rewritten to drop the precedent reference. | The sweep used only `READ_THE_ROOM_ENABLED` and `sdlc_slug` as search terms; `_read_enabled` is a third term and must be swept on the same live-vs-archive partition. Add all three sites to the Inline/Feature Documentation checklists (reword the precedent references so they no longer name a deleted private symbol), then either keep the check as `grep -rn "_read_enabled" bridge/ tests/ docs/features/` or narrow it to `bridge/read_the_room.py tests/`, which is the only file the function can live in. `docs/features/promise-gate.md:238` needs its own rewrite: after this change RTR has no env default to be stricter than, so the contrast the paragraph draws no longer exists. |
| CONCERN | structural check | Decision D2's stub deletion activates 57 lines of internal-tooling narration on the same machine, and in the same commit, as the register nudge that exists to suppress exactly that. The issue's register complaint was that the agent narrated "skill names, the SDLC stage model, a `valor-session create --role eng` invocation". The repo overlay that becomes live carries `config/personas/teammate.md:26` "Creating GitHub issues via /do-issue", `:33` "route to proper SDLC pipeline", `:38` "restricted to the developer and project-manager personas", and `:40-48` a fenced bash block of `python -m tools.job_scheduler` commands. D2's Risk 4 names the 9-to-57-line jump as a behavior shift but does not notice that the content arriving contradicts the bullets being added. | **Resolved** — build reworded `:26-28` to "Filing, viewing, commenting on, labeling, and updating GitHub issues and PRs" and collapsed the Job Scheduler bash block to one prose sentence (see diff, `config/personas/teammate.md`: 6 insertions / 16 deletions, net -10). The anti-criterion relaxation this note calls for was not made until this DOCS patch (2026-09-05) — see the Verification-table row above. | Do not expand the overlay (the No-Go holds). The cheap resolution is to fold the fix into the three bullets' own placement: the nudge's first bullet ("capability described in outcomes, never in internal tooling") is directly falsified by `:26-28` and `:40-50` of the file it is being appended to. Either reword `:26-28` from tool names to outcomes ("Filing and updating issues and PRs" rather than "Creating GitHub issues via /do-issue") and collapse the Job Scheduler bash block to one prose sentence — both are edits to existing lines, so the `a<=3 and d==0` anti-criterion at the Verification table must be relaxed to permit modified lines while still bounding net growth — or state explicitly in the plan that the contradiction is accepted and why. Silently shipping both is the one option that should not survive critique. |
| CONCERN | structural check (partly corroborated by Risk & Robustness) | The Failure Path Test Strategy item for `bridge/message_drafter.py:1147` rests on two claims that are false as written. It says "this call site is **not** inside a `try`" — but `draft_message` is invoked at `agent/output_handler.py:754` inside a `try:` opened at `:751` whose `except Exception` at `:885` logs "Drafter failure MUST NOT block delivery. Fall back to raw text". It then prescribes asserting that a session "whose `is_sdlc` property raises" behaves the same as "a session without the attribute" — true only for `AttributeError`. Verified empirically on the pinned interpreter: `getattr(obj, "is_sdlc", False)` returns `False` when the property raises `AttributeError` (silently masked) and **propagates** any other exception. A builder writing the prescribed test would assert behavior that does not hold. | **Resolved** — DOCS patch (2026-09-05). The Failure Path Test Strategy item was reworded to state the call site is inside a `try` (`:751`/`:885`), split the `AttributeError`-vs-other-exception cases, and correct the "plain field reads" description to name the two properties involved. | Split the test into the two cases the semantics actually have: a property raising `AttributeError` returns the `False` default (assert `getattr(s, "is_sdlc", False) is False`), and a property raising `RuntimeError` propagates out of `draft_message` and is caught by `agent/output_handler.py:885`, delivering raw text. Assert the second at the output-handler boundary, not at `message_drafter.py:1147`. Also correct the plan's "both plain field reads" description of `is_sdlc`: `models/agent_session.py:2271` reads `_get_stage_states_dict()` (`:1779`, which swallows `JSONDecodeError`/`TypeError`) and the `classification_type` **property** (`:1579`, which reads `extra_context`), so the chain is two properties deep, not two field reads. |
| CONCERN | Risk & Robustness | The RTR short-circuit ladder evaluates `len(draft_text) < SHORT_OUTPUT_THRESHOLD` at `bridge/read_the_room.py:530-531` **before** the SDLC bypass at `:533`. Any SDLC `delivery_text` under 200 chars returns `send`/`short_output` and never reaches the bypass branch, so no `rtr.bypassed` event is appended. Risk 1 leans on `rtr.bypassed` as load-bearing production evidence that the repaired bypass fires, but the only test exercising that event uses `_long_draft()`, so nothing establishes how much real SDLC traffic reaches the branch at all. | **Resolved** — build. `docs/features/read-the-room.md` carries the undercount note in Observability ("`rtr.bypassed` **undercounts** real SDLC bypasses..."), restates it in the Risk 1-equivalent cost section, and again in the Operations paragraph ("remembering it undercounts..."). | Both branches return `send`, so there is no behavior regression — the gap is purely observability. Add a Test Impact case for an SDLC session with a sub-200-char `delivery_text` asserting `action="send"`, `reason="short_output"`, and **no** `rtr.bypassed` event, so the ordering is documented rather than assumed. Add one sentence to the Risk 1 mitigation and to the new `docs/features/read-the-room.md` operations paragraph: `rtr.bypassed` undercounts SDLC bypasses whenever the composed message is short, so a low count is not evidence the repair failed. |
| CONCERN | History & Consistency | Decision D3 names the root-cause pattern — `getattr(obj, name, default)` turns a rename into a silent policy change — and then ships a repair keeping the identical shape at `bridge/read_the_room.py:533` and `bridge/message_drafter.py:1147`. This is safe only because `is_sdlc` genuinely exists at `models/agent_session.py:2271`; a future rename degrades both sites silently back to "no bypass", which is the exact failure this plan spent two sections diagnosing. | **Resolved** — build. `tests/unit/test_read_the_room.py` adds `assert hasattr(AgentSession, "is_sdlc")` against the real imported model. | Keep the `getattr` form (it is correct for the test-fake reason D3 gives) and add the canary the codebase already uses one file over: `tests/unit/test_update_persona_drift.py:145` (`test_default_template_path_points_at_real_file`) is this pattern applied to a path. Add the attribute equivalent to `tests/unit/test_read_the_room.py` — `assert hasattr(AgentSession, "is_sdlc")` against the real imported model, not a fake — so a rename fails loud. This is the single test that would have caught the original 2026-04 defect. |
| CONCERN | History & Consistency | `tests/unit/test_read_the_room.py::test_disabled_flag_returns_send` (line 234, the sole call site of `_disable_rtr` at `:235`) asserts `reason == "rtr_disabled"`, a state this change deletes, yet Test Impact never names it. The plan explicitly names and DELETEs its structural twin at `test_output_handler_filters.py:342-372`, so the asymmetry reads as an oversight; the generic "`_enable_rtr` / `_disable_rtr` — DELETE ... remove every call site" can be read as "strip the helper call" rather than "delete the enclosing test". | **Resolved** — build deleted `test_disabled_flag_returns_send` along with `_enable_rtr`/`_disable_rtr` and every call site; confirmed absent from `tests/unit/test_read_the_room.py`. | Add an explicit Test Impact line: `tests/unit/test_read_the_room.py::test_disabled_flag_returns_send` — DELETE, matching the treatment given its `test_output_handler_filters.py` twin. Failure direction is loud (deleting the helper alone leaves a `NameError` on `_disable_rtr`), so this is a disposition-table completeness fix rather than a latent bug. |
| CONCERN | Scope & Value | The teammate nudge ships three bullets, but the issue's Scope list enumerates two topics (outcomes-not-tooling, name-the-specific-gap) and its Acceptance Criteria repeats those same two ("covering outcome-framing and specific context gaps"). The third bullet — third-person mentions still get a first-person reply — appears only in the issue's narrative prose. The issue also says "Resist expanding this into a persona rewrite" twice, so a silent third topic is the exact drift that instruction guards. | **Decision: kept, not a deferral.** The third bullet shipped. The issue's own Step 2 task text names all three topics; the drift this row flags is between the issue's Scope/Acceptance-Criteria summary and its own narrative prose, not between the plan and the build. Reviewed and endorsed at REVIEW (2026-09-05): one line, same register as the other two, cutting it now would need a follow-up issue to restore it. | Either cut the third bullet from the Technical Approach list and from Step 2's task text, keeping the nudge to the two topics the Scope list and Acceptance Criteria name, or promote it to a fourth explicitly-flagged reviewer-overturnable call in Open Questions alongside D1 and D2. Note the `a<=3` anti-criterion permits either count, so the anti-criterion does not settle this and cannot be cited as approval. |
| CONCERN | Scope & Value | Appetite claims `Size: Small`, `Team: Solo dev`, and "The coding is an afternoon", while the plan's own body specs a four-agent Team Orchestration, 8 live doc files, 8 test files with disposition changes, a fleet-wide `/update` script generalization plus new tests, and a 26-row Verification table with 6 anti-criteria. The mismatch mis-calibrates the single declared review round. | pending | If the scope stands, change `Team:` from "Solo dev" to the two-builder plus documentarian plus validator structure the plan already declares, and drop "an afternoon". If the appetite stands, the `scripts/update/persona_drift.py` generalization is the cleanest piece to defer — it is the only item in the plan that neither the issue body nor the owner's comment asks for, and deferring it does not weaken the core ask. Do not do both. |
| NIT | History & Consistency | The Verification row `/update drift check covers teammate` is `grep -c "teammate" scripts/update/persona_drift.py` expecting `> 0`, which proves only that the string appears in the file — a comment mentioning teammate passes it. | **Resolved** — DOCS patch (2026-09-05). The Verification-table row now greps for `check_all_persona_drift` in `scripts/update/run.py` (the actual wiring call site, `:2187`), not a bare string match against `persona_drift.py`. Backed by a real test: `tests/unit/test_update_persona_drift.py::TestPersonaDriftWiring` extracts the exact `run_update` statement by AST and asserts it calls `check_all_persona_drift`, not `check_pm_persona_drift`/`check_persona_drift` — mutation-verified against reverting the wiring line. | Tighten to something behavior-adjacent (confirm the call-site wiring at `scripts/update/run.py:2185`) or drop the row and cite the planned `tests/unit/test_update_persona_drift.py` teammate cases as the stated proof. |
| NIT | structural check | The Success Criterion "`/update` warns when a private `teammate.md` shadows the repo copy" does not match the mechanism D2 names. `scripts/update/persona_drift.py` returns an empty list when either file is absent and warns only on a **diff**, so a byte-identical private shadow produces no warning at all. Its warning string is also hardcoded `"PM persona overlay drift: ..."`, which would misreport a teammate finding. | **Resolved** — the Success Criterion above now reads "drifts from"; `docs/features/personas.md`, `remote-update.md`, and `harness-abstraction.md` all already used the honest diff-based wording. The hardcoded `"PM"` label prefix is fixed under Nit 1 below (`check_persona_drift`'s `persona_name` default). | Either reword the Success Criterion to "warns when a private `teammate.md` **drifts from** the repo copy" (honest about the diff-based mechanism, and sufficient in practice since this change edits the repo copy and thereby creates drift on every shadowing machine), or parameterize the check to warn on mere existence for teammate. Whichever is chosen, parameterize the warning prefix — the current literal is emitted from `persona_drift.py:82`. |
| NIT | structural check | Six Verification rows are phrased "match count == 0" and are satisfied by a bare `grep -c`, which prints `0` and **exits 1** (confirmed empirically on this machine). The table's wording is internally consistent, but Step 4 instructs the validator to "run every command in the Verification table and report each result", and a validator gating on exit status reads six correct results as failures. | pending | Wrap those rows as `test "$(grep -c PATTERN FILE)" -eq 0` so success is exit 0, or add one line above the table stating that rows phrased "match count" are read from stdout and rows phrased "exit code" from `$?`. |

**Independent convergence:** none of the three critics duplicated a finding, so no severity was elevated by agreement. The `_read_enabled` gap was reached twice from different directions (Risk & Robustness via the code docstrings, the structural check via the live feature doc) and is merged into one row.

---

## Open Questions

None blocking. The issue body and the owner's scope-correction comment together settle every question
this plan had to answer, and the three decisions the comment demanded (D1 short-output threshold, D2
overlay precedence, D3 bypass repair and Scope reframing) are made and reasoned above rather than
deferred to a human.

Two items are stated as calls the reviewer may want to overturn, and are flagged here so a
disagreement surfaces at critique rather than at merge:

1. **D1 keeps the 200-char bypass.** The alternative — inspecting short drafts too — is defensible if
   the reviewer weighs social-misjudgment risk in the short band above per-send latency. The plan's
   position is that the band already carries two cheaper guards and that RTR has produced no data yet
   to tune against.
2. **D2 deletes the private stub rather than syncing it.** The alternative is keeping a private
   teammate overlay per machine. The plan's position is that #1692 moved the file into the repo
   precisely to stop that, and Risk 4 names the behavior shift this causes on this machine.
