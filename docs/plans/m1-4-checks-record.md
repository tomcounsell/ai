# Records for 1.4: critique, build, patch, delivery, the popoto trial

Records of the checks milestone from [m1-4-checks.md](m1-4-checks.md): the critique rounds, the 1.4a build and patch rounds, its deliveries, and the popoto #191 trial run.


Verdict `revise`; the split and the order were found sound. Each finding
is resolved in this revision:

1. Check profiles now get a per-run `pgpass` copy in their own `tmp/`;
   caches are a seed filled only by the base's setup, cloned per run,
   deleted after the run; test that a head suite's
   cache write does not reach the next candidate.
2. Every turn and check has its own `TMPDIR` and Claude Code config
   directory; fresh profiles deny `/private/tmp`, `/private/var/folders`,
   `~/.claude`, `~/.claude.json` except their own; tests that the critic
   cannot read the builder's `$TMPDIR` or `todos`. The config-directory
   login premise is checked first, with a fallback.
3. Calibration: case sets, labels, and floors frozen by digest before run
   1, chosen by stated rules; drafted labels count in neither bar until
   Tom confirms; the bars are a question to Tom, with the entry check
   (counting governance false positives) until then; 1.5's takeover items
   excluded from breadth; a site that misses keeps its stages manual.
4. `push_url` lands in 1.4a; `origin_url` stays the local origin until
   1.4d; 1.4a switches merge reads to the mirror and the manual docs head
   is fetched into it.
5. The mirror fetch: `hostile` first, alternates and shallow refused,
   upload-pack under `turn.sb`, fsck, no tags, size and time caps, replace
   refs and grafts off; the mirror seeded with the base; 1.4a waits for or
   carries 1.2's fix, flagged; tests with alternates, a graft, a replace
   ref.
6. The merge-target list in the kernel key directory, the default branch
   always refused; Valor's own GitHub account recommended with Tom's token
   as fallback, both rollout options; writes denied to
   `~/Library/LaunchAgents`, shell rc files, and `~/.local/bin` in every
   profile, narrowing the harnesses.md opening.
7. Absent or skipped tests that passed at base fail unless the diff
   deletes their definition, named as defining the existing check; no
   reuse of a timed-out or infrastructure-failed `suite.ran`; the spec's
   env and `bin/` versions in the environment digest.
8. Every router run first stops other tasks' services by their mark
   (`services.reaped`); tested after a SIGKILL against the 16 GB lines.
9. `_legacy` fixed too, with a test through it.
10. `verdict.json` opened component by component with no-follow and
    non-blocking; tests for a symlinked `.valor` and a FIFO.
11. Docs governance after the turn stated as overriding valor-rebuild.md,
    which the 1.4b build fixes.
12. `binaries.require` on the helpers in `/usr/local/libexec/container/`;
    the runtime's launch-agent plists and data directory denied in every
    profile.
13. Premises recorded: arguments are visible from inside `sandbox-exec`
    (a `pgrep` probe from `turn.sb` joins the no-leak test); nested
    `sandbox-exec` works.
14. Subagent transcript files are copied too, with a live test.

## Critique round 2 (of 2), carried into the build

Verdict `sound`, with findings folded in above: other tasks' services are
stopped only when their router lock is free; the startup-files deny widened
(`~/.local/share/claude`, `~/.claude`, `~/.gitconfig`, `~/.config/git`,
`/opt/homebrew`, `DISABLE_AUTOUPDATER=1`) and called narrowed, not closed;
the login check's result and the gateway-held credential; `fetch.unpackLimit=1`
and a footprint watchdog on the mirror fetch; no read-only cache (1.4b);
`/bin/ps` fails inside any sandbox, so the reaper's tests are host-only
(1.4b decides how); merge targets as Tom's ledger rows (1.4d); the
parametrize and non-Python rules (1.4b); the trusted git first on fresh
sessions' `PATH`. 1.2's replace-ref fix is carried in 1.4a's first commit
(1a1a6235d), "carried from 1.2's open finding pending Tom".

