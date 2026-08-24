---
status: Planning
type: feature
appetite: Medium
owner: Tom Counsell
created: 2026-05-09
tracking: https://github.com/tomcounsell/ai/issues/1358
last_comment_id:
---

# Behavioral Episode Memory Revival

## Problem

`reflections/behavioral_learning.py` runs daily, exits in 0.0s, and logs:

```
[behavioral_learning] models.cyclic_episode not available — skipping episode cycle-close and pattern crystallization
```

The reflection is enabled in `config/reflections.yaml:271-277` but the models it consumes (`CyclicEpisode`, `ProceduralPattern`) and the `AgentSession` instrumentation (`tool_sequence`, `friction_events`) it reads do not exist on `main`. They live on unmerged branch `origin/session/behavioral_episode_memory` (commit `04af11fd`), closed via PR #391 in March 2026 — pending Popoto Agent Memory primitives that have since shipped.

The retrieval surface — taking a structural fingerprint `(problem_topology, affected_layer)` and returning ranked patterns with `canonical_tool_sequence + success_rate` — was never built, even on the branch.

**Current behavior:**
- Daily reflection no-ops with status `ok`, leaving an "everything is fine" trail while the system records zero behavioral episodes.
- The `behavioral_learning` slot is dead weight: it occupies a runtime slot, fires every 86400s, and produces nothing. The skip-with-status-ok pattern is exactly the false-positive #1271's hourly cleanup is meant to surface.
- No way to ask "we have a `(bug_fix, agent)` problem — what did we do last time it worked?"

**Desired outcome:**
- `models/cyclic_episode.py` and `models/procedural_pattern.py` exist on main using current Popoto primitives (`ConfidenceField`, `DecayingSortedField`, `AccessTrackerMixin`).
- `AgentSession` accumulates `tool_sequence` (`"{stage}:{tool_name}"`) and `friction_events` during SDLC sessions, wired into the live `PostToolUse` hook (not the dead `bridge/session_transcript.append_tool_result()` from the original branch).
- `scripts/fingerprint_classifier.py` classifies completed sessions with non-`"ambiguous"` topology in ≥80% of cases.
- `agent/trajectory_retrieval.py` plus a `valor-trajectory` CLI takes a `(problem_topology, affected_layer)` fingerprint and returns ranked `ProceduralPattern` records with `canonical_tool_sequence` + `success_rate` + `confidence`.
- After one daily cycle, `logs/worker.log` shows `Created N behavioral episodes…` instead of "not available — skipping".

## Freshness Check

**Baseline commit:** `827057b9` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-05-09T13:16:13Z (today, ~2 hours before plan creation)
**Disposition:** Unchanged — but **Popoto primitives have shipped** since the original branch was written (March 2026). The plan must layer on top of `ConfidenceField` / `DecayingSortedField` / `AccessTrackerMixin` instead of the manual scaffolding the branch used.

**File:line references re-verified:**
- `config/reflections.yaml:271-277` — the `behavioral-learning` registration with `enabled: true` and `every: 86400s` — still holds (verified line-for-line).
- `agent/memory_retrieval.py:235-302` — `retrieve_memories()` 4-signal RRF fusion (BM25 + relevance + confidence + embedding), all content-addressed — still holds.
- `agent/memory_extraction.py:305-324` — PR-takeaway extraction prompt explicitly drops implementation details and collapses a PR to one sentence — still holds.
- `models/cyclic_episode.py`, `models/procedural_pattern.py`, `scripts/fingerprint_classifier.py`, `agent/trajectory_retrieval.py` — all confirmed missing on main.
- `models/agent_session.py` — no `tool_sequence` or `friction_events` fields on main — confirmed missing.

**Cited sibling issues/PRs re-checked:**
- #1310 — closed 2026-05-09 13:16 (just before #1358 was filed; this issue is the spawn point).
- #376 — closed 2026-03-13. Original behavioral-episode tracking issue.
- #391 — CLOSED, NOT MERGED on 2026-03-13. Reason: "Closing in favor of a new issue designed to build on top of Popoto Agent Memory primitives (DecayingSortedField, ConfidenceField, AccessTracker, etc.) rather than reimplementing them." This is the explicit prior-art gate this revival must respect.
- #1271 — closed 2026-05-05. Cited only as the model for the cleanup pattern reference; still applies.

**Commits on main since issue was filed (touching referenced files):**
- None (issue was filed today; only docs/skills commits since).

