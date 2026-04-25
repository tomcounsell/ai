---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-04-25
tracking: https://github.com/tomcounsell/ai/issues/1169
last_comment_id:
---

# Telegram Cross-Chat Project-Level Stitching

## Problem

A single "project" in Valor often lives across multiple Telegram chats. PsyOPTIMAL has three: a main group chat (`PsyOPTIMAL`), a PM sidebar (`PM: PsyOptimal`), and a dev channel (`Dev: PsyOPTIMAL`). When the agent (or a human reader) wants to know "what's been happening on PsyOptimal lately?", the current `valor-telegram read` requires running the command three separate times (once per chat) and mentally concatenating the results.

PR #1168 (issue #1163) shipped the ambiguity policy that fixed silent wrong-matches *for the single-chat case*. It deliberately deferred cross-chat project-level stitching as defect 7 — high value but a separate design — and filed this issue from the parent plan's Task 8 follow-up. That deferral is the reason this plan exists.

**Current behavior:**
- `valor-telegram read --chat NAME` operates on exactly one resolved chat.
- A reader who wants project-wide situational awareness runs N reads and interleaves them by hand.
- `valor-telegram chats` has no way to say "show me only the chats associated with project X."
- `Chat.project_key` is already populated by the bridge ([`bridge/telegram_bridge.py:899-904`](../../bridge/telegram_bridge.py)) but is invisible at the read surface.

**Desired outcome:**
A reader runs `valor-telegram read --project psyoptimal` and gets the most recent N messages across every chat where `Chat.project_key == "psyoptimal"`, interleaved chronologically (newest first), each line tagged with the originating chat name so the reader can see where each message came from. JSON output includes `chat_id` and `chat_name` per message. `valor-telegram chats --project psyoptimal` lists every chat that would be unioned. The single-chat `read` path is unchanged.

## Freshness Check

**Baseline commit:** `3401c882` (`update: make /update --full idempotent + skip bridge wait on no-bridge machines`)
**Issue filed at:** 2026-04-24T15:56:55Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `models/chat.py:22` — `project_key = Field(null=True)` — confirmed, still a plain `Field` (not `KeyField`), so `Chat.query.filter(project_key=...)` is NOT an indexed lookup. Implementation must scan `Chat.query.all()` and filter in Python.
- `bridge/telegram_bridge.py:899-904` — `register_chat(..., project_key=_early_project_key)` — confirmed, bridge writes `project_key` on every received message.
- `tools/valor_telegram.py:745` — `read_target_group = read_parser.add_mutually_exclusive_group()` — confirmed, the mutex group already covers `--chat / --chat-id / --user`. Adding `--project` to the same group is the natural extension.
- `tools/valor_telegram.py:300-520` — `cmd_read` body, freshness header at lines 491-502 — confirmed, structure intact.
- `tools/valor_telegram.py:660-730` — `cmd_chats` body — confirmed, `--search` filter is at lines 686-698.
- `tools/telegram_history/__init__.py:452` — `get_recent_messages(chat_id, limit)` — confirmed, returns `{chat_id, messages, count}`.
- `tools/telegram_history/__init__.py:1088-1133` — `list_chats()` — confirmed, builds per-chat dicts but does NOT currently include `project_key` in the output.

**Cited sibling issues/PRs re-checked:**
- #1163 — closed 2026-04-25, resolution shipped in #1168 (merged 2026-04-25T03:32:59Z). The parent plan was migrated out of `docs/plans/` into `docs/features/telegram-messaging.md` + `docs/features/telegram-history.md` in commit `72f6e2fa`. This issue is the explicit follow-up.
- #1067 — closed, shipped `valor-email` CLI as the analog of `valor-telegram`. Useful as a flag-design reference; not direct overlap.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --since="2026-04-24T15:56:55Z" -- tools/valor_telegram.py tools/telegram_history/ models/chat.py` returns empty.

**Active plans in `docs/plans/` overlapping this area:** none. The parent plan was migrated out post-merge; no other Telegram plan touches the read path.

**Notes:** No drift. The recon evidence in the issue body is current; no line numbers need correction.

## Prior Art

