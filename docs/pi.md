# Pi

Pi (`@mariozechner/pi-coding-agent`, pinned in `harnesses/pi.py`, `PINNED`)
is the second harness. It is a node script installed at `PINNED` with
`npm install -g @mariozechner/pi-coding-agent@<PINNED>`, where `settings.pi`
defaults (`/opt/homebrew/bin/pi`); `VALOR_PI` names another install. The
kernel runs it as `settings.node` and the resolved `cli.js` (`VALOR_NODE`
names another node), and records the release (`harness_version`) on every
`turn.started`. Its wrapper `workspace_turn` takes the same `harness`
settings as Claude Code's, plus `pi_agent_dir`, the turn's own Pi directory,
and a task's Brief names the wrapper in `harness_name` (`claude_code` or
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
- **Configuration a clone carries.** `--system-prompt` is always passed, so
  `.pi/SYSTEM.md` and `.pi/APPEND_SYSTEM.md` never replace it, and
  `--no-context-files --no-extensions --no-skills --no-prompt-templates
  --no-themes` keep `AGENTS.md`, `CLAUDE.md`, and the clone's extensions,
  skills, prompts, and themes out. The one file Pi still reads is
  `.pi/settings.json` (compaction, retry, thinking, shell path and prefix,
  package list); it cannot change the provider, model, tools, or system
  prompt, which are flags.
- **The prompt is on stdin.** `-p` merges piped input into the prompt, so a
  prompt starting with `-` or `@` is neither an option nor a file. Only the
  Brief is appended to the system prompt.
- **Resume** is by session id from `--session-dir`; an unknown id prints
  `No session found matching '<id>'` and exits 1, which `parse` reports as
  `is_error` (no session line).
- **Compaction.** A compaction a turn triggers runs after `agent_end`; in
  print mode the stream can end on `compaction_start` and the session still
  holds the result. `parse` lists each with `finished`. The API refuses a
  request above the window less the model's maximum output, so that is the
  window Pi is given, and Pi compacts on its threshold rather than after a
  failed call. Measured live (2026-10-03): Pi compacted at 914,292 input
  tokens, over its threshold of 905,616, and the next call ran at 316,591;
  the session cost $8.87 at the gateway against $2.63 Pi reported, since Pi
  cannot express the cache-write and long-context rates.

The suite holds no Pi tests.
