---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-24
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

**Desired outcome:** A reader querying Telegram history either gets the right chat or gets an explicit disambiguation error with candidates ordered by recency. Read output carries an activity/freshness marker. One blessed CLI entry point (`valor-telegram`) handles groups and DMs; the orphan script and half-finished skill consolidation are cleaned up. Matching rules are coherent and punctuation-tolerant.

## Freshness Check

**Baseline commit:** `3ef5894b` (2026-04-24 plan time)
**Issue filed at:** 2026-04-24T10:04:29Z (same day as planning)
**Disposition:** Unchanged

No commits have landed on main between issue creation and this plan. All file:line references in the issue body were confirmed by direct code-read during recon (see issue Recon Summary). Sibling issues referenced ([#1065](https://github.com/tomcounsell/ai/issues/1065), [#1067](https://github.com/tomcounsell/ai/issues/1067), [#949](https://github.com/tomcounsell/ai/issues/949)) are all closed with resolutions consistent with their use here as precedent/context. No active plan in `docs/plans/` overlaps this area.

**Notes:** None — recon is fresh.

## Prior Art

Prior issue/PR searches surfaced adjacent work on Telegram tooling but nothing that previously attempted to fix chat-name resolution, so "Why Previous Fixes Failed" is omitted below.

- **Issue [#1065](https://github.com/tomcounsell/ai/issues/1065)** (closed): added a persona-level hard rule that Valor must search Telegram history before asking in group chats. The rule depends on the resolver under it — silent wrong-matches here undermine it in practice. This plan strengthens the foundation under #1065.
- **Issue [#1067](https://github.com/tomcounsell/ai/issues/1067)** (closed): shipped the `valor-email` CLI as an analog of `valor-telegram`. Useful as a design reference if we add/rename flags; the email CLI's `read --search` pattern is a template for `chats --search`.
- **Issue [#949](https://github.com/tomcounsell/ai/issues/949)** (closed): reply-to threads and implicit-context messages not carrying conversation context. Different layer (context propagation) than this one (identity resolution); no direct code overlap.
- **Issue [#919](https://github.com/tomcounsell/ai/issues/919)** (closed): reply-to routing split sessions. Different layer (session routing); not applicable here.
- **PR [#392](https://github.com/tomcounsell/ai/pull/392)** (merged): Popoto model relationship cleanup. Relevant only as context for how `Chat` model fields are indexed; the SortedField on `updated_at` is already in place from this era.

No prior work directly attempted to fix chat-name resolution. This is greenfield on that code path.

## Research

No relevant external findings — proceeding with codebase context. The work is purely internal Python (Popoto ORM, Telethon as a fallback we do not change), no new libraries or APIs introduced.

## Data Flow

End-to-end flow for `valor-telegram read --chat NAME`, with the current break points highlighted:

1. **Entry point** — `tools/valor_telegram.py:cmd_read` (line 181).
2. **Name → chat_id resolution** — `resolve_chat()` at line 53 delegates to `tools.telegram_history.resolve_chat_id` (line 940). **Current break:** returns the first arbitrary match across 3 stages, no ambiguity signal, no recency tiebreak. **New behavior:** collect all candidates surviving normalization+comparison at each stage, sort by `Chat.updated_at` desc, if >1 remain then raise `AmbiguousChatError` carrying the candidate list up to the CLI.
3. **DM fallback** — if `resolve_chat_id` returns None, falls back to `resolve_username` against `projects.json`. Unchanged by this plan *except* that we'll also accept an explicit `--user USERNAME` flag on `read` to enable folding in `scripts/get-telegram-message-history`.
4. **Message fetch** — `_fetch_messages_from_redis` (via `get_recent_messages` in `telegram_history`) reads the Redis message store for the resolved `chat_id`. **Current break:** output does not surface freshness. **New behavior:** include the `Chat.updated_at` timestamp in the CLI output header ("last activity: 2h ago").
5. **Telethon fallback** — `_fetch_from_telegram_api` at line 258 only triggers when Redis returns zero messages. Unchanged.
6. **Output** — formatted message list to stdout. **New behavior:** prepend a header line with chat_name, chat_id (for unambiguous reuse), and last-activity age.

The orphan path — `scripts/get-telegram-message-history "username" COUNT` — writes a request file to `data/message_query_request.json` and polls for a result. **New behavior:** this path is folded into `valor-telegram read --user USERNAME` and the script is removed.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:**
  - `resolve_chat_id(chat_name: str) -> str | None` gains a companion `resolve_chat_candidates(chat_name: str) -> list[Chat]` that returns all matches ordered by `updated_at` desc. `resolve_chat_id` is kept as a thin wrapper that returns the first candidate (preserving single-return API for programmatic callers), but it will raise `AmbiguousChatError` when >1 matches survive unless `allow_ambiguous=True` is passed.
  - `valor-telegram read` gains a new optional `--chat-id ID` flag (numeric bypass) and `--user USERNAME` flag (DM bypass, folds in orphan script).
  - `valor-telegram chats` gains an optional `--search PATTERN` flag.
- **Coupling:** slight decrease. Consolidating the orphan script removes a second identity space.
- **Data ownership:** unchanged. `Chat` model still owned by the bridge.
- **Reversibility:** high. All changes are additive at the API layer. The `resolve_chat_id` signature-preserving wrapper means existing internal callers continue to work; only their error surface expands.

## Appetite

**Size:** Medium

**Team:** Solo dev (builder + validator via Task pattern), 1 code-reviewer pass.

**Interactions:**
- PM check-ins: 1 — to confirm the ambiguity policy (error vs. pick-most-recent-with-warning) in Open Questions before implementation.
- Review rounds: 1 — single code review pass after validator confirms tests pass.

Rationale: 8 defects but tightly coupled around one function family and one model. Scope is bounded; defect 7 (cross-chat project-level stitching) is deferred to a No-Go. Interface changes are additive.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | `Chat` model reads/writes during tests |
| Popoto importable | `python -c "from models.chat import Chat"` | Model layer available |
| Existing test baseline passes | `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q` | Confirm no unrelated breakage before starting |

No prerequisites beyond normal dev environment.

## Solution

### Key Elements

- **`resolve_chat_candidates`** (new): returns all `Chat` records matching a normalized name, ordered by `updated_at` desc. Returns empty list for no match, single item for unique, multiple for ambiguous.
- **`AmbiguousChatError`** (new): exception carrying `candidates: list[Chat]`. Raised by `resolve_chat_id` when >1 candidate survives; callers format it into user-facing "did you mean" output.
- **Name normalization** (new): helper that lowercases, collapses whitespace, and strips a small, conservative punctuation set (`: - _ |`) from both sides of the comparison. Preserves emoji and non-ASCII; conservative by design.
- **`valor-telegram read` output header** (new): one-line activity marker derived from `Chat.updated_at`.
- **`--chat-id`, `--user` flags on `read`** (new): escape hatches for scripted/unambiguous use; `--user` also folds in the orphan script's sole use case.
- **`--search PATTERN` on `chats`** (new): substring filter, still sorted by recency.
- **Resolution-failure UX**: `cmd_read` catches `AmbiguousChatError` and prints a formatted candidate list with `chat_id` values for direct reuse. On zero-match, prints top-3 nearest candidates ordered by `updated_at` (same normalization, with a lower similarity bar).
- **Orphan script removal**: delete `scripts/get-telegram-message-history`, update any in-tree callers, update `telegram` skill doc.

### Flow

Happy path (unique match):
`valor-telegram read --chat "PM: PsyOptimal" --limit 20` → resolver finds 1 candidate → header `[PM: PsyOptimal · chat_id=-100123 · last activity: 3m ago]` → message list.

Ambiguous path:
`valor-telegram read --chat "PsyOptimal"` → resolver finds 2 candidates → exits non-zero with:
```
Ambiguous chat name "PsyOptimal". 2 candidates (most recent first):
  -100123  PM: PsyOptimal       last: 3m ago
  -100456  PsyOptimal           last: 2d ago
Re-run with --chat-id <id> or a more specific --chat string.
```

Zero-match path:
`valor-telegram read --chat "PM PsyOptimal"` (missing colon) → normalization collapses `: `→` ` on both sides → matches → same as happy path. If normalization still produces zero matches, prints top-3 did-you-mean candidates from full `Chat` list by recency.

Discovery path:
`valor-telegram chats --search "psy"` → returns all chats whose normalized name contains `psy`, sorted by `updated_at` desc.

### Technical Approach

- **Normalization** is applied symmetrically on both sides of every comparison. Keep it conservative to avoid false positives (no Levenshtein, no emoji stripping, no unicode folding). Implementation: one pure helper in `telegram_history/__init__.py`, unit-tested independently.
- **Candidate collection** changes the cascade semantics: at each of the three stages (exact → case-insensitive exact → substring), collect ALL hits before returning. Only move to the next stage if the current stage yields zero hits. This preserves the "prefer exact over fuzzy" ordering but within a stage never silently picks one of N.
- **Recency ranking** sorts candidates by `Chat.updated_at` desc. Popoto's `SortedField` already indexes this; we read all candidates for a stage (small N — there are hundreds of chats, not thousands) and sort in Python. If this becomes a performance concern in the future, we can use Popoto's sorted query API — not needed now.
- **`AmbiguousChatError`** carries the candidate list. Internal callers that set `allow_ambiguous=True` get the first (most-recent) candidate and a log warning; the CLI never sets this flag.
- **Freshness in output** reads `Chat.updated_at` once per read and formats as relative time (`format_timestamp` already exists in `valor_telegram.py`).
- **No new `last_sync_ts` field.** The original defect 3 suggested adding one, but recon confirmed `updated_at` is updated on every inbound message — which is what "freshness" practically means for a reader ("is this chat active or quiet?"). Adding a second timestamp field would require bridge changes and migration for marginal benefit. Surfacing `updated_at` closes the information gap without the schema churn.
- **Orphan script consolidation**: the script's sole feature is "read DM messages by username via bridge IPC." `valor-telegram read --user USERNAME` achieves the same outcome by routing through `resolve_username` (which `valor-telegram` already uses via `resolve_chat`) and the existing Redis-first/Telethon-fallback path. After migration, `scripts/get-telegram-message-history` is deleted.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `resolve_chat_id` at lines 978–979 has an `except Exception: return None` — this must be kept narrow (catch only the Popoto/Redis error we actually anticipate) or replaced with a logger.warning + re-raise in the new code. Test must assert the log/metric when the underlying query fails, not just that None is returned.
- [ ] New `resolve_chat_candidates` must have the same try/except discipline; test both the success path and the Redis-unavailable path.

### Empty/Invalid Input Handling
- [ ] Empty string `--chat ""` → clear error message, not silent None.
- [ ] Whitespace-only `--chat "   "` → treated same as empty.
- [ ] Non-ASCII / emoji-containing chat names → normalization preserves them; matching still works.
- [ ] Very long chat names (>200 chars) → no crash; either match or clean no-match.

### Error State Rendering
- [ ] Ambiguity error renders candidate list to stdout with exit code 1 (not silent selection).
- [ ] Zero-match "did you mean" renders top-3 to stdout with exit code 1.
- [ ] `--chat-id` with numeric input that has no messages → renders "no messages found for chat -100123" (clear), not a raw empty list.

## Test Impact

- [ ] `tests/tools/test_telegram_history.py` — UPDATE: existing `resolve_chat_id` tests (currently only exercise success paths implicitly via imports) need expansion to cover ambiguity, zero-match, normalization, and the new `resolve_chat_candidates` function. Add a fixture that seeds two `Chat` records with overlapping names (`PsyOptimal` + `PM: PsyOptimal`) and assert the ambiguity-detection path.
- [ ] `tests/unit/test_valor_telegram.py` — UPDATE: existing `TestResolveChat` tests pass through `resolve_chat_id` as a mock; add new tests that assert `cmd_read` handles the new `AmbiguousChatError` by printing candidates and exiting non-zero. Add tests for the new `--chat-id` and `--user` flags.
- [ ] `tests/unit/test_valor_telegram.py::TestResolveChat::test_returns_none_for_unknown` — UPDATE: current behavior returns None; new behavior should still return None (for legacy API preservation) but the CLI caller should print the "did you mean" candidates. Test both the function-level None return and the CLI-level did-you-mean output.
- [ ] `scripts/get-telegram-message-history` tests (if any) — DELETE when the script is removed. Audit `tests/` for references first.
- [ ] New test file `tests/unit/test_chat_name_normalization.py` (NEW): pure-function tests for the normalization helper covering whitespace collapse, punctuation stripping, case folding, emoji/non-ASCII preservation.

## Rabbit Holes

- **Levenshtein / fuzzy-matching libraries**: tempting for "did you mean" but adds a dependency and can produce surprising matches (e.g., "PsyOptimal" matching "OptimalPsy"). Stick to substring + normalization. The top-3 did-you-mean uses same normalization with a lower bar (shortest chat-name substring match).
- **Rewriting the `Chat` model schema**: adding `last_sync_ts`, `aliases`, `nicknames` — out of scope. This plan deliberately avoids schema churn; surface the existing `updated_at` instead.
- **Cross-chat project-level stitching** (defect 7): high value but a larger design (project_key indexing, unified read semantics, display formatting). Separate follow-up.
- **Telethon fallback enrichment**: making Telethon fallback trigger on stale-match suspicion (not just zero-match) — invites new failure modes and spec ambiguity; keep current fallback semantics.
- **Popoto query-layer optimization**: reading all chats and sorting in Python is fine at this scale (hundreds of chats); resist premature optimization.

## Risks

### Risk 1: Existing internal callers rely on current `resolve_chat_id` behavior (first-match silent)

**Impact:** If internal code paths call `resolve_chat_id` and expect a single chat_id even when ambiguous, they'll now hit `AmbiguousChatError`. Silent behavior change could propagate deep.

**Mitigation:** `resolve_chat_id` retains the `str | None` return signature by default. An `allow_ambiguous=True` kwarg (default False) preserves old behavior for callers that explicitly opt in — with a logger.warning recording the ambiguity. Grep for all internal callers before the PR; set `allow_ambiguous=True` on any that don't have a clean error-handling path, and file a follow-up to revisit them one by one. Callers in the bridge hot path are the highest-risk; treat each one explicitly.

### Risk 2: Normalization over-matches, silently resolving to the wrong chat

**Impact:** Normalization that's too aggressive (e.g., stripping `_`) could merge two legitimately-distinct chat names (`dev_valor` + `dev valor`).

**Mitigation:** Conservative set — lowercasing, whitespace collapse, and `: - _ |` stripping only. No unicode folding, no emoji stripping, no Levenshtein. Unit tests cover both the "should match despite punctuation" and "should NOT match when names are genuinely different after normalization" cases. The ambiguity detector is the safety net: if normalization produces >1 distinct `chat_id`, the user sees both and picks.

### Risk 3: Orphan script has callers we don't know about (scripts, cron, external docs)

**Impact:** Deleting `scripts/get-telegram-message-history` breaks unknown callers.

**Mitigation:** `grep -r "get-telegram-message-history"` across `scripts/`, `docs/`, `.claude/`, `tests/`, and recent git log before deletion. Migrate any found callers to `valor-telegram read --user`. Leave a one-commit deprecation window: replace the script body with a shim that prints a deprecation message and forwards to `valor-telegram read --user`, then delete in a follow-up. (Optional — if audit confirms no callers, straight delete is fine.)

### Risk 4: Popoto `Chat.query.all()` iteration order changes under Redis load

**Impact:** Tests pass in isolation but flake under realistic load; users hit non-deterministic ambiguity-error ordering.

**Mitigation:** Always sort candidates by `updated_at` desc before returning. Never rely on natural iteration order. Tests include an "order-independence" fixture (multiple runs, same expected top candidate).

## Race Conditions

No race conditions identified — `resolve_chat_id` is a synchronous read-only lookup on the Redis `Chat` index; the CLI is a short-lived process with no concurrent state mutation. Bridge writes to the `Chat` model may race with a concurrent CLI read, but the worst case is that the CLI sees a slightly-stale `updated_at` — which is exactly the freshness surface this plan exposes, not a correctness bug.

## No-Gos (Out of Scope)

- **Defect 7 (cross-chat project-level stitching)**: `valor-telegram read --project psyoptimal` unioning across all chats tagged with a project_key. High value for PM sessions but a separate design task (project_key semantics, multi-chat merge formatting, pagination across chats). File follow-up issue after this ships.
- **New `last_sync_ts` field on `Chat`**: superseded by the decision to surface existing `updated_at`. Not needed.
- **Fuzzy matching beyond normalization**: no Levenshtein, no trigram, no aliases table.
- **Persona/agent behavior changes**: [#1065](https://github.com/tomcounsell/ai/issues/1065) already handles the persona layer. This plan is infrastructure only.
- **Telethon live-query improvements**: fallback semantics unchanged; no new paths through `_fetch_from_telegram_api`.
- **Bridge-side changes**: `register_chat` is untouched. No schema migration.

## Update System

No update system changes required. This is a purely internal refactor of Python code and CLI surface. No new dependencies, no new config files, no new env vars. The `valor-telegram` CLI continues to be installed via the standard entry-point mechanism; the orphan script removal requires no update-skill support because it was never installed outside this repo.

## Agent Integration

No new agent integration required — the agent already invokes `valor-telegram read` via Bash (see `.claude/skills/telegram/SKILL.md`). This plan only changes the CLI output format and adds new flags; the existing invocation pattern continues to work.

Changes to surface:
- Update `.claude/skills/telegram/SKILL.md` to document the new `--chat-id`, `--user`, and `--search` flags and the new ambiguity-error format the agent may encounter.
- No changes to `.mcp.json` or `mcp_servers/`.
- No bridge imports change.

Integration test: a smoke test that the skill's documented invocation pattern (`valor-telegram read --chat NAME`) still works and that an ambiguous invocation surfaces the candidate list in a format the agent can parse.

## Documentation

### Feature Documentation
- [ ] Update [`docs/features/telegram-messaging.md`](../features/telegram-messaging.md) to reflect the new flags and error format.
- [ ] Update [`docs/features/telegram-history.md`](../features/telegram-history.md) if it documents `resolve_chat_id` semantics.
- [ ] Update [`docs/features/bridge-message-query.md`](../features/bridge-message-query.md) if it references `scripts/get-telegram-message-history`.
- [ ] Update [`docs/features/README.md`](../features/README.md) index if any file names change.

### Skill Documentation
- [ ] Update [`.claude/skills/telegram/SKILL.md`](../../.claude/skills/telegram/SKILL.md) to document new flags and the ambiguity-error format the agent should handle.
- [ ] Remove references to `scripts/get-telegram-message-history` from the `telegram` skill and any other skill that mentions it.
- [ ] Update [`CLAUDE.md`](../../CLAUDE.md) "Reading Telegram Messages" section to include the new flags.

### Inline Documentation
- [ ] Docstring on `resolve_chat_candidates` documents the ordering guarantee (by `updated_at` desc) and the normalization rules.
- [ ] Docstring on `AmbiguousChatError` documents the candidate list shape.
- [ ] One-line comment on the `allow_ambiguous` kwarg explaining why it exists (back-compat escape hatch).

## Success Criteria

- [ ] `valor-telegram read --chat "PsyOptimal"` with both `PsyOptimal` and `PM: PsyOptimal` in Redis prints an ambiguity error with both candidates, ordered by `updated_at` desc, and exits non-zero.
- [ ] `valor-telegram read --chat "PM PsyOptimal"` (missing colon) resolves to `PM: PsyOptimal` via normalization.
- [ ] `valor-telegram read --chat-id -100123` bypasses the matcher entirely and reads that chat unconditionally.
- [ ] `valor-telegram read --user lewis` reads DM messages from a whitelisted username (replacing the orphan script's behavior).
- [ ] `valor-telegram read` output includes a header line with chat name, chat_id, and last-activity age.
- [ ] `valor-telegram chats --search "psy"` returns only chats whose normalized name contains `psy`, sorted by recency desc.
- [ ] `scripts/get-telegram-message-history` is deleted; no remaining in-tree callers.
- [ ] `.claude/skills/telegram/SKILL.md` documents the new flags and ambiguity-error format.
- [ ] `docs/features/telegram-messaging.md` and related docs reflect the new behavior.
- [ ] All new and modified tests pass (`pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py tests/unit/test_chat_name_normalization.py -q`).
- [ ] Full test suite green (`/do-test`).
- [ ] Lint and format clean (`python -m ruff check . && python -m ruff format --check .`).
- [ ] `grep -r "get-telegram-message-history"` returns no matches outside git history.

## Team Orchestration

Simple solo builder + validator pattern. One builder handles the Python + CLI work (tight cohesion — all edits touch `tools/telegram_history/__init__.py`, `tools/valor_telegram.py`, and `models/chat.py` at most). One documentarian updates docs in parallel once the CLI surface is stable. One validator confirms end-to-end before merge.

### Team Members

- **Builder (core)**
  - Name: telegram-resolver-builder
  - Role: Implement normalization helper, `resolve_chat_candidates`, `AmbiguousChatError`, CLI flag wiring, freshness header, orphan script removal, and all new tests.
  - Agent Type: builder
  - Resume: true

- **Documentarian (docs)**
  - Name: telegram-resolver-docs
  - Role: Update `docs/features/telegram-messaging.md`, `docs/features/telegram-history.md`, `.claude/skills/telegram/SKILL.md`, and `CLAUDE.md` sections to reflect new flags and error format.
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: telegram-resolver-validator
  - Role: Verify all Success Criteria, run full test suite, confirm orphan script removal is complete, confirm docs match behavior.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard tier 1 agents — no specialists needed.

## Step by Step Tasks

### 1. Audit orphan-script callers
- **Task ID**: audit-callers
- **Depends On**: none
- **Validates**: grep output captured for review
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Run `grep -rln "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" .` and enumerate callers.
- For each caller, note whether it can migrate to `valor-telegram read --user` and record in a short checklist.

### 2. Implement normalization helper
- **Task ID**: build-normalization
- **Depends On**: none
- **Validates**: tests/unit/test_chat_name_normalization.py (create)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add pure-function `_normalize_chat_name(s: str) -> str` in `tools/telegram_history/__init__.py`.
- Cover: lowercase, whitespace collapse, strip `: - _ |` from both sides, preserve non-ASCII/emoji.
- Write unit tests covering the listed transforms and a "does NOT over-match" case (e.g., `dev_valor` vs `dev valor` must still produce equal normalized forms — decide explicitly per open question Q2).

### 3. Implement candidate resolver and ambiguity error
- **Task ID**: build-candidates
- **Depends On**: build-normalization
- **Validates**: tests/tools/test_telegram_history.py (expanded)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `AmbiguousChatError(candidates: list[Chat])` exception class.
- Add `resolve_chat_candidates(chat_name: str) -> list[Chat]` — runs the 3-stage cascade, collects ALL matches per stage, only advances to next stage on zero hits, returns candidates sorted by `updated_at` desc.
- Refactor `resolve_chat_id(chat_name: str, allow_ambiguous: bool = False) -> str | None` to delegate to `resolve_chat_candidates`. Raise `AmbiguousChatError` when >1 and not `allow_ambiguous`. Return first when `allow_ambiguous=True` with a logger.warning.
- Tests: ambiguity with 2 candidates, ambiguity with 3 candidates, zero-match, unique match, ordering by recency, `allow_ambiguous=True` returning most-recent and logging warning.

### 4. Audit internal callers of resolve_chat_id
- **Task ID**: audit-internal-callers
- **Depends On**: build-candidates
- **Validates**: list of caller sites recorded
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- `grep -rln "resolve_chat_id" --include="*.py" .` — inventory every call site.
- For each, decide: (a) let it raise `AmbiguousChatError` (caller has a clean error path), or (b) set `allow_ambiguous=True` with a logger.warning (caller is in a hot path that can't reasonably handle ambiguity right now).
- Record the disposition for each site in a comment in the PR body.

### 5. Wire CLI read command
- **Task ID**: build-cli-read
- **Depends On**: build-candidates, audit-internal-callers
- **Validates**: tests/unit/test_valor_telegram.py (expanded)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--chat-id` flag to `read` subcommand (numeric passthrough, bypasses matcher).
- Add `--user USERNAME` flag (forces DM path via `resolve_username`).
- In `cmd_read`, catch `AmbiguousChatError` and format candidates to stdout, exit 1.
- On zero-match (`resolve_chat` returns None AND no `--chat-id`/`--user`), print top-3 did-you-mean candidates from full Chat list sorted by `updated_at` desc, exit 1.
- Prepend output with header: `[chat_name · chat_id=N · last activity: T]` using `Chat.updated_at` and existing `format_timestamp`.
- Tests: new flag behaviors, ambiguity handling in CLI, zero-match did-you-mean, freshness header.

### 6. Wire CLI chats search
- **Task ID**: build-cli-chats-search
- **Depends On**: build-normalization
- **Validates**: tests/unit/test_valor_telegram.py::TestCmdChats
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--search PATTERN` flag to `chats` subcommand.
- Apply normalized substring filter; keep existing sort by `last_message` desc.
- Tests: search finds single match, multiple matches, zero-match returns empty list cleanly.

### 7. Consolidate orphan script
- **Task ID**: consolidate-orphan
- **Depends On**: build-cli-read, audit-callers
- **Validates**: grep returns no matches outside git history
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Migrate any callers found in audit-callers to `valor-telegram read --user USERNAME`.
- Delete `scripts/get-telegram-message-history` and any associated test files.
- `grep -r "get-telegram-message-history"` must return zero matches in tracked files.

### 8. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-cli-read, build-cli-chats-search, consolidate-orphan
- **Assigned To**: telegram-resolver-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/telegram-messaging.md` with new flags and error format.
- Update `docs/features/telegram-history.md` for resolver semantics.
- Update `.claude/skills/telegram/SKILL.md` with new flags and ambiguity-error format.
- Update `CLAUDE.md` "Reading Telegram Messages" section.
- Remove references to `scripts/get-telegram-message-history` from all docs.

### 9. Final validation
- **Task ID**: validate-all
- **Depends On**: build-cli-read, build-cli-chats-search, consolidate-orphan, document-feature
- **Assigned To**: telegram-resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py tests/unit/test_chat_name_normalization.py -v`.
- Run full suite via `/do-test`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Run `grep -rln "get-telegram-message-history" .` and confirm zero hits.
- Walk the Success Criteria list and confirm each item.
- Generate pass/fail report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py tests/unit/test_chat_name_normalization.py -q` | exit code 0 |
| Full suite green | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Orphan script removed | `grep -rln "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" .` | exit code 1 |
| Ambiguity error format correct | `python -c "from tools.telegram_history import AmbiguousChatError; e = AmbiguousChatError([]); assert hasattr(e, 'candidates')"` | exit code 0 |
| New CLI flags documented | `grep -l "chat-id\|--user\|--search" .claude/skills/telegram/SKILL.md` | output contains `.claude/skills/telegram/SKILL.md` |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Ambiguity policy**: should the default CLI behavior on `>1 candidate` be (a) hard error with candidate list and exit 1, or (b) pick most-recent-with-warning and exit 0? This plan chose (a) on the argument that silent-picks were the original bug. But scripted callers may prefer (b) — especially cron jobs reading "the Dev chat." If (b) is preferred, the current design still supports it via `--allow-ambiguous` as an explicit opt-in flag. Confirm.

2. **Normalization scope — underscore handling**: should `_` be stripped? `dev_valor` vs `dev valor` are likely the same chat in intent but `backup_logs` vs `backup logs` may not be. This plan strips `_` by default; if that feels aggressive, we can pull it from the normalization set. (Whitespace, case, and `: -` are clearly in; `|` is borderline; `_` is the judgment call.)

3. **Orphan script: delete vs. shim**: should `scripts/get-telegram-message-history` be (a) deleted outright after callers migrate, or (b) replaced with a one-line shim that prints a deprecation warning and forwards to `valor-telegram read --user`? This plan defaults to (a) if the audit finds no callers, (b) otherwise. Confirm if preference differs.

4. **Defect 7 follow-up issue**: should we file the follow-up issue for cross-chat project-level stitching (`--project` flag) as part of this plan's completion, or wait to see if the need still feels pressing after defects 1–3 land? This plan defers to the build step.

5. **`allow_ambiguous=True` audit rigor**: the plan treats `allow_ambiguous=True` on internal callers as a temporary escape hatch. Should those sites also get tracking TODOs filed as issues so they don't linger silently, or is the PR-body disposition list sufficient?
