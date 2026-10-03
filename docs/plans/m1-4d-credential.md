---
tracking: none
slug: m1-4d-credential
type: build
status: building; critique rounds spent (2 of 2, both revise, every finding built in)
critique_rounds: 2
review_rounds: 2
---

# 1.4d in full: the GitHub credential, transcripts, performers per task

Task 1.4d of [m1-4-checks.md](m1-4-checks.md), milestone 1.4 of
[valor-rebuild.md](valor-rebuild.md). The outline is m1-4-checks.md's
"1.4d outline" and its Project specs section (the merge-target list); this
plan lifts them and settles what they leave open. It merges after 1.4b
(the waves table in `.claude/skills/build/SKILL.md`) and depends on 1.4s
([m1-4s-signal-reads.md](m1-4s-signal-reads.md)) for `open_turn_file` and
`open_turn_dir`: it builds against that plan's text and keeps one helper
when the two meet at merge. No migration: new rows are new event types,
and transcripts are a new document kind in `documents`.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task hands the kernel a
credential that can write to every repository Valor's GitHub account can
write to, decides where a merge may land, changes the broker's performer
contract and every caller of `request`, `release`, and `reconcile`, and
copies files a turn wrote into the store. A mistake leaks the token to a
turn, pushes to `main` of `tomcounsell/ai`, pushes one task's commit into
another task's origin, or copies a file the turn could not read itself.

## The Done items it closes

From valor-rebuild.md, 1.4:

| Done item | What closes it here |
|---|---|
| `tools/push_branch.py` gains a GitHub credential held by the kernel and never by a turn, so a released merge reaches the rebuild branch on GitHub | `github-key` copies the token into the kernel key directory; the `merge` performer of a task with a kernel mirror sends it as a pinned header through a per-call config file, only to a granted (URL, branch) pair; the live push at rollout lands a commit on `valor/push-check` of `tomcounsell/ai` through the same performer, and `holds` confirms it |
| Transcript copies kept in the store with a digest | `core/transcripts.py` copies each turn's session file and its subagents' files, as base64 of the raw bytes, into `transcript` documents, and `turn.ended` records each file's SHA-256, size, offset, and `prefix_changed` |
| Absorbs: the broker's synchronous `perform` (made awaitable) | `perform`, `lookup`, and `refuse` are `async`; git runs in a worker thread |
| Absorbs: performers registered in a module-global dict (keyed per task) | `broker.PERFORMERS` and `broker.register` are deleted; the composition root builds a `broker.Performers` per task and passes it to every broker call and to `tasks.dispatch` |

Also from the outline: the merge-target list, and `merge_url` honoured by
`start --project`.

## Threat model

What a turn controls:

- **Its own clone**: config, hooks, refs, objects, and the commits it
  offers. For a `--workspace` task the clone is the workspace itself. It
  never controls the kernel mirror or the spec's cache clone.
- **Its Claude Code config directory** (`state/work/claude`, or
  `checks/<name>/claude` for a fresh session) and everything under it: any
  byte in any file, and any entry type, including a symbolic link (at the
  root `claude` itself too), a hard link to any file on the same volume
  (the kernel key directory is on it), a FIFO, or a directory where a file
  is expected. It cannot rename the directory holding `claude`
  (`state/work`, or the check directory), since that directory's parent is
  not writable to it.
- **Spec edits through candidates**: a candidate may change
  `projects/valor.toml`, and once Tom merges it later tasks read the edit.
- **Process listing**: a running turn can read other processes' arguments
  (`pgrep -lf` works under `sandbox-exec`) and, for non-platform binaries,
  their environment (`ps -E`).
- **Loopback ports**: a turn may listen on its dev ports on `127.0.0.1`.

What the kernel must never do:

- Put the token, or any part of it, in argv, a process environment, a
  ledger row, a document, an exception message, or a log line.
