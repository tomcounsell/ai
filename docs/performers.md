# The broker's performers

Part of [tech-stack.md](tech-stack.md), section 8.

A performer is a Python class with an `action_type`, a fixed
`effect_class`, and the coroutines `perform` and `lookup` (by idempotency
key). It may define `async refuse(conn, action)`, checked at request and
again at release. It runs in the kernel process, outside the turn's
sandbox, which is what lets it write a remote the turn cannot. Git runs in a
worker thread, so the gateway's streams never wait on a push. The composition
root builds a `broker.Performers` for each task from its Brief and passes it
to every broker call, so one task's performer never acts for another.
Status: **in use**.

- **`push_branch`** (`act`, `tools/push_branch.py`): pushes one commit to
  one branch of the workspace's origin over git, never with `--force`,
  tags, submodules, or a signature. Git is the Command Line Tools' install
  (`VALOR_GIT` overrides), checked before each call to be root's alone and
  a real install, never Apple's `/usr/bin/git` shim (`core/binaries.py`),
  with a system-only PATH, no `DYLD_*` or global config, one deadline per
  perform (git in its own process group, killed whole on timeout), replace
  refs, grafts, commit-graph, and multi-pack-index ignored, and hooks,
  helpers, pagers, and transports pinned off, and refuses a workspace whose
  own config names a program, redirects a push, sets any `push.*` or
  `http.*`, or includes other config, because that config is the turn's to write (`core/git.py`). `lookup`
  answers present (the branch holds the commit, at its tip or below),
  absent, or unknown; `broker.reconcile` uses it to settle a dangling merge
  intent: the intent row holds the whole action, so reconcile rebuilds what to look up from it. It pushes to the task's `push_url` (else the origin URL recorded
  at start), never carries the credential, and refuses the task's target branch.
- **`merge`** (`act`, `tools/push_branch.py`): the kernel's push of a
  passed candidate onto the target branch, released only when the merge
  predicate holds; no turn is offered it. It is built from the task's Brief
  and refuses a payload naming any other URL or branch. A local origin
  needs no grant and no token. A remote must be a (URL, branch) pair Tom
  granted (`python -m core merge-target add`), is never the branch the
  remote's `HEAD` names (read again just before the push), and is pushed
  from the kernel mirror with the GitHub token, which only a task the
  kernel provisioned has. Each git call against the remote gets a config
  file of its own holding one `Authorization` header scoped to that URL,
  removed when git exits, with redirects and proxies pinned off in the file
  and on the command line. GitHub's refusal of the token fails the merge
  saying to rotate it ([machine.md](machine.md), Keychain).
- **`WorkspaceWrite`** (`propose`) and **`OutboxAppend`** (`act`)
  (`tests/performers.py`), for the tests only: a file in the workspace, and
  a local outbox that stands where a bridge's send will.
