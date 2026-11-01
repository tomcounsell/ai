#!/usr/bin/env python3
"""Deep research on one question with Cuttlefish's tools, written up as a long report.

    run.py "<question>" [--context FILE] [--title TEXT] [--out DIR]
    run.py episode RUN_DIR [--podcast SLUG] [--title TEXT]

Returns at once with the run directory; the work goes on in the background and
writes status.json at each step, then report.md and raw.json. Every research step is
a Cuttlefish function called from a Cuttlefish checkout, so the research improves as
Cuttlefish does; the only code here is the order of calls and the report's layout.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
MANAGED = Path.home() / ".cache" / "yudame" / "cuttlefish"
REPO = "yudame/cuttlefish"

# name, label, key, module in apps/podcast/tools, field of TargetedResearchPrompts,
# and the tool's own parameters passed as they are. OpenRouter's Llama is Llama 4
# Maverick, whose every provider takes 128k tokens or more: Cuttlefish's default,
# Llama 3.3 70B, was routed on 2026-11-01 to a 12,288-token provider and refused the prompt.
TOOLS = [
    ("perplexity", "Perplexity", "PERPLEXITY_API_KEY", "perplexity_deep_research", None, {"timeout": 600, "max_retries": 2}),
    ("chatgpt", "ChatGPT", "OPENAI_API_KEY", "chatgpt_deep_research", "gpt_prompt", {}),
    ("gemini", "Gemini", "GEMINI_API_KEY", "gemini_deep_research", "gemini_prompt", {"poll_interval": 60, "max_attempts": 30}),
    ("grok", "Grok", "GROK_API_KEY", "grok_deep_research", "grok_prompt", {"timeout": 300}),
    ("claude", "Claude", "ANTHROPIC_API_KEY", "claude_deep_research", "claude_prompt", {}),
    ("together", "OpenRouter Llama", "OPENROUTER_API_KEY", "together_deep_research", "together_prompt", {"model": "meta-llama/llama-4-maverick:online"}),
]
LABEL = {t[0]: t[1] for t in TOOLS}
CONTEXT_SOURCE = "caller-context"
STYLE_GUIDE = (
    "The reader is a decision-maker who asked this question and will act on the answer, "
    "not a podcast audience. Write plainly and directly. Keep every number with its source, "
    "and say where the evidence is thin rather than smoothing it over."
)


# --- status ---------------------------------------------------------------


def write_status(run_dir: Path, **fields) -> None:
    path = run_dir / "status.json"
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update(fields, updated=now())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2))
    tmp.replace(path)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# --- outer: start the run and return ----------------------------------------


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "research"


def start(question: str, context: str, out: str | None, title: str = "") -> Path:
    run_dir = Path(out or f"research/{datetime.date.today()}-{slug(question)}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "question.txt").write_text(question)
    (run_dir / "context.txt").write_text(context)
    (run_dir / "title.txt").write_text(title)
    write_status(run_dir, state="running", step="starting", started=now(), report=None, error=None)
    with open(run_dir / "run.log", "ab") as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(run_dir)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    return run_dir


# --- worker: Cuttlefish and keys, then the run inside Cuttlefish --------------


def find_cuttlefish(env: dict, run=subprocess.run) -> Path:
    """$CUTTLEFISH_DIR as it is, else the managed clone, cloned or fast-forwarded."""
    if env.get("CUTTLEFISH_DIR"):
        return Path(env["CUTTLEFISH_DIR"]).expanduser()
    if (MANAGED / ".git").exists():
        done = run(["git", "-C", str(MANAGED), "pull", "--ff-only", "-q"], capture_output=True, text=True)
    else:
        MANAGED.parent.mkdir(parents=True, exist_ok=True)
        done = run(["gh", "repo", "clone", REPO, str(MANAGED), "--", "-q"], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError(f"cannot reach Cuttlefish ({REPO}): {(done.stderr or done.stdout).strip()[-300:]}")
    return MANAGED


def key_refs(path: Path = HERE / "keys.env") -> dict[str, str]:
    refs = {}
    for line in path.read_text().splitlines():
        name, sep, ref = line.partition("=")
        if sep and not line.startswith("#"):
            refs[name.strip()] = ref.strip()
    return refs


def op_read(ref: str) -> str | None:
    try:
        done = subprocess.run(["op", "read", ref], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else None


def resolve_keys(env: dict, refs: dict[str, str], read=op_read) -> dict[str, str]:
    """The keys to add to env: each read from Valor's vault, over the environment's.
    The environment keeps only a key the vault cannot give: on 2026-11-01 the shared
    .env's GEMINI_API_KEY was a free-tier key, which Gemini deep research refused,
    while the vault's was accepted."""
    found = {}
    for name, ref in refs.items():
        value = read(ref)
        if value:
            found[name] = value
    return found


