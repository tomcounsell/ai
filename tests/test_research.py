"""Ported from yudame/skills tests/check_research.py at c906f00ea (docs/plans/researcher.md).

research/run.py calls Cuttlefish's functions in the order and with the arguments its
episode wrappers use, and renders their output as the report. Every Cuttlefish function
is a stand-in here, so no network, keys or Cuttlefish checkout is needed.
"""

import contextlib
import importlib.util
import inspect
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.settings import VALOR_PLUGIN

ROOT = Path(VALOR_PLUGIN)
RUN = ROOT / "skills/deep-research/run.py"
spec = importlib.util.spec_from_file_location("research_run", RUN)
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

ALL_KEYS = {t[2]: "k" for t in run.TOOLS}
TEXT = "Research text. " * 60


class Model(SimpleNamespace):
    def model_dump(self):
        def plain(v):
            if isinstance(v, Model):
                return v.model_dump()
            if isinstance(v, list):
                return [plain(x) for x in v]
            return v

        return {k: plain(v) for k, v in vars(self).items()}


CV = Model(
    summary="Short answer from the cross-check.",
    verified_claims=[
        Model(claim="A holds", sources=["perplexity", "gemini", "claude"], confidence="high"),
        Model(claim="B holds", sources=["chatgpt", "claude"], confidence="medium"),
    ],
    single_source_claims=[
        Model(claim="C holds", sources=["grok"], confidence="high"),
        Model(claim="D holds", sources=["Together", "caller-context"], confidence="low"),
    ],
    conflicting_claims=[
        Model(
            topic="E or not E",
            positions=[Model(source="gemini", position="E"), Model(source="chatgpt", position="not E")],
            resolution_suggestion="look again",
        ),
    ],
    source_quality=[],
    coverage_map=[],
)
BRIEFING = Model(
    verified_findings=[
        Model(
            topic="Topic one",
            main_finding="Main finding.",
            evidence=[Model(finding="F1", source="Smith 2024", quality="RCT", sample_size="120")],
            contradictions=[],
            source_quality_notes=[],
        )
    ],
    depth_distribution=[Model(topic="Topic two", depth_rating="shallow", recommendation="say it is thin")],
    practical_audit=[],
    story_bank=[],
    counterpoints=[],
    research_gaps=["Nobody measured G"],
    source_inventory=Model(tier1=["Review 2023"], tier2=[], tier3=[]),
    synthesis_notes="",
)


def fake_cuttlefish(calls, tools=None, refute=None):
    def record(name, out):
        def fn(*args, **kwargs):
            calls.setdefault(name, []).append((args, kwargs))
            return out() if callable(out) else out

        return fn

    def tool(name, cost=0.5):
        def fn(prompt, verbose, **kwargs):
            calls.setdefault("tool", []).append((name, prompt, kwargs))
            return TEXT, {"cost": {"total": cost}, "citations": [f"https://example.org/{name}"]}

        return fn

    usage = [{"cost_usd": 0.1}]

    @contextlib.contextmanager
    def track_usage(service):
        buffer = []
        yield buffer
        buffer.extend(usage)

    targeted = Model(
        gpt_prompt="P-gpt",
        gemini_prompt="P-gemini",
        together_prompt="P-together",
        claude_prompt="P-claude",
        grok_prompt="P-grok",
    )
    disps = [
        Model(
            claim="C holds",
            claim_kind="single_source",
            disposition="refuted",
            rationale="R1",
            contradicting_evidence=["x"],
        ),
        Model(
            claim="E or not E",
            claim_kind="conflicting",
            disposition="weakened",
            rationale="R2",
            contradicting_evidence=[],
        ),
        Model(
            claim="A holds",
            claim_kind="single_source",
            disposition="survives_refutation",
            rationale="R3",
            contradicting_evidence=[],
        ),
        Model(
            claim="B holds",
            claim_kind="single_source",
            disposition="bogus",
            rationale="R4",
            contradicting_evidence=[],
        ),
    ]
    return SimpleNamespace(
        tools=tools or {t[0]: tool(t[0]) for t in run.TOOLS},
        craft_research_prompt=record("craft_research_prompt", Model(prompt="P-perplexity")),
        craft_targeted_prompts=record("craft_targeted_prompts", targeted),
        digest_research=record("digest_research", Model(d=1)),
        format_digest=lambda d: "digest",
        discover_questions=record("discover_questions", Model(gaps_in_literature=["q1"])),
        format_question_discovery=lambda q: "QD-TEXT",
        classify_question_resolution=record(
            "classify_question_resolution",
            Model(resolved=[], unresolved=["u1"], partially_addressed=["p1"], summary=""),
        ),
        build_source_tiers=lambda arts, key_fn: {
            key_fn(a): a.metadata["credibility_tier"]
            for a in arts
            if "credibility_tier" in (a.metadata or {})
        },
        cross_validate=record("cross_validate", CV),
        format_cross_validation=lambda cv: "CV-TEXT",
        select_weak_claims=record(
            "select_weak_claims", [{"claim_kind": "single_source", "claim": "C holds"}]
        ),
        refute_claims=refute
        or record("refute_claims", lambda: Model(dispositions=disps, summary="Rebuttal summary.")),
        write_briefing=record("write_briefing", BRIEFING),
        filter_output=lambda text, source: text,
        guard_length=lambda text, source_label: (
            text
            if len(text) >= 500
            else f"[FAILED: {source_label} returned empty or truncated response — {len(text)} chars]"
        ),
        track_usage=track_usage,
    )


