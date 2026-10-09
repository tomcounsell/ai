---
tracking: none
slug: c6-vm-setup-offline
type: build
status: planned
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
output), and `read_result` adds `tail` and `output` to each setup entry and
`tail` to a failed setup's record, as the host does. `verify.ran` keeps
recording only each setup command and exit: the documented field list says
it holds no text the candidate printed, and that stays.

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
