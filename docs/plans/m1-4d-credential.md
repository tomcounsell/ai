---
tracking: none
slug: m1-4d-credential
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 1.4d in full: the GitHub credential, transcripts, performers per task

Task 1.4d of [m1-4-checks.md](m1-4-checks.md), milestone 1.4 of
[valor-rebuild.md](valor-rebuild.md). The outline is m1-4-checks.md's
"1.4d outline" and its Project specs section (the merge-target list); this
plan lifts them and settles what they leave open. It is built beside 1.4b
and merges after it (the waves table in `.claude/skills/build/SKILL.md`).
It has no migration: new rows are new event types on new or existing
streams, and the transcript is a new document kind in `documents`.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task hands the kernel a
credential that can write to every repository Valor's GitHub account can
write to, decides where a merge may land, changes the broker's performer
contract and every caller of `request`, `release`, and `reconcile`, and
copies files a turn wrote into the store. A mistake leaks the token to a
turn, pushes to `main` of `tomcounsell/ai`, pushes one task's commit into
another task's origin, or follows a link a turn planted.

## The Done items it closes

From valor-rebuild.md, 1.4:

| Done item | What closes it here |
|---|---|
| `tools/push_branch.py` gains a GitHub credential held by the kernel and never by a turn, so a released merge reaches the rebuild branch on GitHub | `github-key` copies the token into the kernel key directory; the `merge` performer sends it as a pinned header through a per-call config file, only to a granted (URL, branch) pair; the live push at rollout lands a commit on a scratch branch of `tomcounsell/ai`, and the first released merge after rollout lands on the rebuild branch |
| Transcript copies kept in the store with a digest | `core/transcripts.py` copies each turn's session file and its subagents' files into a `transcript` document, and `turn.ended` records each file's digest, size, offset, and `prefix_changed` |
| Absorbs: the broker's synchronous `perform` (made awaitable) | `perform`, `lookup`, and `refuse` are `async`; git runs in a worker thread |
| Absorbs: performers registered in a module-global dict (keyed per task) | `broker.PERFORMERS` and `broker.register` are deleted; the composition root builds a `broker.Performers` per task and passes it to every broker call and to `tasks.dispatch` |

Also from the outline: the merge-target list, and `merge_url` honoured by
`start --project`.

## Threat model

What a turn controls:

- **Its own clone**: config, hooks, refs, objects, and the commits it
  offers as a candidate. It never controls the kernel mirror, which no
  turn writes, and the merge pushes from the mirror.
- **Its Claude Code config directory** (`state/work/claude`, or
  `checks/<name>/claude` for a fresh session): every file under it,
  transcripts included, and any link it plants there. It controls the
  `session_id` it prints on stdout.
- **Spec edits through candidates**: a candidate may change
  `projects/valor.toml`, and once Tom merges it later tasks read the edit.
- **Process listing**: a running turn can read other processes' arguments
  (`pgrep -lf` works under `sandbox-exec`) and, for non-platform binaries,
  their environment (`ps -E`).

What the kernel must never do:

- Put the token, or any part of it, in argv, in a process environment, in
  a ledger row, a document, an exception message, or a log line.
- Put the token in any path a sandbox profile allows; the kernel key
  directory is denied by every profile (turn, fresh session, suite,
  service) and no environment a turn gets names it.
- Send the token on `push_branch`, to a pair not in the merge-target list,
  to the remote's default branch, to a URL outside the GitHub prefix, or
  after a redirect.
- Follow a link, or read anything but a regular file, under a turn's
  config directory; use the turn's `session_id` in a path before checking
  its shape.
- Let a transcript decide anything: it is a record, and nothing reads it
  to make a decision.
- Let a task's performers push to another task's origin.

## Design

### The credential (`core/credentials.py`, `core/settings.py`, `core/__main__.py`)

- **The token.** Valor's account `valorengels` holds a classic token with
  the `repo` scope, expiring 2026-12-31 (Tom's answer to m1-4-checks.md
  Questions, 3). Its durable copy is the vault `.env` as
  `GITHUB_PUSH_TOKEN` (and 1Password, "GitHub Push Token"). It reaches
  every repository the account can write to, so the kernel's restrictions
  below, and a ruleset on `main` with `valorengels` off the bypass list
  once Tom adds it, are what keep it to the rebuild branch.
