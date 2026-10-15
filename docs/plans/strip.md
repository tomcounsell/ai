---
tracking: none
slug: strip
type: build
status: waves 0-3 merged on main (2026-10-15); wave 4 holds for the Cowboy
critique_rounds: 0
review_rounds: 0
---

# The strip: developer parity for a turn, a minimal suite, and the walls taken down

From Tom's decision of 2026-10-15 to strip rather than rebuild, after
[Valor rebuild: missteps and hard rules](https://claude.ai/code/artifact/b870a299-849d-4fd7-b449-15dda22f7035)
(the missteps doc). It removes the restrictions marked Cut there, cuts the
suite to the tests that keep the kernel solid and Telegram working, and
ends when Tom has used a change Valor carried on a real project.

This plan is executed by a lead driving session that fans out subagents in
worktrees, not by the kernel's own pipeline: the pipeline is the thing
being cut, and running the strip through it would cost the review rounds,
VM reruns and governance taps it removes. One adversarial review per wave
replaces per-task critique and review rounds.

## Goal

A Valor turn can do what a developer running Claude Code in the same
repository can do. The suite proves the kernel is solid and that Telegram
carries messages both ways, and nothing else. A real task on popoto goes
from Tom's Telegram message to a merge Tom uses.

## The rules this work runs under

The missteps doc proposes ten hard rules. The strip adopts them as its
working rules from the start, and Phase 0 writes them into the repository
(Tom's call on wording, see "Tom's calls").

1. Developer parity. 2. Capability on Tom's ask. 3. Governance means any
limit. 4. One mechanism per concern. 5. Done means used. 6. Bars do not
move. 7. Real work outnumbers self-work. 8. Size budgets, enforced as
tests. 9. A bug fix is a commit. 10. Reuse before writing.

## What stays, what goes

| Area | Stays | Goes |
| --- | --- | --- |
| Turn command (`harnesses/claude_code.py`) | `-p`, JSON output, session id and resume, persona and Brief appended, the gateway as the model endpoint, a placeholder Anthropic credential, reaping | `--safe-mode`, `--strict-mcp-config`, `--setting-sources ""`, `--disallowedTools WebFetch WebSearch`, the env allowlist, `DISABLE_AUTOUPDATER`, the empty gh config, the credential-less gitconfig |
| Turn accounts | The Anthropic credential only in the gateway (metering) | Every other credential block: the turn gets Valor's own `gh` login, git credential helper, SSH agent, and `sentry-cli` auth |
| Plugins | | Absent today; added: `yudame` and `yudame-dev` via `--plugin-dir` from a kernel-kept clone of `yudame/skills`, refreshed at task start (absorbs C18) |
| Sandbox (`core/workspace.py`) | One profile per turn and service that denies the kernel key directory, the Postgres data directory, the backup disk, and loopback 5432 (the one incident: the Postgres trust hole) | Every other deny: `~/src`, vault, Documents and the rest; startup-place writes; shared temp and the `mktemp` wrapper; Apple events, `open`, mount; container denies; port bind limits; `pi_install`'s denies; the check and setup profiles |
| Processes | Reaping everything a turn started (the leaked redis incident); `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` (the killed-tests incident) | The single machine-wide turn slot (`core/slot.py`), replaced by a concurrency limit read from settings, default 2 (the RAM plan in `docs/machine.md`: each turn with its work, cluster and Redis is about 4.4 GB, so two fit a 16 GB Mac and three do not) |
| Review | A fresh session reviews the candidate in a clean worktree of its own and can run the suite there | The Apple `container` VM verifier (`core/container.py`, `core/images/`), the blind checkout's two-commit history rewrite, the `.valor`-in-tree refusal |
| Governance | The governance paragraph as text, widened by rule 3; the four seeded guards in `core/guards.py` with their expiry | The `governance.adds` classifier site, the broker's grant refusal, merge-predicate grant terms, `python -m core grant`, `core/audit_sample.py` |
| Merge predicate (`core/machine.py`) | One current candidate; test passed (or gaps with rounds spent); review passed | "Every governance instance granted" in terms 2 and 4; the docs verdict as a merge term (docs still run, they no longer hold the merge) |
| Git (`core/git.py`, `tools/push_branch.py`) | The kernel's merge to the Brief's target from the mirror; no force-push onto the target | `git.hostile`, the hostile-config refusals, the mirror import's gitfile, alternates and shallow refusals, the pack and size limits; `git.PINNED` cut to `core.hooksPath` off for the kernel's own merge |
| Binaries (`core/binaries.py`) | Absolute paths for `git`, `claude`, `sandbox-exec` | The root-owned requirement; Homebrew and user tools are found on `PATH` |
| Effect ceilings | `act` for every task Tom starts (his 2026-10-09 ruling) | `propose` as a default, child-ceiling property machinery beyond one check |
| Gateway | Metering on every model call; the Anthropic and OpenAI routes | The path allowlists (any path on the route is forwarded and metered) |
| Emulator | | `tests/emulator/`, `routines/emulator/`, emulator metering, replay arms, the takeover and cutover sweep bars |
| Expiry | The 90-day list | Its deletion merge: the routine reports to Tom, it never deletes (rule 2) |
| Bridges | Telegram, email, Slack, local: all code stays | Every bridge test except Telegram's (Tom's scope for the suite) |
| Pi and the OpenAI route | Code stays | Their tests (contract and live), until Tom asks for a second harness again |

## The minimal suite

Target: at most 15 test files and 5,000 test lines (from 96 files and
48,335 lines), under 2 minutes at `-n auto` and under 5 minutes serial on
one Mac, no `container` marker, no `macos`-only test. Rule 8 is enforced
by `tests/test_budget.py`, which fails when the suite passes either
number.

A test stays only when it asserts one of these:

1. **State machine.** A task's fold reaches the right state from its rows
   (`core/machine.py`), including stop, waiting on a question, patch after
   a failed check, and merge.
2. **Merge predicate.** A merge is performed only with one current
   candidate, a passed test, and a passed review.
3. **Ledger.** Rows are written once and read back; the ledger role cannot
   update or delete (one test).
4. **Kernel resident.** `python -m core serve` starts, takes a task, runs a
   turn through the scripted harness, collects it, and survives a restart
   with the task resumed.
5. **Stop.** Stopping a task kills its turn and everything the turn
   started (reaping).
6. **Telegram in.** A message from the operator's chat reaches the kernel
   once, starts a task, and a reply to one of Valor's messages binds to
   that task.
7. **Telegram out.** A send leaves through the outbox exactly once,
   including across a bridge crash and restart.
8. **End to end.** One pipeline test: a Telegram message starts a task, a
   scripted turn builds, the test check passes, review passes, the kernel
   merges, and Tom's chat gets the report.
9. **Rollout.** A merge of the kernel's own branch restarts it on the new
   commit, syncing dependencies when `uv.lock` changed.
10. **Metering.** A turn's model call through the gateway writes its
    opened and charged rows with the turn's id.

| Keep (trim to the assertions above) | From | Target |
| --- | --- | --- |
| `test_machine.py` | 36 tests, 725 lines | 1, 2: about 12 tests |
| `test_kernel.py` | 26 tests, 740 lines | 3, 4: about 6 |
| `test_serve.py` | 32 tests, 1,503 lines | 4, 5: about 5 |
| `test_pipeline.py` | 68 tests, 1,941 lines | 8: 2 (happy path, failed check to patch to merge) |
| `test_broker.py` | 4 tests, 117 lines | merge performed and refused: 2 |
| `test_reap.py` | 440 lines | 5: 2 |
| `test_telegram_inbound.py` | 10 tests | 6: 4 |
| `test_telegram_pipeline.py` | 9 tests | 6: 3 |
| `test_telegram_send.py` | 16 tests | 7: 4 |
| `test_telegram_outbox.py` | 6 tests | 7: 3 |
| `test_telegram_crash.py` | 5 tests | 7: 2 |
| `test_rollout.py` | 1,052 lines | 9: 3 |
| `test_gateway_meter.py` | 730 lines | 10: 2 |
| `test_live_telegram_dc.py` | 76 lines | live only, `VALOR_LIVE=1`: kept as is |
| `test_budget.py` | new | rule 8 |

Support kept, trimmed to what the kept tests import: `conftest.py`,
`scripted.py`, `scripted_upstream.py`, `judgement_upstream.py`,
`telegram_emulator.py`, `telegram_child.py`, `telegram_port.py`,
`bridges.py`, `ports.py`, `kernel_child.py`, `performers.py`. Every other
file under `tests/` is deleted, `tests/emulator/` and `tests/denials.py`
included. `tests/README.md` is rewritten to one page.

`projects/valor.toml`'s `suite` becomes `uv run pytest -q -n auto -p
no:cacheprovider --junitxml={junit}`, and the dev-port comment goes with
the port limits.

## Execution: waves and agents

The lead works in `~/src/valor-rebuild` and cuts every branch from `main`
at `strip-base`, the commit Phase 0 tags. Every agent works in its own
worktree (`isolation: worktree`), owns the files listed for it and no
others, and ends with its share of the minimal suite green and a one-line
report: files touched, lines removed, lines added, anything it could not
cut and why. An agent that needs a change in a file it does not own puts
it in its report; the lead makes it at merge.

`core/__main__.py` (the composition root) and `core/settings.py` are
shared: an agent edits only the hunks naming what it removes, and the lead
merges those branches one at a time, rebasing the next onto the last.

Opus where the cut touches the kernel's logic, Sonnet where it is
mechanical.

### Phase 0: freeze and baseline (lead, 30 minutes)

- On the Cowboy, stop new kernel tasks on the `valor` project and stop the two open
  tasks building the same feature; record their ids and plan files here.
  Tasks on other projects keep running on the old commit.
- Mark C18 absorbed by this plan; C19 (Sentry) waits until the strip
  merges and is then rewritten as "Valor's Sentry login in the turn".
- Record the baseline in this file: `wc -l` of production code and tests,
  suite time serial and at `-n auto`, test count.
- Tag `strip-base`.
- Write the ten rules into `CLAUDE.md` under the governance paragraph and
  into `persona/governance.md`, with rule 3's widened list in the
  paragraph itself, in the words Tom approves. The paragraph's "blind
  verifier asks one Jev-class boolean" sentence becomes "the reviewer
  asks whether the diff adds governance; a yes with no grant in the plan
  file is a refused merge", since the classifier goes in wave 2.

### Wave 1: the suite cut (3 agents, in parallel, about 1 hour)

The suite is cut first, against the current code, so wave 2's agents work
against 5,000 test lines instead of 48,000.

| Agent | Model | Owns | Job |
| --- | --- | --- | --- |
| T1 kernel tests | Opus | `test_machine`, `test_kernel`, `test_serve`, `test_pipeline`, `test_broker`, `test_reap`, `test_rollout`, `test_gateway_meter`, `scripted*.py`, `kernel_child.py`, `performers.py` | Trim each to the assertions 1 to 5 and 8 to 10; a test asserting a restriction in the Goes column is deleted, not fixed |
| T2 Telegram tests | Sonnet | `test_telegram_*.py`, `telegram_*.py`, `bridges.py`, `ports.py` | Trim to assertions 6 and 7; keep the live DC test |
| T3 delete and budget | Sonnet | every other file under `tests/`, `conftest.py`, `tests/README.md`, `pyproject.toml` markers, `projects/valor.toml` | Delete; strip `conftest.py` of fixtures nothing kept uses; write `test_budget.py`; drop the `container` and `macos` markers |

Exit: the minimal suite is green on `strip-base` plus wave 1, inside the
budget. The lead merges T3, then T1, then T2.

### Wave 2: the strip (8 agents, in parallel, 3 to 4 hours)

| Agent | Model | Owns | Job |
| --- | --- | --- | --- |
| A turn | Opus | `harnesses/claude_code.py`, `harnesses/pi.py`, `docs/harnesses.md` | Drop the flags and env limits in the Goes column; pass the operator's environment minus the kernel's own secrets; add `--plugin-dir` for the yudame snapshot; give the turn Valor's `gh` and git credentials. Prove the Anthropic placeholder still wins over a Keychain login: one live turn's gateway rows match its transcript's calls |
| B sandbox | Opus | `core/workspace.py`, `docs/sandbox.md`, `docs/sandbox-openings.md`, `docs/workspace.md` | Cut `profile()` to the four denies; delete `check_profile`, `setup_profile`, the `mktemp` wrapper, the tmp redirects and PATH reorder, `pi_install`, the mirror import's refusals; delete `sandbox.md` and `sandbox-openings.md` into one paragraph of `workspace.md` |
| C review | Opus | `core/fresh.py`, `core/container.py`, `core/images/`, `core/router.py` (container reap), `core/checks.py` (container use) | Delete the VM verifier; the review session gets a clean worktree of the candidate and may run the suite; delete the blind two-commit checkout and the `.valor` refusal |
| D governance | Opus | `core/judgement_sites.py` (governance site), `core/broker.py`, `core/machine.py` (`merge_predicate`), `core/audit_sample.py`, `ui/app.py`, `core/__main__.py` (grant command hunk) | Delete the classifier site, the broker's grant refusal and `UNGRANTED`, the grant terms; delete `audit_sample.py` and its page; the request judge and breadth sites stay |
| E git | Sonnet | `core/git.py`, `tools/push_branch.py`, `tools/README.md` | Delete `hostile` and the refusals; cut `PINNED` to hooks off; `push_branch` keeps only "not the target branch, no force" |
| F concurrency | Opus | `core/slot.py`, `core/runs.py`, `core/session.py` (slot use), `core/settings.py` (one setting) | Replace the single slot with a limit of concurrent turns (default 2: each turn with its work, cluster and Redis is about 4.4 GB, so two fit a 16 GB Mac); a question to Tom never waits on a slot |
| G routines and emulator | Sonnet | `routines/emulator/`, `routines/expiry/`, `core/routines.py`, `core/findings.py`, `core/__main__.py` (emulator hunk), `docs/emulator.md`, `docs/routines.md` | Delete the emulator routine and docs; the expiry routine lists, never deletes; `findings` stops reading emulator reports |
| H binaries and gateway | Sonnet | `core/binaries.py`, `core/gateway.py`, `docs/metered-spending.md` | Drop the root-owned rule; drop the gateway's path allowlists, keep metering and the credential swap |

Merge order: E, H, G, D, C, B, F, A. The lead runs the minimal suite after
each merge. After the last, one Opus review of the whole wave's diff
against this plan's tables: anything in the Goes column still present, and
anything removed that the Stays column keeps. Its findings are fixed as
commits, with no second round.

### Wave 3: docs and plans (4 agents, in parallel, about 1 hour)

| Agent | Model | Owns | Job |
| --- | --- | --- | --- |
| P1 core docs | Sonnet | `docs/architecture.md`, `docs/machine.md`, `docs/tech-stack.md`, `docs/performers.md` | Remove what wave 2 removed; one sentence per mechanism; no restriction is described as a reason |
| P2 READMEs | Sonnet | every directory `README.md` | Same; the Not-here paragraph stays word for word with `CLAUDE.md` |
| P3 plans | Sonnet | `docs/plans/` | Delete every plan file with `status: merged` or `done` (git keeps them); keep this plan, `valor-rebuild.md`, `valor-rebuild-feedback.md`, `valor-rebuild-decided.md`, `rebuild-handoff.md`, C19; rewrite `valor-rebuild.md`'s milestones as one page of what exists |
| P4 site | Sonnet | `site/` | Remove pages and graph nodes for what was cut |

Exit: `rg -i "safe-mode|sandbox_profile|container\.verify|governance\.adds|audit_sample|emulator"` finds nothing outside this plan and git history.

### Wave 4: live (lead and Tom, about 1 hour)

1. Roll the Cowboy's kernel to the stripped `main` (`python -m core`
   rollout, or a restart where the rollout refuses).
2. A Telegram round trip on Valor's account: Tom sends, Valor answers.
3. A turn on popoto: its transcript shows popoto's `CLAUDE.md` read, a
   `/yudame:` skill available, a `WebSearch` call, and `gh issue view`
   succeeding.
4. A real task: Tom names one popoto issue on Telegram; Valor carries it
   to a merge; Tom uses the result and says so; the lead records the date
   and the merge here.

## Done

- Wave 4 step 4 recorded with its date (rule 5).
- The minimal suite inside the budget, green on `main`.
- Production code smaller by at least 6,000 lines, tests by at least
  43,000; both recorded against the baseline.
- No restriction from the Goes column left (the wave 3 search).
- Every metered model call of the wave 4 task has its gateway rows.

## Tom's calls

Each has the default the plan proceeds on if Tom says nothing.

- **The rules' wording in `CLAUDE.md`.** Default: the ten rules as in the
  missteps doc, rule 3's list added to the paragraph.
- **The old system on other Macs.** Default: it stays on until wave 4 step
  4 is recorded, then Tom turns it off.
- **Email, Slack and local bridge tests.** Default: deleted per Tom's
  scope; the code stays and is exercised by use. Alternative: one smoke
  test each (about 150 lines).
- **Pi and the OpenAI route.** Default: code stays, tests go. Alternative:
  delete both until a second harness is asked for.

## Threats

- **Isolation is load-bearing.** If agent B or C finds the kernel's
  collection or review cannot work without the profile or the blind
  checkout, it stops and reports. That is the missteps doc's decision
  test: the lead brings Tom the thin-rebuild option with what B or C
  found.
- **Keychain credential leak past the gateway.** Without safe mode,
  Claude Code may read Tom's Keychain login. Agent A's live check is the
  answer; if the Keychain token wins, the turn's `CLAUDE_CONFIG_DIR` keeps
  an `apiKeyHelper`-free config with the placeholder forced, and the
  check is rerun.
- **Concurrent turns and RAM.** A turn with its work, cluster and Redis
  is about 4.4 GB (the RAM plan in `docs/machine.md`), so F's default is
  2: two fit a 16 GB Mac and three do not.
- **A small suite misses a regression.** Accepted: the end-to-end test,
  the live round trip and Tom's real use are the measure, not test count.

## Record

- **Baseline (strip-base, 2026-10-15, Tom's MacBook Pro, 10 cores).**
  Production Python 32,180 lines; tests 50,348 lines in 123 files; 2,257
  tests (2,206 passed, 47 skipped, 4 failed on the paragraph Phase 0
  rewrote) in 11 min 22 s at `-n auto`, run beside three wave 1 agents.
- **Phase 0.** Rules and the widened paragraph committed (55d21d8f6); C18
  superseded, C19 waiting. The Cowboy freeze was not done: this Mac's SSH
  key is not authorized on `valor@valor-the-cowboy`, so the freeze and
  wave 4 hold for the Cowboy.
- **Wave 1** (5ef42e1c9). 15 test files, 4,249 lines; 83 passed, 1
  skipped, in 7 s at `-n auto`.
- **Wave 2.** All eight agents merged (E, H, G, F, D, C, B, A, then D's
  second pass over the classifier's plumbing and the dead preemption
  branches). One Opus review found nine issues and no blocker; all fixed
  as commits: docs never hold the merge (a docs check that fails its tries
  records `no_change`); the gateway meters by a normal-form path and
  answers unmeterable calls (batches, OpenAI chat) 400; setup, suite and
  lint get the operator's environment and PATH, as the turn does; every
  turn and service rewrites its profile from `profile()`, so tasks in
  flight lose the old denies; a clarify turn never waits on a slot;
  `.valor/inputs` in a plan's tree no longer loops critique.
- **Wave 3.** 92 plan files deleted (8 kept); every doc and README trimmed
  to what exists; `site/` describes the old system and needed nothing.
  The exit search finds only what stays: the `sandbox_profile` harness key
  (it carries the four denies) and `tests/telegram_emulator.py` (the
  Telegram test double), plus Tom's historical rulings.
- **Live check on this Mac** (no spend: a dead gateway, the init event
  read). The real `workspace_turn` command under the cut `turn.sb`, on
  popoto: WebSearch and WebFetch offered; plugins `yudame` and
  `yudame-dev` loaded with 51 skills; popoto's own `.claude/skills`
  (`do-deploy`, `release`, ...) loaded. Under the same profile `gh` is
  logged in, `gh issue list` and `git ls-remote origin` work, and `~/src`
  reads. Agent A's local check: with the turn's own `CLAUDE_CONFIG_DIR`,
  11 of 11 model requests carried the placeholder, never the Keychain
  login.
- **Against Done.** Suite: 15 files, 4,268 lines, 83 passed, 1 skipped,
  8 s at `-n auto`, 34 s serial (from 11 min 22 s). Tests smaller by
  46,080 lines (bar 43,000: met). Production Python 32,180 to 27,961,
  smaller by 4,219 (all production files, the VM images included, net
  -4,654). **The 6,000-line bar is not met**: every row of the Goes column
  is cut, and what remains of the shortfall is code the Stays column keeps.
  Rule 6: Tom drops or keeps the bar.
- **Accepted risk, named by the review.** With the hostile-config refusals
  gone, the kernel runs `git worktree add` and `git merge` outside the
  sandbox in a mirror a turn can write; a planted `core.fsmonitor` or
  filter driver there would run with the kernel's reach. This is the
  trade the Goes column made.
- **Still to do, on the Cowboy:** Phase 0's freeze, and all of wave 4.
