# The valor spec test names main after the cutover

## Cause

`tests/test_targets.py::test_the_valor_spec_names_the_rebuild_branch` asserts
`projects/valor.toml` has `branch = "valor-cori-rebuild"`. Cutover commit
a13b21d2c ("Cutover: valor and its routines land on main") moved the spec,
and the emulator, expiry and verify routines, to `branch = "main"` on
purpose. Plan 2.4a (`docs/plans/m2-4a-valor-branch.md`, "Decided by default")
recorded that the spec changes at cutover. The spec is right and the test is
stale: the cutover updated neither `tests/test_targets.py` nor
`tests/test_verify.py`.

## Build

- `tests/test_targets.py`: rename the test to
  `test_the_valor_spec_names_main` and assert `spec.branch == "main"` and
  `spec.target_branch is None`.
- `tests/test_verify.py:588`
  (`test_the_real_tomls_name_the_valor_spec_and_print_a_plist`, verify and
  emulator): assert `r.branch == "main"`. Both cases are red for the same
  cause, since `core/routines.py` reads the routine's `branch` verbatim.
- `tests/test_verify.py:125-127`: drop the `.replace('branch =
  "valor-cori-rebuild"', 'branch = "main"')` on the copied verify
  routine.toml. The source no longer holds that string, so the replace is
  dead and the copy already says `main`.
- `projects/README.md` line 23: "(`valor` names the rebuild's branch)"
  becomes "(`valor` names `main`)".
- `tests/test_live_push.py` docstring: "(the rebuild branch, in the kernel
  checkout)" becomes "(`main`, in the kernel checkout)".

## Out of scope

- `projects/valor.toml` and the routine specs: already correct.
- Historical plan records under `docs/plans/` that name
  `valor-cori-rebuild`: they record what happened then and stay as written.
- No check, gate, or guard is added.

## Tests

- The renamed test passes against the real `projects/valor.toml`.
- `tests/test_targets.py` in full, which also covers a spec without `branch`
  falling back to the remote default and a named branch targeting it.
- `tests/test_verify.py` in full: the two real-toml cases, plus the toy
  fixture tests, to show they still pass without the dropped replace.
- `git grep -n valor-cori-rebuild -- ':!docs/plans/'` comes back empty at
  the end of the build.
- The full suite and `ruff check .`; any other red is reported separately
  from this one, with evidence it predates this change. Collection needs a
  free port in `VALOR_TEST_PORTS`; if the sandbox has none, run with the
  range set to free dev ports and say so.
