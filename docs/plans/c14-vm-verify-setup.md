---
status: build
stakes: 1
governance_grant: none
---

# C14: the review's VM verify installs a real project's setup

Stakes 1. Every `verify.ran` with `where` `vm` in the D3 runs (ledger
`valor_rebuild_test_d3`, 24 rows) failed in setup at head and at base, so
the review's VM rerun ran no test of psyoptimal or popoto. The fix is code
and tests of documented behavior; no check, gate, hook, guard or validator
is added.

Threat model: the candidate controls its tree, so its manifests and the
commands its setup runs from them. The spec's setup lines are the
operator's. The kernel never runs a candidate's choice of command in the
dependency build: the commands it runs there are derived from the spec's
setup alone.

## Shape 1: an installing command behind leading assignments

psyoptimal's setup (the draft in `cutover-data.md`, items `pso-a`, `pso-c`)
is `PATH="$PATH:/opt/homebrew/opt/postgresql@18/bin"
SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk uv sync
--frozen`. `container.INSTALLING` matched only a line starting with
`uv sync`, `npm ci` or `npm install`, so `installs` read this spec as
installing nothing: no dependency image, the run in the bare base image
(its `image_tag` is `valor/base:...`), and the offline `uv sync` failed
with nothing in the uv cache. Tip still did this.

The assignments are harmless in the VM: the `PATH` one appends a directory
that does not exist to the VM's `PATH`, and the Mac's `SDKROOT` is read by
no Linux compiler. On the host they are what the turn's setup needs
(SDKROOT for psycopg2's build under the turn profile; the `PATH` one has
been redundant since C12 put `settings.pg_bin` on a Postgres project's
PATH).

Fix: `INSTALLING` takes any number of leading `NAME=value` assignments, the
value bare or quoted. `install_commands` gives the commands as the
dependency build runs them: assignments kept, `uv sync` given
`--no-install-project`. `spec_json` writes them as `install`, and
`deps.sh` runs that list instead of matching `setup` itself, so the kernel
and the build share one rule. `deps.sh` is in the base image, so the base
digest changes and the base image builds once more; every dependency key
changes with it.

## Shape 2: popoto's offline sync exits 1

The D3 popoto rows had a dependency image (`valor/d3-pop-*:91b2bbc3...`,
`0083191a...`) and still failed. Their base image tags are
`valor/base:6ebb5fbf...` (earlier `b0ed330c...`). That digest is
`core/images/base/` as it was before C6 (`ae2390c62^`); from C6 on it is
`e599c031...`, and at this branch `ecf6bec2...` before this change. The rows'
setup entries also lack `output`, which C6 added. So the D3 verification
ran pre-C6 container code, whose `deps.sh` ignored the spec's
`UV_PYTHON = "3.12"` and filled the cache for CPython 3.14: the bug C6
recorded for kernel task `f917b77bfdd3` (`91b2bbc3...` is the very key that
incident names).

Reproduced on the host at popoto `e51903535669`: the build's
`uv sync --no-install-project --frozen --extra dev` under Python 3.14, then
the source over it and `UV_PYTHON=3.12 UV_OFFLINE=1 uv sync --frozen
--extra dev`, exits 1 in 0.1 s:

```
error: Failed to download `mypy==2.3.1`
  cause: Network connectivity is disabled, but the requested data wasn't
  found in the cache: .../mypy-2.3.1-cp312-cp312-macosx_11_0_arm64.whl
hint: `mypy` (v2.3.1) was included because `popoto[dev]` (v1.8.2) depends on `mypy`
```

The same steps as tip's `deps.sh` runs them (the build under
`UV_PYTHON=3.12`, then setuptools and wheel fetched and the backend's
editable hooks asked) make the offline sync build and install popoto 1.8.2.
Tip needs no change for this shape; the container test
`test_a_popoto_shaped_spec_installs_offline_in_the_vm` covers it.

## The cause of a setup that fails at base too

`core/checks.py` and `docs/sdlc-checks-test.md` say a failed setup is
`cause: "commit"`, and `compare` makes a head with that cause a failure,
adding `BOTH_FAIL` when the base fails too. No documented rule makes a
setup that fails the same at base another cause, so the record is kept as
it is; `base_run.cause` already shows the base failed. A rule for it would
be new, and is left to the lead.

## Tests

- `test_an_installing_command_is_told_apart_behind_leading_assignments`:
  `installs` and `spec_json`'s `install` for plain, prefixed, quoted,
  `npm` and non-installing lines.
- Container: `test_a_popoto_shaped_spec_installs_offline_in_the_vm` gains
  a case with psyoptimal's Mac prefix before `uv sync`.

## Questions for Tom

None.

## Build record

### Build (branch `mc14-vm-verify-setup`)

- `core/container.py`: `INSTALLING` takes leading assignments;
  `install_commands`; `spec_json` writes `install`; docstrings.
- `core/images/base/deps.sh`: runs the spec's `install` list.
- `docs/sandbox.md`: what a dependency image holds.
- `tests/test_container.py`: the tests above.
- Host suite: 1898 passed, 24 skipped, 53 deselected; ruff check and
  format clean. Container case: waiting on the lead's window.
