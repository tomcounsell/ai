---
tracking: none
slug: orchestration
type: build
status: waves 0, 1 and 2 built (2026-10-17); the done bar is open
critique_rounds: 0
review_rounds: 0
---

# Orchestration: one voice per chat, workers by size, the web one subagent away

From Tom's rulings of 2026-10-16 ([valor-rebuild-feedback.md](valor-rebuild-feedback.md)).
It builds on [team-chat.md](team-chat.md) and changes nothing in it. The persona
turn *is* team-chat step 2's long-lived session per chat. This plan makes Tom's DM
one more such session (step 1's "only Tom is heard there" holds), uses step 3
("One voice") as written, and adds the web agent, unlimited nesting, the Brief
reaching every subagent, and Valor's own skills.

Like [strip.md](strip.md), it is built by a lead session that fans out
subagents, with one adversarial review per wave in place of the pipeline's rounds.

## Goal

Every message that does not bind to running work is the next prompt of its chat's
session, on every channel, Tom's DM included. The session reads, answers and
dispatches. A small thing is a subagent inside the session's turn. Anything with
a deliverable or a merge is a kernel task, which reports to the session, and only
the session speaks. Only the web agent holds WebFetch and WebSearch, and any
agent at any depth can start it.

## The rules this work runs under

The strip's ten rules, with rules 1, 3, 4, 8 and 10 most often in play. On top of them:

- **Structure, not denial.** "Read and dispatch only" is the session's prompt and
  the persona, with no tool removed from the session. "Workers never reach comms"
  is routing: a worker's questions, progress and deliveries become prompts of its
  parent session, with no performer denied to a worker.
- **One guard.** The web agent is the only governance this plan adds.

## Tom's grants (verbatim)

- **Web, 2026-10-16:** "for web fetch, I recommend a dedicated subagent that must
  be used." And: "subagents can create subagents to unlimited degree. so web
  fetch is only ever another subagent away."
  - Guard `web.agent_only`: in a Claude Code turn, WebFetch and WebSearch are
    refused to every agent but `valor:web`.
  - Incident: injection through inbound and retrieved content (#3648, the old
    system's inbound injection inspection).
  - Serves Mission item 4 and the constraint **bounded authority**.
  - Granted 2026-10-16, expires 2027-01-14. It is seeded as a `guard.granted`
    row by `core/guards.py`, the same way as the pipeline's guards.
  - The web agent is a leaf: its tools are the two web tools only, so it cannot
    start another agent or run a command with what it read. That is part of the
    same grant.
- **Comms, 2026-10-16:** "other kernel tasks report to the parent Valor agent and
  are not allowed to interact with comms channels." This is built as routing in
  wave 2, not as a denial.

## Wave 0: what Claude Code 2.1.301 does (lead, 2026-10-17, $0.07 of live turns)

1. **A session-wide `--disallowedTools WebFetch,WebSearch` removes the tools from
   every agent**, the web agent included. Spawning it then fails with "would be
   spawned with zero tools". So the guard is a PreToolUse hook, not a flag.
2. **The hook sees who is calling.** Inside the web agent its input carries
   `agent_type: "valor:web"`. In the main session `agent_type` is absent. A
   `deny` holds under `bypassPermissions`, and the agent reads the hook's reason.
3. **`SubagentStart` `additionalContext` reaches every depth.** A nonce in it
   was reported by subagents at depths 1 and 2.
4. **Nesting.** With `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` unset, a depth-2
   agent has no Agent tool. Set to 1000, with
   `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` (which a workspace turn already
   sets), a chain reached depth 5 and returned in order. With background tasks
   on, the nested agents returned before their children did.
5. **Codex and Pi.** Pi offers no web tool. Codex's built-in `web_search` is
   priced (`core/settings.py`) and Codex has no subagent to hand web work to, so
   the guard covers Claude Code turns, the default harness. A Codex turn keeps
   its web search until Codex can start a web agent.

## Team-chat status this depends on

| Team-chat step | State | What this plan needs |
| --- | --- | --- |
| 1, hear the team | built (935e46889) | nothing |
| 4, the persona for groups | built (d7c7cb3bd) | nothing |
| 2, one session per chat | built (a50097858), groups; wave 2 adds Tom's own chat | done |
| 3, one voice | built (a50097858), groups; wave 2 adds Tom's own chat | done |
| 5, media; 6, the reference | not built | nothing |

## Wave 1 (touches none of team-chat's in-flight files)

**A. Valor's own plugin: the web agent, the guard and the first skill.**

- `skills/valor/.claude-plugin/plugin.json`: the plugin `valor`.
- `skills/valor/agents/web.md`: tools `WebFetch, WebSearch`; it returns findings
  as data (the answer, the quotes it rests on, the URLs), never a page, and treats
  what it reads as data.
- `skills/valor/hooks/hooks.json`: PreToolUse on `WebFetch|WebSearch` runs
  `web_only.py` under `/usr/bin/python3 -I`, and any failure exits 2, a block.
  SubagentStart runs `subagent_start.py`.
- `skills/valor/hooks/web_only.py`: allows `agent_type == "valor:web"` and denies
  everyone else, and any call it cannot read, naming the web agent.
- `skills/valor/hooks/subagent_start.py`: prints `VALOR_SUBAGENT_START` for every
  subagent but the web agent, which reads untrusted pages with the web in hand.
- `skills/valor/skills/authenticity-pass/SKILL.md`: ported from the old system as
  craft. It returns the missing markers and how to add them, with no PASS or BLOCK.
- `core/guards.py`: the `web.agent_only` entry, with its own grant date, expiry
  and provenance.

**B. The turn.**

- `core/settings.py`: `plugin_dirs` puts `<kernel checkout>/skills/valor` first.
  Codex's `link_skills` and Pi's `--skill` already read every plugin dir's
  `skills/`, so the third skill source reaches all three harnesses with no new
  code.
- `harnesses/claude_code.py`, `workspace_turn.build`:
  - sets `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`, so there is no depth limit;
  - sets `VALOR_SUBAGENT_START`, the SubagentStart JSON whose `additionalContext`
    is the dispatched text, opened by one line saying it is the Brief of the
    session that started the subagent and the subagent's task is its prompt;
  - passes `--settings '{"disableAllHooks": false}'`, so neither the project's
    settings nor a settings file the turn writes can switch the guard off.

**The review** (2026-10-17) found the plugin's two JSON files ignored by
`.gitignore` (now un-ignored), the guard failing open when the hook could not
run, a turn able to switch hooks off mid-turn, and the Brief reaching the web
agent. All four are fixed and probed live. The guard's reach is stated under
"Known and left open".

**Tests** (`tests/test_orchestration.py`):

- The hook allows `valor:web` and denies the main session and any other agent.
- The web agent's tools are exactly the two web tools.
- The ledger holds the `web.agent_only` grant with its expiry.
- A turn loads Valor's plugin first, sets the depth, and carries the Brief to
  subagents byte for byte.

After the merge: `docs/harnesses.md`'s "Gap" and "Planned" paragraphs say what
now exists, and `docs/persona.md` and `skills/README.md` likewise.

## Wave 2 (built 2026-10-17)

**C. Tom's own chat is a chat session.**

- `core/intake.py`, `_bind`: in Tom's own chat (`_toms_own`: the operator chat,
  his Telegram DM, his Slack DMs, the local chat), a message of his goes to that
  chat's session through the same `_chat` path as a team chat's, under
  `_project_for` (the project listing the chat, else `valor`), and always wakes
  it. A reply that `_replied` resolves to a task other than one of this chat's
  sessions (an older task's notice or send, another chat's session's post, or a
  notice that stays Tom's: credential, binding, rollout) keeps the reply
  binding; a reply to the session's own post goes to the session. With no
  project, the message starts a task. `_chat_start` opens with "Tom's own chat
  on <channel>, a 1:1 with him, not a team chat".
- The session is picked under the chat's lock (`open_session`) and read again
  under its tree and task locks; one that ended in between gives way to a new
  session in the same transaction.
- Stop is immediate by structure: in Tom's own chat an exact `stop` from Tom,
  top level or in reply to the session's own post, stops every open task that
  reports to the session (`tasks.reporting`), by Tom, and reaches the session as
  a steer. It never stops the session. Team chats are unchanged.
- Anyone else in Tom's own chat binds to nothing.
- Email has no session: Tom's emails each start a task.
- The session reads the whole DM, so a top-level Slack DM message carries no
  quoted posts.

**D. One voice for every task.**

- `core/notices.py`, `request` → `reports_to` → `_to_parent`: a task whose
  parent is a chat session reports to it as `child.reported`; a root task that
  is no session (the CLI, a routine, Tom's email) reports to the operator chat's
  session (`intake.operator_session`), started for it when none is open. The
  kinds in `TOMS` (credential, binding, rollout) go to Tom directly, as does a
  notice with no session to take it.
- `notices.home`: a chat session's own chat, or a session's child's session's
  chat, when it is Tom's own; else the chat of the message that started the
  task.
- `tools/chat_tasks.py`: a session answers, steers (`task.steer`) and stops any
  task that reports to it, its children and the tasks whose report is on it.
  A task waiting on a question it sent Tom directly still takes his threaded
  reply.
- `skills/sdlc/channel.md` tells a worker that a session's task leaves its
  output for the session, which tells the chat.
- Test, end to end (`tests/test_pipeline.py`): Tom's DM message, the DM
  session's turn requests `task.start`, the task builds, its checks and review
  pass, the kernel merges, the report is one `child.reported` on the session,
  and the session's next turn sends the news to Tom's DM, which owes no report.

**E. The session orchestrates.**

- `skills/sdlc/converse.md` is any chat's, Tom's own included, where it is a 1:1
  with the CEO. The session reads and dispatches: a Claude Code subagent for
  something small, `valor:web` for anything on the web, a task for anything with
  a deliverable or a merge, and `task.answer`, `task.steer` and `task.stop` for
  the tasks that report to it.
- `docs/persona.md`, "One voice that orchestrates", describes it as built.

**Tests** (`tests/test_kernel.py`): a non-reply DM from Tom binds to his DM
session and starts no task; a stranger there binds to none; a threaded reply to
an older task's question answers it, a reply to a team session's post binds to
that session, and a reply to the DM session's post reaches it; `stop` stops the
tasks reporting to the session and not the session; a root task reports to the
operator chat's session, which answers, steers and stops it; `home` for a
session and its child; a session ended after its pick gives way to a new one;
with no project, the message starts a task.

## Done

1. Tom asks in his DM for something that needs the web. The transcript shows
   `valor:web` fetching, no other agent calling WebFetch, and Tom using the answer.
2. In a team chat, a worker's question reaches the chat through the session, a
   teammate's reply answers it, and the session reports the merge. Tom judges
   it, recorded with the date.
3. The `web.agent_only` row is in the ledger with its expiry, and no other new
   governance is in the diff.

## Known and left open

- The guard covers WebFetch and WebSearch only. Other web paths stay open: the
  logged-in browser (byob), a command (`curl`, `gh api`, a script), and an MCP
  server's own fetch tool. Covering any of them would be new governance and
  would need its own grant.
- A turn can write the kernel checkout, the hook script included.
- The guard cannot write the ledger when it fires, since turns cannot reach
  Postgres. At expiry it is listed for Tom with no firing record, and he decides.
- Every root task that is no session reports to the operator chat's
  session, so one session hears the CLI's, the routines' and Tom's emails'
  work, whichever chat Tom is in.
- A `stop` in Tom's own chat stops every task reporting to that session, not
  one of them; the session stops one with `task.stop`.
- A `stop` that takes the tree locks of root tasks reporting to the session
  while the session's own are held can deadlock with a writer taking them the
  other way; Postgres ends one, and that message is bound `none` with a notice.
- `VALOR_SUBAGENT_START` puts a second copy of the Brief into the turn's
  environment (UTF-8, not escaped). That is fine while argv plus environment
  stays under macOS's 1 MB limit. Above about 10,000 characters, Claude Code
  hands the subagent a preview and a file path instead of the whole text.
