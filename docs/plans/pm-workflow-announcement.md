---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-29
tracking: https://github.com/tomcounsell/ai/issues/1189
last_comment_id:
---

# PM Workflow Announcement and Pause-for-Confirmation

## Problem

The PM persona's "Intake and Triage" rule for bucket #3 (coding/feature/bug/automation/config requests) tells the agent to silently file a GitHub issue and route through SDLC. The rule is real but undermined by two structural facts:

1. **The shared `work-patterns.md` segment loads BEFORE the PM overlay** and pushes a developer-mode default: "YOLO mode — NO APPROVAL NEEDED," "code changes don't require check-ins," "implementation detail? My call," "Should I fix this bug I found? Yes, fix it." The PM overlay rules come after, but the developer-flavored content is louder and gets read first.
2. **The contract is never spoken to the human.** When the PM follows the rule, the human sees no announcement that an issue is being filed. When the PM violates the rule, the human has no signal until after the work has shipped.

**Current behavior:**

A PM session occasionally implements code/automation/config changes directly without filing an issue or running SDLC, rationalizing the work as "config" rather than "software." The 2026-04-28 PBA incident (briefing LaunchAgents and shell scripts created entirely outside SDLC) is the canonical failure mode.

**Desired outcome:**

When intake bucket #3 fires, the PM:

1. **Stops** before doing anything that touches code/config/automation/infra.
2. **Announces** the workflow contract using a literal phrase: *"Unless you directly instruct me to skip our standard workflow, we need to file an issue to plan all improvements and changes to software."*
3. **Asks** for a `plan` or `skip` reply token.
4. **Persists** the question to `session.expectations` by ending the response with a `## Open Questions` section (the drafter at `bridge/message_drafter.py:1727` extracts verbatim).
5. **Ends the turn**, transitioning to `dormant` via the existing transcript-end path (`bridge/session_transcript.py:317`).
6. **Resumes inside the same session** when the human replies — `bridge/session_router.py::find_matching_session` matches the fresh `plan`/`skip` reply at confidence ≥ 0.80.

The shared segment's developer-mode defaults are explicitly overridden in the PM overlay so the agent doesn't read two contradictory documents and pick the louder one.

## Freshness Check

**Baseline commit:** `6bbb2b9258b318fafb37dc90f3e648ddca0ccf1b`
**Issue filed at:** `2026-04-28T05:58:52Z`
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/sdk_client.py:919` — existing CRITIQUE-missing warning sits at line 919 inside `load_persona_prompt`. Confirmed verbatim.
- `bridge/message_drafter.py:1725` — `expectations = structured.expectations` followed by the verbatim `_extract_open_questions` fallback at line 1727. Issue cited 1725; the fallback block is 1725–1731. Both still present.
- `agent/output_handler.py:380` — `session.expectations = expectations` inside `_persist_routing_fields`. Confirmed.
- `models/agent_session.py:201` — `expectations = Field(null=True)`. Confirmed (line 201 exact).
- `models/agent_session.py:108` — `dormant - Paused on open question, waiting for human reply`. Confirmed (line 108 exact).
- `bridge/session_router.py:57` and `:86` — `s.status not in ("active", "dormant")` filter and `expectations` filter both present. Confirmed.
- `~/Desktop/Valor/personas/project-manager.md:96` — bucket #3 with the exact wording the issue quotes. Confirmed verbatim.
- `config/personas/project-manager.md` — does NOT contain a "Role" / "How I Work" / "Intake and Triage" section. The in-repo template is *only* the SDLC pipeline overlay (Hard Rules, Stage→Model Dispatch, etc.). The "Intake and Triage" overlay only exists in the private `~/Desktop/Valor/personas/project-manager.md`. **This is a planning constraint, see Notes.**
- `config/personas/segments/manifest.json` — confirms universal segment order (all personas render all segments). The architectural rationale for "edit overlay, don't fork segments" still holds.

**Cited sibling issues/PRs re-checked:**
- #274 (Semantic Session Routing) — closed, infrastructure shipped. Foundation for `expectations`/router still present.
- #318 (route unthreaded into active sessions) — closed, decision matrix shipped. Active+dormant routing both supported.
- #280 (summarizer fabricated questions) — closed; verbatim `## Open Questions` extraction is the resolution this plan depends on.
- #1007 / PR #1009 (PM self-monitoring + pipeline completion guards) — merged. Last significant PM overlay edit. Convention of "hard rules near the top" still in force.
- #1148 / PR #1152 (inject SESSION_TYPE + PM persona for harness) — merged. Confirms PM overlay actually loads in production for harness sessions.

**Commits on main since issue was filed (touching referenced files):**
- None. `git log --oneline --since="2026-04-28T05:58:52Z" -- agent/sdk_client.py bridge/message_drafter.py bridge/session_router.py config/personas/ models/agent_session.py agent/output_handler.py` returned empty.

**Active plans in `docs/plans/` overlapping this area:** None. No other open plan touches the PM persona overlay or `expectations` routing.