## Build record (1.4a)

Built on `m1.4-checks`. The first commit (1a1a6235d) carries 1.2's
replace-ref fix, "carried from 1.2's open finding pending Tom", with a test
for each of a replace ref and a graft; it is dropped on rebase onto 1.2's
own fix.

**The login check.** `claude -p` 2.1.287 with a fresh `CLAUDE_CONFIG_DIR`
answers "Not logged in" and sends nothing; with a dummy
`CLAUDE_CODE_OAUTH_TOKEN` it sends `Authorization: Bearer <dummy>` to the
base URL (both checked against a local probe server). So the gateway holds
the credential (`core/gateway.py`, `ClaudeLogin`): `claude-token` in the
kernel key directory when present, otherwise the Keychain login's access
token. Claude Code also keeps scratch under `/tmp/claude-<uid>` unless
`CLAUDE_CODE_TMPDIR` names another place; every turn sets it to its own
`TMPDIR`. One live fresh critique (Haiku) then ran under the fresh profile,
with `/private/tmp`, `/private/var/folders`, and `~/.claude` denied, needed
no path allowed back, wrote a valid verdict, and kept its transcript in its
own config directory.

Settled while building:

- The task's Postgres listens on TCP loopback only: a unix socket under a
  long work directory passed the 103-byte path limit.
- `GATEWAY_PORT=1` for sandboxed steps with no gateway (0 does not parse).
- The file-size limit on the mirror fetch is set by `/bin/sh -c 'ulimit -f'`,
  since a preexec function is unsafe in the threaded kernel.
- The sweep runs at the start of every router run, for every task; it looks
  for marked service processes with one process listing and stops a task's
  only when its router lock is free.
- `start --project` takes `--branch`, since the rebuild's branch is not the
  repository's default; `projects/valor.toml` names none.
- Replays provision through the kernel: `scripts/replay_workspace.py`
  writes the spec, `replay.py` starts with `--project` and reads the
  workspace back with `workspace show`, and the judge verifies inside the
  task's own state with its services started and stopped by the kernel. The
  replay profile tests moved to the kernel's profiles
  (`tests/test_demo_sandbox.py`, `tests/test_reap.py`).
- `verdict` lost `--raise-critique` and `--raise-review` with the critique
  stage; a stage with a runner is refused as having one.
- A raise outside 0 to 2 in a verdict file is no verdict; a lower raise
  changes nothing (the fold takes the higher count).

Evidence: `cd ~/src/valor-rebuild-m14 && VALOR_TEST_DB=valor_rebuild_test_m14
.venv/bin/python -m pytest -q tests` (see the done note for the counts);
`uvx ruff check .` and `uvx ruff format --check` on the code clean.
`VALOR_LIVE=1 ... tests/test_live_fresh.py` passed once, metering $0.034
(an earlier attempt that stopped at the $0.15 it started with metered $0.066; superseded 2026-10-03: metered spending only; nothing refuses on money).
`tests/test_live_session.py` was rewritten for `start --project` and the
critique runner and not run (it now runs an Opus critique, up to $1.00).

## Patch round 1 (review round 1 of 2)

On top of the docs session's `0f24a571c`. Every finding resolved:

- **R1.** A plan or candidate whose tree holds a top-level `.valor` (any
  case) is no plan and no candidate (`session._keep`), and `blind_checkout`
  refuses one; `workspace.write_inputs` makes `.valor` itself (refusing one
  that exists), writes each input relative to a descriptor with
  `O_NOFOLLOW` and `O_EXCL`, and refuses a verdict file before the turn;
  turn-chosen text in `plan.md` is JSON-quoted. Both reproductions are
  tests (a committed `.valor/inputs` symlink to a stand-in for
  `~/.zshenv`, and a committed `.valor/verdict.json`). 1.4b's outline says
  the same holds for test, docs, and review.
- **R2.** The file-size limit runs under `/bin/bash` with 1024-byte blocks;
  a test writes 4 MB under a 1 MB limit and finds exactly 1 MB.
