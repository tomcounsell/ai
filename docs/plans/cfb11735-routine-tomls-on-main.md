---
tracking: none
slug: cfb11735-routine-tomls-on-main
type: plan
status: planned
critique_rounds: 0
review_rounds: 0
---

# The routine toml test follows the cutover to main

Fix task cfb11735ee36: two reds at the head of `main` (7aed653a0),
`tests/test_verify.py::test_the_real_tomls_name_the_valor_spec_and_print_a_plist[verify|emulator]`.

**Cause.** Commit a13b21d2c ("Cutover: valor and its routines land on
main") moved `projects/valor.toml` and the `verify`, `emulator` and
`expiry` routine tomls from `branch = "valor-cori-rebuild"` to
`branch = "main"`. That is the intended state after the cutover (the
following commits record the Cowboy running the new system on main). The
test still asserts the old branch. The code is right; the test's
assumption moved.

## What is built

1. `tests/test_verify.py:588`: assert `r.branch == "main"`.
2. `tests/test_verify.py:125-127`, the `toy` fixture: drop the
   `.replace('branch = "valor-cori-rebuild"', 'branch = "main"')`. The
   real toml already says `main`, so the replace is a silent no-op; the
   fixture's docstring ("naming that spec and `main`") stays true.

## Out of scope

- `tests/test_targets.py:207` (`test_the_valor_spec_names_the_rebuild_branch`)
  fails at the same head for the same cause. Task 5172ae8d8b4f holds it;
  touching it here would collide with that delivery.
- Historical mentions of `valor-cori-rebuild` under `docs/plans/` are
  records and stay.

## Stakes

A test-only change in well-covered code; a mistake costs one red test and
is undone by a revert.

## Tests

- `uv run pytest -q tests/test_verify.py`: both parametrized cases pass,
  and every test using the `toy` fixture still passes (showing the
  dropped replace changed nothing).
- The full suite, with any failure compared against 7aed653a0 to separate
  pre-existing reds (expected:
  `test_targets::test_the_valor_spec_names_the_rebuild_branch`) from new
  ones.
- `uv run ruff check .`

## Tech debt

None added.

## Loops

0 critique, 0 review: small, reversible, test-only.