**Notes:**
- The in-repo template `config/personas/project-manager.md` is *not* a mirror of the private overlay's "Role / How I Work / Intake and Triage" content. It's the pipeline-rules overlay (CRITIQUE/REVIEW gates, dispatch table, exit validation). This affects how AC#2 ("public template updated to match the private overlay's bucket #3 wording") gets satisfied: we must **introduce** the Intake and Triage section into the in-repo template, not edit an existing line. See Solution → Technical Approach for details.
- The exact line of bucket #3 is `~/Desktop/Valor/personas/project-manager.md:96`. The issue cited this; verified it matches verbatim.

## Prior Art

- **#1007 / PR #1009** (closed/merged 2026-04-16) — PM persona self-monitoring + pipeline completion guards. Most recent significant PM overlay edit. Established the convention of putting hard rules near the top of the overlay; this plan extends that convention with a new hard rule (workflow announcement) without disrupting the existing rule ordering.
- **PR #802** (merged 2026-04-07) — `fix(sdlc): enforce CRITIQUE and REVIEW gates in PM persona and sdk_client`. **Direct precedent for this plan's loader-warning addition.** Added the `if "CRITIQUE" not in overlay_content` warning at `agent/sdk_client.py:919–923`. We mirror the exact shape (substring check + WARN log + actionable message) for the workflow-announcement rule.
- **#274** (closed) — Semantic Session Routing: structured summarizer + context-aware routing. Foundational. The `expectations` field, the `## Open Questions` extraction path, and the Haiku classifier with 0.80 threshold are all #274 deliverables.
- **#318** (closed) — Route unthreaded messages into active sessions via expectations + queued_steering_messages. Extended #274 to cover active sessions. Decision matrix:
  - active session, confidence ≥ 0.80 → push to steering queue (don't create new session)
  - dormant session, confidence ≥ 0.80 → resume session
  - any session, confidence < 0.80 → create new session
- **#280** (closed) — Summarizer fabricated questions. Motivated the verbatim `## Open Questions` extraction path. The drafter no longer LLM-summarizes questions — it copies them verbatim from the section. This plan relies on that verbatim path (we cannot risk Haiku rephrasing the workflow phrase).
- **#1148 / PR #1152** — inject SESSION_TYPE + load PM persona into harness sessions. Confirms the PM overlay actually reaches production code paths.

## Research

No relevant external findings — proceeding with codebase context. The work is purely internal: PM persona overlay text + an in-repo loader-warning addition + tests against existing infrastructure. No new libraries, no API contract changes, no external ecosystem patterns to research.

## Spike Results

No spikes required. Small appetite, all assumptions verified by code reading during freshness check (Phase 0.5):

- The verbatim extraction path in `bridge/message_drafter.py:1727` works on `## Open Questions` headers.
- The `_persist_routing_fields` callback at `agent/output_handler.py:380` writes `expectations` to the session.
- The `dormant` transition is handled by `bridge/session_transcript.py:317` via `transition_status`.
- The semantic router at `bridge/session_router.py:50–172` filters `("active", "dormant")` and uses Haiku at 0.80 threshold.

The "text-only vs. text + hook" question raised in the issue is a design decision, not a verifiable assumption. It is resolved in Solution → Technical Approach, with rationale.

## Data Flow

End-to-end trace for the announce-and-pause flow when a coding/feature/bug/automation/config message arrives:

1. **Entry point**: Telegram message arrives in a PM-mode chat → `bridge/telegram_bridge.py` enqueues a PM AgentSession.
2. **Worker pickup**: `worker/__main__.py` picks up the session → `agent/session_executor.py` invokes `claude -p` with the PM system prompt (segments + overlay).
3. **PM agent classification**: PM agent reads the message, classifies it as bucket #3 (coding/feature/bug/automation/config) using its updated overlay rules.
4. **PM agent response composition**: Per the new rule, the PM agent emits a response that:
   - Announces the workflow contract using the literal phrase
   - Lists the `plan` / `skip` short-token options
   - Ends with a `## Open Questions` section containing the workflow question
   - Performs no other tool calls (no Bash, no Write, no Edit) before yielding
5. **Stop hook + drafter**: When the PM agent finishes its turn, `bridge/message_drafter.py::draft_message` runs. It detects the `## Open Questions` section, extracts it verbatim via `_extract_open_questions`, and sets `MessageDraft.expectations` (line 1727–1729).
6. **Persist**: `agent/output_handler.py::_persist_routing_fields` (line 380) writes `expectations` back to the AgentSession.
7. **Transcript completion**: The session transcript ends. `bridge/session_transcript.py:317` calls `transition_status(session, "dormant", ...)`.
8. **Telegram delivery**: The drafted message goes out to the user with the announce-and-pause text. The session is now `dormant` with non-null `expectations`.
9. **Human replies** (`plan` or `skip`): A fresh unthreaded reply lands in the same chat.
10. **Semantic routing**: `bridge/session_router.py::find_matching_session` finds the dormant session (status in `("active", "dormant")`, non-null `expectations`), passes the message + context_summary + expectations to the Haiku classifier at confidence threshold 0.80.
11. **Match decision**:
    - If matched (≥ 0.80) AND session is dormant → resume the session via `valor-session resume` with the reply as the new message.
    - If matched (≥ 0.80) AND session were active → push to steering queue. (Not the expected path here because we just transitioned to dormant.)
    - If unmatched (< 0.80) → create a new session.
12. **PM agent resumes**: On `plan` reply → PM proceeds with "create issue + run /do-plan." On `skip` reply → PM acknowledges the override and implements directly *for this task only* (the override does not persist beyond this turn).

The two-token design (`plan` / `skip`) is load-bearing for step 11. They are intentionally short so a one-word fresh Telegram reply contains enough signal for Haiku to clear the 0.80 threshold against the stored expectations text.

## Architectural Impact

- **New dependencies**: None.
- **Interface changes**: None at the code/API layer. Persona text is interpreted by the LLM at runtime, not parsed by code (except for the existing CRITIQUE-substring check). The new substring check follows the same shape.
- **Coupling**: No change. The PM overlay and the loader function continue their current relationship.
- **Data ownership**: No change. `expectations` already lives on AgentSession; we are using it as designed.
- **Reversibility**: Trivial. Reverting the PR restores the silent rule. The two persona files and the one loader function are the entire surface area.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (resolve the "text-only vs. text + hook" question before BUILD; the plan resolves it as text-only, but the human should confirm)
- Review rounds: 1 (single PR review)

This is persona-text editing + a 5-line loader patch + 2 new tests. No infrastructure changes, no migrations, no new dependencies.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `~/Desktop/Valor/personas/project-manager.md` exists | `test -f ~/Desktop/Valor/personas/project-manager.md && echo OK` | Private overlay must be present to edit it |
| `config/personas/project-manager.md` exists | `test -f config/personas/project-manager.md && echo OK` | In-repo template target |
| `agent/sdk_client.py` line 919 contains the existing CRITIQUE warning | `grep -q 'CRITIQUE.*overlay_content' agent/sdk_client.py && echo OK` | Confirms the precedent block is intact before we add a parallel block |
| Anthropic API key for Haiku routing | `python -c "from config.settings import settings; assert settings.anthropic_api_key"` | Required for the integration test in Test Impact |
| Redis available for session persistence | `redis-cli ping` returns `PONG` | Required for both unit and integration tests |

## Solution

### Key Elements

- **PM overlay text update** (private + in-repo template): Replace bucket #3 with the announce-then-pause version. Add a new "What counts as a software change (issue required)" enumeration, and a "PM Overrides of Shared Defaults" table that explicitly reverses ≥5 developer-flavored defaults from `work-patterns.md`.
- **Loader-warning patch**: Extend `agent/sdk_client.py:919` block (CRITIQUE warning) with a parallel block that warns when the PM overlay loads without the literal "Unless you directly instruct me to skip" substring. Same shape as the existing precedent.
- **Test coverage**: One unit test asserting the drafter's `## Open Questions` → `expectations` path works for the workflow phrase. One integration test asserting a fresh `plan` or `skip` reply routes back to the dormant session at confidence ≥ 0.80.
- **Documentation update**: Extend `docs/features/personas.md` with a new section on the PM workflow announcement (what it does, when it fires, the literal phrase, the override semantics).

### Flow

PM-mode chat → coding/feature/bug/automation/config message → PM agent reads overlay → PM emits announce + `## Open Questions` → drafter writes `expectations` → session goes dormant → human sees announcement → human replies `plan` or `skip` → router matches reply to dormant session at ≥ 0.80 → PM resumes with the chosen path.

### Technical Approach

**Decision: text-only, no structural enforcement.**

The issue's Solution Sketch raises an open question: "rather than edit the prompt and hope it's followed, attempt to enforce structurally — e.g., a stop-hook check that blocks `Bash`/`Write`/`Edit` tool calls in PM sessions until `expectations` has been written and a confirming reply is matched."

This plan adopts the **text-only** path. Rationale:

1. **Lower risk and reversible.** Persona text edits do not change runtime contracts. A failure mode (the agent ignores the rule anyway) reverts to current behavior, not a new failure mode.
2. **Direct precedent.** PR #802 added the CRITIQUE-substring loader warning. We mirror it. Same shape, same loader, same operator visibility.
3. **A blocking stop-hook would have unacceptable false positives.** The PM agent legitimately runs Bash for read-only triage in *every* session: `gh issue list`, `gh pr list`, `valor-telegram read`, `python -m tools.sdlc_stage_query`, etc. A hook that blocks Bash until `expectations` is written would block bucket #1 (questions answerable from context) and bucket #2 (status checks) — both of which use the same Bash surface. Distinguishing "Bash for triage" from "Bash for implementation" requires the same classification the PM overlay text already does, so the hook would re-implement the rule it's enforcing in a less expressive substrate.
4. **A blocking stop-hook for `Write`/`Edit` only is also wrong.** PM sessions are read-only by design (`agent/hooks/pre_tool_use.py` already enforces this for the pure PM session — no Write/Edit). The PM agent's failure mode is *not* writing files; it's *spawning a dev session* (or running `launchctl load` via Bash) that does the writing. A `Write`/`Edit` hook in the PM session catches nothing.
5. **The structural failure mode the issue worries about is real, but it's solved by the loader warning + future audit.** The loader warning catches overlay drift on the bridge machine where the private overlay is iCloud-synced and could fall out of sync. If the rule is missing from the overlay, the bridge logs WARN immediately on PM session startup. We do not need a runtime hook to catch this; we need overlay integrity, which the loader warning provides.

If the text-only approach proves insufficient in practice (measured by recurring "PM implemented without filing an issue" incidents over a 2-week observation window), we revisit and add a hook *then*. Premature hook addition is a rabbit hole — see Rabbit Holes section.

**Three coordinated changes:**

#### 1. Replace bucket #3 in BOTH overlay files

**Private overlay** (`~/Desktop/Valor/personas/project-manager.md`, line ~96, in the `### Intake and Triage` section): Replace the current bucket #3 line:

```markdown
3. **Coding task / feature request / bug report / software update** — route through SDLC: create a GitHub issue if none exists, then drive the pipeline (ISSUE → PLAN → CRITIQUE → BUILD → …). **Never implement code directly, even for small or "trivial" changes.**
```

with:

```markdown
3. **Coding task / feature / bug / software update / automation / config change** — STOP. Before doing anything that touches code, config, automation, or infrastructure, I announce the workflow contract and pause for confirmation.

   **Required announcement** (use this literal phrase):
   > "Unless you directly instruct me to skip our standard workflow, we need to file an issue to plan all improvements and changes to software."

   Then ask the human to reply with one of:
   - `plan` — file an issue and run `/do-plan`
   - `skip` — override SDLC for this task only (one-time override; does not persist)

   End the response with a `## Open Questions` section containing the workflow question verbatim. This populates `session.expectations` so the unthreaded-message router can match the human's reply back to this session.

   Then end the turn. Do NOT implement, plan, dispatch, or run any tool that writes code/config/infra in the same turn. The session transitions to `dormant`. When the human replies `plan` or `skip`, semantic routing resumes this session with their answer.
```

**In-repo template** (`config/personas/project-manager.md`): Currently this file does NOT contain a "Role / How I Work / Intake and Triage" section — it's only the pipeline-rules overlay. We **add** an "Intake and Triage" section that mirrors the private overlay's bucket #3 wording. Place it before the existing `## Hard Rules` section (under a new `## Intake and Triage` heading) so the overlay reads top-to-bottom as: identity → triage → hard rules → pipeline mechanics. This satisfies AC#2 ("public template is updated to match the private overlay's bucket #3 wording") by making the in-repo template a usable fallback when `~/Desktop/Valor/` is absent.

#### 2. Add two new sections to the PM overlay

Add to BOTH the private overlay and the in-repo template, immediately after the new "Intake and Triage" section.

**Section A — "What counts as a software change (issue required)":**

A bullet list naming concrete artifact types that must trigger bucket #3, plus a contrasting "no-issue tasks" list. Required artifact types per the issue's Solution Sketch §2:

- Source code in any repo (`.py`, `.js`, `.ts`, `.go`, `.sh`, etc.)
- LaunchAgents (`~/Library/LaunchAgents/*.plist`), launchd daemons (`~/Library/LaunchDaemons/`), system cron, systemd units
- Shell scripts, Python scripts, Node scripts (anywhere on disk)
- Runtime config files (`.env`, `projects.json`, `.mcp.json`, `settings.json`, `.plist`)
- Infrastructure changes (Vercel/Render/SMTP/DNS/IAM)
- New dependencies (anything added via `pip`, `npm`, `brew`, `uv add`, etc.)
- Anything new under `~/Library/LaunchAgents/`, `~/.local/bin/`, `/etc/`, `~/Library/LaunchDaemons/`

Plus a "no-issue tasks" list (handle directly, no announcement needed):
- Replying to messages, reading state, sending Telegram messages
- GitHub issue management (create/edit/label/close — these are the PM's job)
- Searching memory, running existing tools to read state
- Status reports and triage summaries

**Section B — "PM Overrides of Shared Defaults":**

A markdown table that reverses developer-flavored defaults from `config/personas/segments/work-patterns.md`. Must reverse at least 5 distinct defaults. Concrete table:

| Shared default (from `work-patterns.md`) | PM override |
|------------------------------------------|-------------|
| "Most work does not require check-ins: Code changes, refactoring, bug fixes" | Code changes ALWAYS require an issue + plan + announcement first. The PM does not implement code in any session. |
| "Implementation detail? My call." | Implementation choices belong to the dev session, not the PM. The PM dispatches; the dev session decides. |
| "Should I fix this bug I found? Yes, fix it" | Bugs require a GitHub issue. The PM files the issue and dispatches; the dev session fixes. |
| "Reversible decision? Make it and move on. Git exists." | The PM does not commit code. All commits flow through dev sessions on `session/{slug}` branches. |
| "YOLO mode — NO APPROVAL NEEDED." | The PM announces the workflow contract for any code/config/automation/infra request and waits for `plan` / `skip`. |
| "Git operations are FULLY autonomous - NO APPROVAL NEEDED" | The PM only commits docs/plans on main. Code commits are dev-session-only. The PM never runs `git commit` on a feature branch. |

End the section with the literal sentence:

> When the shared segment and this overlay disagree, this overlay wins.

This is the load-bearing override line — it tells the agent how to resolve the contradiction it would otherwise pick the louder side of.

#### 3. Loader-warning patch in `agent/sdk_client.py:919`

Add a parallel block immediately after the existing CRITIQUE warning. Existing block:

```python
if persona == "project-manager" and "CRITIQUE" not in overlay_content:
    logger.warning(
        f"PM persona overlay '{overlay_path}' is missing CRITIQUE gate rules "
        "— pipeline integrity may be compromised"
    )
```

New block (immediately after):

```python
if persona == "project-manager" and "Unless you directly instruct me to skip" not in overlay_content:
    logger.warning(
        f"PM persona overlay '{overlay_path}' is missing the workflow-announcement rule "
        "— PM may silently implement code/config changes without surfacing the SDLC contract."
    )
```

Same shape, same logger, same `overlay_path` interpolation. The substring is chosen to be specific enough that no incidental phrasing trips the check, and short enough to be robust to minor wording adjustments around it (we check for the unique opening clause, not the full phrase).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks in this change.
- [ ] The loader warning at `sdk_client.py:919` does not introduce exception handling — it's a substring check + log call. Existing tests in `tests/unit/test_persona_loading.py` cover the loader's exception paths (missing overlay → FileNotFoundError); add a case asserting the new WARN log fires when the substring is missing.

### Empty/Invalid Input Handling
- [ ] Empty `overlay_content` (zero-length file): the substring check returns `False`, the WARN log fires. Add a unit test asserting this.
- [ ] Whitespace-only overlay content: same — substring missing, WARN fires.
- [ ] Overlay content containing partial substring (e.g., "Unless you directly" without "instruct me to skip"): substring check returns `False`, WARN fires. This is correct; we want the full phrase or none.

### Error State Rendering
- [ ] If the drafter fails to extract `## Open Questions` (Haiku unavailable AND OpenRouter unavailable AND no `## Open Questions` section in the response), `expectations` stays None and the session does NOT go dormant on this signal — the PM never wrote the section. This is an upstream concern; the PM agent failing to follow the rule is what the loader warning surfaces.
- [ ] The fresh-reply routing path: if the human replies with neither `plan` nor `skip` (e.g., "actually, never mind, let's talk about something else"), the Haiku classifier returns confidence < 0.80 and the message creates a new session. This is the correct fallback — `## Open Questions` was answered via topic change, not via the expected tokens. No bug.

## Test Impact

- [ ] `tests/unit/test_open_question_gate.py` — UPDATE: add a new test class `TestWorkflowAnnouncementExtraction` that asserts a PM response containing the literal "Unless you directly instruct me to skip" announcement plus a `## Open Questions` section produces `expectations` with the workflow question text. Existing tests must continue to pass without modification.
- [ ] `tests/integration/test_unthreaded_routing.py` — UPDATE: add `TestPlanSkipReplyRouting` test class that creates a dormant PM session with `expectations="Should I file an issue (plan) or skip SDLC (skip)?"` and asserts a fresh `plan` reply routes back to the dormant session via the semantic router at confidence ≥ 0.80, and a fresh `skip` reply does the same. Existing tests must continue to pass without modification.
- [ ] `tests/unit/test_persona_loading.py` — UPDATE: add `TestPMWorkflowAnnouncementWarning` test class that asserts the loader emits a WARN log when the PM overlay does NOT contain "Unless you directly instruct me to skip". Mirrors the existing CRITIQUE-warning test pattern. Existing tests must continue to pass without modification.

No DELETE or REPLACE dispositions — all existing tests remain valid; we only add new test classes inside existing files.

## Rabbit Holes

- **Editing `config/personas/segments/work-patterns.md` to be persona-aware.** The issue explicitly drops this from scope. The segments architecture per `manifest.json` is intentionally universal; making segments persona-aware is a larger architectural change that should land separately if needed. We override via the PM overlay, not by forking segments.
- **Adding a stop-hook to block Bash/Write/Edit until expectations is written.** Tempting because it would catch the failure mode structurally. Rejected for now (see Solution → Technical Approach for full rationale): it would have unacceptable false positives on legitimate triage Bash, the PM session is already read-only for Write/Edit (so the hook catches nothing useful), and the failure mode the issue worries about is overlay drift, which the loader warning already addresses.
- **Renaming the reply tokens to something "more descriptive" (e.g., `plan-issue` / `skip-sdlc`).** The current tokens are load-bearing — chosen so a single-word Telegram reply contains enough lexical signal for the Haiku classifier to clear the 0.80 threshold against the stored expectations text. Multi-word tokens are no better. Renaming requires re-verifying the classifier; not worth it without a measured failure.
- **Backfilling a GitHub issue for the existing PBA briefing LaunchAgents.** Out of scope (issue #1189 explicitly drops this). That's a separate cleanup decision (delete vs. retroactively legitimize) for the human, not part of this PM-persona fix.
- **Generalizing the announce-and-pause flow to all personas.** Only the PM persona has the bucket #3 problem; the developer persona is *supposed* to autonomously implement, and the teammate persona doesn't dispatch coding work. Generalizing would be solving a problem we don't have.

## Risks

### Risk 1: The PM agent ignores the new rule and implements anyway.
**Impact:** The failure mode the plan is meant to prevent persists. The human still has no signal until after the work has shipped.
**Mitigation:**
- The loader warning at `sdk_client.py:919` ensures the rule is *physically present* in the overlay every time a PM session starts. If the overlay drifts (private file out of sync), the bridge logs WARN immediately.
- The "PM Overrides of Shared Defaults" table directly contradicts the developer-flavored defaults the agent inherits, with the literal sentence "When the shared segment and this overlay disagree, this overlay wins." This is the strongest text-level intervention available.
- 2-week observation window after merge: if the failure mode recurs, we revisit and add a structural enforcement (stop-hook). The plan explicitly defers the hook decision to a measured outcome.

### Risk 2: The Haiku classifier mismatches `plan` / `skip` replies.
**Impact:** Human replies `plan`, but the router creates a new session instead of resuming. The PM never gets the answer; the human is confused.
**Mitigation:**
- The tokens are intentionally short and contextually distinctive against the stored `expectations` text ("Should I file an issue (plan) or skip SDLC (skip)?"). The 0.80 threshold has been load-tested in `tests/integration/test_unthreaded_routing.py` for similar single-word replies.
- New integration test asserts both `plan` and `skip` reach confidence ≥ 0.80 against the workflow expectations text. If the test fails on real Haiku output, we adjust the expectations wording to make the match unambiguous.

### Risk 3: The in-repo template diverges from the private overlay.
**Impact:** Dev machines without `~/Desktop/Valor/` load the in-repo template, which now mirrors the private overlay's "Intake and Triage" section. If the private overlay updates and the in-repo template doesn't, dev-machine PM sessions get stale rules.
**Mitigation:**
- This is an existing risk (the private overlay has always been the source of truth, the in-repo template a fallback). Not introduced by this plan.
- Add a future audit task (out of scope for this plan): periodically diff the two files and flag divergence. Not in this plan; tracking only.
- For this plan: when we ship, both files are updated atomically in the same PR. Future drift is a separate concern.

## Race Conditions

No race conditions identified. All operations are synchronous from the agent's perspective:

- Persona overlay loading is a one-shot file read at session startup (`agent/sdk_client.py::load_persona_prompt`).
- The drafter runs synchronously after the agent's turn ends; `_persist_routing_fields` writes `expectations` before the transcript completes.
- The transcript completion in `bridge/session_transcript.py:317` calls `transition_status(session, "dormant", ...)` after `expectations` is already persisted.
- The unthreaded-message router runs only after a fresh message arrives, by which time the dormant session is fully persisted.

There is no concurrent-session scenario where `expectations` could be read before written, or where the transition to `dormant` could race the routing decision.

## No-Gos (Out of Scope)

- Backfilling a GitHub issue for the existing 2026-04-28 PBA briefing LaunchAgents. Separate cleanup decision (delete vs. retroactively legitimize) for the human.
- Editing `config/personas/segments/work-patterns.md` to be persona-aware. The segments architecture is intentionally universal per `manifest.json`. We override via the PM overlay, not by forking the segment.
- Adding a stop-hook to block Bash/Write/Edit until `expectations` is written. Deferred unless the text-only approach proves insufficient over a 2-week observation window.
- Renaming the `plan` / `skip` tokens. They are load-bearing for the routing classifier.
- Generalizing the announce-and-pause flow to non-PM personas. Only PM has the bucket #3 problem.
- Schema changes. `expectations` and `context_summary` already exist on AgentSession.

## Update System

No update system changes required. This feature is purely internal to the PM persona overlay and a single loader-warning patch in `agent/sdk_client.py`. The standard `/update` workflow already pulls latest, restarts the bridge (which re-loads the persona at session startup), and runs doctor checks. There are no new dependencies, no new config files, no migration steps for existing installations.

**One operational note:** The private overlay at `~/Desktop/Valor/personas/project-manager.md` lives outside the repo (iCloud-synced). After this PR merges, each bridge machine's private overlay must be hand-edited (or copied from the in-repo template) to add the new sections. This is a one-time per-machine action; the loader WARN log will flag any machine that hasn't been updated, so the operator gets immediate feedback. This is consistent with existing private-overlay handling (#1148 / PR #1152 took the same approach).

## Agent Integration

No agent integration required — this is a persona-text + loader-warning change.

- No new MCP server functionality.
- No changes to `.mcp.json`.
- No bridge code changes (`bridge/telegram_bridge.py` is unchanged; the PM persona is loaded by `agent/sdk_client.py::load_pm_system_prompt` which is unchanged structurally — only the overlay text and the in-loader substring check change).
- The existing semantic-routing infrastructure (`bridge/session_router.py`, `bridge/message_drafter.py`, `agent/output_handler.py`) is used as designed; we don't add to it, we exercise it.
- Integration test (`tests/integration/test_unthreaded_routing.py`) verifies the PM agent's `## Open Questions` flow works end-to-end via the existing routing path.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/personas.md` with a new section "PM Workflow Announcement" describing what bucket #3 does, the literal announcement phrase, the `plan` / `skip` reply tokens, and the override semantics (one-time, does not persist).
- [ ] No new `docs/features/<slug>.md` file needed — the PM workflow announcement is a feature *of* the personas system, not a new system. Adding a section to the existing `docs/features/personas.md` is the right home.
- [ ] No update to `docs/features/README.md` index table required — the entry for "Personas" already exists at line 86 of the index and points at `personas.md`. The new section is internal to that doc.

### External Documentation Site
- This repo does not use Sphinx, Read the Docs, or MkDocs. Skipping.

### Inline Documentation
- [ ] Add a docstring to the new loader-warning block in `agent/sdk_client.py` explaining what the substring check guards against (overlay drift on bridge machines where the private overlay is iCloud-synced).
- [ ] No new functions or public APIs introduced; existing docstrings unchanged.

## Success Criteria

- [ ] `~/Desktop/Valor/personas/project-manager.md` bucket #3 contains the literal phrase "Unless you directly instruct me to skip our standard workflow"
- [ ] `config/personas/project-manager.md` (in-repo template) has an "Intake and Triage" section matching the private overlay's bucket #3 wording
- [ ] PM overlay (both files) contains a "What counts as a software change (issue required)" enumeration explicitly naming LaunchAgents, cron, launchd, shell scripts, runtime config files, infrastructure, and new dependencies
- [ ] PM overlay (both files) contains a "PM Overrides of Shared Defaults" table reversing ≥5 specific developer-flavored defaults from `work-patterns.md` and ending with "When the shared segment and this overlay disagree, this overlay wins"
- [ ] `agent/sdk_client.py` emits a WARN log when the PM overlay is loaded and does NOT contain the substring "Unless you directly instruct me to skip"
- [ ] New unit test confirms a PM agent response containing a `## Open Questions` section with the workflow question populates `session.expectations` with the question text (in `tests/unit/test_open_question_gate.py`)
- [ ] New unit test confirms the loader WARN fires when the PM overlay is missing the workflow-announcement substring (in `tests/unit/test_persona_loading.py`)
- [ ] New integration test confirms a fresh unthreaded `plan` or `skip` reply in the same chat routes back to the dormant session at confidence ≥ 0.80 (in `tests/integration/test_unthreaded_routing.py`)
- [ ] No regression in `tests/unit/test_open_question_gate.py`, `tests/integration/test_unthreaded_routing.py`, or `tests/unit/test_persona_loading.py`
- [ ] `docs/features/personas.md` has a new section describing the PM Workflow Announcement
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (persona overlay text)**
  - Name: `pm-overlay-builder`
  - Role: Edit both PM overlay files (private and in-repo template) — replace bucket #3, add "What counts as a software change" enumeration, add "PM Overrides of Shared Defaults" table.
  - Agent Type: builder
  - Resume: false

- **Builder (loader patch + tests)**
  - Name: `loader-builder`
  - Role: Add the parallel WARN block in `agent/sdk_client.py:919–924`, write the new unit test in `test_persona_loading.py`, write the new test in `test_open_question_gate.py`, write the new integration test in `test_unthreaded_routing.py`.
  - Agent Type: builder
  - Resume: false

- **Validator (full suite)**
  - Name: `pm-overlay-validator`
  - Role: Run unit and integration tests, lint and format, verify all Success Criteria checkboxes are objectively met by inspecting the diff.
  - Agent Type: validator
  - Resume: false

- **Documentarian**
  - Name: `pm-overlay-documentarian`
  - Role: Update `docs/features/personas.md` with the new section.
  - Agent Type: documentarian
  - Resume: false

## Step by Step Tasks

### 1. Update both PM overlay files
- **Task ID**: build-overlay
- **Depends On**: none
- **Validates**: bucket #3 text matches the spec verbatim; "What counts" enumeration includes all 7 named artifact categories; "PM Overrides" table reverses ≥5 work-patterns defaults; both files end the table with "When the shared segment and this overlay disagree, this overlay wins."
- **Informed By**: none
- **Assigned To**: pm-overlay-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `~/Desktop/Valor/personas/project-manager.md` line 96 — replace bucket #3 with the announce-then-pause version per Solution → Technical Approach §1
- Edit `config/personas/project-manager.md` — add a new `## Intake and Triage` section before `## Hard Rules` mirroring the private overlay's bucket #3 content
- Add "What counts as a software change (issue required)" enumeration to BOTH files (private + in-repo) immediately after the new bucket #3 / Intake and Triage section
- Add "PM Overrides of Shared Defaults" table to BOTH files immediately after the "What counts" enumeration
- End each "PM Overrides" table with the literal sentence: "When the shared segment and this overlay disagree, this overlay wins."

### 2. Patch the loader and add tests
- **Task ID**: build-loader-and-tests
- **Depends On**: none (can run parallel to overlay edit)
- **Validates**: `tests/unit/test_persona_loading.py` (existing + new), `tests/unit/test_open_question_gate.py` (existing + new), `tests/integration/test_unthreaded_routing.py` (existing + new); `agent/sdk_client.py` has the parallel WARN block at line ~924
- **Informed By**: none
- **Assigned To**: loader-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the parallel WARN block in `agent/sdk_client.py` immediately after the existing CRITIQUE warning (currently at lines 919–923)
- Add `TestPMWorkflowAnnouncementWarning` test class to `tests/unit/test_persona_loading.py` asserting the WARN log fires when the overlay is missing the workflow-announcement substring (mirror existing CRITIQUE-warning test pattern)
- Add `TestWorkflowAnnouncementExtraction` test class to `tests/unit/test_open_question_gate.py` asserting `## Open Questions` extraction works for the workflow phrase
- Add `TestPlanSkipReplyRouting` test class to `tests/integration/test_unthreaded_routing.py` asserting `plan` and `skip` replies match dormant sessions at confidence ≥ 0.80

### 3. Validate
- **Task ID**: validate-all
- **Depends On**: build-overlay, build-loader-and-tests
- **Assigned To**: pm-overlay-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_persona_loading.py tests/unit/test_open_question_gate.py tests/integration/test_unthreaded_routing.py -v`
- Run `python -m ruff check .` and `python -m ruff format --check .`
- Verify each Success Criteria checkbox by inspecting the diff and the running PM session's loader log
- Report pass/fail status

### 4. Document
- **Task ID**: document-pm-workflow-announcement
- **Depends On**: validate-all
- **Assigned To**: pm-overlay-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Add new section "PM Workflow Announcement" to `docs/features/personas.md`
- Document the announcement phrase, the `plan` / `skip` reply tokens, and the override semantics (one-time, does not persist)
- Cross-reference the issue (#1189), the loader warning location (`agent/sdk_client.py:919`), and the existing routing infrastructure (`bridge/session_router.py`, `bridge/message_drafter.py`)
- Verify `docs/features/README.md` index entry for "Personas" still points at this doc (no index change needed)

### 5. Final validation
- **Task ID**: final-validate
- **Depends On**: document-pm-workflow-announcement
- **Assigned To**: pm-overlay-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/ -x -q` (no regressions in any test file)
- Verify all Success Criteria checkboxes
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_persona_loading.py tests/unit/test_open_question_gate.py tests/integration/test_unthreaded_routing.py -x -q` | exit code 0 |
| Full suite passes | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Private overlay has the announcement phrase | `grep -q "Unless you directly instruct me to skip our standard workflow" ~/Desktop/Valor/personas/project-manager.md` | exit code 0 |
| In-repo template has the announcement phrase | `grep -q "Unless you directly instruct me to skip our standard workflow" config/personas/project-manager.md` | exit code 0 |
| In-repo template has "PM Overrides of Shared Defaults" table | `grep -q "PM Overrides of Shared Defaults" config/personas/project-manager.md` | exit code 0 |
| In-repo template has the closing sentence | `grep -q "When the shared segment and this overlay disagree, this overlay wins." config/personas/project-manager.md` | exit code 0 |
| Loader has the new WARN block | `grep -q "Unless you directly instruct me to skip" agent/sdk_client.py` | exit code 0 |
| Docs updated | `grep -q "PM Workflow Announcement" docs/features/personas.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Text-only vs. text + structural hook?** The plan adopts text-only (Solution → Technical Approach explains why). The remaining decision: are you comfortable with the 2-week observation window before revisiting the hook decision? If you want a faster signal (e.g., 1 week, or measured per-incident rather than per-window), say so and I will adjust the post-merge follow-up commitment.
2. **In-repo template structure**: the freshness check found that `config/personas/project-manager.md` does NOT currently mirror the private overlay's "Role / How I Work / Intake and Triage" content — it's only the pipeline-rules overlay. The plan adds a new `## Intake and Triage` section to satisfy AC#2. Is that the right structural placement, or do you want a separate `config/personas/project-manager-triage.md` file the loader concatenates? (The plan assumes single-file additions are simpler.)
3. **Should the "skip" override be one-time or session-wide?** The plan specifies one-time (the next bucket-#3 message in the same session re-fires the announcement). Alternative: session-wide (once skipped, the rest of this session bypasses bucket #3). The plan defaults to one-time because session-wide creates ambiguity if the human switches topics mid-session. Confirm.
