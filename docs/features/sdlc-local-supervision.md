# SDLC Local Supervision (`/do-sdlc`)

## Problem

`/sdlc` is a single-stage router by contract: it assesses state, dispatches ONE sub-skill, and returns ("NEVER loop" — Hard Rule 7). Pipeline *progression* is assigned to the PM session, which re-invokes `/sdlc` after each stage. That PM loop only exists for bridge-initiated sessions — in a local Claude Code session there is no PM, so the human has to re-prompt "continue" after every stage.

## Solution

`/do-sdlc {issue|PR|description}` (`.claude/skills-global/do-sdlc/SKILL.md`) is the local stand-in for the bridge PM loop. It supervises the full pipeline in one invocation:

1. Resolves the issue (creates one via a `/do-issue` subagent if given a bare description) and runs `sdlc-tool session-ensure`.
2. Loops: `sdlc-tool next-skill` → `sdlc-tool dispatch record` → spawn a stage subagent that invokes the stage's `/do-*` skill → read its structured report → repeat.
3. Exits on merge confirmation (`gh pr view --json state` = `MERGED`), a `blocked` router decision (guard fired — surfaced to the human, never retried), or a 15-dispatch iteration cap.

The supervisor never decides dispatch itself — `sdlc-tool next-skill` (→ `agent.sdlc_router.decide_next_dispatch()`) remains the single source of dispatch truth, so all guards (G1–G7, including G4 oscillation) apply identically to local runs.

## Stage→Model Parity

Each stage subagent is spawned with an explicit `model:` parameter mirroring the PM persona's Stage→Model Dispatch Table (`config/personas/project-manager.md`): opus for PLAN/CRITIQUE/REVIEW, sonnet for ISSUE/BUILD/TEST/PATCH/DOCS/MERGE. This is the local equivalent of the bridge PM's `valor-session create --model` flag — without it, every stage would run on the interactive session's model.

## Stage-Marker Backfill

`/do-test` and `/do-patch` do not write their own stage markers; on the bridge, `_handle_dev_session_completion()` writes them when the dev session finishes. `/do-sdlc` backfills those two markers itself (`sdlc-tool stage-marker --stage TEST|PATCH --status completed|failed`) based on the subagent's report. All other stage skills self-mark (see [sdlc-stage-tracking.md](sdlc-stage-tracking.md)) and are not double-written.

## Relationship to `/sdlc`

| | `/sdlc` | `/do-sdlc` |
|---|---|---|
| Contract | dispatch ONE stage, return | loop until merge/blocked/cap |
| Progression | PM session re-invokes | skill re-invokes the router |
| Model assignment | PM passes `--model` on dev-session create | `model:` on the Agent tool |
| Execution | dev `AgentSession`s via worker | subagents in the local session |

`/loop /sdlc {N}` remains a zero-code alternative that closes the progression gap but runs every stage on the session model (no opus/sonnet cost profile).

## Distribution

Lives in `.claude/skills-global/` and is hardlink-synced to `~/.claude/skills/` by `/update` (`scripts/update/hardlinks.py`) — available in any repo, like the other `do-*` stage skills it dispatches.
