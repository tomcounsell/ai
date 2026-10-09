#!/bin/bash
# The dependency build (core/images/deps/Containerfile), as root, with the
# network open. Runs the spec's installing setup commands as `valor` in
# /work, which holds only the project's manifests: each `uv sync` with
# --no-install-project, each `npm ci` or `npm install` as written. Every
# other setup command needs the source and runs offline in the VM
# (run.sh). For a Python project with a build system, its build
# requirements and what its backend adds for an editable build are fetched
# into the uv cache, so the offline sync can build the project itself.
set -euo pipefail

spec=/valor/deps.json
valor_env=(
  HOME=/home/valor USER=valor LOGNAME=valor LANG=C.UTF-8
  PATH=/usr/local/bin:/usr/bin:/bin
  UV_CACHE_DIR=/home/valor/.cache/uv UV_PYTHON_INSTALL_DIR=/home/valor/.local/share/uv/python
  UV_LINK_MODE=copy npm_config_cache=/home/valor/.npm
)

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
mapfile -d '' commands < <(jq -j '.setup[]? | "\(.)\u0000"' "$spec")
for command in "${commands[@]}"; do
  case "$command" in
    "uv sync"*) as_valor "uv sync --no-install-project${command#uv sync}" ;;
    "npm ci"* | "npm install"*) as_valor "$command" ;;
    *) echo "runs in the VM: $command" ;;
  esac
done

if [ -f pyproject.toml ] && [ "$(jq -r '.kind' "$spec")" != "node" ]; then
  as_valor 'requires=$(uv run --no-project --python 3.14 python -c "
import tomllib
print(chr(10).join(tomllib.load(open(\"pyproject.toml\", \"rb\")).get(\"build-system\", {}).get(\"requires\", [])))
")
if [ -n "$requires" ]; then
  mapfile -t reqs <<<"$requires"
  uv pip install --python 3.14 --target /tmp/build-requires "${reqs[@]}" || exit
  # hatchling, for one, adds editables for an editable build. The setuptools
  # hooks log `running egg_info` on stdout, so file descriptor 1 is stderr
  # while the backend runs and only the list goes to the original stdout.
  requires=$(PYTHONPATH=/tmp/build-requires uv run --no-project --python 3.14 python -c "
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
    uv pip install --python 3.14 --target /tmp/build-requires "${reqs[@]}" || exit
  fi
  rm -rf /tmp/build-requires
fi'
fi
