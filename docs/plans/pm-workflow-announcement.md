---
status: Planning
type: feature
appetite: Small
owner: Valor
created: 2026-04-29
tracking: https://github.com/tomcounsell/ai/issues/1189
last_comment_id:
---

# PM Persona — Announce Workflow Boundary and Pause for plan/skip Confirmation

## Problem

The PM persona is supposed to route every coding/feature/bug/automation/config request through SDLC starting with a GitHub issue. The rule is real — bucket #3 of the "Intake and Triage" section in `~/Desktop/Valor/personas/project-manager.md:96` says: *"route through SDLC: create a GitHub issue if none exists, then drive the pipeline (ISSUE → PLAN → CRITIQUE → BUILD → …). Never implement code directly, even for small or 'trivial' changes."*

But the rule is silent and frequently bypassed because:

1. **Shared segment outranks the overlay.** `agent/sdk_client.py::load_persona_prompt` prepends `config/personas/segments/work-patterns.md` *before* the PM overlay. That segment is written from a developer's autonomous-mode perspective and tells the agent things like "Most work does not require check-ins: Code changes, refactoring, bug fixes," "Implementation detail? My call," "Should I fix this bug I found? Yes, fix it," and "YOLO mode — NO APPROVAL NEEDED." The PM rules come after, but the developer-flavored content is louder, longer, and gets read first.
2. **The workflow contract is never spoken aloud.** Even when the PM follows the rule, the human sees no announcement that an issue is being filed (or skipped). When the PM violates the rule, the human has no signal until after the work has shipped.
3. **No pause-and-confirm.** The persona expects the PM to either silently file an issue or silently implement. There is no documented behavior for "ask the human, wait, and proceed based on their answer."

**Current behavior:**

A PM session for the PBA project (2026-04-28) built a daily-briefing system entirely outside SDLC — created `~/Library/LaunchAgents/com.valor.pba.briefing.{morning,evening}.plist` (loaded into launchd), created `~/src/pba.ai/scripts/daily_briefing.sh` (~100 LoC shell + embedded prompt), and posted "Briefings: live" to the `PM: PBA` Telegram chat. There is no GitHub issue, no plan doc, no PR for this work. The infrastructure is real and functional. The rule was in the prompt; the agent did not surface it; the human had no opportunity to either confirm or override before the LaunchAgents were on disk.

**Desired outcome:**

When intake bucket #3 fires (request touches code, config, automation, infra, or scripts), the PM:

1. **Stops** before doing anything that touches code/config/automation/infra.
2. **Announces** the workflow contract using a literal phrase the human will recognize: *"Unless you directly instruct me to skip our standard workflow, we need to file an issue to plan all improvements and changes to software."*
3. **Asks** for a structured short-token reply: `plan` (file an issue + run `/do-plan`) or `skip` (override SDLC for this task only).
4. **Persists** the question to `session.expectations` by ending the response with a `## Open Questions` section — the drafter at `bridge/message_drafter.py:1725` copies it into the field.
5. **Ends the turn**, transitioning the session to `dormant`.
6. **Resumes inside the same session** when the human replies, via the existing semantic routing path: `bridge/session_router.py::find_matching_session` queries dormant sessions in the chat with non-null `expectations` and matches via Haiku at confidence ≥ 0.80. The `plan`/`skip` tokens are deliberately chosen so even a one-word fresh reply clears the threshold.

The shared segment's developer-mode defaults (commit autonomously, decide implementation details, don't escalate) are explicitly overridden in the PM overlay so the agent doesn't read two contradictory documents and pick the louder one. A loader-side warning catches overlay drift on machines where the private overlay is stale.

## Freshness Check

**Baseline commit:** `6bbb2b9258b318fafb37dc90f3e648ddca0ccf1b`
**Issue filed at:** `2026-04-28T05:58:52Z`
**Disposition:** Unchanged

