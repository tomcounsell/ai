---
tracking: none
slug: m3-pi-harness-record
type: record
---

# 3b Pi harness: build, patch rounds and rollouts

The plan is [m3-pi-harness.md](m3-pi-harness.md).

## Build record

Built on branch `m3b-pi`, against 3a's interface (branch `m3-harnesses`,
not yet merged).

- **Pi.** Pinned at 0.73.1 (`PINNED`), selected with the `VALOR_PI`
  setting. The prompt goes on stdin; the system prompt and
  `--no-context-files` are always passed. The contract case plants
  `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`, `AGENTS.md`, `CLAUDE.md`, and
  `.pi/settings.json` and asserts none reaches the model. A blind checkout
  leaves `.pi/` out of the tree for every harness (the diff and both
  commits keep it); a working clone is untouched.
- **Contract suite.** `tests/test_harness_contract.py`, both harnesses
  against the real binaries under real turn profiles, a real gateway, and
  `tests/scripted_upstream.py`. The Pi parameter skips unless the gateway
  has an OpenAI route (3a) and Pi is at `PINNED`; it passes with 3a's
  gateway overlaid.
- **Corrections and subagents.** A Claude Code subagent does not receive
  the correction: the case asserts that absence, and the docs name the
  gap. A fix is a kernel decision (disallow the Task tool, or put
  corrections in the subagent's prompt) and is not added.
- **Compaction.** In Pi's print mode the stream ends on `compaction_start`
  while the session still saves the compaction entry; `parse` records a
  start as unfinished and upgrades it on `compaction_end`. Measured live
  (`tests/test_compaction_live.py`): Pi compacted at 914,292 input tokens
  against a threshold of 905,616, ran on at 316,591, and the session cost
  $8.87 at the gateway against $2.63 Pi reported. Claude Code's calls fell
  from 947,155 to 75,066 at its compaction, in 38 calls for $17.27.
- **Context window.** Pi's `contextWindow` is the price table's
  `context_window` less its `max_output`, 922,000, from `pi.context_window`.
  The API refuses a request above that (901,587 input tokens accepted,
  950,000 refused), so the whole 1,050,000 would have Pi compact only
  after a failed call. No second constant.
- **Assumptions about 3a.** Model id `gpt-6.1-sol`; `openai_prices`
  returns tiers (`tiers.default.base`); `Gateway(openai_upstream=,
  openai_credential=)`; route `<gateway>/t/<token>/openai/v1`. Rebased
  onto 3a at 2861e2f5b, then again onto 3a's final head b1fbdffcb; they
  held. `pi.context_window` finds the model through `openai_prices`
  (exact or dated id), and a test covers a dated id and `-pro`.
- **Live runs.** One Pi turn on GPT-6.1 (Pi reported $0.0083, the gateway
  charged $0.0093), a critique at `reviewer_openai` on a recorded candidate
  (verdict `revise`, $0.0086), and the two compaction sessions above. Total
  live spend about $77, much of it failed attempts while the compaction
  sessions were being sized (a Claude Code reader that stopped at truncated
  output, a Pi session that quit early, two Pi attempts that overflowed the
  window): metered by the gateway, nothing else.
- **Not run.** Rollout steps 3 and 4 (the pair of reviews after 1.4c, popoto
  #633 after 1.5).

### Patch round 1

From the review (`changes`) and the test check (`gaps`):

- **Blind checkout.** The sparse pattern is `!/.pi`, so a committed `.pi`
  link is left out as well as a directory; a test commits `.pi` as a link
  to a directory.
- **Pi install.** `settings.pi` defaults to `/opt/homebrew/bin/pi`, not a
  PATH lookup. Every turn profile denies writing the directory above the
  first `node_modules` of the resolved `VALOR_PI` (`workspace.pi_install`),
  with a test. `docs/pi.md` says both. The machine's own Pi (0.66.1) is
  not upgraded here; that is rollout step 1.
- **Stop and reap.** The case runs a `sleep` the turn backgrounds, records
  its PID and process group before the stop, and asserts after it that the
  PID and every process of the group are gone.
- **Docs.** An unknown session exits 1 (`docs/pi.md`, `parse`). The Files
  list no longer names a moved `test_session.py` test.
- **Test gaps.** The `~/.pi` denial is tested under the workspace profile
  too; the metered case asserts the turn token header. `docs/pi.md` says
  how to run the Pi cases (`VALOR_PI`).
- **Evidence.** The Pi cases ran with `VALOR_PI` set to
  `~/.cache/valor-pi-0.73.1/node_modules/.bin/pi`: `tests/test_pi.py` and
  `tests/test_harness_contract.py` 62 passed, 1 skipped (Pi has no
  subagents).

## Checks after patch round 1, at 74431ce59 (review round 1 of 1)

- Test: `gaps`. With `VALOR_PI` at the 0.73.1 install, 693 passed and 14
  skipped; the default run skips about 20 Pi cases on the version
  mismatch. Real `sandbox-exec` refuses writes to the Pi install under
  both profiles. The `~/.pi` test passes with the denial removed: it runs
  `sandbox-exec` without `-D GATEWAY_PORT` and `-D VALOR_TURN`, so the
  profile fails to load before `cat` runs. Pass the `-D` flags as
  `tests/test_demo_sandbox.py` does.
- Review: `changes`; governance boolean no; no invented caps. `_node()`
  finds `node` with `shutil.which`, and `~/.bun/bin` and `~/.opencode/bin`
  come before `/opt/homebrew/bin` on the kernel's PATH; a turn can write
  both, so a planted `node` runs the `reviewer_openai` Pi session, which
  writes the verdict. Minor: `pi_install()` returns nothing when the Pi
  path has no `node_modules`, and does not cover a `VALOR_PI` that is a
  link outside the install.
- Docs: `updated`, 0c3d8a438 on `m3b-docs2`.

## Delivery: delivered, not passed

The review rounds are spent. The recommendation is one more patch:
`node` at a fixed path, as 1.4v does for `claude` and the Postgres
programs (`docs/plans/m1-4v-binary-paths.md`); `pi_install()` covering an
install with no `node_modules` and the resolved target of `VALOR_PI`; the
`~/.pi` test given its `-D` flags.

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild-feedback.md, Tom's feedback of 2026-10-03).

Scope: the Delivery's recommendation (`node` at a fixed path, after 1.4v; `pi_install()` covering no `node_modules` and the resolved `VALOR_PI` target; the `~/.pi` test given its `-D` flags). Pi 0.73.1 is installed by the build session at rollout, as planned.

### Patch round 2

Rebased onto `valor-cori-rebuild` at ca620a91f with 3a's commits dropped
(3a is merged there under other SHAs); the conflicts in `core/runs.py`
(`harness_version` added to the merged `turn.started`), `docs/README.md`,
and `docs/architecture.md` were resolved onto the merged text.

- **`node` at a fixed path.** `settings.node` defaults to
  `/opt/homebrew/bin/node`, inside the prefix every turn profile denies
  writing; `VALOR_NODE` overrides it. The `shutil.which("node")` lookup is
  gone, so a `node` planted ahead on the kernel's PATH is never run. A
  missing `node` fails at exec. Test: the default holds with a fake `node`
  first on PATH, and `VALOR_NODE` wins.
- **`pi_install()`.** It returns the directory above the first
  `node_modules` of the resolved `VALOR_PI`, or the resolved target's own
  directory when there is none, plus the directory that holds `VALOR_PI`
  itself (where a link could be replaced). Tests: no `node_modules`, and a
  link whose target sits in another install.
- **The `~/.pi` test.** It runs `sandbox-exec` with `-D GATEWAY_PORT` and
  `-D VALOR_TURN`, reads a file in the work directory first to show the
  profile loads, then asserts the refusal is "Operation not permitted".
- **Docs.** `docs/pi.md` says all three; `docs/architecture.md` stays under
  600 lines.
- **Checks.** `tests/test_pi.py` and `tests/test_harness_contract.py` with
  Pi 0.73.1 installed: 65 passed, 1 skipped. Full suite: 767 passed, 17
  skipped. `ruff check` clean; `ruff format --check` flags only
  `docs/bridges/telegram.md` and one `docs/plans/m2-1-*.md`.

### Patch round 3

The lead's decision after the round 2 review and test: three fixes, each
reproduced first under the real turn profile (`kws.profile`, `sandbox-exec`)
and then covered by a test.

- **Ancestors of the install.** With the layout `docs/pi.md` names under a
  turn-writable parent, a turn renamed the parent away and put its own tree
  there, so `pi._entry()` pointed at the turn's `cli.js`. The profile now
  also denies writing, as `literal`, every ancestor of each denied
  directory and each symlink component of `VALOR_PI` and `VALOR_NODE`
  (`workspace.pi_install_held`). Creating entries beside them still works.
  The denies close a read of turn-owned state the threat model names; they
  are not a new check.
- **The link's directory.** `pi_install` takes it with `os.path.realpath`
  on the directory only, not on the link, because Seatbelt matches resolved
  paths. Test: a `VALOR_PI` spelled through a symlinked parent.
- **`VALOR_NODE`** stays as an override (the lead's decision). Its resolved
  interpreter goes through the same denials as Pi's install: its directory,
  the ancestors, the symlink components. `docs/pi.md` says so.
- **Tests** (`tests/test_pi.py`): ancestor rename refused with the docs
  layout, the symlinked parent, and a node outside Homebrew, each under
  `sandbox-exec`.
- **Checks.** Each new test fails on the round 2 code and passes now. Full
  suite with `VALOR_PI` set: 770 passed, 17 skipped. With it unset: 769
  passed, 17 skipped, and one "the task's Postgres did not start" port
  flake that passes alone. `ruff check` clean; `ruff format --check` flags
  only `docs/bridges/telegram.md` and one `docs/plans/m2-1-*.md`.

### Patch round 4

The lead's decision after the round 3 review: links reached through other
links were not held. Reproduced under the real turn profile with temp trees:
a two-hop chain whose middle link sits in a writable directory, and a
symlinked directory inside the link's target replaced by the turn.

- **Fix.** `workspace.pi_install_held` follows `VALOR_PI` and `VALOR_NODE`
  hop by hop (`_links_on_the_way`): each symlink among a path's components,
  then the components of its target, and so on. Every link found, by its
  spelling and by its resolved directory, goes in as a `literal` write deny
  with all of its ancestors, so the middle directory cannot be renamed
  either. No new check; the same deny read.
- **Tests** (`tests/test_pi.py`), each for `VALOR_PI` and `VALOR_NODE`
  under `sandbox-exec` on temp trees: the two-hop chain, and the symlinked
  directory inside the target. Each fails on the round 3 code.
- **Not changed.** The `~/.node_modules` note stays, as the lead said.

### Patch round 5

The round 4 review found `_links_on_the_way` resolved `..` by text while the
kernel follows a link first and goes up from where it points: a middle link
whose target is `sub/../inst/node_modules/p/cli.js`, with `sub` a symlink,
left `sub` unheld.

- **Fix.** The walk is one component at a time from `/`: `..` goes to the
  parent of the directory already reached, a link is recorded and its
  target's components go in front of the rest, and it stops after 32 hops
  (`MAXSYMLINKS`, macOS's limit, a protocol fact). No normpath or abspath.
  `_program_dirs` joins the working directory instead of `abspath`, so the
  operator's spelling is not normalized either.
- **Test** (`tests/test_pi.py`), for `VALOR_PI` and `VALOR_NODE` under
  `sandbox-exec`: the `sub/../inst/...` shape; replacing `sub` is refused.
- **Docs.** `docs/pi.md` says exactly what is held (the path to the
  program and every link on it with their ancestors, and the install
  directory) and that an install whose other directories are symlinks
  (pnpm, `npm link`) is not held, so Pi is installed by Homebrew or plain
  npm. Lead decision: those layouts are not covered in code.

## Merged

Merged 2026-10-04 by the merge train, fast-forward to abcd8da75.

- **Checks.** review-3b-p5 `pass` on 7b426b15a (governance boolean: no).
  test-3b-p5 `pass` (base 774 passed, head 776 passed, 17 skipped each).
  docs-3b-p5 `no_change`.
- **Rebase.** Squashed from m3b-pi at 7b426b15a onto 1.4u's merge. Folds:
  1.4v's fixed `claude` path stays and `pi` and `node` are added at fixed
  Homebrew paths; a turn's stdout and stderr go whole to files (1.4u) and
  a prompt on stdin goes through `communicate`; `fresh_dir` keeps
  `rmtree` and makes `pi`; the docs runner takes its seat's harness
  through `resolve_seat`, as critique does; harnesses-codex-pi.md gives
  way to harnesses.md's Pi section, and the Pi install denial goes into
  sandbox-openings.md.
- **Suite.** 1007 passed, 19 skipped; `ruff check` clean; `ruff format
  --check` flags only docs/bridges/telegram.md and docs/plans/m2-1-port.md.
- **Backup.** valor_rebuild-20261003T202331Z.dump.
- **Rollout.** 1: `openai-key` reports the key kept; Pi is 0.73.1. 2
  (backup first; spend metered by the gateway): working turn: passed, Pi reported $0.0157, the gateway charged $0.0180.
  Compaction survived: passed. Pi compaction: 8 calls, input 914,278 then
  591,229 and 323,666, charged $8.84, Pi reported $2.62. Claude Code
  compaction: 37 calls, 971,417 down to 78,312, charged $17.41. Critique
  at `reviewer_openai`: failed. node aborts at start (signal 6) because
  the turn's stdout and stderr files (`<work>/<task>/turns/`) sit where
  the fresh profile denies reads, and node needs `file-read-metadata` on
  its standard streams. The same case passed at 7b426b15a ($0.0160).
- **Follow-ups.** The turn output files under a denied path: in a
  provisioned task `<work>` is denied to every profile, so a working Pi
  turn there likely aborts too (not run). Steps 3 and 4 wait for 1.4c, 1.5.

## Rollout 4: Pi carries #633

Run 2026-10-09 on the resident kernel and the real ledger, popoto at base e5190353 with the pop-a request text, `--ceiling act --harness pi --model gpt-6.1-sol`. The finished task is `f917b77bfdd3`. It reached the merge stage and the merge is held (effect 481509881e10, no grants) until a release. Two earlier tasks were stopped and are not part of the result.

- **Model id.** `--model gpt-6.1` fails at plan ("no context window for gpt-6.1: the OpenAI price table has no entry"); the table holds `gpt-6.1-sol`. That first task (`cc93b252fa49`) was stopped at $0.000045.
- **Plan question.** The plan turn asked whether repeated list-like access could reuse a cached result. Valor answered as a stand-in (`--by valor --role-played`) with Tom's recorded contract: `len()` still executes, re-iteration re-queries, builder mutators drop the parked result.
- **Stages.** judge precise; plan; critique (1 round, no revision); build (candidate 9f62a4f780c2, Pi on gpt-6.1-sol); test pass; review pass (in the VM); docs no_change, recorded by hand because docs has no runner; join passed; merge held.
- **Redis 6379 red.** With the suite as the project's first spec, the test check was red at base and at head alike: base 2259 failing, head 2318 errors, 1135 passed, 1 failed. Every error is a `ConnectionError` to `localhost:6379` ("Operation not permitted"); `tests/test_connection.py` does not use the kernel's `REDIS_URL` port. The candidate did not cause it. The spec now ignores that file and the test check passes at head.
- **Earlier failures, all kernel side.** The first task's review failed on disk space (data volume full) twice, once at the base image build and once at the image export. Between them `deps.sh` read the build backend's `running egg_info` line as a requirement; that was fixed in C5. A step that fails leaves the task waiting, and the kernel keeps the task's locks, so `core run` answered "already running" until the kernel was restarted.
- **Offline dependency sync.** In the VM the suite's `uv sync --frozen --extra dev` exits 1 within half a second at base and at head. The result keeps no setup stderr, so the cause is not known. The check still recorded pass, so the VM suite did not run for popoto and the review pass does not stand for a suite run. Task C6 covers the sync failure and the dropped setup output.
- **Spend, task f917b77bfdd3, metered by the gateway.** Judge $0.000045, plan $0.0850, critique $0.5189, build $0.3849, test breadth $0.00036, review $0.7393, governance $0.0008; total $1.7293. The two stopped tasks cost about $1.17 and $0.00005.
- **Review pass missed a contract violation.** At candidate 9f62a4f780c2 the review (ledger row 2755, seat `reviewer`, no VM suite behind it) passed `src/popoto/models/query.py` where `__len__` (line 1926) answers from the snapshot that `_materialize` (lines 1802 to 1806) returns whenever `_result_cache` is set. A for loop parks it through `__iter__` (line 1920) and `all()` (line 1799), so a later bare `len()` read stale after writes. `__getitem__`, `__bool__` and `__contains__` read the same snapshot. Tom's contract says a bare `len()` still executes and re-iterating re-queries. The candidate's doc said "Database writes do not refresh that snapshot". This is evidence for the paired reviews.
- **Patch round.** Tom's recorded review of a first fix went in verbatim as feedback (role-played). The patch (candidate d79ab98635fa) removes the persistent cache; only `list(builder)` reuses one hydration through its length hint. The test check passes at it. The review step then failed because the VM deps image was no longer held; the task waits on the kernel.
- **Final state.** After the kernel restarted, review passed at d79ab98635fa (row 2908). The reviewer ran the 40 unit tests itself, because the kernel's own run has no per-test results (uv sync failed at base and head) and Redis was unreachable from its checkout. Docs was recorded `no_change` by hand. The merge then ran without a release (effect bc63ccc24b59, 0 grants) into the task's local origin, so the task is merged. Total spend $2.8914.

## Rollout 3: paired reviews on #633

Run 2026-10-10 on the real ledger, task f917b77bfdd3, through a driver outside the repo that calls `review_runner` at each seat. Each candidate had one VM `verify.ran` that both seats read (C6's dependency key differed from the earlier digest, so the first seat reran the VM). The Claude Code seat ran as the model id `claude-opus-5-5`, the `reviewer` seat's own harness and model, because the registered seat name on a merged task does nothing. Both results are `review.compared` rows and move nothing.

| Candidate | VM suite | `reviewer` (claude-opus-5-5) | `reviewer_openai` (gpt-6.1-sol) |
| --- | --- | --- | --- |
| d79ab98635fa (patched) | 3394 passed, 17 errored as at base | pass, $0.4797 | pass, $0.1208 |
| 9f62a4f780c2 (original) | 3369 passed, 17 errored as at base | changes, $0.3633 | pass, $0.1099 |

On the original candidate, `reviewer` reproduced the stale `len()` (a for loop saw 3 rows, the database held 4, `len()` said 3, and the empty case said 0 where 1 was fresh) and called the snapshot documentation a changed contract. `reviewer_openai` described the same snapshot as the plan's deliberate semantics and passed it. The earlier registered review of that candidate (row 2755) also passed it.

Both seats had Tom's recorded review of the stale `len()` in the task's feedback by the time they ran; the original review did not. The pair therefore shows one seat catching it and one not, given that feedback, not what either would catch without it. Total spend for the four reviews: $1.0737.