@pytest.fixture(autouse=True)
def no_cuttlefish_dir(monkeypatch):
    monkeypatch.delenv("CUTTLEFISH_DIR", raising=False)


def test_glue_matches_cuttlefish_wrappers():
    calls = {}
    result = run.research("Q?", "my notes", fake_cuttlefish(calls), ALL_KEYS)
    cv_kwargs = calls["cross_validate"][0][1]
    wb_kwargs = calls["write_briefing"][0][1]
    gaps = "## Unresolved Questions\n- u1\n\n## Partially Addressed Questions\n- p1"
    assert cv_kwargs["question_gaps"] == gaps and wb_kwargs["question_gaps"] == gaps, (
        "question_gaps reach cross_validate and write_briefing"
    )
    assert cv_kwargs["source_tiers"] == {"caller-context": "ground_truth"}, cv_kwargs["source_tiers"]
    assert calls["refute_claims"][0][1]["source_tiers"] == {"caller-context": "ground_truth"}
    assert cv_kwargs["research_texts"]["caller-context"] == "my notes"
    assert isinstance(calls["select_weak_claims"][0][0][0], dict), "selection gets a dict"
    disp = wb_kwargs["refutation_dispositions"]
    assert [d["disposition"] for d in disp] == ["refuted", "weakened"], disp
    assert set(disp[0]) == {"claim", "disposition", "contradicting_evidence"}
    assert wb_kwargs["cross_validation"] == "CV-TEXT"
    assert calls["classify_question_resolution"][0][1]["question_discovery"] == {"gaps_in_literature": ["q1"]}
    assert result["refutation"]["dispositions"][3]["disposition"] == "survives_refutation", (
        "unknown verdicts coerced"
    )


def test_tiers_without_context_are_empty_but_passed():
    calls = {}
    run.research("Q?", "", fake_cuttlefish(calls), ALL_KEYS)
    assert calls["cross_validate"][0][1]["source_tiers"] == {}


def test_each_tool_gets_its_own_prompt_and_time():
    calls = {}
    run.research("Q?", "", fake_cuttlefish(calls), ALL_KEYS)
    sent = {name: (prompt, kwargs) for name, prompt, kwargs in calls["tool"]}
    for name, field in (
        ("chatgpt", "gpt"),
        ("claude", "claude"),
        ("grok", "grok"),
        ("gemini", "gemini"),
        ("together", "together"),
        ("perplexity", "perplexity"),
    ):
        assert sent[name][0].endswith(f"Research query:\nP-{field}"), (name, sent[name][0][-40:])
    assert "Context:\nQD-TEXT" in sent["claude"][0], "targeted tools get the question discovery as context"
    assert sent["together"][1] == {"model": "meta-llama/llama-4-maverick:online"}, (
        "a model every provider fits"
    )
    assert sent["gemini"][1] == {"poll_interval": 60, "max_attempts": 30}
    assert sent["perplexity"][1] == {"timeout": 600, "max_retries": 2} and sent["grok"][1] == {"timeout": 300}
    assert calls["craft_targeted_prompts"][0][1]["question_discovery"] == "QD-TEXT"