- Put the token in any path a sandbox profile allows; the kernel key
  directory is denied by every profile and named in no turn's environment.
- Hand the credential file to git running in a repository a turn writes:
  only a task with a kernel mirror gets it, for git run in the mirror.
- Send the token on `push_branch`, to a pair not in the merge-target list,
  to the remote's default branch, or over plain `http`.
- Write a granted URL into a config file without checking its shape.
- Store the contents of anything under a turn's config directory but a
  regular file with one link, reached without following a link from a
  directory the turn cannot replace; or take the session id from the turn.
- Let a transcript decide anything, or keep `turn.ended` from being written.
- Let a task's performers push to another task's origin.

## Design

### The credential (`core/credentials.py`, `core/settings.py`, `core/__main__.py`)

- **The token.** Valor's account `valorengels` holds a classic token with
  the `repo` scope, expiring 2026-12-31 (Tom's answer to m1-4-checks.md
  Questions, 3), durable in the vault `.env` as `GITHUB_PUSH_TOKEN` and in
  1Password as "GitHub Push Token". It reaches every repository the
  account can write to, so the merge-target list and the default-branch
  refusal below, and a ruleset on `main` with `valorengels` off the bypass
  list once Tom adds it, keep it to the rebuild branch.
- **`python -m core github-key`** calls
  `credentials.copy_keys(settings.vault_env, settings.github_keyfile,
  ["GITHUB_PUSH_TOKEN"])`, the code `judgement-keys` uses, and prints
  `written`, `kept`, or `missing`, never a value. `github_keyfile` is a new
  setting, `github-keys` in `pg_passfile`'s directory, mode 600.
  `read_key(path, name, command)` names the command to run in its
  `MissingKey` text (`judgement-keys` or `github-key`).
- **The header.** For each git call the `merge` performer makes against a
  non-local target (the push, `holds`, the default-branch read), the
  kernel writes `github-push-<random>.gitconfig` in the key directory,
  opened with `O_CREAT | O_EXCL | O_NOFOLLOW`, mode 600, holding exactly:

  ```
  [http]
      followRedirects = false
  [http "<the granted URL>"]
      extraHeader = Authorization: Basic <base64 of x-access-token:TOKEN>
  ```

  It is passed as `GIT_CONFIG_GLOBAL` for that call through `_git`'s
  `extra_env` and deleted in a `finally` in the same worker thread when
  git exits. Git matches `http.<url>.*` against the URL, so the header
  goes to that repository URL only; with redirects and proxies pinned off
  (in the file and on the command line) git never carries it elsewhere. It is a pinned header (helpers stay
  refused, the PATH stays system-only), delivered as a file because
  arguments are readable by a running turn (m1-4-checks.md Questions, 4).
- **The URL's shape.** `targets.url_ok(url, loopback=False)` parses the
  URL and requires `https://<host>/<path>`: host of letters, digits, `.`
  and `-`; path matching `[A-Za-z0-9._/-]+` with no `.` or `..` segment;
  no port, user info, query, or fragment. `merge-target add` and the
  header writer call it with `loopback=False`, so only `https` is ever
  granted or written. Tests write grant rows directly and pass
  `loopback=True` to the writer to reach `http://127.0.0.1:<port>/...`.
- **Crash leftovers.** Before writing its own file, the writer removes
  `github-push-*.gitconfig` files older than twice `git_timeout_s`; every
  kernel git call is killed at `git_timeout_s`, so none belongs to a live
  call.
- **`git.py`**: `push`, `remote_head`, `remote_sha`, and `holds` take
  `credential: Path | None`; with it, `GIT_CONFIG_GLOBAL` is that file
  instead of `/dev/null`. `hostile` already refuses `http.`, `credential`,
  `url.`, and `push.` keys in the mirror's config. `remote_head` checks the
  exit code: a failed `ls-remote` raises `GitError`, an unborn `HEAD`
  returns `None`. Git's stderr, carried by `GitError`, never echoes a
  request header; a test checks it.
