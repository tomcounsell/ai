# Hook Target Resolution

## Problem

Five `PostToolUse` validators under `.claude/hooks/validators/` gate what an
SDLC lane just wrote: `validate_no_gos_justification.py`,
`validate_documentation_section.py`, `validate_test_impact_section.py`,
`validate_verification_section.py`, and `validate_file_contains.py`. Each one
used to decide *which file to judge* by shelling out to
`git status --porcelain docs/plans/` and picking whichever untracked `.md` had
the newest mtime, discarding the hook payload on stdin that already named the
file the triggering `Write` targeted.

That guess ran against whatever checkout the hook process happened to start
in. Two SDLC lanes writing plan docs in separate `.worktrees/{slug}/` checkouts
at the same time is the common case, not the edge case, so a `Write` in one
lane routinely got judged against another lane's in-progress plan (issues
#2682, #2689). #2682 fixed one validator; #2689 covered the remaining four with
the shared module described below.

## The Contract

`.claude/hooks/hook_utils/hook_target.py` exposes three functions, and every
validator in the family resolves and filters its target through them and
nowhere else:

- `read_hook_input() -> dict` — parses the hook's JSON payload from stdin.
  Never raises: empty stdin, malformed JSON, an unreadable stream, or a
  payload that parses to anything other than an object all return `{}`.
- `target_from_hook_input(hook_input: dict) -> str | None` — the path the
  triggering `Write` (or `NotebookEdit`) actually targeted, read from
  `tool_input.file_path` or `tool_input.notebook_path`.
- `in_scope(path, directory, extension=".md") -> bool` — whether that path
  falls inside the directory/extension pair this hook watches.

The rule the whole module exists to enforce: **`None` means "nothing to
validate," never "go find something to validate."** Working-tree state, git
status, and file mtimes are never an input to target selection. A validator
that cannot name its target from the payload exits 0 rather than guessing.

### No cwd, ever

`in_scope` judges the path **as given**. An absolute payload path is never
first rewritten into a cwd-relative one, because both available cwds are the
wrong thing to consult:

- The **payload's** `cwd` need not exist. `.opencode/plugins/valor-bridge.ts`
  builds `PostToolUse` payloads with no `cwd` key at all, and on macOS a
  present one disagrees with `file_path` over `/tmp` versus `/private/tmp`. A
  scope filter that requires the rewrite to have succeeded silently exits 0 on
  those payloads — a fail-open on exactly the writes the hook exists to police.
- The **hook process's** cwd is not the lane's. Once a target is reduced to
  `docs/plans/p.md`, both the existence check and the file read resolve against
  whichever checkout the hook process started in, so a validator can report
  success for a file it never opened.

Scope is therefore a containment test on an anchored path segment, not a
`startswith` on a relative form: `docs/plans` matches `docs/plans/a.md` and
`/repo/.worktrees/slug/docs/plans/a.md`, and does not match the sibling
`docs/plans_archive/old.md` or the prefix lookalike `docs/plansomething/a.md`.
The extension is part of the filter too, so `docs/plans/helper.py` is out of
scope for hooks watching `.md` and is never asked to carry a plan section.

### Explicit argv beats the scope filter

All five validators take an optional positional path. The rule is uniform: an
explicit argv path is judged directly, bypassing the scope filter, because an
operator named it on purpose — and a path that names nothing is a user error
worth reporting (exit 2). A hook-derived path gets the opposite treatment on
both counts: it must pass the scope filter, and one that resolves to no file is
not a finding at all (exit 0). The scope filter runs before any `Path.exists()`
so an out-of-scope write is never even statted.

Both functions guard a syntactically-valid non-dict payload. Stdin of `null`,
`[1, 2]`, `"str"`, or `42` all parse cleanly with `json.loads`, and an
unguarded `.get(...)` on the result raises `AttributeError`, a traceback out
of a hook that gates every write. `target_from_hook_input` checks
`isinstance(hook_input, dict)` and `isinstance(tool_input, dict)` before
touching either, and its `or` between `file_path` and `notebook_path` is
paired with an explicit `and path` so an empty string from either key
collapses to `None` rather than surviving as a falsy-but-truthy target.

## The Five Validators

| Validator | What it gates |
|---|---|
| `validate_no_gos_justification.py` | A plan's `## No-Gos` section carries a real justification, not a punt phrase. |
| `validate_documentation_section.py` | A plan's `## Documentation` section is present and non-empty. |
| `validate_test_impact_section.py` | A plan's `## Test Impact` section is present and non-empty. |
| `validate_verification_section.py` | A plan's `## Verification` section is present and non-empty. |
| `validate_file_contains.py` | An arbitrary file (default scope `docs/plans/*.md`) contains a set of required strings, passed via repeated `--contains`. |

