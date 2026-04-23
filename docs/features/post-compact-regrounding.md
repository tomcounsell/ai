# Post-Compact Re-Grounding

Short re-grounding nudge delivered to the agent immediately after context compaction, directing it to re-read the plan, check SDLC stage progress, review any PROGRESS.md scratchpad, and check the TodoWrite task list.

## Status

Shipped — issue [#1139](https://github.com/tomcounsell/ai/issues/1139).

## Problem

After context compaction, dev sessions resume against a compacted summary that rarely preserves working-state precision. Without a targeted nudge, multi-hour builds routinely drift from plan, repeat already-completed steps, or abandon in-progress work because the agent has no signal that a compaction just happened.

## Behavior

Immediately after every compaction event, `.claude/hooks/post_compact.py` emits a short imperative nudge to stdout. The Claude CLI surfaces this as a user-visible message at the start of the next turn.

### Nudge content

The nudge is directive, not descriptive. Each item is conditionally included only when its data is available:

1. **Plan doc** (if `AgentSession.plan_url` is set): "Re-read the plan: `<plan_url>`"
2. **SDLC stage progress** (if `AgentSession.stage_states` is set): "Check SDLC stage progress: `python -m tools.sdlc_stage_query --issue-number <N>`" — issue number is extracted from `AgentSession.issue_url`
3. **PROGRESS.md scratchpad** (if `PROGRESS.md` exists in the session's `cwd`): "Re-read `PROGRESS.md` for working state."
4. **TodoWrite task list** (always): "Re-read your current TodoWrite task list."

The nudge always includes the header ("Context was just compacted. Re-ground:") and the TodoWrite item. All other items degrade gracefully when the underlying data is absent.

**Token budget**: The full 4-item nudge runs ~80-120 tokens. Well under the 300-token ceiling.

### Degradation behavior

| Context available | Nudge content |
|-------------------|---------------|
| AgentSession with plan_url + stage_states + PROGRESS.md | Full 4-item nudge |
| AgentSession with plan_url only | Header + plan item + TodoWrite |
| AgentSession with no plan_url or stage_states | Header + TodoWrite (minimal nudge) |
| session_id present, no matching AgentSession row | Header + TodoWrite (minimal nudge) |
| No session_id in hook input | Nothing emitted |

The minimal nudge (header + TodoWrite) is universally useful — even in one-off Claude Code sessions with no SDLC context, reminding the agent to check its task list is sound.

## Why CLI-only

The Claude SDK's `HookEvent` type does not include `PostCompact`. Registering this hook in `build_hooks_config()` (used by bridge/SDK-based sessions) would require SDK changes that are out of scope and unnecessary — bridge sessions rely on:

1. The existing `defer_post_compact` nudge guard in `agent/output_router.py` (shipped in #1127/#1135).
2. Issue #1130's prompt instructions in `builder.md` directing the agent to re-read `PROGRESS.md` after compaction.

This hook is scoped purely to local interactive Claude Code CLI sessions.

## Hook Contract

**Input** (stdin JSON):
- `hook_event_name`: `"PostCompact"`
- `session_id`: Claude session UUID — same as `AgentSession.claude_session_uuid`
- `trigger`: `"auto"` or `"manual"`
- `compact_summary`: compacted summary text
- `transcript_path`: path to the JSONL transcript
- `cwd`: working directory of the Claude session

**Output** (stdout, exit code 0): nudge message text, or empty if no context available (no `session_id` in input).

**Exit code**: always 0. The hook must never block the session.

**Bail-out guarantee**: All exceptions are swallowed. On any error, the hook prints nothing and exits 0. This ensures the Claude CLI is never interrupted by hook failures.

## Registration

`.claude/settings.json`:

```json
"PostCompact": [
  {
    "matcher": "",
    "hooks": [
      {
        "type": "command",
        "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post_compact.py || true",
        "timeout": 10
      }
    ]
  }
]
```

The `|| true` ensures the CLI is never blocked if the hook process exits non-zero. Timeout is 10 seconds — generous enough for a Redis lookup on a cold connection.

## Relationship to Compaction Hardening (#1127/#1135)

[Compaction Hardening](compaction-hardening.md) handles the *before-compaction* side:
- PreCompact hook takes a JSONL backup
- 5-minute cooldown prevents backup thrashing
- 30-second post-compact nudge guard in the output router prevents immediate nudge-on-compaction

This feature handles the *after-compaction* side: the agent's re-grounding at the start of the first post-compaction turn.

The two features are complementary and independent. Disabling either does not affect the other.

## Relationship to Long-Task Checkpointing (#1130)

Issue #1130 adds prompt instructions in `builder.md` directing the agent to write `PROGRESS.md` during long tasks and to re-read it after compaction. This hook is the CLI-level enforcement of the same re-grounding intent — it fires automatically without requiring the agent to remember.

If `PROGRESS.md` exists in `cwd`, the hook includes it as item 3 in the nudge. Issue #1130 need not have merged for this hook to be useful.

## Cross-References

- Plan: [`docs/plans/postcompact-regrounding-hook.md`](../plans/postcompact-regrounding-hook.md)
- Issue: [#1139](https://github.com/tomcounsell/ai/issues/1139)
- Hook: `.claude/hooks/post_compact.py`
- Registration: `.claude/settings.json` (PostCompact key)
- Tests: `tests/unit/hooks/test_post_compact_hook.py`
- Related: [Compaction Hardening](compaction-hardening.md) — PreCompact backup + nudge guard
- Related: [Claude Code Memory](claude-code-memory.md) — sibling CLI hook pattern