- **Rotation.** Tom creates a new token and replaces it in the vault
  `.env`; the build session runs `python -m core github-key` (it prints
  `written`); Tom revokes the old one. A push GitHub refuses (git's stderr
  says "The requested URL returned error: 401" or "...: 403", or names
  failed authentication; never a bare status code, which a SHA can hold)
  fails the merge with "GitHub refused the credential; rotate
  it", and the next run requests the merge again.
- **A missing key** fails the merge with the `MissingKey` text and pushes
  nothing. It never refuses `start`.

### The merge-target list (`core/targets.py`, new)

- Rows on the global stream `merge_targets` (a fixed task id, as
  `corrections`, `guards`, and `judgement` are): `merge_target.granted`
  and `merge_target.revoked`, each `{url, branch, note, provenance}`. A
  pair is granted when its latest row is a grant. Rows never change.
- **`python -m core merge-target add URL BRANCH --note TEXT`** writes a
  grant with `provenance.by = "tom"`, `role_played: false`, always; no
  `--by` or `--role-played`, like `grant`. It refuses a URL `url_ok`
  refuses and a branch `git check-ref-format --branch` refuses.
- **`merge-target remove URL BRANCH --note TEXT --by NAME`** writes a
  revocation; it takes authority away, so anyone running the kernel may,
  and `--by` is required, so the row names who ran it.
  **`merge-target list`** prints the granted pairs.
- **`targets.local(url)`**: no scheme and no `host:path` form is a local
  path, always allowed; every other target must be granted.
- **`targets.check(conn, url, branch)`** returns a refusal reason or
  `None`, reading only the ledger, at `start`, the merge request, and
  release.
- **The default branch.** For a non-local target the kernel reads the
  remote's `HEAD` and refuses a target branch equal to it, even if
  granted, so `main` of `tomcounsell/ai` is never a target. At `start` the
  read is anonymous, inside `_provision` right after `_cache` (which reads
  the same public remote anonymously), run in the cache clone; a refusal
  there removes the half-built workspace as any provisioning failure does.
  Inside `perform` it runs immediately before the push, in the mirror
  with the credential for a task with a mirror, in the workspace without
  one for a `--workspace` task. A failed read refuses with "cannot read
  the remote's HEAD: <stderr>"; an unborn `HEAD` with "the remote's HEAD
  names no branch". The read never runs inside a ledger transaction. If
  Tom makes the rebuild branch the default, every merge to it is refused;
  docs/machine.md says so.

### `merge_url` honoured (`core/workspace.py`, `core/__main__.py`, `core/tasks.py`)

- `start --project NAME`: when the spec has a `merge_url`, `__main__`
  runs `targets.check(merge_url, target_branch)` before `provision` and
  refuses, naming the `merge-target add` command; `_provision` then reads
  the default branch (above) and sets `origin_url = spec.merge_url`. A
  spec without `merge_url` keeps the local origin.
- `start --workspace PATH` keeps `origin_url` from the workspace's own
  remote. `tasks.resolve_workspace` catches the `GitError` the checked
  `remote_head` raises and refuses with `WorkspaceRefused` carrying git's
  stderr. The task has no mirror, so its merge never gets the credential.
- `push_url` stays the local `origin.git` for provisioned tasks, so their
  `push_branch` never reaches GitHub; `push_branch` never takes a
  credential in any task. Only the merge pushes to `origin_url`, from the
  mirror, and only the commit and branch Tom's approval binds.
- `Merge(repo, *, url, branch, credential)` is built from the Brief
  (`url=b.origin_url`, `branch=b.target_branch`), and `Merge.refuse`
  refuses a payload whose `url` or `target_branch` differs from them, or
  that `targets.check` refuses.
- `workspace.py`'s refusal for a source that needs a credential to fetch
  says "private repositories are not fetched".
