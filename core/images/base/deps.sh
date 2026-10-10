#!/bin/bash
# The dependency build (core/images/deps/Containerfile), as root, with the
# network open. Runs the spec's installing setup commands as `valor` in
# /work, which holds only the project's manifests: the spec's `install`
# list, which the kernel writes (`container.install_commands`): each
# `uv sync`, `npm ci` or `npm install` setup command, leading assignments
# kept, `uv sync` given --no-install-project. Every other setup command
# needs the source and runs offline in the VM (run.sh). For a Python
# project with a build system, its build requirements and what its backend
# adds for an editable build are fetched into the uv cache for the Python
# of the project's environment, so the offline sync can build the project
# itself.
#
# Every command gets the environment run.sh gives the setup (the spec's
# `env`, which holds PATH and the cache paths), less the two variables that
# make uv and npm offline: what is fetched here is what the setup asks for.
set -euo pipefail

spec=/valor/deps.json
mapfile -d '' spec_env < <(jq -j \
  '.env | del(.UV_OFFLINE, .npm_config_offline) | to_entries[] | "\(.key)=\(.value)\u0000"' "$spec")
valor_env=(HOME=/home/valor USER=valor LOGNAME=valor LANG=C.UTF-8 "${spec_env[@]}")

as_valor() {
  # Output to a file, waited on by exit, the rest of the group killed after.
  local log
  log=$(mktemp /var/log/valor/deps.XXXXXX)
  setsid setpriv --reuid valor --regid valor --init-groups \
    env -i "${valor_env[@]}" /bin/bash -c "$1" >"$log" 2>&1 </dev/null &
  local pid=$! code=0
  wait "$pid" || code=$?
  kill -KILL -- "-$pid" 2>/dev/null || true
  cat "$log"
  return "$code"
}

cd /work
mapfile -d '' commands < <(jq -j '.install[]? | "\(.)\u0000"' "$spec")
for command in "${commands[@]}"; do
  as_valor "$command"
done

if [ -f pyproject.toml ] && [ "$(jq -r '.kind' "$spec")" != "node" ]; then
  as_valor 'requires=$(uv run --no-project --python 3.14 python -c "
import tomllib
print(chr(10).join(tomllib.load(open(\"pyproject.toml\", \"rb\")).get(\"build-system\", {}).get(\"requires\", [])))
")
# The interpreter of the environment the sync made, else Python 3.14.
python=3.14
[ -x .venv/bin/python ] && python=$PWD/.venv/bin/python
if [ -n "$requires" ]; then
  mapfile -t reqs <<<"$requires"
  uv pip install --python "$python" --target /tmp/build-requires "${reqs[@]}" || exit
  # hatchling, for one, adds editables for an editable build. The setuptools
  # hooks log `running egg_info` on stdout, so file descriptor 1 is stderr
  # while the backend runs and only the list goes to the original stdout.
  requires=$(PYTHONPATH=/tmp/build-requires uv run --no-project --python "$python" python -c "
import importlib, os, tomllib
out = os.fdopen(os.dup(1), \"w\")
os.dup2(2, 1)
system = tomllib.load(open(\"pyproject.toml\", \"rb\")).get(\"build-system\", {})
module, _, attr = system.get(\"build-backend\", \"setuptools.build_meta:__legacy__\").partition(\":\")
backend = importlib.import_module(module)
for part in filter(None, attr.split(\".\")):
    backend = getattr(backend, part)
extra = set()
for hook in (\"get_requires_for_build_editable\", \"get_requires_for_build_wheel\"):
    if hasattr(backend, hook):
        extra.update(getattr(backend, hook)())
out.write(chr(10).join(sorted(extra)))
out.close()
") || requires=""  # a hook that needs the source: the offline sync says so
  if [ -n "$requires" ]; then
    mapfile -t reqs <<<"$requires"
    uv pip install --python "$python" --target /tmp/build-requires "${reqs[@]}" || exit
  fi
  rm -rf /tmp/build-requires
fi'
fi