- **R3.** A refused or killed fetch deletes `tmp_*` packs, `incoming-*`, and
  `tmp_objdir-*`; the size-limit and delta tests assert none remain.
- **R4.** The comments in `core/workspace.py` (no socket; gateway port 1)
  and `core/fresh.py` (what blindness covers) say what is true.
- **R5.** Fresh profiles deny `/private/var/tmp`; harnesses.md's Known
  openings says blindness holds for the paths the kernel names.
- **R6.** With a credential, the gateway forwards only `v1/messages`,
  `v1/messages/count_tokens`, and `v1/models`; anything else is a 403.
  (Review round 2 found the `v1/models/` prefix match let dot segments and
  encoded slashes through; the proposed patch below closes it.)
- **R7.** The sweep also stops the services of task directories with no task
  row whose `provision:<id>` lock is free; `workspace remove ID` removes
  such a directory.
- **R8.** `start --project` holds `workspace:ports` only to choose the ports
  and record them in the task's directory (`ports.json`, which
  `taken_ports` reads), and holds `provision:<id>` through provisioning;
  the sweep only try-locks `workspace:ports` and skips when it is busy.
- **R9.** A `.git` gitfile or a `commondir` refuses the fetch.
- **R10.** The kernel's cache is keyed by the URL's digest.
- **R11.** The Keychain's expiry is parsed inside the check; the Keychain is
  read at most once a minute, failures included and whatever a 401 asked;
  `advice.graftFileDeprecated=false` is pinned.
- **T1 to T15** are tests: a cluster that will not start fails the run
  naming its log with no turn; the cluster is up during a turn and down
  after; `start --project` refusals, `--branch`, two concurrent starts on
  distinct ports, a failing start removing its workspace; a provisioning
  whose cluster will not start leaving nothing; the cache's refusals; a
  refused mirror fetch is no plan; the fetch's time limit, sha, and ref
  refusals; `ClaudeLogin` through an injected Keychain reader (no test reads
  the real Keychain); `CLAUDE_CODE_TMPDIR`; session verdicts naming their
  turn and model; a mismatched plan digest; a lower raise; a docs head that
  is not a full sha or cannot be fetched; the replay workspace's
  build, attach, and teardown through the kernel; critique with no database
  credential and no service port (`check_harness(services=False)`); and a
  merged task's workspace and Redis removed through the command line.

## 1.4a delivery 1 (did not pass)

Review round 2, the last the plan allows, on `88029a1f6` (docs at
`7b2c5c4be`): review `changes`, test `gaps`, docs `updated`. With both
review rounds spent, 1.4a goes to Tom as a delivery that did not pass.

Review findings, blocking:

- **B1.** The gateway's allowlist accepted any tail starting with
  `v1/models/`, the upstream URL was built as a string, and the client's URL
  parser resolves dot segments and decodes `%2f`, so
  `/v1/models/../../api/oauth/profile`,
  `/v1/models/%2e%2e/%2e%2e/api/oauth/profile`,
  `/v1/models/..%2f..%2fapi/oauth/profile`, and
  `/v1/models%2f..%2f..%2fapi/oauth` reached other upstream paths carrying
  Tom's bearer token.

Non-blocking: N2, `_orphan` and the sweep read the task row before taking
`provision:<id>`; N3, a first 401 should allow one Keychain re-read despite
the once-a-minute bound, since Tom's sessions rotate the token; N4,
`_docs_into_mirror` should refuse a docs head that commits `.valor`.

Test gaps: G1 the commondir test should point at a valid repository and
match the refusal; G2 `write_inputs` with a pre-existing `.valor`, a planted
verdict file, and its exclusive and no-follow creation each exercised; G3
the orphan path through the command line, including the refusal while
`provision:<id>` is held; G4 `stop_services`' `stopped` entries; G5 a lower
raise through the runner; G6 the private-HTTPS refusal test without
github.com.

## 1.4a proposed patch, awaiting Tom's feedback

Prepared while Tom is away; not authorised by the pipeline, which has spent
its review rounds. It is a candidate for his decision: his feedback on the
delivery is what would send it through the checks.

