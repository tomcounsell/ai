# Pi

Pi (`@earendil-works/pi-coding-agent`, pinned in `harnesses/pi.py`,
`PINNED`, 1.0.0: the newest release two weeks old with MCP built in) is the
second harness. It is a node script (node 22.19 or later) installed at
`PINNED` with `npm install -g @earendil-works/pi-coding-agent@<PINNED>`, where
`settings.pi` defaults (`/opt/homebrew/bin/pi`); `VALOR_PI` names another
install. The package before 1.0 was `@mariozechner/pi-coding-agent`; both
install a `pi`, so the old one is uninstalled first. The
kernel runs it as `settings.node` and the resolved `cli.js` (`VALOR_NODE`
names another node), and records the release (`harness_version`) on every
`turn.started`. Its wrapper `workspace_turn` takes the same `harness`
settings as Claude Code's, plus `pi_agent_dir`, the turn's own Pi directory,
and a task's Brief names the wrapper in `harness_name` (`claude_code`, `codex` or
`pi`; `--harness` on `start`). Pi runs under the same turn sandbox profile
and `VALOR_TURN` mark as Claude Code.

- **Provider.** Pi reaches OpenAI only through the gateway's OpenAI route.
  Each turn the kernel writes `models.json` into `pi_agent_dir`: one
  provider whose base URL is `<gateway>/t/<token>/openai/v1`, the
  placeholder key the gateway replaces, `maxTokens` from the harness's
  `max_output_tokens`, `contextWindow` from the model's entry in the
  gateway's OpenAI price table less its maximum output (the input the API
  accepts), and the gateway's default-tier prices as Pi's cost fields. Pi's
  own reported cost is an approximation (it cannot express tiers or
  long-context rates); the
  gateway's meter is the record.
- **Context and skills, as a developer's Pi.** `--system-prompt` is always
  passed, so `.pi/SYSTEM.md` and `.pi/APPEND_SYSTEM.md` never replace it;
  the project's `AGENTS.md` and `CLAUDE.md` are appended to it. Skills load
  from the project's `.pi/skills` and `.agents/skills`, the operator's
  `~/.agents/skills`, and `--skill <plugin>/skills` for each of
  `settings.plugin_dirs`. Project skills, `.pi/settings.json` and
  `.pi/mcp.json` are behind Pi's project trust, which JSON mode cannot ask
  for, so the turn passes `--approve`. `--no-extensions --no-prompt-templates
  --no-themes` keep the clone's extensions, prompts, and themes out;
  `--no-extensions` also drops Pi's built-in extensions, so `-e builtin:mcp`,
  `builtin:codemode` and `builtin:tool-search` name them back.
  `.pi/settings.json` is read (compaction, retry, thinking, shell path and prefix, default tools,
  package and skills lists); it cannot change the provider, model, or
  system prompt, which are flags.
- **MCP.** The kernel writes the operator's MCP servers
  (`claude_code.user_mcp_servers`) into `mcp.json` in `pi_agent_dir` each
  turn, entries as Claude Code reads them. Pi names their tools
  `mcp__<server>__<tool>` and by default reaches them through `codemode`
  scripts (its docs/mcp.md, exposure), listing each server in the system
  prompt; a server's `exposure` in the operator's config changes that.
- **The prompt is on stdin.** `-p` merges piped input into the prompt, so a
  prompt starting with `@` is not a file. Only the
  Brief is appended to the system prompt.
- **Resume** is by session id from `--session-dir`; an unknown id prints
  `No session found matching '<id>'` and exits 1, which `parse` reports as
  `is_error` (no session line).
- **Retry.** An `agent_end` with `willRetry` is not the end of the run;
  `parse` waits for one without it.
- **Compaction.** A compaction a turn triggers runs after `agent_end`; in
  print mode the stream can end on `compaction_start` and the session still
  holds the result. `parse` lists each with `finished`. The API refuses a
  request above the window less the model's maximum output, so that is the
  window Pi is given, and Pi compacts on its threshold rather than after a
  failed call. Measured live (2026-10-03): Pi compacted at 914,292 input
  tokens, over its threshold of 905,616, and the next call ran at 316,591;
  the session cost $8.87 at the gateway against $2.63 Pi reported, since Pi
  cannot express the cache-write and long-context rates.

The suite builds a Pi turn and checks its MCP config, skills and flags
(`tests/test_kernel.py`); it runs no Pi.
