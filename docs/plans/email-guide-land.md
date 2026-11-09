# Land the email-guide change on main

Tom, 2026-11-09. Task 24e9d55bbc42 built and reviewed (pass, twice) the
tomcounsell/ai half of the email-guide change, but its merge stopped when
`tests/test_small_workspaces.py::test_the_clone_origin_and_mirror_borrow_the_caches_history`
errored once in fixture setup. The yudame/skills half is merged.

## Build

1. Cherry-pick, unchanged and in order, from branch `valor/24e9d55b`:
   `a81f9cb53` (creates `docs/plans/email-editorial-pass.md`; the brief
   named three commits, but the plan commit `3198806c7` edits this file,
   so it cannot apply without it), `3198806c7`, `cace8b77d`, `69d972050`.
   No wording in `skills/sdlc/converse.md` or `skills/sdlc/channel.md`
   changes.
2. Run the full suite. Pass: merge.
3. Only if the clone test errors again, a bug fix as its own commit: add
   `"GIT_ADVICE": "0"` beside `GIT_GRAFT_FILE` in `core.git.env()`, with a
   test that a kernel git call reading a repository (with the graft file
   set) prints no `hint:` lines on stderr, so an error shows git's real
   message. Rerun, read the real error, and fix its cause with its own
   commit and test. If the real error does not reproduce, the advice fix
   still lands and the delivery says the cause is still unknown.

## Out of scope

Any change to the email wording; the formatting debt in
`tests/test_kernel.py`; any gate, check or retry around the clone test.
No email or message is sent.

## Tests

- `test_an_email_is_drafted_in_full_then_edited_against_the_guide`
  (carried over): the sentence in the rendered chat text, before the draft
  rule and `email.send`, and in `channel_text()`.
- Full suite under 5 minutes, run twice to give the flaky test a second
  chance to show.
- If step 3 runs: the no-hint test above, plus a test for whatever cause
  the real error names.
