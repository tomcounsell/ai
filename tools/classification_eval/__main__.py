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
  acceptance bar to its latest record (the latest *landed* record for a
  ``LOCAL_ENCODER`` site, whose committed head must match it); exit 1 on
  any miss, any site without a record, and any ``ANTHROPIC`` landing with
  no Anthropic arm.

The fit path (#3420, ``tools/classification_eval/fit.py``):

* ``--site <id> --fit --candidate local_encoder,anthropic`` splits the draw
  by digest, labels the training split with the reference arm, fits the
  per-site head, writes it to the staging path, and measures it on the
  held-out split; the record carries a ``fit`` block. ``--land`` is the only
  flag under which the served head ``agent/llm/backends/heads/<site>.json``
  is written (both arms clear the bar) or deleted (either misses), and it
  needs the ``anthropic`` candidate. ``--precheck-agreement`` applies the
  precheck gate before any spend.
* ``--preflight`` says whether this machine's memory store can meet each
  site's held-out real need (exit 1 under the routing sites' 100).
* ``--precheck`` scores every site with a lane A record five-fold on its
  fixtures through the same ``fit_head`` the fit uses; zero spend.

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
        help=f"candidate arm: one of {', '.join(CANDIDATE_BACKENDS)}; repeat or comma-separate",
    )
    parser.add_argument(
        "--fit",
        action="store_true",
        help="fit the site's local_encoder head on the training split and measure it on"
        " the held-out split (needs --candidate local_encoder)",
    )
    parser.add_argument(
        "--land",
        action="store_true",
        help="with --fit: install the staged head as the served head when both the"
        " local_encoder and the anthropic arm clear the bar; delete it when either misses",
    )
    parser.add_argument(
        "--precheck-agreement",
        type=float,
        default=None,
        metavar="AGREEMENT",
        help="with --fit: the site's --precheck number; under bar - 0.10 the fit is skipped"
        " before any reference call and one precheck_below_bar claim is recorded",
    )
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="report the real messages this machine's memory store holds against each"
        " site's held-out need; exit 1 under the routing sites' need",
    )
    parser.add_argument(
        "--precheck",
        action="store_true",
        help="five-fold agreement of the local_encoder head on every site with a lane A"
        " record, beside the majority baseline and the bar; zero spend",
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


def _draw_inputs(args: argparse.Namespace, site):
    from tools.classification_eval import arms as live

    if args.inputs:
        inputs = live.load_inputs(args.inputs.read_text(encoding="utf-8"))
    else:
        inputs = live.site_inputs(site, args.real_limit, project_key=args.project_key)
    if args.save_inputs:
        args.save_inputs.write_text(live.dump_inputs(inputs), encoding="utf-8")
    return inputs


def _reference_arm(args: argparse.Namespace, site, reserve_calls: int):
    """``(reference arm, gemma transport or None)``; the caller settles the transport."""
    from tools.classification_eval import arms as live

    if site.reference == "openrouter_gemma":
        reference, transport = live.openrouter_gemma_arm(paid=args.reference_model == "paid")
        transport.reserve(reserve_calls, project_key=args.project_key)
        return reference, transport
    return live.anthropic_arm(site.id, model=site.model, name="anthropic"), None


async def _run_fit(args: argparse.Namespace, candidates: list[str]) -> int:
    from agent.llm.errors import LLMCallError
    from tools.classification_eval.arms import arm_builders
    from tools.classification_eval.fit import FitError, FitRefusalError, run_fit
    from tools.classification_eval.sites import site_for

    site = site_for(args.site)
    inputs = _draw_inputs(args, site)
    contended = is_contended()
    transport = None
    try:
        # Training labels once, plus the two passes over the held-out split.
        reference, transport = _reference_arm(args, site, len(inputs) * 3)
        outcome = await run_fit(
            site,
            inputs,
            reference=reference,
            candidates=candidates,
            build_arm=lambda name, staged: arm_builders(site.id, head_path=staged)[name](),
            contended=contended,
            land=args.land,
            precheck_agreement=args.precheck_agreement,
            project_key=args.project_key,
            attach=not args.no_attach,
        )
    except (ShortfallError, FitRefusalError) as e:
        print(str(e), file=sys.stderr)
        return 2
    except (FitError, LLMCallError) as e:
        # A FitError is the post-labeling abort; an LLMCallError here is the
        # encoder leg refusing to embed (extra, weights), named by the leg.
        print(str(e), file=sys.stderr)
        return 1
    finally:
        if transport is not None:
            transport.settle(project_key=args.project_key)
    if outcome.skipped:
        return 0
    assert outcome.record is not None
    print(render_report(outcome.record.as_dict()))
    print(
        f"record: {outcome.evidence_id} (ImprovementEvidence kind=classifier_comparison,"
        f" case {CASE_ID})"
    )
    return 0


async def _run_site(args: argparse.Namespace, candidates: list[str]) -> int:
    from tools.classification_eval.sites import site_for

    site = site_for(args.site)
    inputs = _draw_inputs(args, site)

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
        reference, transport = _reference_arm(args, site, len(inputs) * 2)

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

    if args.preflight:
        from tools.classification_eval.fit import preflight

        return preflight(args.real_limit, project_key=args.project_key)

    if args.precheck:
        from agent.llm.errors import LLMCallError
        from tools.classification_eval.fit import precheck

        try:
            return precheck(project_key=args.project_key)
        except LLMCallError as e:
            print(str(e), file=sys.stderr)
            return 1

    if not args.site:
        parser.error("one of --site, --audit, --preflight, --precheck, or --list-sites is required")
    candidates = parse_candidates(args.candidate)
    if args.fit:
        if args.latency_only:
            parser.error("--fit measures agreement; drop --latency-only")
        if not candidates:
            parser.error("--fit needs --candidate local_encoder,anthropic")
        return asyncio.run(_run_fit(args, candidates))
    if args.land:
        parser.error("--land needs --fit")
    if not candidates:
        if not args.latency_only:
            parser.error("--site needs at least one --candidate (or --latency-only)")
        candidates = [Backend.OLLAMA.value]
    return asyncio.run(_run_site(args, candidates))


if __name__ == "__main__":
    sys.exit(main())
