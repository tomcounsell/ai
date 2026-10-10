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
- A spec never decides by itself where a merge may land. Without
  `merge_url` the merge lands on the task's own bare origin. With it, the
  merge lands on that URL only when Tom has granted the (URL, branch) pair
  (`python -m core merge-target add`), and never on the remote's default
  branch; `start` refuses otherwise.
- `branch` is the branch a task's clone, bare origin, and merge target use;
  without it the remote's default branch is used, which a merge to a
  remote refuses. A
  task started by a message has no `--branch`, so a project whose messages
  start tasks names it (`valor` names the rebuild's branch); `start
  --branch` overrides it for one task.
- `chats` lists the chats whose messages start tasks under the project
  (`telegram:<chat id>`, `email:<sender address>`), and `machine` the
  machine whose bridges receive them; none means the default machine, so
  each chat has one owner.
- `{port}` and `{passfile}` in `env` become the task's Postgres port and its
  password file.

Governed by [docs/architecture.md](../docs/architecture.md) (workspace
provisioning) and [docs/harnesses.md](../docs/harnesses.md) (the workspace).

## Imports

- Read by: `core/workspace.py` (`Spec.load`), through the `projects_dir`
  setting.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, or approval step is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance is an `act`-class effect: the Brief carries a `governance_grant` field, default none, and a diff that adds any of the above needs Tom's tap, one approval per instance, through the same approval surface as a merge or a send. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is deleted by default. The blind verifier asks one Jev-class boolean over every diff, "does this add a check, gate, hook, round, or review step", and a yes with no grant is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Secrets of any kind.
- Client repositories' specs, until a client task first starts.