- Side effect: `judgement_sites.project_name` derives the judge's
  `project` from `origin_url`, so for `--project valor` it reads `ai`
  instead of `origin`.

### Performers per task, awaitable (`core/broker.py`, `tools/push_branch.py`)

- **`broker.Performers(*performers)`** holds a task's performers by
  action type, with `get` and `offered()`. The protocol:

  ```python
  async def perform(self, action, key) -> dict
  async def lookup(self, action, key) -> dict | None
  async def refuse(self, conn, action) -> str | None   # optional
  ```

  `refuse` takes `conn` so `Merge.refuse` reads the target list inside
  the request's and the release's transaction under the task lock.
- **Signatures**: `broker.request(conn, performers, task_id, action)`,
  `broker.release(conn, performers, effect_id)`,
  `broker.reconcile(conn, performers, effect_id, settle_after_s=None)`,
  `runs.run_turn(..., offered=())` handing it to `tasks.dispatch(...,
  offered=())`. `PERFORMERS`, `register`, and module-level `offered` are
  deleted.
- **The composition root**: `__main__._performers(b)` returns
  `Performers(PushBranch(b.workspace, url=b.push_url or b.origin_url,
  protected=b.target_branch), Merge(b.mirror or b.workspace,
  url=b.origin_url, branch=b.target_branch, credential=settings.github_keyfile
  if b.mirror else None))`. `release` reads the effect's task from the
  held row and builds that task's performers. The router's `Context`
  gains `performers`, built per task by the composition root; runners pass
  it to `session` (turn effect requests), `verdicts.ensure_merge`, and
  `broker.reconcile`, and session and fresh pass `performers.offered()` to
  `run_turn`.
- **`tools/push_branch.py`**: `perform` and `lookup` run in
  `asyncio.to_thread`, setting `git.deadline` inside the thread function
  (a contextvar; `to_thread` copies the context). `Merge` writes and
  removes the config file around each git call, all inside the thread.
- **Cancellation.** `to_thread` cannot stop a running thread. A cancelled
  release leaves the push running to its git deadline, the config file in
  place until git exits, and an intent with no outcome; the effect lock
  frees, and `reconcile` settles it (it waits `reconcile_after_s`, twice
  the git deadline, before it concludes `failed`).
- **The intent carries the action.** `broker._intent` writes
  `{effect_id, idempotency_key, approval_id, action_type, target,
  payload, payload_sha256, effect_class}`, so `reconcile` rebuilds what to
  look up from the intent row alone; for an intent written without those
  fields it reads them from the effect's `effect.held` row, and with
  neither it concludes nothing (a propose-class intent never raises
  `KeyError`). `held_task` answers for an effect with only an intent.
- **`tests/performers.py`**: `WorkspaceWrite` and `OutboxAppend` become
  async; tests that called `broker.register` build a `Performers`.

### Transcripts (`core/transcripts.py`, new; `core/runs.py`; `harnesses/claude_code.py`)

- **The session id is the kernel's.** `workspace_turn` passes
  `--session-id <uuid>` (a new UUID) on a new session and keeps
  `--resume <id>` on a resumed one, so the id is known before the turn
  starts and stdout decides nothing; a stopped or crashed turn's
  transcript is found the same way. The resumed id comes from the fold,
  which takes it from an earlier `turn.ended` and checks it is a UUID.
- **Where.** `TurnCommand` gains `transcript: Transcript | None`
  (`anchor`, the directory holding the config root, which the kernel made
  and the turn cannot replace: `<task>/state/work` or
  `<task>/checks/<name>`; `root`, the name `claude`; `session_id`).
  `turn` (no persistence) gives none.