def worker(run_dir: Path) -> int:
    try:
        write_status(run_dir, step="preparing Cuttlefish")
        env = dict(os.environ)
        cuttlefish = find_cuttlefish(env)
        env.update(resolve_keys(env, key_refs()))
        if not env.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("no ANTHROPIC_API_KEY: the analysis steps cannot run")
        env["DJANGO_SETTINGS_MODULE"] = "settings"
        # Cuttlefish calls the Anthropic API itself; a base URL set for the calling
        # agent's own session (a local proxy with its own rate limit) is not its.
        env.pop("ANTHROPIC_BASE_URL", None)
        done = subprocess.run(
            ["uv", "run", "--project", str(cuttlefish), "python", str(Path(__file__).resolve()), "--inner", str(run_dir)],
            cwd=cuttlefish,
            env=env,
        )
        status = json.loads((run_dir / "status.json").read_text())
        if done.returncode and status.get("state") != "failed":
            tail = (run_dir / "run.log").read_text().strip().splitlines()[-1:] or ["no output"]
            write_status(run_dir, state="failed", error=f"run exited {done.returncode}: {tail[0]}")
        return done.returncode
    except Exception as exc:  # noqa: BLE001 - the caller reads the reason in status.json
        write_status(run_dir, state="failed", error=f"{type(exc).__name__}: {exc}")
        return 1


# --- inner: the research, inside Cuttlefish's environment ---------------------


def load_cuttlefish() -> SimpleNamespace:
    import importlib

    import django

    sys.path.insert(0, os.getcwd())
    django.setup()
    from apps.podcast.services import analysis, research, safety, source_tier, usage_tracking
    from apps.podcast.services.classify_question_resolution import classify_question_resolution
    from apps.podcast.services.craft_research_prompt import craft_research_prompt, craft_targeted_prompts
    from apps.podcast.services.cross_validate import cross_validate
    from apps.podcast.services.digest_research import digest_research
    from apps.podcast.services.discover_questions import discover_questions
    from apps.podcast.services.refute_claims import _select_load_bearing_weak_claims, refute_claims
    from apps.podcast.services.write_briefing import write_briefing

    tools, tool_meta = {}, {}
    for name, _label, _key, module, _field, _kw in TOOLS:
        mod = importlib.import_module(f"apps.podcast.tools.{module}")
        tools[name] = getattr(mod, f"run_{module.removesuffix('_deep_research')}_research")
        # Each tool's own reading of its response (cost, citations), where it has one.
        tool_meta[name] = getattr(mod, "extract_metadata", None)
    return SimpleNamespace(
        tools=tools,
        tool_meta=tool_meta,
        craft_research_prompt=craft_research_prompt,
        craft_targeted_prompts=craft_targeted_prompts,
        digest_research=digest_research,
        format_digest=analysis._format_research_digest,
        discover_questions=discover_questions,
        format_question_discovery=analysis._format_question_discovery,
        classify_question_resolution=classify_question_resolution,
        build_source_tiers=source_tier.build_source_tiers,
        cross_validate=cross_validate,
        format_cross_validation=analysis._format_cross_validation,
        select_weak_claims=_select_load_bearing_weak_claims,
        refute_claims=refute_claims,
        write_briefing=write_briefing,
        filter_output=safety.filter_research_output,
        guard_length=research._guard_content_length,
        track_usage=usage_tracking.track_usage_to_buffer,
    )


def dump(model) -> dict:
    return model.model_dump() if hasattr(model, "model_dump") else dict(model)


