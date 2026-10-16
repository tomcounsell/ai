# harnesses

Wrappers for running work through an agent harness.

## Scope

- One wrapper per harness: Claude Code, Pi and Codex.
- Turn execution: one `claude -p` (or equivalent) at a time on this machine.
- Session resume and transcript capture.
- Per-harness skill rendering: turning a versioned skill into what the harness loads.
- Each wrapper conforms to the harness port in `core/`.

Governed by [docs/harnesses.md](../docs/harnesses.md).

## The Claude Code wrapper

`claude_code.py` builds `workspace_turn`: one turn of a task that works in a directory. It runs as a developer's Claude Code would there: Claude Code's own system prompt and tools, the project's own `CLAUDE.md`, settings, hooks, skills and MCP servers, web tools, the yudame plugins (`--plugin-dir` for each of `settings.plugin_dirs`), and the operator's environment less the kernel's secrets, with Valor's own `gh` login, git credentials and SSH agent. It appends the persona and the dispatched Brief re-rendered every turn, resumes the task's session (a fresh session never resumes), and edits and runs commands without prompting. Two things differ from a developer's: every model call goes to the gateway, which meters it and supplies the credential in place of the placeholder the turn carries, and the turn runs under a sandbox-exec profile. The task's `harness` settings must name that profile (it refuses a task without one), and may give the turn its own `TMPDIR` and Claude Code config directory and add the workspace's own variables to the environment.

## The Pi wrapper

`pi.py` builds one turn of a task through Pi, pinned in `PINNED`. It matches `claude_code.workspace_turn`: the same `harness` settings, the same refusal without a sandbox profile, plus the turn's own Pi directory (`pi_agent_dir`). It writes Pi's `models.json`, `settings.json` and `mcp.json` (the operator's MCP servers) each turn, passes the system prompt, the project's context files and skills and the yudame plugins' skills (`--skill`), trusts the clone, turns the other loaders off but Pi's MCP, codemode and tool-search extensions, gives the prompt on stdin, and reads Pi's JSON event stream in `parse`. How it is configured and why is `docs/pi.md`.

## The Codex wrapper

`codex.py` builds one turn of a task through `codex exec --json`, ported from the old system's adapter. It matches `claude_code.workspace_turn`: the same `harness` settings, the same refusal without a sandbox profile, plus the turn's own Codex home (`codex_home`). It writes Codex's `config.toml` each turn (the gateway's Responses route, `CLAUDE.md` as fallback instructions, the Brief, the operator's MCP servers), links the yudame plugins' skills into the home, gives the prompt on stdin, and reads Codex's JSONL events in `parse`. How and why is the Codex part of `docs/harnesses.md`.

## Imports

- May import: `core/`.
- Imported by: `core/` through the harness port, and `tests/`.

## Effect classes

Holds none directly. A harness runs a turn inside the ceiling `core/` issued for it; any effect the agent attempts reaches the world through the broker with that ceiling.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Deciding what to run, or when. That is `core/`.
- Hooks or validators injected into a harness to police the agent. Authority is the kernel's, not the prompt layer's.
- Skill content. That is `skills/`.