- **How it reads.** The kernel opens `anchor` itself, then walks `claude`
  and `projects` with 1.4s's `open_turn_dir` (`O_NOFOLLOW | O_DIRECTORY`
  at each step, a link at the root refused), lists `projects/` through
  its descriptor, and takes the one child directory that holds
  `<session_id>.jsonl`, never rebuilding Claude Code's cwd encoding (it
  shortens and hashes long paths). Each file is opened with 1.4s's
  `open_turn_file(dir_fd, relpath) -> (fd, why)`: a regular file with one
  link that is not sparse (`st_blocks * 512 < st_size` is refused), or
  nothing and a reason, or `(None, None)` when it does not exist. The
  kernel streams it from that descriptor, from its offset, one chunk at a
  time, each read, digest, base64 encoding, and JSON dump in a worker thread
  off the event loop. Subagent files are the regular `agent-*.jsonl`
  entries of `<session_id>/subagents/`. A file is named by its path
  relative to `projects/<dir>`, such as
  `<session>/subagents/agent-x.jsonl`, so different sessions' files never
  share a delta chain.
- **When.** In `runs.run_turn`, after `reap` (no process of the turn is
  left to write), stopped turns included, before `turn.ended`.
- **What is stored.** Documents of kind `transcript`, id
  `<turn_id>/<name>/<n>`, body `{turn_id, name, offset, chunk: n,
  base64}`: base64 of the raw bytes stored, so any byte stores exactly and
  can be checked against the digest. Chunks are 64 MiB of raw bytes
  (base64 about 85 MiB, under the `jsonb` string limit of 2^28 bytes);
  no size cap. `turn.ended` gains `transcript: {files: [{name, documents,
  sha256, bytes, offset, prefix_changed}], skipped: [{name, why}]}`, or
  `no_transcript: reason`.
- **Failure.** Documents are written in their own transaction before
  `turn.ended`; if it or the read fails, it rolls back and `turn.ended`
  carries `no_transcript: <error>`.
- **Deltas.** For each file the kernel finds the task's last `turn.ended`
  recording the same name; when the first `bytes` bytes now hash to its
  `sha256`, it stores the bytes from `offset = bytes`; otherwise the whole
  file with `prefix_changed: true`, the sign that something rewrote it.
  `sha256` and `bytes` are always the whole file's, and joining a name's
  documents from its last whole copy gives bytes with the latest digest.
- **Fresh sessions** run through `run_turn` too, from their check
  directory.

### Docs fixed in the same build

- `docs/machine.md`: the Git hosting tokens row (where the token lives,
  `github-key`, the per-call file, the merge-target list) and the
  default-branch refusal.
- `docs/data.md`: the `transcript` document kind and the `merge_targets`
  stream's rows.
- `docs/harnesses.md`: the transcript paragraph, and `--session-id`.
- `docs/architecture.md`: the records table's transcript row and where a
  merge lands.
- `core/README.md`: `github-key`, `merge-target`, transcripts, per-task
  performers.
- `projects/valor.toml`: the header comment on `merge_url`.
- `docs/plans/m1-4-checks.md`: the 1.4d outline matches this plan.

## Tech debt absorbed

- The synchronous `perform`, which blocks the gateway's event loop.
- Performers in a module-global dict, where the last `register` wins.
- `Provisioned.origin_url` always the local origin, and the comments
  saying so.
- `remote_head` ignoring git's exit code.

## Left out

- Fetching private repositories at provisioning; `tomcounsell/ai` is
  public (default branch `main`).
- At takeover, a released merge that touches `core/` pulling the kernel
  checkout, migrating, and restarting (valor-rebuild.md, Execution).
- The ruleset on `main`: Tom's to add on GitHub; the kernel does not
  create or check it.
- A credential for `--workspace` tasks' merges, and any push but the merge.
- Client project targets (milestone 2); they use the same list.
- Searching or summarising transcripts.
- Bridge performers; they use the same `Performers` shape.

## Tests

Against the builder's own `VALOR_TEST_DB`, never `valor_rebuild`. The
smart-HTTP server is a loopback `http.server` in the test process running
the trusted git's `git http-backend` as CGI for one bare repository; it
answers 401 to a request without the expected `Authorization` header, can
redirect to a second loopback server, and logs every header.