`validate_file_contains.py` additionally lost its `--max-age` flag along with
the mtime-scanning helpers it fed: once the target comes from the payload
instead of a directory scan, an age window has nothing left to bound. It keeps
`--directory`/`--extension` as a scope filter — a `Write` outside that
directory/extension pair is out of scope and passes through untouched, checked
before any filesystem access — and gained a positional `target_file` argument
so a CLI/test invocation can name a path explicitly instead of relying on
stdin.

Every guesser these validators used to carry is gone: three separate
`find_newest_plan_file` implementations, plus
`validate_file_contains.py`'s `find_newest_file`, `get_git_new_files`,
`get_recent_files`, `get_git_committed_files`, and
`get_committed_file_content`.

## The Import Bootstrap Convention

Validators are standalone scripts, invoked by the harness with an absolute
path through the `hook_python` shim (see
[Hook Manifest](hook-manifest.md#project-scope-the-hook_python-shim)), so
`Path(__file__).resolve()` is CWD-independent. Any validator that needs the
shared module adds this bootstrap before importing it:

```python
# Standalone script — sys.path mutation is safe (never imported as library).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hook_utils.hook_target import read_hook_input, target_from_hook_input  # noqa: E402
```

This mirrors the existing precedent in
`validate_no_destructive_git_in_worktree.py`,
`validate_no_destructive_git_in_shared_checkout.py`, and
`validate_sdlc_on_stop.py` — `hook_utils/` is the sanctioned home for logic
shared across validators that are each invoked as their own process, not a
new pattern introduced here.

**Consequence for tests:** a test file that imports a validator module directly
must put both `.claude/hooks` and `.claude/hooks/validators` on `sys.path`,
not just `validators/` — the validator's own `sys.path.insert` only runs when
the script executes as `__main__`, not when a test imports it as a module. See
`tests/unit/test_validate_sdlc_on_stop.py:12-17` for the working pattern.

## The Canonical Regression: the Tracked Anchor

Reproducing the original bug requires care, because the naive reproducer
(an unfixed checkout, one untracked deficient plan doc, nothing else in
`docs/plans/`) does not reproduce it. With nothing tracked in the directory,
`git status --porcelain docs/plans/` collapses the whole directory to a single
line, `?? docs/plans/`, whose path does not end in `.md`. The old helper's
suffix filter drops that line, the file list comes back empty, and the
validator exits 0 — looking innocent while the payload it should have read
sat unread on stdin. That false clear, not a validator that actually cleared a
file, is what produced the original "no evidence of the bug" claim.

The fixture that does reproduce it needs at least one **tracked** file in
`docs/plans/`: a committed anchor doc, plus the other lane's untracked,
deficient, newest-by-mtime plan. With the anchor present, `git status
--porcelain` reports the untracked plan as its own `?? docs/plans/other.md`
line, the old helper's suffix filter keeps it, and the validator judges *that*
file instead of the one the payload actually named — reproducing the
cross-lane misattribution instead of masking it. Against pre-fix code, the
anchor-less control exits 0 and the anchored fixture exits 2; against
post-fix code, both exit according to the payload's own target, independent
of whatever else `docs/plans/` holds.

Any future test asserting this class of validator ignores working-tree state
should include a tracked anchor in the fixture, or risk validating nothing.

The builder lives in `tests/unit/conftest.py` as the `cross_lane_repo` fixture,
a factory taking the anchor and other-lane plan bodies as arguments so each
validator's test module supplies its own. The per-module `run_hook` subprocess
helper is deliberately *not* shared — a shared harness would couple the test
files to each other, and the helper is eight lines.

## Key Files

| File | Role |
|---|---|
| `.claude/hooks/hook_utils/hook_target.py` | `read_hook_input()`, `target_from_hook_input()`, `in_scope()` — the shared contract. |
| `.claude/hooks/validators/validate_no_gos_justification.py` | No-Gos section justification check. |
| `.claude/hooks/validators/validate_documentation_section.py` | Documentation section presence check. |
| `.claude/hooks/validators/validate_test_impact_section.py` | Test Impact section presence check. |
| `.claude/hooks/validators/validate_verification_section.py` | Verification section presence check. |
| `.claude/hooks/validators/validate_file_contains.py` | Required-string content check, scoped by directory/extension. |
| `tests/unit/test_hook_target.py` | Direct unit tests for the shared module. |

## Related

- [Hook Manifest](hook-manifest.md) — the interpreter contract and
  `hook_python` shim that make the import bootstrap CWD-independent.
- Issues #2682, #2689 — the cross-lane misattribution this module fixes.
