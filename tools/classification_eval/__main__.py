"""``python -m tools.classification_eval`` (#3410).

Two modes:

* ``--site <id> --candidate <backend>[,<backend>] [--latency-only]`` runs
  the comparison for one site row (``tools/classification_eval/sites.py``),
  prints the report, writes the ``classifier_comparison`` record, and
  records the run's claims on a ``probe`` investigation of case
  ``1ec40086ca1d422e90ef747775ff7f64``. ``--candidate`` repeats or takes a
  comma list. Inputs are the row's fixtures plus real inbound ``valor``
  messages from the memory store (``--real-limit``); under the site minimum
  the run refuses and prints the shortfall, and no reference spend happens.
  Stop the local services first (``./scripts/valor-service.sh stop``): a
  loaded bridge, worker, or reflection-worker stamps ``contended: true``
  and the record cannot clear the bar (Race 3).
* ``--audit`` walks every declared classification site and applies the
  acceptance bar to its latest record; exit 1 on any miss, any site without
  a record, and any ``ANTHROPIC`` landing with no Anthropic arm.

Developer tooling; no ``[project.scripts]`` entry.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from agent.llm.tasks import Backend, LLMTask
from tools.classification_eval import (
    CASE_ID,
    PROJECT_KEY,
    Arm,
    ShortfallError,
    attach_claims,
    audit,
    compare,
    declared_classification_tasks,
    is_contended,
    render_report,
    require_minimum,
    write_record,
)

CANDIDATE_BACKENDS = tuple(b.value for b in Backend)


def parse_candidates(raw: Sequence[str]) -> list[str]:
    """``--candidate`` values, repeats and comma lists, deduped in order."""
    names: list[str] = []
    for chunk in raw:
        for name in chunk.split(","):
            name = name.strip()
            if not name:
                continue
            if name not in CANDIDATE_BACKENDS:
                raise SystemExit(
                    f"unknown candidate backend {name!r}; one of {', '.join(CANDIDATE_BACKENDS)}"
                )
            if name not in names:
                names.append(name)
    return names


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.classification_eval",
        description="Compare a classification site's reference backend against candidate arms.",
    )
    parser.add_argument("--site", help="site id from tools/classification_eval/sites.py")
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        metavar="BACKEND",
        help="candidate arm: ollama or anthropic; repeat or comma-separate",
    )
    parser.add_argument(
        "--latency-only",
        action="store_true",
        help="no reference arm and no agreement (C12, C13, C14 stay on granite)",
    )
    parser.add_argument(
        "--real-limit",
        type=int,
        default=400,
        help="how many real inbound valor messages to draw from the memory store",
    )
    parser.add_argument(
        "--inputs",
        type=Path,
        help="replay a saved JSON-lines input sample instead of drawing one",
    )
    parser.add_argument(
        "--save-inputs", type=Path, help="write the drawn sample as JSON lines for replay"
    )
    parser.add_argument(
        "--no-attach", action="store_true", help="write the record but record no claims"
    )
    parser.add_argument(
        "--reference-model",
        choices=("free", "paid"),
        default="free",
        help="C15 only: the gemma route; 'paid' is the same weights at a metered price,"
        " for a run made while the free route is throttled upstream",
    )
    parser.add_argument("--audit", action="store_true", help="apply the bar to every site")
    parser.add_argument("--list-sites", action="store_true", help="print the site rows")
    parser.add_argument("--project-key", default=PROJECT_KEY, help=argparse.SUPPRESS)
    return parser


def _candidate_arms(site_id: str, names: Sequence[str]) -> list[Arm]:
    from tools.classification_eval.arms import arm_builders

    builders = arm_builders(site_id)
    return [builders[name]() for name in names]


async def _run_site(args: argparse.Namespace, candidates: list[str]) -> int:
    from tools.classification_eval import arms as live
    from tools.classification_eval.sites import site_for

    site = site_for(args.site)
    if args.inputs:
        inputs = live.load_inputs(args.inputs.read_text(encoding="utf-8"))
    else:
        inputs = live.site_inputs(site, args.real_limit, project_key=args.project_key)
    if args.save_inputs:
        args.save_inputs.write_text(live.dump_inputs(inputs), encoding="utf-8")

    try:
        require_minimum(site, inputs)
    except ShortfallError as e:
        print(str(e), file=sys.stderr)
        return 2

    contended = is_contended()
    if contended:
        print(
            "WARNING: com.valor services are loaded; the record will carry contended: true"
            " (stop them with ./scripts/valor-service.sh stop for a latency run)",
            file=sys.stderr,
        )

    transport = None
    reference = None
    if not args.latency_only:
        if site.reference == "openrouter_gemma":
            reference, transport = live.openrouter_gemma_arm(paid=args.reference_model == "paid")
            transport.reserve(len(inputs) * 2, project_key=args.project_key)
        else:
            reference = live.anthropic_arm(site.id, model=site.model, name="anthropic")

    try:
        record = await compare(
            site,
            inputs,
            reference=reference,
            candidates=_candidate_arms(site.id, candidates),
            contended=contended,
        )
    finally:
        if transport is not None:
            transport.settle(project_key=args.project_key)

    payload = record.as_dict()
    print(render_report(payload))
    evidence_id = write_record(record, project_key=args.project_key)
    print(f"record: {evidence_id} (ImprovementEvidence kind=classifier_comparison, case {CASE_ID})")
    if not args.no_attach:
        investigation_id = attach_claims(record, evidence_id, project_key=args.project_key)
        print(f"claims recorded on investigation {investigation_id}")
    return 0


def main(argv: Sequence[str] | None = None, *, tasks: Sequence[LLMTask] | None = None) -> int:
    """Entry point; ``tasks`` overrides site discovery for ``--audit`` (tests)."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_sites:
        from tools.classification_eval.sites import SITES

        for site in sorted(SITES.values(), key=lambda s: s.id):
            print(
                f"{site.id:<36} backend={site.task.backend.value:<10} tier={site.tier:<7}"
                f" reference={site.reference:<16} minimum={site.minimum_n} budget={site.budget_s}"
            )
        return 0

    if args.audit:
        return audit(
            tasks if tasks is not None else declared_classification_tasks(),
            project_key=args.project_key,
        )

    if not args.site:
        parser.error("one of --site, --audit, or --list-sites is required")
    candidates = parse_candidates(args.candidate)
    if not candidates:
        if not args.latency_only:
            parser.error("--site needs at least one --candidate (or --latency-only)")
        candidates = [Backend.OLLAMA.value]
    return asyncio.run(_run_site(args, candidates))


if __name__ == "__main__":
    sys.exit(main())