**Credential**: a released merge to a granted loopback target lands with
the header, from the mirror; with the key file missing the merge fails
with the `github-key` text and no request carried a header; a wrong token
fails with "GitHub refused the credential; rotate it", and a rejected push
whose SHA holds "403" fails with git's rejection, not that; `push_branch` in the
same task sends no header and lands on the local origin; a `--workspace`
task whose origin is a granted loopback URL merges with no header (401),
and its `push_branch` sends none; a mirror holding an `http.` or `url.` key
is refused before any request; on a redirect the second server never
receives the header (left to itself, git 2.39.5 follows the first redirect,
`http.followRedirects=initial`, and sends the header there; the header file
and the command line pin `followRedirects=false`, so the push fails at the
redirect); a mirror whose config sets a redirect, a proxy (each general and
for the URL), a URL rewrite, a push URL, a second header, or a credential
helper sends nothing, and with `git.run`'s refusal switched off still sends
no header anywhere but the granted URL; no leak:
while the server holds
a push open, `ps -E -ww` from the kernel and `pgrep -lf` and `ps -E -ww`
from a probe under `turn.sb` show no process whose arguments or environment
contain the token, its base64 form, or the file's contents, and the probe
cannot open the file or `github-keys`; afterwards no config file remains,
and the token, its base64 form, and its SHA-256 appear in no `events`
payload, no `documents` body (each `transcript` document's base64 decoded
before searching), and no captured log or exception; a leftover older than
twice `git_timeout_s` is removed and a younger one kept; `github-key`
prints `written`, `kept`, then `missing`, never the value.

**Merge targets**: `start --project` with an ungranted pair refuses,
naming `merge-target add`; granted, `origin_url = merge_url`; a granted pair
naming the remote's `HEAD` branch is refused at `start` (and the workspace
removed), and with `HEAD` moved onto it after `start` the merge fails before
any push; an unreadable remote and an unborn `HEAD` refuse with their own
texts; `start --workspace` with an unreachable origin refuses with
`WorkspaceRefused`; a pair revoked after the hold is refused at release; a
candidate's spec edit changes nothing for its task and a later task from the
edited spec is refused; a merge payload whose `url` or branch differs from
the Brief's is refused by `Merge.refuse`; `merge-target add` refuses
`http`, `ssh`, user info, a port, a query, a fragment, `"`, `\`, `]`, a
newline, a space, a `..` segment, and a malformed branch, and has no
`--role-played`; a local origin needs no grant, and its symbolic `HEAD`
does not trigger the default-branch refusal.

**Performers**: two tasks with different origins, released concurrently
(`asyncio.gather`), each land only on their own origin; a timer task ticks
while the server holds a push open; `git.deadline` applies inside the
thread; a release cancelled mid-push leaves an intent that `reconcile`
settles `done` once the push lands, and the config file is gone; `reconcile`
uses the task's own performers; a propose-class perform cut off after its
intent is settled `done` from the intent row alone, an older intent reads
its action from `effect.held`, and an intent with neither concludes
nothing; `dispatch` lists the task's offered types;
the existing suites pass with per-task `Performers`.

**Transcripts**: a scripted turn with a session file and two subagent
files gives three documents whose decoded bytes and digests match; a
resumed turn stores only the appended bytes, and the joined bytes hash to
the latest digest; an edited earlier line gives `prefix_changed`; a NUL
byte and a delta splitting a multibyte character round-trip; a failing
document insert leaves no documents and `turn.ended` with
`no_transcript`; a file larger than one chunk (chunk size small in the
test) joins back; a hard link to a scratch secret as the session file, and
as an `agent-*.jsonl` file, stores nothing for it with "has 2 links"; a
symlink as the session file, as `projects/<dir>`, and as the root `claude`
stores nothing with a reason; in each case the secret's bytes are in no
row and no decoded document; a FIFO or a directory named `agent-x.jsonl`
is skipped with a reason and does not block; a sparse `agent-*.jsonl`
(1 PiB apparent size) is skipped as sparse; every read runs off the event
loop; a stopped turn's transcript
is copied; `workspace_turn` passes `--session-id` on a new session and
`--resume` on a resumed one. Live (`VALOR_LIVE=1`, metered, expected about
$0.30): a real turn that starts a subagent, then one resumed turn: the
subagent's file is copied with its own digest, and the same
`<session_id>.jsonl` grew, stored as a delta.

**Live push at rollout** (`VALOR_LIVE=1`, `VALOR_LIVE_GITHUB=1`, no model
spend): with the real key file and `https://github.com/tomcounsell/ai.git`,
`valor/push-check` granted, `Merge` pushes from a kernel-owned bare
repository a commit whose parent is that branch's head (or the rebuild
branch's head when it does not exist), and `holds` confirms it.

