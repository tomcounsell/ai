#!/usr/bin/env bash
# check-interpreter-pin: compare a checkout's .venv against the committed pin.
#
# Issue #2617. `.python-version` at the repo root is the committed interpreter
# pin uv reads for every `uv sync` / `uv venv` / `uv run`. A venv built before
# the pin landed, or built with `--python` overridden, or copied in from
# elsewhere, sits on a different interpreter than the baseline its results are
# compared against — and every tool downstream misreports that as something
# else (an unfixable lint block with no findings, an "unrecognized arguments"
# pytest abort, a test failure that reproduces nowhere).
#
# This is the one place that comparison is implemented; scripts/pytest-clean.sh
# and .githooks/pre-commit both call it so they cannot drift apart.
#
# Usage:  scripts/check-interpreter-pin.sh [repo_root]
#
# Exit 0: match, or nothing to compare (no pin file, or no venv yet).
# Exit 1: mismatch — a loud report is printed to stderr.

set -u

ROOT="${1:-}"
if [ -z "$ROOT" ]; then
    ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
fi
if [ -z "$ROOT" ]; then
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

PIN_FILE="$ROOT/.python-version"
PYVENV_CFG="$ROOT/.venv/pyvenv.cfg"

[ -f "$PIN_FILE" ] || exit 0
[ -f "$PYVENV_CFG" ] || exit 0

# MAJOR.MINOR is the comparison granularity: it is the axis interpreter
# behavior varies on, and uv records "3.14" for an env built from its own
# managed download but "3.14.3" for one built from a system interpreter.
major_minor() {
    printf '%s' "$1" | awk -F'[.@]' '{ if ($(NF-1) ~ /^[0-9]+$/) print $(NF-1)"."$NF; else print "" }'
}

PIN_RAW="$(grep -vE '^[[:space:]]*(#|$)' "$PIN_FILE" | head -1 | tr -d '[:space:]')"
[ -n "$PIN_RAW" ] || exit 0
# Drop a patch component so 3.14 and 3.14.3 both yield 3.14.
PIN_RAW="$(printf '%s' "$PIN_RAW" | awk -F. '{ if (NF >= 3) print $1"."$2; else print $0 }')"
PIN="$(major_minor "$PIN_RAW")"
[ -n "$PIN" ] || exit 0

VENV_RAW="$(grep -E '^[[:space:]]*version_info' "$PYVENV_CFG" | head -1 | cut -d= -f2 | tr -d '[:space:]')"
[ -n "$VENV_RAW" ] || exit 0
VENV_RAW="$(printf '%s' "$VENV_RAW" | awk -F. '{ if (NF >= 3) print $1"."$2; else print $0 }')"
VENV_VERSION="$(major_minor "$VENV_RAW")"
[ -n "$VENV_VERSION" ] || exit 0

[ "$PIN" = "$VENV_VERSION" ] && exit 0

echo "" >&2
echo "INTERPRETER MISMATCH (#2617): $ROOT/.venv is on Python $VENV_VERSION," >&2
echo "  but $ROOT/.python-version pins $PIN." >&2
echo "" >&2
echo "  Results from this venv are not comparable to a main baseline, and a venv" >&2
echo "  built off the pin is usually incomplete too (no ruff, no pytest-xdist)," >&2
echo "  which downstream tools misreport as a lint or argument failure." >&2
echo "" >&2
echo "  Fix: rm -rf .venv && uv sync --all-extras" >&2
echo "" >&2
exit 1
