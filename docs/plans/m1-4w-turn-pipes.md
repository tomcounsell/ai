---
tracking: none
slug: m1-4w-turn-pipes
type: bug
status: built
---

# 1.4w A turn's output through pipes

A bug fix in milestone 1 of [valor-rebuild.md](valor-rebuild.md), with a
second item from 1.4b's rollout. Decided by the lead by default; no
question for Tom.

**The bug.** A turn's whole stdout and stderr go to
`<work>/<task>/turns/<turn>.stdout` and `.stderr`. The harness held those
files as its standard streams, and in a provisioned task every turn profile
denies `<work>`. node aborts at startup (SIGABRT, no message) when its
stdout or stderr is a file at a path it cannot read, so Pi, which runs on
node, cannot start in a provisioned task: the critique at `reviewer_openai`
failed on 3b's merged tip (m3-pi-harness.md, Rollout).

**The fix.** The harness's stdout and stderr are pipes. The kernel copies
both at once, whole, into the same two files until EOF, so neither pipe
fills while the other is read and nothing is cut. A harness whose prompt
travels on stdin gets it through a third pipe written at the same time.
EOF comes once every process of the turn has ended: the kernel awaits the
copies after the reap, so a child the turn left holding a pipe is ended by
the reap, not waited on. A stop still kills the group at once. No sandbox
opening is added; `<work>` stays denied to every profile.

**The second item.** m1-4b-runners.md (Landing) lands a site with
`calibrated` set only when its record passes its entry check. Governance's
record on the real ledger (task `405d06d5bb53`, `task_sha256`
`e47a2161d4dd2cc39bedb7a4d0883d95f62e5048030040479040829540f11e6f`) failed
its entry check (m1-4b-records.md), yet `GOVERNANCE.calibrated` held that
digest. It is now `None`; the comment beside it names the record and why.
`BREADTH.calibrated` was already `None`. The docs runner stays
unregistered and `docs` stays on the manual path; nothing read the field
at run time apart from the digest every judgement row carries beside its
own.

Stakes 2: the kernel's turn runner.

## Threat model

The output files stay where no turn can read or write them; only the
kernel holds them. A turn sees only pipe ends, and can do with them no more
than it could with the files: write any amount, which the kernel copies
whole. A process that escaped every reap mark could hold a pipe open and
keep the turn's end waiting; the marks are the existing reap's (process
group, environment marker, sandbox name), and a sandboxed process cannot
shed its sandbox mark.

## Done, as evidence

| Done item | Evidence |
|---|---|
| A turn's stdout and stderr are pipes, and the files hold what came through them | `test_a_turns_stdout_and_stderr_are_pipes_and_its_files_hold_what_came_through_them` (fails on the old code) |
| Large output on both streams and a large stdin do not deadlock | `test_a_large_output_on_both_streams_and_a_large_stdin_do_not_deadlock`: 4 MiB on stderr before any stdout, 4 MiB on stdout before stdin is read |
| node starts in a turn under a profile that denies the work dir | `test_node_starts_when_its_turn_runs_under_a_profile_that_denies_the_work_dir` (fails on the old code) |
| The whole output still lands in the files, and a stop still ends the turn | `test_a_turns_whole_output_is_in_files_no_turn_can_write_and_its_row_names_them`, `test_stop_from_another_connection_kills_the_turn_and_leaves_a_consistent_ledger`, unchanged |
| Neither breadth nor governance is landed calibrated; docs has no runner | `test_breadth_and_governance_have_no_landed_record_so_docs_has_no_runner` |
| The critique at `reviewer_openai` passes on a provisioned task | `tests/test_pi_live.py::test_a_live_critique_at_the_openai_seat_leaves_a_verdict`, run once (Build record) |

## Docs

`docs/harnesses.md` (Running one turn) and the `core/runs.py` docstring say
the streams are pipes copied into the files, and why.

## Build record

Built on `460943b8a`, branch `m1-4w-turn-pipes`.

- `core/runs.py`: the harness gets `PIPE` for stdout and stderr (and stdin
  when it carries a prompt). `_pump` copies one pipe into its file until
  EOF; `_feed` writes stdin and closes it, ending the write when the
  harness exits without reading it all. Both pumps and the feed run
  together; the turn waits on the process's exit or a stop, as before,
  then drains the gateway, reaps, and then awaits the pumps. A cancel of
  the turn cancels them.
- `core/judgement_tasks.py`: `GOVERNANCE.calibrated` is `None`.
  `test_a_landed_calibrated_digest_is_the_task_as_the_providers_legs_render_it`
  now runs for `intake.underspecified` alone.
- Reproduced before the fix: node under a profile that denies a directory
  exits 134 with stdout and stderr as files there, and prints with them as
  a pipe. The two new pipe and node tests fail on the old `runs.py`.
- Suite: 1010 passed, 19 skipped (3b's 1007 plus four new tests, less the
  governance case of the landed-digest test). `ruff check` clean; `ruff
  format --check` flags only docs/bridges/telegram.md and
  docs/plans/m2-1-port.md.
- Live: `test_pi_live.py::test_a_live_critique_at_the_openai_seat_leaves_a_verdict`
  passed on a provisioned task (verdict `revise`), charged $0.0156 by the
  gateway.
- Follow-up for the lead: setup commands (`workspace._setup`) still hold
  `<task root>/setup/<n>.log` as their stdout and stderr, under the turn's
  profile; a node-based setup command there would abort the same way.
