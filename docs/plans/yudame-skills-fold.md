# #yudame-dev over yudame/skills too

Tom, valor team chat, 2026-10-29 08:09 UTC: "with multi-repo support for
Valor projects, let's fold the yudame-skills repo into the same project as
studio tools and yuda.me site".

Done when Tom asks for a skills change in #yudame-dev and a task starts on
yudame/skills.

## Where a listed project's task runs (settled from the code)

`task.start` from a chat session starts its child in the session's own
kernel (`tools/chat_tasks.py` `_child_project`; nothing in provisioning
reads `machine`), so a #yudame-dev task runs on the Pirate whatever the
listed spec's `machine` says. On the Pirate, `board.board_spec` finds the
listing project through `Spec.here()`, so skills' tasks show on that board.
A spec's `machine` is read only by `intake` (its chats), `routines.task_runner`
(its routines), `permissions` (Slack chats), `memory.import_old` (a one-off
import) and `board.owe` (its board). skills has no chats, no board and no
routine. projects/README says a listed project sits on the same machine as
the project listing it, and an edit to a spec has to keep that true. So
skills moves to the Pirate.

What stays on the Cowboy and does not read skills.toml: every Mac's yudame
and yudame-dev plugins, which the Claude Code marketplace keeps current
(`settings.plugin_dirs`, docs/harnesses.md); the 02:00 verify routine
(project valor); the 03:00 backup. A skills task started from a Cowboy chat
still runs on the Cowboy, as above.

Checked already: the Pirate's checkout is at main's head (d3bea323b) and
`git ls-remote` reaches yudame/skills.

## What is built

1. Private specs (`~/Desktop/Valor/projects/`, no commit; docs/plans/private-projects.md):
   - `yudame.toml`: `projects = ["studio-tools", "skills"]`; the comment
     above `chats` names skills (Tom, 2026-10-29); #yudame-dev's room line
     also names the Yudame Claude Code plugins (yudame/skills).
   - `skills.toml`: `machine = "Valor the Pirate"`, with a comment saying
     #yudame-dev works on it from the yudame spec's list (Tom, 2026-10-29).
   - Load both with `Spec.load` and confirm each listed project loads and
     sits on the yudame spec's machine. The diff and the result go in the
     delivery.
2. Docs: docs/bridges/slack.md "A channel over several repositories" names
   the skills plugin repository as well. The skills.toml header comment
   already says a merge there changes every Mac's harness, so a reader of
   the spec sees the stakes.
3. On the Pirate, over `ssh -o BatchMode=yes valorengels@valor-the-pirate`:
   clone yudame/skills to a scratch dir, run `python3 tests/check_skills.py
   <junit>` there and confirm it exits 0 and writes the report, then run
   `git push --dry-run` to a throwaway branch to confirm the Pirate can push.
   Delete the scratch dir.
4. Taking effect: specs are read fresh on every load and the session's
   `Repos:` line is rendered each turn, so nothing needs a restart. Confirm
   the Pirate sees the edited files through iCloud (read through Valor.app,
   since plain ssh can't read ~/Desktop). Restart its kernel only if it
   turns out to cache them.

## Out of scope

Chats of skills' own; changing the Slack channel's own topic; any code
change. If the code turns out to need one, it is a separate fix with a test.

## Tests

No code changes, so there is no new unit test. The evidence:
- `Spec.load` on the edited yudame.toml and skills.toml: yudame lists
  both projects, and each one loads with `machine` equal to the yudame spec's.
- `tasks.repos("yudame")`, run against the real folder, lists yuda.me,
  studio-tools and skills with their repositories (this is the session's
  Repos line).
- `board.board_spec("skills")` with this Mac set to the Pirate resolves to
  the yudame spec, so skills' questions reach #yudame-dev's board.
- On the Pirate: the skills suite passes and writes its JUnit file, and a
  dry-run push succeeds.
- Full suite stays green after the doc edit.
- Live, still open after merge: Tom asks for a skills change in #yudame-dev
  and a task starts on yudame/skills.