- **`python -m core github-key`** calls
  `credentials.copy_keys(settings.vault_env, settings.github_keyfile,
  ["GITHUB_PUSH_TOKEN"])`, the same code `judgement-keys` uses, and prints
  `written`, `kept`, or `missing`, never a value. `github_keyfile` is a
  new setting derived from `pg_passfile`'s directory (`github-keys`), mode
  600, beside `judgement-keys`.
- **The header.** At each merge push or remote read to a granted
  non-local target, the kernel writes a config file of its own in the key
  directory, named `github-push-<random>.gitconfig`, opened with
  `O_CREAT | O_EXCL | O_NOFOLLOW`, mode 600, holding exactly:

  ```
  [http "<the granted URL>"]
      extraHeader = Authorization: Basic <base64 of x-access-token:TOKEN>
      followRedirects = false
  ```

  It is passed as `GIT_CONFIG_GLOBAL` for that one git call through
  `_git`'s `extra_env`, and deleted in a `finally` when the call returns.
  Git matches `http.<url>.*` against the URL being fetched or pushed, so
  the header goes to that one repository URL only. It is a pinned header
  (credential helpers stay refused, the PATH stays system-only), and a
  file rather than `-c http.<url>.extraHeader=...` because arguments are
  readable by a running turn (m1-4-checks.md Questions, 4).
- **The prefix.** The token is attached only when the target URL starts
  with `settings.github_url_prefix` (default `https://github.com/`). Tests
  set it to the loopback smart-HTTP server's `http://127.0.0.1:<port>/`.
  A granted URL outside the prefix is pushed to with no header.
- **`git.py`**: `push`, `remote_head`, `remote_sha`, and `holds` take an
  optional `credential: Path | None`; with it, `GIT_CONFIG_GLOBAL` is that
  file instead of `/dev/null`. `PINNED` gains `http.followRedirects=false`
  for every call. `hostile` already refuses `http.`, `credential`, `url.`,
  and `push.` keys in the mirror's config, so the mirror cannot add a
  header, a helper, or a rewrite of its own. `git.GitError` messages carry
  git's stderr, which never echoes a request header; a test checks it.
- **Rotation.** Tom creates a new token, replaces it in the vault `.env`,
  the build session runs `python -m core github-key` (it prints `written`),
  and Tom revokes the old one. A push GitHub refuses (git's stderr names
  HTTP 401 or 403) fails the merge effect with "GitHub refused the
  credential; rotate it", and the next run requests the merge again, as a
  failed outcome does.
- **A missing key.** `credentials.read_key` raises `MissingKey`; the merge
  effect fails with "no GitHub credential: run `python -m core
  github-key`", and `start` refuses a granted GitHub target with the same
  text, so a task never runs to the merge only to find no key.

### The merge-target list (`core/targets.py`, new)

- Rows on the global stream `merge_targets` (a fixed task id, as
  `corrections`, `guards`, and `judgement` are): `merge_target.granted`
  `{url, branch, note, provenance}` and `merge_target.revoked` `{url,
  branch, note, provenance}`. A pair is granted when its latest row is a
  grant. Rows never change.
- **`python -m core merge-target add URL BRANCH --note TEXT`** writes a
  grant, `provenance.by = "tom"`, `role_played: false`, always; the command
  has no `--by` or `--role-played`, like `grant`. It refuses a URL that is
  not `https://`, or `http://` to a loopback address (for tests), a URL
  carrying user info (`user@` or `user:pass@`), a query, or a fragment,
  and a branch `git check-ref-format --branch` refuses.
- **`python -m core merge-target remove URL BRANCH --note TEXT [--by
  NAME]`** writes a revocation. It takes authority away, so anyone running
  the kernel may (provenance records who).
- **`python -m core merge-target list`** prints the granted pairs.
- **`targets.local(url)`**: a URL with no scheme and no `host:path` form
  is a local path. A task's local `origin.git` is always allowed; every
  other target must be granted.
- **`targets.check(conn, url, branch)`** returns a refusal reason or
  `None`, reading only the ledger. It is called at `start`, at the merge
  request, and at release.
- **The default branch.** For a non-local target the kernel reads the
  remote's `HEAD` (`git.remote_head` from the mirror, with the credential)
  and refuses a target branch equal to it, even if granted, so `main` of
  `tomcounsell/ai` can never be a target. A remote whose `HEAD` cannot be
  read is refused too. This read is a network call, so it runs at `start`
  and inside `perform` immediately before the push (same worker thread,
  same credential file), never inside a ledger transaction. A merge it
  refuses fails with "the target is the remote's default branch" and
  pushes nothing.

