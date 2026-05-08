"""Capture per-machine byte-stability baselines for persona system prompts.

Reads the local hostname and writes the current `load_system_prompt()` and
`load_pm_system_prompt(work_dir)` outputs verbatim to
`tests/fixtures/{machine_name}/{dev,pm}_system_prompt_baseline.txt`.

These fixtures protect issue #1227's prompt-cache invariant: after the composed
persona refactor lands, `compose_system_prompt(...)` for the
`(DEVELOPER, WORKER)` and `(PROJECT_MANAGER, PM_READONLY, work_dir)` cells must
produce byte-identical bytes to what these baselines captured.

Idempotent — re-running overwrites the local-machine fixtures with the current
output. Safe to run on any machine; each developer/CI host commits its own
subdirectory.

Usage:
    python scripts/capture_persona_baseline.py [--work-dir PATH]

If `--work-dir` is omitted, defaults to the AI Valor Engels System work-vault
folder (`~/work-vault/AI Valor Engels System`); falls back to the current repo
root if that path does not exist.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path


def _slug_hostname() -> str:
    """Return a filesystem-safe slug for socket.gethostname()."""
    raw = socket.gethostname()
    return raw.replace(".", "-").replace("/", "-").replace(" ", "-")


def _default_work_dir() -> str:
    """Best-effort default for the PM work-vault directory.

    Tries `~/work-vault/AI Valor Engels System` (production layout). Falls back
    to the repo root if that path is missing on this machine.
    """
    candidate = Path.home() / "work-vault" / "AI Valor Engels System"
    if candidate.exists():
        return str(candidate)
    return str(Path(__file__).resolve().parent.parent)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--work-dir",
        default=_default_work_dir(),
        help="Working directory passed to load_pm_system_prompt(work_dir).",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output directory (defaults to tests/fixtures/{hostname}/).",
    )
    args = parser.parse_args(argv)

    # Import lazily so a busted sdk_client doesn't kill `--help`.
    from agent.sdk_client import load_pm_system_prompt, load_system_prompt

    repo_root = Path(__file__).resolve().parent.parent
    machine = _slug_hostname()
    out_dir = Path(args.output_dir) if args.output_dir else repo_root / "tests" / "fixtures" / machine
    out_dir.mkdir(parents=True, exist_ok=True)

    dev_prompt = load_system_prompt()
    pm_prompt = load_pm_system_prompt(args.work_dir)

    dev_path = out_dir / "dev_system_prompt_baseline.txt"
    pm_path = out_dir / "pm_system_prompt_baseline.txt"

    dev_path.write_text(dev_prompt)
    pm_path.write_text(pm_prompt)

    print(f"Captured baselines for hostname slug '{machine}':")
    print(f"  {dev_path}  ({len(dev_prompt)} chars)")
    print(f"  {pm_path}  ({len(pm_prompt)} chars)")
    print(f"  work_dir used for PM cell: {args.work_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
