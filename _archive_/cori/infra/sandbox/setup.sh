#!/bin/sh
# Machine setup for the sandbox. Tech stack §6, plan 06 task 2.
#
# Idempotent: every step checks before it acts and prints "ok" when there was
# nothing to do, so a second run prints no "created" and no "built". The only
# network a profile uses is cori-hostonly (seams §1.9); cori-egress was a spike
# artifact and this script neither creates it nor removes one that exists.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=${CORI_SANDBOX_ROOT:-$HOME/.cori/sandboxes}
image=cori-base:3.14
network=cori-hostonly

# 1. The runtime.
if container system status >/dev/null 2>&1; then
    echo "ok: container system is running"
else
    echo "created: container system start"
    container system start --enable-kernel-install
fi

# 2. Rosetta. The builder VM needs it even for arm64 images (spike 06).
if [ -d /Library/Apple/usr/share/rosetta ]; then
    echo "ok: rosetta is installed"
else
    echo "created: rosetta"
    softwareupdate --install-rosetta --agree-to-license
fi

# 3. The host-only network, internal: it reaches the Mac and nothing else.
if container network ls 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$network"; then
    echo "ok: network $network"
else
    echo "created: network $network"
    container network create --internal "$network"
fi

# 4. The base image. Built only when absent, so a rerun leaves the tag alone.
if container image ls 2>/dev/null | awk 'NR>1 {print $1":"$2}' | grep -qx "$image"; then
    echo "ok: image $image"
else
    echo "built: image $image"
    container build -t "$image" -f "$here/base.Dockerfile" "$here"
fi

# 5. The sandbox root: one directory per space lives under it.
if [ -d "$root" ]; then
    echo "ok: sandbox root $root"
else
    echo "created: sandbox root $root"
    mkdir -p "$root"
fi
