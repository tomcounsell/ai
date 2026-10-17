# projects

Project specs: one TOML file per repository the kernel provisions
workspaces for (`python -m core start ... --project NAME`).

## Scope

- What the kernel needs to make a task's workspace runnable and to check
  it: the repository, its kind (`python-uv`, `django`, `node`, `plain`),
  the services its tests need (`postgres`, `redis`), extra Postgres roles, the extensions the app's tests create (`extensions`, made by the superuser in the task's `template1`, so the app role holds them without being a superuser),
  the setup commands, the suite and lint commands, and the environment.
- The suite command is the kernel's, never the candidate's: a command the
  candidate chose could be `true`. A spec is read once at start and copied
  into the task's Brief, so editing it never changes a running task.
- Without `merge_url` the merge lands on the task's own bare origin. With
  it, the merge lands on that URL; the kernel records it, and the target
  branch, in the task's Brief at start, and no turn changes either. The
  GitHub token's own reach bounds which repositories that can be
  ([machine.md](../docs/machine.md), Where a merge lands).
- `branch` is the branch a task's clone, bare origin, and merge target use;
  without it the remote's default branch is used. A
  task started by a message has no `--branch`, so a project whose messages
  start tasks names it (`valor` names `main`); `start
  --branch` overrides it for one task.
- `chats` lists the chats whose messages start tasks under the project
  (`telegram:<chat id>`, `email:<sender address>`, `email:@<domain>` for
  every sender at that domain unless an address is listed,
  `slack:<team id>/<channel or user id>`), and `machine` the
  machine whose bridges receive them; none means the default machine, so
  each chat has one owner. In a group chat or someone else's DM listed
  here, anyone's message is heard, not only Tom's (a team chat,
  [telegram.md](../docs/bridges/telegram.md)).
- `rooms` maps some of those chats to a description of what the chat is
  for (`rooms."telegram:<chat id>" = "..."`, and `rooms.email` for every
  email thread under the project), rendered on every turn of its chat
  session; Tom's own chat's is in `core/intake.py`
  ([telegram.md](../docs/bridges/telegram.md)).
- `mentions` lists those chats where only a message naming Valor, or a
  reply to his post, wakes the chat session, as the old teammate groups
  did; the rest reach it with the next.
- `knowledge_base` names the business's folder in `~/work-vault`, where its
  description lives. A working task's turns read and write that folder
  (`VALOR_KNOWLEDGE_BASE` names it to them).
- `client` names the client the project is for. Valor has one Mac per
  client, and an internal Yudame project's client is `yudame`. Every memory
  record ingested from the project's tasks carries it with the project, so
  a record marked a standard of working is recalled for other clients, and
  a record confined by a correction only for its own
  ([docs/memory.md](../docs/memory.md)). A spec without one gives its records
  the empty client.
- `{port}` and `{passfile}` in `env` become the task's Postgres port and its
  password file.

Governed by [docs/architecture.md](../docs/architecture.md) (workspace
provisioning) and [docs/harnesses.md](../docs/harnesses.md) (the workspace).

## Imports

- Read by: `core/workspace.py` (`Spec.load`), through the `projects_dir`
  setting.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Secrets of any kind.
- Client repositories' specs, until a client task first starts or a Mac's rollout routes its chats (docs/rollout.md).
