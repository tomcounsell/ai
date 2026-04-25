---
status: docs_complete
type: bug
appetite: Medium
owner: Valor
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1163
last_comment_id:
revision_applied: true
revision_date: 2026-04-24
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

## Revision Notes (post-critique, 2026-04-24)

This revision incorporates critique findings (verdict: NEEDS REVISION). Specifically:

1. **Call-site recon completed up front.** The only in-tree caller of `resolve_chat_id` outside the test suite is `tools/valor_telegram.py:61` (via the thin `resolve_chat` wrapper at line 53) — verified by `grep -rn "resolve_chat_id" --include="*.py" .`. The plan previously spoke about "internal callers deep in the bridge hot path"; recon shows that concern is overblown — there is effectively ONE caller, and it owns the CLI-error-formatting path. This reshapes the risk profile and is now reflected in Task ordering (audit moved ahead of the signature change) and in the Risks section.
2. **Open Questions resolved.** Q1 (ambiguity policy), Q2 (underscore handling), Q3 (orphan script handling), Q5 (audit rigor) are now resolved in-plan. Q4 (defect 7 follow-up timing) is moved to a No-Go with a concrete follow-up-issue task after merge.
3. **`AmbiguousChatError` payload tightened.** Previously carried raw `list[Chat]` (Popoto model instances). Now carries a frozen dataclass `ChatCandidate(chat_id: str, chat_name: str, last_activity_ts: float | None)` — serializable, decoupled from Popoto field churn.
4. **Test layout simplified.** No new `test_chat_name_normalization.py` file. Normalization tests join `tests/tools/test_telegram_history.py` alongside the resolver tests they exercise. One file, one failure-domain.
5. **Failure-mode ordering.** The current `except Exception: return None` at `tools/telegram_history/__init__.py:978` is called out explicitly in Task 3 as "replace with `except RedisError, PopotoError: logger.warning(...); return None`" — the new code will not inherit the bare-except smell.

The issues below drove these fixes; the sections that follow have been updated to match.

## Hotfix Revision Notes (2026-04-24, post-ship)

The prior revision landed an ambiguity policy (hard error + exit 1) that contradicted the user's explicit Q2 lean. This hotfix restores the original decision. All other post-critique changes from the 2026-04-24 revision (ChatCandidate dataclass, caller recon findings, narrow exception handling, task reorder, test layout simplification) are preserved.

**What changed:**

1. **Ambiguity policy: pick-most-recent + stderr warning is the default** (Q2 = option b, per user). `resolve_chat_id` no longer raises by default when >1 candidate survives. Instead it:
   - Returns the `chat_id` of the most-recently-updated candidate.
   - Emits a machine-parseable warning to `stderr` listing ALL candidates with `chat_id`, `chat_name`, and `last activity: X ago` for each — chosen AND non-chosen.
   - Deterministic tiebreak on `chat_id` when two candidates share `updated_at`, so the same query always returns the same result.
