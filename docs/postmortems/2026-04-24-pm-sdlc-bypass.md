# Postmortem: PM Agent Bypassed SDLC Pipeline

**Date**: 2026-04-24  
**Severity**: Medium — incorrect behavior, no data loss, caught and corrected same day  
**Project**: PsyOPTIMAL  

---

## What Happened

Tom posted a feature request in the PM: PsyOptimal Telegram group:

> "In the Staff Tools Teams page, when a user searches for a team we need to also match by Teams parent team name. So for example, if a team name is 'Accounting' and the parent team is 'University' then a search for 'uni' would match."

The PM agent classified this as a coding task, immediately implemented the code change in `apps/staff/views/team_manager.py`, and offered to run tests and commit. Tom had to intervene:

> "Run it through SDLC."

## Root Cause

Two problems in `~/Desktop/Valor/personas/project-manager.md` (the private PM persona overlay):

**1. Triage item 3 was ambiguous:**
```
3. Coding task — write a precise brief, dispatch a dev-session
```
This was interpreted as "implement it yourself, then optionally dispatch" rather than "route to SDLC pipeline."

**2. An escape hatch normalized bypassing the pipeline:**
```
For trivial or docs-only work, use judgment on whether the full pipeline is warranted.
```
The word "trivial" gave the agent permission to shortcut. A parent-team name search is not architecturally complex — it fit the "trivial" label, triggering the bypass.

## Fix

Updated `~/Desktop/Valor/personas/project-manager.md`:

**Triage item 3** now reads:
> Coding task / feature request / bug report / software update — route through SDLC: create a GitHub issue if none exists, then drive the pipeline (ISSUE → PLAN → CRITIQUE → BUILD → …). **Never implement code directly, even for small or "trivial" changes.**

**Escape hatch** replaced with:
> The full pipeline is always warranted for software changes. Docs-only work (no code, no PR) may skip BUILD/TEST/REVIEW, but must still have an issue and a DOCS stage.

## Docs Updated

- `docs/features/pm-dev-session-architecture.md` — removed "PM decides whether trivial work warrants the full pipeline" from the Why Stage-by-Stage section
- `docs/features/pm-routing-collaboration.md` — updated collaboration mode fallback instruction to say "route through SDLC pipeline" instead of "spawn a dev-session"

## Lessons

1. **"Trivial" is a dangerous label.** Any qualifier that allows bypassing a quality gate will be used to bypass that gate. The PM persona should have no notion of trivial software changes — only the SDLC pipeline.

2. **Triage instructions need to be unambiguous about routing, not just classification.** "Coding task — dispatch a dev-session" doesn't convey that the dev-session must be part of a full SDLC pipeline rather than a one-shot implementation.

3. **The collaboration mode escape hatch had a similar hole.** The fallback said "if code changes, spawn a dev-session" — not "route through SDLC." Both the triage and the collaboration fallback now explicitly name SDLC as the destination.