**File:line references re-verified:**
- `~/Desktop/Valor/personas/project-manager.md:96` — bucket #3 wording — still holds (verified 2026-04-29). Private overlay is 254 lines, last modified 2026-04-24.
- `agent/sdk_client.py:919` — existing CRITIQUE-missing loader warning — still holds (verified). The function `load_persona_prompt` lives in this file; the warning is the precedent for the proposed loader-warning addition.
- `models/agent_session.py:201` — `expectations` field — still holds. `Field(null=True)` on the AgentSession model.
- `models/agent_session.py:108` — `dormant` status definition — still holds: "Paused on open question, waiting for human reply."
- `bridge/message_drafter.py:1725` — open-question extraction path — still holds. `expectations = structured.expectations` then falls back to `_extract_open_questions(raw_response)` if not set.
- `agent/output_handler.py:380` — `_persist_routing_fields` write path — still holds (`session.expectations = expectations`).
- `bridge/session_router.py:50-130` — `find_matching_session` flow — still holds; threshold `>= 0.80` is enforced on the classifier output.
- `config/personas/segments/work-patterns.md` — developer-flavored defaults — still holds. Verified strings: "Most work does not require check-ins", "Implementation detail? My call", "Should I fix this bug I found? Yes, fix it", "YOLO mode - NO APPROVAL NEEDED".

**Cited sibling issues/PRs re-checked:**
- #274 — closed (semantic session routing infrastructure shipped) — still relevant prior art.
- #318 — closed (unthreaded routing extension) — still relevant prior art.
- #280 — closed (verbatim `## Open Questions` extraction; motivated `_extract_open_questions`) — still relevant prior art.
- #1007 / PR #1009 — closed/merged (PM persona hardening: established the pattern of adding sections to the PM overlay) — still relevant prior art; pattern reuse.
- #1148 / PR #1152 — closed/merged (SESSION_TYPE injection + PM persona load into harness sessions) — confirmed; this issue's persona changes will reach production.

**Commits on main since issue was filed (touching referenced files):**
- None of `agent/sdk_client.py`, `config/personas/`, `models/agent_session.py`, `bridge/message_drafter.py`, `bridge/session_router.py` have been touched since 2026-04-28T05:58:52Z. `git log --oneline --since="..."` returned empty.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/pm-persona-hardening.md` (issue #1007) — adds different sections (Pre-Completion Checklist, Child Session Monitoring, Exit Validation). No overlap with bucket #3 / Intake and Triage / shared-segment override; pattern is reusable.
- `docs/plans/pm-skips-critique-and-review.md` — addresses pipeline-stage-skipping. Different failure mode; no overlap with intake-bucket announcement.
- No plan file at `docs/plans/pm-workflow-announcement.md` exists yet — this plan is greenfield in that path.

**Notes:** Discovered during recon that the **public template** at `config/personas/project-manager.md` does **NOT** contain an Intake and Triage section — only the private overlay at `~/Desktop/Valor/personas/project-manager.md` has it. The public template is currently scoped to "Hard Rules" (CRITIQUE/REVIEW/MERGE gates) and rule numbering. Issue #1189 says to update both files to "match" — but the public template doesn't have the section to update. Resolution: **add** the Intake and Triage section + new sections to the public template so it serves as a real authoritative fallback when the private overlay is missing on dev machines. This is captured in the Open Question section to confirm direction with the supervisor before BUILD.

## Prior Art

Search results from `gh issue list --state closed` and `gh pr list --state merged`:

- **#274** — Semantic Session Routing: Structured Summarizer + Context-Aware Message Routing — Original implementation of the `expectations` + routing infrastructure this issue depends on. Successful; landed.
- **#318** — Route unthreaded messages into active sessions via `expectations` + `queued_steering_messages` — Extended #274 to handle active (not just dormant) sessions; introduced the decision matrix this issue's flow plugs into. Successful; landed.
- **#280** — Summarizer fabricates questions not present in raw agent output — Motivated the verbatim `## Open Questions` extraction path in the drafter (`bridge/message_drafter.py:1725-1732`) that this issue's PM update relies on. Successful; landed.
- **#1007 / PR #1009** — PM persona needs self-monitoring and pipeline completion guards — Most recent significant PM persona overlay edit. Established the convention of adding new sections (Pre-Completion Checklist, Child Session Monitoring, Exit Validation) to the overlay. Pattern directly reusable.
- **#1148 / PR #1152** — Inject SESSION_TYPE + load PM persona into harness sessions — Confirms the PM persona is now actually loaded for harness sessions; this issue's persona changes will reach production.

