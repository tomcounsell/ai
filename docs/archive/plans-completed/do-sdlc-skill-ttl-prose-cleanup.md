---
status: docs_complete
type: bug
appetite: Small
tracking: https://github.com/tomcounsell/ai/issues/2466
revision_applied: true
revision_applied_at: 2026-07-30T03:28:48Z
---

# do-sdlc SKILL.md: strip stale TTL number + attribute owned-run_ids set

## Problem

Two prose gaps remain in `.claude/skills-global/do-sdlc/SKILL.md` after #2452 (PR #2463, merged `20b706983`):

1. **Stale TTL number (line 103).** The "Recovery after run_id loss" paragraph still says the orphaned lock is *"bounded by the ≤300s lock TTL, since nothing renews the orphaned run's lock"*. The real lease is `ISSUE_LOCK_TTL_SECONDS = 1800` (`models/session_lifecycle.py:848`), unchanged since #1954. Because the skill body hardlink-syncs to `~/.claude/skills/` on every machine, this line actively misleads a live supervisor into planning a ~5-minute wait when the real lease is ~30 minutes. This is the higher-severity instance of the stale-300s class (the three source docstrings carrying the same number are owned by the #2446 lease/TTL lane, out of scope here).

2. **Unattributed owned-run_ids set (lines 95-101).** The decisive refusal discriminator (`owner_run_id ∈ {run_ids this run has held}`) is presented as a given, with nothing pointing a reader at the work that records that set.

**Current behavior:** line 103 names a wrong, soon-to-re-stale `≤300s`; the owned-run_ids set has no cross-reference to its owning work.

**Desired outcome:** line 103 is duration-free and defers timing to the #2446 cross-reference (consistent with the already-shipped line 90); the owned-run_ids discriminator carries a bare #2446/#2451 attribution cross-reference (no mechanism description).

## Freshness Check

**Disposition: Unchanged.** Baseline commit `20b706983` (merged head of #2463). Verified in-session against merged `main`: line 103 carries the `≤300s` parenthetical; lines 89-91 carry the timing-free table (line 90 is the phrasing model); lines 95-101 carry the discriminator with no cross-reference. `git show 20b706983^` confirmed the `≤300s` text predates #2452. The live TTL value `1800` at `models/session_lifecycle.py:848` was independently verified. No drift.

## Solution

Two one-line prose edits to `.claude/skills-global/do-sdlc/SKILL.md`, on branch `session/do-sdlc-skill-ttl-prose-cleanup`:

1. **Line 103** — remove the `"(bounded by the ≤300s lock TTL, since nothing renews the orphaned run's lock)"` parenthetical. Replace with duration-free prose that defers timing to the #2446 cross-reference, matching line 90's established phrasing ("the lock frees within its TTL … duration and renewal semantics are #2446's").

2. **Lines 95-101** — append a bare parenthetical cross-reference to the owned-run_ids discriminator: `(see #2446, #2451)`. Pointer only, no verb phrase and no mechanism description, so the duplicate-build failure class #2451 exists to describe is not re-created here.

**Literal line-103 replacement** (drafted here so the PR reviewer can confirm it is not a verbatim echo of line 90). Current parenthetical:

> While the old lock is live it returns `ISSUE_LOCKED` (bounded by the ≤300s lock TTL, since nothing renews the orphaned run's lock); after the TTL lapses a fresh contest mints a new run_id.

becomes:

> While the old lock is live it returns `ISSUE_LOCKED`; the lock frees within its TTL and a fresh contest then mints a new run_id (duration and renewal semantics are #2446's — do not restate a number here).

This differs from line 90 (which is a table cell describing the orphaned-lock refusal row); line 103 is the recovery narrative for the `--reuse-run-id` path, so the two are not duplicates.

No source-code, tool-contract, or `sdlc_router.py` changes.

## No-Gos

- Do NOT substitute `1800` for `300` — any number re-goes-stale the moment #2446 lands. Strip the number entirely.
- Do NOT touch the three source docstrings (`tools/sdlc_session_ensure.py:44`, `:180`, `tools/_sdlc_utils.py:400`) — Lane 1 (#2446) owns those in its DOCS stage.
- Do NOT describe how the owned-run_ids set is recorded — bare cross-reference only.
- Do NOT edit any file other than `.claude/skills-global/do-sdlc/SKILL.md`.

## Update System

No update system changes required — `/update` already hardlink-syncs `.claude/skills-global/` to every machine; no new dependency, config, or migration is introduced.

## Agent Integration

No agent integration required — this is a prose edit to a skill body already surfaced via the existing hardlink-sync path. No new CLI entry point, no bridge import.

## Failure Path Test Strategy

No runtime failure paths — a Markdown prose change has no executable branch. The only regression surface is the skill-repo-agnosticism audit, covered under Success Criteria.

## Test Impact

No existing tests affected — this is a prose edit to a Markdown skill body with no test coverage tied to the specific sentences being changed. `audit-skills --skill do-sdlc` (rule_13/rule_21 probe structure) is the only automated gate that runs, but it does NOT grep for TTL numbers or the cross-reference token — the content success criteria below are therefore verified manually at PR review via `git diff`, not by an automated check.

## Success Criteria

- [ ] `.claude/skills-global/do-sdlc/SKILL.md` line 103 (recovery paragraph) contains no numeric TTL; timing deferred to the #2446 cross-reference.
- [ ] The owned-run_ids discriminator (lines 95-101 region) carries a bare #2446/#2451 attribution cross-reference with no mechanism description.
- [ ] `git diff` shows changes confined to `.claude/skills-global/do-sdlc/SKILL.md` (manual, at PR review).
- [ ] `audit-skills --skill do-sdlc` remains PASS (rule_13/rule_21 probe coverage intact) — the only automated gate.
- [ ] The added cross-reference is a bare pointer (`(see #2446, #2451)`) with no verb phrase — manual, at PR review.
- [ ] Behavioral: a supervisor reading the recovery paragraph is not told any concrete wait duration and is pointed at #2446 for timing.

## Rabbit Holes

- Rewriting the whole recovery paragraph. Scope is the stale parenthetical only; keep the surrounding `--reuse-run-id` guidance intact.
- Documenting the owned-run_ids recording mechanism. That is #2446/#2451's scope; a bare pointer is the whole ask.

## Documentation

No documentation changes needed — the change IS to a skill body (which is itself the documentation surface). `docs/features/sdlc-local-supervision.md` already summarizes the refusal-discrimination behavior at the #2452 section and does not restate the TTL number, so it needs no update. Confirmed in-session that it defers timing rather than naming a duration.