- **B1.** The gateway checks each path as it arrived, undecoded, and
  refuses (400) any tail with a percent escape, a backslash, or an empty,
  `.`, or `..` segment; with a credential it forwards only `v1/messages`,
  `v1/messages/count_tokens`, `v1/models`, and `v1/models/<id>` with an id
  of letters, digits, `.`, `_`, `-` and no `..` (403 otherwise); the upstream
  URL is built byte for byte (`yarl.URL(..., encoded=True)`), never
  re-normalised. A test sends the four reproduced paths and the earlier
  refused ones exactly as written and finds the upstream saw only the two
  allowed paths.
- **N2.** `_orphan` and the sweep look for the task row again after taking
  `provision:<id>`, and leave a directory that became a task to its own run.
- **N3.** The first 401 after a Keychain read allows one more read at once;
  later ones wait out the minute. Tested.
- **N4.** A docs head that commits `.valor` is refused. Tested.
- **G1.** The commondir test points at a valid repository and matches
  "commondir"; the gitfile test matches its own refusal.
- **G2.** `write_inputs` refuses an existing `.valor` (nothing written), a
  planted verdict file, and a linked checkout; `write_files` refuses an
  existing name (exclusive creation), a link to a real file, and a dangling
  link (nothing created through it). With both flags always set, a link is
  refused by either; the existing plain file isolates exclusive creation.
- **G3.** `workspace show` and `workspace remove` on a directory with no task
  row through the command line, and the refusal while `provision:<id>` is
  held.
- **G4.** A clean Postgres stop is reported with `stopped` entries naming the
  postmaster.
- **G5.** A lower raise from a critique turn leaves the review rounds at the
  plan's count.
- **G6.** The HTTPS refusal test fetches from a loopback port where nothing
  listens; the credential refusal is tested on git's own error text.

## Checks on the 1.4a proposed patch, and the recommendation to Tom

Candidate `fb22c8796` plus the docs check's commit `0598e961a`. Iteration
stopped here for Tom's decision.

- **Review:** `pass`, governance no. The four gateway bypass paths and
  variants (mixed-case escapes, overlong dots, semicolon parameters,
  fragments, absolute-form targets) are refused; allowed paths arrive
  byte for byte. The orphan race, the 401 re-read, and the docs-head
  `.valor` refusal are fixed.
- **Test:** `gaps`. 507 passed, 6 skipped; no regressions, no leftover
  services. Untested on their own: the pre-existing `verdict.json` check in
  `write_inputs` (shadowed by the `mkdir`), `safe_tail` on a gateway with no
  credential, an allowed path with escapes in its query, and the task-row
  recheck race. One sweep test is flaky when another suite runs on the same
  machine, since the sweep is machine-wide.
- **Docs:** `updated`.

**Recommendation:** give feedback that accepts this patch with the four
test gaps added, which needs one patch and one more run of the three checks.

## Tom's feedback on 1.4a (delegated decision, 2026-10-02)

Decided on Tom's behalf under his delegation: apply the proposed patch,
with four test gaps added, and run the suite only; no further review round,
since review passed the patch. Added:

1. `write_inputs`' "a verdict file exists before the session's turn" check
   is its own step (`workspace.no_verdict_yet`), and a test reaches it with
   a verdict file appearing in `.valor` after the kernel made it.
2. A gateway without a credential refuses `..`, `%2e`, and `//` paths with
   400 before forwarding anything.
3. An allowed path whose query carries percent escapes reaches the upstream
   byte for byte.
4. A task row appearing in the window: for the sweep, after its directory
   scan (an `after_scan` hook the test drives); for `workspace remove` on an
   orphan, before and under the provision lock (an `after_lock` hook). In
   each case the directory is left to its task.

## 1.4a merged