def research(question: str, context: str, cf, env: dict, step=lambda s: None) -> dict:
    """Run Cuttlefish's pipeline on one question, in the order its episode wrappers use."""
    started = time.time()
    usage: list[dict] = []
    tools: dict[str, dict] = {}
    texts: dict[str, str] = {}

    def analyse(service, fn, *args, **kwargs):
        buffer: list[dict] = []
        try:
            with cf.track_usage(service=service) as buffer:
                return fn(*args, **kwargs)
        finally:
            usage.extend(buffer)

    def run_tool(name: str, prompt: str, tool_context: str) -> None:
        label, kwargs = LABEL[name], next(t[5] for t in TOOLS if t[0] == name)
        full = f"Episode: {question}\n\nContext:\n{tool_context}\n\nResearch query:\n{prompt}"
        began = time.time()
        entry = tools[name] = {"label": label, "state": "skipped", "seconds": 0, "cost": None, "citations": []}
        try:
            text, meta = cf.tools[name](prompt=full, verbose=False, **kwargs)
        except Exception as exc:  # noqa: BLE001 - one tool failing leaves the others running
            text, meta, entry["reason"] = None, {}, f"error: {type(exc).__name__}: {exc}"[:300]
        meta = meta or {}
        reader = getattr(cf, "tool_meta", {}).get(name)
        if text and reader:
            meta = {**meta, **reader(meta)}
        entry["seconds"] = round(time.time() - began)
        # A run is never free: a zero total means the tool's own reader missed its usage.
        entry["cost"] = (meta.get("cost") or {}).get("total") or None
        entry["citations"] = citations(meta)
        if text:
            text = cf.guard_length(cf.filter_output(text, source=name), source_label=label)
        if not text or text.startswith("[FAILED:"):
            entry.setdefault("reason", skip_reason(name, text, meta, kwargs))
            return
        entry["state"] = "ran"
        texts[name] = text

    for name, label, key, *_ in TOOLS:
        if not env.get(key):
            tools[name] = {"label": label, "state": "skipped", "reason": f"no {key}", "seconds": 0, "cost": None, "citations": []}
    live = [t[0] for t in TOOLS if t[0] not in tools]
    brief = question + (f"\n\nContext from the caller:\n{context}" if context else "")

    question_discovery, discovery_text = None, ""
    if "perplexity" in live:
        step("Perplexity research")
        prompt = analyse("craft_research_prompt", cf.craft_research_prompt, episode_brief=brief, episode_title=question, research_type="perplexity")
        run_tool("perplexity", prompt.prompt, brief)
        if "perplexity" in texts:
            step("question discovery")
            question_discovery = analyse("discover_questions", cf.discover_questions, research_digest=texts["perplexity"], episode_topic=question)
            discovery_text = cf.format_question_discovery(question_discovery)

    others = [n for n in live if n != "perplexity"]
    if others:
        step("targeted research: " + ", ".join(LABEL[n] for n in others))
        targeted = analyse("craft_targeted_prompts", cf.craft_targeted_prompts, episode_brief=brief, question_discovery=discovery_text, episode_title=question)
        field = {t[0]: t[4] for t in TOOLS}
        with ThreadPoolExecutor(len(others)) as pool:
            for future in [pool.submit(run_tool, n, getattr(targeted, field[n]), discovery_text or brief) for n in others]:
                future.result()

    if not texts:
        raise RuntimeError("no research tool returned anything: " + "; ".join(f"{t['label']}: {t.get('reason')}" for t in tools.values()))

    step("digests")
    digests = {}
    for name, text in texts.items():
        try:
            digests[name] = cf.format_digest(analyse("digest_research", cf.digest_research, text, question))
        except Exception:  # noqa: BLE001 - Cuttlefish falls back to the raw research
            digests[name] = text

    question_gaps, resolution = "", None
    if question_discovery is not None:
        step("open questions")
        resolution = analyse("classify_question_resolution", cf.classify_question_resolution, question_discovery=dump(question_discovery), research_texts=dict(texts), episode_topic=question)
        question_gaps = format_gaps(resolution)

    corpus = dict(texts)
    sources = [SimpleNamespace(title=name, metadata={"raw": True}) for name in texts]
    if context:
        corpus[CONTEXT_SOURCE] = context
        sources.append(SimpleNamespace(title=CONTEXT_SOURCE, metadata={"credibility_tier": "ground_truth"}))
    tiers = cf.build_source_tiers(sources, key_fn=lambda s: s.title)

    step("cross-checking claims")
    cv = analyse("cross_validate", cf.cross_validate, research_texts=corpus, episode_topic=question, question_gaps=question_gaps, source_tiers=tiers)

    refutation = {"state": "none selected", "dispositions": [], "summary": ""}
    selected = cf.select_weak_claims(dump(cv))
    if selected:
        step("rebuttal pass")
        try:
            result = analyse("refute_claims", cf.refute_claims, selected_claims=selected, research_texts=corpus, episode_topic=question, source_tiers=tiers)
            for d in result.dispositions:
                if d.disposition not in ("refuted", "weakened", "survives_refutation"):
                    d.disposition = "survives_refutation"
            refutation = {"state": "ran", **dump(result)}
        except Exception as exc:  # noqa: BLE001 - the rebuttal annotates; it never stops the report
            refutation = {"state": "skipped", "reason": f"{type(exc).__name__}: {exc}"[:300], "dispositions": [], "summary": ""}

    step("writing the briefing")
    briefing = analyse(
        "write_briefing",
        cf.write_briefing,
        cross_validation=cf.format_cross_validation(cv),
        research_digests=digests,
        episode_title=question,
        question_gaps=question_gaps,
        style_guide=STYLE_GUIDE,
        refutation_dispositions=briefing_dispositions(refutation["dispositions"]),
    )
    return {
        "question": question,
        "context_given": bool(context),
        "started": datetime.datetime.fromtimestamp(started, datetime.timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started),
        "tools": tools,
        "texts": texts,
        "question_gaps": dump(resolution) if resolution is not None else None,
        "cross_validation": dump(cv),
        "refutation": refutation,
        "briefing": dump(briefing),
        "analysis_cost": round(sum(float(u.get("cost_usd") or 0) for u in usage), 4),
        "usage": usage,
    }


