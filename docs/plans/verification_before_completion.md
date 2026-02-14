---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-02-14
tracking: https://github.com/tomcounsell/ai/issues/103
---

# Verification-Before-Completion Gate

## Problem

The agent can claim completion without proving it. The output classifier in `bridge/summarizer.py` pattern-matches on words like "done", "committed", "pushed" — it trusts the claim without checking for evidence.

**Current behavior:**
- Builder says "Done. Committed abc1234" → classifier sees "done" + commit hash → COMPLETION
- No verification that tests actually passed, that the commit exists, or that docs were updated
- Agent can hedge ("should work", "probably fine") and still get classified as complete
- Validator is one-shot and trusts builder's self-reported output

**Desired outcome:**
- Agent must produce evidence (command output, test results) alongside completion claims
- Classifier rejects evidence-free completion claims as STATUS_UPDATE (forcing auto-continue)
- Validator independently runs verification commands
- Hedging language ("should", "probably", "seems to") blocks completion classification
- Documentation changes verified as part of completion gate (per #69)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Builder prompt: verification-before-completion section**: Hard gate requiring evidence before any completion claim
- **Classifier hardening**: COMPLETION requires evidence; claims without evidence → STATUS_UPDATE
- **Validator prompt: independent verification**: Run commands yourself, don't trust builder output
- **Rationalization table**: Counter common excuses for skipping verification

### Flow

**Builder finishes work** → [Run verification commands] → [Read output, confirm pass] → [Include evidence in completion message] → **Classifier sees evidence** → COMPLETION

**Builder claims done without evidence** → [Classifier sees no evidence] → STATUS_UPDATE → [Auto-continue forces agent to verify] → loop until evidence provided

### Technical Approach

Three layers, all prompt-level except one classifier change:

**Layer 1: Builder agent prompt** (`.claude/agents/builder.md`)

Add a verification-before-completion section after the SDLC workflow:

```
## Verification Before Completion (MANDATORY)

Before claiming ANY work is complete:
1. IDENTIFY what command proves the claim
2. RUN the command (fresh, not cached from earlier)
3. READ full output — check exit code, count failures
4. INCLUDE the evidence in your completion message

Skip any step = the classifier will reject your completion and force you to verify.
```

Add red flags list: "should work", "probably fine", "seems to", "looks correct" — these words in a completion message cause it to be rejected.

**Layer 2: Classifier prompt hardening** (`bridge/summarizer.py`)

Update the COMPLETION classification rules:

```
COMPLETION — The work is done AND evidence is provided.
  REQUIRES: command output, test results, or verification evidence
  Examples: "All 42 tests pass. Committed abc1234, pushed to main."
  Key signals: specific numbers (N tests, 0 errors), command output, exit codes

  NOT completion (classify as STATUS_UPDATE instead):
  - "Done" without evidence
  - "Should work now" (hedging = not verified)
  - "Committed and pushed" without test results
  - Any use of "should", "probably", "seems to", "looks like"
```

This creates a structural gate: the agent literally cannot complete without evidence because the classifier will auto-continue it.

**Layer 3: Validator prompt** (`.claude/agents/validator.md`)

Add independent verification:

```
## Independent Verification (MANDATORY)

Do NOT trust builder's self-reported output. Run verification commands yourself:
- Run the same test commands the builder claims to have run
- Compare your results to the builder's claims
- If results differ, report the discrepancy
```

### Rationalization Table

| Excuse | Reality |
|---|---|
| "Should work now" | Run the verification. |
| "I'm confident it works" | Confidence is not evidence. |
| "Linter passed so it's fine" | Linter is not tests. Run tests. |
| "I already ran it earlier" | Earlier is not now. Run it fresh. |
| "The change is trivial" | Trivial changes cause production outages. Verify. |
| "I'm running out of iterations" | Commit [WIP]. Don't claim false completion. |
| "The agent reported success" | Verify independently. Agent reports are not evidence. |
| "Tests pass, so requirements are met" | Tests prove code works. Re-read the plan to prove requirements are met. |

### Evidence Requirements Table

| Claim | Required Evidence | Not Sufficient |
|---|---|---|
| "Tests pass" | Test command output: 0 failures, N passed | "Should pass", previous run |
| "Linting clean" | Linter output: 0 errors | "I fixed the lint issues" |
| "Build succeeds" | Build command: exit 0 | "Linter passed" |
| "Bug fixed" | Reproduction test passes | "Code changed, should be fixed" |
| "Docs updated" | File exists at expected path | "I'll update docs later" |
| "PR ready" | PR URL + tests passing | "Committed and pushed" |

## Rabbit Holes

- **Parsing verification evidence programmatically**: Don't try to parse test output in the classifier. The LLM classifier can judge whether evidence is present — we don't need regex for "42 tests passed". Keep it prompt-level.
- **Blocking non-build completions**: This gate applies to `/build` workflow agents. Don't try to enforce it on casual Telegram conversations or Q&A — those completions are fine without test evidence.
- **Evidence freshness verification**: Don't try to verify timestamps on command output. Trust the LLM to distinguish "I ran this just now" from "I ran this earlier." The hard gate is "evidence present vs absent", not "evidence fresh vs stale."

## Risks

### Risk 1: Classifier over-rejects legitimate completions
**Impact:** Agent gets stuck in auto-continue loop, never completing
**Mitigation:** The classifier prompt is tuned by the LLM, not regex. It can distinguish "Done. All 42 tests pass (0 failures). Committed abc1234." from "Done." The MAX_AUTO_CONTINUES cap (3) prevents infinite loops — after 3 rejections the message goes to chat anyway.

### Risk 2: Builder agents include fake evidence
**Impact:** Agent fabricates test output to pass the gate
**Mitigation:** Layer 3 — the validator independently runs the same commands. Fabricated evidence gets caught by the validator.

## No-Gos (Out of Scope)

- Changes to auto-continue system or job queue
- Programmatic parsing of test output
- Coverage threshold enforcement
- Changes to MAX_AUTO_CONTINUES value
- Evidence freshness timestamp verification

## Update System

No update system changes required — prompt engineering and classifier prompt changes only.

## Agent Integration

No agent integration required — changes are to agent prompts (`.claude/agents/builder.md`, `.claude/agents/validator.md`) and classifier prompt (`bridge/summarizer.py`), all loaded natively.

## Documentation

- [ ] Update `docs/features/bridge-workflow-gaps.md` to document the verification gate
- [ ] Add entry to `docs/features/README.md` index if new feature doc created

## Success Criteria

- [ ] Builder agent prompt includes verification-before-completion section with hard gate
- [ ] Rationalization table and evidence requirements table in builder prompt
- [ ] Classifier COMPLETION rules require evidence; hedging → STATUS_UPDATE
- [ ] Validator prompt includes independent verification instructions
- [ ] Red flags list (hedging language) in both builder and classifier prompts
- [ ] Documentation enforcement included (per #69 comment)
- [ ] All existing tests pass after changes

## Team Orchestration

### Team Members

- **Builder (prompts)**
  - Name: prompt-engineer
  - Role: Update builder, validator, and classifier prompts
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: prompt-validator
  - Role: Verify all prompt changes are correct and complete
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update builder agent prompt
- **Task ID**: build-builder-prompt
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Add "Verification Before Completion (MANDATORY)" section to `.claude/agents/builder.md`
- Add the gate function: IDENTIFY → RUN → READ → INCLUDE evidence
- Add red flags list (hedging language that blocks completion)
- Add rationalization table
- Add evidence requirements table
- Update Definition of Done to include "Verification evidence provided"

### 2. Update classifier prompt
- **Task ID**: build-classifier-prompt
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Update COMPLETION rules in `CLASSIFIER_SYSTEM_PROMPT` in `bridge/summarizer.py`
- COMPLETION now requires evidence (test output, command results, specific numbers)
- Claims without evidence → STATUS_UPDATE
- Hedging language ("should", "probably", "seems to") → STATUS_UPDATE
- Add examples of genuine completion vs evidence-free claims

### 3. Update validator agent prompt
- **Task ID**: build-validator-prompt
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Add "Independent Verification (MANDATORY)" section to `.claude/agents/validator.md`
- Validator must run verification commands itself, not trust builder output
- Compare own results to builder's claims
- Report discrepancies

### 4. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-builder-prompt, build-classifier-prompt, build-validator-prompt
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify builder prompt contains verification gate, rationalization table, evidence table
- Verify classifier prompt requires evidence for COMPLETION
- Verify validator prompt includes independent verification
- Run `pytest tests/ -v` to ensure nothing broke
- Run `ruff check . && black --check .`

## Validation Commands

- `cat .claude/agents/builder.md` - Verify verification gate and tables present
- `cat .claude/agents/validator.md` - Verify independent verification section
- `grep -A 10 'COMPLETION' bridge/summarizer.py` - Verify evidence requirement in classifier
- `pytest tests/ -v` - Ensure existing tests pass
- `ruff check bridge/summarizer.py` - Lint classifier changes
- `black --check bridge/summarizer.py` - Format check
