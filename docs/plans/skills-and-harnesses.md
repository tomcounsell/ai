# Skills, tools and harnesses

Tom, 2026-10-16: "we need a more nuanced approach than simply using a
claude plugin. we want to be supportive of pi and codex harnesses as we
move towards broader capability and RSI again."

**Goal.** Every skill and tool Valor has works the same in Claude Code,
Codex and Pi, from one source, kept current by one mechanism, and Valor
improves them with ordinary tasks.

## Shape

- **Skills: one source.** `yudame/skills` in the Agent Skills format
  (agentskills.io). Claude Code reads it as the two plugins, which the
  marketplace keeps current. Codex and Pi read `~/.agents/skills`, whose
  entries link into that same marketplace clone, so Claude Code's update is
  the only update (rule 4).
- **Tools: a CLI and a skill.** A tool is a command on `PATH` plus a skill
  saying how to use it; every harness runs a shell. A tool that exists
  only as an MCP server (byob, the logged-in browser) is configured in every
  harness.
- **Harnesses: same turn, three engines.** `claude_code` (default), `pi`,
  `codex`, picked per task (`--harness`). Each gets the project's
  `CLAUDE.md`/`AGENTS.md`, the skills, the operator's MCP servers and Valor's
  accounts (rule 1), every model call through the gateway.
- **RSI: tasks, not a controller.** Valor improves a skill with a task on
  `yudame/skills` like any project; the merge reaches every harness on every
  Mac. The old improvement controller ran 375 times in four weeks, none
  for Tom (old-system usage count, 2026-10-16), and is not rebuilt.

## Steps

1. **Done 2026-10-16.** Claude turns load the operator's MCP servers;
   a turn drove byob (82a1165d2). `projects/skills.toml`; its suite
   `tests/check_skills.py` (276ba62).
2. **Skills portable** (a Valor task on `skills`): skills called by name
   in plain words; a sequential fallback wherever a subagent is used;
   actions, not Claude tool names; the `$ARGUMENTS` fallback;
   "`AGENTS.md` or `CLAUDE.md`"; space-separated `allowed-tools`;
   `agents/openai.yaml` for the five no-implicit skills; Claude-only skills
   labelled `compatibility`; the do-sdlc `tom:` bug; `install.sh` linking
   into `~/.agents/skills`.
3. **Pi** loads skills and context files (6ab8fb774). MCP needs Pi 1.3;
   the pin (0.73.1) moves up so Pi turns reach byob.
4. **Codex** wrapper (8c25acf06), ported from the old adapter
   (`agent/session_runner/harness/codex.py`, rule 10): `codex exec --json`
   on the gateway's Responses route, its own `CODEX_HOME`, `CLAUDE.md` as
   fallback instructions, its rollout transcript collected.
5. **Machines:** `install.sh` on Tom's Mac and other people's. Not on the
   Valor Macs: their turns take the skills from `settings.plugin_dirs`, and
   a second copy in `~/.agents/skills` would load each skill twice (rule 4).
6. **Old tools Tom used** (counts, 2026-09-10 to 10-14): byob (step 1),
   the do-* skills (in `yudame-dev`); `officecli`, `tts`, `deck_video`,
   calendar as CLIs with skills. `roles:prime-pm-role` and
   `prime-teammate-role` were the old session runner's own prompts, loaded
   for every Telegram session; the persona and the response shape do that
   job, so they are not ported. The rest had no use in the window; Tom sees
   that list (rule 2).

## Done

Tom has had one real task on a real project carried through Codex and one
through Pi, each using a yudame skill, recorded with the date (rule 5).