## Files it changes

| File | Change | Also changed by |
|---|---|---|
| `core/broker.py` | `Performers`, async protocol, signatures | 1.5, 2.1 |
| `core/git.py` | `credential` parameter; `remote_head` exit code | 1.4b, 1.4s |
| `core/credentials.py` | header file writer, leftovers, `read_key` command | |
| `core/settings.py` | `github_keyfile` | 1.4b, 1.4s, 1.5 |
| `core/targets.py`, `core/transcripts.py` | new | |
| `core/runs.py` | transcript copy, `TurnCommand.transcript`, `offered` | 1.5 |
| `core/tasks.py` | `dispatch(offered=)`, `resolve_workspace` refusal | 1.5, 2.1 |
| `core/workspace.py` | `merge_url`, default-branch read, refusal text; `read_turn_file` and `open_turn_dir` as 1.4s defines them | 1.4b, 1.4s |
| `core/__main__.py` | `github-key`, `merge-target`, `_performers`, wiring | 1.4b |
| `core/router.py`, `core/session.py`, `core/verdicts.py`, `core/fresh.py`, `core/machine.py` | performers threaded; the fold's resume id checked | 1.4b, 1.4s, 1.5 |
| `core/judgement_sites.py` | none (side effect above) | 1.4b |
| `tools/push_branch.py`, `harnesses/claude_code.py` | async `Merge` with credential; `--session-id`, transcript source | |
| `tests/` | `test_credential_push.py`, `test_targets.py`, `test_transcripts.py`, a smart-HTTP fixture; every test that registered a performer | 1.4b, 1.4s |
| docs | listed above | 1.4b, 1.4s |

1.4b and 1.4s merge first. Whichever helper lands first is kept; this task
rebases onto it.

## Rollout

1. Merge after 1.4b and 1.4s, alone. In the kernel checkout: `uv sync`.
   No migration.
2. The build session runs `python -m core github-key` from the kernel
   checkout; it prints `written`.
3. **Tom**, from the kernel checkout:
   `python -m core merge-target add https://github.com/tomcounsell/ai.git
   <rebuild branch> --note "the rebuild's merges"` and the same for
   `valor/push-check` with `--note "1.4d live push"`.
4. The build session runs the live push test, then `python -m core
   merge-target remove https://github.com/tomcounsell/ai.git
   valor/push-check --by valor --note "live push done"`. The branch stays
   on GitHub for Tom to delete or keep.
5. **Tom**, on GitHub, when he can: a ruleset on `main` restricting
   updates, `valorengels` off the bypass list. Nothing waits for it.

## Decided by default

Each is reversible and was decided by the build session:

- A per-call config file, not a static one: scoped to each push's exact
  URL; nothing on disk outside a call but the key file and crash
  leftovers, which the next merge removes.
- Basic auth with the user `x-access-token`; GitHub accepts a classic
  token with any user name, and the live push confirms it.
