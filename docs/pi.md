# Pi

Pi (`@mariozechner/pi-coding-agent`, pinned in `harnesses/pi.py`, `PINNED`)
is the second harness. It is a node script installed at `PINNED` in
Homebrew's prefix (`npm install -g @mariozechner/pi-coding-agent@<PINNED>`),
where `settings.pi` defaults (`/opt/homebrew/bin/pi`) and every turn profile
denies writing. `VALOR_PI` names another install; the profile then denies
writing the directory above that install's first `node_modules` (the
resolved target's own directory when it has none) and the directory that
holds `VALOR_PI` itself, resolved through any symlinked parent
(`workspace.pi_install`). It also denies renaming or replacing each
ancestor of those directories and every symlink on the way from `VALOR_PI`
to the program, walked one component at a time as the kernel does (a
`..` goes up from where the link before it points), each with its own
ancestors (a `literal` write deny, so a turn still creates entries beside
them), so no turn can move the install aside and put its own tree in its
place. What is held is the path to the program and every link on it, with
their ancestors, and the install directory. An install whose other
directories are symlinks (pnpm, `npm link`) is not held, so Pi is installed
by Homebrew or plain npm. A `VALOR_PI` in a shared
directory denies that whole directory; the operator chooses that breadth.
The kernel runs it as `settings.node` and `cli.js`, both by absolute path
and never looked up on a PATH. `node` defaults to `/opt/homebrew/bin/node`,
inside the prefix every turn profile denies writing; `VALOR_NODE` names
another, and its resolved interpreter gets the same denials as the Pi
install (its directory, the ancestors, every symlink on the way), so a node
in a place a turn can write, such as nvm's, cannot be replaced. Pi runs
inside the same turn sandbox profile and `VALOR_TURN` mark as Claude Code,
and records the release (`harness_version`) on every `turn.started`. Its
wrapper `workspace_turn` takes the same `harness` settings as Claude
Code's, plus `pi_agent_dir`, the turn's own Pi directory, and a task's
Brief names the wrapper in `harness_name`
(`claude_code` or `pi`; `--harness` on `start`).

- **Provider.** Pi reaches OpenAI only through the gateway's OpenAI route.
  Each turn the kernel writes `models.json` into `pi_agent_dir`: one
  provider whose base URL is `<gateway>/t/<token>/openai/v1`, the
  placeholder key the gateway replaces, `maxTokens` from the harness's
  `max_output_tokens`, `contextWindow` from the model's entry in the
  gateway's OpenAI price table less its maximum output (the input the API
  accepts), and the gateway's default-tier prices as Pi's cost fields. Pi's
  own reported cost is an approximation (it cannot express tiers or
  long-context rates); the
  gateway's meter is the record. The machine user's `~/.pi` is denied to
  every turn profile.
- **Configuration a clone carries.** `--system-prompt` is always passed, so
  `.pi/SYSTEM.md` and `.pi/APPEND_SYSTEM.md` never replace it, and
  `--no-context-files --no-extensions --no-skills --no-prompt-templates
  --no-themes` keep `AGENTS.md`, `CLAUDE.md`, and the clone's extensions,
  skills, prompts, and themes out. The one file Pi still reads is
  `.pi/settings.json` (compaction, retry, thinking, shell path and prefix,
  package list); it cannot change the provider, model, tools, or system
  prompt, which are flags. The working session owns its clone, so its settings
  stand there. A blind checkout leaves `.pi` out of the working tree, as a
  directory or a link, for every harness (`workspace.BLIND_LEFT_OUT`); the
  diff the reviewer gets still shows a candidate's change to it.
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

The contract every harness meets is `tests/test_harness_contract.py`, run
against each real binary under the real turn profile, through the real
gateway, with a scripted provider: a turn ends and records its version,
resume carries context, signals are collected, a stop reaps the group,
every call is metered, a stray credential is never used, and candidate
configuration never reaches the model. A new harness joins by being added to
that list. Codex has not been run here.

The Pi cases of the contract suite and `tests/test_pi.py` run only against
the pinned release and are skipped when the installed Pi is another one. On a
machine whose `pi` is not at `PINNED`, run them with `VALOR_PI` set to a
pinned install, for example:

```
VALOR_PI=~/.cache/valor-pi-<PINNED>/node_modules/.bin/pi \
  python -m pytest tests/test_pi.py tests/test_harness_contract.py
```
