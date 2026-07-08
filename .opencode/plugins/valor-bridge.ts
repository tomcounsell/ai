// opencode-sync: generated 2026-07-08
// Port of .claude/hooks/*.py validators into an OpenCode plugin.
// Re-run scripts/sync_claude_to_opencode.py to regenerate idempotently.
//
// OpenCode has no PreToolUse/PostToolUse/Stop hook runner, so this plugin
// re-dispatches the SAME python validators on tool.execute.before/after and
// the nearest session-lifecycle equivalents. CLAUDE_PROJECT_DIR is injected
// from the project directory so the validators behave exactly as under Claude Code.
// Blocking validators throw to reject the operation; best-effort ("|| true")
// validators never block.

import { type Plugin } from "@opencode-ai/plugin"

const PRE_BASH = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_commit_message.py", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_merge_guard.py", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_no_raw_redis_delete.py", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_design_system_sync.py", "blocking": true}]
const PRE_EDIT = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_design_system_readonly.py", "blocking": true}]
const PRE_GLOBAL = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/pre_tool_use.py", "blocking": false}]
const POST_WRITE = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_documentation_section.py", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_test_impact_section.py", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_no_gos_justification.py", "blocking": true}, {"cmd": "uv run \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_file_contains.py -d docs/plans -e .md --contains '## Success Criteria' --contains '## Update System' --contains '## Agent Integration' --contains '## Test Impact'", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_features_readme_sort.py", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/sdlc_reminder.py", "blocking": false}]
const POST_EDIT = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_features_readme_sort.py", "blocking": true}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/sdlc_reminder.py", "blocking": false}]
const POST_GLOBAL = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post_tool_use.py", "blocking": false}]
const SESSION_CREATED = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/user_prompt_submit.py", "blocking": false}]
const SESSION_IDLE = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/stop.py --chat", "blocking": false}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/validators/validate_sdlc_on_stop.py", "blocking": false}, {"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/subagent_stop.py", "blocking": false}]
const SESSION_COMPACTED = [{"cmd": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post_compact.py", "blocking": false}]

async function runValidator(rec: { cmd: string; blocking: boolean }, payload: any, dir: string) {
  const proc = Bun.spawn(["bash", "-c", rec.cmd], {
    stdin: new Blob([JSON.stringify(payload)]),
    env: { ...process.env, CLAUDE_PROJECT_DIR: dir },
    stdout: "pipe",
    stderr: "pipe",
  })
  const code = await proc.exited
  if (rec.blocking && code !== 0) {
    const err = await new Response(proc.stderr).text()
    const tail = err.slice(0, 400)
    const msg = "[valor-bridge] " + rec.cmd.split(" ").pop()
      + " blocked (exit " + code + "): " + tail
    throw new Error(msg)
  }
}

const toolPayload = (tool: string, args: any) => ({
  tool_name: tool === "write" ? "Write" : tool === "edit" ? "Edit" : tool,
  tool_input: {
    command: args?.command ?? "",
    file_path: args?.filePath,
    filePath: args?.filePath,
  },
})

export const ValorBridge: Plugin = async ({ directory }) => {
  const dir = directory
  return {
    "tool.execute.before": async (input, _output) => {
      const p = toolPayload(input.tool, input.args)
      if (input.tool === "bash") for (const v of PRE_BASH) await runValidator(v, p, dir)
      if (input.tool === "edit" || input.tool === "write") {
        for (const v of PRE_EDIT) await runValidator(v, p, dir)
      }
      for (const v of PRE_GLOBAL) await runValidator(v, p, dir)
    },
    "tool.execute.after": async (input, _output) => {
      const p = toolPayload(input.tool, input.args)
      if (input.tool === "edit" || input.tool === "write") {
        for (const v of POST_WRITE) await runValidator(v, p, dir)
        for (const v of POST_EDIT) await runValidator(v, p, dir)
      }
      for (const v of POST_GLOBAL) await runValidator(v, p, dir)
    },
    "session.created": async () => {
      for (const v of SESSION_CREATED)
        await runValidator(v, { tool_name: "", tool_input: {} }, dir)
    },
    "session.idle": async () => {
      for (const v of SESSION_IDLE)
        await runValidator(v, { tool_name: "", tool_input: {} }, dir)
    },
    "session.compacted": async () => {
      for (const v of SESSION_COMPACTED)
        await runValidator(v, { tool_name: "", tool_input: {} }, dir)
    },
  }
}