2. **`--strict` flag opts into hard error** on the CLI, flipping the default. Callers that want the previous "exit 1 with candidate list" behavior pass `--strict`. Scripted callers that can't parse stderr use `--strict` (hard failure) or `--chat-id NUMBER` (unambiguous bypass).
3. **`AmbiguousChatError` remains** but is only raised when `strict=True`. Still carries `list[ChatCandidate]`. Same dataclass shape as before.
4. **Signature rename:** `resolve_chat_id(chat_name: str, *, allow_ambiguous: bool = False)` → `resolve_chat_id(chat_name: str, *, strict: bool = False)`. The old kwarg name inverted in meaning under the new default and was confusing; renaming makes the semantics obvious. Since recon confirms exactly one in-tree caller (`tools/valor_telegram.py:61`), the rename has no blast radius.
5. **Defensive guard:** if the most-recent-candidate sort produces a result where the "chosen" candidate is NOT the most-recently-updated (shouldn't be possible given the ordering rule, but defense-in-depth), the function raises `AmbiguousChatError` unconditionally to fail loud rather than silently return a wrong answer.
6. **No change to zero-match, single-match, or non-ambiguity paths.**

**Why:** the silent wrong-match was the bug, but "wrong match" is different from "most-recent match." Under the new default, the result is always the most-recently-active candidate (per defect 8's recency-ranked intent), AND the warning carries the full candidate list — so the failure mode the user hit (agent confidently reports stale group-chat data while fresher DM chat goes unread) cannot recur: the fresher candidate is always chosen by default, and the warning makes the alternative visible. `--strict` is the escape hatch for contexts where "wrong answer with warning" is unacceptable.

The sections below have been updated to match. The pre-hotfix "hard error" rationale lives only in git history — no shadow text left in this plan.

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
2. **Name → chat_id resolution** — `resolve_chat()` at line 53 delegates to `tools.telegram_history.resolve_chat_id` (line 940). **Current break:** returns the first arbitrary match across 3 stages, no ambiguity signal, no recency tiebreak. **New behavior:** collect all candidates surviving normalization+comparison at each stage, sort by `Chat.updated_at` desc (tiebreak on `chat_id`). If >1 remain: default path returns the most-recent candidate's `chat_id` and emits a machine-parseable stderr warning listing all candidates; `strict=True` raises `AmbiguousChatError` instead.
3. **DM fallback** — if `resolve_chat_id` returns None, falls back to `resolve_username` against `projects.json`. Unchanged by this plan *except* that we'll also accept an explicit `--user USERNAME` flag on `read` to enable folding in `scripts/get-telegram-message-history`.
4. **Message fetch** — `_fetch_messages_from_redis` (via `get_recent_messages` in `telegram_history`) reads the Redis message store for the resolved `chat_id`. **Current break:** output does not surface freshness. **New behavior:** include the `Chat.updated_at` timestamp in the CLI output header ("last activity: 2h ago").
5. **Telethon fallback** — `_fetch_from_telegram_api` at line 258 only triggers when Redis returns zero messages. Unchanged.
6. **Output** — formatted message list to stdout. **New behavior:** prepend a header line with chat_name, chat_id (for unambiguous reuse), and last-activity age.

The orphan path — `scripts/get-telegram-message-history "username" COUNT` — writes a request file to `data/message_query_request.json` and polls for a result. **New behavior:** this path is folded into `valor-telegram read --user USERNAME` and the script is removed.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:**
  - New `ChatCandidate` dataclass in `tools/telegram_history/__init__.py`: `@dataclass(frozen=True) class ChatCandidate: chat_id: str; chat_name: str; last_activity_ts: float | None`. Serializable, decoupled from Popoto schema.
  - New `resolve_chat_candidates(chat_name: str) -> list[ChatCandidate]` returns all matches ordered by `last_activity_ts` desc (None sorts last).
  - New `AmbiguousChatError(candidates: list[ChatCandidate])` exception class. Raised only when `strict=True`.
  - `resolve_chat_id(chat_name: str, *, strict: bool = False) -> str | None` signature adds `strict` keyword-only kwarg (default False). Default behavior on ambiguity: return the most-recently-updated candidate's `chat_id` and emit a `logger.warning` with the full candidate list. `strict=True`: raise `AmbiguousChatError` instead. Return type `str | None` preserved across all paths (None on zero-match).
  - `valor-telegram read` gains optional `--chat-id ID` flag (numeric bypass), `--user USERNAME` flag (DM bypass, folds in orphan script), and `--strict` flag (opt into hard error on ambiguity).
  - `valor-telegram chats` gains optional `--search PATTERN` flag.
- **Coupling:** slight decrease. Consolidating the orphan script removes a second identity space. `AmbiguousChatError` carries plain dataclass — callers that format it are not coupled to Popoto.
- **Data ownership:** unchanged. `Chat` model still owned by the bridge.
- **Reversibility:** high. All changes are additive at the API layer. The `resolve_chat_id` signature extension is keyword-only (`strict=False` default) — the single caller site (`tools/valor_telegram.py:61`) is updated in the same PR to pass `strict=True` when `--strict` is on the CLI, and to wrap with a `try/except AmbiguousChatError` block for that path only. Rollback is a single revert.
- **Behavioral change risk:** the single in-tree caller of `resolve_chat_id` is `tools/valor_telegram.py:61`. Recon (`grep -rn "resolve_chat_id" --include="*.py" .`) confirms zero other non-test callers. The "silent behavior change in bridge hot path" scenario does not apply here — the bridge registers chats but does not *resolve* them by name.

## Appetite

**Size:** Medium

**Team:** Solo dev (builder + validator via Task pattern), 1 code-reviewer pass.

**Interactions:**
- PM check-ins: 0 — all Open Questions resolved (see Hotfix Revision Notes).
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

- **`ChatCandidate`** (new): `@dataclass(frozen=True) class ChatCandidate: chat_id: str; chat_name: str; last_activity_ts: float | None`. Plain dataclass; not tied to Popoto schema.
- **`resolve_chat_candidates`** (new): returns all matches as `list[ChatCandidate]`, ordered by `last_activity_ts` desc (None sorts last). Empty list for no match, single item for unique, multiple for ambiguous.
- **`AmbiguousChatError`** (new): exception carrying `candidates: list[ChatCandidate]`. Raised by `resolve_chat_id` only when `strict=True`. Default path does not raise.
- **Name normalization** (new): helper that lowercases, collapses whitespace, and strips a small, conservative punctuation set (`: - | _`) from both sides of the comparison. Preserves emoji and non-ASCII; conservative by design. Underscore IS included (resolves Q2; rationale in Technical Approach).
- **`valor-telegram read` output header** (new): one-line activity marker derived from `Chat.updated_at`.
- **`--chat-id`, `--user`, `--strict` flags on `read`** (new): escape hatches. `--chat-id` bypasses the matcher entirely. `--user` folds in the orphan script's DM-only path. `--strict` flips ambiguity handling from warn-and-pick-most-recent to raise-and-exit-1.
- **`--search PATTERN` on `chats`** (new): substring filter, still sorted by recency.
- **Resolution-failure UX**: on ambiguity, the default CLI path prints a stderr warning listing all candidates and proceeds with the most-recent candidate's messages (exit 0). `--strict` converts ambiguity into a stdout error + exit 1. On zero-match, `cmd_read` prints top-3 "did you mean" candidates from full `Chat` list ordered by `updated_at` (exit 1).
- **Orphan script removal**: delete `scripts/get-telegram-message-history`, update any in-tree callers, update `telegram` skill doc.

### Flow

Happy path (unique match):
`valor-telegram read --chat "PM: PsyOptimal" --limit 20` → resolver finds 1 candidate → header `[PM: PsyOptimal · chat_id=-100123 · last activity: 3m ago]` → message list.

Ambiguous path (default):
`valor-telegram read --chat "PsyOptimal"` → resolver finds 2 candidates → emits to stderr:
```
WARN: ambiguous chat "PsyOptimal" — 2 candidates, chose most recent:
  chose    -100123  PM: PsyOptimal       last: 3m ago
  also     -100456  PsyOptimal           last: 2d ago
Use --chat-id <id> or --strict to change behavior.
```
…then continues on stdout with the `-100123` chat's header + messages, exit 0.

Ambiguous path (`--strict`):
`valor-telegram read --chat "PsyOptimal" --strict` → same resolution but raises `AmbiguousChatError` which `cmd_read` catches; prints an error block to stdout and exits 1:
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

- **Normalization** is applied symmetrically on both sides of every comparison. Keep it conservative to avoid false positives (no Levenshtein, no emoji stripping, no unicode folding). Implementation: one pure helper `_normalize_chat_name(s: str) -> str` in `telegram_history/__init__.py`, unit-tested in `tests/tools/test_telegram_history.py`.
  - **Underscore handling (Q2 resolved):** `_` IS stripped. Rationale: in practice, chat names with underscores (`dev_valor`, `backup_logs`) mirror slug/channel conventions; users typing them interactively are likely to type space or nothing. Name collisions where `dev_valor` and `dev valor` mean *different* chats are vanishingly rare in this workspace — and if they ever occur, the ambiguity detector is the safety net: both would show up in the candidate list for the user to disambiguate. Being conservative on `_` would trade a real UX win for a hypothetical edge case.
- **Candidate collection** changes the cascade semantics: at each of the three stages (exact → case-insensitive exact → substring), collect ALL hits before returning. Only move to the next stage if the current stage yields zero hits. This preserves the "prefer exact over fuzzy" ordering but within a stage never silently picks one of N. Each hit is projected to a `ChatCandidate` at collection time — `Chat` model instances never leak past this boundary.
- **Recency ranking** sorts `ChatCandidate`s by `last_activity_ts` desc with `None` sorting last (i.e., chats that have never been updated). Popoto's `SortedField` on `Chat.updated_at` already indexes this; we read all candidates for a stage (small N — there are hundreds of chats, not thousands) and sort in Python. If this becomes a performance concern in the future, we can use Popoto's sorted query API — not needed now.
- **`AmbiguousChatError`** carries `list[ChatCandidate]`. Raised only in `strict=True` mode — the CLI sets this kwarg when `--strict` is passed. Default (non-strict) callers never see it; they get the most-recent candidate's `chat_id` back from the function plus a `logger.warning` with the full candidate list (rendered to stderr by default Python logging config).
- **Ambiguity policy (Q1/Q2 resolved):** default = pick-most-recent + stderr warning listing all candidates (chosen AND non-chosen); opt into hard error via `--strict`. Rationale: the silent wrong-match was the bug, but "wrong" specifically meant "the matcher silently picked a less-active chat while fresher messages lived elsewhere." Picking-most-recent inverts that failure mode — the active chat wins by default — and the warning makes the alternative visible for audit. `--strict` is available for scripted callers that can't parse stderr and need non-zero exit on ambiguity; `--chat-id NUMBER` remains the unambiguous bypass for both modes.
- **Defensive guard:** `resolve_chat_id` asserts that the chosen candidate is the one with the maximum `last_activity_ts` (with `chat_id` tiebreak). If this invariant is ever violated — a bug in the sort or a race — the function raises `AmbiguousChatError` unconditionally regardless of `strict`, to fail loud rather than silently return the wrong answer.
- **Tiebreak:** when two candidates share `last_activity_ts` (including both being `None`), deterministic secondary sort on `chat_id` ensures the same query returns the same result across runs.
- **Freshness in output** reads `Chat.updated_at` once per read and formats as relative time (`format_timestamp` already exists in `valor_telegram.py`). Header format: `[chat_name · chat_id=N · last activity: T]`. If `updated_at` is None (chat registered but no messages yet), format as `last activity: never`.
- **No new `last_sync_ts` field.** The original defect 3 suggested adding one, but recon confirmed `updated_at` is updated on every inbound message — which is what "freshness" practically means for a reader ("is this chat active or quiet?"). Adding a second timestamp field would require bridge changes and migration for marginal benefit. Surfacing `updated_at` closes the information gap without the schema churn.
- **Narrow exception handling.** The current `except Exception: return None` at `tools/telegram_history/__init__.py:978` is replaced by an explicit `except (redis.RedisError, popoto.errors.PopotoError): logger.warning(...); return None` (or equivalent — exact exception classes confirmed at implementation time). A test asserts the log is emitted when Redis is unavailable.
- **Orphan script consolidation (Q3 resolved):** straight delete if audit finds zero callers, single-commit deprecation shim otherwise. The script's sole feature is "read DM messages by username via bridge IPC." `valor-telegram read --user USERNAME` achieves the same outcome by routing through `resolve_username` and the existing Redis-first/Telethon-fallback path. After migration, `scripts/get-telegram-message-history` is deleted.

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
- [ ] Ambiguity (default) renders warning to stderr listing chosen + all alternate candidates, proceeds with most-recent chat messages on stdout, exit 0.
- [ ] Ambiguity (`--strict`) renders candidate list to stdout with exit code 1, does not print any chat messages.
- [ ] Zero-match "did you mean" renders top-3 to stdout with exit code 1.
- [ ] `--chat-id` with numeric input that has no messages → renders "no messages found for chat -100123" (clear), not a raw empty list.
- [ ] Defensive guard: assertion failure on sort-invariant violation raises `AmbiguousChatError` regardless of `strict` — test with a monkeypatched sort.

## Test Impact

- [ ] `tests/tools/test_telegram_history.py` — UPDATE: add tests for `_normalize_chat_name` (whitespace collapse, punctuation stripping including `_`, case folding, emoji/non-ASCII preservation, empty and whitespace-only input), `resolve_chat_candidates` (zero/one/many matches, ordering by `last_activity_ts` desc with None-last, deterministic `chat_id` tiebreak, stage cascade exact→ci→substring), and `resolve_chat_id` (default returns most-recent `chat_id` and emits `logger.warning` on >1 candidate via `caplog`, `strict=True` raises `AmbiguousChatError` on >1 candidate, `None` on zero, narrow exception handling on Redis error, defensive guard raises on monkeypatched invariant violation). Add a fixture that seeds two `Chat` records with overlapping names (`PsyOptimal` + `PM: PsyOptimal`) and assert both default and strict paths.
- [ ] `tests/unit/test_valor_telegram.py` — UPDATE: existing `TestResolveChat` tests pass through `resolve_chat_id` as a mock; add new tests that assert (a) default `cmd_read` on ambiguity prints the stderr warning block (chosen + also entries), continues with message output on stdout, exits 0; (b) `--strict` `cmd_read` on ambiguity prints the stdout error block, does not print messages, exits 1; (c) the stderr warning format is greppable (assert via a regex fixture). Add tests for the new `--chat-id`, `--user`, `--strict`, and `chats --search` flags. Add a test for the freshness header format (`last activity: Xh ago` vs `last activity: never`).
- [ ] `tests/unit/test_valor_telegram.py::TestResolveChat::test_returns_none_for_unknown` — UPDATE: current behavior returns None; new behavior should still return None (for legacy API preservation) but the CLI caller should print the "did you mean" candidates. Test both the function-level None return and the CLI-level did-you-mean output.
- [ ] `scripts/get-telegram-message-history` tests (if any) — DELETE when the script is removed. Audit `tests/` for references first (`grep -rln "get-telegram-message-history" tests/`).

## Rabbit Holes

- **Levenshtein / fuzzy-matching libraries**: tempting for "did you mean" but adds a dependency and can produce surprising matches (e.g., "PsyOptimal" matching "OptimalPsy"). Stick to substring + normalization. The top-3 did-you-mean uses same normalization with a lower bar (shortest chat-name substring match).
- **Rewriting the `Chat` model schema**: adding `last_sync_ts`, `aliases`, `nicknames` — out of scope. This plan deliberately avoids schema churn; surface the existing `updated_at` instead.
- **Cross-chat project-level stitching** (defect 7): high value but a larger design (project_key indexing, unified read semantics, display formatting). Separate follow-up.
- **Telethon fallback enrichment**: making Telethon fallback trigger on stale-match suspicion (not just zero-match) — invites new failure modes and spec ambiguity; keep current fallback semantics.
- **Popoto query-layer optimization**: reading all chats and sorting in Python is fine at this scale (hundreds of chats); resist premature optimization.

## Risks

### Risk 1: Existing internal callers rely on current `resolve_chat_id` behavior (first-match silent)

**Impact:** If internal code paths call `resolve_chat_id` and expect a single chat_id even when ambiguous, they'll now hit `AmbiguousChatError`. Silent behavior change could propagate deep.

**Status:** Recon completed. The only in-tree caller outside the test suite is `tools/valor_telegram.py:61` (via `resolve_chat` wrapper at line 53). The bridge does NOT call `resolve_chat_id` — it calls `register_chat` (the writer). No hot-path caller exists.

**Mitigation:** `resolve_chat_id` retains the `str | None` return signature under the default (non-strict) path — the existing behavior of "never raises, always returns a chat_id or None" is preserved by the most-recent-with-warning default. The only call site that observes a behavior change is one where ambiguity previously returned an arbitrary `chat_id` silently; it now returns the most-recent one and logs a warning. Callers that want strict behavior opt in via the new keyword-only `strict=True` kwarg. Task 1 (audit-callers) re-confirms at build time — if a new caller has appeared since recon, it's recorded and handled explicitly. The single caller (`valor_telegram.py:61`) is updated in the same PR to pass `strict=True` through to the function when the CLI `--strict` flag is set, and to catch `AmbiguousChatError` only in that path.

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

- **Defect 7 (cross-chat project-level stitching)**: `valor-telegram read --project psyoptimal` unioning across all chats tagged with a project_key. High value for PM sessions but a separate design task (project_key semantics, multi-chat merge formatting, pagination across chats). Task 8 (validate-all) files a follow-up issue titled "Telegram read: cross-chat project-level stitching (defect 7 of #1163)" so it lands in the backlog instead of being forgotten.
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
- [x] Update [`docs/features/telegram-messaging.md`](../features/telegram-messaging.md) to reflect the new flags and error format.
- [x] Update [`docs/features/telegram-history.md`](../features/telegram-history.md) if it documents `resolve_chat_id` semantics.
- [x] Update [`docs/features/bridge-message-query.md`](../features/bridge-message-query.md) if it references `scripts/get-telegram-message-history`.
- [x] Update [`docs/features/README.md`](../features/README.md) index if any file names change.

### Skill Documentation
- [x] Update [`.claude/skills/telegram/SKILL.md`](../../.claude/skills/telegram/SKILL.md) to document new flags and the ambiguity-error format the agent should handle.
- [x] Remove references to `scripts/get-telegram-message-history` from the `telegram` skill and any other skill that mentions it.
- [x] Update [`CLAUDE.md`](../../CLAUDE.md) "Reading Telegram Messages" section to include the new flags.

### Inline Documentation
- [x] Docstring on `resolve_chat_candidates` documents the ordering guarantee (by `updated_at` desc) and the normalization rules.
- [x] Docstring on `AmbiguousChatError` documents the candidate list shape and that it's raised only under `strict=True` or defensive-guard violation.
- [x] One-line comment on the `strict` kwarg explaining why it exists (opt-in hard error for scripted callers).

## Success Criteria

- [ ] `valor-telegram read --chat "PsyOptimal"` with both `PsyOptimal` and `PM: PsyOptimal` in Redis prints a stderr ambiguity warning listing both candidates ordered by `updated_at` desc, then proceeds with the most-recent chat's messages on stdout, exit 0.
- [ ] `valor-telegram read --chat "PsyOptimal" --strict` with the same data prints an ambiguity error block to stdout listing both candidates, exits 1, prints no messages.
- [ ] `valor-telegram read --chat "PM PsyOptimal"` (missing colon) resolves to `PM: PsyOptimal` via normalization.
- [ ] `valor-telegram read --chat-id -100123` bypasses the matcher entirely and reads that chat unconditionally.
- [ ] `valor-telegram read --user lewis` reads DM messages from a whitelisted username (replacing the orphan script's behavior).
- [ ] `valor-telegram read` output includes a header line with chat name, chat_id, and last-activity age.
- [ ] `valor-telegram chats --search "psy"` returns only chats whose normalized name contains `psy`, sorted by recency desc.
- [ ] `scripts/get-telegram-message-history` is deleted; no remaining in-tree callers.
- [ ] `.claude/skills/telegram/SKILL.md` documents the new flags and ambiguity-error format.
- [ ] `docs/features/telegram-messaging.md` and related docs reflect the new behavior.
- [ ] All new and modified tests pass (`pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q`).
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

### 1. Audit orphan-script and resolve_chat_id callers
- **Task ID**: audit-callers
- **Depends On**: none
- **Validates**: grep output captured for review; caller disposition checklist produced
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Run `grep -rln "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" .` and enumerate callers.
- Run `grep -rln "resolve_chat_id" --include="*.py" .` and enumerate every call site (excluding tests and the definition itself).
- Produce a checklist: for `get-telegram-message-history` callers, note whether each migrates to `valor-telegram read --user`. For `resolve_chat_id` callers, note whether each (a) accepts the new default (pick-most-recent + logged warning) or (b) needs `strict=True` for hard-error semantics. (Recon already establishes the only in-tree caller is `tools/valor_telegram.py:61`; this task confirms and catches anything new.)
- The checklist output lives in the PR description for reviewer visibility.

### 2. Implement normalization helper
- **Task ID**: build-normalization
- **Depends On**: none
- **Validates**: tests/tools/test_telegram_history.py::test_normalize_chat_name_* (create within existing file)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add pure-function `_normalize_chat_name(s: str) -> str` in `tools/telegram_history/__init__.py`.
- Cover: lowercase, whitespace collapse (multiple spaces → single space), strip `: - | _` from both sides, preserve non-ASCII/emoji.
- Edge cases: empty string → `""`; whitespace-only → `""`; all-punctuation `":::"` → `""`.
- Write tests in the existing `tests/tools/test_telegram_history.py` (no new file) covering the listed transforms and an over-match sanity case: `dev_valor` and `dev valor` MUST normalize equal (Q2 policy decision; the ambiguity detector is the safety net).

### 3. Implement ChatCandidate, candidate resolver, and ambiguity error
- **Task ID**: build-candidates
- **Depends On**: build-normalization
- **Validates**: tests/tools/test_telegram_history.py (expanded)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `@dataclass(frozen=True) class ChatCandidate: chat_id: str; chat_name: str; last_activity_ts: float | None` to `tools/telegram_history/__init__.py`.
- Add `AmbiguousChatError(Exception)` with `__init__(self, candidates: list[ChatCandidate])` storing `self.candidates`.
- Add `resolve_chat_candidates(chat_name: str) -> list[ChatCandidate]` — runs the 3-stage cascade, collects ALL matches per stage (project each to `ChatCandidate` at collection), only advances to next stage on zero hits, returns candidates sorted by `last_activity_ts` desc with None sorting last.
- Refactor `resolve_chat_id(chat_name: str, *, strict: bool = False) -> str | None` to delegate to `resolve_chat_candidates`. On `>1` candidates: if `strict=True`, raise `AmbiguousChatError(candidates)`. Otherwise, emit a single `logger.warning("ambiguous chat %r: chose %s (%s, last %s); alternatives=%s", chat_name, chosen.chat_id, chosen.chat_name, chosen_age_str, alt_list)` and return the most-recent candidate's `chat_id` (tiebreak on `chat_id`). Zero-match returns None regardless. Single-match returns that chat_id regardless.
- **Defensive guard:** after sorting candidates, assert the chosen candidate is the one with max `last_activity_ts` (tiebreak on max `chat_id`). On violation (shouldn't happen), raise `AmbiguousChatError` unconditionally — this is fail-loud, not fail-silent-with-wrong-answer.
- **Replace the bare `except Exception: return None`** at line 978–979 with a narrow `except (redis.RedisError, popoto.errors.PopotoError) as e: logger.warning("resolve_chat_candidates failed: %s", e); return None` (exact exception classes confirmed at implementation time — may be `popoto.exceptions.PopotoError` or similar; adjust to the actual package layout).
- Tests: ambiguity with 2 candidates (default path returns most-recent + emits warning via `caplog`; `strict=True` raises with candidates ordered), ambiguity with 3 candidates (default + strict both), zero-match (returns `[]` / None), unique match, ordering by recency with a None-last case, deterministic tiebreak when two candidates share `updated_at` (sorted by `chat_id`), defensive guard raises `AmbiguousChatError` when sort invariant violated (use `monkeypatch` on `sorted` or inject a stub list), narrow exception on simulated Redis error.

### 4. Wire CLI read command
- **Task ID**: build-cli-read
- **Depends On**: build-candidates, audit-callers
- **Validates**: tests/unit/test_valor_telegram.py (expanded)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `--chat-id ID` flag to `read` subcommand (numeric passthrough; bypasses matcher; mutually exclusive with `--chat` and `--user`).
- Add `--user USERNAME` flag (forces DM path via `resolve_username`; mutually exclusive with `--chat` and `--chat-id`).
- Add `--strict` flag (opt into hard error on ambiguity; default is pick-most-recent + stderr warning).
- In `cmd_read`, pass `strict=args.strict` into the `resolve_chat` wrapper. Default path: ambiguity results in a `logger.warning` already emitted by `resolve_chat_id` — `cmd_read` does not need to re-print; Python's default logging config emits warnings to stderr. `cmd_read` then proceeds to fetch and render messages for the returned `chat_id`. `--strict` path: catch `AmbiguousChatError` and format candidates to stdout with exit 1. Format (example):
  ```
  Ambiguous chat name "PsyOptimal". 2 candidates (most recent first):
    -100123  PM: PsyOptimal       last: 3m ago
    -100456  PsyOptimal           last: 2d ago
  Re-run with --chat-id <id> or a more specific --chat string.
  ```
- The `logger.warning` format in the default path must be greppable: `WARN: ambiguous chat "<NAME>" — N candidates, chose <CHOSEN_CHAT_ID>: chose=(<id>,<name>,last:<age>); also=[(<id>,<name>,last:<age>), ...]`. This is the contract a scripted caller can parse; documented in the `telegram` skill.
- On zero-match (`resolve_chat` returns None AND no `--chat-id`/`--user`), print top-3 did-you-mean candidates from full Chat list sorted by `updated_at` desc, exit 1.
- Prepend successful read output with header: `[chat_name · chat_id=N · last activity: T]` using `Chat.updated_at` and existing `format_timestamp`. If `updated_at` is None, format as `last activity: never`.
- Tests: new flag behaviors (happy path + mutex violation for `--chat-id`/`--user`/`--chat`; `--strict` is compatible with `--chat` only), default ambiguity handling in CLI (warning to stderr via `capfd`/`caplog`, messages to stdout, exit 0), `--strict` ambiguity handling (candidates to stdout, exit 1, no messages), zero-match did-you-mean, freshness header (Xh-ago case and never case).

### 5. Wire CLI chats search
- **Task ID**: build-cli-chats-search
- **Depends On**: build-normalization
- **Validates**: tests/unit/test_valor_telegram.py::TestCmdChats (expanded)
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `--search PATTERN` flag to `chats` subcommand.
- Apply normalized substring filter (reusing `_normalize_chat_name`); keep existing sort by last-message desc.
- Tests: search finds single match, multiple matches, zero-match returns empty list cleanly, normalization-aware match (e.g., `--search "PM psy"` matches `PM: PsyOptimal`).

### 6. Consolidate orphan script
- **Task ID**: consolidate-orphan
- **Depends On**: build-cli-read, audit-callers
- **Validates**: grep returns no matches outside git history
- **Assigned To**: telegram-resolver-builder
- **Agent Type**: builder
- **Parallel**: false
- Migrate any callers found in `audit-callers` to `valor-telegram read --user USERNAME`.
- If audit found zero callers: delete `scripts/get-telegram-message-history` and associated test files.
- If audit found callers: replace the script body with a one-line shim (`exec valor-telegram read --user "$@"`), commit separately, migrate callers in the same PR, then delete the shim in a follow-up commit within the same PR.
- `grep -rln "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" .` must return zero matches in tracked files at PR merge time.

### 7. Update documentation
- **Task ID**: document-feature
- **Depends On**: build-cli-read, build-cli-chats-search, consolidate-orphan
- **Assigned To**: telegram-resolver-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/telegram-messaging.md` with new flags and error format.
- Update `docs/features/telegram-history.md` for resolver semantics (candidate projection, ambiguity, narrow exception).
- Update `.claude/skills/telegram/SKILL.md` with new flags and ambiguity-error format the agent should handle on stderr.
- Update `CLAUDE.md` "Reading Telegram Messages" section.
- Remove references to `scripts/get-telegram-message-history` from all docs.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: build-cli-read, build-cli-chats-search, consolidate-orphan, document-feature
- **Assigned To**: telegram-resolver-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -v`.
- Run full suite via `/do-test`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Run `grep -rln "get-telegram-message-history" --include="*.py" --include="*.md" --include="*.sh" .` and confirm zero hits.
- Walk the Success Criteria list and confirm each item.
- File the defect-7 follow-up issue (see No-Gos) now that the PR is otherwise ready.
- Generate pass/fail report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `pytest tests/tools/test_telegram_history.py tests/unit/test_valor_telegram.py -q` | exit code 0 |
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

**All resolved in the post-critique revision.** Recorded decisions:

1. **Ambiguity policy (Q1/Q2):** RESOLVED — default path returns the most-recently-updated candidate with a machine-parseable stderr warning listing chosen + alternates; `--strict` flag flips to hard error + exit 1. Rationale: the silent wrong-match was the bug, but "wrong" specifically meant the matcher picked a less-active chat while fresher messages lived elsewhere. Pick-most-recent inverts that failure mode (active chat wins), and the stderr warning makes the alternative visible for audit. Scripted callers that can't parse stderr use `--strict` (hard error) or `--chat-id NUMBER` (unambiguous bypass). A defensive invariant assertion raises `AmbiguousChatError` unconditionally if the sort ever violates its ordering guarantee — fail loud, never return silently-wrong.

2. **Underscore handling (Q2):** RESOLVED — strip `_`. Real-world chat names with underscores mirror slug/channel conventions and users typing them interactively are likely to type space or nothing. The ambiguity detector is the safety net for the rare collision case. Conservative-on-`_` trades a real UX win for a hypothetical edge case.

3. **Orphan script delete vs. shim (Q3):** RESOLVED — straight delete if audit finds zero callers (expected outcome); one-commit shim-then-delete within the same PR otherwise. Task 6 encodes the branch.

4. **Defect 7 follow-up issue (Q4):** RESOLVED — file the follow-up issue at `validate-all` time (Task 8), not deferred to a separate coordination step. See No-Gos for details.

5. **`strict=True` caller rigor (Q5):** RESOLVED — PR-body disposition checklist is sufficient. Recon shows only ONE in-tree caller; the disposition for that one site goes in the PR body (expected: the CLI caller passes `strict=True` only when `--strict` is on the command line; otherwise it accepts the default pick-most-recent behavior and relies on the logger.warning to surface the ambiguity). We do not file tracking issues per-site because there is no "per-site" plural to track.

Leave this section in place as the historical record of decisions — do not remove during finalize.