### `merge_url` honoured (`core/workspace.py`, `core/__main__.py`, `core/tasks.py`)

- `start --project NAME` sets `Provisioned.origin_url = spec.merge_url`
  when the spec has one and `(merge_url, target_branch)` passes
  `targets.check` and the default-branch read; otherwise it refuses
  before provisioning, naming the `merge-target add` command to run. A
  spec without `merge_url` keeps the local origin.
- `start --workspace PATH` resolves `origin_url` from `git.push_url`; a
  non-local URL there goes through the same check.
- `push_url` stays the task's local `origin.git`, so `push_branch` never
  reaches GitHub. Only the merge pushes to `origin_url`, from the mirror,
  and only the commit and branch Tom's approval binds.
- `verdicts.merge_action` keeps building `{url, target_branch, head_sha,
  candidate}` from the Brief, and `Merge.refuse` checks the payload's
  `url` and `target_branch` against the Brief's `origin_url` and
  `target_branch` and against `targets.check`, so neither a changed spec
  nor a forged request moves the target.

### Performers per task, awaitable (`core/broker.py`, `tools/push_branch.py`)

- **`broker.Performers`**: a small class holding the task's performers by
  action type, with `get(action_type)` and `offered()`. The `Performer`
  protocol becomes:

  ```python
  async def perform(self, action, key) -> dict
  async def lookup(self, action, key) -> dict | None
  async def refuse(self, conn, action) -> str | None   # optional
  ```

  `refuse` takes `conn` so `Merge.refuse` reads the target list inside the
  request's and the release's transaction under the task lock.
- **Signatures**: `broker.request(conn, performers, task_id, action)`,
  `broker.release(conn, performers, effect_id)`,
  `broker.reconcile(conn, performers, effect_id, settle_after_s=None)`,
  `tasks.dispatch(conn, task_id, state=None, *, fresh=None, offered=())`.
  `PERFORMERS`, `register`, and module-level `offered` are deleted.
- **The composition root**: `__main__._performers(b)` returns
  `Performers(PushBranch(b.workspace, url=b.push_url or b.origin_url,
  protected=b.target_branch), Merge(b.mirror or b.workspace))`. For
  `release`, it reads the effect's task id from the held row first and
  builds that task's performers. The router's `Context` gains a
  `performers` field; `router.run` takes it; the runners pass it to
  `session` (turn effect requests), `verdicts.ensure_merge`, and
  `broker.reconcile`, and `runs.run_turn` gains `offered`, which session
  and fresh pass as `performers.offered()` and it hands to `tasks.dispatch`.
- **`tools/push_branch.py`**: `perform` and `lookup` run the git work in
  `asyncio.to_thread`, setting `git.deadline` inside the thread function
  (the deadline is a contextvar, and `to_thread` copies the context).
  `Merge` gains the credential path and the prefix from settings; it
  writes and removes the per-call config file around the push and around
  `holds` and `remote_head`. `PushBranch` never takes a credential.
- **`tests/performers.py`**: `WorkspaceWrite` and `OutboxAppend` become
  async, and the tests that called `broker.register` build a `Performers`
  instead (`tests/test_kernel.py`, `test_pipeline.py`, `test_attention.py`,
  `test_replay.py`, `test_session.py`, `test_fresh.py`,
  `test_judgement_sites.py`, `test_live_turn.py`, `scripted.py`).

### Transcripts (`core/transcripts.py`, new; `core/runs.py`; `harnesses/claude_code.py`)