def test_failing_and_silent_tools_are_skipped_with_reason():
    calls = {}
    cf = fake_cuttlefish(calls)

    def boom(**kw):
        raise TimeoutError("read timed out")

    cf.tools["grok"] = boom
    cf.tools["gemini"] = lambda **kw: (None, {})
    cf.tools["together"] = lambda **kw: ("short", {})
    env = dict(ALL_KEYS)
    del env["OPENAI_API_KEY"]
    result = run.research("Q?", "", cf, env)
    t = result["tools"]
    assert t["grok"]["state"] == "skipped" and "read timed out" in t["grok"]["reason"]
    assert t["gemini"]["reason"] == "no result within 30 min (timed out or failed)", t["gemini"]["reason"]
    assert "truncated" in t["together"]["reason"]
    assert t["chatgpt"]["reason"] == "no OPENAI_API_KEY"
    assert set(result["texts"]) == {"perplexity", "claude"}
    report = run.render(result)
    for label in ("Grok", "Gemini", "OpenRouter Llama", "ChatGPT"):
        assert f"| {label} | skipped:" in report, label


def test_no_perplexity_still_crafts_targeted_prompts():
    calls = {}
    env = dict(ALL_KEYS)
    del env["PERPLEXITY_API_KEY"]
    result = run.research("Q?", "", fake_cuttlefish(calls), env)
    assert calls["craft_targeted_prompts"][0][1]["question_discovery"] == ""
    assert "classify_question_resolution" not in calls and result["question_gaps"] is None


def test_failing_rebuttal_does_not_stop_the_report():
    calls = {}

    def refute(**kw):
        raise RuntimeError("quota")

    result = run.research("Q?", "", fake_cuttlefish(calls, refute=refute), ALL_KEYS)
    assert result["refutation"]["state"] == "skipped"
    assert calls["write_briefing"][0][1]["refutation_dispositions"] == []
    assert "Rebuttal pass skipped: RuntimeError: quota" in run.render(result)


def test_report_shape_and_costs():
    calls = {}
    result = run.research("Q?", "", fake_cuttlefish(calls), ALL_KEYS)
    report = run.render(result)
    order = [
        "## Summary",
        "## Findings by topic",
        "## Every claim",
        "## Where the evidence is thin",
        "## Sources",
        "## Method",
    ]
    positions = [report.index(h) for h in order]
    assert positions == sorted(positions), positions
    summary = report[report.index("## Summary") : report.index("## Findings by topic")]
    assert "A holds (confidence: **high**" in summary and "C holds" not in summary
    assert summary.index("A holds") < summary.index("B holds"), "highest confidence first"
    for claim in ("A holds", "B holds", "C holds", "D holds"):
        assert claim in report[report.index("## Every claim") :], claim
    assert "C holds (confidence: **high**; sources: [Grok](#grok)) — rebuttal: **refuted**" in report
    assert "[Gemini](#gemini): E" in report and "Rebuttal: **weakened**" in report
    assert (
        "D holds (confidence: **low**; sources: [OpenRouter Llama](#openrouter-llama), your context)"
        in report
    ), "source names match whatever their case"
    assert "Smith 2024; RCT, n=120" in report
    assert "Unanswered: u1" in report and "**Topic two** (shallow)" in report
    assert "https://example.org/grok" in report
    analysis_calls = sum(
        len(calls[k])
        for k in (
            "craft_research_prompt",
            "craft_targeted_prompts",
            "digest_research",
            "discover_questions",
            "classify_question_resolution",
            "cross_validate",
            "refute_claims",
            "write_briefing",
        )
    )
    assert abs(result["analysis_cost"] - 0.1 * analysis_calls) < 1e-6, (
        result["analysis_cost"],
        analysis_calls,
    )
    assert "- Research tools: $3.00" in report
    assert (
        f"- Analysis steps (prompts, digests, cross-check, rebuttal, briefing): ${0.1 * analysis_calls:,.2f}"
        in report
    )
    assert f"- Total: ${3 + 0.1 * analysis_calls:,.2f}" in report and "uncounted" not in report


def test_tool_cost_comes_from_its_own_metadata_reader():
    calls = {}
    cf = fake_cuttlefish(calls)
    cf.tools["claude"] = lambda **kw: (TEXT, {"usage": {"tokens": 10}})
    cf.tool_meta = {
        "claude": lambda response: {
            "cost": {"total": response["usage"]["tokens"] / 10},
            "citations": [{"url": "https://example.org/c", "title": "C"}],
        }
    }
    result = run.research("Q?", "", cf, ALL_KEYS)
    assert result["tools"]["claude"]["cost"] == 1.0
    assert result["tools"]["claude"]["citations"] == [{"url": "https://example.org/c", "title": "C"}]


