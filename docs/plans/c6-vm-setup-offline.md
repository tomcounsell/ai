---
tracking: none
slug: c6-vm-setup-offline
type: build
status: built
stakes: low
critique_rounds: 0
review_rounds: 1
patch_rounds: 0
governance_grant: none
---

# The VM's offline setup uses what the dependency build prepared; setup output is kept

Track C6 of `rebuild-finish-prompt.md`: a bug that kernel task
`f917b77bfdd3` (popoto #633, candidate `9f62a4f780c2`) hit in review. It is
a bug fix. It adds no check, gate, hook, round, review step or guard, so no
`governance_grant` is needed. Stakes are low: the dependency build script's
environment, the dependency key, `run.sh` copying setup output out, and
`read_result` reading it.

## 1. The dependency build installs for a different Python than the VM's setup

### Incident

Ledger row 2706 (`verify.ran`, `where` `vm`): exit 1, cause `commit`,
"setup failed at base and at head: uv sync --frozen --extra dev exited 1",
0.5 s each. The base image and popoto's dependency image
(`valor/popoto:91b2bbc3...`) both built. `out/result.json` holds only the
command, exit and duration, so the VM kept no reason.

### Cause

`checks/vm-deps-91b2bbc3df11.out` in the task's workspace shows the
dependency build's `uv sync --no-install-project --frozen --extra dev`
"Using CPython 3.14.8" and downloading cp314 wheels. popoto's spec sets
`UV_PYTHON = "3.12"` in `[env]`. `deps.sh` runs each command under `env -i`
with a fixed list (HOME, PATH, the cache paths) and never reads the spec's
`env`, while `run.sh` runs the setup with the spec's `env`, so the VM's
offline sync asks for Python 3.12 and needs cp312 wheels of mypy, msgpack,
regex, tiktoken, charset-normalizer and librt that the cache never got.
With `UV_OFFLINE=1` that is an exit 1 in half a second. The host test check
(`test-base-e51903535669.setup-0.out`) used CPython 3.12 and passed.

The build requirements step has the same gap: it installs them with
`--python 3.14` whatever Python the project's environment uses.

A second, smaller gap: `deps_key` hashes the base tag, the deps
Containerfile, kind, setup and lockfiles, not the spec's environment, so a
spec whose `env` changes (a different `UV_PYTHON`) reuses an image built for
the old one.

### Fix

- `deps.sh` gives every command the environment `run.sh` gives the setup:
  HOME, USER, LOGNAME, LANG and the spec's `env` (which already carries
  PATH and the cache paths), with `UV_OFFLINE` and `npm_config_offline`
  removed, since this build is the one that has the network.
- The build requirements are installed for, and the backend's hooks run
  on, the interpreter of the `.venv` the sync just made; only when no
  `.venv` exists does it fall back to Python 3.14 as before.
- `deps_key` also hashes the environment `spec.json` hands the build.

Changing `deps_key` changes the head and base digests `verify.ran` records
and reuses by: an earlier VM run is not reused after this change, and every
project's dependency image builds once more. That is the cost of the key
now covering what the image depends on.

## 2. The VM keeps each setup command's output

The host test check keeps each setup command's output in its own file and
a `tail` (the last 1500 characters) and the file's name in the run record
(`core/checks.py` `_setup`). The VM keeps neither. `run.sh` copies each
`setup-N.log` out as `out/setup-N.out` (a plain file only, as the lint
output), and `read_result` adds `tail` and `output` (the file's path under
the task's `checks/`) to each setup entry and `tail` to a failed setup's
record, as the host does.

`verify.ran` is the blind reviewer's input, and `docs/data.md` and
`verify_payload` say it holds no text the candidate printed. So each of its
setup entries gains the output file's path, never the tail: the ledger row
now says where the reason is, and no candidate text reaches the reviewer.
Putting the tail itself in `verify.ran` would change that contract and is
left to the lead.

## Tests

- Container: the popoto-shaped spec with `UV_PYTHON = "3.12"` in its env and
  a dependency that ships only per-Python binary wheels (`msgpack`) installs
  offline in the VM. Red before the fix (setup exit 1), green after.
- `read_result` returns each setup command's tail and output file, and a
  failed setup's tail.
- `deps_key` changes with the spec's environment.

## Questions for Tom

None.

## Build record

### Build (branch `c6-vm-setup-offline`)

- `core/images/base/deps.sh`: every command runs with HOME, USER, LOGNAME,
  LANG and the spec's `env` less `UV_OFFLINE` and `npm_config_offline`;
  the build requirements are installed for, and the backend's hooks run on,
  `.venv/bin/python` when the sync made one, else Python 3.14.
- `core/container.py`: `deps_key` hashes the spec's own `env`;
  `read_result` reads `out/setup-N.out` per setup entry (`tail`, `output`)
  and gives a failed setup's record its `tail`; `verify.ran`'s setup
  entries carry `output`. The module docstring says so.
- `core/images/base/run.sh`: copies each `setup-N.log` out as
  `setup-N.out`.
- `docs/data.md`: `verify.ran` lists the setup output files.
- Tests: `test_each_setup_commands_output_is_kept_as_on_the_host`,
  `test_the_dependency_key_covers_the_specs_environment`, and the
  container test `test_a_popoto_shaped_spec_installs_offline_in_the_vm`
  now with `UV_PYTHON = "3.12"`, `msgpack==1.1.2` in the dev extra, a test
  that the suite ran on 3.12, and the kept setup output. Its fixture's
  `.gitignore` also ignores `*.egg-info/`: with setuptools the host
  provisioning's sync leaves `toy.egg-info/` untracked, and the scripted
  build turn could never commit (833 turns before it was stopped).
- Red, on unchanged `b832d10ec` scripts with the new test: the dependency
  build "Using CPython 3.14.8"; the VM's `uv sync --frozen --extra dev`
  exit 1 in 0.5 s at base and head, the popoto signature
  (`~/src/valor-build-notes/c6/red-final.out`).
- Green, on this branch: both parametrizations of
  `test_a_popoto_shaped_spec_installs_offline_in_the_vm` pass in real VMs
  (setuptools 18 s with the images built, hatchling 150 s). The dependency
  build says "Using CPython 3.12.15" and installs setuptools for that
  interpreter; the VM's offline `uv sync --frozen --extra dev` exits 0 in
  0.8 s, building only the project, and `out/setup-0.out` is kept.
- Suite (`-m "not container"`): 1805 passed, 24 skipped, 4 failed. The
  four (three in `test_ports.py`, one in `test_targets.py` on "No space
  left on device") pass alone: 4 passed in 3.78 s. Ruff check and format
  clean.