No prior issue attempted to fix the "shared segment outranks overlay" tension; this is the first time it's being addressed. The prior PM-overlay edits did not change the bucket #3 wording.

## Research

No relevant external findings — this work is purely internal (persona text edits + a Python loader warning + an optional internal validator hook). No external libraries, APIs, or ecosystem patterns are involved. Proceeding with codebase context.

## Spike Results

No spikes needed — all assumptions in the issue's Solution Sketch are verifiable by reading the cited code paths, and recon confirmed every cited file:line still holds. The remaining uncertainty (text-only vs. text+hook) is a human design decision, not an empirical question — captured in Open Questions.

## Data Flow

The pause-and-confirm flow piggybacks on the existing semantic-routing path. No new data flow is introduced. Trace:

1. **Entry**: Human sends a Telegram message that the PM session classifies as bucket #3 (coding/automation/config request).
2. **PM session**: Reads bucket #3 rule, emits a response containing the workflow-announcement phrase plus a `## Open Questions` section asking for `plan` or `skip`, then exits the turn.
3. **Drafter** (`bridge/message_drafter.py:1725`): Extracts the `## Open Questions` content into `expectations` (verbatim, bypassing anti-fabrication filter).
4. **Output handler** (`agent/output_handler.py:380`): Writes `session.expectations = expectations` and `session.save()`.
5. **Lifecycle**: Session transitions to `dormant` (status documented at `models/agent_session.py:108`).
6. **Human reply**: Sends `plan` or `skip` (or longer) as a fresh Telegram message in the same chat.
7. **Bridge router** (`bridge/session_router.py:53`): Queries `AgentSession` by `chat_id`, filters to `status in (active, dormant)` with non-null `expectations`. The dormant PM session is a candidate.
8. **Haiku classifier**: Builds the multiple-choice prompt, returns `{"match": "<session_id>", "confidence": 0.85+, "reason": "..."}`. Confidence threshold ≥ 0.80 routes the reply back to the dormant session.
9. **PM resumes**: Same session, same context. Branch on the reply: `plan` → file an issue (or use existing) and dispatch `/sdlc`; `skip` → proceed without SDLC for this task only.

No new state, no new IPC, no new keys. The mechanism is already shipped — the change is making the PM use it.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** None to Python APIs. PM persona overlay text changes; `agent/sdk_client.py::load_persona_prompt` gains one additional warning check (parallel to the existing CRITIQUE-missing check).
- **Coupling:** No coupling change. The PM overlay already drives behavior; this issue tightens what the overlay says, not how it's loaded.
- **Data ownership:** Unchanged. `session.expectations` is already PM-owned (and any session that emits `## Open Questions`).
- **Reversibility:** Trivial — revert the overlay edits and the loader-warning line. No data migration, no schema change, no users impacted.

## Appetite

**Size:** Small

**Team:** Solo dev (with PM check-in for the open question on text-only vs. text+hook approach)

**Interactions:**
- PM check-ins: 1 (resolve "text only vs. text+hook" before BUILD)
- Review rounds: 1 (standard PR review)

This is a prompt edit + a one-line Python warning. The complexity is in getting the PM persona text right, not in the code change.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `~/Desktop/Valor/personas/project-manager.md` exists | `test -f ~/Desktop/Valor/personas/project-manager.md && echo OK` | Private overlay is the runtime authority on this machine |
| Python venv has anthropic SDK | `.venv/bin/python -c "import anthropic; print(anthropic.__version__)"` | Required for any new test that exercises the routing classifier |

## Solution

### Key Elements