- **Where they are.** The harness knows its own layout, so
  `TurnCommand` gains `transcripts: Callable[[dict], Transcripts | None]`,
  default none. Claude Code's returns, from the parsed result: the config
  root (the turn's `CLAUDE_CONFIG_DIR`), the session file
  `projects/<encoded cwd>/<session_id>.jsonl`, and the subagent directory
  `projects/<encoded cwd>/<session_id>/subagents/`, whose `agent-*.jsonl`
  files are copied. The encoded cwd replaces every non-alphanumeric
  character of the turn's cwd with `-`. A `session_id` that is not a UUID
  (lowercase hex, 8-4-4-4-12) gives none, recorded as `no_transcript:
  "session id is not a UUID"`. A turn with no config directory of its own
  (a task started with `--workspace`) copies
  nothing and records why: the kernel never reads Tom's own `~/.claude`.
- **When.** In `runs.run_turn`, after `reap` (no process of the turn is
  left to write) and before `turn.ended`, in a worker thread.
- **How it reads.** 1.4b leaves `read_turn_file(dir_fd, relpath,
  max_bytes)` in `core/workspace.py`, the no-follow component walk of
  `read_verdict`; transcripts reuse it. Each component is opened with
  `O_NOFOLLOW`, the final file must be regular, and the subagent directory
  is listed through its own descriptor, taking only regular files named
  `agent-*.jsonl`. A link, a non-regular file, or a missing file stores
  nothing for that file and records the reason.
- **What is stored.** One `transcript` document per turn (id: the turn
  id), body `{session: {...}, subagents: {name: {...}}}`, each entry
  holding the text (decoded UTF-8 with replacement) of the bytes stored.
  `turn.ended` gains `transcript: {document, files: [{name, sha256, bytes,
  offset, prefix_changed}]}` or `no_transcript: reason`.
- **Deltas.** A resumed session's file grows. For each file the kernel
  finds the last `turn.ended` of the task that recorded the same name and
  session; when the first `bytes` bytes of the file now have that row's
  `sha256`, it stores only the bytes from `offset = bytes` on; otherwise
  it stores the whole file with `prefix_changed: true`, the sign that
  something rewrote it (compaction, or the turn). `sha256` and `bytes` are
  always the whole file's.
- **Size.** A file over 50 MB keeps its digest and size, and its text is
  not stored (`stored: false`, reason `over 50 MB`). The 50 MB is the
  outline's.
- **Fresh sessions** run through `run_turn` too, so critique (and 1.4b's
  docs, 1.4c's review) transcripts are copied the same way from their own
  config directory.

### Docs fixed in the same build

- `docs/machine.md`: the secrets table row for Git hosting tokens says
  where the token lives, `github-key`, the per-call config file, and the
  merge-target list.
- `docs/harnesses.md`: the transcript paragraph describes what the kernel
  does, with the paths above.
- `docs/architecture.md`: the records table's transcript row and the merge
  sentence say where a merge lands.
- `core/README.md`: `github-key`, `merge-target`, transcripts, and the
  per-task performers, replacing "registered performers".
- `projects/valor.toml`: the header comment says the merge lands on
  `merge_url` when granted.
- `docs/plans/m1-4-checks.md`: the token text matches Tom's answer (done
  in this plan's commit).

## Tech debt absorbed

- The broker's synchronous `perform`: a push blocks the event loop the
  gateway's streams run on. Made awaitable here.
- Performers in a module-global dict: the last `register` wins, so two
  tasks in one process could push to each other's origin. Keyed per task
  here.
- `Provisioned.origin_url` always the local origin, with its comment, and
  the matching comment in `projects/valor.toml`: deleted when `merge_url`
  is honoured.

## Left out

- At takeover, a released merge that touches `core/` pulling the kernel
  checkout, migrating, and restarting (valor-rebuild.md, Execution): not a
  1.4 Done item; it lands with takeover.
- The ruleset on `main` of `tomcounsell/ai`: Tom's to add on GitHub; the
  kernel does not create or check it.
- Deleting a remote branch, or any push other than the merge, with the
  credential.
- Client project targets (milestone 2); they use the same list.
- Searching or summarising transcripts; they are stored and digested only.
- Bridge performers (send, pay): they use the same `Performers` shape when
  they land.

## Tests

All against a test database (`VALOR_TEST_DB`), never `valor_rebuild`.
The smart-HTTP server is a loopback `http.server` in the test process that
runs the trusted git's `git http-backend` as CGI for one bare repository,
refuses any request without the expected `Authorization` header with 401,
can answer with a 302 to another loopback port, and logs every header it
receives.

**Credential**

- A released merge to a granted loopback target lands, with the header,
  from the mirror.
- With the key file missing, the merge fails with the `github-key` text
  and the server saw no request carrying a header.
- With a wrong token, the server answers 401 and the outcome's error is
  "GitHub refused the credential; rotate it".
- `push_branch` in the same task never sends the header (server log), and
  lands on the local origin.
- A mirror whose config holds an `http.` or `url.` key is refused before
  any request.
- The server redirects the push to a second loopback server: the second
  never receives the header, and the push fails.
- A granted URL outside `github_url_prefix` is pushed to with no header.
- No leak: while the server holds a push open, `ps -E -ww` from the
  kernel and `pgrep -lf` and `ps -E -ww` from a probe under `turn.sb` show
  no process whose arguments or environment contain the token, its base64
  form, or the config file's contents; the probe cannot open the config
  file or `github-keys` (denied).
- Afterwards no `github-push-*.gitconfig` file remains, and the token, its
  base64 form, and its SHA-256 appear in no `events` payload, no
  `documents` body, and no captured log or exception text.
- `github-key` prints `written`, then `kept`, then `missing` for a vault
  without the name, and never prints the value.

**Merge targets**

- `start --project` with a spec whose `merge_url` and branch are not
  granted refuses, naming the `merge-target add` command; granted, it
  provisions with `origin_url = merge_url`.
- A granted pair naming the server repository's `HEAD` branch is refused
  at `start`, and, when `HEAD` is moved onto it after `start`, the merge
  fails before any push (server log).
- A remote whose `HEAD` cannot be read refuses at `start`.
- A pair revoked after the merge was held is refused at release.
- A candidate that edits `projects/valor.toml` to name another URL or
  branch changes nothing for the running task; a later task started from
  the edited spec is refused because the pair is not granted.
- A forged `merge` request from a turn's `.valor/` signal is refused (as
  it is), and a merge payload whose `url` differs from the Brief's
  `origin_url` is refused by `Merge.refuse`.
- `merge-target add` refuses `user:pass@` URLs, `ssh` URLs, a query, a
  fragment, and a malformed branch; it has no `--role-played`.
- A local origin is allowed without a grant, and its symbolic `HEAD`
  (which names the target branch) does not trigger the default-branch
  refusal.

**Performers**

- Two tasks with different origins, run concurrently in one process
  (`asyncio.gather` of two releases), each land only on their own origin.
- While a push is held open by the server, the event loop keeps serving
  (a timer task ticks during the push).
- `git.deadline` applies inside the worker thread: a push the server
  stalls fails at `git_timeout_s` (set small in the test).
- `reconcile` of a dangling merge intent uses the task's own performers
  and its credential for `holds`.
- `tasks.dispatch` lists the task's offered action types.
- The existing broker, pipeline, attention, replay, session, and fresh
  tests pass with per-task `Performers`.

**Transcripts**

- A scripted turn whose config directory holds a session file and two
  subagent files: one document, three entries, digests equal to the
  files' SHA-256.
- A second turn resuming the session stores only the appended bytes, with
  `offset` equal to the first copy's `bytes`.
- An edited earlier line shows `prefix_changed: true` and the whole file
  stored.
- A turn that replaces its session file with a symlink to a scratch
  secret, and one that makes a directory component a symlink, store
  nothing for it and record why; the secret's text is in no row or
  document.
- A FIFO or a directory named `agent-x.jsonl` is skipped with a reason
  and does not block.
- A `session_id` of `../../x` or with a slash records `no_transcript`.
- A 51 MB file keeps digest and size with no text.
- Live (`VALOR_LIVE=1`, metered, expected about $0.30): a real turn that
  starts a subagent has the subagent's transcript copied with its own
  digest.

**Live push at rollout** (`VALOR_LIVE=1`, `VALOR_LIVE_GITHUB=1`, no model
spend): with the real key file and the pair `https://github.com/
tomcounsell/ai.git`, `valor/push-check` granted, a merge performer pushes
a commit whose parent is that branch's head (or the rebuild branch's head
when the branch does not exist) and `holds` confirms it.