**Active plans in `docs/plans/` overlapping this area:** none. Closest neighbor is `agent-session-outcome-verification.md`, which classifies session outcomes — orthogonal (it sets `intent_satisfied` / `resolution_type`; we read those signals but don't compute them).

**Notes:** The PR-#391 close note is decisive — porting `04af11fd` verbatim would re-do the very thing that was rejected. The revival must use Popoto primitives where they apply.

## Prior Art

- **Issue #376** — Behavioral Episode Memory System (Mar 2026). Original spec for the models, classifier, and crystallization pipeline. Closed in favor of #1310 / #1358 once Popoto primitives shipped.
- **PR #391** — Add Behavioral Episode Memory System (Phases 1-2). The `04af11fd` implementation. CLOSED unmerged on 2026-03-13 because: "Closing in favor of a new issue designed to build on top of Popoto Agent Memory primitives... rather than reimplementing them." Includes 37 unit tests for models + classifier + sync, plus 52 functional tests for cycle-close and crystallization in commit `5b07a47e`. Source of the canonical schema the consumer (`reflections/behavioral_learning.py`) already targets.
- **Issue #1310** — Trajectory memory: scope vs existing memory systems (May 2026). The scoping investigation that confirmed trajectory recall is its own primitive, distinct from subconscious memory and from the PR-takeaway memory extraction path.
- **PR #967** (merged 2026-04-14) — `feat(reflections): delete 3086-line monolith, extract reflections/ package`. The refactor that lifted `behavioral_learning.py` out of the monolith and into the standalone reflections package — but only the consumer; the producers (models + instrumentation) were never ported. This is the structural reason the reflection no-ops daily.
- **PR #797** (closed 2026-04-07) — `wip: recovered uncommitted work + 17 stashes as snapshot chain`. Unrelated to this revival; mentioned only because it appears in keyword search.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #391 (04af11fd) | Ported full behavioral-episode-memory stack: models, classifier, sync, AgentSession instrumentation, reflection wiring | Reimplemented confidence/decay/sample-counting plumbing manually instead of using Popoto's `ConfidenceField` / `DecayingSortedField` / `AccessTrackerMixin`. Closed at the author's discretion to wait for those primitives to ship. They have since shipped (`models/memory.py:148-160` is the canonical pattern). |
| PR #967 (merge of `d1c07951` → `d00a1c95`) | Extracted `reflections/behavioral_learning.py` into the new reflections package | Only ported the **consumer**, not the producers. Left the consumer running daily against missing models — which the consumer's own `try/except ImportError` at line 32 swallows silently. The skip-with-`status:"ok"` pattern is itself a defect (#1271 family). |

**Root cause pattern:** The revival is half-shipped. The consumer is registered and runs daily, but its inputs (CyclicEpisode/ProceduralPattern/instrumentation) were intentionally deferred and never returned to. Any plan that doesn't use Popoto primitives gets closed for the same reason PR #391 was closed; any plan that doesn't add the missing retrieval helper just rebuilds PR #391's deferred Phase 2.

## Architectural Impact

- **New dependencies**: None external. Models use Popoto primitives already imported by `models/memory.py`. Classifier uses Anthropic Haiku via the existing `AsyncAnthropic` client (same as `agent/memory_extraction.py`).
- **Interface changes**: `AgentSession` gains two `ListField` columns (`tool_sequence`, `friction_events`) and two append helpers (`append_tool_event`, `append_friction_event`). All other models are net-new.
- **Coupling**: `reflections/behavioral_learning.py` already imports the missing modules — porting them tightens the existing coupling rather than creating new edges. PostToolUse hook gains a single best-effort write to AgentSession; failures are swallowed.
- **Data ownership**: Reflections is the sole writer of CyclicEpisode and ProceduralPattern. PostToolUse hook is the sole writer of AgentSession.tool_sequence/friction_events. Retrieval helper is read-only.
- **Reversibility**: Models can be deleted without affecting other systems (the consumer already gracefully degrades on `ImportError`). PostToolUse instrumentation is fail-silent and additive — it can be reverted by removing one helper call. Retrieval helper is purely additive.

## Spike Results

### spike-1: Use `ConfidenceField` for ProceduralPattern confidence?
- **Assumption**: "Popoto's `ConfidenceField` (now shipped) supersedes the manual `success_count` / `sample_count` / `_compute_confidence` formula in `04af11fd`'s ProceduralPattern."
- **Method**: code-read of `models/memory.py:88-160` (canonical Popoto Agent Memory usage) and `popoto.fields.confidence_field` API surface.
- **Finding**: `ConfidenceField(initial_confidence=0.5)` is updated by `ObservationProtocol` and produces a Bayesian confidence score. The branch's manual pipeline (`success_count++`, `sample_count++`, `confidence = success_rate * min(sample_count/10, 1.0)`) duplicates this. Use `ConfidenceField` and call `pattern.observe(success=True/False)` from the crystallization step. Keep `sample_count` and `success_count` as plain `IntField` for diagnostic transparency, but make `confidence` and `success_rate` derived/Popoto-managed.
- **Confidence**: high
- **Impact on plan**: ProceduralPattern model uses `ConfidenceField` and `AccessTrackerMixin`; `_compute_confidence` from the branch is dropped. The crystallization step in `reflections/behavioral_learning.py:228-244` becomes 3 lines simpler.

### spike-2: Use `DecayingSortedField` for CyclicEpisode and/or ProceduralPattern?
- **Assumption**: "Episodes and patterns benefit from time-decayed indexing (recent matters more)."
- **Method**: code-read of `popoto.fields.decaying_sorted_field` docstring + `models/memory.py:148-151` usage.
- **Finding**: For `CyclicEpisode`, NO — episodes are queried by `(topology, layer)` cluster, not by recency. Branch's `created_at = SortedField(type=float)` is correct. For `ProceduralPattern`, YES — `last_reinforced` should decay so an unused pattern is naturally surfaced lower than a freshly reinforced one. Replace `last_reinforced = SortedField(type=float)` with `last_reinforced = DecayingSortedField(decay_rate=0.5, base_score_field="confidence")`. This makes retrieval naturally prefer recently-reinforced, high-confidence patterns without manual sorting.
- **Confidence**: medium-high
- **Impact on plan**: ProceduralPattern uses DecayingSortedField for `last_reinforced`. Retrieval helper queries by topology+layer, then sorts by the decayed `last_reinforced` score (which already factors confidence in via `base_score_field`).

### spike-3: PostToolUse hook is the canonical instrumentation point, not `bridge/session_transcript.append_tool_result()`?
- **Assumption**: "The branch wires instrumentation at `bridge/session_transcript.py::append_tool_result()`. Verify that's still the right surface on main."
- **Method**: code-read of `bridge/session_transcript.py` + grep for production callers.
- **Finding**: `append_tool_result()` is **dead in production** — only tests call it (`grep -rn "append_tool_result\b"` shows zero callers in `agent/`, `bridge/`, `worker/`). The live tool-event surface is `.claude/hooks/post_tool_use.py::_update_agent_session()` (lines 334-394), which already resolves the AgentSession via sidecar and updates `tool_call_count` per tool. That function is the natural append site for `tool_sequence` and `friction_events`.
- **Confidence**: high
- **Impact on plan**: Wire instrumentation in `.claude/hooks/post_tool_use.py::_update_agent_session()` rather than `bridge/session_transcript.py`. Drop the branch's `_infer_current_stage()` helper — `AgentSession.current_stage` (already at `models/agent_session.py:1222`) returns the in-progress SDLC stage from `stage_states`.

### spike-4: Where to record friction events?
- **Assumption**: "Friction events come from tool errors and session-level retries."
- **Method**: code-read of `agent/sdk_client.py` retry paths (lines 1751-1773) + PostToolUse hook signature (`tool_response.is_error`).
- **Finding**: Two natural sites: (1) PostToolUse hook when `tool_response` indicates an error/retry — append `("STAGE", "tool_X failed: Y")` to friction_events. (2) Harness-level: when the subprocess exits with non-zero returncode, call `agent_session.append_friction_event(stage, "harness_exit_code: N")`. Both are best-effort and fail-silent. The branch's `record_friction_event()` helper in sdk_client.py-on-the-branch is fine to port for site (2).
- **Confidence**: high
- **Impact on plan**: PostToolUse and the harness exit path each emit one friction event per error. No new "decision tree" — just two well-defined emit sites.

### spike-5: Does the unmerged branch's classifier still apply, or do we need a fresh design?
- **Assumption**: "`scripts/fingerprint_classifier.py` from `04af11fd` is portable as-is."
- **Method**: code-read of the branch file (187 lines) + cross-check against `agent/memory_extraction.py` (the canonical Haiku-call pattern on main).
- **Finding**: The branch's classifier is conceptually correct — Haiku call, JSON output, graceful fallback to `{"problem_topology": "ambiguous", "affected_layer": "unknown"}`. The Anthropic SDK API surface has not changed. Port it but use the same `AsyncAnthropic` instantiation + retry pattern as `agent/memory_extraction.py:extract_post_merge_async()`. Reject any LLM output whose `problem_topology` / `affected_layer` is not in the validated enum (defined in `models/cyclic_episode.py`).
- **Confidence**: high
- **Impact on plan**: Port classifier with structural preservation; modernize only the SDK call shape and validate enums against the model module (single source of truth).

## Data Flow

End-to-end trace for one episode lifecycle:

1. **Entry point**: A Telegram message arrives, the bridge enqueues an AgentSession (PM or Dev), and the worker spawns a Claude Code subprocess.
2. **PostToolUse hook fires per-tool**: `.claude/hooks/post_tool_use.py::main()` runs, resolves the AgentSession via the sidecar, calls existing `_update_agent_session(hook_input)`. We extend that function:
   - On every tool: `agent_session.append_tool_event(stage=agent_session.current_stage or "UNKNOWN", tool_name=hook_input["tool_name"])`. The append helper truncates `tool_sequence` to last 50 entries.
   - On tool errors (`hook_input.get("tool_response", {}).get("is_error")`): `agent_session.append_friction_event(stage, description="tool_X failed: <truncated_error>")`. Truncated to last 20 entries.
3. **Harness exit path**: `agent/sdk_client.py` already stores `returncode` via `_store_exit_returncode()`. Add a sibling helper `_record_harness_friction(session, returncode)` that appends a friction event when returncode is non-zero. Best-effort; swallows all exceptions.
4. **Session completes**: AgentSession.status is set to `completed` (or `failed`/`abandoned`/`killed`) by the worker. `tool_sequence` and `friction_events` are now durable Redis lists.
5. **Daily reflection (86400s)**: `reflections/behavioral_learning.py::run()` queries `AgentSession.query.all()` for recent (`completed_at >= now - 86400`) sessions. For each completed SDLC session not already covered by an episode, it calls `scripts.fingerprint_classifier.classify_session(session)` and creates a `CyclicEpisode` with the captured trajectory.
6. **Crystallization sub-step**: The same reflection clusters episodes by `(problem_topology, affected_layer)`. Clusters with ≥3 episodes either reinforce an existing `ProceduralPattern` (call `pattern.observe(success_rate > 0.5)` to update Popoto's `ConfidenceField`) or create a new one with the modal `tool_sequence` as `canonical_tool_sequence`.
7. **Retrieval (read path)**: `agent/trajectory_retrieval.py::recall_pattern(problem_topology, affected_layer, limit=5)` runs `ProceduralPattern.query.filter(problem_topology=..., affected_layer=...)`, sorts by `last_reinforced` (the DecayingSortedField score natively factors confidence), and returns ranked `ProceduralPattern` instances.
8. **CLI surface**: `valor-trajectory recall --topology bug_fix --layer agent` (and `valor-trajectory list`, `valor-trajectory inspect --id N`) wraps the Python helper for shell usage and as a debugging affordance.
9. **Output**: The retrieval helper is currently invoked manually (CLI) or programmatically (Python). Hooking it into the agent's planning loop is OUT OF SCOPE for this plan (`[SEPARATE-SLUG]` below).

## Appetite

**Size:** Medium

**Team:** Solo dev with one validator pass.

**Interactions:**
- PM check-ins: 1 (after critique, before build, to confirm spike findings landed)
- Review rounds: 1 (one PR review, one patch loop expected)

The work is mechanically large (3 new models, instrumentation, classifier, retrieval, CLI, tests, docs) but each piece has a clear precedent (`04af11fd` for the bulk; `models/memory.py` for the Popoto-primitive pattern). The bottleneck is staying disciplined about NOT porting the manual confidence/decay plumbing.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Fingerprint classifier Haiku call |
| Popoto >= 1.6 | `python -c "from popoto import ConfidenceField, DecayingSortedField, AccessTrackerMixin"` | New models depend on these primitives |
| Redis running | `python -c "from popoto import get_redis; get_redis().ping()"` | Models persist to Redis |
| Branch `origin/session/behavioral_episode_memory` fetchable | `git -C \"$SDLC_TARGET_REPO\" rev-parse origin/session/behavioral_episode_memory` | Source of truth for porting models, classifier, tests |

Run all checks: `python scripts/check_prerequisites.py docs/plans/behavioral_episode_memory_revival.md`

## Solution

### Key Elements

- **CyclicEpisode model**: Structural record of a completed SDLC cycle. Fingerprint (topology, layer, ambiguity), trajectory (tool_sequence, friction_events), outcome (resolution_type, intent_satisfied, review_round_count). Vault-isolated per project.
- **ProceduralPattern model**: Crystallized pattern from ≥3 episodes sharing a fingerprint cluster. Holds `canonical_tool_sequence`, warnings, and (via Popoto) `ConfidenceField` + `DecayingSortedField` for `last_reinforced`. Lives in `vault="shared"`.
- **AgentSession instrumentation**: Two new `ListField`s (`tool_sequence`, `friction_events`) plus capped append helpers. Wired into `.claude/hooks/post_tool_use.py::_update_agent_session()` (live surface) and `agent/sdk_client.py` exit path (subprocess-level friction).
- **Fingerprint classifier**: `scripts/fingerprint_classifier.py::classify_session(session) -> dict` calls Haiku to label the session by `(problem_topology, affected_layer, ambiguity_at_intake, acceptance_criterion_defined)`. Validates output against enums declared in the model module. Falls back to `{"problem_topology": "ambiguous", ...}` on any error.
- **Trajectory retrieval helper**: `agent/trajectory_retrieval.py::recall_pattern(topology, layer, limit=5) -> list[ProceduralPattern]`. Plus `valor-trajectory` CLI registered in `pyproject.toml [project.scripts]`.
- **Side-cleanup, NOT a separate path**: The "if revival is deferred, remove the consumer entry" branch from the issue is moot — this plan revives. No `behavioral_learning.py` code or yaml entry is removed; we just give it real inputs.

### Flow

A typical end-to-end: A dev session ships a bug fix → the PostToolUse hook records each tool call to `tool_sequence` → on completion, the daily reflection 24h later classifies the session (`problem_topology="bug_fix"`, `affected_layer="agent"`) → creates a CyclicEpisode → the third such episode in that cluster crystallizes into a ProceduralPattern with the canonical 4-tool sequence (`Read → Edit → Bash → Bash`) → next time we hit a similar fingerprint, `valor-trajectory recall --topology bug_fix --layer agent` returns that pattern with its tool sequence and confidence score.

### Technical Approach

- **Port-with-modernize, don't port-verbatim.** The branch is a starting point. Where Popoto primitives now exist (`ConfidenceField`, `DecayingSortedField`, `AccessTrackerMixin`), use them — that's the *exact* reason PR #391 was closed.
- **Live surface only.** Wire AgentSession instrumentation into `.claude/hooks/post_tool_use.py`, NOT `bridge/session_transcript.py::append_tool_result()` (which has no production callers).
- **Single source of enum truth.** `PROBLEM_TOPOLOGIES`, `AFFECTED_LAYERS`, `RESOLUTION_TYPES` declared in `models/cyclic_episode.py`; classifier and any consumers import from there.
- **Read existing AgentSession.current_stage.** Don't port `_infer_current_stage()` from the branch; the property already exists and reads from `stage_states`.
- **Idempotent reflection.** `behavioral_learning.py` already has dedup logic (`raw_ref` and topology+layer+branch matching) — keep it. Verify with a re-run test: running the reflection twice in succession must NOT create duplicate episodes for the same session.
- **CLI surface mirrors `tools/memory_search/cli.py`.** `valor-trajectory recall|list|inspect` with argparse subparsers, `--json` flag for machine output. Register in `pyproject.toml [project.scripts]`.
- **Vault model.** Episodes use `vault="mem:{project_key}"` (per-project privacy). Patterns use `vault="shared"` (cross-project, structural-only — no project content). This matches the branch and Memory's pattern.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `reflections/behavioral_learning.py:31-42` — `try: import models.cyclic_episode; except ImportError: return {"status": "ok", ...}` — after revival, the `ImportError` branch becomes unreachable in normal operation; add a unit test that explicitly verifies the post-revival reflection returns `status="ok"` with `findings` populated when SDLC sessions exist.
- [ ] `agent/sdk_client.py` `_record_harness_friction()` — must swallow all exceptions and log at DEBUG only; add a test that injects a save failure and asserts no propagation.
- [ ] `.claude/hooks/post_tool_use.py::_update_agent_session()` — already wraps in `try/except` per existing pattern; the new `append_tool_event` calls go inside that envelope, asserted via a test that injects an AgentSession.save() failure and confirms the hook still exits 0.
- [ ] `scripts/fingerprint_classifier.py::classify_session()` — every Haiku failure mode (network error, malformed JSON, invalid enum value) must return the `ambiguous/unknown` default. One test per failure mode.

### Empty/Invalid Input Handling
- [ ] `append_tool_event(stage="", tool_type="")` — must accept and persist `:` (empty stage and tool); test added.
- [ ] `recall_pattern("", "")` — empty topology/layer must return `[]`, not raise.
- [ ] `classify_session(session_with_no_tool_sequence)` — classifier must operate on the session metadata it has (issue title, branch_name, etc.) and not crash on missing trajectory data.
- [ ] Reflection running over zero qualifying sessions — must return `status="ok"` with empty `findings` and `summary` containing the literal string "0 episodes".

### Error State Rendering
- [ ] `valor-trajectory recall --topology X --layer Y` with zero matches — must print "No matching patterns" to stdout and exit 0 (not 1; "no result" is not an error). Test asserts exit code 0 and stderr is empty.
- [ ] `valor-trajectory recall --topology bogus --layer Y` — must reject the invalid enum at argparse time with a clear error message and exit 2 (argparse default for usage errors).

## Test Impact

- [ ] `tests/unit/test_behavioral_episode_memory.py` — REPLACE: port the 37 unit tests from `04af11fd:tests/unit/test_behavioral_episode_memory.py` and the 52 functional tests from `5b07a47e`, then update each to use Popoto primitives (`ConfidenceField` instead of manual `success_count` math). The original file does not exist on main, so this is "create from a known reference."
- [ ] `tests/unit/test_agent_session.py` (or equivalent) — UPDATE: add tests for `append_tool_event` and `append_friction_event` cap/truncation behavior. Locate via `grep -rn "test_agent_session\b\|TestAgentSession" tests/` at build time; if no host file exists, add a new one.
- [ ] `tests/unit/test_post_tool_use.py` (locate during build) — UPDATE: add a test that verifies `_update_agent_session` calls `append_tool_event` with the resolved `current_stage` and the hook input's `tool_name`. Inject a session with `stage_states` set and assert the AgentSession `tool_sequence` grows by one entry.
- [ ] `tests/unit/test_reflections_behavioral_learning.py` — REPLACE: the post-revival flow (creates episodes, crystallizes patterns) is the new test surface. The existing skip-path test (if any) becomes unreachable; replace it with a populated-sessions integration test that asserts non-zero `episodes_created`.
- [ ] `tests/integration/test_trajectory_retrieval.py` — CREATE: new integration test that seeds 3+ ProceduralPattern records and verifies `recall_pattern()` returns them ranked by decayed `last_reinforced`.
- [ ] `tests/unit/test_fingerprint_classifier.py` — CREATE (port from branch with API-call mocks updated to current AsyncAnthropic surface).

No existing main-branch tests are deleted — every change is additive. The "REPLACE" disposition above means "import from the unmerged branch + modernize."

## Rabbit Holes

- **Don't redesign Memory or RRF retrieval.** They are content-addressed and intentionally distinct. Trajectory retrieval is a *separate primitive* — orthogonal to the existing memory store. Resist any urge to "unify" them.
- **Don't build the Observer integration / agent-side recall hook.** The issue scopes this plan to "Retrieval helper (CLI + Python)". Hooking the agent's planning to auto-call `recall_pattern()` at stage entry is a separate plan with its own design questions (when to call, how to inject the result into context, how to avoid noise).
- **Don't port `scripts/pattern_sync.py`.** Cross-machine iCloud JSON sync is in the branch but not in this issue's acceptance criteria. Defer to a separate slug.
- **Don't model the full friction taxonomy.** The branch uses `"{stage}|{description}|{repetition_count}"` strings — that's enough. Don't introduce a `FrictionEvent` model with its own indexes.
- **Don't try to back-fill historical sessions.** Sessions completed before this revival ships have no `tool_sequence` data, period. The dedup logic ensures we don't over-classify. Empty trajectories are acceptable for the first 24h; classification still produces useful (topology, layer) labels from session metadata alone.
- **Don't change the reflection's schedule or the `behavioral-learning` config entry.** The whole point is that the existing daily slot starts producing real output. Editing `config/reflections.yaml:271-277` is unnecessary scope.

## Risks

### Risk 1: Classifier hits >20% `ambiguous` rate on real sessions
**Impact:** Episodes accumulate in a single cluster, crystallization produces low-quality (or zero) patterns, retrieval returns nothing useful. Acceptance criterion #3 fails.
**Mitigation:**
- Run the classifier against a backlog of recent completed sessions during BUILD (not deferred) and tune the prompt iteratively until ≥80% non-ambiguous on a sample of 20.
- Add a `--dry-run` flag to the classifier CLI for tuning without writing episodes.
- If the rate is irrecoverable, fall back to a hybrid: regex on issue title for primary topology (e.g., `^fix(`/`bug` → `bug_fix`, `^feat(`/`add` → `new_feature`) plus Haiku for layer. Document the heuristic in the feature doc.

### Risk 2: PostToolUse hook latency increase
**Impact:** Every tool call writes to AgentSession.tool_sequence, which is a Redis Hash field with a list value. Two writes per tool (existing `tool_call_count` + new `tool_sequence`) could measurably slow the agent loop.
**Mitigation:**
- The branch's append helper already uses `save(update_fields=["tool_sequence"])` semantics — use the same Popoto partial-update pattern for both new fields.
- Benchmark `_update_agent_session` before and after the change with `pytest --benchmark` (or a manual `timeit` loop). Acceptance: median latency increase ≤2ms per tool call.
- If the benchmark fails, batch the writes: keep an in-memory accumulator and flush every N calls, similar to how `tool_call_count` is incremented per-call but the index is rebuilt nightly.

### Risk 3: Reflection performance scales linearly with all-time AgentSession count
**Impact:** `AgentSession.query.all()` returns every session ever recorded. The `cutoff` filter happens in Python after the network round-trip. With 10k+ sessions, this becomes slow.
**Mitigation:**
- The branch already does `for session in all_sessions: if completed_ts < cutoff: continue`. That's O(N) per day but already shipped pattern.
- If it becomes a bottleneck (when >50k sessions), introduce a `completed_at_index` (DecayingSortedField partition) on AgentSession. Out of scope for this plan; track as `[SEPARATE-SLUG]` only if the BUILD validation phase shows degraded performance.
- Document the linear scaling in the feature doc.

### Risk 4: ConfidenceField semantics differ from manual confidence formula
**Impact:** The branch's formula was `success_rate * min(sample_count/10, 1.0)`. ConfidenceField uses Bayesian update via ObservationProtocol. The numbers won't match historical expectations.
**Mitigation:**
- This is a *feature*, not a bug — the whole reason PR #391 was closed was to use the canonical primitive.
- Document the change explicitly in `docs/features/behavioral-episode-memory.md` so anyone debugging confidence scores knows to read the ConfidenceField semantics.
- Keep `success_count`, `sample_count` as plain IntFields for diagnostic transparency. Only `confidence` (and the derived `success_rate` if any consumer reads it) is Popoto-managed.

## Race Conditions

### Race 1: Concurrent reflections during the same daily window
**Location:** `reflections/behavioral_learning.py::run()`
**Trigger:** The reflection is registered as enabled in two reflections schedulers (e.g., one on the bridge machine, one on a secondary machine post-update).
**Data prerequisite:** Both reflections see the same set of unprocessed AgentSessions.
**State prerequisite:** The dedup query at `behavioral_learning.py:92` (`existing = CyclicEpisode.query.filter(raw_ref=session.agent_session_id)`) and the topology+layer+branch dedup at lines 112-123 must run on consistent data.
**Mitigation:** Reflections are single-machine (per `## Single-Machine Ownership` in CLAUDE.md). Add an explicit comment in the reflection file noting this. The dedup logic also tolerates concurrent runs via Popoto's last-write-wins — a duplicate Episode would be the worst-case outcome and the cleanup reflection (#1271 family) would prune the duplicate. No additional locking required.

### Race 2: Tool-event append racing with session save during high-throughput stages
**Location:** `.claude/hooks/post_tool_use.py::_update_agent_session()` and `agent/sdk_client.py` worker save path.
**Trigger:** A long-running tool call completes mid-flight while the worker is concurrently saving the session (e.g., updating `last_message_id`).
**Data prerequisite:** Both writers read `tool_sequence` before mutating it. A pure read-modify-write would lose entries.
**State prerequisite:** Popoto's `save(update_fields=["tool_sequence"])` performs a Redis HSET on just that field, which is atomic for a single field write but not for read-modify-write.
**Mitigation:** Use Popoto's recommended pattern: `agent_session.refresh()` or re-query inside the hook before the append, then save. The capped truncation absorbs the rare race (last 50 entries kept regardless of order). For absolute correctness, the append helper can use `redis.rpush` directly on the underlying ListField key, then trim — investigate at BUILD time. If `redis.rpush` isn't surfaced cleanly through Popoto, accept the rare race (loss of one tool_sequence entry every few thousand) since the data is observational, not authoritative.

### Race 3: Reflection deletes an episode while crystallization is reading it
**Location:** `reflections/behavioral_learning.py:175-244` (clustering loop) and `models/cyclic_episode.py::cleanup_expired()`.
**Trigger:** A long-running reflection re-reads `CyclicEpisode.query.all()` while another reflection (or manual cleanup) deletes old episodes.
**Data prerequisite:** The cluster computation expects a stable list of episodes.
**State prerequisite:** Episodes >180 days old can be deleted at any time.
**Mitigation:** Reflections are single-machine and serial within a process. Cross-process risk is mitigated by `#1271`'s reflection serialization. The crystallization step iterates a snapshot returned by `query.all()` — once read, the deletion of an episode mid-loop is harmless (we have the local reference). This race is real but trivially impactful.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1310] Hooking the agent's planning loop to auto-call `recall_pattern()` at stage entry. The retrieval helper exists; integration is its own design problem (when to call, how to inject, how to avoid noise). Track in #1310's follow-up. (Note: #1310 is already closed; if revisited as an integration plan, will need a new issue. The agent is free to file one if BUILD demonstrates the helper is materially valuable.)
- [SEPARATE-SLUG #376] Cross-machine pattern sync (`scripts/pattern_sync.py` from branch). Issue #376 is closed; the sync feature was deferred there and is not in #1358's acceptance criteria.
- [DESTRUCTIVE] Back-filling historical AgentSessions with synthetic `tool_sequence` data. Sessions completed before revival have no trajectory; this is acceptable.
- [EXTERNAL] Removing `behavioral_learning.py` and the `config/reflections.yaml:271-277` entry — the issue's "side cleanup if deferred" branch. We are not deferring, so this branch does not apply.

## Update System

No update-system changes required. All new code lives within the repo (`models/`, `scripts/`, `agent/`, `tools/`, `.claude/hooks/`, `pyproject.toml`). The new `valor-trajectory` CLI registers in `pyproject.toml [project.scripts]` and becomes available after `pip install -e .` or `uv sync`, both of which run during `/update`. No new env vars (uses existing `ANTHROPIC_API_KEY`). No new Redis indexes that must be rebuilt at install time — Popoto creates them on first model save.

The update skill (`scripts/remote-update.sh`) does not need changes; it already runs `uv sync` which picks up the new `[project.scripts]` entry.

## Agent Integration

The `valor-trajectory` CLI is the agent integration surface. Once registered in `pyproject.toml [project.scripts]`, the agent invokes it through the existing Bash tool (no new MCP server, no `.mcp.json` change, no bridge import). Pattern matches existing tools like `valor-telegram`, `valor-email`, `valor-tts`, etc.

- Whether a new or existing MCP server in `mcp_servers/` needs to expose this functionality: **No.** CLI-via-Bash is the canonical pattern for tools the agent uses occasionally rather than every turn.
- Changes to `.mcp.json` registration: **None.**
- Whether the bridge itself (`bridge/telegram_bridge.py`) needs to import/call the new code directly: **No.**
- Integration test that verifies the agent can actually invoke the new capability: a test in `tests/integration/test_trajectory_retrieval.py` that runs `subprocess.run(["valor-trajectory", "recall", "--topology", "bug_fix", "--layer", "agent"])` and asserts exit code 0 + non-error stdout, with the binary resolved through the `.venv/bin/` path the agent uses.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/behavioral-episode-memory.md` — port from `04af11fd:docs/features/behavioral-episode-memory.md` with updates: (a) Popoto-primitive section replacing manual confidence/decay scaffolding, (b) live PostToolUse wiring instead of `bridge/session_transcript`, (c) `valor-trajectory` CLI section.
- [ ] Add entry to `docs/features/README.md` index table referencing the feature doc.
- [ ] Update `docs/features/reflections.md` if it currently lists `behavioral_learning` as inert — change the description to reflect the active behavior.

### External Documentation Site
Not applicable — this repo does not have a Sphinx/MkDocs site.

### Inline Documentation
- [ ] Module docstring on `models/cyclic_episode.py` and `models/procedural_pattern.py` explaining the abstraction-barrier (Reflections is sole writer; agent code is read-only).
- [ ] Docstring on `agent/trajectory_retrieval.py::recall_pattern` documenting return shape, ranking semantics (DecayingSortedField), and the structural-not-content guarantee.
- [ ] Comment in `.claude/hooks/post_tool_use.py::_update_agent_session()` explaining why `append_tool_event` lives inside the existing try/except envelope.
- [ ] Comment in `reflections/behavioral_learning.py:31-42` updating the legacy "skip" path — note that the ImportError fallback remains for safety but is unreachable in steady state.

## Success Criteria

- [ ] `models/cyclic_episode.py` and `models/procedural_pattern.py` exist on main; both define `vault`, fingerprint fields, and `last_reinforced`/`confidence` via Popoto primitives where applicable.
- [ ] `python -c "import asyncio, reflections.behavioral_learning; asyncio.run(reflections.behavioral_learning.run())"` returns `{"status": "ok", "findings": [...], "summary": "..."}` with `summary` containing a non-zero `episodes` count on a session-populated dev DB.
- [ ] `models/agent_session.py` declares `tool_sequence: ListField` and `friction_events: ListField` with `append_tool_event` and `append_friction_event` helpers that cap at 50 and 20 entries respectively.
- [ ] `.claude/hooks/post_tool_use.py::_update_agent_session()` calls `append_tool_event(stage, tool_name)` per tool invocation; verified via a test that runs the hook with a fixture session and asserts the AgentSession `tool_sequence` grows.
- [ ] `scripts/fingerprint_classifier.py::classify_session(session)` returns `{"problem_topology": str, "affected_layer": str, "ambiguity_at_intake": float, "acceptance_criterion_defined": bool}` with `problem_topology != "ambiguous"` for ≥80% of a 20-session sample drawn from the dev DB. Sample command: `python scripts/fingerprint_classifier.py --validate-sample 20` (new CLI subcommand on the classifier).
- [ ] `agent/trajectory_retrieval.py::recall_pattern(topology, layer, limit=5)` returns a list of ProceduralPattern instances ranked by decayed `last_reinforced`. Verified by a test seeding 3 patterns at distinct `last_reinforced` ages and asserting the most-recent ranks first when confidences are equal.
- [ ] `valor-trajectory recall --topology bug_fix --layer agent` works from the venv bin path with exit code 0; `valor-trajectory recall --topology bogus --layer agent` exits 2 with argparse error mentioning the valid enum values.
- [ ] After running the reflection once on a session-populated dev DB, `logs/worker.log` (or the script's stdout) contains `Created N behavioral episodes` with N ≥ 1 — NOT `not available — skipping`.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `pyproject.toml [project.scripts]` includes `valor-trajectory = "tools.trajectory.cli:main"`; `which valor-trajectory` resolves inside the venv after `uv sync`.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly.

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Port `CyclicEpisode` and `ProceduralPattern` from `04af11fd`, modernizing to Popoto primitives. Add `tool_sequence`/`friction_events`/append helpers to `AgentSession`.
  - Agent Type: builder
  - Resume: true

- **Builder (instrumentation)**
  - Name: hook-builder
  - Role: Wire `append_tool_event` / `append_friction_event` calls into `.claude/hooks/post_tool_use.py::_update_agent_session()` and `agent/sdk_client.py` exit path. Add tests for both surfaces.
  - Agent Type: builder
  - Resume: true

- **Builder (classifier)**
  - Name: classifier-builder
  - Role: Port `scripts/fingerprint_classifier.py` from branch, update to current `AsyncAnthropic` surface, add `--validate-sample N` subcommand and tests for fallback paths.
  - Agent Type: builder
  - Resume: true

- **Builder (retrieval + CLI)**
  - Name: retrieval-builder
  - Role: Create `agent/trajectory_retrieval.py` and `tools/trajectory/cli.py`. Register `valor-trajectory` in `pyproject.toml`. Write integration test.
  - Agent Type: builder
  - Resume: true

- **Validator (end-to-end)**
  - Name: e2e-validator
  - Role: Run the full end-to-end: seed sessions → run reflection → verify episodes created → verify crystallization → run CLI → assert ranked output. Confirm acceptance criterion #5 (worker.log message change) on a manual run.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-author
  - Role: Port `docs/features/behavioral-episode-memory.md` from branch, modernize for Popoto primitives, update `docs/features/README.md` index, refresh `docs/features/reflections.md` if needed.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Standard tier-1 + documentarian. No specialists needed; this is a port + modernize, not a novel design.

## Step by Step Tasks

### 1. Build models layer
- **Task ID**: build-models
- **Depends On**: none
- **Validates**: `tests/unit/test_behavioral_episode_memory.py` (port + modernize from `04af11fd:tests/unit/test_behavioral_episode_memory.py`)
- **Informed By**: spike-1 (use ConfidenceField for ProceduralPattern); spike-2 (use DecayingSortedField for `last_reinforced` only).
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Port `models/cyclic_episode.py` from `git show 04af11fd:models/cyclic_episode.py`. Keep the schema and the enum constants. Replace any manual confidence/decay plumbing with Popoto primitives where applicable (CyclicEpisode itself stays mostly as-is — `created_at = SortedField(type=float)` is correct).
- Port `models/procedural_pattern.py` from `04af11fd`. Replace `confidence = Field(type=float, default=0.0)` and `_compute_confidence()` with `confidence = ConfidenceField(initial_confidence=0.5)`. Replace `last_reinforced = SortedField(type=float)` with `last_reinforced = DecayingSortedField(decay_rate=0.5, base_score_field="confidence")`. Inherit `AccessTrackerMixin`. Update `reinforce()` to call `self.observe(success=success)` instead of manual `success_count++`.
- Add `tool_sequence`, `friction_events`, `_TOOL_SEQUENCE_MAX = 50`, `_FRICTION_EVENTS_MAX = 20`, `append_tool_event()`, `append_friction_event()` to `models/agent_session.py`. Port helpers from `04af11fd:models/agent_session.py:327-366`; keep the silent-on-save-failure pattern.
- Update `models/__init__.py` to export `CyclicEpisode` and `ProceduralPattern`.
- Port and update `tests/unit/test_behavioral_episode_memory.py` from the branch. Tests that asserted manual `_compute_confidence` math must be updated for ConfidenceField semantics; tests on schema, enums, append helpers, and dedup carry over unchanged.

### 2. Validate models layer
- **Task ID**: validate-models
- **Depends On**: build-models
- **Assigned To**: e2e-validator (acting as model validator first)
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_behavioral_episode_memory.py -xvs` and confirm all ported tests pass.
- Verify `python -c "from models.cyclic_episode import CyclicEpisode; from models.procedural_pattern import ProceduralPattern; from models.agent_session import AgentSession; s = AgentSession(); s.append_tool_event('BUILD','Edit'); print(s.tool_sequence)"` works without exceptions.
- Verify `python -c "from popoto import ConfidenceField, DecayingSortedField; from models.procedural_pattern import ProceduralPattern; assert isinstance(ProceduralPattern.confidence, ConfidenceField); assert isinstance(ProceduralPattern.last_reinforced, DecayingSortedField)"` succeeds.

### 3. Build instrumentation hooks
- **Task ID**: build-instrumentation
- **Depends On**: validate-models
- **Validates**: `tests/unit/test_post_tool_use.py` (locate or create), `tests/unit/test_sdk_client_friction.py` (create)
- **Informed By**: spike-3 (PostToolUse is the live wire); spike-4 (two emit sites, hook + harness exit).
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- In `.claude/hooks/post_tool_use.py::_update_agent_session()`, after the existing `tool_call_count` increment, add: `agent_session.append_tool_event(stage=agent_session.current_stage or "UNKNOWN", tool_type=hook_input.get("tool_name", "unknown"))`. Inside the existing `try/except` envelope so failures are swallowed.
- In the same function, on tool-error events (`hook_input.get("tool_response", {}).get("is_error")`), call `agent_session.append_friction_event(stage, description=f"{tool_name}_failed: {short_error_text}")` with description truncated to 200 chars.
- In `agent/sdk_client.py`, immediately after the existing `_store_exit_returncode` call (search for `_store_exit_returncode(`), add a sibling `_record_harness_friction(session_id, returncode, current_stage)` helper that resolves the AgentSession, calls `append_friction_event(stage, f"harness_exit_code: {returncode}")` when returncode is non-zero. Best-effort; swallow all exceptions.
- Add unit tests for both hook surfaces.

### 4. Validate instrumentation
- **Task ID**: validate-instrumentation
- **Depends On**: build-instrumentation
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run a real local Claude Code session via `claude -p "echo hello"` and confirm a corresponding AgentSession in Redis has `tool_sequence` populated with at least one entry.
- Run a session that intentionally errors (e.g., `claude -p "Read /nonexistent"`) and confirm `friction_events` has at least one entry.
- Benchmark `_update_agent_session()` before/after with a microbenchmark; assert median latency increase ≤ 2ms.

### 5. Build classifier
- **Task ID**: build-classifier
- **Depends On**: validate-models
- **Validates**: `tests/unit/test_fingerprint_classifier.py` (create)
- **Informed By**: spike-5 (port-with-SDK-modernize).
- **Assigned To**: classifier-builder
- **Agent Type**: builder
- **Parallel**: true (with build-instrumentation)
- Port `scripts/fingerprint_classifier.py` from `04af11fd`. Update the `AsyncAnthropic` instantiation to match `agent/memory_extraction.py` patterns (same client, same retry, same model name `claude-haiku-4-5` if used by extraction, otherwise the explicit haiku-4-5 string).
- Validate output against `models.cyclic_episode.PROBLEM_TOPOLOGIES` and `AFFECTED_LAYERS`. Reject invalid values, fall back to `ambiguous`/`unknown`.
- Add a `--validate-sample N` CLI subcommand that pulls N recent sessions and prints the per-session classifier output as a table (no Episode created — read-only). Used for tuning the prompt and meeting the 80% non-ambiguous criterion.
- Tests for: malformed JSON, network error, invalid enum value, empty session metadata, success path. Mirror the branch's test patterns.

### 6. Validate classifier
- **Task ID**: validate-classifier
- **Depends On**: build-classifier
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/fingerprint_classifier.py --validate-sample 20` against the dev DB. Assert ≥80% of the 20 sessions classified non-ambiguous topology.
- If <80%, iterate the prompt with the classifier-builder until passing or document a justified lower threshold (e.g., the dev DB happens to have more genuinely-ambiguous sessions than production would).

### 7. Build retrieval helper + CLI
- **Task ID**: build-retrieval
- **Depends On**: validate-models
- **Validates**: `tests/integration/test_trajectory_retrieval.py` (create)
- **Informed By**: spike-2 (DecayingSortedField makes ranking trivial).
- **Assigned To**: retrieval-builder
- **Agent Type**: builder
- **Parallel**: true (with build-instrumentation, build-classifier)
- Create `agent/trajectory_retrieval.py::recall_pattern(problem_topology: str, affected_layer: str, limit: int = 5) -> list[ProceduralPattern]`. Filter by `problem_topology` and `affected_layer`, return up to `limit`. Sort by `last_reinforced` (DecayingSortedField) descending. Empty list on filter miss; raise on argument validation failure.
- Create `tools/trajectory/__init__.py` (empty), `tools/trajectory/cli.py` (argparse with subcommands `recall`, `list`, `inspect`, `--json` flag), and `tools/trajectory/__main__.py` (entry point delegating to cli).
- Register `valor-trajectory = "tools.trajectory.cli:main"` in `pyproject.toml [project.scripts]`. Run `uv sync` (or `pip install -e .`) to install the new entry point.
- Integration test: seed 3 ProceduralPattern records with distinct `last_reinforced` (now, 1d ago, 100d ago) and equal `confidence`. Assert `recall_pattern("bug_fix", "agent")` returns them in the expected (most-recent-first) order.
- CLI test (subprocess.run): `valor-trajectory recall --topology bug_fix --layer agent --json` returns valid JSON; invalid enum returns exit code 2.

### 8. Validate retrieval + CLI
- **Task ID**: validate-retrieval
- **Depends On**: build-retrieval
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run integration test in isolation; pass.
- Manually run `valor-trajectory recall --topology bug_fix --layer agent` from the venv and assert sane output on the dev DB (may be empty if classifier+reflection haven't run yet — that's acceptable; the CLI must still exit 0 and print "No matching patterns").

### 9. End-to-end smoke
- **Task ID**: e2e-smoke
- **Depends On**: validate-models, validate-instrumentation, validate-classifier, validate-retrieval
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Manually trigger the reflection on the dev DB: `python -c "import asyncio, reflections.behavioral_learning; r = asyncio.run(reflections.behavioral_learning.run()); print(r)"`. Assert `status == "ok"` AND `summary` does NOT contain "skipped" AND `findings` is non-empty.
- Verify the reflection's log line is `Created N behavioral episodes…` (not `not available — skipping`). Capture the log line in the validator's report.
- Run the same reflection a second time within seconds — assert no duplicate episodes are created (dedup works).
- Confirm `valor-trajectory recall --topology <whatever-the-classifier-found> --layer <whatever>` returns the freshly-crystallized pattern (only if ≥3 episodes in the same cluster were created — if fewer, document explicitly that crystallization needs more data and the smoke is a partial pass).

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: e2e-smoke
- **Assigned To**: docs-author
- **Agent Type**: documentarian
- **Parallel**: false
- Port `docs/features/behavioral-episode-memory.md` from `04af11fd`. Modernize the model section to reference Popoto primitives. Add a section documenting the `valor-trajectory` CLI. Add a section on the live wiring point (`.claude/hooks/post_tool_use.py`).
- Add an entry to `docs/features/README.md` index table linking the feature doc.
- Open `docs/features/reflections.md` and verify the `behavioral_learning` description reflects the active behavior (not the skip path).
- Update inline docstrings on the new modules.

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature, build-models, build-instrumentation, build-classifier, build-retrieval, e2e-smoke
- **Assigned To**: e2e-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands from the Verification table.
- Verify all Success Criteria checkboxes pass.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Models import | `python -c "from models.cyclic_episode import CyclicEpisode; from models.procedural_pattern import ProceduralPattern; from models.agent_session import AgentSession; AgentSession().append_tool_event('BUILD','Edit')"` | exit code 0 |
| Popoto primitives wired | `python -c "from popoto import ConfidenceField, DecayingSortedField; from models.procedural_pattern import ProceduralPattern; assert isinstance(ProceduralPattern.confidence, ConfidenceField); assert isinstance(ProceduralPattern.last_reinforced, DecayingSortedField)"` | exit code 0 |
| Reflection produces output | `python -c "import asyncio, reflections.behavioral_learning, json; r = asyncio.run(reflections.behavioral_learning.run()); assert r['status']=='ok'; assert 'skipped' not in r['summary'].lower(); print(json.dumps(r))"` | exit code 0 |
| CLI installed | `which valor-trajectory` | output contains `.venv/bin` |
| CLI valid invocation | `valor-trajectory recall --topology bug_fix --layer agent --json` | exit code 0 |
| CLI invalid enum | `valor-trajectory recall --topology bogus --layer agent` | exit code 2 |
| Tests pass | `pytest tests/unit/test_behavioral_episode_memory.py tests/unit/test_fingerprint_classifier.py tests/integration/test_trajectory_retrieval.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check models/cyclic_episode.py models/procedural_pattern.py models/agent_session.py agent/trajectory_retrieval.py scripts/fingerprint_classifier.py tools/trajectory/` | exit code 0 |
| Format clean | `python -m ruff format --check models/cyclic_episode.py models/procedural_pattern.py models/agent_session.py agent/trajectory_retrieval.py scripts/fingerprint_classifier.py tools/trajectory/` | exit code 0 |
| Classifier non-ambiguous rate | `python scripts/fingerprint_classifier.py --validate-sample 20` | output contains `non_ambiguous_pct: 8` (or 9, or 10) — i.e., ≥80% |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Confidence-semantics break is acceptable, right?** ConfidenceField uses Bayesian update via ObservationProtocol; the branch's `success_rate * min(sample_count/10, 1.0)` formula will produce different numbers. The plan treats this as a feature (it's the canonical primitive). Confirm this is the right call rather than preserving the legacy formula via a custom field.
2. **Acceptable to gate BUILD on the classifier 80% rate?** If the classifier comes in at, say, 65% non-ambiguous on the dev DB sample, do we (a) iterate the prompt until it crosses 80%, (b) ship at 65% and document, or (c) introduce the regex-based hybrid fallback during BUILD rather than as Risk-1 mitigation?
3. **Should `valor-trajectory` be exposed to the agent during this slug, or stay as a human-only diagnostic CLI?** The plan registers the entry point but does NOT add the CLI to any allowlist or wire it into agent prompts. Confirm "ship the CLI but defer agent-side use" matches your intent.
4. **The reflection is currently registered with `priority: low` in `config/reflections.yaml`.** Once it's producing real output, should the priority be promoted? Plan does not change priority; happy to add a follow-up step if you want it bumped.
5. **The `vault="shared"` design for ProceduralPattern means patterns leak across projects.** That's deliberate — patterns are structural and should be reusable. Confirm acceptable; otherwise switch to `vault="mem:{project_key}"` and accept that the pattern store stays small per-project.