def citations(meta: dict) -> list[dict]:
    seen, out = set(), []
    for c in (meta.get("citations") or []) + (meta.get("search_results") or []):
        url = c if isinstance(c, str) else (c or {}).get("url")
        if url and url not in seen:
            seen.add(url)
            out.append({"url": url, "title": "" if isinstance(c, str) else (c.get("title") or "")})
    return out


def skip_reason(name: str, text: str | None, meta: dict, kwargs: dict) -> str:
    if text and text.startswith("[FAILED:"):
        return text.strip("[]").removeprefix("FAILED: ")
    if meta.get("_error_message") or meta.get("_error_status"):
        return f"error {meta.get('_error_status', '')}: {meta.get('_error_message', '')}".strip()
    if name == "gemini":
        return f"no result within {kwargs['poll_interval'] * kwargs['max_attempts'] // 60} min (timed out or failed)"
    return "returned nothing"


def format_gaps(resolution) -> str:
    lines = []
    if resolution.unresolved:
        lines += ["## Unresolved Questions", *(f"- {q}" for q in resolution.unresolved)]
    if resolution.partially_addressed:
        lines += ["\n## Partially Addressed Questions", *(f"- {q}" for q in resolution.partially_addressed)]
    return "\n".join(lines)


def briefing_dispositions(dispositions: list[dict]) -> list[dict]:
    """Only refuted and weakened claims change the briefing, as in Cuttlefish."""
    return [
        {"claim": d["claim"], "disposition": d["disposition"], "contradicting_evidence": list(d.get("contradicting_evidence") or [])}
        for d in dispositions
        if d.get("disposition") in ("refuted", "weakened") and d.get("claim")
    ]


# --- the report ----------------------------------------------------------------


def money(x: float | None) -> str:
    return "not reported" if x is None else f"${x:,.2f}"


def source_names(names: list[str], result: dict) -> str:
    out = []
    for n in names:
        key = n.strip().lower()
        label = LABEL.get(key) or ("your context" if key == CONTEXT_SOURCE else n)
        out.append(f"[{label}](#{slug(label)})" if key in result["tools"] else label)
    return ", ".join(out) or "none named"