def test_long_question_gets_a_short_title():
    calls = {}
    result = run.research(
        "Review the system. Then go deeper on each book.", "", fake_cuttlefish(calls), ALL_KEYS
    )
    lines = run.render(result).splitlines()
    assert (
        lines[0] == "# Review the system"
        and lines[2] == "**Question.** Review the system. Then go deeper on each book."
    )
    assert run.render(run.research("Is X true?", "", fake_cuttlefish({}), ALL_KEYS)).splitlines()[:2] == [
        "# Is X true?",
        "",
    ]
    titled = dict(result, title="A short title")
    assert run.render(titled).splitlines()[0] == "# A short title"
    long = run.render(run.research("word " * 40 + "end", "", fake_cuttlefish({}), ALL_KEYS)).splitlines()[0]
    assert len(long) <= 105 and long.endswith("word..."), long


def test_tool_without_cost_is_named_uncounted():
    calls = {}
    cf = fake_cuttlefish(calls)
    cf.tools["gemini"] = lambda **kw: (TEXT, {})
    cf.tools["perplexity"] = lambda **kw: (TEXT, {"cost": {"total": 0.0}})
    report = run.render(run.research("Q?", "", cf, ALL_KEYS))
    assert "(uncounted: Perplexity, Gemini report no cost)" in report, (
        "a zero total is a missed reading, not free"
    )
    assert "| Gemini | returned | 0m 0s | not reported |" in report


def test_one_tool_means_no_cross_check():
    calls = {}
    env = {"PERPLEXITY_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}
    cf = fake_cuttlefish(calls)
    cf.tools["claude"] = lambda **kw: (None, {})
    report = run.render(run.research("Q?", "", cf, env))
    assert "Only one research tool returned" in report


def test_key_resolution():
    refs = run.key_refs()
    assert set(refs) == {t[2] for t in run.TOOLS}
    assert all(r.startswith("op://") for r in refs.values()), "keys.env holds references only"
    read = {}
    got = run.resolve_keys(
        {"OPENAI_API_KEY": "env"},
        refs,
        read=lambda ref: read.setdefault(ref, "op") if ref != refs["GEMINI_API_KEY"] else None,
    )
    assert got["OPENAI_API_KEY"] == "op" and refs["OPENAI_API_KEY"] in read, "the vault wins"
    assert "GEMINI_API_KEY" not in got and got["PERPLEXITY_API_KEY"] == "op", (
        "the environment's when op fails"
    )


def test_cuttlefish_location():
    ran = []

    def fake(cmd, **kw):
        ran.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    assert (
        run.find_cuttlefish({"CUTTLEFISH_DIR": "/somewhere"}, run=fake) == Path("/somewhere") and not ran
    ), "never pulls a checkout it did not clone"
    with tempfile.TemporaryDirectory() as home:
        run.MANAGED, saved = Path(home) / "cuttlefish", run.MANAGED
        try:
            assert run.find_cuttlefish({}, run=fake) == run.MANAGED and ran[-1][:3] == ["gh", "repo", "clone"]
            (run.MANAGED / ".git").mkdir(parents=True)
            run.find_cuttlefish({}, run=fake)
            assert ran[-1][:4] == ["git", "-C", str(run.MANAGED), "pull"]
            fail = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="no access")
            try:
                run.find_cuttlefish({}, run=fail)
                raise AssertionError("an unreachable Cuttlefish fails")
            except RuntimeError as exc:
                assert "no access" in str(exc)
        finally:
            run.MANAGED = saved


