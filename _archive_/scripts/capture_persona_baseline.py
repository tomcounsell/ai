"""Capture the byte-stability baseline for the composed (ENGINEER, WORKER) prompt.

Protects issue #1227's prompt-cache invariant: the composed prompt's bytes must
not drift unnoticed, because a changed prefix costs the cache.

One baseline, checked in, guarding the REPO's contribution to the prompt
(#2555). The private per-machine layer is pinned out of the way while composing:

    ~/Desktop/Valor/identity.json   -> absent, so config/identity.json stands alone
    ~/Desktop/Valor/personas/       -> absent, so config/personas/ overlays are used
    config/PRINCIPAL.md             -> tests/fixtures/persona/principal.md

Those three inputs vary per machine and none of them is in the repo, so no pull
request can drift them and no test could ever have gated them. What IS in the
repo -- the segments, the manifest, the in-repo overlays, ``WORKER_RULES``, the
composition order, ``CLAUDE.md``'s completion criteria -- is exactly what this
baseline pins, on every host, with nothing to skip.

Supersedes the per-hostname scheme. That keyed the fixture directory to
``socket.gethostname()``, so the guard ran only on a machine whose name happened
to match a checked-in directory and SKIPPED silently everywhere else. Renaming a
machine, or joining a new one, moved the guard between running and not running
with nothing in a passing run to tell the two apart.

Usage:
    python scripts/capture_persona_baseline.py           # rewrite the baseline
    python scripts/capture_persona_baseline.py --check   # report drift, write nothing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The checked-in baseline. One file, read on every host by
#: ``tests/unit/test_compose_system_prompt.py``.
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "persona" / "eng_worker_repo_baseline.txt"

#: Deterministic stand-in for the machine-local ``config/PRINCIPAL.md``.
PRINCIPAL_FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "persona" / "principal.md"

#: A path guaranteed not to exist, used to pin the private layer out of the
#: composition. Both readers fall back to their in-repo equivalents when their
#: private path is missing, which is the whole mechanism here.
_ABSENT = REPO_ROOT / "tests" / "fixtures" / "persona" / "no-private-layer-here"


def compose_repo_only_eng_worker_prompt() -> str:
    """Compose the (ENGINEER, WORKER) cell from repo-tracked inputs alone.

    Imported by ``tests/unit/test_compose_system_prompt.py`` rather than
    duplicated there. A second copy of this recipe would be free to drift from
    the one that wrote the baseline, and the test would then compare two
    different compositions and report the difference as persona drift.
    """
    import agent.sdk_client as sdk_client
    from config.enums import AccessLevel, PersonaType

    with (
        patch.object(sdk_client, "PRIVATE_IDENTITY_PATH", _ABSENT / "identity.json"),
        patch.object(sdk_client, "PERSONAS_OVERLAY_DIR", _ABSENT / "personas"),
        patch.object(sdk_client, "PRINCIPAL_PATH", PRINCIPAL_FIXTURE_PATH),
    ):
        return sdk_client.compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report whether the baseline is current; write nothing. Exits 1 on drift.",
    )
    args = parser.parse_args(argv)

    composed = compose_repo_only_eng_worker_prompt()
    current = BASELINE_PATH.read_text() if BASELINE_PATH.exists() else None

    if args.check:
        if current == composed:
            print(f"Baseline is current ({len(composed)} chars): {BASELINE_PATH}")
            return 0
        print(f"Baseline is STALE: {BASELINE_PATH}", file=sys.stderr)
        print(
            f"  recorded: {len(current) if current is not None else 'missing'}    "
            f"composed now: {len(composed)}",
            file=sys.stderr,
        )
        print("  Re-run without --check if the persona change is intentional.", file=sys.stderr)
        return 1

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(composed)
    verb = "Unchanged" if current == composed else "Updated"
    print(f"{verb}: {BASELINE_PATH} ({len(composed)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