def render(result: dict) -> str:
    cv, mb, tools = result["cross_validation"], result["briefing"], result["tools"]
    outcome = {d["claim"]: d for d in result["refutation"].get("dispositions", [])}
    ran = [t for t in tools.values() if t["state"] == "ran"]
    question = result["question"].strip()
    title = result.get("title") or question.split(". ")[0].rstrip(".")
    if len(title) > 100:
        title = title[:100].rsplit(" ", 1)[0] + "..."
    L = [f"# {title}", ""]
    if title != question.rstrip("."):
        L += [f"**Question.** {question}", ""]
    L.append(f"Deep research report, {result['started'][:10]}. {len(ran)} of {len(tools)} research tools returned; "
             f"every claim below carries its sources and a confidence (high: 3+ sources agree, medium: 2, or 1 strong study; low: 1).")
    L.append("")
    if len(ran) < 2:
        L += ["> Only one research tool returned, so no claim could be confirmed by a second source: "
              "every claim is single-source.", ""]

    L += ["## Summary", "", cv.get("summary") or "No summary returned.", ""]
    rank = {"high": 0, "medium": 1, "low": 2}
    key = [c for c in cv.get("verified_claims", []) if outcome.get(c["claim"], {}).get("disposition") != "refuted"]
    key = sorted(key, key=lambda c: rank.get(c.get("confidence"), 3))[:7]
    if key:
        L += ["**The claims this rests on:**", ""]
        L += [f"- {c['claim']} (confidence: **{c.get('confidence')}**; sources: {source_names(c.get('sources', []), result)})" for c in key]
        L.append("")

    if mb.get("verified_findings"):
        L += ["## Findings by topic", ""]
        for f in mb["verified_findings"]:
            L += [f"### {f['topic']}", "", f["main_finding"], ""]
            for e in f.get("evidence", []):
                extra = ", ".join(x for x in (e.get("quality"), f"n={e['sample_size']}" if e.get("sample_size") else "") if x)
                L.append(f"- {e['finding']} ({e['source']}{'; ' + extra if extra else ''})")
            for c in f.get("contradictions", []):
                L.append(f"- Contradiction: {c}")
            for q in f.get("source_quality_notes", []):
                L.append(f"- Source note: {q}")
            L.append("")

    L += ["## Every claim", ""]

    def claim_line(c: dict) -> str:
        line = f"- {c['claim']} (confidence: **{c.get('confidence')}**; sources: {source_names(c.get('sources', []), result)})"
        d = outcome.get(c["claim"])
        return line + (f" — rebuttal: **{d['disposition']}**. {d.get('rationale', '')}".rstrip() if d else "")

    if cv.get("verified_claims"):
        L += ["### Confirmed by two or more sources", "", *map(claim_line, cv["verified_claims"]), ""]
    if cv.get("single_source_claims"):
        L += ["### From one source only", "", *map(claim_line, cv["single_source_claims"]), ""]
    if cv.get("conflicting_claims"):
        L += ["### Contested", ""]
        for c in cv["conflicting_claims"]:
            L.append(f"**{c['topic']}**")
            L.append("")
            L += [f"- {source_names([p['source']], result)}: {p['position']}" for p in c.get("positions", [])]
            L.append(f"- Suggested resolution: {c.get('resolution_suggestion', '')}")
            d = outcome.get(c["topic"])
            if d:
                L.append(f"- Rebuttal: **{d['disposition']}**. {d.get('rationale', '')}".rstrip())
            L.append("")
    reb = result["refutation"]
    if reb["state"] == "ran":
        L += [f"Rebuttal pass: {reb.get('summary', '')}", ""]
    elif reb["state"] == "skipped":
        L += [f"Rebuttal pass skipped: {reb.get('reason')}", ""]

    thin = [d for d in mb.get("depth_distribution", [])]
    gaps = result.get("question_gaps") or {}
    if thin or mb.get("research_gaps") or gaps.get("unresolved") or gaps.get("partially_addressed"):
        L += ["## Where the evidence is thin", ""]
        L += [f"- **{d['topic']}** ({d['depth_rating']}): {d['recommendation']}" for d in thin]
        L += [f"- {g}" for g in mb.get("research_gaps", [])]
        L += [f"- Unanswered: {q}" for q in gaps.get("unresolved", [])]
        L += [f"- Partly answered: {q}" for q in gaps.get("partially_addressed", [])]
        L.append("")
    if mb.get("practical_audit"):
        L += ["## Practical steps", ""]
        L += [f"- **{p['finding']}**: {p['implementation']} ({p['parameters']})" for p in mb["practical_audit"]]
        L.append("")
    if mb.get("counterpoints"):
        L += ["## Counterpoints", ""]
        L += [f"- **{c['topic']}**: {c['position_a']} / {c['position_b']}" for c in mb["counterpoints"]]
        L.append("")

    L += ["## Sources", ""]
    inv = mb.get("source_inventory") or {}
    for tier, name in (("tier1", "Systematic reviews and meta-analyses"), ("tier2", "Trials and large studies"), ("tier3", "Case studies and reports")):
        if inv.get(tier):
            L += [f"**{name}**", "", *(f"- {s}" for s in inv[tier]), ""]
    for t in tools.values():
        if t["state"] == "ran":
            L += [f"### {t['label']}", ""]
            L += [f"- [{c['title'] or c['url']}]({c['url']})" for c in t["citations"]] or ["- Sources are cited inline in its text (raw.json)."]
            L.append("")

    L += ["## Method", "", "| Research tool | Result | Time | Cost |", "|---|---|---|---|"]
    for t in tools.values():
        state = "returned" if t["state"] == "ran" else f"skipped: {t.get('reason')}"
        L.append(f"| {t['label']} | {state} | {t['seconds'] // 60}m {t['seconds'] % 60}s | {money(t['cost']) if t['state'] == 'ran' else '-'} |")
    tool_cost = sum(t["cost"] or 0 for t in ran)
    uncounted = [t["label"] for t in ran if t["cost"] is None]
    L += ["", f"- Research tools: {money(tool_cost)}", f"- Analysis steps (prompts, digests, cross-check, rebuttal, briefing): {money(result['analysis_cost'])}",
          f"- Total: {money(tool_cost + result['analysis_cost'])}" + (f" (uncounted: {', '.join(uncounted)} report no cost)" if uncounted else ""),
          f"- Wall time: {result['seconds'] // 60} min", ""]
    if result.get("context_given"):
        L += ["The caller's own context was treated as ground truth: a claim it contradicts resolves toward it.", ""]
    return "\n".join(L)


