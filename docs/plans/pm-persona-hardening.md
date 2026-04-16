---
slug: pm-persona-hardening
tracking: https://github.com/tomcounsell/ai/issues/1007
status: In Progress
---

# PM Persona Hardening — Self-Monitoring and Pipeline Completion Guards

## Problem Summary

The PM session (`session_type="pm"`) lacks self-awareness about its own execution state, leading to three failure modes:

1. **No completion guard**: PM exits with open PRs still unmerged (see #1005)
2. **No child session timeout**: PM waits indefinitely for stuck children (see #1004)
3. **No pipeline stage assertion**: PM skips stages silently without validation

## Solution

Add three new sections to `config/personas/project-manager.md` that encode self-monitoring behaviors into the PM's system prompt. This is a prompt-level fix only — no Python infrastructure changes.

### Changes

- [ ] Add "Pre-Completion Checklist" section to PM persona — requires `gh pr list --head session/{slug}` check before exit, invokes `/do-merge` if open PRs exist, refuses to exit with open PRs unless a concrete blocker is stated
- [ ] Add "Child Session Monitoring" section to PM persona — instructs PM to check child status via `valor_session status` after dispatch, defines 5-minute timeout for pending children, fallback to running read-only stages directly, escalation for dev-permission stages
- [ ] Add "Exit Validation" section to PM persona — requires `sdlc_stage_query` on exit, validates all display stages (ISSUE, PLAN, CRITIQUE, BUILD, TEST, REVIEW, DOCS, MERGE) show completed, refuses to exit with incomplete stages unless skip justification provided
- [ ] Verify existing Rules 1-4 remain intact and are not weakened

## No-Gos

- No Python infrastructure changes in `worker/`, `agent/`, or `bridge/`
- No changes to `pipeline_graph.py` or `pipeline_state.py`
- No new CLI tools or MCP servers
- Timeouts are hardcoded in prompt text, not configurable via env vars (simplicity over flexibility)

## Update System

No update system changes required — this is purely a persona prompt file change that propagates via normal git pull.

## Agent Integration

No agent integration required — the PM persona file is already loaded by `load_persona_prompt()` and injected into the PM session's system prompt. No MCP server or bridge changes needed.

## Documentation

- [ ] The PM persona file itself serves as documentation for these behaviors
- [ ] No separate feature doc needed — the guards are prompt-level behavioral rules

## Test Impact

- [ ] `tests/unit/test_pm_persona_guards.py` — existing test file already defines 18 tests for the three guard sections. These tests are currently RED (failing) and will go GREEN once the persona is updated.

## Failure Path Test Strategy

The tests validate persona text content (section headings, key terms, command references). No runtime behavior testing is needed since these are prompt-level instructions.

## Rabbit Holes

- Do not attempt to build infrastructure enforcement of these guards (e.g., worker-level timeout enforcement) — that is out of scope
- Do not refactor existing Rules 1-4 — add new sections alongside them
- Do not add stage-skipping format parsing — free-text justification is sufficient

## Critique Results

N/A — fast-tracking due to production incidents (#1004, #1005).
