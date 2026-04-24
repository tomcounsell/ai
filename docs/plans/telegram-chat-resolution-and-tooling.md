---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-04-24
revised: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1163
last_comment_id:
---

# Telegram Chat Resolution & Tooling Hardening

## Problem

During a live session on 2026-04-24, the agent was asked to summarize recent activity in "PM: PsyOptimal." It ran `valor-telegram read --chat "PsyOptimal"`, received messages dated 2 days old, and confidently reported a natural pause. The user had to correct it — fresher messages were today, in a *different* chat that the matcher silently ignored. Recovery required `grep` on `logs/bridge.log`.

Issue [#1163](https://github.com/tomcounsell/ai/issues/1163) enumerates 8 related defects rooted in the same mismatch: **tooling assumes a unique exact chat identity per query, but the real data has overlapping short names across group chats and their PM sidebars, plus stale caches masquerading as fresh results.** Silent wrong-matches defeat the persona rule shipped by [#1065](https://github.com/tomcounsell/ai/issues/1065) (search Telegram history before asking in group chats) — that rule is only as reliable as the tool beneath it.

**Current behavior:**
- `resolve_chat_id` ([`tools/telegram_history/__init__.py:940-979`](../../tools/telegram_history/__init__.py)) runs a 3-stage cascade (exact → case-insensitive exact → substring-contains) and returns the **first hit**, with iteration order over `Chat.query.all()` unspecified. No ambiguity signal.
- Matching is simultaneously too loose (silent substring wrong-match) and too strict (a missing colon in `"PM PsyOptimal"` vs. stored `"PM: PsyOptimal"` breaks all three stages).
- CLI output shows no freshness signal — the reader can't tell whether messages are current.
- Three entry points exist — `valor-telegram`, `scripts/get-telegram-message-history`, and the `telegram` skill — with different identity spaces and no decision tree. The script is an orphan from a half-finished consolidation.
- No `chats --search` discovery affordance; no "did you mean" on resolution failure.
- No cross-chat project rollup — a project's group chat + PM sidebar are read separately with manual merging.

**Desired outcome:** A reader querying Telegram history gets the right chat by default via recency-ranked picking, with a stderr warning listing all candidates so a silent wrong-match becomes visible. Read output carries an activity/freshness marker. One blessed CLI entry point (`valor-telegram`) handles groups, DMs (`--user`), and cross-chat project rollups (`--project`); the orphan script and half-finished skill consolidation are cleaned up. Matching rules are coherent and punctuation-tolerant, applied symmetrically at write and query sides.

## Freshness Check

**Baseline commit:** `58c3bfee` (2026-04-24 plan time, post-#1158 merge)
**Issue filed at:** 2026-04-24T10:04:29Z (same day as planning)
**Disposition:** Minor drift — one test-path reference in the issue body and routing input is imprecise.

File:line references re-verified:
- `tools/telegram_history/__init__.py:940-979` (`resolve_chat_id`) — **still holds** exactly; 3-stage cascade confirmed.
- `models/chat.py` — **still holds**; `chat_id` (UniqueKeyField), `chat_name` (KeyField), `chat_type` (KeyField nullable), `project_key` (Field nullable — intentionally NOT a KeyField to avoid delete-and-recreate per line 22 comment), `updated_at` (SortedField). This is relevant for the new `chat_name_normalized` field decision below.
- `bridge/telegram_bridge.py:899` (`register_chat` invocation) — **still holds**. `register_chat` itself is defined at `tools/telegram_history/__init__.py:830`.
- `tools/valor_telegram.py` — confirmed structure: `cmd_read` at line 181, `cmd_send` at line 316, `cmd_chats` at line 408, argparse wiring in `main` at line 444.
- `scripts/get-telegram-message-history` — 211 lines, DM-only via `resolve_username` against `projects.json`, writes `data/message_query_request.json`. Confirmed orphan.
- `.claude/skills/telegram/SKILL.md` — exists (98 lines); does NOT mention `scripts/get-telegram-message-history`. The "prior `searching-message-history` and `get-telegram-messages` skills" referenced in the issue are confirmed absent from `.claude/skills/`.

**Test-path drift correction:** the issue body and do-plan input both name `tests/unit/test_telegram_history.py`. The actual canonical location is **`tests/tools/test_telegram_history.py`** (547+ lines, covers `TestRegisterChat`, `TestResolveChatId`, `TestListChats`, `TestSearchAllChats`). `tests/unit/test_telegram_history.py` does NOT exist. `tests/unit/test_valor_telegram.py` DOES exist and mocks `resolve_chat_id` — both files get updated.

**Sibling issues re-checked:**
- [#1067](https://github.com/tomcounsell/ai/issues/1067) — closed 2026-04-21, shipped `valor-email` CLI; precedent for folding DM-only scripts into unified CLIs.
- [#1158](https://github.com/tomcounsell/ai/issues/1158) — closed 2026-04-24 (today), enforced immutable project→repo pairing; makes `Chat.project_key` a trustworthy immutable anchor and unblocks defect 7's `--project` rollup.
- [#1065](https://github.com/tomcounsell/ai/issues/1065) — closed; persona hard rule undermined by current resolver; this plan strengthens its foundation.
- [#1161](https://github.com/tomcounsell/ai/issues/1161) — open; `valor-ingest` precedent for unified CLIs (referenced in user's routing brief).
- [#1159](https://github.com/tomcounsell/ai/issues/1159) — open; "last activity: X ago" wording convention referenced in user's Q3 resolution.

**Commits on main since issue was filed (touching referenced files):** none; plan was triaged within the hour.

**Active plans in `docs/plans/` overlapping this area:** none. `unify-telegram-send.md` (merged) and `bridge-telegram-api-id-import-crash.md` (merged) are prior-art only.

**Notes:** The canonical test file path is corrected above. No premise-breaking drift. #1158 landing the same day materially strengthens this plan — see Q5 resolution in Solution.

## Prior Art

- **Issue [#1065](https://github.com/tomcounsell/ai/issues/1065)** (closed): added a persona-level hard rule that Valor must search Telegram history before asking in group chats. The rule depends on the resolver — silent wrong-matches here undermine it in practice. This plan strengthens the foundation under #1065.
- **Issue [#1067](https://github.com/tomcounsell/ai/issues/1067)** (closed): shipped `valor-email` CLI as an analog of `valor-telegram`. Structural precedent for unified CLI design — its `read --search` / `send --to` / `threads` subcommand layout and Redis-first / fallback pattern inform the `read --user` / `read --project` / `chats --search` additions here.
- **Issue [#1158](https://github.com/tomcounsell/ai/issues/1158)** (closed, 2026-04-24): made `Chat.project_key` a trustworthy immutable anchor via project→repo pairing enforcement. This is the keystone that makes Q5 (`read --project <key>`) safe to ship in this plan — without immutable project_key, a rollup could mix chats whose project association had silently drifted.
- **Issue [#1161](https://github.com/tomcounsell/ai/issues/1161)** (open): `valor-ingest` CLI precedent. Reinforces the "fold orphan scripts into a single unified CLI" pattern.
- **Issue [#949](https://github.com/tomcounsell/ai/issues/949)** (closed): reply-to thread context propagation. Different layer (context propagation) than this one (identity resolution); no direct code overlap.
- **PR [#746](https://github.com/tomcounsell/ai/pull/746)** (merged): `valor-telegram send` routed via Redis relay + `--reply-to` flag. Relevant only as context for how the CLI is structured; doesn't touch resolver.

No prior work directly attempted to fix chat-name resolution or add cross-chat project rollup. This is greenfield on those code paths.

## Research

No relevant external findings — the work is purely internal Python (Popoto ORM, existing Telethon fallback we do not change, standard library `str.casefold()` for normalization). No new libraries or APIs introduced.

## Data Flow

End-to-end flow for `valor-telegram read`, with the current break points highlighted:

**Write side** (bridge inbound message → Chat record):
1. **Entry point** — `bridge/telegram_bridge.py` receives a Telegram event.
2. **Storage** — `store_message` writes the `TelegramMessage` record.
3. **Chat registration** — `register_chat` at `tools/telegram_history/__init__.py:830` upserts the `Chat` record, setting `chat_name` and bumping `updated_at`. **New behavior:** also compute and set `chat_name_normalized` on the same upsert (foundational write-side change).

**Query side** (`valor-telegram read --chat NAME`):
1. **Entry point** — `tools/valor_telegram.py:cmd_read` (line 181).
2. **Name → chat_id resolution** — `resolve_chat()` at line 53 delegates to `tools.telegram_history.resolve_chat_id` (line 940). **Current break:** returns the first arbitrary match across 3 stages, no ambiguity signal, no recency tiebreak, no punctuation tolerance. **New behavior:** normalize the user input using the same helper as the write side, gather all `Chat` candidates whose `chat_name_normalized` matches (exact then substring), sort by `updated_at` desc, tiebreak on `chat_id` asc, return the top candidate and emit a stderr warning listing ALL candidates with last-activity ages. Defensive: if any non-chosen candidate has a strictly greater `updated_at` than the chosen one (should be impossible given the sort, but guards against bugs), upgrade the warning to a non-zero exit unconditionally. `--strict` flag turns the ordinary warning into a non-zero exit as well.
3. **DM fallback** — if `resolve_chat_id` returns None, falls back to `resolve_username` against `projects.json`. Unchanged by this plan *except* that `read --user USERNAME` now short-circuits directly to this path, folding in `scripts/get-telegram-message-history`.
4. **Project rollup** — `read --project KEY` (new path): `Chat.query.filter(project_key=KEY)` returns all chats for the key; for each, fetch recent messages; union, sort chronologically ascending, apply `--limit` after merge, annotate each line with `[Chat Name]` prefix.
5. **Message fetch** — `_fetch_messages_from_redis` reads the Redis message store for the resolved `chat_id`. **Current break:** output does not surface freshness. **New behavior:** include the `Chat.updated_at` timestamp in the CLI output header ("last activity: 2h ago").
6. **Telethon fallback** — `_fetch_from_telegram_api` at line 258 triggers when Redis returns zero messages. **New behavior:** `--fresh` flag forces this path unconditionally, bypassing the Redis cache entirely.
7. **Output** — formatted message list to stdout. **New behavior:** prepend a header line with chat_name, chat_id (for unambiguous reuse), and last-activity age.

The orphan path — `scripts/get-telegram-message-history "username" COUNT` — writes a request file to `data/message_query_request.json` and polls for a result. **New behavior:** this path is folded into `valor-telegram read --user USERNAME` and the script is deleted outright (no deprecation shim — see Rabbit Holes).

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:**
  - `models.chat.Chat` gains one new field: `chat_name_normalized = KeyField(null=True)` — indexed because the primary resolver query hits it directly. `null=True` is load-bearing: existing records have `None` until the bridge re-registers them on next inbound message.
  - `tools.telegram_history` exports a new pure helper `normalize_chat_name(s: str) -> str` used symmetrically on write side (`register_chat`) and query side (`resolve_chat_id`).
  - `resolve_chat_id(chat_name: str) -> str | None` return signature is **preserved** for back-compat with all existing callers. The new ambiguity-visibility contract lives entirely in the warning emitted to stderr as a side effect; callers that don't read stderr see the same single return value they saw before (the most recent match).
  - `valor-telegram read` gains four optional flags: `--user USERNAME`, `--project KEY`, `--strict`, `--fresh`. `--chat`, `--user`, and `--project` are mutually exclusive at the argparse level.
  - `valor-telegram chats` gains one optional flag: `--search PATTERN` (substring filter on normalized name).
- **Coupling:** slight decrease. Consolidating the orphan script removes a second identity space. Normalization at write time trades one new field for eliminating the cascade's case-insensitive/substring Python loops.
- **Data ownership:** unchanged. `Chat` model still owned by the bridge.
- **Reversibility:** high. The new field is additive and nullable. The normalization helper is pure. Ambiguity detection writes to stderr only — no caller contract changes. Removing the orphan script is the one hard-to-reverse move; mitigated by confirming zero external callers via audit (see Risks).

## Appetite

**Size:** Medium

**Team:** Solo dev (builder + validator via Task pattern), 1 documentarian, 1 code-reviewer pass.

**Interactions:**
- PM check-ins: 0 required — all 5 Solution Sketch open questions resolved before planning started (see binding user inputs in issue-tracking).
- Review rounds: 1 — single code review pass after validator confirms tests pass.

Rationale: 8 defects and one additive feature (defect 7), but all tightly coupled around one function family, one model field, one bridge callsite, and one CLI. Scope is bounded; #1158 landing today removed the last blocker for the `--project` rollup. Interface changes are additive and preserve all existing signatures.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | `Chat` model reads/writes during tests |
| Popoto importable | `python -c "from models.chat import Chat"` | Model layer available |
| Existing test baseline passes | `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q` | Confirm no unrelated breakage before starting |
| Project registry populated | `python -c "from models.chat import Chat; print(len(list(Chat.query.all())))"` | At least a handful of Chat records must exist for the normalized-backfill test to be meaningful |

No prerequisites beyond normal dev environment.

## Solution

All five open questions in the issue's Solution Sketch have pre-resolved answers (binding inputs to this plan). The sub-sections below encode those resolutions directly; they are not open.

### Key Elements

- **`normalize_chat_name(s: str) -> str`** (new): pure helper. Rules: `str.casefold()` → collapse internal whitespace runs to single space → strip the punctuation set `{_, :, -, ., ,}` (underscore, colon, hyphen, period, comma). Does NOT touch emoji or non-ASCII. Applied symmetrically on both sides of every comparison.
- **`Chat.chat_name_normalized`** (new field): `KeyField(null=True)`. Populated at write time by `register_chat`. Nullable for legacy records until they are re-registered (see Backfill Strategy).
- **Recency-ranked pick-with-warning**: when resolution produces >1 candidate, the resolver returns the candidate with the greatest `updated_at` (tiebreak on `chat_id` ascending, for determinism across Popoto iteration order), AND emits a stderr warning listing ALL candidates (chosen + non-chosen) with their `chat_id`, `chat_name`, and last-activity age, in greppable format.
- **Defensive ordering check**: if the sort returns a chosen candidate whose `updated_at` is strictly less than any other candidate's `updated_at` (this should be impossible given the sort rule, but guards against bugs in future edits), the stderr warning is upgraded to a non-zero exit code unconditionally, regardless of `--strict`.
- **`--strict` flag on `read`**: flips the stderr warning into a non-zero exit code. For scripted callers (cron, CI) who prefer hard failure over silent-picks-with-stderr.
- **`--fresh` flag on `read`**: forces a Telethon round-trip, bypassing the Redis cache entirely. For callers who explicitly doubt cache freshness.
- **`--user USERNAME` flag on `read`**: folds in the orphan script's sole feature. Routes via `resolve_username` against `projects.json` (existing code path); does NOT use `resolve_chat_id`.
- **`--project KEY` flag on `read`**: unions messages across all chats where `Chat.project_key == KEY`, sorted chronologically ascending, annotated per line with `[Chat Name]`. Mutually exclusive with `--chat` and `--user`. On zero chats matching KEY, prints known project_keys as "did you mean..." output.
- **`valor-telegram chats --search PATTERN`**: substring filter on `chat_name_normalized` (both sides normalized). Sorted by recency desc.
- **Freshness header on `read`**: one-line header before the message list: `[chat_name · chat_id=N · last activity: T ago]` using `Chat.updated_at`. Clarifies the semantics in the `--help`: "last activity = last inbound message timestamp; not a last-confirmed-sync signal."
- **"Did you mean" on zero-match**: on `resolve_chat_id` → None, `cmd_read` prints top-3 candidates from the full Chat list sorted by `updated_at` desc with the lowest-bar normalized substring match. Reuses the same ranking function as the ambiguity warning.
- **Orphan script removal**: `scripts/get-telegram-message-history` is deleted outright after caller migration. No deprecation shim (per `feedback_prevention_over_cleanup`: half-measures accumulate).

### Flow

Happy path (unique match):
`valor-telegram read --chat "PM: PsyOptimal" --limit 20` → normalized to `pm psyoptimal` on both sides → 1 candidate → header `[PM: PsyOptimal · chat_id=-100123 · last activity: 3m ago]` → message list. Exit 0.

Ambiguous path (default — pick-most-recent + warning to stderr):
`valor-telegram read --chat "PsyOptimal"` → normalized to `psyoptimal` on both sides → 2 candidates. Stderr (greppable):
```
WARN ambiguous-chat query="PsyOptimal" n_candidates=2
CHOSEN  chat_id=-100123  chat_name="PM: PsyOptimal"  last_activity_age=3m
OTHER   chat_id=-100456  chat_name="PsyOptimal"      last_activity_age=2d
```
Stdout: freshness header + messages from the chosen (most-recent) chat. Exit 0 (warning, not error).

Ambiguous path with `--strict`:
`valor-telegram read --chat "PsyOptimal" --strict` → same stderr warning → exit 1, no message output.

Zero-match path:
`valor-telegram read --chat "asdfxxx"` → normalized to `asdfxxx` → 0 candidates → stderr prints top-3 did-you-mean from full Chat list sorted by `updated_at` desc with lowest-bar match. Exit 1.

Fresh bypass:
`valor-telegram read --chat "PM: PsyOptimal" --fresh` → skips Redis, hits Telethon directly → returns live messages. Freshness header reflects live-fetch provenance.

DM path:
`valor-telegram read --user lewis --limit 30` → routes via `resolve_username`, not `resolve_chat_id` → reads DM history. Replaces the orphan script.

Project rollup:
`valor-telegram read --project psyoptimal --limit 30 --since "1 day ago"` → finds all Chat records where `project_key=="psyoptimal"`, merges their messages, sorts ascending, applies `--limit` after merge, prints `[PsyOptimal] ...` / `[PM: PsyOptimal] ...` interleaved.

Project-not-found:
`valor-telegram read --project unknownkey` → stderr prints "no chats with project_key=unknownkey; known keys: [ai, psyoptimal, ...]" sorted by count desc. Exit 1.

Discovery path:
`valor-telegram chats --search "psy"` → returns all chats whose `chat_name_normalized` contains `psy`, sorted by `updated_at` desc.

### Technical Approach

- **Normalization is applied symmetrically** — the same `normalize_chat_name` helper runs on `register_chat`'s input and on `resolve_chat_id`'s input. The helper is deterministic and pure; unit-tested in isolation. Rules (binding): `str.casefold()` → collapse internal whitespace runs to single space → strip `{_, :, -, ., ,}`. Emoji and non-ASCII are untouched.
- **Chat model sidecar field** — `chat_name_normalized = KeyField(null=True)`. Chose `KeyField` (not `Field`) because the resolver's first stage will query this field directly — KeyField gives O(1) indexed lookup via `Chat.query.filter(chat_name_normalized=X)`. The `null=True` flag is required to avoid delete-and-recreate churn when an existing record is re-registered (contrast with `project_key` at models/chat.py:22 which intentionally uses `Field` to avoid delete-and-recreate on change — for `chat_name_normalized`, value changes only when chat_name itself changes, which already delete-and-recreates via the `chat_name` KeyField cascade at `register_chat` lines 858-868, so the KeyField choice is consistent with existing semantics).
- **Resolver order of operations** — new cascade: (1) `Chat.query.filter(chat_name_normalized=normalize(input))` returns all indexed matches. (2) If zero matches, fall back to the legacy Python-side cascade (case-insensitive exact on `chat_name`, then substring) to handle records whose `chat_name_normalized` is still NULL from pre-backfill. (3) Collect all candidates across stages before returning. Sort by `updated_at` desc, tiebreak on `chat_id` asc. (4) If >1 candidate, emit stderr warning; return the top candidate (or exit 1 if `--strict`).
- **`resolve_chat_id` signature preservation** — external contract `resolve_chat_id(chat_name: str) -> str | None` is preserved. The warning side-effect goes to stderr via a dedicated log channel (not `print` — use `sys.stderr.write` directly so we control the format precisely and it stays greppable). Internal callers that don't route stderr keep working unchanged.
- **CLI argparse** — `--chat`, `--user`, `--project` are declared in a `mutually_exclusive_group(required=True)` on the `read` subparser. `--strict` and `--fresh` are independent booleans. `--search` on `chats` is an independent optional string.
- **Freshness header** — one line before messages: `[{chat_name} · chat_id={chat_id} · last activity: {format_timestamp(updated_at)}]`. `format_timestamp` already exists in `valor_telegram.py` (line 79); extend it if needed to emit relative form (e.g., "3m ago", "2d ago") rather than absolute. Wording convention from [#1159](https://github.com/tomcounsell/ai/issues/1159) Tweak 4.
- **`--fresh` implementation** — set a boolean flag that skips the `get_recent_messages` / Redis path and goes straight to `_fetch_from_telegram_api`. The Telethon client setup at `valor_telegram.py:90` is already gated on env vars; `--fresh` simply reorders the dispatch.
- **`--project` implementation** — new function `cmd_read_project(project_key, limit, since)` in `valor_telegram.py`. `Chat.query.filter(project_key=project_key)` returns the chat set; iterate to fetch recent messages from each; merge into one list, sort by timestamp ascending, apply limit after merge. Annotate each line with `[chat_name]` prefix. Error path: if the query returns empty, query all Chat records for their project_keys, dedupe, print sorted list as "did you mean..." candidates.
- **No separate `last_sync_ts`** — per Q3 resolution, we extend `updated_at` semantics only. The cost of a new field (bridge write-path coupling, migration) outweighs the benefit when "last inbound message" is what readers actually need. Documented explicitly in CLI `--help` and skill doc: "`last activity` = last inbound message timestamp, not a last-confirmed-sync signal."
- **Orphan script consolidation** — delete `scripts/get-telegram-message-history` outright after auditing for callers. `valor-telegram read --user USERNAME` replaces the sole feature (DM by username).
- **Orphan skill directory cleanup** — the issue body notes that `.claude/skills/searching-message-history/` and `.claude/skills/get-telegram-messages/` directories no longer exist (confirmed in Freshness Check). The `.claude/skills/telegram/SKILL.md` header still claims consolidation from them. Update the SKILL header to reflect the final state; no files to remove.

### Backfill Strategy

Existing `Chat` records have `chat_name_normalized = None` until re-registered by the bridge on next inbound message. Two approaches were considered:

**Chosen: Lazy populate-on-read (with migration helper fallback).**

Rationale:
- The bridge updates `chat_name_normalized` on every inbound message anyway (automatic over time). For an active chat, this happens within minutes to hours.
- For dormant chats that haven't received a message recently, the resolver's legacy-cascade fallback (Python-side case-insensitive exact, then substring, on `chat_name`) keeps them resolvable until they're re-registered. Nothing breaks.
- On every resolver call, if a candidate `Chat` has `chat_name_normalized is None` but matches via the legacy cascade, we populate and save it eagerly (one-line side effect, cost is a single Redis HSET per affected record). This converges all actively-queried chats within normal use.
- A one-shot migration helper `python -m tools.backfill_chat_normalized_names` is provided as an escape hatch for operators who want to backfill immediately. It iterates `Chat.query.all()`, populates the new field, saves. Idempotent; safe to re-run.

Rejected alternative: mandatory upfront migration. Rejected because (a) the Chat table is small (hundreds of rows), (b) lazy-populate guarantees correctness even if the migration isn't run, and (c) the legacy cascade provides a safety net during the transition. Shipping with an optional helper is strictly safer than requiring a pre-deployment migration step.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `resolve_chat_id` at lines 978–979 currently has `except Exception: return None` — this swallows all Redis/Popoto errors. The new implementation tightens the exception handler to catch only `popoto.ModelException` and `redis.exceptions.RedisError`, logging the exception detail via `logger.warning` before returning None. Test must assert the log message is emitted, not just that None is returned.
- [ ] `register_chat` exception handler at lines 888-889 must also populate `chat_name_normalized` in both the "existing + update" and "new + create" branches. Test that the field is populated even when the Chat record is being renamed (delete-and-recreate path).
- [ ] `normalize_chat_name` is pure and deterministic — no exceptions expected. Test that it accepts empty string, whitespace-only, extremely long input, and non-ASCII without raising.

### Empty/Invalid Input Handling
- [ ] Empty string `--chat ""` → clear error message, not silent None. Argparse `required=True` + `mutually_exclusive_group` handles the mutual-exclusion case; `cmd_read` checks for empty-after-strip.
- [ ] Whitespace-only `--chat "   "` → treated same as empty.
- [ ] Non-ASCII / emoji-containing chat names → normalization preserves them; matching still works (e.g., a chat named "🚀 Launch" still resolves to itself).
- [ ] Very long chat names (>200 chars) → no crash; either match or clean no-match.
- [ ] `--project` with empty key → clear error message.
- [ ] `--user` with empty username → clear error message.

### Error State Rendering
- [ ] Ambiguity warning renders to stderr in greppable format with the CHOSEN/OTHER discriminator on each line. Exit 0 by default; exit 1 with `--strict`.
- [ ] Zero-match "did you mean" renders top-3 to stderr with exit code 1.
- [ ] `--project` with unknown key → stderr lists known keys, exit 1.
- [ ] `--fresh` with no Telethon credentials → clear actionable error pointing at `.env` setup, not a raw exception.
- [ ] Defensive ordering violation (chosen candidate has lower `updated_at` than any non-chosen) → exit 1 unconditionally with a specific error log.

## Test Impact

- [ ] `tests/tools/test_telegram_history.py::TestRegisterChat` — UPDATE: `test_register_new_chat` and `test_register_chat_idempotent` should both assert that `chat_name_normalized` is populated after the registration (not just `registered=True`).
- [ ] `tests/tools/test_telegram_history.py::TestResolveChatId::test_resolve_exact_match` — UPDATE: still passes but now goes through the normalized-field indexed path; add a dedicated assertion that the normalized-field query is hit (spy on the Popoto filter).
- [ ] `tests/tools/test_telegram_history.py::TestResolveChatId::test_resolve_case_insensitive` — UPDATE: current test asserts `resolve_chat_id("dev: valor")` finds a chat named `"Dev: Valor"`. New implementation resolves via normalization (both normalize to `dev valor`). Keep the assertion; extend with a punctuation case: `resolve_chat_id("dev valor")` (no colon) must also find `"Dev: Valor"`.
- [ ] `tests/tools/test_telegram_history.py::TestResolveChatId::test_resolve_partial_match` — UPDATE: currently asserts `resolve_chat_id("Valor")` resolves to `"Dev: Valor Project"` via silent substring match. Under new contract: still returns a chat_id (most-recent match wins), but stderr warning is emitted. Test must capture stderr and assert the warning format, plus assert the correct winner when two candidates are seeded.
- [ ] `tests/tools/test_telegram_history.py::TestResolveChatId::test_resolve_not_found` — UPDATE: `resolve_chat_id("NonexistentChatXYZ")` still returns None at the function level; NEW test case for `cmd_read` behavior: the CLI prints top-3 did-you-mean candidates with exit 1.
- [ ] `tests/tools/test_telegram_history.py` — ADD: `TestNormalizeChatName` class with cases for casefold, whitespace collapse, punctuation stripping (`{_, :, -, ., ,}`), emoji preservation, non-ASCII preservation, empty/whitespace-only input.
- [ ] `tests/tools/test_telegram_history.py` — ADD: `TestResolveAmbiguity` class with the canonical collision fixture (`PsyOptimal` group + `PM: PsyOptimal` DM), asserting (a) the most-recent wins, (b) deterministic tiebreak on chat_id when `updated_at` ties, (c) stderr warning lists both candidates in greppable format, (d) defensive check fires when a non-chosen candidate has greater `updated_at` (simulated by monkey-patching sort key).
- [ ] `tests/unit/test_valor_telegram.py::TestResolveChat` (mock-based tests around `resolve_chat`) — UPDATE: existing tests mock `resolve_chat_id` and assume single return value; add tests for the new stderr warning path, `--strict` exit behavior, `--fresh` dispatch, `--user` and `--project` flags, `chats --search` flag.
- [ ] `tests/unit/test_valor_telegram.py` — ADD: `TestCmdReadProject` class covering the rollup behavior (union across chats, chronological sort, limit-after-merge, `[Chat Name]` annotations, zero-match did-you-mean).
- [ ] `tests/tools/test_telegram_history.py` — ADD: `TestBackfillChatNormalized` covering (a) migration helper populates all NULL records, (b) lazy populate-on-read updates a single record during resolve, (c) idempotent re-run of migration.
- [ ] Integration test for bridge chat registration — UPDATE: any integration test that exercises `bridge/telegram_bridge.py::register_chat` must assert `chat_name_normalized` is populated after inbound message handling.
- [ ] `scripts/get-telegram-message-history` has no dedicated tests in the tree (confirmed via `find tests -name "*get-telegram*"` → zero results) — nothing to DELETE there. The only adjacent reference is in the tools/test_telegram_history.py file.
- [ ] `.claude/skills/telegram/SKILL.md` — UPDATE (docs task): new flags documented, stale references removed.

## Rabbit Holes

- **Separate `last_sync_ts` field on `Chat`**: explicitly out of scope per Q3. Adding the field is a bridge hot-path coupling and a new invariant for marginal benefit. Surface `updated_at` instead; document the semantics gap.
- **Levenshtein / fuzzy-matching libraries**: tempting for "did you mean" but adds a dependency and can produce surprising matches (e.g., "PsyOptimal" matching "OptimalPsy"). Stick to substring + normalization. The top-3 did-you-mean uses normalized substring match.
- **Unicode normalization (NFC/NFKC)**: tempting — "café" vs "café" differ by composition — but injecting Unicode normalization into chat-name matching risks over-matching unrelated names. Defer to a follow-up if real collisions surface.
- **Deprecation shim for `scripts/get-telegram-message-history`**: explicitly rejected. Per `feedback_prevention_over_cleanup`, half-measures accumulate. If the audit finds callers, migrate them in the same PR. If it finds none, delete outright. No shim.
- **Rewriting the `Chat` model schema beyond the one sidecar field**: adding `aliases`, `nicknames`, `emoji_prefix` — out of scope. This plan adds exactly one new field.
- **Telethon fallback enrichment beyond `--fresh`**: making Telethon fallback trigger on stale-match suspicion (not just zero-match or `--fresh`) — invites new failure modes and spec ambiguity; keep current fallback semantics.
- **Popoto query-layer optimization**: reading all chats for did-you-mean and sorting in Python is fine at this scale (hundreds of chats); resist premature optimization.
- **Expanding `--project` to support multiple project_keys or wildcard matching**: out of scope. Exactly-one key at a time; add in a follow-up if needed.

## Risks

### Risk 1: Pre-backfill `Chat` records fail the normalized-field query silently

**Impact:** After deployment, existing `Chat` records have `chat_name_normalized=None`. A resolver query using the normalized-field index finds none and falls through to the legacy cascade. If the legacy cascade then hits the silent-wrong-match bug this plan is fixing, we've regressed.

**Mitigation:** The new resolver also gathers all candidates from the legacy cascade (not just the first hit), sorts by `updated_at` desc, and emits the same stderr warning on >1 match. The legacy-fallback path never silently picks without a visible warning. Additionally, every resolver call with a legacy-path hit eagerly populates the missing `chat_name_normalized` field on the matched record, converging the index over normal use. A test explicitly exercises the pre-backfill state and asserts the warning is still emitted.

### Risk 2: Normalization strips too much, merging legitimately-distinct chat names

**Impact:** Normalizing `backup_logs` and `backup logs` to the same form may cause an unintended ambiguity warning (or worse, a silent wrong-pick if only one exists). Per Q4, `_` IS in the strip set — this is a deliberate choice but has blast radius.

**Mitigation:** The ambiguity-warning design is the primary safety net: ambiguous normalization always surfaces via stderr with both candidates visible. Add unit tests that explicitly verify (a) normalization-collision cases emit the warning, (b) `--strict` converts to an error for scripted callers. If post-deployment surfaces genuine false-positive merges, pulling `_` from the strip set is a one-line change. The choice of `{_, :, -, ., ,}` as the strip set is conservative against `/`, `|`, `;`, `!`, `?`, `@`, `#`, `$`, `%`, `&`, `*`, `(`, `)`, `[`, `]`, `{`, `}`, `<`, `>`, `~`, `` ` ``, `'`, `"`, `=`, `+`, `^` — none of those are stripped.

### Risk 3: `--project` rollup returns duplicate messages if a chat appears in two project_keys

**Impact:** `Chat.project_key` is a `Field` (not unique) and in principle could be changed. Although #1158 made it immutable at creation, legacy records may have drifted project_keys from before that enforcement.

**Mitigation:** #1158's enforcement is prevention-at-creation-site; pre-#1158 records are grandfathered. For this plan, the `--project` rollup queries `Chat.query.filter(project_key=KEY)` and each `Chat` has exactly one `project_key` at a time (scalar field), so no dedupe is needed within a single call — a given chat appears in the result for at most one project_key. No message-level dedupe needed. Add a test that exercises a chat with a post-#1158 project_key to confirm the filter behaves correctly.

### Risk 4: Existing internal callers of `resolve_chat_id` rely on stderr being quiet

**Impact:** If an internal caller routes stderr into a structured log or pipes it somewhere user-visible, the new warning noise could confuse downstream consumers.

**Mitigation:** Audit all internal callers via `grep -rln "resolve_chat_id" --include="*.py" .`. The warning writes to `sys.stderr` directly as a formatted greppable line; it is not using a logger that could be redirected. Each internal caller is reviewed for stderr handling. If any caller cannot tolerate stderr writes, a `silent=True` kwarg on `resolve_chat_id` is added as an explicit opt-in (but this is only added if an actual caller needs it — no speculative addition).

### Risk 5: Orphan script has undocumented callers (scripts, cron, external docs)

**Impact:** Deleting `scripts/get-telegram-message-history` breaks unknown callers.

**Mitigation:** Before deletion, run `grep -rn "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" --include="*.json" .` across the repo. Check `~/Desktop/Valor/` for any machine-local cron references (user-accessible only). Migrate any caller to `valor-telegram read --user`. Delete outright after audit confirms zero remaining references. No shim per rabbit-hole rejection.

### Risk 6: Chat model schema change blocks bridge startup on machines with stale Redis state

**Impact:** The new `chat_name_normalized` KeyField adds a new Popoto index. On startup, Popoto may try to rebuild indices for existing records, slowing bootstrap or failing if the index assumption is violated.

**Mitigation:** `KeyField(null=True)` permits existing records to have null values without reindexing failure — Popoto's indexed fields with `null=True` create index entries only for non-null values. Test the upgrade path on a machine with pre-existing Chat records before merging. If Popoto's null-KeyField semantics turn out to be different in practice, fall back to lazy populate-on-read (already the chosen backfill strategy) and file a follow-up to migrate to `chat_name_normalized = Field(null=True)` — losing the index but keeping the feature. Verified in Phase 1.5 below.

## Race Conditions

### Race 1: Bridge write vs CLI read on `chat_name_normalized`

**Location:** `bridge/telegram_bridge.py:899` (register_chat call) vs `tools/telegram_history/__init__.py:940` (resolve_chat_id).

**Trigger:** Bridge receives an inbound message and starts updating a `Chat` record. Concurrently, the CLI reader calls `resolve_chat_id` on the same chat name.

**Data prerequisite:** The Chat record exists; the race is on whether `chat_name_normalized` has been updated to match the new input.

**State prerequisite:** None; resolver is read-only.

**Mitigation:** Popoto operations on a single Chat record are atomic at the Redis level (HSET + index update). The CLI read will see either the pre-update or post-update state, never a torn read. The legacy cascade fallback also works against whichever state is visible. Worst case: the CLI misses a newly-populated normalized value by milliseconds and falls through to legacy cascade — which is correct. No explicit lock needed.

### Race 2: Concurrent `--fresh` Telethon fetches

**Location:** `tools/valor_telegram.py:258` (`_fetch_from_telegram_api`).

**Trigger:** Two concurrent `valor-telegram read --fresh` invocations.

**Data prerequisite:** Shared Telethon session file.

**State prerequisite:** Telethon client uses a shared session file at the default location.

**Mitigation:** Pre-existing constraint unchanged by this plan. The Telethon client at `valor_telegram.py:90` already reuses a single session file. Concurrent reads are safe; concurrent writes are not attempted. No new risk introduced.

Other than these two, there are no new races — `resolve_chat_id` is a synchronous read-only lookup on the Redis `Chat` index; the CLI is a short-lived process with no concurrent state mutation.

## No-Gos (Out of Scope)

- **New `last_sync_ts` field on `Chat`**: per Q3 resolution. Use `updated_at` with documented semantics.
- **Fuzzy matching beyond normalization**: no Levenshtein, no trigram, no aliases table, no Unicode normalization.
- **Persona/agent behavior changes**: [#1065](https://github.com/tomcounsell/ai/issues/1065) already handles the persona layer. This plan is infrastructure only.
- **Telethon live-query improvements beyond `--fresh`**: fallback semantics unchanged except for the new `--fresh` dispatch.
- **Bridge control-flow changes beyond `register_chat`**: `register_chat` writes the new field; no other bridge code changes.
- **Deprecation shim for the orphan script**: rejected per rabbit-hole.
- **Aliases or nickname system for chats**: separate design task.
- **Multi-project rollup (`--project a,b,c`)**: exactly-one project_key per invocation; multi-project is a follow-up.
- **Pagination for `--project` rollup**: `--limit` is applied after merge, sufficient for N<1000 messages across <20 chats. Pagination is a follow-up if the feature's usage pushes past that scale.

## Update System

Minimal update-system changes required. The new `chat_name_normalized` field is populated lazily on read and on next bridge-registered inbound message (see Backfill Strategy), so no pre-deployment migration is strictly required. However:

- On each machine that runs the bridge, operators may OPTIONALLY run `python -m tools.backfill_chat_normalized_names` once after deploy to eagerly populate all existing records. This is idempotent and safe. The script is added to this repo.
- The update skill (`.claude/skills/update/SKILL.md`) gains a one-line note: "After updating, `python -m tools.backfill_chat_normalized_names` is available as an optional one-shot to backfill Chat normalization for legacy records. Not required — the bridge and resolver populate lazily."
- No new `.env` variables.
- No new system dependencies.
- The `scripts/get-telegram-message-history` deletion is in-repo; no cross-machine concern beyond the standard `git pull` that `/update` already performs.

## Agent Integration

The agent already invokes `valor-telegram read` via Bash (see `.claude/skills/telegram/SKILL.md`). This plan only changes the CLI output format and adds new flags; the existing invocation pattern continues to work. Specifically:

- No new MCP server required. No changes to `.mcp.json` or `mcp_servers/`.
- No new bridge imports.
- The new flags (`--user`, `--project`, `--strict`, `--fresh`, `chats --search`) are additive; the agent uses them via Bash directly once the skill doc advertises them.
- The CLI continues to be installed as a `pyproject.toml` entry point (no new install step).
- Integration verification: a smoke test confirms the skill's documented invocation pattern still works (`valor-telegram read --chat NAME` — unchanged), plus new invocations (`valor-telegram read --project KEY`, `valor-telegram chats --search PATTERN`) return zero-exit on success and the expected output format.

Ambiguity warning format is designed to be agent-parseable. The stderr lines (`WARN ambiguous-chat ...`, `CHOSEN ...`, `OTHER ...`) are keyword-prefixed so a downstream grep or structured log parser can identify them without ambiguity. The skill doc will note that stderr may contain these lines on some invocations and should not be treated as failure unless `--strict` is set.

## Documentation

### Feature Documentation
- [ ] Create or update `docs/features/telegram-cli.md` to describe the unified CLI, all flags (`--chat`, `--user`, `--project`, `--strict`, `--fresh`, `chats --search`), error formats (ambiguity warning, did-you-mean), and the `updated_at` freshness semantics ("last inbound message timestamp, not last-confirmed-sync").
- [ ] Update `docs/features/README.md` index table with an entry for `telegram-cli.md`.

### Skill Documentation
- [ ] Update `.claude/skills/telegram/SKILL.md`:
  - Document `--user`, `--project`, `--strict`, `--fresh` flags on `read`.
  - Document `--search` on `chats`.
  - Remove any stale references to `scripts/get-telegram-message-history` or `get-telegram-messages` skill.
  - Update the "Notes" section to clarify that chat names are resolved via normalization (casefold, whitespace collapse, punctuation strip) and that ambiguous matches emit a stderr warning with all candidates listed.
  - Remove the header claim about consolidating the `searching-message-history` and `get-telegram-messages` skills (those directories are already gone; the claim is stale).
- [ ] Update `CLAUDE.md` "Reading Telegram Messages" section to mention `--user`, `--project`, `--strict`, `--fresh` examples.

### Inline Documentation
- [ ] Docstring on `normalize_chat_name` documents the exact rules (casefold, whitespace collapse, punctuation-strip set) and what is NOT touched (emoji, non-ASCII).
- [ ] Docstring on `resolve_chat_id` documents the new recency-ranked ordering, the stderr warning contract on ambiguity, and the defensive ordering check.
- [ ] Docstring on `Chat.chat_name_normalized` explains its purpose (indexed lookup for the resolver) and its nullable-for-legacy rationale.
- [ ] CLI `--help` text for `--fresh` explicitly says "forces a Telethon round-trip bypassing Redis cache".
- [ ] CLI `--help` for `--chat` / the header output documents "last activity" as "last inbound message timestamp, not last-confirmed-sync".
- [ ] CLI `--help` for `--strict` explicitly says "converts ambiguity warnings into exit code 1; for scripted callers who want hard failure on ambiguity".

## Success Criteria

- [ ] `valor-telegram read --chat "PsyOptimal"` with both `PsyOptimal` (last activity 2d ago) and `PM: PsyOptimal` (last activity 3m ago) in Redis returns messages from `PM: PsyOptimal` (most recent), emits a stderr warning listing BOTH candidates with `chat_id`, `chat_name`, and activity-age, and exits 0.
- [ ] Same scenario with `--strict` flag: exits 1 with stderr warning, no stdout messages.
- [ ] `valor-telegram read --chat "PM PsyOptimal"` (missing colon) resolves to `PM: PsyOptimal` via normalization.
- [ ] `valor-telegram read --chat "asdfxxx"` (zero-match) prints top-3 did-you-mean candidates to stderr and exits 1.
- [ ] `valor-telegram read --user lewis` reads DM messages for the whitelisted username (replacing the orphan script's behavior).
- [ ] `valor-telegram read --project psyoptimal --limit 30` returns a chronologically-ascending union of messages across all chats with `project_key=="psyoptimal"`, annotated with `[Chat Name]` per line.
- [ ] `valor-telegram read --project unknownkey` exits 1 with a stderr list of known project_keys.
- [ ] `valor-telegram read --chat "X" --fresh` bypasses Redis cache and fetches live from Telethon.
- [ ] `valor-telegram read` output includes a header line `[{chat_name} · chat_id={N} · last activity: {T} ago]`.
- [ ] `valor-telegram chats --search "psy"` returns only chats whose normalized name contains `psy`, sorted by recency desc.
- [ ] `scripts/get-telegram-message-history` is deleted; `grep -r "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" --include="*.json" .` returns zero matches in tracked files.
- [ ] `.claude/skills/telegram/SKILL.md` documents all new flags and removes stale orphan-script references.
- [ ] `docs/features/telegram-cli.md` exists (or existing feature doc is updated) with the full CLI reference.
- [ ] `chat_name_normalized` field exists on `Chat` with `KeyField(null=True)`; `register_chat` populates it on every call; lazy-populate fires on resolver legacy-path hits.
- [ ] Backfill helper `python -m tools.backfill_chat_normalized_names` exists, is idempotent, and populates all NULL records.
- [ ] All new and modified tests pass (`pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q`).
- [ ] Full test suite green (`/do-test`).
- [ ] Lint and format clean (`python -m ruff check . && python -m ruff format --check .`).

## Team Orchestration

Simple solo builder + validator pattern with a documentarian in parallel. One builder handles the Python + CLI work (tight cohesion — all edits touch `tools/telegram_history/__init__.py`, `tools/valor_telegram.py`, `models/chat.py`, and `bridge/telegram_bridge.py` at the single `register_chat` call-site). One documentarian updates docs once the CLI surface is stable. One validator confirms end-to-end before merge.

### Team Members

- **Builder (core)**
  - Name: telegram-resolver-builder
  - Role: Implement normalization helper, `chat_name_normalized` field, `register_chat` write-side population, resolver indexed-lookup + ambiguity warning + defensive check, all CLI flags (`--user`, `--project`, `--strict`, `--fresh`, `chats --search`), freshness header, orphan script removal, backfill helper, and all new tests.
  - Agent Type: builder
  - Resume: true

- **Documentarian (docs)**
  - Name: telegram-resolver-docs
  - Role: Create/update `docs/features/telegram-cli.md`, update `docs/features/README.md`, update `.claude/skills/telegram/SKILL.md` (new flags + remove orphan references + stale header claim), update `CLAUDE.md` "Reading Telegram Messages" section.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: telegram-resolver-validator
  - Role: Verify all Success Criteria, run full test suite, confirm orphan script removal is complete, confirm docs match behavior, exercise the collision fixture (PsyOptimal + PM: PsyOptimal) end-to-end against live Redis.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard tier 1 agents — no specialists needed.

## Step by Step Tasks

### 1. Audit orphan-script callers
- **Task ID**: audit-callers
- **Depends On**: none
- **Validates**: grep output captured
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Run `grep -rn "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" --include="*.json" .` and enumerate callers.
- For each caller, decide: migrate to `valor-telegram read --user` (preferred) or annotate as safe-to-break.
- Record disposition for each site in the PR body.

### 2. Implement normalization helper
- **Task ID**: build-normalization
- **Depends On**: none
- **Validates**: tests/tools/test_telegram_history.py::TestNormalizeChatName
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add pure-function `normalize_chat_name(s: str) -> str` in `tools/telegram_history/__init__.py`.
- Rules: `s.casefold()` → collapse internal whitespace runs to single space (`re.sub(r"\s+", " ", x)`) → strip `{_, :, -, ., ,}` via `str.translate`.
- Do NOT touch emoji or non-ASCII.
- Write unit tests: casefold, whitespace collapse, each punctuation stripped individually, emoji preservation, non-ASCII preservation, empty string, whitespace-only, extremely-long input.

### 3. Add `chat_name_normalized` field to Chat model
- **Task ID**: build-chat-field
- **Depends On**: none
- **Validates**: tests/tools/test_telegram_history.py::TestRegisterChat (updated)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `chat_name_normalized = KeyField(null=True)` to `models/chat.py::Chat`.
- Update `cleanup_expired` if needed (no change expected; `updated_at` logic is independent).

### 4. Update `register_chat` to populate normalized field
- **Task ID**: build-register-chat
- **Depends On**: build-normalization, build-chat-field
- **Validates**: tests/tools/test_telegram_history.py::TestRegisterChat (updated)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/telegram_history/__init__.py::register_chat` (line 830):
  - On the "existing + update" branch (lines 856-873), compute and assign `chat_name_normalized` whenever `chat_name` is written.
  - On the "new + create" branch (lines 874-881), include `chat_name_normalized` in the `Chat.create()` call.
- Tests: register new chat + assert normalized field populated; rename a chat + assert normalized field reflects new name; idempotent re-register.

### 5. Rewrite `resolve_chat_id` with normalized lookup + ambiguity warning
- **Task ID**: build-resolver
- **Depends On**: build-register-chat
- **Validates**: tests/tools/test_telegram_history.py::TestResolveChatId, TestResolveAmbiguity
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/telegram_history/__init__.py::resolve_chat_id`:
  - Primary path: `Chat.query.filter(chat_name_normalized=normalize_chat_name(chat_name))` returns candidates.
  - Legacy fallback (for NULL `chat_name_normalized` records): Python-side case-insensitive exact on `chat_name`, then substring; collect ALL matches. On legacy-path hit, eagerly populate the missing `chat_name_normalized` field on the matched record.
  - Sort candidates by `updated_at` desc, tiebreak on `chat_id` asc.
  - If >1 candidate: emit stderr warning in greppable format:
    ```
    WARN ambiguous-chat query="..." n_candidates=N
    CHOSEN  chat_id=...  chat_name="..."  last_activity_age=...
    OTHER   chat_id=...  chat_name="..."  last_activity_age=...
    ```
  - Defensive: after the sort, assert chosen `updated_at >= max(other updated_ats)`; if violated, stderr-print and return None (or raise — design decision: return None so caller exits 1 via zero-match path, preserving existing signature).
  - Return chosen candidate's `chat_id`.
- Keep the `str | None` signature; do NOT add `--strict` at this layer (it's a CLI-layer concern).
- Tests: unique match, 2-candidate ambiguity (most-recent wins, stderr warning emitted), 3-candidate ambiguity, tiebreak on chat_id when `updated_at` equal, zero-match returns None, defensive check fires when monkey-patched sort returns a violator, legacy-NULL path works and populates the field.

### 6. Wire CLI `read` flags and error paths
- **Task ID**: build-cli-read
- **Depends On**: build-resolver
- **Validates**: tests/unit/test_valor_telegram.py::TestCmdRead
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/valor_telegram.py`:
  - `read` subparser: add `--chat`, `--user`, `--project` inside a `mutually_exclusive_group(required=True)`. Add `--strict`, `--fresh` as booleans.
  - `cmd_read`:
    - If `--project`: invoke new `cmd_read_project` (see next task).
    - If `--user`: route directly via `resolve_username` (existing code path, no resolve_chat_id).
    - If `--chat`: call `resolve_chat_id` as usual.
    - After resolve: fetch messages. If `--fresh`, skip Redis and go directly to Telethon.
    - Capture the stderr warning: if the resolver emitted one AND `--strict` is set, exit 1 with no stdout output.
    - On zero-match: print top-3 did-you-mean candidates to stderr, exit 1.
    - Prepend output with header: `[{chat_name} · chat_id={N} · last activity: {T} ago]`.
- Tests: `--chat` single-match (header + messages), `--chat` ambiguous (stderr warning, exit 0), `--chat` ambiguous + `--strict` (exit 1), `--chat` zero-match (did-you-mean), `--user` DM path, `--fresh` bypass, mutually-exclusive flag validation.

### 7. Implement `read --project` rollup
- **Task ID**: build-cli-project
- **Depends On**: build-cli-read
- **Validates**: tests/unit/test_valor_telegram.py::TestCmdReadProject
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `cmd_read_project(project_key, limit, since)` in `tools/valor_telegram.py`.
- `Chat.query.filter(project_key=project_key)` → list of chats.
- For each chat, fetch recent messages via existing `get_recent_messages`. Apply per-chat `--since` filter early for perf.
- Merge all messages into one list, sort by `timestamp` ascending, apply `--limit` after merge.
- Annotate each output line: `[{chat_name}] {timestamp} {sender}: {content}`.
- Zero-chats path: fetch `Chat.query.all()`, dedupe their `project_key` values, print "no chats with project_key={key}; known keys: [list]" to stderr sorted by count desc. Exit 1.
- Tests: 2-chat rollup with known project_key (chronological merge), `--limit` applies after merge not per-chat, `--since` filters per-chat before merge, zero-chats path emits did-you-mean project_keys.

### 8. Implement `chats --search`
- **Task ID**: build-cli-chats-search
- **Depends On**: build-normalization
- **Validates**: tests/unit/test_valor_telegram.py::TestCmdChatsSearch
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--search PATTERN` to `chats` subparser.
- In `cmd_chats`: after fetching chats via `list_chats`, if `--search` is set, apply `normalize_chat_name(PATTERN) in normalize_chat_name(chat.chat_name)` filter. Sort unchanged (by `last_message` desc).
- Tests: search returns single match, multiple matches, zero-match empty-but-clean.

### 9. Implement backfill helper
- **Task ID**: build-backfill
- **Depends On**: build-normalization, build-chat-field
- **Validates**: tests/tools/test_telegram_history.py::TestBackfillChatNormalized
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `tools/backfill_chat_normalized_names.py` (new module).
- `main()`: iterates `Chat.query.all()`, for each record with `chat_name_normalized is None`, compute the normalized form and save. Idempotent. Prints a count at end.
- Tests: populates all NULL records in one pass, idempotent on re-run, handles empty Chat table cleanly.

### 10. Delete orphan script
- **Task ID**: delete-orphan
- **Depends On**: build-cli-read, audit-callers
- **Validates**: `grep -r "get-telegram-message-history"` returns zero
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Migrate any callers found in audit-callers to `valor-telegram read --user USERNAME`.
- Delete `scripts/get-telegram-message-history`.
- Delete any associated orphan test files (none expected per Test Impact audit, but double-check with `find tests -name "*get-telegram-history*"`).
- `grep -r "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" --include="*.json" .` must return zero in tracked files.

### 11. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-cli-read, build-cli-project, build-cli-chats-search, delete-orphan, build-backfill
- **Assigned To**: telegram-resolver-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create or update `docs/features/telegram-cli.md` with the full unified-CLI reference, all new flags, error formats, freshness semantics, backfill helper.
- Update `docs/features/README.md` index table.
- Update `.claude/skills/telegram/SKILL.md` with new flags, error format, remove orphan references, correct stale "consolidation" claim in header.
- Update `CLAUDE.md` "Reading Telegram Messages" section with `--user`, `--project`, `--strict`, `--fresh` examples.

### 12. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous task IDs
- **Assigned To**: telegram-resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -v`.
- Run full suite via `/do-test`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Run `grep -rn "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" --include="*.json" .` and confirm zero.
- Seed a live Redis instance with the canonical collision fixture (`PsyOptimal` + `PM: PsyOptimal`) and run `valor-telegram read --chat "PsyOptimal"` by hand; assert the stderr warning appears and the most-recent chat's messages are returned.
- Run `python -m tools.backfill_chat_normalized_names` against the same Redis; confirm no errors; confirm it's idempotent on second run.
- Walk the Success Criteria list and confirm each item.
- Generate pass/fail report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q` | exit code 0 |
| Full suite green | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Orphan script removed | `grep -rn "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" --include="*.json" .` | exit code 1 |
| New CLI flags in skill | `grep -E "^- .*--user\|--project\|--strict\|--fresh\|chats --search" .claude/skills/telegram/SKILL.md` | output contains all five flag tokens |
| Backfill helper exists | `python -c "import tools.backfill_chat_normalized_names"` | exit code 0 |
| Normalized field on Chat | `python -c "from models.chat import Chat; assert 'chat_name_normalized' in Chat._fields"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---
