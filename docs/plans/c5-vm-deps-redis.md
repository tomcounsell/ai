---
tracking: none
slug: c5-vm-deps-redis
type: build
status: built
stakes: low
critique_rounds: 1
review_rounds: 1
patch_rounds: 1
governance_grant: none
---

# setuptools build requirements reach the VM; popoto's suite gets its Redis

Track C5 of `rebuild-finish-prompt.md`: two bugs that task D1 (task
`8ed5902a54d4`, the kernel carrying popoto #633 under the Pi harness) hit.
Both are bug fixes. Neither adds a check, gate, hook, round, review step or
guard, so no `governance_grant` is needed. Stakes are low: one changed line
of the dependency build script, one project spec line, and a container test.

## 1. The dependency build reads a setuptools backend's log as requirements

### Incident

Review's VM dependency build for popoto at `e51903535669` failed (ledger
row 2491, `step.failed`, "the dependencies at e51903535669 did not
install"). The log `checks/vm-deps-b2e740595704.out` in the task's
workspace ends with `uv pip install` refusing "Failed to parse: `running
egg_info`".

### Cause

`core/images/base/deps.sh` imports the project's build backend, calls
`get_requires_for_build_editable` and `get_requires_for_build_wheel`, and
takes everything the script prints on stdout as one requirement per line.
setuptools' hooks run `egg_info` and log `running egg_info`, `creating
toy.egg-info`, `writing ...` on stdout, so those lines reach `uv pip
install` as requirements. A setuptools project with `pyproject.toml` alone
shows it outside any container:

```
running egg_info
creating toy.egg-info
...
R []
```

### Fix

The script that calls the hooks points file descriptor 1 at standard error
while the hooks run (`os.dup2`, so a subprocess the backend starts is
covered too, not only Python's `sys.stdout`) and writes the requirement list
to the saved original stdout. The backend's log stays in the dependency
build's log; only the list reaches `uv pip install`.

### Test

`tests/test_container.py::test_a_popoto_shaped_spec_installs_offline_in_the_vm`
runs for a hatchling backend and a setuptools backend
(`requires = ["setuptools>=61"]`, `build-backend = "setuptools.build_meta"`,
packages by `[tool.setuptools.packages.find]`), whose hooks print `running
egg_info` on stdout. With `find`, `egg_info` succeeds on the manifests alone,
as popoto's does; with a fixed `packages = ["toy"]` it fails for want of
`toy/`, the hook script exits non-zero, and the build goes on without the
list at base too. It fails on the setuptools
case before the fix (the dependency build does not install) and passes
after. It is `container`-marked and runs alone.

## 2. popoto's suite fails at base and head with Redis on 6379

### Incident

The test check for task `8ed5902a54d4` was red at base and head alike:
base 2259 failing, head 2318 errors, every error a redis `ConnectionError`
to `localhost:6379`, "Operation not permitted".

### Cause

The kernel gives the suite the task's Redis as documented: the turn and
check environment (`core/workspace.py`, `_env`) carries `REDIS_URL`,
`REDIS_HOST` and `REDIS_PORT` for the task's own Redis (6402 in that task),
and popoto's `src/popoto/redis_db.py` reads `REDIS_URL`. The suite's output
shows every test passing until `tests/test_connection.py`:

```
tests/test_confidence_modulated_decay.py ....  [ 33%]
tests/test_connection.py .......FEEEEEEEE      [ 33%]
tests/test_content_field.py EEEEEEEEEEEE       [ 34%]
```

`tests/test_connection.py` connects to `localhost:6379` by hand in five
tests (`set_REDIS_DB_settings(host="localhost", port=6379)` and the async
and pool variants). The first,
`TestConnectionReconfiguration::test_set_redis_db_settings_with_valid_url`,
rebinds popoto's global client to `localhost:6379` and never restores it.
Port 6379 is the machine's live Redis, which the turn sandbox denies, so
that test fails, and every later test inherits the rebound client: the
plugin's database switch keeps the client's host and port and sets db 15,
hence `Connection(host=localhost,port=6379,db=15)` in every error.

The suite would flush db 15 of the machine's live Redis if the sandbox let
it through. The test file needs a Redis at a fixed address the kernel does
not promise.

### Fix

The project spec, not the kernel. The popoto spec's suite leaves the file
out with `--ignore=tests/test_connection.py`, the form the emulator's later
popoto run specs already carry (`~/src/valor-demo/runs/pop-a-gate-b` and
`pop-a-1-5r`):

```toml
suite = "uv run pytest -p no:cacheprovider -q -m 'not slow and not benchmark' --ignore=tests/test_connection.py --junitxml={junit} tests"
```

It is applied to the spec task D1 used
(`~/src/valor-build-notes/d1/popoto.toml`) and to the popoto spec in
`docs/plans/cutover-data.md`. The valor-demo specs `pop-a-gate` and
`pop-b-gate` keep the old suite line; they are emulator records of runs
already made and are not edited.

popoto's own fix is upstream: `tests/test_connection.py` should take its
host and port from `REDIS_URL` and restore the client it rebinds. Filing
that is a popoto issue, outside this task.

### Test

A reproduction outside the kernel: the popoto checkout at `e51903535669`,
a scratch Redis on a port in this task's block with `REDIS_URL` set, and a
sandbox profile that denies `localhost:6379`. With `tests/test_connection.py`
in the run, the tests after it error with the same `ConnectionError`; with
`--ignore=tests/test_connection.py`, they pass. The result is recorded below.

## Question for Tom

None.

## Records

### Build, round 1

- `core/images/base/deps.sh`: the hook script writes the requirement list
  to a duplicate of the original stdout and runs the backend with file
  descriptor 1 on stderr.
- `tests/test_container.py`: the popoto-shaped test runs for `hatchling`
  and `setuptools`.
- popoto spec: `--ignore=tests/test_connection.py` in
  `docs/plans/cutover-data.md` and in the D1 spec
  (`~/src/valor-build-notes/d1/popoto.toml`).

Evidence:

- `bash -n core/images/base/deps.sh` passes.
- The hook step of `deps.sh`, run on this Mac: the `as_valor` argument is
  captured by a stub `as_valor` that bash itself calls, then run with
  bash 3's `read` in place of `mapfile` and the build requirements in a
  scratch directory. Against a setuptools project with only
  `pyproject.toml`, the base script exits 2 with "error: Failed to parse:
  `running egg_info`" and the new one exits 0. Against a hatchling
  project both exit 0 and the new one installs `editables`.
- popoto at `e51903535669`, files `conftest.py`,
  `test_confidence_modulated_decay.py`, `test_connection.py`,
  `test_content_field.py`, with `REDIS_URL` at a scratch Redis on 6540 and
  a sandbox profile denying `localhost:6379`: with `test_connection.py`,
  1 failed, 47 passed, 20 errors, each "Error 1 connecting to
  localhost:6379. Operation not permitted."; with
  `--ignore=tests/test_connection.py`, 52 passed.
- `tests/test_container.py -m "not container"`: 11 passed, 15 deselected.
  Both container cases collect.
- `uvx ruff check .`: all checks passed; `uvx ruff format --check .`: 325
  files already formatted.

Not run: the container test and the full suite. The Data volume had 739 MB
free (the brief asks for 5 GB before a container run), and a full suite on
that margin could fill the volume the resident kernel writes to.

### Patch, round 1

Review round 1 asked for changes: the setuptools test case passed at base
too, because `[tool.setuptools] packages = ["toy"]` makes `egg_info` fail on
the manifests alone and `|| requires=""` takes over.

- `tests/test_container.py`: the setuptools case finds its packages with
  `[tool.setuptools.packages.find] include = ["toy*"]`.
- `core/images/base/deps.sh`: the hook script closes the saved stdout after
  writing the list.

Evidence, outside any VM: the `as_valor` argument of base (`150f1f2ba`) and
head `deps.sh`, captured by a stub `as_valor` that bash calls, run with a
`mapfile` shim for bash 3 in project directories holding only
`pyproject.toml`:

| project | base | head |
|---|---|---|
| setuptools, `packages.find` (the test's case) | exit 2, "Failed to parse: `running egg_info`" | exit 0, installs setuptools |
| setuptools, `packages = ["toy"]` (the old case) | exit 0 | exit 0 |
| hatchling | exit 0, installs editables | exit 0, installs editables |

The head run keeps `running egg_info` in the log. `uv lock` on the test's
setuptools project with `toy/` present resolves. `bash -n` passes on
`deps.sh` and on both extracted scripts. `tests/test_container.py -m "not
container"`: 11 passed, 15 deselected; both container cases collect.
`uvx ruff check .` passes; `uvx ruff format --check .`: 325 files already
formatted.

Not run: the container test. The test check runs it on this head.

## Checks and merge

Review: changes (the setuptools case passed at base), answered by the patch
above; the lead took the patch as the review's own fix. Docs: updated,
`bb4edf295`, carried onto this head. Test: gaps. The suite with `-m "not
container"` passed apart from five tests that pass alone (three
`test_ports` StopIteration under load, two from a container fixture the
checker ran beside the suite). Hook probes outside a VM: output from
`print`, a subprocess, `os.system`, and a raw write to fd 1 all reach
stderr; hatchling gives `editables~=0.3`; setuptools, the toy project and
popoto's own `pyproject.toml` at `e51903535669`, give an empty list; a
backend that raises gives an empty list. The container test did not run
to the end: the data volume filled during it, in the scripted turn before
the VM dependency build. The lead merged on that evidence; the in-VM
evidence is D1's review step on popoto after the rollout, and the
container test runs in the next test check with room on the disk.