## Files it changes

| File | Change | Also changed by |
|---|---|---|
| `core/broker.py` | `Performers`, async protocol, signatures, registry deleted | 1.5, 2.1 |
| `core/git.py` | `credential` parameter, `followRedirects` pinned | 1.4b (`hostile` profile) |
| `core/credentials.py` | the header file writer | |
| `core/settings.py` | `github_keyfile`, `github_url_prefix` | 1.4b, 1.5 |
| `core/targets.py` | new | |
| `core/transcripts.py` | new | |
| `core/runs.py` | transcript copy before `turn.ended`; `TurnCommand.transcripts`; `run_turn(offered=)` | 1.5 |
| `core/tasks.py` | `dispatch(offered=)`; `origin_url` check for `--workspace` | 1.5, 2.1 |
| `core/workspace.py` | `origin_url = merge_url` when granted | 1.4b |
| `core/__main__.py` | `github-key`, `merge-target`, `_performers`, release and run wiring | 1.4b |
| `core/router.py` | `Context.performers`, `reconcile` call | 1.4b (`_Services` reap) |
| `core/session.py` | `request` with performers | 1.5 |
| `core/verdicts.py` | `ensure_merge` with performers | 1.4b |
| `core/fresh.py` | passes `offered` to `run_turn` | 1.4b |
| `tools/push_branch.py` | async, credential for `Merge` | |
| `harnesses/claude_code.py` | transcript locator | |
| `tests/` | new `test_credential.py`, `test_targets.py`, `test_transcripts.py`, a smart-HTTP fixture; the files listed under Performers | 1.4b |
| `projects/valor.toml`, `core/README.md`, `docs/machine.md`, `docs/harnesses.md`, `docs/architecture.md`, `docs/plans/m1-4-checks.md` | docs | 1.4b (README, architecture) |