- **PM overlay bucket #3 rewrite**: Replace the silent rule with an announce-then-pause version that contains the literal phrase, the `plan`/`skip` tokens, and the `## Open Questions` requirement. Applies to BOTH `~/Desktop/Valor/personas/project-manager.md` (private) and `config/personas/project-manager.md` (public template, requires adding the Intake section).
- **"What counts as a software change" enumeration**: Explicit list of categories (LaunchAgents/cron/launchd/systemd, shell/Python/Node scripts, runtime config files, infrastructure, new dependencies, new files under `~/Library/LaunchAgents/`, `~/.local/bin/`, `/etc/`, `~/Library/LaunchDaemons/`) so the "it's just config" rationalization fails. Plus a clear "no-issue tasks" list for handle-directly cases.
- **"PM Overrides of Shared Defaults" table**: Reverses developer-flavored defaults from `work-patterns.md` (code-changes-don't-need-check-ins, implementation-detail-my-call, fix-the-bug-yourself, reversible-decisions-just-do-it, YOLO mode), with closing line "When the shared segment and this overlay disagree, this overlay wins." Mirrors the precedent in PR #1009.
- **Loader warning**: Extend `agent/sdk_client.py:919` with a parallel check for the announcement phrase. Catches overlay drift on machines where the private overlay lives in iCloud and may be stale or out of sync.

### Flow

Bucket-#3 message → PM emits announcement + `## Open Questions` (with `plan`/`skip` tokens) → drafter writes `expectations` → session goes `dormant` → human replies `plan` or `skip` → router matches reply back to the same session → PM branches on reply (file issue + run `/sdlc`, or proceed with SDLC bypass).

### Technical Approach

This is a prompt-engineering change with a thin Python guardrail. Three coordinated edits:

1. **`~/Desktop/Valor/personas/project-manager.md`** — Rewrite bucket #3 (currently line 96), then append two new sections after Intake and Triage: "What counts as a software change (issue required)" and "PM Overrides of Shared Defaults". The rewritten bucket #3 contains the literal phrase verbatim, names the `plan`/`skip` tokens, requires `## Open Questions` to be the closing section, and forbids implementation in the same turn. The bucket #3 description itself expands beyond "coding task / feature request / bug report / software update" to cover "automation, scripts (shell, Python, Node), runtime config (`.env`, `projects.json`, `.mcp.json`, `settings.json`, plist files), infrastructure (Vercel/Render/SMTP/DNS/IAM/launchd/cron), and new dependencies."
2. **`config/personas/project-manager.md`** (public template) — This file currently has only Hard Rules (CRITIQUE/REVIEW/MERGE gates). Add an Intake and Triage section that mirrors the private overlay, plus the two new sections from step 1. The public template is the authoritative fallback when the private overlay is absent (e.g., on dev machines per `_resolve_overlay_path`); it must contain the same intake rules so dev-machine PM behavior matches production.
3. **`agent/sdk_client.py`** — Inside `load_persona_prompt`, after the existing CRITIQUE-missing check (line 919) and the `subagent_type="dev-session"` deprecation check (line 924), add a third check:

   ```python
   if persona == "project-manager" and "Unless you directly instruct me to skip" not in overlay_content:
       logger.warning(
           f"PM persona overlay '{overlay_path}' is missing the workflow-announcement rule "
           "— PM may silently implement code/config changes without surfacing the SDLC contract."
       )
   ```

   Same pattern, same precedent. Just a warning — the loader does not refuse to load.

The `plan`/`skip` token choice is load-bearing: short, distinctive, semantically unambiguous to the Haiku classifier. The classifier prompt already enforces ≥0.80 confidence; the `plan`/`skip` tokens trivially clear that bar because the dormant session's `expectations` contains those literal tokens.

**Open question (resolved before BUILD):** The issue raises whether to also add a stop-hook check in `.claude/hooks/` that blocks `Bash`/`Write`/`Edit` tool calls in PM sessions until `expectations` has been written. This is a structural enforcement layer that catches the failure mode the persona text alone cannot. Recommendation: **start with text-only** (this plan), and if a follow-up incident shows the persona text is not sufficient, file a separate issue for the hook. Rationale: prompt-only changes have precedent in #1007/PR #1009; structural hooks have precedent in `validate_no_raw_redis_delete.py` but are heavier to maintain. Ship the lightweight fix first; add structural enforcement only if needed. Captured under Open Questions for supervisor confirmation.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception:` blocks added by this work. The `_persist_routing_fields` path already has `try/except` swallowing persistence errors (`agent/output_handler.py:390`) — that behavior is unchanged; existing tests cover it.

### Empty/Invalid Input Handling
- [ ] If the human reply is empty/whitespace-only, the existing router behavior applies: `find_matching_session` is invoked, the Haiku classifier sees an empty message, and returns null/low-confidence — the message becomes a fresh session, NOT a routing match. No new edge case introduced.
- [ ] If the human reply is something other than `plan` or `skip` (e.g., a question, a clarification), the Haiku classifier's reasoning naturally handles it — the dormant session's `expectations` contains the announcement question, so the classifier matches on relevance not literal token. The PM session resumes with the human's actual response and decides how to interpret it.

### Error State Rendering
- [ ] If the PM persona overlay is malformed (missing the announcement phrase), the loader warning logs to stderr but the session still loads. This is intentional — same shape as the existing CRITIQUE warning. Test asserts the warning fires.
- [ ] If `_extract_open_questions` finds the workflow question but the drafter's anti-fabrication filter would normally strip it, the verbatim-extraction path bypasses the filter (`bridge/message_drafter.py:1727-1732` is the existing fallback path). Test asserts `expectations` is populated when the response ends with a `## Open Questions` section containing the announcement question.

## Test Impact

- [ ] `tests/unit/test_open_question_gate.py` — UPDATE: add a new test case asserting that a PM-shaped response containing the workflow announcement phrase + `## Open Questions` populates `expectations` correctly. Existing tests are unaffected.
- [ ] `tests/integration/test_unthreaded_routing.py` — UPDATE: add a new test case asserting that a fresh `plan` reply (or `skip` reply) in the same chat routes back to a dormant PM session whose `expectations` contains the workflow question. Confidence threshold ≥0.80 must hold. Existing tests are unaffected.
- [ ] `tests/unit/test_pm_persona_guards.py` — UPDATE: add new test cases asserting (a) the private overlay (or public template) contains the literal announcement phrase, (b) the "What counts as a software change" enumeration names LaunchAgents/cron/launchd/shell scripts/runtime config/infrastructure/new dependencies, (c) the "PM Overrides of Shared Defaults" table reverses ≥5 developer-flavored defaults and ends with "When the shared segment and this overlay disagree, this overlay wins." This file already exists (created by PR #1009).
- [ ] New unit test: `tests/unit/test_sdk_client_persona_warnings.py` (REPLACE if exists, otherwise CREATE): add test asserting that loading the PM persona with an overlay missing the announcement phrase emits a WARNING log to the logger.

## Rabbit Holes

- **Editing `work-patterns.md` to be persona-aware.** Tempting (the contradiction is structural), but the segments architecture is intentionally universal per `config/personas/segments/manifest.json`. Overriding via the PM overlay is the lower-risk path; making segments persona-aware is a larger architectural change and explicitly out of scope per the issue.
- **Backfilling an issue for the existing PBA briefing LaunchAgents.** That's a separate cleanup decision (delete vs. retroactively legitimize); not part of this PM-persona fix.
- **Building the structural stop-hook now.** Tempting (more enforcement), but unnecessary if the persona text is sufficient. Defer to a follow-up issue if real incidents show text-only is not enough. Adding a hook touches `.claude/hooks/pre_tool_use.py` (currently 92 lines, simple) and would need session-type detection plus an `expectations`-set check — non-trivial test coverage.
- **Renaming or expanding the `plan`/`skip` token set.** The tokens are deliberately short and distinctive to clear the 0.80 confidence threshold. Adding more tokens (e.g., `defer`, `triage`, `noop`) dilutes that signal. If the human wants a richer reply, the Haiku classifier already handles it — no schema change needed.

## Risks

### Risk 1: PM ignores the overlay rule in practice
**Impact:** The persona text says one thing; the agent does another. Same failure mode as before.
**Mitigation:** Three layers — (a) the announcement phrase becomes part of the explicit override table, making the rule unmissable in the prompt; (b) the loader warning catches overlay drift on the machine where production runs; (c) `tests/unit/test_pm_persona_guards.py` asserts the announcement phrase exists in the overlay file (CI gate). If the agent ignores all three, the structural hook approach (deferred to a follow-up) becomes warranted.

### Risk 2: Haiku classifier misroutes `plan` / `skip` reply
**Impact:** Human types `plan`; classifier returns confidence 0.79; reply spawns a new session instead of resuming the dormant one. PM sits dormant with stale expectations indefinitely.
**Mitigation:** The classifier prompt has been tested for similar short-token replies in the existing routing tests. The dormant session's `expectations` will contain the *full* announcement question, not just the tokens — so the Haiku model sees rich context. If the threshold is genuinely too tight, that's a routing-system bug independent of this issue. Mitigation: integration test in `tests/integration/test_unthreaded_routing.py` that asserts the route succeeds with confidence ≥0.80 for both `plan` and `skip` (and for a longer "yes please plan it" variant).

### Risk 3: Shared segment evolves and re-introduces conflicts
**Impact:** If `work-patterns.md` is edited later (e.g., new "fix it autonomously" defaults added), the override table goes stale.
**Mitigation:** The override table cites the specific lines it reverses. A future edit to `work-patterns.md` should grep for "PM Overrides of Shared Defaults" and update both files together. Document this in the override-table preamble.

### Risk 4: Public template diverges from private overlay over time
**Impact:** Dev-machine PM (using the public template) behaves differently from production PM (using the private overlay).
**Mitigation:** `tests/unit/test_pm_persona_guards.py` asserts both files contain the announcement phrase and the override table. Both are checked in CI.

## Race Conditions

No race conditions identified — all the operations in this flow are synchronous from the agent's perspective. The drafter writes `expectations` after the agent finishes its turn; the lifecycle transition to `dormant` happens after the write; the human reply arrives later as a separate event. There is no concurrent-write or shared-state hazard.

## No-Gos (Out of Scope)

- Editing `config/personas/segments/work-patterns.md` to be persona-aware. (See Rabbit Holes.)
- Backfilling a GitHub issue for the existing PBA briefing LaunchAgents. (Separate cleanup decision.)
- Adding a structural stop-hook to block tool calls until `expectations` is written. (Defer to a follow-up issue if needed; see Open Questions.)
- Renaming the `plan`/`skip` reply tokens or adding new tokens.
- Schema changes to `AgentSession` (`expectations` and `context_summary` already exist).
- Changes to the bridge nudge loop or worker output routing.

## Update System

No update system changes required — this is a persona-text edit + a one-line warning in `agent/sdk_client.py`. The private overlay at `~/Desktop/Valor/personas/project-manager.md` is iCloud-synced and will reach all machines automatically once committed there. The public template is in the repo and propagates via normal `git pull`. The loader-warning code is loaded on session startup; no migration required for existing sessions.

## Agent Integration

No agent integration required — the PM persona file is loaded by `load_persona_prompt()` and injected into the PM session's system prompt at startup. No MCP server, bridge, or `.mcp.json` changes needed. The `expectations` and routing infrastructure are already wired and tested. The change is which text the PM reads, not which tools the agent has.

## Documentation

- [ ] Create `docs/features/pm-workflow-announcement.md` describing the announce-then-pause flow, the `plan`/`skip` reply contract, and how it interacts with semantic routing. Include the literal announcement phrase so anyone debugging session expectations can match it.
- [ ] Add an entry to `docs/features/README.md` under the PM/SDLC section linking to the new feature doc.
- [ ] Update `docs/features/session-steering.md` (if it references intake-bucket behavior) with a cross-reference to the new feature doc.
- [ ] Update `docs/features/pm-dev-session-architecture.md` with a brief note that PM bucket-#3 messages now require human confirmation before SDLC dispatch (and link to the new feature doc).

## Success Criteria

- [ ] `~/Desktop/Valor/personas/project-manager.md` bucket #3 contains the literal phrase "Unless you directly instruct me to skip our standard workflow"
- [ ] `config/personas/project-manager.md` (public template) is updated to include an Intake and Triage section + the same bucket #3 wording as the private overlay
- [ ] PM overlay (both files) contain a "What counts as a software change" enumeration explicitly naming LaunchAgents, cron, launchd, shell scripts, runtime config files, infrastructure, and new dependencies
- [ ] PM overlay (both files) contain a "PM Overrides of Shared Defaults" table that reverses ≥5 specific developer-flavored defaults from `work-patterns.md` and ends with "When the shared segment and this overlay disagree, this overlay wins"
- [ ] `agent/sdk_client.py` emits a `WARNING` log when the PM overlay is loaded and does NOT contain the substring "Unless you directly instruct me to skip"
- [ ] New unit test confirms a PM agent response ending with a `## Open Questions` section containing the workflow question populates `session.expectations` with the question text
- [ ] New integration test confirms a fresh unthreaded `plan` or `skip` reply in the same chat routes back to the dormant session at confidence ≥ 0.80
- [ ] No regression in `tests/unit/test_open_question_gate.py` or `tests/integration/test_unthreaded_routing.py`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

This is a small persona-text + thin Python plan. One builder, one validator, one documentarian.

### Team Members

- **Builder (persona-edits)**
  - Name: persona-builder
  - Role: Edit the private overlay, public template, and `agent/sdk_client.py` warning. Add new tests.
  - Agent Type: builder
  - Resume: true

- **Validator (persona-edits)**
  - Name: persona-validator
  - Role: Verify the announcement phrase exists in both files, the override table reverses ≥5 defaults, the warning fires under the missing-phrase condition, and all new and existing tests pass.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: pm-docs
  - Role: Write `docs/features/pm-workflow-announcement.md` and update the index + cross-references.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Edit the private overlay (`~/Desktop/Valor/personas/project-manager.md`)
- **Task ID**: build-private-overlay
- **Depends On**: none
- **Validates**: file contains "Unless you directly instruct me to skip"; "What counts as a software change" enumeration; "PM Overrides of Shared Defaults" table
- **Informed By**: Recon Summary in issue #1189; existing bucket #3 at line 96
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite bucket #3 to include the literal announcement phrase, the `plan`/`skip` token contract, the `## Open Questions` requirement, and the "no implementation in the same turn" rule. Expand the bucket description to include automation, scripts, runtime config, infrastructure, and new dependencies.
- Add "What counts as a software change (issue required)" section enumerating LaunchAgents, cron, launchd, systemd, shell/Python/Node scripts, `.env`, `projects.json`, `.mcp.json`, `settings.json`, plist files, Vercel/Render/SMTP/DNS/IAM, new dependencies, new files under `~/Library/LaunchAgents/`, `~/.local/bin/`, `/etc/`, `~/Library/LaunchDaemons/`. Plus a "no-issue tasks (handle directly)" sub-list for replying, status, GitHub issue management, sending messages, searching memory, running existing read-only tools.
- Add "PM Overrides of Shared Defaults" table with at least 5 rows reversing the developer-flavored defaults from `config/personas/segments/work-patterns.md` (code-changes-don't-need-check-ins → bucket #3 announcement required; implementation-detail-my-call → ask before code/config changes; fix-the-bug-yourself → file an issue first; reversible-decisions-just-do-it → not for software changes; YOLO mode for everything → YOLO mode does not extend to bypassing SDLC). Closing line: "When the shared segment and this overlay disagree, this overlay wins."

### 2. Edit the public template (`config/personas/project-manager.md`)
- **Task ID**: build-public-template
- **Depends On**: build-private-overlay
- **Validates**: file contains the same announcement phrase, enumeration, and override table
- **Informed By**: build-private-overlay (mirror the same content)
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Add an "Intake and Triage" section to the public template (currently absent) that mirrors the private overlay's bucket structure. The private overlay is the authority for production; the public template is the authority for dev machines per `_resolve_overlay_path` fallback.
- Add the same "What counts as a software change" enumeration and "PM Overrides of Shared Defaults" table as the private overlay.

### 3. Add the loader warning (`agent/sdk_client.py`)
- **Task ID**: build-loader-warning
- **Depends On**: build-public-template
- **Validates**: tests/unit/test_sdk_client_persona_warnings.py (new); ruff check passes
- **Informed By**: existing CRITIQUE-missing warning at line 919; existing dev-session deprecation warning at line 924
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Inside `load_persona_prompt`, after the existing two warnings (lines 919 and 924), add a third `if` block checking for the announcement phrase. Match the pattern of the existing warnings. Use `logger.warning` with the same message shape.
- Run `python -m ruff format agent/sdk_client.py && python -m ruff check agent/sdk_client.py`.

### 4. Write tests
- **Task ID**: build-tests
- **Depends On**: build-loader-warning
- **Validates**: pytest tests/unit/test_sdk_client_persona_warnings.py; pytest tests/unit/test_pm_persona_guards.py; pytest tests/unit/test_open_question_gate.py; pytest tests/integration/test_unthreaded_routing.py
- **Informed By**: existing test patterns in test_open_question_gate.py and test_pm_persona_guards.py
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a new test case to `tests/unit/test_pm_persona_guards.py`: assert the private overlay (and public template) contain the announcement phrase, the enumeration of software-change categories, and the override table with ≥5 rows ending with "this overlay wins".
- Add a new test case to `tests/unit/test_open_question_gate.py` (or equivalent): assert that a PM-shaped response containing the workflow announcement + `## Open Questions` populates `expectations` correctly.
- Add a new test case to `tests/integration/test_unthreaded_routing.py`: assert that a fresh `plan` reply and a fresh `skip` reply each route back to the dormant session at confidence ≥ 0.80 when the dormant session's `expectations` contains the workflow question.
- Create `tests/unit/test_sdk_client_persona_warnings.py` (or extend an existing similar file): assert that loading the PM persona from a temporary overlay file missing the announcement phrase emits a WARNING log via `caplog`.

### 5. Validate
- **Task ID**: validate-edits
- **Depends On**: build-tests
- **Assigned To**: persona-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_pm_persona_guards.py tests/unit/test_open_question_gate.py tests/unit/test_sdk_client_persona_warnings.py tests/integration/test_unthreaded_routing.py -v`.
- Run `python -m ruff check . && python -m ruff format --check .`.
- Verify the announcement phrase exists in both persona files via `grep`.
- Verify the loader warning fires by loading a PM persona overlay missing the phrase (use a tmp file) and asserting the warning is emitted.
- Report pass/fail.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-edits
- **Assigned To**: pm-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-workflow-announcement.md` describing the flow (announce → expectations write → dormant → human reply → semantic route → resume). Include the literal phrase. Cross-reference `bridge/session_router.py`, `bridge/message_drafter.py:1725`, and `agent/output_handler.py:380`.
- Add an entry to `docs/features/README.md` index table.
- Add a cross-reference from `docs/features/session-steering.md` and `docs/features/pm-dev-session-architecture.md` if appropriate.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: persona-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests: `pytest tests/ -x -q`.
- Run lint and format: `python -m ruff check . && python -m ruff format --check .`.
- Verify all success criteria are met.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_pm_persona_guards.py tests/unit/test_open_question_gate.py tests/unit/test_sdk_client_persona_warnings.py tests/integration/test_unthreaded_routing.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Private overlay has announcement phrase | `grep -c "Unless you directly instruct me to skip" ~/Desktop/Valor/personas/project-manager.md` | output > 0 |
| Public template has announcement phrase | `grep -c "Unless you directly instruct me to skip" config/personas/project-manager.md` | output > 0 |
| Loader has new warning check | `grep -c "Unless you directly instruct me to skip" agent/sdk_client.py` | output > 0 |
| Override table closing line present | `grep -c "this overlay wins" config/personas/project-manager.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
|          |        |         |              |                     |

---

## Open Questions

1. **Text-only or text+hook?** The issue explicitly raises this. Recommendation: ship text-only first (this plan), file a follow-up issue for a structural stop-hook only if a real incident shows text-only is insufficient. The lighter path has precedent (#1007/PR #1009) and the heavier path is non-trivial to maintain. Confirm direction before BUILD.

2. **Public template scope.** The public template `config/personas/project-manager.md` does NOT currently have an Intake and Triage section — it's scoped to Hard Rules only. This plan adds the Intake section (mirroring the private overlay) so dev-machine PMs behave consistently with production. Confirm this is the right call vs. limiting the change to the private overlay only. Recommendation: add it to the public template — the public template is the documented fallback per `_resolve_overlay_path`, and dev-machine PMs need consistent behavior or the test gate at `tests/unit/test_pm_persona_guards.py` won't catch overlay drift on dev machines.
