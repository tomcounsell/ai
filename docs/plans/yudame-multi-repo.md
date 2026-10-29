# #yudame-dev over yuda.me and Studio Tools

Task 1641e6e5ead7 finishes branch `valor/yudame-multi-repo` (c31827ebb,
5c1ee924f), rebased on main as d3941ab39 and 8edeb11bf.

## Where it came from: Tom's words are the bar

- Tom, #yudame-dev, 2026-10-28 08:10 UTC (1793175025.483879): "we need to
  scope this channel to several repositories including yuda.me company site
  as well as studio-tools mac app".
- The #yudame-dev session (the Pirate) proposed the branch at 08:20 and named
  the Pirate's old Swift as the second call. Tom, 09:55–09:56: "I updated
  Xcode on the pirate machine", "and yes to accepting the edits". The
  session committed both commits at 10:14 and 10:20 UTC as valorengels.
  It was never merged.
- Tom, valor team chat, 15:54 UTC: "Check the yudame multi repo branch and
  see if we can finish SDLC".

Done when Tom asks for Studio Tools work in #yudame-dev and a task starts on
yudame/studio-tools, running its suite on the Pirate.

## What is built (already, on this branch)

- `Spec.projects` lists other specs whose repositories a project's chats work
  in. `yudame.toml` lists `studio-tools`.
- `task.start` takes `"project"`: none starts under the session's
  project, as before, and a named one starts under that project. The child
  carries the session's chat as `project.chat`. `tasks.brief` keeps it through
  provisioning, and the child reads that chat (`_reading_env`). This
  changes every project's chat-started tasks: a child used to read its
  project's first chat, and now reads the chat that started it (for
  #yudame-dev's children, the channel instead of the Telegram team chat).
- The chat session's Brief gets a `Repos:` line. The board (`board_spec`,
  `snapshot`) covers the listed projects' tasks.
- `projects/studio-tools.toml`: plain repo, the Swift Testing suite
  (`--disable-xctest` so the report lands at `{junit}`), the Pirate,
  Xcode 27.1 there.
- Docs: projects/README, slack.md, telegram.md. Tests: `tests/test_projects.py`,
  plus updated kernel and pipeline asserts.

## Governance check

No limit is added. A `"project"` key was ignored before; now the child
starts under the project it names, else the session's own. Nothing checks
the name against the list: an earlier version refused an unlisted one,
which was a new refusal with no grant, so review removed it. The `Repos:`
line and the usage text steer the session to the listed ones, and a name
with no spec fails at provisioning (`Spec.load`), as any task's does.
`Spec.load` checks a `projects` entry only as a non-empty string, as it
checks `machine`.

## Remaining work

1. **Review fixes**: the builder reads the diff against the request and fixes
   what it finds.
2. **Live check before merge**: on the Pirate, clone yudame/studio-tools
   (private; the Pirate's git reaches it) and run the spec's `suite` line, so
   the JUnit report lands at the path given. The studio-tools suite must pass,
   or the first task there fails its tests.
3. **Full suite and lint** on the final head, under 5 minutes (378 passed,
   1 skipped, 135 s on the rebase).
4. **Rollout**: the merge rolls forward on the Cowboy by itself. The Pirate
   (11 commits behind main today) holds #yudame-dev's session and the
   yudame specs. Run `python -m core update --wait` there over ssh, then
   confirm its checkout holds the merge.

## Out of scope

Giving studio-tools chats of its own. Changing the Slack channel's
description. Packaging Studio Tools (yudame/studio-tools#2).

## Tests

The branch already holds tests for:

- `projects` defaulting to empty, and rejecting a bad name;
- `task.start` with no project, its own project by name, and a listed one;
- a listed child's checkout of its own repo, keeping `chat` once provisioned;
- a child reading its own chat, else the one it came from, else its
  project's first;
- every chat of a listing project getting `Repos:`, and a project without a
  list getting none;
- a listed project's question showing on the board and answered there;
- a project with its own board resolving to itself;
- the real specs: #yudame-dev covers both repositories, and every listed
  project loads and is on the same Mac.

A fix the review makes comes with its own test. Live checks: the studio-tools suite on the Pirate, and the Pirate's head
containing the merge.