Merged 2026-10-02 at `a30c03350` on Tom's tap, after his delegated feedback
(apply the proposed patch with four tests; no further review round). Next,
per the rebuild plan: the popoto #191 trial run through the kernel with
`python -m core verdict` playing the runners not yet built, then 1.4b (test
and docs runners, routing on the entry check, no calibration-first), then
1.4d (the GitHub credential), with 1.4c (the container verifier) after
takeover.

## The popoto #191 trial run

Run 2026-10-02 on Valor's Mac, in the real ledger, as task `75c0902b6e25`
(`scripts/replay.py pop-b.json --arm routed` with $4.50 committed, the real
judgement legs, Opus 5.5 for every turn). The ledger was dumped to the
backup disk first. It did not reach a held merge: the build stopped on its
per-call reservation before it produced a candidate, and finishing would
have passed a $5 cap this plan set. Superseded 2026-10-03: metered
spending only; nothing refuses on money (see Tom's decision below).

What the pipeline did, with the metered spend of each turn:

| Step | Result | Spend |
|---|---|---|
| judge | Jev answered `one_line_ask`, so `thin` (the baseline's label for this item) | $0.00003 |
| clarify | no question for Tom: "the task names its own mechanism" | $0.26 |
| plan | `docs/plans/capped-list-field.md`, critique 1 and review 2 | $0.56 |
| critique 1 | `revise`, seven findings, one a real wrong premise (a key change on full save loses the list) | $0.50 |
| plan, revised | every finding met in the plan | $0.48 |
| critique 2 | `sound`, four findings carried into the build | $0.37 |
| build, turn 1 | stopped: the next call's reservation ($1.44) exceeded what was left ($1.38) | $0.93 |
| build, turn 2 | after a $1.75 raise; stopped the same way (reservation $1.74, $1.65 left) | $1.49 |

Total metered: $4.60 of the $6.25 committed to the task. The workspace holds about 380
uncommitted lines across four files of popoto, with no tests yet.

What Tom would have had to do: nothing up to the build (no question, no
tap). Then two raises of the committed amount, one of which the driving session
made under this plan's $5 cap (role played, with the reason; both recorded
in the ledger as the legacy raise rows). Superseded 2026-10-03: metered
spending only; raises no longer exist.

Findings:

1. **The last $1.40 to $1.75 of the committed amount could not be spent.** Each Opus call
   reserves its worst case (32,000 output tokens on the turn's whole
   context) before it runs, so a turn stopped with that much left. The tail
   grew with the context. It no longer exists: the reservation refusal is
   removed (see below), and the finding stands as a cost finding only.
2. **The build turn starts carrying the whole working session.** (Stands as a cost finding for 1.4b.) The first
   build call already sent about 70,000 input tokens (clarify, plan, and the
   revision, resumed), and every call after resends it. The bare baseline
   did this whole item in one turn for $1.57, and its clarify run for $1.75.
   Before any code, the pipeline here spent $2.18.
3. **The turn did not use the task's own Redis.** The kernel put
   `REDIS_URL` on the task's port in the turn's environment; the turn ran
   `redis-cli ping` on the default port, found TCP blocked, started its own
   redis-server on a unix socket under `.valor/`, and overrode `REDIS_URL`.
   The reaper stopped that server when the turn ended. Three of popoto's
   tests reach `localhost:6379` directly and fail in the sandbox at the
   base.
4. The judge, clarify, plan, critique, and fresh-session pieces all ran as
   built in 1.4a, with no manual step.

**Recommendation to Tom (superseded 2026-10-03: metered spending only;
nothing refuses on money):** raise this trial's cap to $8 and let the build finish to a held merge, with `verdict` playing
test, review, and docs. The build needs about $1.50 to $2.50 more, and the
checks and the merge have not yet run on a provisioned task. Findings 1
and 2 then go into 1.4b's plan as questions about the call's worst-case size and
whether the build should start a fresh session from the plan.

### Tom's answer, and the third build turn

Tom raised the cap to $8 (by Tom, 2026-10-02; the legacy raise row). Build turn
3 metered $1.49 and stopped the same way, with $1.91 left that the next
reservation exceeded. Total metered: $6.09 of the $8.00 then committed. The workspace now
holds about 460 uncommitted lines across seven files, a new
`tests/test_capped_list_field.py`, and doc edits, but no candidate yet. The
task is in `build`; no service of it is running.

Next (before Tom's decision): another raise of about $2 was needed, past the
$8 Tom had set. Superseded by the decision below.

### Tom's decision, 2026-10-03

Metered spending only: cost is information, and nothing refuses, pauses, or
asks Tom because of money. The reservation refusal and raises are removed
from the kernel. The trial continues on Valor's Mac after pulling and
running `python -m core migrate`, with `python -m core run 75c0902b6e25`.
Then fresh subagents play test, review, and docs through `python -m core
verdict`, stopping at the held merge. Findings 1 and 2 stand as cost
findings: the reservation tail no longer exists, and the context-carrying
build is still a cost finding for 1.4b.

### The trial to the held merge, 2026-10-03

Run on Valor's Mac after `python -m core migrate` (the per-call index
swap; no row changed). The backup disk was not mounted when migrate ran,
so the dump before it was missed; the dump after it is
`valor_rebuild-20261003T011437Z.dump` on the backup disk.

1. Build turn 4 metered $1.22 and produced candidate `543c1395`
   (10 files, +943/-58: the field, `push()`, the load, save, delete, and
   key-move paths, 26 tests, docs).
2. Checks, played by fresh subagents through `python -m core verdict`,
   each in its own clone and its own Redis port:
   - test `gaps`: base 289 passed, head 315, no regressions; eight
     untested behaviors, the batch-load stride with several capped fields
     or instances first.
   - review (Opus, blind) `pass`, no findings, governance no.
   - docs `updated` (`25f9e8e4`), with two code points: `push()` refreshed
     only the list key's TTL, and a capped-only partial save returned the
     pipeline's first result, not the `HSET` count.
3. The join sent it to `patch` as the repair round. The patch turn metered
   $1.46 and produced `aeb94f19`: `push()` refreshes the hash TTL with the
   lists, a capped-only partial save returns 0, and 142 lines of tests.
   The first docs commit did not ride into it, as designed.
4. Checks again on `aeb94f19`:
   - test `gaps`: base 289, head 327, no regressions; eight narrower
     behaviors untested (all probed and working).
   - review `pass`, governance no, one design finding: `push()` from a
     stale copy after another process deleted the instance recreates the
     list key alone, with no expiry on a model without a TTL.
   - docs `updated` (`1cfb6000`, the first docs commit replayed and
     corrected against the patch).
5. The join (row 7: review pass, test `gaps`, repair round spent) went to
   `merge` with the gaps listed. The merge effect `f10a2751760c` is held:
   candidate `aeb94f19`, head `1cfb6000`, onto the task's own `main`. It is
   not released.

Total metered: $8.77. What Tom would have had to do from judge to the held
merge: nothing, once spending stopped refusing. The six verdicts are role
played; 1.4b replaces them with runners.

Findings for 1.4b:

5. **Hand-played checkers need the task's environment.** popoto's tests
   flush their Redis, and `tests/test_connection.py` reconnects to
   `localhost:6379`, where the live old system's Redis listens. Each
   checker ran its own `redis-server` on its own port and ignored that
   file. One checker's install landed in the rebuild's venv through an
   inherited `VIRTUAL_ENV` and was removed. The runners should give each
   check the task's provisioned Redis and environment, never the caller's.
6. **The docs head has to reach the builder's clone by hand.** A docs
   commit made in a checker's own clone was fetched into the workspace on
   a ref of its own before `verdict --head` could fetch it into the mirror.
   The docs runner should make the commit where the kernel fetches from.
7. **Breadth keeps finding gaps.** Both rounds said `gaps`, the second on
   narrower behaviors. Row 7 delivers with the gaps listed, which is what
   happened; nothing else is needed.
8. **The packaging pin.** pandas 3 breaks popoto's test collection at the
   base; the checkers pinned 2.3.3 from `uv.lock`. Setup should install
   from the lock.

The trial is done. Next: 1.4b.