def inner(run_dir: Path) -> int:
    question = (run_dir / "question.txt").read_text()
    context = (run_dir / "context.txt").read_text()
    try:
        cf = load_cuttlefish()
        result = research(question, context, cf, dict(os.environ), step=lambda s: write_status(run_dir, step=s))
        title = run_dir / "title.txt"
        result["title"] = title.read_text().strip() if title.exists() else ""
        (run_dir / "raw.json").write_text(json.dumps(result, indent=2, default=str))
        (run_dir / "report.md").write_text(render(result))
        write_status(run_dir, state="done", step="done", report=str(run_dir / "report.md"), finished=now())
        return 0
    except Exception as exc:  # noqa: BLE001 - the caller reads the reason in status.json
        write_status(run_dir, state="failed", error=f"{type(exc).__name__}: {exc}"[:500])
        raise


# --- a podcast episode from a finished report ------------------------------------


def episode(run_dir: Path, podcast: str, title: str | None, env: dict, run=subprocess.run) -> dict:
    """Draft a Cuttlefish episode on the podcast with the report attached; never publish.

    Uses Cuttlefish's own operator commands (`episode create --defer-setup`, then
    `episode attach --require`), so it needs a checkout configured for production.
    Setting the episode up, producing its audio and publishing are left to a person.
    """
    cuttlefish = env.get("CUTTLEFISH_DIR")
    if not cuttlefish:
        raise RuntimeError("drafting an episode needs CUTTLEFISH_DIR: a Cuttlefish checkout configured for production")
    report = run_dir / "report.md"
    if not report.exists():
        raise RuntimeError(f"no report yet in {run_dir}")

    def manage(*args: str) -> dict:
        done = run(
            ["uv", "run", "--project", cuttlefish, "python", "manage.py", "episode", *args, "--podcast-slug", podcast, "--json"],
            cwd=cuttlefish, capture_output=True, text=True,
        )
        if done.returncode:
            raise RuntimeError(f"episode {args[0]} failed: {(done.stderr or done.stdout).strip()[-300:]}")
        return json.loads(done.stdout)

    title = title or (run_dir / "question.txt").read_text().strip().split(". ")[0][:120]
    created = manage("create", "--title", title, "--defer-setup")
    attached = manage("attach", created["slug"], "--file", str(report), "--title", "Deep research report", "--require")
    result = {"podcast": podcast, "slug": created["slug"], "title": title, "attached": attached.get("artifact_title"), "published": False}
    (run_dir / "episode.json").write_text(json.dumps(result, indent=2))
    return result


def main(argv: list[str]) -> int:
    if argv[:1] in (["--worker"], ["--inner"]):
        return (worker if argv[0] == "--worker" else inner)(Path(argv[1]))
    if argv[:1] == ["episode"]:
        p = argparse.ArgumentParser(prog="run.py episode", description="Draft a podcast episode from a finished report; never publishes.")
        p.add_argument("run_dir", type=Path)
        p.add_argument("--podcast", default="yudame-research", help="the podcast's slug in Cuttlefish (default yudame-research)")
        p.add_argument("--title")
        args = p.parse_args(argv[1:])
        print(json.dumps(episode(args.run_dir.resolve(), args.podcast, args.title, dict(os.environ)), indent=2))
        return 0
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("question")
    p.add_argument("--context", type=Path, help="a file of the caller's own material, treated as ground truth")
    p.add_argument("--out", help="run directory (default research/<date>-<question>)")
    p.add_argument("--title", default="", help="the report's title (default the question's first sentence)")
    args = p.parse_args(argv)
    run_dir = start(args.question, args.context.read_text() if args.context else "", args.out, args.title)
    print(run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
