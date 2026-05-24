"""CLI: decompose a plan doc into independent work units for multi-dev fan-out.

Produces a JSON array of independent task groups extracted from a plan's
``## Implementation Plan`` section, suitable for the SDLC PM session to spawn
one Dev sub-session per unit (see ``docs/plans/sdlc-1393.md`` Phase 1).

Usage::

    sdlc-decompose docs/plans/{slug}.md
    sdlc-decompose docs/plans/{slug}.md --max-units 3

Schema (each element of the printed JSON array)::

    {
        "unit_id": "u1",                       # snake_case, non-empty string
        "description": "short summary",        # non-empty string
        "tasks": ["Task 1.1 ...", ...]         # non-empty list of strings
    }

Exit codes:
    0 — decomposition succeeded, JSON printed to stdout.
    1 — fatal error: plan not found, malformed JSON from Claude, schema
        violation, or decomposition exceeded ``max_parallel_devs`` cap.
    2 — usage error (bad CLI args).

The cap is the ``MAX_PARALLEL_DEVS`` constant in ``agent/sdlc_router.py``
(default ``3``). Phase 1 fails closed on over-cap decompositions -- multi-wave
queueing is explicitly out of scope.

The PM session pipes the JSON list into a sequential
``valor-session create --role dev --slug {slug}-u{i}`` loop, then calls the
existing ``valor-session wait-for-children`` to dormant the PM until every
sub-session reaches a terminal status.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Reasonable model defaults; mirror tools/classifier.py
_MODEL = "claude-haiku-4-5"

_PROMPT_TEMPLATE = """You are an SDLC planning assistant. Given a plan
document's "Implementation Plan" section, decompose it into independent
parallelizable work units.

Two units are INDEPENDENT when they touch disjoint sets of files AND can be
implemented without one needing the other's output. Tests for a unit go in the
same unit. Documentation is its own unit only when it does not depend on
implementation details.

If the plan has only one cohesive unit, return a single-element array.

Return STRICT JSON: a top-level JSON array of objects, each with:
- "unit_id": snake_case string (e.g. "phase1_decompose_cli")
- "description": one-line summary (string)
- "tasks": non-empty array of task-title strings copied from the plan

Do NOT include explanatory prose, markdown fences, or any keys other than
the three above. Cap output at {max_units} units.

## Implementation Plan section to decompose:

{section}
"""


def _read_plan(plan_path: Path) -> str:
    if not plan_path.exists():
        raise FileNotFoundError(f"plan not found: {plan_path}")
    return plan_path.read_text(encoding="utf-8")


_SECTION_RE = re.compile(
    r"^##\s+Implementation Plan\s*\n(.*?)(?=^##\s|\Z)",
    re.DOTALL | re.MULTILINE,
)


def extract_implementation_plan(plan_text: str) -> str:
    """Return the body of the ``## Implementation Plan`` section, or ``""``."""
    m = _SECTION_RE.search(plan_text)
    if not m:
        return ""
    return m.group(1).strip()


def _fallback_single_unit(section: str) -> list[dict]:
    """Return a single-unit fallback when no section / empty section."""
    return [
        {
            "unit_id": "u1",
            "description": "entire plan (no decomposition possible)",
            "tasks": ([section.strip()] if section.strip() else []),
        }
    ]


def _validate_units(units: object, max_units: int) -> list[dict]:
    """Raise ``ValueError`` if ``units`` does not conform to the schema."""
    if not isinstance(units, list):
        raise ValueError(f"top-level JSON must be a list, got {type(units).__name__}")
    if not units:
        raise ValueError("decomposition produced zero units")

    seen_ids: set[str] = set()
    for idx, unit in enumerate(units):
        if not isinstance(unit, dict):
            raise ValueError(f"unit at index {idx} must be a dict, got {type(unit).__name__}")
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ValueError(f"unit {idx}: 'unit_id' must be a non-empty string")
        if not re.fullmatch(r"[a-z0-9_]+", unit_id):
            raise ValueError(
                f"unit {idx}: 'unit_id' must be snake_case ([a-z0-9_]+), got {unit_id!r}"
            )
        if unit_id in seen_ids:
            raise ValueError(f"unit {idx}: duplicate unit_id {unit_id!r}")
        seen_ids.add(unit_id)

        description = unit.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"unit {unit_id}: 'description' must be a non-empty string")

        tasks = unit.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError(f"unit {unit_id}: 'tasks' must be a non-empty list")
        for t_idx, task in enumerate(tasks):
            if not isinstance(task, str) or not task.strip():
                raise ValueError(f"unit {unit_id}: tasks[{t_idx}] must be a non-empty string")

    if len(units) > max_units:
        raise ValueError(
            f"Decomposition produced {len(units)} units; cap is {max_units}. "
            f"Reduce plan scope or raise cap."
        )

    return units  # type: ignore[return-value]


def _call_claude(prompt: str) -> str:
    """Call Claude and return the raw text response."""
    import anthropic  # imported lazily so unit tests can monkeypatch this fn

    from config.settings import get_anthropic_api_key

    api_key = get_anthropic_api_key()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def _strip_code_fences(content: str) -> str:
    """Strip markdown ``` fences if the model wrapped output in them."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        if lines[-1].strip().startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        content = "\n".join(lines).strip()
        # Strip an optional language tag like "json" on the first line.
        if content.lower().startswith("json\n"):
            content = content.split("\n", 1)[1]
    return content.strip()


def decompose(plan_path: Path, max_units: int) -> list[dict]:
    """Run the full decomposition pipeline. Pure function, no I/O on stdout."""
    plan_text = _read_plan(plan_path)
    section = extract_implementation_plan(plan_text)

    if not section:
        # Empty / missing Implementation Plan section → single-unit fallback.
        return _fallback_single_unit("")

    prompt = _PROMPT_TEMPLATE.format(max_units=max_units, section=section)
    raw = _call_claude(prompt)
    cleaned = _strip_code_fences(raw)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned malformed JSON: {e}") from e

    return _validate_units(parsed, max_units)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Decompose a plan doc into independent dev work units.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "plan_path",
        type=Path,
        help="Path to docs/plans/{slug}.md",
    )
    parser.add_argument(
        "--max-units",
        type=int,
        default=None,
        help=(
            "Cap on the number of units to emit. Defaults to agent.sdlc_router.MAX_PARALLEL_DEVS."
        ),
    )
    args = parser.parse_args(argv)

    if args.max_units is None:
        try:
            from agent.sdlc_router import MAX_PARALLEL_DEVS

            args.max_units = MAX_PARALLEL_DEVS
        except Exception:
            args.max_units = 3

    if args.max_units < 1:
        print(
            json.dumps({"error": "--max-units must be >= 1"}),
            file=sys.stderr,
        )
        return 2

    try:
        units = decompose(args.plan_path, args.max_units)
    except FileNotFoundError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    except ValueError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 1
    except Exception as e:
        logger.debug("decompose failed", exc_info=True)
        print(json.dumps({"error": f"unexpected: {e}"}), file=sys.stderr)
        return 1

    print(json.dumps(units, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
