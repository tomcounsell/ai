# 1.4d outline: the GitHub credential, transcripts, the performer registry

Outline of task 1.4d from [m1-4-checks.md](m1-4-checks.md); [m1-4d-credential.md](m1-4d-credential.md) is the plan.


### The credential

- **Whose token** (Questions, 3, answered): Valor's own account
  `valorengels`, a collaborator with write access to `tomcounsell/ai`,
  holding a classic token with the `repo` scope that expires 2026-12-31.
  A fine-grained token cannot reach a repository of a user account. The
  classic token reaches every repository the account can write to, so the
  merge-target list and the default-branch refusal are the restriction,
  plus a ruleset on `main` with `valorengels` off the bypass list once Tom
  adds it. Full design: [m1-4d-credential.md](m1-4d-credential.md).
- **Where the merge may land.** Only a (URL, branch) pair granted by a
  `merge_target.granted` ledger row (`python -m core merge-target add URL
  BRANCH --note TEXT`, Tom's alone, never role-played; see Project specs),
  never the remote's default branch, checked at `start`, at the merge
  request, and at release. A candidate's edit to `projects/valor.toml` cannot add a target.
- **Where it lives.** The vault `.env` holds the durable copy as
  `GITHUB_PUSH_TOKEN`, beside the judgement keys. `python -m core
  github-key` copies it into `github-keys` in the kernel key directory
  (mode 600, path derived from `pg_passfile` like `judgement-keys`),
  printing `written`, `kept`, or `missing`, never a value.
- **How it reaches only the push.** For each merge push or remote read,
  the kernel writes a config file of its own in the key directory (mode
  600, deleted when git exits) holding only `[http "<granted URL>"]
  extraHeader = Authorization: Basic <base64 of x-access-token:TOKEN>`,
  and passes it as `GIT_CONFIG_GLOBAL` for that one call instead of
  `/dev/null`. Only the `merge` performer of a task with a kernel mirror
  gets it; a `--workspace` task merges without it. The granted URL's
  shape is checked before it is written. The header is a
  pinned header, as 1.2's review proposed, since credential helpers are
  refused and the PATH is system-only; it is delivered as a file path
  rather than as `-c http.<url>.extraHeader=...` on the command line,
  because any process of Tom's user, a running turn included, can read
  another's arguments: the critique of this plan confirmed that
  `pgrep -lf` run inside `sandbox-exec` shows other processes' full
  arguments. A test checks that while a push runs, the token is in no
  process's arguments or environment, both from the kernel (`ps -E -ww`)
  and from a probe running under `turn.sb` (`pgrep -lf`, `ps -E -ww`).
- **`push_branch` stays local.** `push_url` (1.4a) keeps `push_branch` on
  the local `origin.git`. 1.4d is where `origin_url` becomes the spec's
  `merge_url` for a pair in the merge-target list. So the credential is
  used for exactly one (URL, branch) per task: the target branch the merge
  payload names and Tom's approval binds. A turn's own branches never
  reach GitHub.
- **How a turn can never read it.** The key directory is denied by every
  profile (turn, fresh session, suite, service); no environment a turn
  gets names it; the container never mounts it; no ledger row, exception,
  or log line carries the token (push errors are reported by git's exit
  code and stderr, which never echo the header, and a test asserts the
  token's digest appears in no row).
- **Rotation.** Tom creates a new token, replaces it in the vault `.env`,
  runs `python -m core github-key`, and revokes the old one. A refused
  push fails the merge effect with "GitHub refused the credential; rotate
  it" and the next run requests the merge again, as a failed outcome does
  now.
- **Tests** against a local smart-HTTP server (the trusted git's own
  `git http-backend` behind a loopback HTTP server that refuses a push
  without the expected header): a released merge lands with the file, is
  refused without it, `push_branch` never sends the header, and a
  workspace or mirror holding any `http.*` key is still refused; a merge
  to a pair missing from the merge-target list is refused at start, at
  request, and at release, with an edited `projects/valor.toml` naming it;
  the remote's default branch (the local server's `HEAD`) is refused at
  start and before the push. Live, at
  rollout, with the chosen token: one released merge to a scratch branch
  on `tomcounsell/ai`.

### Transcripts

When any turn with its own config directory ends (working or fresh), the kernel copies its Claude Code
session file into `transcript` documents (one per file per chunk) and
records `transcript: {files: [{name, documents, sha256, bytes, offset,
prefix_changed}]}` on `turn.ended`. The kernel chooses the session id
(`--session-id`), and the session file is under the turn's own
`<config dir>/projects/`, reached without following a link from the
directory holding the config root. Subagents a turn starts
write their transcripts as separate files beside the session's (in the
session's directory), and those are copied too, each with its own digest,
listed on the same `turn.ended`. Each file is opened component by
component without following symlinks (as `verdict.json` is), must be a
regular file with one link, and is stored as base64 of its raw
bytes, one document per file, chunked to stay under the `jsonb` string
limit, with no size cap. A failed copy still records `turn.ended`, with
the reason. A
resumed session's file grows, so each copy stores the bytes appended since
the previous copy when the earlier prefix still has the digest recorded
for it; otherwise it stores the whole file and marks `prefix_changed`,
which is itself the sign that something rewrote the transcript (compaction
or the turn). Tests: a turn that replaces its transcript with a symlink to
a scratch secret stores nothing and records why; an edited earlier line
shows `prefix_changed`; a live turn that starts a subagent (`VALOR_LIVE`,
metered, expected about $0.30) has the subagent's transcript copied with its own digest.

### The performer registry, awaitable

The composition root builds a `broker.Performers` per task (`push_branch`
to `push_url`, `merge` from the mirror) and passes it to `request`,
`release`, `reconcile`, and `tasks.dispatch`. No module-global dict
remains. `perform` and `lookup` become `async`, with git run in a worker
thread, so a push no longer blocks the event loop the gateway's streams
run on (the bridges need this too). Test: two tasks with different
origins in one process never push to each other's.