1.4b merges first. Its interfaces this plan uses are `read_turn_file` and
`git.hostile(profile=)`; the rest of the overlap is call sites, rebased
after 1.4b lands.

## Rollout

1. Merge after 1.4b, kernel-touching, alone. In the kernel checkout:
   `uv sync`. No migration.
2. The build session runs `python -m core github-key` from the kernel
   checkout; it prints `written`.
3. Tom runs, from the kernel checkout:
   `python -m core merge-target add https://github.com/tomcounsell/ai.git
   <rebuild branch> --note "the rebuild's merges"` and
   `python -m core merge-target add https://github.com/tomcounsell/ai.git
   valor/push-check --note "1.4d live push"`.
4. The build session runs the live push test; then
   `python -m core merge-target remove ... valor/push-check --by valor
   --note "live push done"`. The branch `valor/push-check` stays on GitHub
   for Tom to delete or keep.
5. The next task started with `--project valor` merges to the rebuild
   branch on GitHub when Tom approves it.
6. Tom adds the ruleset on `main` when he can; nothing waits for it.

## Decided by default

Each is reversible and was decided by the build session:

- **A per-call config file, not a static one written by `github-key`.**
  Scoped to the exact granted URL of each push; nothing on disk outside a
  call but the key file. A crash between write and delete leaves one file
  at mode 600 in the denied key directory, the same exposure as the key
  file; no sweep removes others, since a sweep could delete a concurrent
  call's file.
- **Basic auth with the user `x-access-token`.** GitHub accepts a classic
  token with any user name over HTTPS; the live push confirms it.
- **`http.followRedirects=false` pinned on every kernel git call.** No
  kernel git call needs a redirect.
- **The GitHub prefix setting.** It keeps the token off every other host
  even if a non-GitHub URL is granted, and lets tests use loopback.
- **The default-branch read at `start` and inside `perform`, not in
  `refuse`.** A network call stays out of a ledger transaction holding the
  task lock; `perform` is the latest moment before the push.
- **`merge-target remove` exists and anyone may run it.** It only takes
  authority away; provenance records who.
- **Performers passed explicitly** (a router `Context` field threaded to
  each call) rather than looked up through a factory, so a test sees
  exactly which performers a call used.
- **`refuse` is async and takes `conn`.** The target list is ledger data
  and must be read in the same transaction as the intent.
- **The harness locates transcripts.** `core` never knows Claude Code's
  directory layout; another harness supplies its own locator or none.
- **A UUID check on `session_id`**, not a general path check: a session id
  never needs anything else.
- **No transcript from a turn without its own config directory**: the
  kernel never reads Tom's `~/.claude`.
- **Transcript text stored decoded with replacement**: a `jsonb` string
  body needs no migration; the digest is over the raw bytes.
- **The live push branch `valor/push-check`**, created by the test, kept
  afterwards.

## Questions for Tom

Only credential and scope choices are his:

1. **The two grants at rollout** (step 3): the rebuild branch and
   `valor/push-check` on `tomcounsell/ai`. Assumed: yes, both, run by Tom
   from the kernel checkout when the build reaches rollout; the scratch
   grant is revoked after the live push.
2. **The ruleset on `main`** with `valorengels` off the bypass list.
   Assumed: Tom adds it when he can; the build does not wait for it, and
   the kernel's default-branch refusal holds either way.