def test_detached_run_returns_at_once_and_reports_status():
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "run.py"
        # The real start() and status code, with a worker that sleeps then writes done or fails.
        fake.write_text(
            RUN.read_text().replace(
                "def worker(run_dir: Path) -> int:\n",
                "def worker(run_dir: Path) -> int:\n"
                "    time.sleep(2)\n"
                "    if 'boom' in (run_dir / 'question.txt').read_text():\n"
                "        write_status(run_dir, state='failed', error='RuntimeError: boom')\n"
                "        return 1\n"
                "    write_status(run_dir, state='done', report=str(run_dir / 'report.md'))\n"
                "    return 0\n",
            )
        )
        for question, final in (("fine?", "done"), ("boom?", "failed")):
            out = Path(tmp) / question[:4]
            began = time.time()
            done = subprocess.run(
                [sys.executable, str(fake), question, "--out", str(out)],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert done.returncode == 0 and time.time() - began < 1.5, (done.stderr, time.time() - began)
            assert json.loads((out / "status.json").read_text())["state"] == "running"
            for _ in range(50):
                status = json.loads((out / "status.json").read_text())
                if status["state"] != "running":
                    break
                time.sleep(0.2)
            assert status["state"] == final, status
            if final == "failed":
                assert status["error"] == "RuntimeError: boom"
            else:
                assert status["report"].endswith("report.md")


class FakeCuttlefish:
    """Production Cuttlefish as run.publish sees it: its `episode` verbs (with their
    arguments), `claude -p /research-workflow`, the Render env group, the
    pending-audio list and the local audio worker. `states` is the workflow's
    (current_step, status, blocked_on) after the research workflow, one per poll."""

    SECRET = "postgres://user:secret-password@db"

    def __init__(self, states, pending=(), workflow_stops_at="Audio Generation", worker_exits=None):
        self.calls, self.attach, self.states = [], None, list(states)
        self.pending, self.worker_exits = list(pending), worker_exits
        self.current, self.published, self.workers, self.gets = ("Setup", "running", ""), None, [], []
        self.workflow_stops_at = workflow_stops_at

    def run(self, cmd, **kw):
        self.calls.append((cmd, kw.get("env") or {}))
        if cmd[0] == "claude":
            self.current = (self.workflow_stops_at, "running", "")
            return SimpleNamespace(returncode=0, stdout="Research workflow complete.", stderr="")
        if cmd[:3] == ["git", "-C", str(run.MANAGED)] or cmd[:2] == ["gh", "repo"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "shell" in cmd:  # queuing Cuttlefish's audio task
            self.queued = cmd[-1]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        verb, args = cmd[cmd.index("episode") + 1], cmd[cmd.index("episode") + 2 :]
        out = {"slug": "ep-1"}
        if verb == "attach":
            self.attach = args
            out = {"slug": "ep-1", "episode_id": 7}
        elif verb == "status":
            if self.states and self._listed:  # the first status after the workflow is its stopping point
                self.current = self.states.pop(0)
            step, status, blocked = self.current
            out = {"current_step": step, "status": status, "blocked_on": blocked}
        elif verb == "list":
            self._listed = True
            out = {"episodes": [{"slug": "other"}, {"slug": "ep-1", "published_at": self.published}]}
            if self.current[0] == "Publish":
                self.published = "2026-11-02T10:00:00Z"
                out["episodes"][1]["published_at"] = self.published
        return SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")

    _listed = False
    queued = None

    def get(self, url, headers):
        self.gets.append((url, headers))
        if url.endswith("/env-groups?limit=100"):
            return [{"envGroup": {"id": "evg-x", "name": "other"}}, {"envGroup": {"id": "evg-cf", "name": "cuttlefish"}}]
        if url.endswith("/env-groups/evg-cf"):
            return {"id": "evg-cf", "name": "cuttlefish", "envVars": [
                {"key": "DATABASE_URL", "value": self.SECRET}, {"key": "LOCAL_WORKER_API_KEY", "value": "group-key"}]}
        if url.endswith("/services?name=cuttlefish&type=web_service"):
            return [{"service": {"id": "srv-cf", "name": "cuttlefish"}}]
        if url.endswith("/services/srv-cf/env-vars?limit=100"):  # the service's own variable wins, as on Render
            return [{"envVar": {"key": "LOCAL_WORKER_API_KEY", "value": "worker-key"}, "cursor": "c"}]
        assert url == "https://yudame.ai/api/podcast/pending-audio/", url
        ids = self.pending.pop(0) if self.pending else [7]
        return {"episodes": [{"id": i} for i in ids]}

    def popen(self, cmd, **kw):
        exits = self.worker_exits
        worker = SimpleNamespace(cmd=cmd, env=kw["env"], terminated=False)
        worker.poll = lambda: exits
        worker.terminate = lambda: setattr(worker, "terminated", True)
        if exits is not None:
            kw["stdout"].write(b"Authentication expired or invalid. Redirected to accounts.google.com")
            kw["stdout"].flush()
        self.workers.append(worker)
        return worker

    def verbs(self):
        return [c[c.index("episode") + 1] for c, _ in self.calls if "episode" in c]


AUDIO_PAUSE = ("Audio Generation", "paused_for_human", "audio_generation")
PUBLISHED_PATH = [("Audio Generation", "running", ""), AUDIO_PAUSE, AUDIO_PAUSE, ("Audio Processing", "running", ""),
                  ("Publish", "running", "")]


def finished_run(tmp, state="done"):
    d = Path(tmp)
    (d / "question.txt").write_text("Is X better than Y? More words")
    (d / "report.md").write_text("# Is X better\n\n## Summary\n\nX is better.\n\n## Findings by topic\n\nlong")
    run.write_status(d, state=state, error="RuntimeError: boom" if state == "failed" else None)
    run.write_status(d, "episode.json", state="running", podcast="yudame-staff")
    return d


def publish(d, cf, podcast="yudame-staff", **kw):
    clock = kw.pop("clock", iter(range(0, 10**6, 60)).__next__)
    return run.publish(d, podcast, None, {"RENDER_API_KEY": "render-key", "PATH": "/bin"}, run=cf.run, get=cf.get,
                       popen=cf.popen, sleep=lambda s: None, clock=clock, read=lambda ref: None, **kw)


def failure(d, cf, **kw):
    with pytest.raises(RuntimeError) as exc:
        publish(d, cf, **kw)
    return str(exc.value)


def test_publish_makes_the_episode_through_cuttlefish_and_ends_published(tmp_path):
    """docs/plans/research-podcast.md: create, attach, setup, Cuttlefish's
    research workflow, its audio worker on the expected audio pause, published_at as done."""
    cf = FakeCuttlefish(PUBLISHED_PATH)
    d = finished_run(tmp_path)
    result = publish(d, cf, podcast="yudame-staff")
    verbs = cf.verbs()
    assert verbs[:3] == ["create", "attach", "setup"], verbs
    assert all(c[c.index("--podcast-slug") + 1] == "yudame-staff" for c, _ in cf.calls if "episode" in c)
    create = next(c for c, _ in cf.calls if "create" in c)
    assert "--defer-setup" in create and create[create.index("--title") + 1] == "Is X better than Y?"
    assert create[create.index("--description") + 1] == "X is better."
    assert cf.attach[cf.attach.index("--file") + 1] == str(d / "report.md") and "--require" not in cf.attach, (
        "the report goes into the research corpus, not as a required input, which sends it to the tables-only analyst")
    claude = next(c for c, _ in cf.calls if c[0] == "claude")
    assert "/research-workflow ep-1 --auto-advance" in claude[-1]
    assert cf.queued.endswith("step_audio_generation.enqueue(episode_id=7)"), "the audio step is queued once research ends"
    assert len(cf.workers) == 1 and "local_audio_worker" in cf.workers[0].cmd and cf.workers[0].terminated
    assert cf.workers[0].cmd[cf.workers[0].cmd.index("--extra") + 1] == "podcast", "the audio worker needs pydub"
    assert cf.workers[0].env["LOCAL_WORKER_API_KEY"] == "worker-key" and cf.workers[0].env["DATABASE_URL"] == cf.SECRET
    assert ("https://yudame.ai/api/podcast/pending-audio/", {"Authorization": "Bearer worker-key"}) in cf.gets
    assert result["state"] == "published" and result["url"] == "https://yudame.ai/podcast/yudame-staff/ep-1/"
    for f in d.iterdir():
        assert cf.SECRET not in f.read_text() and "worker-key" not in f.read_text(), f"production env written to {f.name}"


def test_publish_routes_to_the_podcast_given(tmp_path):
    cf = FakeCuttlefish(PUBLISHED_PATH)
    d = finished_run(tmp_path)
    (d / "title.txt").write_text("X against Y")
    result = publish(d, cf, podcast="yudame-research")
    create = next(c for c, _ in cf.calls if "create" in c)
    assert create[create.index("--title") + 1] == "X against Y", "the report's title names the episode"
    assert all(c[c.index("--podcast-slug") + 1] == "yudame-research" for c, _ in cf.calls if "episode" in c)
    assert result["url"] == "https://yudame.ai/podcast/yudame-research/ep-1/"


def test_publish_waits_for_the_report_and_stops_on_a_failed_one(tmp_path):
    d = finished_run(tmp_path, state="running")
    cf = FakeCuttlefish(PUBLISHED_PATH)
    sleeps = []

    def sleep(s):
        if not sleeps:
            assert not cf.calls, "nothing reaches Cuttlefish before the report is done"
            run.write_status(d, state="done")
        sleeps.append(s)

    run.publish(d, "yudame-staff", None, {"RENDER_API_KEY": "k"}, run=cf.run, get=cf.get, popen=cf.popen,
                sleep=sleep, clock=lambda: 0, read=lambda ref: None)
    assert sleeps and json.loads((d / "episode.json").read_text())["state"] == "published"
    cf = FakeCuttlefish(PUBLISHED_PATH)
    assert "the research failed: RuntimeError: boom" in failure(finished_run(tmp_path, state="failed"), cf)
    assert not cf.calls


def test_publish_stops_with_the_reason(tmp_path):
    d = finished_run(tmp_path)
    cf = FakeCuttlefish([("Audio Generation", "paused_for_human", "anchor_review")])
    assert "Cuttlefish stopped at Audio Generation (paused_for_human), blocked on 'anchor_review'" in failure(d, cf)
    cf = FakeCuttlefish([], workflow_stops_at="Synthesis")
    assert "research workflow stopped at Synthesis" in failure(d, cf)
    cf = FakeCuttlefish([AUDIO_PAUSE] * 3, worker_exits=1)
    assert failure(d, cf).startswith("notebooklm auth expired")
    cf = FakeCuttlefish([("Audio Generation", "running", "")] * 400)
    assert "timeout: not published after 4 hours, at Audio Generation (running)" in failure(d, cf)


def test_audio_worker_is_not_started_while_other_episodes_wait(tmp_path):
    d = finished_run(tmp_path)
    cf = FakeCuttlefish([AUDIO_PAUSE], pending=[[3, 7, 5]])
    assert "other episodes are waiting for audio ([3, 5])" in failure(d, cf) and not cf.workers
    cf = FakeCuttlefish(PUBLISHED_PATH, pending=[[7], [7, 9]])
    publish(d, cf)
    assert json.loads((d / "episode.json").read_text())["others_waiting_for_audio"] == [9], (
        "an episode that joins the queue while the worker runs is named")


def test_research_never_waits_on_publishing(tmp_path, monkeypatch):
    """Tom, 2026-11-02: "full research and report generation shouldn't delay"."""
    monkeypatch.setattr(run, "load_cuttlefish", lambda: None)
    monkeypatch.setattr(run, "research", lambda *a, **kw: {})
    monkeypatch.setattr(run, "render", lambda result: "# report")
    monkeypatch.setattr(run, "publish", lambda *a, **kw: pytest.fail("the research run publishes nothing"))
    for name in ("question.txt", "context.txt"):
        (tmp_path / name).write_text("Q?")
    assert run.inner(tmp_path) == 0
    assert json.loads((tmp_path / "status.json").read_text())["state"] == "done"
    assert not (tmp_path / "episode.json").exists()
    assert "podcast" not in inspect.signature(run.start).parameters


def test_wording_report_first_publish_on_toms_word():
    """The session and the skill say: research starts at once, the PDF goes back,
    an episode is made only when Tom said so with the topic or in his answer,
    Staff for private topics, Research otherwise, and the ask carries a recommendation."""
    converse = (Path(__file__).parent.parent / "skills/sdlc/converse.md").read_text()
    para = converse[converse.index("**Research for Tom.**") :].split("\n\n")[0]
    flat = " ".join(para.split())
    for phrase in ("at once", "never waits", "PDF", "`yudame-staff`", "`yudame-research`", "recommend",
                   "only on his answer", "silence", "with the topic"):
        assert phrase in flat, phrase
    skill = " ".join((ROOT / "skills/deep-research/SKILL.md").read_text().split())
    agent = " ".join((ROOT / "agents/researcher.md").read_text().split())
    for text in (skill, agent):
        assert "waits for a person" not in text and "never publishes" not in text
        assert "only on Tom's word" in text
    assert "`yudame-staff`" in skill and "`yudame-research`" in skill and "never waits" in skill


def test_agent_file_has_frontmatter():
    text = (ROOT / "agents/researcher.md").read_text()
    head = text.split("---")[1]
    for field in ("name: researcher", "description:", "tools:"):
        assert field in head, field
