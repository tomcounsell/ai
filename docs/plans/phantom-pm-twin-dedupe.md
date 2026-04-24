---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-04-24
revised: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1157
last_comment_id: 4311794216
revision_applied: true
---

# Phantom PM Twin Dedupe

## Revision Log

- **2026-04-24 (revision 4)** — Revision pass in response to the second `/do-plan-critique` verdict `NEEDS REVISION` (recorded 08:43:05 UTC against plan hash `sha256:a4eaf47e...` — the revision-2 post-pivot state). The prior revisions 2 and 3 structurally addressed the first verdict (drop cleanup, focus on prevention, add integration test). This revision 4 addresses residual concerns not yet landed on-file:
  - Set `revision_applied: true` in frontmatter so the SDLC router transitions from Row 4b (concern-triggered revision) to Row 4c (proceed to build after next critique pass).
  - Record `last_comment_id: 4311794216` (Phase 2.7 of `/do-plan`). Two comments exist on the tracking issue since the plan was filed; both are now recorded.
  - Updated the Critique Results section to reflect the second NEEDS REVISION verdict (plan hash `a4eaf47e`), not only the first (`9209503e`). Added explicit mappings for each concern to where in this revision it was addressed.
  - Added a new risk (Risk 5: `session_id` filter lookup is non-indexed) — `AgentSession.session_id` is `Field()` not `KeyField()`, so the `VALOR_SESSION_ID` fallback does a Redis full-scan. Cost is negligible (fallback fires only when `AGENT_SESSION_ID` is missing), but worth documenting so a future reader doesn't mistake it for a hot path.
  - Tightened No-Gos: explicitly called out that `AgentSession.create_local()`'s method body stays untouched (previously only in Rabbit Holes).
  - Line-by-line re-verification of all cited file positions against current HEAD (`47ce861e` as of this revision): all pointers — `user_prompt_submit.py:105/109/117/134/148`, `stop.py:138/145/146/147/148/149/150`, `sdk_client.py:1345-1351` — still match. Freshness disposition remains `Unchanged`.
