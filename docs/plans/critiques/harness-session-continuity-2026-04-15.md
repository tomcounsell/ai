# Critique Archive: Harness Session Continuity

**Plan**: `docs/plans/harness-session-continuity.md`
**Issue**: [#976](https://github.com/tomcounsell/ai/issues/976)
**Critique runs**: 2 (2026-04-15)

This document archives the critique cycles for `harness-session-continuity.md`. The plan body should describe only the current spec; this file holds the audit trail.

---

## Round 1 — 2026-04-15

**Critics:** Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings:** 5 total (1 blocker, 3 concerns, 1 nit)
**Revision applied:** 2026-04-15 — All findings addressed in plan text (Change 1 side-effect note, Change 2 mandatory fallback + UUID validation, Data Flow step 3/4, Risk 1, Failure Path Test Strategy, Test Impact, Step by Step Tasks, Success Criteria).

### Blockers (Round 1)

#### Stale UUID causes hard error, not silent fallback
- **Severity**: BLOCKER
- **Critics**: Skeptic, Adversary
- **Location**: Risk 1 / Failure Path Test Strategy / "Error State Rendering"
- **Finding**: The plan stated in Risk 1 that `claude -p --resume <stale_uuid>` "may either silently start a new session (best case) or error out (worst case)" and deferred the determination to the integration test. Empirical testing (2026-04-15) confirms the binary **errors**: `Error: --resume requires a valid session ID or session title when used with --print. Provided value "nonexistent-uuid" is not a UUID and does not match any session title.` This is not a "may" -- it is the actual behavior. The plan's conditional fallback ("if the binary errors, add the fallback") must be unconditional.
- **Suggestion**: Promote the retry-without-`--resume` fallback from conditional to mandatory in the implementation. In `get_response_via_harness()`, when `prior_uuid` is set and the process exits with non-zero return code AND stderr contains "resume" or "session", retry the call once without `--resume` (full first-turn path). Add this as an explicit sub-step in Task 1 rather than leaving it to discovery during the integration test.
- **Implementation Note**: After `proc.communicate()`, if `proc.returncode != 0` and `prior_uuid` is set, check `stderr_text` for the substring `"--resume"`. If found, log a warning (`"Stale UUID {prior_uuid}, falling back to first-turn path"`), then re-enter the function recursively (or inline) with `prior_uuid=None` — which means re-applying `_apply_context_budget()` to the original full message. The caller must pass the original un-skipped message for this fallback, so `get_response_via_harness` needs access to both the full message and the minimal message, or the retry must reconstruct the full-context path. Simplest approach: accept both `message` (always the full context) and `prior_uuid`; when `prior_uuid` is set, ignore `message` and use a separately-passed `resume_message` (just the new user text). On fallback, use `message` with `_apply_context_budget()`.

### Concerns (Round 1)

#### UUID validity not checked before subprocess spawn
- **Severity**: CONCERN
- **Critics**: Adversary
- **Location**: Solution / Change 2
- **Finding**: The plan injected `--resume <prior_uuid>` directly into the subprocess argv without validating that `prior_uuid` is a well-formed UUID. If the Popoto record contains a corrupted or non-UUID string (e.g., from a bug in a future code path), this could cause unexpected CLI behavior or argument injection.
- **Suggestion**: Add a UUID format check (regex or `uuid.UUID()` parse) before injecting `--resume`. If the value is not a valid UUID, treat it as `None` and take the first-turn path.
- **Implementation Note**: Guard in `get_response_via_harness()` right after the empty-string check: `try: uuid.UUID(prior_uuid); except ValueError: prior_uuid = None`. Import `uuid` from stdlib. This is a 3-line defensive check that prevents argument injection and corrupted-data issues.

#### Dual message paths require careful orchestration at call site
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: Solution / Change 3 / `_execute_agent_session` call site
- **Finding**: When `prior_uuid` is set, `build_harness_turn_input(skip_prefix=True)` returns just the raw message, and this is what gets passed to `get_response_via_harness()`. But if the stale-UUID fallback fires (see Blocker above), the function needs the full-context message to retry. The plan did not specify how the full-context message is preserved for the fallback path.
- **Suggestion**: Always call `build_harness_turn_input()` with `skip_prefix=False` to get the full context message. Pass both the full message and the minimal message (just `_turn_input`) to `get_response_via_harness()`. On first attempt with `--resume`, use the minimal message. On fallback, use the full message with `_apply_context_budget()`.
- **Implementation Note**: In `_execute_agent_session()`, always build `_harness_input_full = await build_harness_turn_input(skip_prefix=False, ...)`. When `_prior_uuid` is set, also prepare `_harness_input_minimal = _turn_input` (the raw steering/user message). Pass both to `get_response_via_harness(message=_harness_input_full, resume_message=_harness_input_minimal, prior_uuid=_prior_uuid, ...)`. This ensures the fallback path has the full context without needing to call `build_harness_turn_input` again.

#### `get_response_via_harness` return type is `str` but UUID must be persisted
- **Severity**: CONCERN
- **Critics**: Skeptic
- **Location**: Solution / Change 1
- **Finding**: The plan adds `session_id` as a parameter to `get_response_via_harness()` and calls `_store_claude_session_uuid()` inside the function. This couples a side-effecting Popoto write into a function whose current contract is "run CLI, return text." The function currently returns `str`; callers (including tests) expect pure string return. Embedding the store call inside means tests of `get_response_via_harness` now need Popoto/Redis mocked or available.
- **Suggestion**: This is acceptable given the existing pattern (the SDK path does the same inside `get_agent_response_sdk`), but the docstring and test setup must explicitly note the side effect. Ensure the new unit tests mock `_store_claude_session_uuid` to verify it is called without requiring Redis.
- **Implementation Note**: In each new test case in `test_harness_streaming.py`, patch `agent.sdk_client._store_claude_session_uuid` as a `MagicMock` and assert `mock.assert_called_once_with(session_id, expected_uuid)`. The mock prevents Redis dependency in unit tests. The side effect is already fail-silent (line 228 `except Exception`), so even if the mock is misconfigured the test won't hang.

### Nits (Round 1)

#### Redundant success criteria
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Success Criteria
- **Finding**: "Tests pass (`/do-test`)" and "All pre-existing tests continue to pass (`pytest tests/` exits 0)" are redundant -- `/do-test` runs `pytest tests/`. Similarly "No new lint or format violations" is covered by `/do-test` which runs ruff.
- **Suggestion**: Consolidate into a single "All tests, lint, and format checks pass" criterion to reduce noise.

### Verdict (Round 1)

**READY TO BUILD (with concerns)** -- No BLOCKERs after the stale-UUID fallback is promoted from conditional to mandatory. The 1 BLOCKER finding identifies behavior that the plan already anticipated as a possibility but must now be treated as certain (empirically confirmed). A revision pass embedded the Implementation Notes from the 1 blocker and 3 concerns into the plan text before build proceeds.

---

## Round 2 — 2026-04-15 (re-pass on revised plan)

**Critics:** Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings:** 4 total (0 blockers, 3 concerns, 1 nit)
**Revision applied:** 2026-04-15 — Concerns embedded into Change 2 and Verification (this revision).

### Concerns (Round 2)

#### Stale-UUID detection substring is fragile across CLI versions
- **Severity**: CONCERN
- **Critics**: Adversary, Operator
- **Location**: Solution / Change 2 (substring trigger) and the Round-1 Implementation Note
- **Finding**: The fallback trigger was `stderr contains "requires a valid session"` (Change 2) but the Round-1 blocker's Implementation Note said the simpler check is `"--resume"` substring. These are inconsistent. More importantly, both are version-coupled to today's `claude` binary error text. A future CLI release that rewords the error (e.g., "Session not found" or localized strings) silently breaks the fallback — the binary returns non-zero, the substring miss leaves `prior_uuid` untouched, the function returns "" via the existing error path, and the user gets an empty turn instead of a fallback retry.
- **Suggestion**: On ANY non-zero exit when `prior_uuid` is set, attempt the fallback rather than gating on substring. Cost of an unnecessary retry on a real error is one extra subprocess spawn vs. a silent stuck-empty turn.
- **Implementation Note**: After `proc.communicate()` and the existing `if proc.returncode and proc.returncode != 0:` block, fall back unconditionally when `prior_uuid` was set: `if prior_uuid and proc.returncode != 0: logger.warning(f"[harness] Stale UUID {prior_uuid} for session_id={session_id}, falling back to first-turn path"); return await get_response_via_harness(message=full_context_message, working_dir=working_dir, harness_cmd=harness_cmd, env=env, session_id=session_id, prior_uuid=None, full_context_message=None)`. This avoids substring brittleness entirely.

#### `_apply_context_budget` retention assumes first-turn argv stays bounded — but a single mega-message also overflows
- **Severity**: CONCERN
- **Critics**: Skeptic, Adversary
- **Location**: Solution / Change 2 + Risks (no entry covered this)
- **Finding**: The plan retained `_apply_context_budget()` only as a "first-turn safety net" and bypassed it on resumed turns because "the message at this point is just the new user input — it cannot overflow." This is true for *typical* resumed turns, but a Telegram message can carry a forwarded transcript, a pasted log, or a media-enriched message body that *itself* exceeds `HARNESS_MAX_INPUT_CHARS` (100K chars). On a resumed turn, that single user message would be passed unbudgeted into argv and re-create the original "Separator is found" crash — exactly the bug this plan is fixing.
- **Suggestion**: Apply `_apply_context_budget()` unconditionally to the final argv message regardless of `--resume` state. The function is a no-op when `len(message) <= max_chars`, so the cost is one length comparison on the typical-small resume path.
- **Implementation Note**: In `get_response_via_harness`, do not gate `_apply_context_budget()` on `prior_uuid`. Keep `message = _apply_context_budget(message)` as today, regardless of `--resume`. The "skip the budget" optimization is premature — it saves nothing on small messages and reintroduces the original crash on large ones.

#### No telemetry to confirm `--resume` is actually being used in production
- **Severity**: CONCERN
- **Critics**: Operator
- **Location**: No-Gos (deferred telemetry to follow-up) + Verification (no operator-facing observability)
- **Finding**: The plan's No-Gos section explicitly deferred `Telemetry / metrics for resume hit-rate` to a follow-up chore, but the only way to know the fix is *actually* working in production is to observe (a) that `--resume` is being injected on second+ turns of long sessions, and (b) that the stale-UUID fallback rate is bounded. Without minimal observability, a regression where `_get_prior_session_uuid` silently always returns `None` (e.g., a future Popoto schema change) would be invisible — every turn would still work via the first-turn path, just with the original crash on long threads still occurring.
- **Suggestion**: Add a single log line at INFO level when `--resume` is injected and another when the fallback fires. Zero-cost observability that grep-able log analysis can confirm the new path is hot.
- **Implementation Note**: In `get_response_via_harness`, after the empty/uuid-format guards but before subprocess spawn: `if prior_uuid: logger.info(f"[harness] Resuming Claude session {prior_uuid} for session_id={session_id}")`. In the fallback path: `logger.warning(f"[harness] Stale UUID {prior_uuid} for session_id={session_id}, falling back to first-turn path")`. Add a Verification row: `Resume hit logged | grep -c "Resuming Claude session" logs/worker.log after manual two-turn reproducer | output > 0`.

### Nits (Round 2)

#### Embedded "Critique Results" section blurs revision history
- **Severity**: NIT
- **Critics**: Simplifier
- **Location**: Plan body (lines 427-491 of pre-revision plan)
- **Finding**: The plan contained an embedded record of the previous critique round inside the plan itself. Useful for traceability but creates an awkward situation if a third critique round occurs — the plan would gain a second "Critique Results" section, or the first would need editing. Also makes the plan no longer just a forward-looking spec but also a partial commit log. Violates the user's documented preference (`feedback_no_parallel_migrations.md`: "no historical artifacts in docs. Fully cut over, describe only the new status quo.").
- **Suggestion**: Move the embedded critique results to this separate file (`docs/plans/critiques/harness-session-continuity-2026-04-15.md`) and link it from the plan's `## Prior Art` or front-matter. The plan body should describe only the current spec; the audit trail belongs in a sibling document.
- **Resolution**: Done in this revision — embedded Critique Results section removed from plan body; this file is the audit trail; plan links here from Prior Art.

### Verdict (Round 2)

**READY TO BUILD (with concerns)** — No BLOCKERs. The 3 CONCERNs from this round are not present in Round 1, so they represent fresh findings. The Round-2 revision pass (this commit) embedded the Implementation Notes from these three concerns into Change 2 and added one new Verification row. The NIT is resolved by moving the audit trail into this file.

---

## Structural Check Results (Round 2)

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | All 4 (`Documentation`, `Update System`, `Agent Integration`, `Test Impact`) present and non-empty |
| Task numbering | PASS | Tasks 1-5, sequential, no gaps |
| Dependencies valid | PASS | All `Depends On` references resolve |
| File paths exist | PASS | 11/11 referenced source files exist |
| Prerequisites met | PASS | `claude` binary on PATH; `--resume` flag rejects bad UUID with cited error text; Popoto query succeeds |
| Cross-references | PASS | Every Success Criterion maps to a task; No-Gos do not appear as Solution work; Rabbit Holes excluded from tasks |
| Line references | PASS | All cited lines verified against current files |
| AgentSession schema | PASS | `claude_session_uuid = Field(null=True)` exists at `models/agent_session.py:190` |
