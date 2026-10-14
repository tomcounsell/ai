# The valor spec test names main after the cutover

## Cause

`tests/test_targets.py::test_the_valor_spec_names_the_rebuild_branch` asserts
`projects/valor.toml` has `branch = "valor-cori-rebuild"`. Cutover commit
a13b21d2c ("Cutover: valor and its routines land on main") moved the spec,
and the emulator, expiry and verify routines, to `branch = "main"` on
purpose. Plan 2.4a (`docs/plans/m2-4a-valor-branch.md`, "Decided by default")
recorded that the spec changes at cutover. The spec is right and the test is
stale; the test was not updated in the cutover step.

## Build

- `tests/test_targets.py`: rename the test to
  `test_the_valor_spec_names_main` and assert `spec.branch == "main"` and
  `spec.target_branch is None`.
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
- The full suite and `ruff check .`; any other red is reported separately
  from this one, with evidence it predates this change.