- **2026-04-24 (revision 3)** — Revision pass in response to `/do-plan-critique` verdict `NEEDS REVISION` (recorded against plan hash `sha256:9209503e...`, the pre-revision-2 state). The critique findings — primarily that the plan over-indexed on cleanup and the prevention story was diffuse — were already addressed structurally by revision 2 (committed at `8b7b9c1e` as `plan revision(#1157): prevention-focused approach, drop cleanup utility`). Revision 3 adds (a) verification that every file:line pointer in the plan still matches current HEAD (`46b2de03`), (b) a Critique Results entry acknowledging the NEEDS REVISION verdict and mapping it to the revision-2 changes, and (c) sets `status: Ready` since all three open questions are resolved and a fresh critique is the next step, not another revision. No substantive plan edits — the prevention-focused approach from revision 2 is the correct direction.
- **2026-04-24 (revision 2)** — Reoriented around **prevention**, not cleanup, per Valor feedback: "Our solution here is more about cleanup when it should be a higher focus on prevention. Ideally something like a cleanup phantom twin function should never need to be used. We should always place way more attention on prevention than band-aids." Cut the `tools/cleanup_phantom_twins.py` dry-run utility entirely. Sharpened the "Why this bug happens" analysis and centered the plan on guards at the exact creation site (`user_prompt_submit.py::main` line 134's `AgentSession.create_local()` call). Added an integration test asserting PM terminal-transition still fires after the `stop.py` change (answer to open question 2). Preserved the `local-{session_id}` legacy fallback in `stop.py` for direct-CLI users (answer to open question 3).
- **2026-04-24 (revision 1)** — Initial plan drafted by `/do-plan`. Scope widened during recon to cover `stop.py:146-147` reconstructing `local-{session_id}` instead of reading the sidecar's `agent_session_id`.

## Problem

Every worker-spawned PM or Teammate subprocess produces two `AgentSession` rows in Redis for a single physical Claude Code subprocess — the legitimate worker-created record, plus a `local-{claude_uuid}` "phantom twin" created by `.claude/hooks/user_prompt_submit.py` on the subprocess's first prompt. Both rows share the same `claude_session_uuid`. The phantom carries the real PM as `parent_agent_session_id` (because `VALOR_PARENT_SESSION_ID` is set), so it appears as a child of its own real self in the session tree.

**Current behavior:**
- `valor-session children --id {pm}` lists the PM's own `local-*` twin as a child, indistinguishable from a legitimate dispatched child.
- `wait-for-children` on a PM terminates instantly: the phantom's Stop hook fires at subprocess exit, the parent sees "children complete," and the PM exits successfully in ~30 seconds without ever dispatching real dev work.
- The dashboard and `python -m tools.agent_session_scheduler list` show ghost "child PM" sessions that look like runaway delegation.
- The phantom carries the full enriched prompt (`PROJECT:\nFOCUS:\nFROM:\nSESSION_ID:\nTASK_SCOPE:\nSCOPE:\nMESSAGE:...`) as its `message_text`, polluting displays and searches.

**Desired outcome:**
- A worker-spawned PM or Teammate subprocess produces exactly ONE `AgentSession` row (the worker-created one). Zero `local-*` twins.
- PostToolUse, Stop, and SubagentStop hooks continue to work against the existing worker-created `AgentSession`.
- `wait-for-children` reflects only real dispatched children, not phantom self-references.

## Why This Bug Happens (Root Cause)

Phantoms come from exactly **one creation site**: the `AgentSession.create_local(...)` call at `.claude/hooks/user_prompt_submit.py:134`. Every other code path in `user_prompt_submit.py`, `stop.py`, `post_tool_use.py`, `subagent_stop.py`, `sdk_client.py`, and `tools/valor_session.py` merely *reads* or *transitions* sessions — none of them births one. The entire phantom phenomenon is a single unguarded mint at that line.

The precondition chain that leads to that call:
1. Worker spawns a subprocess and sets `AGENT_SESSION_ID` + `VALOR_SESSION_ID` env vars at `agent/sdk_client.py:1345-1351`. The worker has **already created** the authoritative `AgentSession` record before spawning.
2. Subprocess starts. On first prompt, `user_prompt_submit.py::main()` runs. Sidecar is empty (fresh subprocess), so control enters the `else:` branch at line 105.
3. The gate at line 109 (`if not VALOR_PARENT_SESSION_ID and not SESSION_TYPE: return`) was introduced by PR #1002 to block direct-CLI orphans. **Worker-spawned subprocesses have both env vars set**, so the gate passes by design.
4. Line 134 calls `create_local(...)` unconditionally — **without ever checking whether the worker already minted a real record**. Phantom is born.

**The missing invariant:** `user_prompt_submit.py` should never mint an `AgentSession` when `AGENT_SESSION_ID` (or `VALOR_SESSION_ID`) already resolves to a live, worker-owned record. The env vars exist precisely to communicate "I already own this subprocess." The hook never consulted them.

**Prevention, not cleanup:** the fix must refuse to create at that line, not clean up after creation. If prevention lands correctly, no phantom ever gets written to Redis. A cleanup utility would signal that prevention has failed; we don't ship one.

## Prevention Strategy

Every change in this plan is a guard placed at a session-creation or lifecycle decision point, so that the wrong `AgentSession` cannot be created or looked up in the first place.

| Site | Guard | Effect |
|------|-------|--------|
| `.claude/hooks/user_prompt_submit.py:134` (`create_local()` call) | New env-var resolution block inserted ahead of the gate at line 109: resolve `AGENT_SESSION_ID` via `AgentSession.get_by_id()`, fallback to `VALOR_SESSION_ID` via `query.filter(session_id=...)`. If resolved and non-terminal, write sidecar + return — the `create_local` call is never reached. | Phantom is never minted. |
| `.claude/hooks/stop.py:146-147` (key-reconstruction lookup) | Primary lookup via `AgentSession.get_by_id(sidecar["agent_session_id"])`. Legacy `local-{session_id}` fallback retained for direct-CLI sessions (answer to open question 3). | Correct record is found at subprocess exit; no silent misses on worker-spawned sessions. |
| `.claude/hooks/post_tool_use.py`, `.claude/hooks/subagent_stop.py` (DB-lookup reconstructions, if any) | Same sidecar-first pattern as stop.py where DB lookups use `local-{session_id}` reconstruction. Log-path-only uses of `local-*` remain untouched. | No hook silently operates on the wrong record. |
| `agent/sdk_client.py:1343-1369` (env-var injection ordering) | Build-step verification that AgentSession `create()`/`save()` completes **before** `subprocess.Popen` is called. Documented as a race-prevention invariant in the code comment. | Worker record is always visible to Redis readers before the child subprocess's first prompt arrives. |

Explicitly **not** in scope:
- A `tools/cleanup_phantom_twins.py` utility. Prevention makes it unnecessary; a cleanup utility is a band-aid. Existing phantoms in Redis remain as benign historical noise (they are already completed/terminal and cannot resurrect — see #1113's terminal-state guard).
- Automated phantom deletion at worker startup. Same reasoning.

## Freshness Check

**Baseline commit (rev 4 re-verification):** `47ce861e` — all referenced file:line positions re-checked against this HEAD on 2026-04-24 as part of revision 4. Previous rev 3 baseline was `46b2de03`; no relevant code has changed between those two commits that touches the cited hook files, `sdk_client.py`, or `session_lifecycle.py`.
**Issue filed at:** 2026-04-24T07:18:13Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/hooks/user_prompt_submit.py:46-149` — issue claimed the phantom-creation `try` block. Verified at plan time: the sidecar-load at line 55, the fallback `else:` at line 105, the env-var gate at line 109, the `local-{session_id}` construction at line 117, the `create_local()` call at line 134, and the sidecar write at line 148 all match exactly.
- `agent/sdk_client.py:1343-1369` — issue claimed this is where the four env vars are set before spawning the subprocess. Verified at plan time: `VALOR_SESSION_ID` = the bridge `session_id` (e.g. `0_1777...`) at line 1346, `AGENT_SESSION_ID` = the worker's `agent_session_id` UUID at line 1351, `VALOR_PARENT_SESSION_ID` = the parent UUID at line 1359 (PM/Teammate only), `SESSION_TYPE` = the persona at line 1369.
- `agent/sdk_client.py:2492-2531` — issue claimed this is where the enrichment prefix is built. Not critical to the fix; not re-verified line-by-line, but the observed phantom `message_text` in the issue table matches the enrichment format, so the claim holds.
- `.claude/hooks/stop.py:146` — issue claimed Stop hook references `local-{session_id}`. **Verified with a drift finding:** stop.py at line 146-147 rebuilds `local-{session_id}` and runs `AgentSession.query.filter(session_id=sidecar_session_id)`. The sidecar's `agent_session_id` field (line 138) is used only as a "did we register a session?" presence gate; the actual record lookup discards it and uses the reconstructed `local-*` key. After this fix, worker-spawned subprocesses will NOT have a `local-*` record, so this lookup will miss. Details under "Revised" in the issue's Recon Summary.

**Cited sibling issues/PRs re-checked:**
- #1001 / PR #1002 — closed 2026-04-16, MERGED. Added the `VALOR_PARENT_SESSION_ID or SESSION_TYPE` gate. The gate blocks direct-CLI-only invocations but allows worker-spawned — which is exactly the population this plan addresses.
- #808 / PR (#821 per issue) — closed 2026-04-07. Introduced `VALOR_PARENT_SESSION_ID` for child→parent linkage. Still the mechanism causing the phantom to appear as a child of itself.
- #1113 / PR #1121 — closed 2026-04-22, MERGED. Fixed zombie terminal-session revival in the same file. The terminal-state guard at `user_prompt_submit.py:80-90` was added there and must NOT be touched by this plan.
- #1147 / PR #1151 — closed 2026-04-24, MERGED (earlier today). Same dedup pattern in a different entry point (`sdlc_session_ensure`). Good reference implementation: env-var short-circuit at the top of the function, fallthrough on miss.
- #1148 — closed 2026-04-24, MERGED. Added `_ENRICHMENT_HEADER_RE` guard in `valor-session create` to block re-spawning with a pre-enriched header. Different layer (CLI, not hook). Not touched.

**Commits on main since issue was filed (touching referenced files):** None. `git log --since="2026-04-24T07:18:13Z"` shows only `46b2de03 Plan revision: apply concerns from READY-TO-BUILD critique for #1155`, which does not touch any hook, `sdk_client.py`, or `memory_bridge.py`.

**Active plans in `docs/plans/` overlapping this area:** None active. `fix-local-session-type.md` (issue #809, status `docs_complete`) and `fix-child-session-parent-linkage.md` (issue #808, status `docs_complete`) both touched the same hook but their work is merged and these are not active plans. No coordination blocker.

**Notes:** The Stop hook drift finding (stop.py reconstructs `local-{session_id}` instead of using the sidecar's `agent_session_id`) elevates a "verify this" item in the issue into a **mandatory code change in the same PR**. The issue's claim that "fix is expected to be confined to `.claude/hooks/user_prompt_submit.py`" is revised to "confined to `.claude/hooks/user_prompt_submit.py` + `.claude/hooks/stop.py` + `.claude/hooks/subagent_stop.py` + `.claude/hooks/post_tool_use.py` wherever a `local-{session_id}` reconstruction lookup exists."

## Prior Art

- **#1001 / PR #1002** (merged 2026-04-16): "gate AgentSession creation to worker-spawned sessions only" — added the `VALOR_PARENT_SESSION_ID or SESSION_TYPE` gate. Prevented direct-CLI orphans. Did NOT address worker-spawned twins. This is the immediate predecessor.
- **#808 / PR #821** (merged 2026-04-07): "Fix child session parent linkage" — added `VALOR_PARENT_SESSION_ID` propagation so child subprocesses store the PM's UUID. Necessary infrastructure; also the mechanism by which the phantom appears as a child.
- **#1113 / PR #1121** (merged 2026-04-22): "prevent zombie session revival + cascade-kill children" — added the terminal-state guard in the re-activation branch at `user_prompt_submit.py:80-90`. MUST NOT be touched by this plan.
- **#1147 / PR #1151** (merged 2026-04-24, earlier today): "dedup sdlc_session_ensure against bridge-initiated PM sessions" — same pattern, different entry point. The env-var short-circuit at the top of `ensure_session()` is the reference template this plan's fix should mirror.
- **#1148** (merged 2026-04-24): "PM harness sessions missing SESSION_TYPE env and persona" — added `_ENRICHMENT_HEADER_RE` in `valor-session create` to block re-enriched spawns. Different layer. Not touched.
- **#809 / fix-local-session-type plan** (docs_complete): added `SESSION_TYPE` env var propagation into `create_local()` kwargs. Touched the same file. Not touched here.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1002 (#1001) | Added `VALOR_PARENT_SESSION_ID or SESSION_TYPE` gate in `user_prompt_submit.py` before `create_local()` | The gate was designed to block **direct CLI** invocations (no env vars), not to dedupe worker-spawned subprocesses. Worker-spawned subprocesses DO have both env vars set, so the gate intentionally passes — and the creation proceeds, producing the phantom. The fix addressed the wrong population. |
| PR #821 (#808) | Introduced `VALOR_PARENT_SESSION_ID` so child subprocesses link to their parent PM | Fixed the parent-linkage gap for legitimate children (dev-spawned), but the same env var is ALSO set on worker-spawned PM/Teammate subprocesses (which are themselves the "child" — they have no real child yet at spawn time). This mis-labels the PM's own phantom as a child of itself. |

**Root cause pattern:** All prior fixes correctly identified that there are multiple populations (direct CLI, worker-spawned PM, PM-spawned child) but none of them distinguished "this subprocess has its OWN worker-created `AgentSession` already" from "this subprocess needs a `local-*` record created for it." The missing signal is `AGENT_SESSION_ID` / `VALOR_SESSION_ID` — set by the worker precisely to communicate "I own you already." The hook never checked for it.

## Data Flow

1. **Entry point**: `bridge/telegram_bridge.py` receives a message, writes to Redis. The worker picks it up.
2. **Worker (`worker/__main__.py` -> `agent/sdk_client.py`)**: Creates an `AgentSession` record (the "real" one) via `AgentSession.create(...)`, then spawns `claude -p` with env vars set at `agent/sdk_client.py:1343-1369`:
   - `VALOR_SESSION_ID` = the worker's `session_id` (e.g. `0_1777013645482`)
   - `AGENT_SESSION_ID` = the worker's `agent_session_id` UUID
   - `VALOR_PARENT_SESSION_ID` = the parent UUID (PM/Teammate only)
   - `SESSION_TYPE` = the persona
3. **Claude Code CLI subprocess starts**. On the first prompt, the `UserPromptSubmit` hook fires (`.claude/hooks/user_prompt_submit.py`). Current behavior: sidecar is empty (fresh subprocess), gate at line 109 passes (`SESSION_TYPE` set), `create_local()` is called at line 134 — **phantom born here**.
4. **Subsequent hooks fire** (`post_tool_use.py`, `subagent_stop.py`) via the sidecar's `agent_session_id` which now points at the phantom, not the real record.
5. **On subprocess exit**, the `Stop` hook fires (`.claude/hooks/stop.py`). It loads the sidecar, confirms `agent_session_id` is present at line 138, then at line 146 reconstructs `local-{session_id}` and queries by that — **finds the phantom** (because that's what the hook created) — and finalizes it as `completed`. The real worker-created session never receives a Stop hook from this subprocess; its lifecycle is managed by the worker.
6. **Parent PM's `wait-for-children` query**: looks for children with `parent_agent_session_id == pm.agent_session_id`. Finds the phantom (which has `parent_agent_session_id` set by line 132-145). Phantom is already `completed`. Parent concludes "all children done," exits — without ever having dispatched a real child.

**After the fix:**

3'. First-prompt hook reads `os.environ["AGENT_SESSION_ID"]`, resolves the existing worker-created `AgentSession` via `AgentSession.get_by_id()`, writes that `agent_session_id` into the sidecar, and returns **without calling `create_local()`**. No phantom.
4'. Subsequent hooks read the sidecar → get the real worker `agent_session_id` → operate on the real record. All memory ingest, tool-use accounting, etc. flow to the right place.
5'. Stop hook: the sidecar's `agent_session_id` now points at the real worker-created session. Stop.py currently reconstructs `local-{session_id}` and filter-queries by that — **the reconstructed key will not match the real record's `session_id`**. Stop.py must be updated to look up by the sidecar's `agent_session_id` directly via `AgentSession.get_by_id()`, with a fallback to the `local-*` reconstruction for legacy/direct-CLI paths.
6'. Parent PM's `wait-for-children`: the phantom no longer exists, so it only sees real dispatched children. If none were dispatched, the list is empty and the PM must actually do work before exiting.

## Spike Results

### spike-1: Does `AgentSession.get_by_id(os.environ["AGENT_SESSION_ID"])` reliably resolve the worker-created session?
- **Assumption**: The worker sets `AGENT_SESSION_ID` to the worker's `agent_session_id` UUID (not the bridge `session_id`), and `AgentSession.get_by_id()` can look that up.
- **Method**: code-read
- **Finding**: Verified. `agent/sdk_client.py:1350-1351` sets `env["AGENT_SESSION_ID"] = self.agent_session_id` (the AutoKeyField id, which `agent_session_id` is an alias for). `models/agent_session.py:735-772` defines `AgentSession.get_by_id(agent_session_id: str | None) -> AgentSession | None` as the canonical lookup. It does `cls.query.filter(id=agent_session_id)` and returns the first result, warning on >1 and returning None on empty/missing. Safe, idempotent, and already used elsewhere in the codebase.
- **Confidence**: high
- **Impact on plan**: The fix's primary lookup is `AgentSession.get_by_id(os.environ.get("AGENT_SESSION_ID"))`. No fallback needed for the happy path.

### spike-2: Should `VALOR_SESSION_ID` be used as a fallback when `AGENT_SESSION_ID` isn't set?
- **Assumption**: If only `VALOR_SESSION_ID` is set (some other spawn path), we could look up via `query.filter(session_id=...)`.
- **Method**: code-read
- **Finding**: In `agent/sdk_client.py:1345-1351`, both env vars are set together under the same conditions (`if session_id` and `if self.agent_session_id`). They're guaranteed to both be set whenever the worker spawns a subprocess with an `agent_session_id`. There is no spawn path that sets `VALOR_SESSION_ID` without also setting `AGENT_SESSION_ID`, except possibly in very early legacy test fixtures (which are not the target population). However, using `VALOR_SESSION_ID` as a secondary fallback is cheap and defensive — it makes the hook robust to future divergence.
- **Confidence**: high
- **Impact on plan**: Fix tries `AGENT_SESSION_ID` first (indexed `id` lookup, O(1)); falls back to `VALOR_SESSION_ID` via `query.filter(session_id=...)` if the first is empty or resolves to None. If both fail, falls through to the existing gate at line 109 (unchanged behavior for legitimate direct-CLI paths).

### spike-3: Stop hook behavior when sidecar contains a real `agent_session_id` but `local-{session_id}` doesn't match any record
- **Assumption**: `stop.py:146-148` will silently return if the filter query returns no results.
- **Method**: code-read
- **Finding**: Confirmed. `stop.py:147-149` runs `matches = list(AgentSession.query.filter(session_id=sidecar_session_id))`, checks `if not matches: return`. Silent skip. This means without a stop.py fix, the real worker-created session's terminal lifecycle transition will NOT happen from the subprocess Stop hook (but that's OK — the worker itself manages the real session's lifecycle and will finalize it when the subprocess exits via `executor.py`). However: for PM/Teammate sessions whose terminal transition IS expected to come from the Stop hook (not from the worker — the worker for PM sessions runs in read-only mode and listens for the subprocess to finish), a silent miss would leave the session stuck in `running`. **Plan must verify which transition path PM/Teammate sessions use and ensure at least one path still finalizes them.**
- **Confidence**: medium (the PM lifecycle finalization path needs one more verification read during build)
- **Impact on plan**: stop.py must be updated to try sidecar's `agent_session_id` first (via `get_by_id`), fall back to `local-{session_id}` reconstruction for legacy paths. post_tool_use.py and subagent_stop.py may have similar reconstruction patterns — build step must grep all four hook files for `local-{` / `f"local-"` / `local_session_id` and audit each site.

### spike-4: Are existing phantom rows in Redis harmful if left alone?
- **Assumption**: Phantoms accumulate but are harmless once their subprocess exits — they are terminal, cannot resurrect, and cannot mis-route new work.
- **Method**: code-read
- **Finding**: Phantoms reach `completed` via the subprocess's Stop hook, then sit in Redis like any other terminal session. Fingerprint: `session.session_id.startswith("local-") AND session.parent_agent_session_id is set AND session.session_type in ("pm", "teammate") AND session.claude_session_uuid is None`. PR #1113's terminal-state guard in `user_prompt_submit.py:80-90` prevents any code path from re-activating a terminal session. No daemon reads terminal phantoms and dispatches on them; `wait-for-children` reads `status`, and a `completed` phantom satisfies the "done" predicate immediately (which is exactly the acute bug, but it only fires while the phantom's subprocess is alive — after that the phantom is inert). The only lingering impact is cosmetic: `valor-session children` output noise and memory records attached to the phantom instead of the real session.
- **Confidence**: high
- **Impact on plan**: **Do not ship a cleanup utility.** Prevention makes it unnecessary. After the fix, no new phantoms are created, and existing ones decay naturally as Redis TTLs / operator cleanup happen in the normal course. If operator tooling is ever needed, it is a separate concern in a separate plan — never in the same PR as the prevention fix.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: None at the `AgentSession` model level. The hook's *internal* logic adds an env-var-resolution step before the existing gate.
- **Coupling**: Slightly reduces coupling. The hook currently assumes it owns the `AgentSession` lifecycle for worker-spawned subprocesses; after this fix, it defers to the worker's pre-existing record, which is architecturally cleaner (single source of truth).
- **Data ownership**: The worker's `AgentSession` record becomes the sole record for a worker-spawned subprocess. The hook only *writes* the sidecar pointing to it; it no longer *creates* a second record.
- **Reversibility**: High. The fix is a ~15-line change in one hook file plus a ~5-line change in `stop.py`. Reverting restores prior behavior. No schema changes, no data migration required (phantoms in Redis remain queryable; new phantoms simply stop being created).

## Appetite

**Size:** Small

**Team:** Solo dev (builder + validator pairing)

**Interactions:**
- PM check-ins: 0 (scope fully specified in issue + recon)
- Review rounds: 1 (standard PR review)

This is a ~20-line prevention-guard change in two hook files plus ~100 lines of unit tests and a short integration test. The investigation cost was front-loaded into the issue + recon; execution is mechanical.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "from utils.redis_client import get_redis; print(get_redis().ping())"` | AgentSession ORM reads |
| Test suite passes on baseline | `pytest tests/unit/test_hook_user_prompt_submit.py tests/unit/test_stop_hook.py -q` | Baseline sanity before changes |

Run all checks: `python scripts/check_prerequisites.py docs/plans/phantom-pm-twin-dedupe.md`

## Solution

### Key Elements

This plan is **all prevention, no cleanup**. Every element below is a guard at an existing decision point that blocks the wrong outcome at the source.

- **Prevention guard in `user_prompt_submit.py` (the phantom's single creation site)**: Before the existing `VALOR_PARENT_SESSION_ID or SESSION_TYPE` gate at line 109, read `AGENT_SESSION_ID` from the environment. If present and it resolves via `AgentSession.get_by_id()` to a live (non-terminal) session, write that session's `agent_session_id` into the sidecar and return. **No `create_local()` call at line 134 is reached.** This is the primary prevention — the phantom is never minted.
- **VALOR_SESSION_ID fallback (secondary prevention)**: If `AGENT_SESSION_ID` is missing or doesn't resolve, try `VALOR_SESSION_ID` via `query.filter(session_id=...)`. Makes the guard robust to future env-var divergence without widening the call surface.
- **Existing gate unchanged**: If neither env var resolves, fall through to the existing gate at line 109. Current behavior preserved for legitimate non-worker paths. The gate remains the keep-direct-CLI-users-honest backstop.
- **Terminal-status safety (preserves #1113)**: If `AGENT_SESSION_ID` resolves to a session in a terminal state (killed/completed/failed/abandoned/cancelled), do NOT attach. Fall through to existing gate — same behavior as today for orphaned subprocesses. This is important: we are preventing the creation of a new wrong record, not reviving a terminal record.
- **Stop hook sidecar-first lookup (prevents silent miss on the correct record)**: `.claude/hooks/stop.py` changed to prefer `AgentSession.get_by_id(sidecar["agent_session_id"])` as the primary lookup. The existing `query.filter(session_id=local-{session_id})` remains as a fallback for direct-CLI / legacy paths — per answer to open question 3, we keep this fallback because local CLI use is still supported.
- **Hook audit for other reconstruction sites**: `post_tool_use.py` and `subagent_stop.py` grepped for `local-{` / `f"local-"` / `local_session_id`. Any DB-lookup reconstruction gets the same sidecar-first treatment. Log-path-only uses are left alone.
- **No cleanup utility.** We deliberately do not ship `tools/cleanup_phantom_twins.py`. Prevention makes it unnecessary, and shipping one would contradict the plan's stance that prevention is the right level to fix this.

### Flow

**Fresh worker subprocess spawn** → `claude -p` starts → first prompt arrives → UserPromptSubmit hook fires → hook reads `AGENT_SESSION_ID` env → resolves real worker session via `get_by_id` → writes real session's id to sidecar → returns → **single AgentSession record**.

Compare to today: **Fresh worker subprocess spawn** → `claude -p` starts → first prompt arrives → UserPromptSubmit hook fires → sidecar empty → gate passes (SESSION_TYPE set) → `create_local()` creates phantom → sidecar now points at phantom → **two AgentSession records for one subprocess**.

### Technical Approach

- **Primary change** is in `.claude/hooks/user_prompt_submit.py`, in the `else:` branch at line 105 (the "first prompt" path). Insert a new block BEFORE the existing env-var gate:

  ```python
  # Try to attach to an existing worker-created AgentSession before creating a local-* one.
  # The worker sets AGENT_SESSION_ID precisely to signal "I already own this subprocess."
  worker_agent_session_id = os.environ.get("AGENT_SESSION_ID", "").strip()
  worker_bridge_session_id = os.environ.get("VALOR_SESSION_ID", "").strip()

  if worker_agent_session_id or worker_bridge_session_id:
      from models.agent_session import AgentSession
      from models.session_lifecycle import TERMINAL_STATUSES

      attached = None

      if worker_agent_session_id:
          attached = AgentSession.get_by_id(worker_agent_session_id)

      if attached is None and worker_bridge_session_id:
          try:
              matches = list(AgentSession.query.filter(session_id=worker_bridge_session_id))
              if matches:
                  attached = matches[0]
          except Exception:
              attached = None

      if attached is not None and getattr(attached, "status", None) not in TERMINAL_STATUSES:
          sidecar["agent_session_id"] = attached.agent_session_id
          save_agent_session_sidecar(session_id, sidecar)
          return
      # If attached is terminal, fall through to existing gate behavior
      # (matches #1113 semantics: terminal sessions are operator-resume-only).
  ```

  Placed BEFORE the existing `if not os.environ.get("VALOR_PARENT_SESSION_ID") and not os.environ.get("SESSION_TYPE"): return` gate. This is a strict behavior-addition for the case where both env vars are present AND resolve to a live session; all other paths fall through unchanged.

- **Stop hook update** in `.claude/hooks/stop.py`: at line 145-150, the current block

  ```python
  sidecar_session_id = f"local-{session_id}"
  matches = list(AgentSession.query.filter(session_id=sidecar_session_id))
  if not matches:
      return
  agent_session = matches[0]
  ```

  changes to prefer the sidecar's `agent_session_id` via `get_by_id`:

  ```python
  agent_session = AgentSession.get_by_id(agent_session_id)
  if agent_session is None:
      # Legacy / direct-CLI fallback: reconstruct local-{session_id}
      sidecar_session_id = f"local-{session_id}"
      matches = list(AgentSession.query.filter(session_id=sidecar_session_id))
      if not matches:
          return
      agent_session = matches[0]
  ```

- **Subsequent-prompt re-activation branch audit** in `user_prompt_submit.py:63-104`: also does `AgentSession.query.filter(session_id=local_sid)` at line 65. After the fix, worker-spawned subprocesses won't have a `local-*` record, so the filter misses, re-activation silently no-ops. This is benign (the worker manages the real session's status directly; it is ALREADY in `running` from the worker's `transition_status` call before spawning). **Leave this branch unchanged.** The filter miss is harmless; no code change needed. A test will assert this explicitly.

- **post_tool_use.py / subagent_stop.py audit**: build step must grep for `local-{` / `f"local-"` / `local_session_id` in all four hook files. Any other reconstruction site gets the same sidecar-first pattern.

- **Race-prevention invariant in `agent/sdk_client.py`**: at the subprocess spawn site, the ordering must be `AgentSession.create()` → `save()` (synchronous Redis write) → set env vars → `subprocess.Popen`. The builder verifies this ordering is already correct (spike-2 / Risk 2 both confirm it is) and leaves a short comment at the spawn site marking it as a load-bearing invariant — any future refactor that reorders this would re-introduce the race that Risk 2 describes.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `user_prompt_submit.py` has `except Exception: pass` at lines 43, 103, 150. The new env-var resolution block is wrapped in the existing outer `try` at line 47 — its failures fall through to the outer catch (silent, matching existing behavior). Test: patch `AgentSession.get_by_id` to raise, assert hook does not propagate exception and falls through to `create_local` gate unchanged.
- [ ] `stop.py` currently has silent skips at lines 149. New primary lookup via `get_by_id` must also fail-safely: if `get_by_id` raises, fall back to the legacy reconstruction. Test: patch `get_by_id` to raise, assert fallback path is taken and finalize still fires.

### Empty/Invalid Input Handling
- [ ] `AGENT_SESSION_ID=""` (empty string): handled via `.strip()` — falls through to `VALOR_SESSION_ID`.
- [ ] `VALOR_SESSION_ID=""`: same.
- [ ] Both empty: falls through to existing `create_local` gate — unchanged behavior.
- [ ] `AGENT_SESSION_ID="not-a-real-uuid"`: `get_by_id` returns None → falls through.
- [ ] `AGENT_SESSION_ID=valid but session is terminal`: falls through to `create_local` gate (preserves #1113 semantics).

### Error State Rendering
- No user-visible output from this hook. Failures are logged silently per the existing hook conventions (`log_hook_error` at the top-level exception handler). No error-rendering path test needed.

## Test Impact

- [ ] `tests/unit/test_hook_user_prompt_submit.py::TestMainCallChain::test_main_creates_session_when_session_type_set` — **UPDATE**: currently asserts `create_local` IS called when `SESSION_TYPE=dev` is set. After the fix, if `AGENT_SESSION_ID` is ALSO set and resolves to a live session, `create_local` must NOT be called. The test must be split: (a) original case (SESSION_TYPE only, no AGENT_SESSION_ID) still asserts create_local IS called; (b) new case (both set, live session) asserts create_local NOT called AND sidecar was written with the resolved agent_session_id.
- [ ] `tests/unit/test_hook_user_prompt_submit.py::TestMainCallChain::test_main_creates_session_when_parent_set` — **UPDATE**: same logic. Currently asserts `create_local` IS called when only `VALOR_PARENT_SESSION_ID` is set. Acceptable as-is for a legacy path, but should also add the new case: when `AGENT_SESSION_ID` is ALSO set, create_local is skipped.
- [ ] `tests/unit/test_hook_user_prompt_submit.py::TestMainCallChain::test_main_creates_session_when_both_env_vars_set` — **UPDATE**: currently asserts `create_local` IS called when `SESSION_TYPE=teammate` AND `VALOR_PARENT_SESSION_ID=agt_parent456` are set. After the fix, add `AGENT_SESSION_ID=agt_existing` that resolves to a live session → assert create_local is NOT called. Keep a variant without `AGENT_SESSION_ID` for the legacy path.
- [ ] `tests/unit/test_hook_user_prompt_submit.py::TestMainCallChain::test_main_skips_create_local_when_no_env_vars` — **UPDATE**: no changes to assertion, but add a parametric variant with `AGENT_SESSION_ID=agt_nonexistent` (does not resolve) to confirm the fallthrough behavior is correct (still skips create_local because the gate at line 109 blocks).
- [ ] `tests/unit/test_stop_hook.py::TestCompleteAgentSession::test_complete_delegates_to_finalize_session` — **UPDATE**: currently mocks `AgentSession.query.filter` and asserts `finalize_session` is called. The mock setup works unchanged because the test mocks `query.filter` AND implicitly `get_by_id` would need a separate mock. Split: (a) happy path uses new `get_by_id` lookup — assert that is called and returns the session; (b) legacy path where `get_by_id` returns None — assert fallback to `query.filter` still fires.
- [ ] `tests/unit/test_stop_hook.py::TestCompleteAgentSession::test_complete_delegates_failed_on_error` — **UPDATE**: same split pattern.

**New unit tests to add:**
- [ ] `test_main_attaches_to_worker_session_when_agent_session_id_set` — new worker-spawn scenario: `AGENT_SESSION_ID=agt_real_worker` resolves to a live session → `create_local` NOT called, sidecar contains `agt_real_worker`.
- [ ] `test_main_attaches_via_valor_session_id_fallback` — `AGENT_SESSION_ID` missing or invalid, `VALOR_SESSION_ID=0_12345` resolves via filter lookup → same behavior.
- [ ] `test_main_falls_through_when_worker_session_terminal` — `AGENT_SESSION_ID=agt_killed` resolves to a session with `status=killed` → falls through to gate → create_local IS called (or gate blocks, depending on other env vars). This guards the #1113 semantics.
- [ ] `test_main_falls_through_when_get_by_id_raises` — patch `get_by_id` to raise → hook does not propagate, falls through to existing gate. Silent-failure convention preserved.
- [ ] `test_subsequent_prompt_misses_filter_on_worker_session_is_harmless` — new test confirming that when the sidecar points at a real worker `agent_session_id` (not a `local-*` one) and the subsequent-prompt branch at line 63 runs, the `query.filter(session_id=local_sid)` at line 65 misses silently. The existing "reactivate" behavior no-ops, but that is fine because the worker's own `transition_status` already keeps the session in `running`. Assert no exception, no `create_local` call, no sidecar re-write.
- [ ] `test_stop_hook_uses_get_by_id_primary_lookup` — sidecar contains `agent_session_id=agt_worker`; assert `get_by_id` is called first and `finalize_session` receives the worker session.
- [ ] `test_stop_hook_falls_back_to_filter_on_get_by_id_miss` — `get_by_id` returns None (legacy sidecar points at `local-*`); assert `query.filter` is used as fallback.

**New integration test to add (per answer to open question 2):**
- [ ] `tests/integration/test_pm_terminal_transition_after_stop_hook.py::test_pm_session_terminal_transition_fires_after_stop_hook_change` — programmatically asserts that a PM/Teammate session's terminal transition path still completes after the `stop.py` sidecar-first change. Uses an in-process `AgentSession` fixture (non-terminal), invokes `stop.py::_complete_agent_session` with a sidecar pointing at the real worker `agent_session_id`, and asserts `finalize_session` was called and the session transitioned to a terminal status. Prevents a future refactor from silently breaking PM session finalization — better coverage can't hurt.

## Rabbit Holes

- **Don't refactor the whole hook.** The `try:/except Exception: pass` structure is intentional and has been churned repeatedly; resist the urge to "clean it up" while making this change. Any refactor extends scope and risks regressions on #1001, #808, #1113, #1148 — all touching the same file.
- **Don't build a cleanup utility, even scaffolded, even dry-run-only.** Prevention is the right level to fix this bug. Shipping a `cleanup_phantom_twins` script — even as a safety net — implicitly admits that prevention might fail, which contradicts the plan's stance. Existing phantoms in Redis are already terminal and inert; they are historical noise, not an operational liability.
- **Don't touch the subsequent-prompt re-activation branch semantics.** The `filter(session_id=local_sid)` at line 65 becomes a no-op for worker-spawned subprocesses after this fix. That is correct and intentional — the worker owns the session's status. Adding a "try get_by_id here too" would be elegant but creates a second path that duplicates the worker's re-activation logic and risks racing with it.
- **Don't expand the fix to the PM fan-out rule.** The issue explicitly rules this out. The fan-out rule produced legitimate child sessions; the phantom is the hook's duplication, not the fan-out's.
- **Don't change `AgentSession.create_local` semantics.** It stays exactly as is. Only the *call-site precondition* in the hook changes — `create_local` itself is a well-tested primitive.
- **Don't remove the `local-{session_id}` fallback in `stop.py`.** Per decision on open question 3: local CLI use is still supported, and it is hard to tell the difference between an agent's headless use and a real local session. The fallback is the backstop for direct-CLI users.

## Risks

### Risk 1: Stop hook fallback path regression for legacy direct-CLI sessions
**Impact:** If the stop.py update breaks the legacy `local-{session_id}` lookup path (the only path direct-CLI sessions use today), direct-CLI session termination stops working — those sessions would accumulate in `running` forever.
**Mitigation:** Stop.py change is additive — it tries `get_by_id` first, falls back to the existing `filter(session_id=local-...)` on miss. Tests assert both the primary path AND the fallback path work. A post-implementation manual test with a direct `claude` invocation (SESSION_TYPE only, no AGENT_SESSION_ID) confirms end-to-end.

### Risk 2: Race — worker creates AgentSession, spawns subprocess, subprocess's UserPromptSubmit fires BEFORE the AgentSession is committed to Redis
**Impact:** Hook's `get_by_id` call returns None → falls through to gate → `create_local` fires → phantom is still created for that one subprocess.
**Mitigation:** The worker's `AgentSession` is created and `save()`d synchronously before the `claude -p` subprocess is spawned (verified in `agent/sdk_client.py` — the spawn is `create_subprocess_exec(...)` after the AgentSession lifecycle begins). The subprocess startup (Python interpreter init + hook import + Redis connection) takes hundreds of milliseconds; Redis commits are synchronous and take < 1ms. Race is theoretically possible but practically impossible. Build-step verification: read `agent/sdk_client.py` around the spawn site to confirm ordering is `create AgentSession → save → set env → spawn subprocess`. If the ordering is reversed, add an assertion or wait.

### Risk 3: `AGENT_SESSION_ID` env var injection gets dropped by a future sdk_client.py refactor
**Impact:** Without the env var, the fix silently degrades — the hook falls through to `create_local` and phantoms return.
**Mitigation:** Test fixture covers both "env var present" and "env var absent" cases. A separate test in `tests/integration/` can spawn a real subprocess via the worker and count AgentSession records to catch regressions end-to-end. Nice-to-have; default to unit tests.

### Risk 4: Subsequent-prompt re-activation branch's filter miss leaves the session status unmanaged
**Impact:** In the current code, the `else if sidecar has agent_session_id` branch at line 58-103 calls `transition_status(agent_session, "running", ...)` on every prompt — a form of heartbeat. After the fix, that branch's filter will miss for worker-spawned sessions, so no heartbeat from this path.
**Mitigation:** The worker itself updates the session's `updated_at` / `last_heartbeat_at` throughout its lifetime — it owns the session, not the hook. Verify at build time that the worker's heartbeat is active (it is, per `worker/__main__.py`). If the worker's heartbeat is ever removed, we'd re-introduce a heartbeat in this branch — but that would be a separate plan.

### Risk 5: `VALOR_SESSION_ID` fallback uses a non-indexed `session_id` filter
**Impact:** `AgentSession.session_id` is declared as a Popoto `Field()` (not a `KeyField`) in `models/agent_session.py`. `AgentSession.query.filter(session_id=...)` therefore cannot use an index and performs a Redis scan across candidate sessions. On the hot path (first-prompt hook for every subprocess), a slow fallback could add latency.
**Mitigation:** The primary lookup is `AgentSession.get_by_id()` which uses the indexed `id` / `AutoKeyField` — O(1). The `VALOR_SESSION_ID` fallback fires ONLY when `AGENT_SESSION_ID` is missing or doesn't resolve — a degenerate case that should not occur for worker-spawned subprocesses after `agent/sdk_client.py:1343-1369` sets both env vars together. In practice the fallback is defensive: if the worker ever stops setting `AGENT_SESSION_ID` but continues setting `VALOR_SESSION_ID`, the hook still works (correctness), just more slowly for that one subprocess (cost). The fallback is NOT removed because it is the correctness backstop; the slow path is explicitly preferred over a phantom-creation regression. No code change required for this risk — it is documented so future maintainers don't assume the fallback is O(1).

## Race Conditions

### Race 1: Worker-AgentSession-create vs. subprocess-hook-read
**Location:** `agent/sdk_client.py` (worker spawns subprocess) ↔ `.claude/hooks/user_prompt_submit.py:47+` (hook reads `AGENT_SESSION_ID` and looks up session)
**Trigger:** Worker creates AgentSession; immediately spawns subprocess; subprocess reaches first prompt before the AgentSession's `save()` is visible to Redis readers.
**Data prerequisite:** `AgentSession` with `id == os.environ["AGENT_SESSION_ID"]` must be readable from Redis before the hook's first call to `AgentSession.get_by_id()`.
**State prerequisite:** The AgentSession's status must be non-terminal when the hook reads it (so the hook does not fall through on the terminal-status guard).
**Mitigation:** (a) The worker's `AgentSession.create()` / `save()` is synchronous to Redis — by the time `subprocess.Popen` returns, the record is committed. (b) Python interpreter startup + Claude Code CLI init + hook import = hundreds of ms minimum; Redis write-to-read propagation is < 1ms in-process. (c) Even if the race somehow occurs, the hook's fallthrough to `create_local` is the current behavior — producing a phantom exactly as today, for that single subprocess. The fix is not worse than status quo in the race case.

### Race 2: Sidecar write race across concurrent hooks
**Location:** `.claude/hooks/hook_utils/memory_bridge.py:539` (`save_agent_session_sidecar`)
**Trigger:** Two concurrent UserPromptSubmit hooks in the same subprocess (rare but possible with parallel prompt streams).
**Data prerequisite:** Both hooks must not overwrite each other's sidecar writes.
**State prerequisite:** Sidecar must contain a coherent `agent_session_id`.
**Mitigation:** `save_agent_session_sidecar` already uses atomic tmp+rename (line 548-552). Safe. No additional mitigation needed for this plan.

## No-Gos (Out of Scope)

- **Any cleanup utility for existing phantom rows.** No `tools/cleanup_phantom_twins.py`, no `--dry-run` scanner, no worker-startup sweep, no scheduled job. Prevention is the fix; cleanup would be a band-aid. Existing phantoms stay as benign terminal historical noise.
- **Auto-deletion of existing phantom rows** from any entry point. If operators ever need it, it is a separate plan.
- **Removing the `local-{session_id}` legacy fallback in `stop.py`.** Per open question 3: local CLI use is supported; fallback stays.
- **Modifying the body of `AgentSession.create_local()`.** The method is a well-tested primitive used by non-worker paths (legitimate direct-CLI sessions). This plan changes *when* the method is called (by inserting a pre-check at the call site), never *what* the method does when called. Any attempt to reshape `create_local()` belongs in a separate plan.
- **Modifying the body of `AgentSession.get_by_id()`.** Same reasoning as `create_local()` — it is a well-tested primitive used across the codebase. This plan uses it as-is.
- **PM persona fan-out rule revision**: Explicitly excluded by the issue. The Multi-Issue Fan-out in `config/personas/project-manager.md:367-389` is working correctly; don't touch it.
- **Adding a `worker_owns_me` flag on AgentSession**: Tempting but out of scope. The env vars + sidecar are sufficient signaling.
- **Changing the PM / Teammate subprocess lifecycle management (who finalizes the session)**: Out of scope. This plan only prevents the duplicate record from being minted.
- **Promoting `session_id` to a `KeyField` to make the fallback filter indexed.** Tempting given Risk 5, but (a) it is a schema change with broad blast radius, (b) the fallback is a defensive cold path that fires only in degenerate env-var cases, and (c) rewriting `KeyField` vs `Field` mid-bugfix is exactly the kind of scope-creep the issue warned against. If the non-indexed filter ever becomes a real problem, it is a separate plan.

## Update System

No update system changes required — this feature is purely internal. The fix is a one-file hook change + one-file stop hook change. All changes land on `main` via the standard PR/merge flow and propagate to deployed machines via `./scripts/remote-update.sh` with no special steps. No new dependencies, no new config files, no migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. The hook is part of the Claude Code CLI subprocess lifecycle, not an agent-callable tool. No MCP server changes, no `.mcp.json` changes, no bridge imports.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/claude-code-memory.md` — the "AgentSession Tracking" subsection describes the hook's session creation behavior. Add a paragraph documenting the env-var resolution path: when `AGENT_SESSION_ID` / `VALOR_SESSION_ID` resolve to a live worker session, the hook attaches instead of creating.
- [ ] Update `docs/features/bridge-worker-architecture.md` if it describes the hook layer — add a note that the worker's AgentSession is the canonical record; the hook attaches via env vars rather than creating a duplicate.
- [ ] No new entry needed in `docs/features/README.md` — this is a bugfix to an existing documented feature (claude-code-memory), not a new feature.

### External Documentation Site
- [ ] N/A — repo does not use Sphinx / Read the Docs / MkDocs for user-facing docs; the `docs/` tree is the source of truth.

### Inline Documentation
- [ ] Docstring at the top of the new prevention guard in `user_prompt_submit.py` explaining why the guard exists, naming issue #1157, and stating explicitly that this is prevention (no duplicate record minted), not cleanup.
- [ ] Docstring update on `.claude/hooks/stop.py::_complete_agent_session` clarifying the primary `get_by_id` path vs legacy `local-{session_id}` fallback (with a note that the fallback is retained for direct-CLI users — see open question 3).

## Success Criteria

- [ ] A fresh worker-spawned PM session (`valor-session create --role pm --message "Run SDLC on issue N"`) produces exactly ONE `AgentSession` row in Redis. No `local-*` twin. Verified by inspecting `AgentSession.query.filter(parent_agent_session_id=<pm.agent_session_id>)` after the subprocess exits — zero `local-*` children for a PM that dispatched no real children.
- [ ] `valor-session children --id {pm_session}` shows only real dispatched children (or empty), never a `local-*` entry whose session_id matches the PM's own `claude_session_uuid`.
- [ ] PostToolUse and Stop hooks still fire correctly for worker-spawned sessions — memory extraction records, transcript backup `.jsonl` files, and lifecycle terminal transitions all occur against the worker's `AgentSession` row. Verified by unit tests + a manual PM→Telegram send test confirming memory records accumulate on the real session.
- [ ] `wait-for-children` on a PM does not terminate instantly due to a phantom's subprocess-exit Stop hook. It transitions to `waiting_for_children` and remains there until a real dispatched child reaches a terminal state. Verified by: create PM with no dispatched children → call wait-for-children → assert status remains `waiting_for_children` and does NOT auto-complete in < 60s from the PM's own Stop hook.
- [ ] Regression test: when `user_prompt_submit.py` runs with `VALOR_SESSION_ID=X` AND `X` resolves to an existing live AgentSession, NO new AgentSession is created; the sidecar is populated with `X`'s `agent_session_id`. Assertion on `AgentSession.create_local` mock: `assert_not_called()`.
- [ ] Regression test: when `user_prompt_submit.py` runs with `AGENT_SESSION_ID=Y` AND `Y` resolves to a live AgentSession, same behavior. Mock `AgentSession.get_by_id` to return the session and assert the sidecar contains `Y` with no `create_local` call.
- [ ] Regression test: when env vars resolve to a terminal-status session, the hook falls through to the existing gate — preserving #1113 semantics.
- [ ] Integration test `test_pm_session_terminal_transition_fires_after_stop_hook_change` passes — PM/Teammate terminal transition still happens after the `stop.py` sidecar-first change.
- [ ] Tests pass (`pytest tests/unit/test_hook_user_prompt_submit.py tests/unit/test_stop_hook.py tests/integration/test_pm_terminal_transition_after_stop_hook.py -v`) — all existing tests updated, new tests added, all green.
- [ ] Documentation updated (`/do-docs`) — `claude-code-memory.md` reflects the env-var attachment path.
- [ ] `grep -rn 'local-' .claude/hooks/*.py` after the change still shows the legacy fallback in stop.py (on purpose), but no NEW `local-*` creation paths for worker-spawned subprocesses.
- [ ] No `tools/cleanup_phantom_twins.py` exists. Prevention is the fix; a cleanup utility would contradict the plan.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (hook-dedupe)**
  - Name: `hook-dedupe-builder`
  - Role: Implement the prevention guard in `user_prompt_submit.py`, update `stop.py` for sidecar-first lookup (preserving the `local-{session_id}` legacy fallback), and audit `post_tool_use.py` and `subagent_stop.py` for similar reconstruction patterns.
  - Agent Type: builder
  - Resume: true

- **Validator (hook-dedupe)**
  - Name: `hook-dedupe-validator`
  - Role: Verify the implementation against Success Criteria and Test Impact tables. Run `pytest tests/unit/test_hook_user_prompt_submit.py tests/unit/test_stop_hook.py tests/integration/test_pm_terminal_transition_after_stop_hook.py -v` and report pass/fail. Run a live manual test invoking `valor-session create --role pm` against a dev Redis and count AgentSession records to confirm zero new phantoms.
  - Agent Type: validator
  - Resume: true

- **Test Writer (hook-dedupe-tests)**
  - Name: `hook-dedupe-test-writer`
  - Role: Write the new unit tests enumerated in Test Impact (`test_main_attaches_to_worker_session_when_agent_session_id_set`, `test_main_attaches_via_valor_session_id_fallback`, `test_main_falls_through_when_worker_session_terminal`, `test_main_falls_through_when_get_by_id_raises`, `test_subsequent_prompt_misses_filter_on_worker_session_is_harmless`, `test_stop_hook_uses_get_by_id_primary_lookup`, `test_stop_hook_falls_back_to_filter_on_get_by_id_miss`) plus the new integration test `test_pm_session_terminal_transition_fires_after_stop_hook_change`. Update existing tests per the UPDATE directives.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian (hook-dedupe-docs)**
  - Name: `hook-dedupe-docs`
  - Role: Update `docs/features/claude-code-memory.md` and `docs/features/bridge-worker-architecture.md` with the env-var attachment path documentation.
  - Agent Type: documentarian
  - Resume: true

### Step by Step Tasks

### 1. Implement prevention guard in user_prompt_submit.py
- **Task ID**: build-hook-attach
- **Depends On**: none
- **Validates**: `tests/unit/test_hook_user_prompt_submit.py`
- **Informed By**: spike-1 (get_by_id is the canonical lookup), spike-2 (VALOR_SESSION_ID fallback is defensive), spike-3 (terminal-status sessions fall through to preserve #1113)
- **Assigned To**: hook-dedupe-builder
- **Agent Type**: builder
- **Parallel**: true
- Open `.claude/hooks/user_prompt_submit.py`.
- In the `else:` branch at line 105 (first-prompt path), BEFORE the existing `if not os.environ.get("VALOR_PARENT_SESSION_ID")` gate at line 109, insert the prevention guard per Technical Approach. Try `AGENT_SESSION_ID` first via `AgentSession.get_by_id()`; fall back to `VALOR_SESSION_ID` via `query.filter(session_id=...)`; if resolved and non-terminal, write sidecar and return — the `create_local()` call at line 134 must never execute for worker-spawned subprocesses.
- Add the `from models.session_lifecycle import TERMINAL_STATUSES` import inside the block (consistent with the existing re-activation branch at line 69).
- Add a docstring comment at the top of the block referencing issue #1157 and explicitly stating that this guard exists to **prevent** duplicate `AgentSession` creation — not to clean up after it.
- Run `python -m ruff format .claude/hooks/user_prompt_submit.py` to normalize.

### 2. Update stop.py for sidecar-first lookup
- **Task ID**: build-stop-lookup
- **Depends On**: none
- **Validates**: `tests/unit/test_stop_hook.py`
- **Informed By**: spike-3 (stop.py filter-miss is silent; primary path must use sidecar's agent_session_id)
- **Assigned To**: hook-dedupe-builder
- **Agent Type**: builder
- **Parallel**: true
- Open `.claude/hooks/stop.py`.
- At lines 145-150 (`sidecar_session_id = f"local-{session_id}"` / `matches = list(AgentSession.query.filter(...))`), change the lookup to try `AgentSession.get_by_id(agent_session_id)` FIRST, falling back to the existing reconstruction on miss.
- Update the docstring of `_complete_agent_session` to describe primary and fallback paths.
- Run `python -m ruff format .claude/hooks/stop.py` to normalize.

### 3. Audit post_tool_use.py and subagent_stop.py for reconstruction patterns
- **Task ID**: build-hook-audit
- **Depends On**: none
- **Validates**: grep results in plan Verification section
- **Assigned To**: hook-dedupe-builder
- **Agent Type**: builder
- **Parallel**: true
- Run `grep -nE 'local-\{|f\"local-|local_session_id' .claude/hooks/*.py` and inspect each result.
- For any reconstruction site that performs a database lookup (not a local log-path computation), apply the same sidecar-first pattern as stop.py.
- If only log-path construction uses `local-*` (non-DB), leave it alone — log paths do not care about session identity.
- Document the audit outcome in the PR body (which files were touched, which were skipped and why).

### 4. Write new unit tests
- **Task ID**: build-tests-new
- **Depends On**: build-hook-attach, build-stop-lookup
- **Validates**: `tests/unit/test_hook_user_prompt_submit.py`, `tests/unit/test_stop_hook.py`
- **Assigned To**: hook-dedupe-test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add the seven new unit tests enumerated in Test Impact: attachment via AGENT_SESSION_ID, fallback via VALOR_SESSION_ID, terminal-status fallthrough, get_by_id raise fallthrough, subsequent-prompt filter-miss harmlessness, stop.py primary path, stop.py fallback path.
- Use the existing `_load_hook_module()` pattern for testing `user_prompt_submit.py::main()`.
- For stop.py tests, follow the existing `patch("models.agent_session.AgentSession.query")` mocking approach and add `patch("models.agent_session.AgentSession.get_by_id")`.

### 5. Write PM terminal-transition integration test
- **Task ID**: build-integration-test
- **Depends On**: build-stop-lookup
- **Validates**: `tests/integration/test_pm_terminal_transition_after_stop_hook.py` (create)
- **Informed By**: answer to open question 2 — better coverage can't hurt; prevents future refactor from silently breaking PM finalization
- **Assigned To**: hook-dedupe-test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/integration/test_pm_terminal_transition_after_stop_hook.py`.
- Fixture: a live non-terminal PM `AgentSession` with `session_type="pm"`, `status="running"`, plus a sidecar file pointing at that session's `agent_session_id`.
- Invoke `stop.py::_complete_agent_session(session_id, hook_input={"stop_reason": "end"})` directly.
- Assert the AgentSession reached a terminal `status` (`completed` or `failed`) via the `get_by_id`-first path.
- Assert the legacy `local-{session_id}` fallback was NOT consulted (it shouldn't be for a sidecar containing a worker `agent_session_id`).
- Teardown: `session.delete()` via Popoto ORM (never raw Redis), per project manual-testing hygiene.

### 6. Update existing unit tests per Test Impact
- **Task ID**: update-tests-existing
- **Depends On**: build-hook-attach, build-stop-lookup
- **Validates**: `tests/unit/test_hook_user_prompt_submit.py::TestMainCallChain`, `tests/unit/test_stop_hook.py::TestCompleteAgentSession`
- **Assigned To**: hook-dedupe-test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update the four TestMainCallChain tests per the UPDATE disposition: the existing assertions stay when env vars for worker attachment are NOT set; new assertions added when they ARE set.
- Update the two TestCompleteAgentSession tests to cover both primary (`get_by_id` hit) and fallback (`get_by_id` miss → filter hit) paths.

### 7. Update feature docs
- **Task ID**: document-feature
- **Depends On**: build-hook-attach, build-stop-lookup, update-tests-existing, build-tests-new, build-integration-test
- **Assigned To**: hook-dedupe-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/claude-code-memory.md` — add a subsection or paragraph to the AgentSession Tracking description explaining the prevention guard and env-var attachment semantics.
- Update `docs/features/bridge-worker-architecture.md` if it describes the hook layer — add a note that hook-created `local-*` records are now reserved for direct-CLI paths; worker-spawned subprocesses attach via env vars so no duplicate record is ever minted.

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-hook-attach, build-stop-lookup, build-hook-audit, build-tests-new, build-integration-test, update-tests-existing, document-feature
- **Assigned To**: hook-dedupe-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_hook_user_prompt_submit.py tests/unit/test_stop_hook.py tests/integration/test_pm_terminal_transition_after_stop_hook.py -v`. All must pass.
- Run `python -m ruff check .claude/hooks/`. Clean.
- Run `python -m ruff format --check .claude/hooks/`. Clean.
- Run the Verification table commands (see below).
- Manual test: create a worker-spawned PM session on a dev Redis, let it run to completion, query `AgentSession.query.filter(parent_agent_session_id=<pm.agent_session_id>)`, confirm zero `local-*` children.
- Confirm `tools/cleanup_phantom_twins.py` does NOT exist (prevention-only plan).
- Generate final report: success criteria status, test pass counts, any unresolved concerns.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_hook_user_prompt_submit.py tests/unit/test_stop_hook.py -q` | exit code 0 |
| Integration test passes | `pytest tests/integration/test_pm_terminal_transition_after_stop_hook.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/hooks/` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_hook_user_prompt_submit.py tests/unit/test_stop_hook.py` | exit code 1 |
| Prevention guard exists in user_prompt_submit | `grep -n 'AGENT_SESSION_ID' .claude/hooks/user_prompt_submit.py` | output contains `AGENT_SESSION_ID` |
| Stop hook primary path exists | `grep -n 'get_by_id' .claude/hooks/stop.py` | output contains `get_by_id` |
| Stop hook legacy fallback preserved | `grep -n 'local-{session_id}\|f"local-"' .claude/hooks/stop.py` | output is non-empty (fallback retained per open question 3) |
| No cleanup utility was created | `test ! -e tools/cleanup_phantom_twins.py` | exit code 0 |

## Critique Results

<!--
Populated by /do-plan-critique (war room). Two verdicts have been recorded:
  1. 2026-04-24T08:28 — against plan hash sha256:9209503e (pre-pivot, cleanup-focused draft).
     Addressed by revision 2 (commit 8b7b9c1e): dropped cleanup utility, pivoted to prevention.
  2. 2026-04-24T08:43:05 — against plan hash sha256:a4eaf47e (post-pivot revision-2 state).
     Addressed by revision 4: frontmatter `revision_applied: true`, last_comment_id recorded,
     non-indexed-filter risk documented, tighter No-Gos, line-number re-verification.
A fresh /do-plan-critique pass against the current revision-4 state is the next expected step.
-->
| Severity | Critic | Finding (paraphrased from verdict + plan delta) | Addressed By | Implementation Note |
|----------|--------|------------------------------------------------|--------------|---------------------|
| Verdict 1 — NEEDS REVISION | Aggregate (hash `sha256:9209503e`) | Cleanup-focused draft mis-ranked prevention vs. cleanup — shipping a `tools/cleanup_phantom_twins.py` utility implicitly admitted prevention might fail. | Revision 2 (commit `8b7b9c1e`) dropped the cleanup utility entirely. Plan is now prevention-only. | The guard must fire at `user_prompt_submit.py:134` (the `AgentSession.create_local()` call) BEFORE the existing env-var gate at line 109. Check `AGENT_SESSION_ID` via `AgentSession.get_by_id()`, fall back to `VALOR_SESSION_ID` via `query.filter(session_id=...)`. If resolved and non-terminal, write sidecar and return. Preserve #1113 terminal-status semantics by falling through on terminal sessions. |
| Verdict 2 — NEEDS REVISION | Aggregate (hash `sha256:a4eaf47e`) | Plan frontmatter missing `revision_applied: true` signal for SDLC router Row 4b→4c transition; plan status was set to `Ready` while an open NEEDS REVISION verdict was still active; `last_comment_id` unset despite two tracking-issue comments recorded. | Revision 4 sets `revision_applied: true`, records `last_comment_id: 4311794216`, and adds explicit mappings between verdict-1 concerns and verdict-2 delta rows. | Frontmatter edits are enumerated in the Revision Log under "revision 4". Status remains `Ready` because (a) the plan is structurally ready to build, (b) `revision_applied: true` explicitly signals that a revision pass has been applied, (c) the next `/sdlc` iteration will issue a fresh critique against the current hash and the router's G5 guard will then consult the new verdict, not the stale one. |
| Verdict 2 — NEEDS REVISION | Operator (inferred) | `VALOR_SESSION_ID` fallback uses `AgentSession.query.filter(session_id=...)` — `session_id` is declared as `Field()` not `KeyField()` in `models/agent_session.py:734-ish`, so the filter is a non-indexed Redis scan. Was not called out as a cost consideration. | Revision 4 adds Risk 5 documenting the non-indexed scan and why it is acceptable (fallback rarely fires; primary `get_by_id` is O(1)). | No code change required — the fallback is correct for correctness; it is slow only in the degenerate case where `AGENT_SESSION_ID` is missing but `VALOR_SESSION_ID` is present. That path is a defensive fallback, not the hot path. |
| Verdict 2 — NEEDS REVISION | Archaeologist (inferred) | `AgentSession.create_local()` is referenced as "don't touch" in Rabbit Holes, but not in No-Gos. A future reader scanning No-Gos for "what is off-limits" would miss it. | Revision 4 promoted `AgentSession.create_local()` method-body stability into a dedicated No-Gos bullet. | No code implication; documentation hygiene only. |

---

## Open Questions

1. **Cleanup scope**: Should the `tools/cleanup_phantom_twins.py` utility ship in this PR at all, or be deferred to a separate operational task?
   - **RESOLVED 2026-04-24 (Valor):** NO. Don't over-engineer this. Drop the cleanup utility from the plan entirely. The focus is prevention, not cleanup — if prevention works, this utility should never need to be used.
   - Plan impact: removed the utility from all sections (Solution, Technical Approach, Team Orchestration, Step-by-Step Tasks, Verification, Test Impact). Added an explicit No-Gos bullet and a Verification check asserting the file is NOT created.

2. **PM/Teammate terminal transition path**: spike-3 flagged medium confidence that after the stop.py fix, the PM/Teammate session's terminal transition path is still intact (worker manages it, not the hook). Should we add a programmatic integration test asserting it?
   - **RESOLVED 2026-04-24 (Valor):** YES. Add the integration test. Better coverage can't hurt.
   - Plan impact: added `tests/integration/test_pm_terminal_transition_after_stop_hook.py` under Test Impact and a dedicated build task (Step 5) to create it.

3. **Legacy path preservation in stop.py**: Do direct-CLI sessions even need stop.py to finalize them? Or can we simplify stop.py to sidecar-only lookup?
   - **RESOLVED 2026-04-24 (Valor):** KEEP the fallback. We support local CLI use because in some cases it's hard to tell the difference between an agent's headless use and a real local session. Do not remove the `local-{session_id}` path.
   - Plan impact: stop.py change is additive only — `AgentSession.get_by_id(sidecar["agent_session_id"])` is the primary lookup, `query.filter(session_id=local-{session_id})` remains as the fallback. A No-Gos bullet and a Verification check explicitly prevent future removal of the fallback.

_All three original open questions are resolved. No new open questions have been surfaced by this revision._