- The default-branch read at `start` (anonymous) and inside `perform`,
  never inside `refuse`, so no network call runs under the task lock.
- `--workspace` tasks merge without the credential (the lead's call).
- `merge-target remove` exists and anyone may run it.
- Performers passed explicitly through the router's `Context`.
- `refuse` is async and takes `conn`.
- The kernel chooses the session id (`--session-id`) and finds the
  project directory by listing.
- No transcript from a turn without its own config directory.
- Base64 of raw bytes, one document per file per 64 MiB chunk.
- The live push branch `valor/push-check`, kept afterwards.
- No redirect and no proxy on a call carrying the credential:
  `http.followRedirects=false` in the header file, and on the command line
  `-c http.followRedirects=false` and `-c http.proxy=`, each also at the
  URL's own scope (`git.credential_pins`). The command line outranks the
  repository's config, and a key scoped to the URL outranks a general one.
  The threat model says the token goes nowhere but the recorded remote;
  git's default follows the first redirect, and a proxy key sends the
  header to the proxy, as the tests show. The pins make the code do what
  the threat model states: they configure the transport, as dropping
  `Authorization` on a cross-host redirect does on the gateway, inspect
  nothing about the work, and refuse nothing a turn asked for. A fix, not
  a guard. Of the other keys a repository's config could set, a URL
  rewrite, a push URL, a second `extraHeader`, and a credential helper do
  not move the header (the header is scoped to the granted URL, the push
  names its URL, and the helper is pinned empty); the TLS keys
  (`http.sslVerify`, `http.sslCAInfo`, `http.curloptResolve`) are not
  pinned, since pinning a CA path would replace the system's trust store,
  and are covered as every `http.*` key is: `git.run` refuses a
  repository whose config sets one, and the mirror is the kernel's.

## Critique round 1 (of 2): revise

Every finding accepted: the credential goes only with a mirror and only
`--project` honours `merge_url` (1); raw bytes as base64, and a failed copy
still records `turn.ended` (2); no size cap (3); the GitHub prefix and the
start-time key check dropped as guards with no incident (4;
`followRedirects` is a transport fix, under Decided by default); the URL's shape checked before it is written (5); the
`start` read given a repository and `remote_head` an exit code (6); the
outline updated and private repositories left out (7); cancellation,
leftovers, a default-branch change, and the cwd encoding handled (8); the
Done evidence is the live push (9); Tom's two items are rollout steps (10).

## Critique round 2 (of 2): revise

Every finding built in:

1. **Hard links.** Transcript files go through 1.4s's `open_turn_file`
   (a regular file with one link); 1.4s is a dependency; hard-link tests
   for the session and a subagent file.
2. **The config root.** The walk starts at the directory holding
   `claude`, which the turn cannot replace, with `O_NOFOLLOW` at each step;
   the kernel chooses the session id (`--session-id`, or the resumed id
   from the fold). Root-symlink test.
3. **Leak tests.** Each transcript document's base64 is decoded before the
   token and the secret are searched for.
4. **Stopped turns.** Copied through the kernel's session id; tested.
5. **`Merge.refuse`.** Built with the Brief's `origin_url` and
   `target_branch`, and refuses a payload that differs.
6. **`start --workspace`.** `resolve_workspace` catches the `GitError` and
   refuses with `WorkspaceRefused`; tested.
7. **The `start` read.** Anonymous, inside `_provision` after `_cache`;
   `targets.check` in `__main__` before `provision`; a missing key never
   refuses `start`.
8. **Loopback.** `merge-target add` accepts only `https`; tests write
   grant rows directly and pass `loopback=True` to the writer.
9. **Small edits.** `read_key` names its command; `docs/data.md` added;
   the outline's transcript text matches; the live test resumes once;
   names are paths under `projects/<dir>`; the `project_name` side effect
   named; the `--workspace` perform-time read runs without the credential.