- **Issue [#1163](https://github.com/tomcounsell/ai/issues/1163)** (closed) → PR [#1168](https://github.com/tomcounsell/ai/pull/1168) (merged 2026-04-25): hardened single-chat resolution with `ChatCandidate`, `AmbiguousChatError`, normalization, freshness header, `--chat-id`, `--user`, `--strict`, `chats --search`. Defect 7 (this issue) was deliberately deferred. Sets the platform this plan builds on.
- **Issue [#1067](https://github.com/tomcounsell/ai/issues/1067)** (closed): `valor-email` CLI shipped with a `read --search` pattern; flag-design analog only.
- **Issue [#811](https://github.com/tomcounsell/ai/issues/811)** (closed): "Memory project_key isolation broken" — established that `project_key` is the canonical project identifier across the codebase. This plan reuses that identifier verbatim.
- **Issue [#1158](https://github.com/tomcounsell/ai/issues/1158)** (closed): "Child sessions lose project scope" — adjacent (session routing, not Telegram), confirms `project_key` is the durable cross-component identifier.

No prior attempt at cross-chat project stitching exists. This is greenfield on that code path.

## Research

**Queries used:**
- `argparse mutually exclusive group add to existing group python 2026`

**Key findings:**
- argparse allows adding multiple options to a single mutually-exclusive group via repeated `group.add_argument(...)` calls. The existing read-target group in `tools/valor_telegram.py:745` is a flat mutex group; adding `--project` as a 4th member is supported and is the idiomatic pattern (no nesting required, no Python version concerns). Source: [Python argparse docs](https://docs.python.org/3/library/argparse.html).
- Nesting mutex groups inside other mutex groups is deprecated in 3.11 and removed in 3.14 — not a concern here, but documents why a single flat group is the only correct shape.

No external dependencies, libraries, or APIs are introduced by this plan. The implementation is pure Python on top of existing Popoto models.

## Data Flow

End-to-end flow for `valor-telegram read --project psyoptimal --limit 20`:

1. **Entry point** — `tools/valor_telegram.py:cmd_read` (line 300). New branch in flag-mutex validation: `--project` is the 4th arm of the existing read-target mutex group at line 745. The mutex-validator block at lines 309-325 is extended to count `--project` alongside `--chat / --chat-id / --user`.
2. **Project → chat list resolution** — new helper `tools.telegram_history.resolve_chats_by_project(project_key) -> list[ChatCandidate]`. Reads `Chat.query.all()`, filters where `chat.project_key == project_key`, projects to `ChatCandidate`, sorts by `last_activity_ts` desc with the same `_sort_candidates` helper used today by `resolve_chat_candidates`. Returns `[]` for unknown project.
3. **Zero-chat path** — if the resolver returns `[]`, print `No chats found for project 'psyoptimal'. Run \`valor-telegram chats --project psyoptimal\` to verify.` to stderr, exit 1.
4. **Per-chat fetch** — for each `ChatCandidate`, call existing `get_recent_messages(chat_id=c.chat_id, limit=N)`. Each returned message dict gets `chat_id` and `chat_name` injected at merge time so the formatter can render them.
5. **Merge & trim** — concatenate all per-chat message lists, sort by `timestamp` desc, slice to `args.limit`. (Per-chat `limit` is also passed as `args.limit` so the merge candidate pool is at least N — this avoids missing recent messages from one chat behind a flood from another. See Technical Approach for the bound.)
6. **Project freshness header** — one line summarizing the matched chats, e.g. `[project=psyoptimal · 3 chats: PsyOPTIMAL, PM: PsyOptimal, Dev: PsyOPTIMAL · last activity: 3m ago]`. The `last activity` is the max `last_activity_ts` across the chat set.
7. **Output** — for each merged message, render `[timestamp] [chat_name] sender: content`. JSON mode (`--json`) emits a list of message dicts each enriched with `chat_id` and `chat_name` fields.

Discovery path for `valor-telegram chats --project psyoptimal`:

1. Entry point — `cmd_chats` (line 660). Add `--project PROJECT_KEY` argument; combinable with `--search` (both filters apply if both are set).
2. After `list_chats()` returns, filter the result list where `chat["project_key"] == project_key`. (Requires extending `list_chats()` to include `project_key` in each chat dict — currently it does not surface this field.)
3. Output the filtered list with the existing table format. Header line clarifies the filter: `Known chats matching project 'psyoptimal' (3):`.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:**
  - New `resolve_chats_by_project(project_key: str) -> list[ChatCandidate]` in `tools/telegram_history/__init__.py`. Pure function; reuses `_sort_candidates` and `_chat_to_candidate`.
  - `list_chats()` in `tools/telegram_history/__init__.py` gains `project_key` in each per-chat dict (additive; existing callers ignore unknown keys).
  - `valor-telegram read` gains a 4th member in the existing read-target mutex group: `--project PROJECT_KEY`. JSON message dicts gain `chat_id` and `chat_name` fields *only* when `--project` is set (single-chat reads are unchanged so existing JSON consumers don't break).
  - `valor-telegram chats` gains an additive `--project PROJECT_KEY` filter.
- **Coupling:** unchanged. The new resolver consumes `Chat` model fields the same way the existing one does; no new model dependencies, no new bridge entry points.
- **Data ownership:** unchanged. `Chat.project_key` is owned by the bridge writer (`register_chat`); this plan only reads it.
- **Reversibility:** high. All changes are additive at the API and CLI layers. Rollback is a single revert.

## Appetite

**Size:** Small

**Team:** Solo dev (builder + validator pair), 1 documentarian for docs.

**Interactions:**
- PM check-ins: 1 (confirm Open Questions decisions before build, or accept the plan's recommended defaults).
- Review rounds: 1.

**Rationale:** Single feature touching two existing files (`tools/valor_telegram.py`, `tools/telegram_history/__init__.py`), one new helper, one new flag on each of two subcommands. No schema changes, no bridge changes, no new dependencies. The platform shipped by #1168 already has `ChatCandidate`, `_sort_candidates`, freshness-header rendering, and the mutex group — this plan reuses all of them.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | `Chat` model reads during tests |
| Popoto importable | `python -c "from models.chat import Chat"` | Model layer available |
| Existing test baseline passes | `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q` | Confirm no unrelated breakage before starting |

No prerequisites beyond normal dev environment.

## Solution

### Key Elements

- **`resolve_chats_by_project`** (new): pure function in `tools/telegram_history/__init__.py`. Returns `list[ChatCandidate]` for a given `project_key`, sorted by `last_activity_ts` desc with the same deterministic `chat_id` tiebreak the single-chat resolver uses.
- **`get_recent_messages_for_project`** (new helper, optional): thin wrapper that calls `resolve_chats_by_project` then `get_recent_messages` per chat, merges, trims. May be inlined into `cmd_read` if doing so keeps test boundaries clean — implementation detail decided at build time.
- **`--project PROJECT_KEY` on `read`**: 4th member of the existing read-target mutex group. Mutually exclusive with `--chat / --chat-id / --user / --strict`. Mutex with `--strict` is enforced because `--strict` is meaningless when no name is being resolved.
- **`--project PROJECT_KEY` on `chats`**: additive filter, combinable with `--search`. Surfaces `Chat.project_key` for the first time in the CLI.
- **Project freshness header** (new): single line analog to the per-chat header, summarizing the chat set. Format: `[project=KEY · N chats: name1, name2, name3 · last activity: T]`.
- **Per-line chat tag** (new, human output): `[timestamp] [chat_name] sender: content`. Single-chat reads keep the original `[timestamp] sender: content` format unchanged.
- **JSON enrichment** (new, `--project` mode only): each message dict gains `chat_id` and `chat_name`. Single-chat JSON output is unchanged.
- **Limit semantics**: `--limit` is total after merge. Each per-chat fetch uses `limit=args.limit` (so the candidate pool is at least N), then the merge sorts by timestamp desc and trims to `args.limit`.

### Flow

Happy path:
`valor-telegram read --project psyoptimal --limit 20` →
1. resolver returns 3 chats →
2. each chat fetches up to 20 recent messages (Redis-backed) →
3. all messages merged by timestamp desc, trimmed to 20 →
4. project header printed →
5. per-line `[chat_name]` tag in chronological output, exit 0.

Zero-chat path:
`valor-telegram read --project unknown` →
1. resolver returns `[]` →
2. stderr: `No chats found for project 'unknown'. Run \`valor-telegram chats --project unknown\` to verify.` →
3. exit 1.

Discovery path:
`valor-telegram chats --project psyoptimal` →
1. `list_chats()` returns all chats with `project_key` field →
2. filter to `project_key == "psyoptimal"` →
3. existing table format, header reflects the filter, exit 0.

Combined-filter path:
`valor-telegram chats --project psyoptimal --search "dev"` →
1. apply both filters (`project_key == "psyoptimal"` AND normalized substring contains `"dev"`) →
2. existing table format, exit 0.

JSON path:
`valor-telegram read --project psyoptimal --json` →
JSON list, each message has `chat_id`, `chat_name`, plus the existing `id`, `message_id`, `sender`, `content`, `timestamp`, `message_type` fields.

### Technical Approach

- **Indexing constraint:** `Chat.project_key` is a plain `Field` (not `KeyField`). The model docstring explicitly notes this is deliberate — keeping it as a `Field` avoids the delete-and-recreate-on-change cost that `KeyField` would impose. Consequence: `Chat.query.filter(project_key=...)` is NOT an indexed lookup. We scan `Chat.query.all()` and filter in Python — exactly the same pattern as the existing `resolve_chat_candidates` cascade. Acceptable at hundreds-of-chats scale (parent plan made the same call for stages 2–3 of the cascade).
- **Reuse over duplication:** `resolve_chats_by_project` reuses the existing `_chat_to_candidate` projection and `_sort_candidates` ordering helpers. No new sorting logic. Tiebreak (when two chats share `last_activity_ts`, including both `None`) inherits the existing deterministic `chat_id` ascending tiebreak.
- **Mutex extension:** the read subcommand's mutex group at `tools/valor_telegram.py:745` gains `--project` as a 4th argument. The defensive `flag_count` check in `cmd_read` lines 309-325 is extended to count `--project` alongside `--chat / --chat-id / --user`. `--strict` is rejected when `--project` is set because `--strict` is a name-resolution flag — it has no meaning under `--project` (which never goes through name resolution).
- **Per-chat fetch budget:** each chat fetches `limit=args.limit` messages via `get_recent_messages`. Worst case: a project with K chats fetches K×N messages, of which N are kept after merge-and-trim. K is bounded by the number of chats per project (typically 2-3, max ~10 in practice) and N is the user-provided limit (default 10). Total reads: O(K·N) — acceptable.
- **Merge ordering:** sort by `timestamp` desc, deterministic tiebreak on `chat_id` then `message_id`. `timestamp` field already comes from `_ts_to_iso(ts)` (an ISO-8601 string) in `get_recent_messages` output — string-compare works correctly because ISO-8601 lexicographic order matches chronological order. Test asserts ordering against a fixture with messages from different chats at known times.
- **Header format:** `[project=KEY · N chats: name1, name2, name3 · last activity: T]`. The chat-name list is ordered by `last_activity_ts` desc to match the resolver's ordering — easier to scan when debugging. If the project has >5 chats, truncate the list to the 5 most recent and append `... +M more` (5 is a soft cap to keep the header on one line; actual cap can be tuned in implementation if 5 produces wrap-around).
- **Last-activity computation:** `max(c.last_activity_ts for c in candidates if c.last_activity_ts is not None)` rendered via the existing `_format_relative_age` helper. If all candidates have `None` `last_activity_ts`, render as `last activity: never`.
- **JSON output enrichment:** in `--project` mode, the message dict produced by `get_recent_messages` is augmented with `chat_id` and `chat_name` *before* JSON serialization. Existing JSON consumers of single-chat reads see no change. Documented in the freshness header for human mode and in the schema for JSON mode.
- **`list_chats()` field addition:** add `project_key: chat.project_key` to each chat dict produced by `list_chats()`. Additive — existing JSON consumers ignore unknown keys. The `chats` subcommand's table output remains unchanged unless `--project` is set (in which case the header reflects the filter).
- **Empty `project_key` handling:** chats with `project_key is None` (e.g., never tagged by the bridge, or registered before #1163's `project_key` writes were enabled) are NEVER matched by `--project`. They appear only in unfiltered `chats` output. No change to bridge writes.
- **Empty/whitespace-only `--project` value:** rejected before reaching the resolver, same defensive pattern as `cmd_read` lines 327-345 use today for `--chat` and `--user`. Error message: `Error: --project cannot be empty or whitespace-only.`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks. The new `resolve_chats_by_project` mirrors the narrow `(redis.RedisError, popoto.ModelException, popoto.QueryException)` pattern from `resolve_chat_candidates` ([`tools/telegram_history/__init__.py:216`](../../tools/telegram_history/__init__.py)). Test asserts a `logger.warning` is emitted when Redis is unavailable and the function returns `[]`.
- [ ] `cmd_read` extension does not introduce new exception handlers — the new `--project` branch flows through existing patterns.

### Empty/Invalid Input Handling
- [ ] `--project ""` → rejected at flag parsing layer (empty/whitespace check before resolver).
- [ ] `--project "   "` → same as empty.
- [ ] `--project unknown_project` → resolver returns `[]`; CLI prints "No chats found for project 'unknown_project'." to stderr, exits 1.
- [ ] `--project psyoptimal` with all matching chats having zero messages → project header still prints, then `No messages found for project 'psyoptimal'.` exit 0 (matches the single-chat empty-result behavior).
- [ ] `--limit 0` with `--project` → returns empty message list with header (matches single-chat behavior).

### Error State Rendering
- [ ] Project header prints to stdout BEFORE any "no messages" text, so the reader always knows which chats were queried even when results are empty.
- [ ] Zero-chat-match prints to stderr (not stdout), consistent with the single-chat zero-match path.
- [ ] Mutex violation (`--project` + `--chat`) prints `Error: --chat, --chat-id, --user, and --project are mutually exclusive.` to stderr, exits 1. Argparse handles this at parse time when both come from the same mutex group; the defensive `flag_count` block is extended to defend the direct-call path used by tests.
- [ ] `--project` + `--strict` prints `Error: --strict has no effect with --project; remove one of them.` to stderr, exits 1.

## Test Impact

- [ ] `tests/tools/test_telegram_history.py` — UPDATE: add a new `TestResolveChatsByProject` class with tests for: (a) zero matching chats returns `[]`, (b) one matching chat returns one candidate, (c) many matching chats are sorted by `last_activity_ts` desc with `chat_id` tiebreak, (d) chats with `project_key=None` are never returned, (e) Redis unavailable returns `[]` and logs a warning. Fixture seeds 4 `Chat` records: 3 tagged `psyoptimal` (with varying `updated_at` to assert ordering) and 1 tagged `valor`. Reuse existing fixture patterns from `TestSearchHistory` / `TestGetRecentMessages`.
- [ ] `tests/tools/test_telegram_history.py` — UPDATE `TestListChats` (or wherever `list_chats` is exercised) to assert the new `project_key` field is present in each chat dict and reflects the underlying value (including `None`).
- [ ] `tests/unit/test_valor_telegram.py::TestCmdReadFlags` — UPDATE: add tests for (a) `--project psyoptimal` returns merged messages from all matching chats with per-line `[chat_name]` tag, (b) `--project psyoptimal --json` returns each message with `chat_id` and `chat_name` fields, (c) `--project unknown` exits 1 with "No chats found" stderr, (d) `--project psyoptimal --limit 5` trims to 5 total after merge (NOT 5 per chat), (e) project freshness header format is correct (regex assertion on the bracket layout), (f) merge ordering — messages from different chats interleaved by timestamp desc.
- [ ] `tests/unit/test_valor_telegram.py::TestCmdReadArgparseMutex` — UPDATE: add tests for (a) `--project` + `--chat` mutex violation, (b) `--project` + `--chat-id` mutex violation, (c) `--project` + `--user` mutex violation, (d) `--project` + `--strict` rejected with explicit error, (e) `--project ""` rejected as empty.
- [ ] `tests/unit/test_valor_telegram.py::TestCmdChatsSearch` — UPDATE: extend or add a sibling `TestCmdChatsProject` class with tests for (a) `chats --project psyoptimal` returns only matching chats, (b) `chats --project psyoptimal --search "dev"` applies both filters, (c) `chats --project unknown` returns empty list with appropriate message.
- [ ] `tests/unit/test_valor_telegram.py::TestResolveChat` — no changes (single-chat path unchanged).

No existing tests are deleted or replaced. All changes are additive; the single-chat read/send/chats paths retain their current behavior and tests.

## Rabbit Holes

- **Making `Chat.project_key` a `KeyField` for indexed lookups**: tempting for "performance" but premature at this scale. The `Field` choice was deliberate per the model docstring (avoids delete-and-recreate on change). Hundreds of chats scanned in Python is fast.
- **Per-chat pagination with separate `--limit-per-chat`**: rabbit hole. "Total after merge" is what readers actually want for situational awareness; per-chat would require pagination state per chat and a more complex merge protocol for marginal benefit.
- **Cross-project merge** (e.g., `--project a,b,c`): out of scope. If real demand emerges later, layering it on `resolve_chats_by_project` is trivial — but no current use case justifies the UX complexity now.
- **Telethon fallback for `--project` mode**: Telethon has no project_key concept. If a project's chats have zero Redis messages, we report empty — same as single-chat read. No new fallback path.
- **Project-discovery flag** (`valor-telegram projects` listing all known project_keys): adjacent feature, not requested. Defer.
- **Surfacing `project_key` on every `chats` row in human output**: would force a wider table or a 4th column. The `--project` filter and JSON output both surface it cleanly already; don't bloat the default table.
- **Group-by-chat section headers in human output**: considered and rejected. Chronological interleaving with per-line `[chat_name]` tags is a stronger UX (the reader can see a true timeline across chats); section headers would lose the cross-chat ordering that's the whole point of stitching.

## Risks

### Risk 1: `Chat.project_key` rows have stale or missing values for chats registered before #1163 shipped

**Impact:** A reader runs `--project psyoptimal` and gets fewer chats than expected because some `Chat` records have `project_key=None`. Silent under-coverage.

**Mitigation:** The bridge has been writing `project_key` on every `register_chat` call since the parent plan shipped, so `updated_at` for any active chat will have triggered a re-save with the correct `project_key`. Inactive chats with stale `None` values are exactly the chats a reader doesn't care about (no recent activity). Test seeds an `updated_at=0` chat with `project_key=None` to confirm it does NOT appear in `--project` output. A separate one-off cleanup script is out of scope (No-Gos); the freshness-header `last activity` will make stale gaps obvious if they ever matter.

### Risk 2: `--limit` semantics confuse users who expect per-chat limits

**Impact:** A user running `--limit 5` on a 3-chat project gets at most 5 messages total, not 5 per chat. May surprise users coming from the single-chat `--limit` semantics.

**Mitigation:** Document "total after merge" explicitly in the help text for `--limit` when `--project` is set, and in `docs/features/telegram-messaging.md`. The project-header line plus per-line `[chat_name]` tags make the merge behavior visible — a user seeing 5 messages from 2 of 3 chats can immediately see the limit is total. If real demand emerges for per-chat semantics later, add `--limit-per-chat` as a follow-up.

### Risk 3: Per-line `[chat_name]` tag bloats output for projects with long chat names

**Impact:** A chat name like `"PsyOPTIMAL Engineering Daily Standup Async Channel"` makes every output line wide enough to wrap.

**Mitigation:** Truncate `chat_name` to 25 chars in the per-line tag with ellipsis (`PsyOPTIMAL Engineerin...`). The full name is in the project header; the per-line tag is for at-a-glance attribution. JSON output preserves the full name. Test asserts a fixture with a >25-char chat_name truncates correctly.

### Risk 4: A `Chat` record exists with `project_key` set but no messages (registered then went silent)

**Impact:** `resolve_chats_by_project` returns it as a candidate, but `get_recent_messages` returns empty. The merged output is shorter than expected, and the project header counts a chat that contributes nothing.

**Mitigation:** Acceptable behavior — the project header lists all matching chats by name, including the empty one, so the reader sees that the chat has no recent messages. The `last activity: T ago` part of the header reflects the most-recently-active chat in the set, not all chats. Test seeds a project with one active and one silent chat to confirm both appear in the header but only active messages appear in the body.

## Race Conditions

No race conditions identified. `resolve_chats_by_project` is a synchronous read-only scan over the `Chat` model. `get_recent_messages` is a synchronous read-only fetch. The CLI is a short-lived process. Bridge writes to `Chat` may race with a concurrent CLI read, but the worst case is that the CLI sees a slightly-stale `project_key` or `updated_at` — same situational class as the existing single-chat freshness behavior, not a correctness bug. No locks, no awaits, no shared mutable state.

## No-Gos (Out of Scope)

- **Schema migration to make `Chat.project_key` a `KeyField`**: deliberate `Field` choice in the model; performance is fine at this scale.
- **One-off cleanup script for `Chat` rows with missing `project_key`**: bridge-side concern (parent plan #1163 shipped the writes); inactive stale rows naturally fall out of the `--project` set.
- **Cross-project merge** (`--project a,b,c`): no current use case.
- **`valor-telegram projects` listing command**: adjacent, not requested.
- **Per-chat pagination / `--limit-per-chat`**: total-after-merge is the right default; revisit only if real demand emerges.
- **Bridge changes to `register_chat`**: untouched.
- **Telethon fallback under `--project`**: Telethon has no `project_key`; empty-result behavior matches single-chat.
- **Group-by-chat section headers**: chronological interleaving is the design intent of "stitching."
- **Surfacing `project_key` as a default column in `valor-telegram chats` table output**: would bloat the default; available via `--json` and the `--project` filter.

## Update System

No update system changes required. This is a purely internal extension of the `valor-telegram` CLI. No new dependencies, no new config files, no new env vars. The CLI is installed via the existing entry-point mechanism, which propagates automatically on `/update`.

## Agent Integration

No new agent integration required — the agent already invokes `valor-telegram read` and `valor-telegram chats` via Bash (see [`.claude/skills/telegram/SKILL.md`](../../.claude/skills/telegram/SKILL.md)). This plan adds two new flags (`--project` on `read` and on `chats`) and changes the output format only when `--project` is set; the existing invocation patterns continue to work unchanged.

Changes to surface:
- Update `.claude/skills/telegram/SKILL.md` to document the new `--project` flag on both `read` and `chats`, including the project freshness header format and per-line `[chat_name]` tagging in human output.
- No changes to `.mcp.json` or `mcp_servers/`.
- No bridge imports change.

Integration test: a smoke test confirming the documented invocation pattern (`valor-telegram read --project psyoptimal`) returns the expected union of messages and that the freshness header matches the documented format.

## Documentation

### Feature Documentation
- [ ] Update [`docs/features/telegram-messaging.md`](../features/telegram-messaging.md) with a new "Cross-Chat Project Reads" section documenting the `--project` flag on `read` and `chats`, the project freshness header format, per-line `[chat_name]` tagging, and `--limit` total-after-merge semantics.
- [ ] Update [`docs/features/telegram-history.md`](../features/telegram-history.md) if it documents resolver semantics — add `resolve_chats_by_project` alongside `resolve_chat_candidates` / `resolve_chat_id`.
- [ ] Update [`docs/features/README.md`](../features/README.md) index entry for telegram-messaging if the section heading changes.

### Skill Documentation
- [ ] Update [`.claude/skills/telegram/SKILL.md`](../../.claude/skills/telegram/SKILL.md) to document the `--project` flag and the project header format.
- [ ] Update [`CLAUDE.md`](../../CLAUDE.md) "Reading Telegram Messages" section with a `--project` example.

### Inline Documentation
- [ ] Docstring on `resolve_chats_by_project` documents the ordering guarantee (by `last_activity_ts` desc with `chat_id` tiebreak), the empty-list behavior for unknown projects, and that `project_key=None` chats are never returned.
- [ ] Help text on `--project` flag explicitly states "total-after-merge" `--limit` semantics.
- [ ] One-line comment on the mutex extension explaining `--strict` rejection.

## Success Criteria

- [ ] `valor-telegram read --project psyoptimal --limit 20` returns the most recent 20 messages across all chats with `project_key="psyoptimal"`, interleaved by timestamp desc.
- [ ] Each output line in human mode is tagged with `[chat_name]` (truncated to 25 chars if longer).
- [ ] A project header line precedes the messages: `[project=psyoptimal · N chats: name1, name2, name3 · last activity: T]`.
- [ ] `valor-telegram read --project psyoptimal --json` emits each message dict with `chat_id` and `chat_name` fields.
- [ ] `valor-telegram read --project unknown` exits 1 with a clear stderr message pointing the user at `chats --project`.
- [ ] `valor-telegram read --project psyoptimal --chat foo` (or any other read-target combination) errors at the argparse layer with a mutex-violation message.
- [ ] `valor-telegram read --project psyoptimal --strict` errors with `Error: --strict has no effect with --project; remove one of them.` and exits 1.
- [ ] `valor-telegram chats --project psyoptimal` returns only chats with `project_key="psyoptimal"`, sorted by recency.
- [ ] `valor-telegram chats --project psyoptimal --search "dev"` applies both filters.
- [ ] `valor-telegram chats --json` includes `project_key` in each chat dict.
- [ ] `valor-telegram read --chat "PsyOptimal" --limit 10` (single-chat path) is unchanged — same output, same JSON, same exit codes.
- [ ] All new and modified tests pass (`pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q`).
- [ ] Full test suite green (`/do-test`).
- [ ] Lint and format clean (`python -m ruff check . && python -m ruff format --check .`).
- [ ] `.claude/skills/telegram/SKILL.md` documents the new flag.
- [ ] `docs/features/telegram-messaging.md` documents the new cross-chat behavior.

## Team Orchestration

Solo builder + validator pair. One builder handles the Python + CLI work (tight cohesion — all edits touch `tools/telegram_history/__init__.py` and `tools/valor_telegram.py`). One documentarian updates docs once the CLI surface stabilizes. One validator confirms end-to-end before merge.

### Team Members

- **Builder (cross-chat)**
  - Name: cross-chat-builder
  - Role: Implement `resolve_chats_by_project`, extend `list_chats` with `project_key`, add `--project` to `read` and `chats`, implement merge-and-trim logic, project freshness header, per-line `[chat_name]` tagging.
  - Agent Type: builder
  - Resume: true

- **Validator (cross-chat)**
  - Name: cross-chat-validator
  - Role: Verify all new tests pass, mutex rules enforced, single-chat path unchanged, JSON enrichment correct, lint/format clean.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: cross-chat-documentarian
  - Role: Update `docs/features/telegram-messaging.md`, `docs/features/telegram-history.md`, `.claude/skills/telegram/SKILL.md`, and `CLAUDE.md` to reflect the new behavior.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Standard tier-1 agent types. No specialists needed.

## Step by Step Tasks

### 1. Implement `resolve_chats_by_project` and extend `list_chats`

- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: tests/tools/test_telegram_history.py::TestResolveChatsByProject (create)
- **Assigned To**: cross-chat-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `resolve_chats_by_project(project_key: str) -> list[ChatCandidate]` to `tools/telegram_history/__init__.py`. Reuse `_chat_to_candidate` and `_sort_candidates`. Narrow exception handling matching `resolve_chat_candidates`. Reject empty/whitespace `project_key` with `[]`.
- Add `project_key` field to each chat dict produced by `list_chats()`.
- Write the new test class with 5 cases (zero/one/many matches, `None` project_key skipped, Redis unavailable returns `[]` with logged warning).

### 2. Validate resolver

- **Task ID**: validate-resolver
- **Depends On**: build-resolver
- **Assigned To**: cross-chat-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/tools/test_telegram_history.py -q`.
- Confirm all new tests pass; confirm no regression in existing tests.
- Confirm `list_chats()` output includes `project_key` field.

### 3. Add `--project` flag to `read` subcommand

- **Task ID**: build-cli-read
- **Depends On**: validate-resolver
- **Validates**: tests/unit/test_valor_telegram.py::TestCmdReadFlags (extend), tests/unit/test_valor_telegram.py::TestCmdReadArgparseMutex (extend)
- **Assigned To**: cross-chat-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--project PROJECT_KEY` to the existing read-target mutex group at line 745. Update help text to mention "total-after-merge" `--limit` semantics.
- Extend the `flag_count` check at lines 309-325 to include `--project`. Reject `--project` + `--strict` with the documented error.
- Reject empty/whitespace `--project` (mirror the `--chat` / `--user` defensive check).
- Implement the merge-and-trim flow: call `resolve_chats_by_project`, fetch per-chat via `get_recent_messages`, inject `chat_id` + `chat_name` into each message dict, sort by timestamp desc, trim to `args.limit`.
- Emit project freshness header `[project=KEY · N chats: name1, name2, name3 · last activity: T]` for human mode (skipped under `--json`).
- For human output: render each message as `[timestamp] [chat_name (truncated to 25)] sender: content`. For JSON: emit list of message dicts with `chat_id` and `chat_name` fields.
- Zero-match path: stderr `No chats found for project '...'.` plus the `chats --project` hint, exit 1.
- Empty-results path: print project header then `No messages found for project '...'.`, exit 0.
- Write the new test cases in `TestCmdReadFlags` and `TestCmdReadArgparseMutex` per the Test Impact section.

### 4. Add `--project` filter to `chats` subcommand

- **Task ID**: build-cli-chats
- **Depends On**: validate-resolver (parallel-safe with build-cli-read)
- **Validates**: tests/unit/test_valor_telegram.py::TestCmdChatsProject (create)
- **Assigned To**: cross-chat-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--project PROJECT_KEY` argument to the `chats` subcommand parser (line 787-793).
- In `cmd_chats`, after `list_chats()` returns, apply the `--project` filter. Combinable with `--search` (both filters apply if both set).
- Update header line: `Known chats matching project 'KEY' (N):` (or combined with search if both flags are set).
- Reject empty/whitespace `--project`.
- Write `TestCmdChatsProject` tests per the Test Impact section.

### 5. Validate CLI

- **Task ID**: validate-cli
- **Depends On**: build-cli-read, build-cli-chats
- **Assigned To**: cross-chat-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_valor_telegram.py -q`.
- Run a manual smoke test: `valor-telegram read --project ai --limit 5` and `valor-telegram chats --project ai`.
- Confirm single-chat path is unchanged (`valor-telegram read --chat "Dev: Valor" --limit 5` produces identical output to a pre-change baseline).

### 6. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-cli
- **Assigned To**: cross-chat-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/telegram-messaging.md` with a "Cross-Chat Project Reads" section.
- Update `docs/features/telegram-history.md` with `resolve_chats_by_project` reference if applicable.
- Update `.claude/skills/telegram/SKILL.md` with the `--project` flag and project header format.
- Update `CLAUDE.md` "Reading Telegram Messages" with a `--project` example.

### 7. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cross-chat-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite (`/do-test`).
- Run lint and format checks.
- Walk Success Criteria checklist; report pass/fail for each item.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q` | exit code 0 |
| Full suite | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `--project` smoke (read) | `valor-telegram read --project ai --limit 5` | exit code 0, project header present |
| `--project` smoke (chats) | `valor-telegram chats --project ai` | exit code 0 |
| Mutex enforcement | `valor-telegram read --project ai --chat foo 2>&1 \| grep -q "mutually exclusive"` | exit code 0 |
| Strict rejection | `valor-telegram read --project ai --strict 2>&1 \| grep -q "no effect"` | exit code 0 |
| Skill doc updated | `grep -q -- '--project' .claude/skills/telegram/SKILL.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

The plan takes positions on the four design questions raised in the issue body. Each is recorded here for the supervisor to confirm or override before build kicks off:

1. **Per-line chat tagging vs section headers** (recommended: per-line tags). Rationale: chronological interleaving is the design intent of "stitching"; section headers would lose cross-chat ordering. Per-line `[chat_name]` tag (truncated to 25 chars) preserves interleaving while keeping attribution. **Confirm or override?**
2. **`--limit` semantics under `--project`** (recommended: total after merge). Rationale: matches what readers actually want for situational awareness ("give me the last 20 across the project"). Per-chat limit would require pagination state per chat for marginal benefit. **Confirm or override?**
3. **JSON output shape** (recommended: each message dict gets `chat_id` and `chat_name` only when `--project` is set; single-chat JSON unchanged). Rationale: avoids breaking existing consumers of single-chat JSON; clearly signals the cross-chat shape difference. **Confirm or override?**
4. **Mutex with `--strict`** (recommended: `--strict` + `--project` is an error with explicit message, not a silent ignore). Rationale: silently ignoring user-typed flags is a footgun; `--strict` is meaningless under `--project` because no name resolution happens. **Confirm or override?**

If all four are confirmed as proposed, the plan is ready for `/do-plan-critique`.
