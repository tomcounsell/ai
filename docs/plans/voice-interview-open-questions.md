---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-09-15
tracking: https://github.com/tomcounsell/ai/issues/3330
last_comment_id: none
---

# Voice Interview for Open Questions

## Problem

[skeleton — Phase 2 fill]

## Freshness Check

[skeleton — Phase 2 fill]

## Prior Art

[skeleton — Phase 2 fill]

## Research

[skeleton — Phase 2 fill]

## Spike Results

[skeleton — Phase 2 fill]

## Data Flow

[skeleton — Phase 2 fill]

## Architectural Impact

[skeleton — Phase 2 fill]

## Appetite

Large. [skeleton — Phase 2 fill]

## Prerequisites

[skeleton — Phase 2 fill]

## Solution

### Key Elements

[skeleton — Phase 2 fill]

### Flow

[skeleton — Phase 2 fill]

### Technical Approach

[skeleton — Phase 2 fill]

## Failure Path Test Strategy

[skeleton — Phase 2 fill]

## Test Impact

New test files are added under `tests/unit/` for each new module; no existing
test is expected to change behavior, but `tests/unit/test_open_question_gate.py`
is a direct consumer of the extraction logic being lifted out of
`bridge/message_drafter.py` and must stay green unchanged as the regression
guard for that refactor.

- [ ] `tests/unit/test_open_question_gate.py` — must pass unchanged after the
      extractor is moved; this is the contract that the lift did not change
      behavior for its existing caller.
- [ ] `tests/unit/test_open_questions_extract.py` — new. Corpus-fixture tests for
      all resolution-marking shapes, including defaulted items.
- [ ] `tests/unit/test_open_questions_score.py` — new. Leverage scoring and
      pruning-edge selection on synthetic question sets.
- [ ] `tests/unit/test_question_clip_cache.py` — new. Cache-key coverage and
      reuse/invalidations.
- [ ] `tests/unit/test_voice_interview_session.py` — new. Answer-to-clip binding,
      pruning traversal, off-script stop.
- [ ] `tests/unit/test_open_questions_writeback.py` — new. Sequential apply,
      `Edit`-only on plan docs, drift detection, commit-message shape.

Run scope: `scripts/pytest-clean.sh tests/unit/test_open_questions_extract.py`
and siblings by name. Never a full `tests/unit/` sweep for this work.

## Rabbit Holes

[skeleton — Phase 2 fill]

## Risks

[skeleton — Phase 2 fill]

## Race Conditions

[skeleton — Phase 2 fill]

## No-Gos (Out of Scope)

- **A live phone call, and any Tel-Agent dependency.** Justification:
  [Tel-Agent](https://github.com/Dpro-at/Tel-Agent) is AGPL-3.0 with network
  copyleft, speaks only SIP (needs a PBX on the LAN), and documents no
  pre-recorded clip playback. Independently this repo has no streaming STT —
  `tools/transcribe` is batch and file-based over a macOS GUI app. A duplex leg
  is greenfield across licensing, infrastructure, and transport at once, so it
  belongs in its own issue taken only after the Telegram leg proves the pipeline.
- **A live intent classifier in the delivery loop.** Justification: only duplex
  traversal needs it, and the nearest measured precedent (`run_typed_local` on
  `granite4.1:3b`, ~1.1s median / ~1.4s p95 per `config/settings.py:383`) is
  marginal inside a call's turn-taking. Deferred with the call itself.
- **Cross-project harvesting.** Justification: owner ruling. Client projects are
  deliberately separate and each is owned by exactly one machine, so a cross-repo
  harvest would violate project isolation and put one project's calling link in
  front of another project's questions. Each machine harvests its own repo.
- **A plan-doc lock or single-writer lease.** Justification: sequential
  single-writer writeback sidesteps the hazard without new infrastructure, and
  #3330's downstream constraints state that concluding a lock is required is a
  scope change to raise, not absorb.
- **Retro-editing the existing plan-doc corpus to one resolution convention.**
  Justification: [skeleton — decided by spike-normalize + Open Question 1].
- **Timestamped transcript alignment.** Justification: unnecessary on the
  Telegram transport, where one inbound voice note answers one outbound clip, and
  it would force the cloud STT path since the local SuperWhisper backend returns
  no timestamps.

## Update System

[skeleton — Phase 2 fill]

## Agent Integration

[skeleton — Phase 2 fill]

## Documentation

- [ ] Create `docs/features/voice-interview-open-questions.md` describing the
      harvester, the question tree, the clip library, the delivery loop, and the
      sequential writeback contract.
- [ ] Add a row to `docs/features/README.md` index table.
- [ ] Add the `tools.open_questions` CLI to `docs/tools-reference.md`.
- [ ] Update `.claude/skill-context/ask-me.md` if the voice mode shares that
      skill's transport (depends on Open Question 3).

## Success Criteria

[skeleton — Phase 2 fill]

## Team Orchestration

[skeleton — Phase 2 fill]

## Step by Step Tasks

[skeleton — Phase 2 fill]

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Existing extractor contract unbroken | `scripts/pytest-clean.sh tests/unit/test_open_question_gate.py` | exit 0, no test edits |
| New unit tests | `scripts/pytest-clean.sh tests/unit/test_open_questions_extract.py` (and each sibling by name) | exit 0 |
| Lint | `python -m ruff check` | exit 0 |
| Format | `python -m ruff format --check` | exit 0 |
| Harvester runs on the live corpus | `python -m tools.open_questions list --json` | exit 0, counts match Success Criteria |
| Clips exist and are speakable | `python -m tools.question_tree prepare --dry-run` | every node has an on-disk clip; no multi-digit identifier in spoken text |
| Writeback is inspectable before it writes | `python -m tools.open_questions writeback --dry-run` | prints each `Edit` and the exact atomic `git add`/`git commit`; `scripts/check_issue_disposition.py` accepts the message |

## Critique Results

[skeleton — /do-critique fills]

## Open Questions

[skeleton — Phase 2 fill]
