# harnesses

Wrappers for running work through an agent harness.

## Scope

- One wrapper per harness: Claude Code, Codex, Pi, and any future one.
- Turn execution: one `claude -p` (or equivalent) at a time on this machine.
- Session resume and transcript capture.
- Per-harness skill rendering: turning a versioned skill into what the harness loads.
- Each wrapper conforms to the harness port in `core/`.

Governed by [docs/harnesses.md](../docs/harnesses.md).

## The Claude Code wrapper

`claude_code.py` builds two kinds of turn. `turn` is one self-contained `claude -p` call with no tools by default. `workspace_turn` is one turn of a task that works in a directory: it keeps Claude Code's own system prompt and tools, appends the persona and the dispatched Brief re-rendered every turn, resumes the task's session, edits and runs commands without prompting, and runs with safe mode, no MCP servers, and no web tools. The task's `harness` settings must name the sandbox-exec profile the turn runs under (it refuses a task without one), and may give git and gh their own config and add the workspace's own variables to the environment. The environment it passes is an allowlist; tokens and agent sockets stay behind.

## Imports

- May import: `core/`.
- Imported by: `core/` through the harness port, and `tests/`.

## Effect classes

Holds none directly. A harness runs a turn inside the ceiling `core/` issued for it; any effect the agent attempts reaches the world through the broker with that ceiling.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Deciding what to run, when, or with what budget. That is `core/`.
- Hooks or validators injected into a harness to police the agent. Authority is the kernel's, not the prompt layer's.
- Skill content. That is `skills/`.
- Container images and sandbox tooling that are not harness-specific. That is `tools/`.
