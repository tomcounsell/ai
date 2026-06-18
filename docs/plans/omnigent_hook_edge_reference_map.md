---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-06-18
tracking: https://github.com/tomcounsell/ai/issues/1732
last_comment_id:
revision_applied: true
---

# Omnigent claude_native_* Reference Map — Capture & Distribute

## Problem

Granite (this repo's interactive-TUI session runner) decides a Claude turn is *done* by screen-scraping the TUI — the "C5" idle heuristic in `pty_driver.py` (bottom-bar regex + prompt glyph + spinner-evidence regex + 2s byte-quiescence). A quiescence heuristic physically cannot distinguish "settled" from "paused mid-turn": a fully-settled TUI and a thinking-mid-turn TUI both paint nothing. This is the root of the #1724 saga (stalled `never_started` sessions, mid-run wedges).

[Omnigent](https://github.com/omnigent-ai/omnigent) — an open-source multi-harness agent runner — solved the *identical* problem in production: it **demoted the PTY to a liveness sensor** and reads turn completion from Claude Code's own `Stop`/`StopFailure` hook edges. Issue #1732 is a **reference map** of Omnigent's `claude_native_*` harness, every practice cited to file:line, so we can adopt what they proved and revisit those exact files when we hit a bug along the same path.

**Current behavior:**
The hook-edge architecture already lives in our backlog as three OPEN issues (#1688 architecture, #1719 completion floor, #1721 resume). But those issues do not yet capture the **five NEW Omnigent-proven practices** (edge-transport bridge file, durable hook cursor, subagent-hook filtering, verified-submit injection, compaction forwarding — practices 3/4/5/6/8 in the home map below), and the Omnigent file:line citations that make those practices auditable live only in issue #1732's body — they are not recorded in the home issues or in any durable doc. When a builder eventually executes #1688/#1719/#1721, the Omnigent reference trail will be lost in a closed/buried issue.

**Desired outcome:**
Every Omnigent practice from the #1732 reference map is **captured** in its home — folded into #1688/#1719/#1721 (issue body + the existing #1721 plan) as explicit, file:line-cited deltas, with the genuinely-NEW deltas additionally recorded in a durable repo reference doc so the citation trail survives issue closure. **No granite code is changed by this plan** — issue #1732 states verbatim "Refactor proposal only — do NOT implement from this issue directly." The deliverable is knowledge capture, not architecture.

## Freshness Check

**Baseline commit:** `3d0dfb53` (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-06-18T08:33:11Z (same day as planning)
**Disposition:** Minor drift (plus active-plan Overlap on #1721 — by design, not a blocker)

**File:line references re-verified (issue's own-side citations):**
- `agent/granite_container/pty_driver.py:36-55` — C5 heuristic docstring — **still holds**, present verbatim.
- `pty_driver.py` `QUIESCENCE_S = 2.0` — claimed `:128`, found at `:128` — **holds**.
- `pty_driver.py:382-384` — spawn args `--model`/`--permission-mode`/conditional `--session-id`, no `--resume` — **holds**; no `--settings` flag either (load-bearing for the subagent-filtering question, see Spike Results).
- `pty_driver.py:422-461` — fixed `SUBMIT_KEY_DELAY_S` then bare `\r` submit — **holds**.
- `agent/granite_container/container.py:553` (`on_turn()` callback) — **drifted**: the `on_turn` param is at `:563`, stored at `:583`, invoked at `:1085-1089` and `:1162-1169`. Claim holds (in-process callback, not a hook edge).
- `.claude/hooks/stop.py`, `agent/hooks/stop.py` — claimed "none wired to granite turn-end" — **confirmed absent**; both are normal Claude Code session-lifecycle hooks. `transcript_tailer.py:444` carries a "hook-driven Stop signal in followup #1688" forward-reference (future, not current).
- `docs/features/granite-pty-production.md:618-626` (no-resume limitation) — **drifted** to `:636-644` ("Known limitations (deep-dive audit, PR #1612)", item 1). Claim holds.

**Cited sibling issues/PRs re-checked:**
- #1688 — **still OPEN**. Labels: `bridge` only (NO `plan` label → **no plan document exists yet**).
- #1719 — **still OPEN**. No plan document.
- #1721 — **still OPEN**. Has a **Ready** plan: `docs/plans/granite_lossless_checkpoint_resume.md`.
- #1724 — **still OPEN** (the never-started saga this issue's problem statement references).

**Commits on main since issue was filed (touching referenced files):** None material — issue filed and planned same day; granite files last touched `Jun 18` for unrelated work (transcript_tailer, container) but no commit changed the C5/submit/resume claims this issue rests on.

**Active plans in `docs/plans/` overlapping this area:**
- `granite_lossless_checkpoint_resume.md` (status: Ready, tracking #1721) — **deliberate overlap**. This plan does NOT compete with it; it *adds* two cited deltas to it (fork-on-resume guard; dead-vs-stalled disambiguation rationale). Coordination, not collision.

**Notes:** Omnigent file:line citations (their side) are pinned to *their* HEAD at filing (2026-06-18) and are not re-verifiable from this repo — they are preserved verbatim as a bug-trail, with an explicit "re-verify against omnigent HEAD when revisiting" caveat in the reference doc. Their `claude_native_*` modules carry a `Phase A → Phase B` migration comment and are actively evolving.

## Prior Art

- **#1688 (OPEN)** "Hook-driven turn returns for granite PTY shuttle" — the core thesis (Stop hooks for turn-end, `AskUserQuestion`/`PermissionRequest` for needs-human, crash-path watchdog/resume). Prior art it cites is the **superwhisper-claude-code** plugin. This plan feeds #1688 a *second* reference implementation (Omnigent) plus the five NEW practices #1688 omits (durable cursor, subagent filtering, verified-submit, compaction, edge-transport — practices 3/4/5/6/8, captured in the durable reference doc since #1688 has no plan yet).
- **#1719 (OPEN)** "Stop-hook completion floor" — deliver the last assistant message when a turn ends with nothing routed. This plan confirms its floor against Omnigent's `_HOOK_EVENT_TO_STATUS` mapping + sticky-`failed` logic.
- **#1721 (OPEN) + `granite_lossless_checkpoint_resume.md` (Ready)** — persist resume handles + loop cursor; lossless `--resume`. This plan adds Omnigent's fork-on-resume guard and dead-vs-stalled rationale to it.
- **`granite_pty_production_cutover.md`, `granite-tui-pty-spike.md` (completed)** — established the PTY-shuttle architecture this reference map proposes to evolve. Confirm the C5 heuristic they shipped is the one being demoted.
- **#1724 (OPEN)** "Stalled never_started sessions" — the operational pain the hook-edge model is meant to cure; the determinism guardrail (never-started → escalate-only) must be honored by any future hook-edge work.

No prior *failed* fix to the screen-scrape problem exists — the C5 heuristic has been iteratively patched (PR #1612 startup_unresolved fixes) but never replaced, which is precisely why the hook-edge proposal exists. No "Why Previous Fixes Failed" section needed.

## Research

No relevant external findings — this is internal knowledge-capture work. Omnigent is a reference implementation already cited exhaustively (file:line) in the issue body; re-fetching its source is out of scope (its citations are preserved as a verbatim bug-trail, not re-derived). No external libraries, APIs, or ecosystem patterns are introduced by capture-and-distribute editing of issues and docs.

## Spike Results

### spike-1: Do subagent Stop hooks land in the SAME hooks file as the parent for granite?
- **Assumption**: "Because granite spawns claude with no `--settings` isolation, a Dev-persona subagent's `Stop`/`StopFailure` hook would land in the same hooks stream as the parent — making Omnigent's subagent-hook filtering (claim #5) mandatory for us, not optional."
- **Method**: code-read (Explore agent, read-only)
- **Finding**: **Confirmed.** `pty_driver.py:382-384` spawns `claude` with only `--model`, `--permission-mode bypassPermissions`, and a conditional `--session-id` — **no `--settings` flag**. Identity is passed purely via inherited env vars (`AGENT_SESSION_ID`, `CLAUDE_CODE_TASK_LIST_ID`, `SESSION_TYPE`, `VALOR_PARENT_SESSION_ID`; `bridge_adapter.py:332-333`). With no per-process settings isolation, child Stop hooks inherit the parent's hook configuration and would write to the same destination. The Dev persona fans out to Sonnet subagents (builder/code-reviewer) routinely.
- **Confidence**: high
- **Impact on plan**: Resolves issue #1732's third open question empirically. The subagent-hook-filtering requirement must be recorded as a **first-class, load-bearing** acceptance criterion on #1688 — a naive Stop-hook wiring WILL end the Dev turn early. This is captured, not deferred.

### spike-2: Is there any existing Stop-hook → granite turn-end wiring to avoid double-capturing?
- **Assumption**: "No Stop hook is currently wired to granite turn completion (so the capture work records a genuine gap, not an existing mechanism)."
- **Method**: code-read
- **Finding**: **Confirmed absent.** `.claude/hooks/stop.py` and `agent/hooks/stop.py` are both normal Claude Code session-lifecycle hooks (metadata save, transcript backup, delivery-review gate); neither references granite/PTY/turn-end. Granite uses an in-process `on_turn()` callback (`container.py:563/583`, invoked `:1085-1089`, `:1162-1169`) for progress signaling. `transcript_tailer.py:444` already carries a `# ... followup #1688` forward-reference marking the Stop-signal as future work.
- **Confidence**: high
- **Impact on plan**: The reference doc records the current `on_turn()` callback as the structure the hook edge would replace, and the existing `transcript_tailer.py:444` marker as the natural seam — no conflicting mechanism to reconcile.

## Architectural Impact

- **New dependencies**: None. No code, no imports, no services.
- **Interface changes**: None — no source modules are edited.
- **Coupling**: Unchanged. (The *future* hook-edge work this captures would decrease coupling between completion-detection and the PTY; that is #1688's concern, not this plan's.)
- **Data ownership**: Unchanged.
- **Reversibility**: Trivially reversible — the deliverable is a markdown reference doc plus issue-body edits; revert by `git revert` and re-editing issue bodies.

## Appetite

**Size:** Small

**Team:** Solo dev, documentarian

**Interactions:**
- PM check-ins: 1-2 (confirm the capture-vs-implement scope decision, confirm which deltas home to which issue)
- Review rounds: 1 (verify each Omnigent citation is preserved and each NEW delta is recorded in exactly one home)

This is a documentation/knowledge-capture chore. The bottleneck is *accuracy of attribution* (every practice → correct home issue, file:line preserved), not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies. All inputs (issue #1732 body, the three sibling issues, the existing #1721 plan, the granite source files) are already present in the repo and on GitHub.

## Solution

### Scope Decision (the decision #1732 demands up front)

Issue #1732's "Downstream context" requires the planner to choose, up front, between:
- **(a)** a thin standalone plan for ONLY the NEW deltas, or
- **(b)** distributing the deltas into the existing issues' plans.

**Decision: a hybrid anchored on (a) — a thin standalone CAPTURE plan that distributes deltas into the home issues AND records the NEW deltas in one durable reference doc.**

**Justification:** The issue is explicitly a reference appendix ("Refactor proposal only — do NOT implement from this issue directly") and its acceptance criteria are about *capturing* each practice as a delta on #1688/#1719/#1721 or as a NEW line item — a knowledge-capture deliverable, not a code deliverable. Pure option (b) ("distribute and vanish") is non-viable for two reasons: (1) the repo SDLC requires a plan document with a tracking issue, so *some* standalone artifact must exist; (2) #1688 and #1719 have **no plan documents yet** (only #1721 does), so there is no plan to distribute deltas *into* for two of the three homes — distribution into #1688/#1719 can only target their **issue bodies**, and the NEW citation trail must live somewhere durable that survives issue closure. The standalone reference doc is that durable home; the issue-body edits are the distribution. This honors "do NOT implement" while producing a genuinely completable unit of work.

### Key Elements

- **Reference doc (`docs/features/omnigent-hook-edge-reference.md`)**: A durable, repo-resident capture of the Omnigent `claude_native_*` reference map — the 9 practices, each with Omnigent file:line, our-side equivalent (re-verified), and home tag (#1688 / #1719 / #1721 / NEW). Survives issue closure as the bug-trail. Carries the "re-verify against omnigent HEAD" caveat.
- **#1688 issue-body delta**: Append a "Captured deltas from #1732 (Omnigent reference)" section recording the **three practices that home to #1688** — practice 1 (Stop/StopFailure as the authoritative turn-end edge), practice 2 (PTY reduced to two jobs), and practice 7 (completion decoupled from injection, with `_inject_lock`) — and pointering to the **five NEW practices** captured in the reference doc that #1688's build must consume (edge-transport NDJSON, durable hook cursor, subagent-hook filtering, verified-submit injection, compaction forwarding). The **subagent-hook-filtering** requirement is called out as a first-class, load-bearing acceptance criterion on #1688 (spike-1 evidence) even though it is itself a NEW practice, because a naive Stop-hook wiring in #1688's build would end the Dev turn early without it.
- **#1719 issue-body delta**: Append the sticky-`failed` floor delta (the completion floor must respect a sticky failure so a `StopFailure` isn't overwritten by trailing PTY idle).
- **#1721 delta (issue body + existing Ready plan)**: Add the fork-on-resume guard (`seen_claude_session_ids` / `SessionStart source=resume`) and the dead-vs-stalled disambiguation rationale to both the #1721 issue body AND `docs/plans/granite_lossless_checkpoint_resume.md` — reconciled against `reflections/crash_recovery.py`'s determinism guardrail so the two issues don't propose conflicting resume triggers.
- **#1732 close-out comment**: A comment on #1732 mapping each of its four acceptance-criteria checkboxes to the artifact that satisfies it.

### Flow

Issue #1732 (reference appendix) → extract 9 practices → write durable reference doc → distribute each practice's delta into its home (issue body for #1688/#1719, issue body + Ready plan for #1721) → reconcile #1721 resume triggers against crash_recovery determinism guardrail → comment on #1732 mapping each acceptance criterion to its capture artifact → #1732 ready to close once the home issues carry the deltas.

### Technical Approach

- **No source code is touched.** Touched artifacts: one new markdown doc, three GitHub issue bodies (`gh issue edit`), one existing plan doc (`granite_lossless_checkpoint_resume.md`), and one GitHub comment (`gh issue comment`).
- **Preserve every Omnigent file:line verbatim** in the reference doc — it is the load-bearing value (a bug-trail back to a production implementation). Annotate each with "pinned to omnigent HEAD 2026-06-18; re-verify on revisit."
- **Re-verify our-side citations** as captured in the Freshness Check (line drifts noted: `on_turn` at container `:563/583`; no-resume limitation at doc `:636-644`).
- **Home-tagging discipline**: each of the 9 practices lands in exactly ONE home (no double-capture, no orphan). The home map is re-derived verbatim from issue #1732's "Omnigent reference map" row tags:

  | # | Practice | Home (per #1732 row tag) |
  |---|----------|--------------------------|
  | 1 | Stop/StopFailure as the authoritative turn-end edge | **#1688** |
  | 2 | PTY reduced to two jobs (inject + running/idle badge) | **#1688** |
  | 3 | Hook writes the edge to a bridge file (append-only NDJSON) | **NEW** |
  | 4 | Durable, idempotent hook cursor `(event_cursor, byte_offset, fingerprint)` | **NEW** (companion to #1721's persistence) |
  | 5 | Subagent-hook filtering (child Stop must not end parent turn) | **NEW** |
  | 6 | Verified-submit injection (poll-until-committed, re-send) | **NEW** |
  | 7 | Completion decoupled from injection (async edge, `_inject_lock`) | **#1688** |
  | 8 | Compaction boundaries forwarded, not mistaken for completion | **NEW** |
  | 9 | Sticky-`failed` against trailing PTY idle | **#1719** |

  Plus the crash/`/resume` section's **fork-on-resume guard** (`seen_claude_session_ids` / `SessionStart source=resume`) and the **dead-vs-stalled disambiguation rationale** → **#1721**.

  Summary: **#1688 → {1, 2, 7}** (three practices); **#1719 → {9}** (one); **NEW → {3, 4, 5, 6, 8}** (five — the exact set #1732 calls "5 practices #1688 omits": durable cursor, subagent filtering, verified-submit, compaction, edge-transport); **#1721 → fork-on-resume + resume deltas**. The five NEW practices are recorded in the durable reference doc as their home (and pointered from the relevant issue bodies), since #1688/#1719 have no plan documents and the NEW citation trail must survive issue closure.
- **Reconciliation check (#1721 ↔ crash_recovery)**: confirm the dead-vs-stalled contract (dead = `pexpect.EOF`/`!isalive()` → resume; stalled-but-alive + no Stop → still-running, bounded by liveness watchdog; never-started → `NON_RESUMABLE_DETERMINISTIC` escalate-only per `agent/crash_signature.py:207-228`, `reflections/crash_recovery.py:280-293`) does not contradict the #1721 plan's resume triggers. Read both, note any conflict in the #1721 plan's risks section.

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers are in scope — this plan edits markdown and GitHub issue bodies; it introduces no `try/except` blocks and no runtime code paths.

### Empty/Invalid Input Handling
- No functions are written or modified. The only "input" is the human-readable issue/doc content; correctness is verified by review (every practice has a home, every Omnigent citation preserved), not by runtime assertions. State: "No code functions in scope — capture-only chore."

### Error State Rendering
- No user-visible runtime output. The "output" is documentation and issue text reviewed for completeness against the four acceptance criteria.

## Test Impact

No existing tests affected — this is a documentation/knowledge-capture chore that edits a new markdown reference doc, three GitHub issue bodies, one existing plan document, and adds one GitHub comment. It changes no source code, no interfaces, and no runtime behavior, so no unit, integration, or e2e test exercises anything this plan touches. (A `docs/features/README.md` index entry is added; the docs-index presence is verified by the Verification table's grep check, not by pytest.)

## Rabbit Holes

- **Actually implementing the hook edge.** The single biggest trap. Issue #1732 says "do NOT implement from this issue directly." Wiring a Stop hook, building the NDJSON bridge file, or touching `pty_driver.py`/`container.py` is OUT — that is #1688/#1719/#1721 build work. This plan captures and distributes; it does not build.
- **Re-deriving Omnigent's citations from their repo.** Their `claude_native_*` modules are actively evolving (Phase A→B). Re-fetching and re-validating their line numbers is a time sink with no payoff — preserve the filing-time citations verbatim with a re-verify caveat and move on.
- **Rewriting the #1688/#1719/#1721 plans wholesale.** The task is to *add* cited deltas, not to re-plan the architecture. Only #1721 has a plan to touch, and only to append two deltas + one reconciliation note.
- **Auditing whether the C5 heuristic is "really" unfixable.** Tempting forensic rabbit hole; the issue already establishes the physical argument (settled and thinking both paint nothing). Don't re-litigate it.
- **Designing the compaction-awareness mechanism.** Capturing "compaction boundaries must be forwarded, not mistaken for completion" as a NEW delta is in scope; designing how is #1688 build work.

## Risks

### Risk 1: A practice is captured in the wrong home, or in two homes
**Impact:** A future #1688/#1719/#1721 builder either misses a load-bearing requirement (e.g., subagent filtering) or implements a delta twice.
**Mitigation:** The reference doc's home-tag column is the single source of truth (corrected map: #1688 → {1,2,7}; #1719 → {9}; NEW → {3,4,5,6,8}; #1721 → fork-on-resume); the close-out comment on #1732 maps each acceptance criterion to exactly one artifact. Review round verifies each of the 9 practices carries exactly one home tag — #1688/#1719-homed practices land in those issue bodies, NEW practices land in the reference doc (their durable home), and no practice is orphaned (practice 8/compaction was the orphan the critique caught).

### Risk 2: The #1721 delta conflicts with the already-Ready resume plan
**Impact:** Two issues propose contradictory resume triggers (e.g., resume-on-stalled vs. escalate-on-never-started), causing build-time confusion.
**Mitigation:** The reconciliation step explicitly reads `granite_lossless_checkpoint_resume.md` against `reflections/crash_recovery.py`'s determinism guardrail and records any conflict in the #1721 plan's risks section rather than silently appending. If a genuine conflict surfaces, raise it as an Open Question rather than papering over it.

### Risk 3: Omnigent's citations rot (their modules evolve)
**Impact:** A future reader follows a file:line that no longer matches.
**Mitigation:** Every Omnigent citation is annotated "pinned to omnigent HEAD 2026-06-18; re-verify on revisit." Our-side citations are re-verified at plan time (Freshness Check) and recorded with current line numbers.

## Race Conditions

No race conditions identified — this plan performs sequential, single-threaded documentation edits and GitHub API writes (`gh issue edit`, `gh issue comment`). There is no concurrent access, no shared mutable runtime state, and no async data flow. The only ordering constraint (write the reference doc before linking it from issue bodies) is a trivial sequential dependency, not a race.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1688] Implementing the Stop/StopFailure hook-edge wiring, the NDJSON edge-transport file, the durable hook cursor, subagent-hook filtering code, verified-submit injection, and compaction forwarding — all of it is #1688/#1719 build work. This plan only *records* these as cited deltas.
- [SEPARATE-SLUG #1721] Implementing `--resume <uuid>` wiring, the fork-on-resume guard code, and lossless checkpoint resume — that is #1721's already-Ready plan (`granite_lossless_checkpoint_resume.md`). This plan only *appends* the fork-on-resume guard delta and the dead-vs-stalled rationale to it.
- [SEPARATE-SLUG #1724] Fixing the never-started recovery saga — referenced as the operational motivation; not touched here.

## Update System

No update system changes required — this feature is purely internal documentation. It adds a markdown reference doc under `docs/features/`, which is part of the repo and propagates via normal `git pull` in `/update`. No new dependencies, config files, services, or migration steps. The `/update` script and skill are unaffected.

## Agent Integration

No agent integration required — this is a documentation/knowledge-capture chore. It adds no CLI entry point to `pyproject.toml [project.scripts]`, exposes no new MCP tool, and requires no bridge import. The agent already reads `docs/features/*.md` as part of its knowledge base when relevant; the new reference doc is discoverable through that existing path with zero wiring. No integration test is needed because there is no invocable capability to test.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/omnigent-hook-edge-reference.md` — the durable Omnigent `claude_native_*` reference map (9 practices, each with Omnigent file:line + re-verified our-side equivalent + home tag), with the "re-verify against omnigent HEAD" caveat. **This doc IS the primary deliverable**, not an afterthought.
- [ ] Add entry to `docs/features/README.md` index table linking the new reference doc.

### External Documentation Site
- Not applicable — this repo's `docs/features/` is plain markdown, no Sphinx/MkDocs build step for feature docs.

### Inline Documentation
- No code is touched — no docstrings or inline comments change.

## Success Criteria

- [ ] `docs/features/omnigent-hook-edge-reference.md` exists, capturing all 9 Omnigent practices with their Omnigent **file:line** preserved verbatim (at least one `claude_native_*.py:NNN` citation per practice — verified by the citation-anchored grep in the Verification table, not a bare module-name count) and the re-verified our-side equivalent + home tag for each. Home map: #1688 → {1,2,7}; #1719 → {9}; NEW → {3,4,5,6,8}; #1721 → fork-on-resume + resume deltas.
- [ ] `docs/features/README.md` has an index entry for the new reference doc.
- [ ] #1688 issue body carries the captured deltas, with **subagent-hook filtering recorded as a first-class, load-bearing acceptance criterion** (spike-1 evidence cited: granite spawns claude with no `--settings` isolation, so child Stop hooks share the parent stream).
- [ ] #1719 issue body carries the sticky-`failed` completion-floor delta.
- [ ] #1721 issue body AND `docs/plans/granite_lossless_checkpoint_resume.md` carry the fork-on-resume guard delta and the dead-vs-stalled disambiguation rationale, reconciled against `reflections/crash_recovery.py`'s determinism guardrail (no conflicting resume triggers, or any conflict surfaced as an Open Question).
- [ ] A close-out comment on #1732 maps each of its four acceptance-criteria checkboxes to the artifact that satisfies it.
- [ ] Every practice homes to exactly one issue (no double-capture); the reference doc's home-tag column is the source of truth.
- [ ] Documentation updated (`/do-docs`)
- [ ] No source code changed (`git diff --stat` shows only `docs/` and the plan file; no `.py` changes).

## Team Orchestration

When this plan is executed, the lead agent orchestrates the capture work. The lead does not build code (there is none); it coordinates documentation capture and issue-body distribution.

### Team Members

- **Documentarian (reference-doc)**
  - Name: `omnigent-ref-documentarian`
  - Role: Author `docs/features/omnigent-hook-edge-reference.md` from issue #1732's reference map; add the `docs/features/README.md` index entry; preserve every Omnigent file:line verbatim; re-verify our-side citations.
  - Agent Type: documentarian
  - Resume: true

- **Builder (issue-distribution)**
  - Name: `delta-distributor`
  - Role: Edit #1688/#1719/#1721 issue bodies (`gh issue edit`) and `granite_lossless_checkpoint_resume.md` to append the home-tagged deltas; reconcile #1721 resume triggers against `reflections/crash_recovery.py`; post the #1732 close-out comment.
  - Agent Type: builder
  - Resume: true

- **Validator (capture-completeness)**
  - Name: `capture-validator`
  - Role: Verify all 9 practices appear in exactly one home, every Omnigent citation is preserved, the four #1732 acceptance criteria each map to an artifact, and `git diff` shows zero `.py` changes.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(Standard roster — see template. This chore uses documentarian + builder + validator.)

## Step by Step Tasks

### 1. Author the durable reference doc
- **Task ID**: build-reference-doc
- **Depends On**: none
- **Validates**: `docs/features/omnigent-hook-edge-reference.md` exists; `docs/features/README.md` index entry present
- **Informed By**: spike-1 (subagent hooks share parent stream — no `--settings` isolation), spike-2 (no existing Stop-hook→granite wiring; `on_turn()` callback at container `:563/583`)
- **Assigned To**: omnigent-ref-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Extract all 9 practices from issue #1732's "Omnigent reference map" table.
- For each: preserve the Omnigent file:line verbatim — keep the literal `claude_native_*.py:NNN` form (the citation-anchored Verification grep requires ≥1 per practice; a module name without `:line` does not count), annotate "pinned to omnigent HEAD 2026-06-18; re-verify on revisit", record the re-verified our-side equivalent with current line numbers (use Freshness Check drift corrections), and assign the home tag per the corrected map: #1688 → {1,2,7}; #1719 → {9}; NEW → {3,4,5,6,8}; #1721 → fork-on-resume + resume deltas.
- Add the "re-verify against omnigent HEAD" caveat and the dead-vs-stalled / determinism-guardrail rationale from #1732's crash section.
- Add the index entry to `docs/features/README.md`.

### 2. Distribute deltas into home issues + the #1721 plan
- **Task ID**: build-distribute-deltas
- **Depends On**: build-reference-doc
- **Validates**: #1688/#1719/#1721 issue bodies updated; `granite_lossless_checkpoint_resume.md` updated; #1732 close-out comment posted
- **Assigned To**: delta-distributor
- **Agent Type**: builder
- **Parallel**: false
- Append a "Captured from #1732 (Omnigent reference)" section to #1688's body — include the three #1688-homed practices (1: Stop/StopFailure turn-end edge; 2: PTY reduced to two jobs; 7: completion decoupled from injection) and pointer to the five NEW practices in the reference doc, with subagent-hook filtering recorded as a first-class acceptance criterion citing spike-1.
- Append the sticky-`failed` floor delta to #1719's body.
- Append the fork-on-resume guard + dead-vs-stalled rationale to #1721's body AND to `granite_lossless_checkpoint_resume.md`; read `reflections/crash_recovery.py` (determinism guardrail at `:280-293`, `agent/crash_signature.py:207-228`) and record reconciliation (or surface a conflict as an Open Question).
- Post a close-out comment on #1732 mapping each of its four acceptance criteria to its satisfying artifact.

### 3. Validate capture completeness
- **Task ID**: validate-capture
- **Depends On**: build-distribute-deltas
- **Assigned To**: capture-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify each of the 9 practices appears in exactly one home per the corrected map (#1688 → {1,2,7}; #1719 → {9}; NEW → {3,4,5,6,8}; #1721 → fork-on-resume) — no double-capture, no orphan (explicitly confirm practice 8/compaction is present).
- Verify every Omnigent file:line from #1732 is preserved in the reference doc — run the citation-anchored grep (`claude_native_*.py:NNN`, ≥9 hits), not a bare module-name count.
- Verify the four #1732 acceptance criteria each map to a concrete artifact.
- Run `git diff --stat` and confirm only `docs/` + the plan file changed — zero `.py` changes.
- Report pass/fail.

### 4. Documentation index + final validation
- **Task ID**: validate-all
- **Depends On**: validate-capture
- **Assigned To**: capture-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm `docs/features/README.md` index entry resolves to the new doc.
- Confirm all success criteria are met.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Reference doc exists | `test -f docs/features/omnigent-hook-edge-reference.md` | exit code 0 |
| Doc indexed | `grep -q 'omnigent-hook-edge-reference' docs/features/README.md` | exit code 0 |
| Omnigent **file:line citations** preserved (anchors on `:` + line digits, not bare module names) | `grep -cE 'claude_native_[a-z_]+\.py:[0-9]+' docs/features/omnigent-hook-edge-reference.md` | output ≥ 9 (one citation per practice minimum; a doc that dropped its file:line trail fails this even if module names survive) |
| All 9 practices present (home-tag column populated) | `grep -cE '\*\*(#1688\|#1719\|#1721\|NEW)\*\*' docs/features/omnigent-hook-edge-reference.md` | output ≥ 9 (every practice row carries exactly one home tag) |
| Subagent-filtering captured | `grep -iq 'subagent' docs/features/omnigent-hook-edge-reference.md` | exit code 0 |
| No source code changed | `git diff --name-only main -- '*.py' \| wc -l` | output contains 0 |
| Lint clean (docs-only, trivially) | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | home-tag map | Home map contradicted #1732's row tags, orphaned practice 8 (compaction), and mis-homed practices 3/5/6 to #1688 (issue tags them NEW). | Re-derived the full 9-row map verbatim from the issue and added it as a table in Solution → Key Elements / Technical Approach. | Corrected map: **#1688 → {1,2,7}; #1719 → {9}; NEW → {3,4,5,6,8}; #1721 → fork-on-resume + resume deltas.** Practice 8 (compaction) restored. Propagated to all four stale call sites (lines ~20, ~55, ~122, ~268) + Success Criteria + Risk 1 + validator task. |
| CONCERN | delta-count | Five-vs-six contradiction for #1688: prose said "five," enumeration listed six, source homes three to #1688. | Reconciled to the single corrected number. | #1688 homes **three** practices (1,2,7). The "five" everywhere now consistently means the **five NEW practices** (3,4,5,6,8) that #1732 says #1688 omits — captured in the reference doc, pointered from #1688's body. No standalone "six." |
| CONCERN | weak verification | Only check was `grep -c 'claude_native_' > 5`, which passes even if every file:line citation is dropped. | Added a citation-anchored Verification check requiring `claude_native_*.py:NNN` (module + `:` + line digits), ≥9 hits, plus a home-tag-count check and a per-practice citation requirement in the build task + success criteria. | The load-bearing value (file:line bug-trail) is now the gate, not bare module names. |

---

## Resolved Decisions (post-critique)

These were Open Questions in the draft; the revision pass settles them so the plan is finalized:

1. **Scope.** Option (a)-hybrid stands: a thin standalone *capture* plan that distributes the #1688/#1719-homed practices into those issue bodies, the #1721 deltas into the #1721 issue body + its Ready plan, and records the five NEW practices (3,4,5,6,8) in the durable reference doc (their home, since #1688/#1719 have no plan docs). Honors "do NOT implement directly."
2. **#1721 plan edit.** Editing the Ready `granite_lossless_checkpoint_resume.md` to *append* the fork-on-resume guard + dead-vs-stalled rationale (plus a reconciliation note in its Risks) is in scope — it is additive, not a re-plan.
3. **#1732 close-out.** #1732 is closed by this plan's implementation PR (`Closes #1732`) once the deltas are distributed and the reference doc lands — it is a reference appendix fully consumed by capture; the home issues (#1688/#1719/#1721) carry the live deltas thereafter.
