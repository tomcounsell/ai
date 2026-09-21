"""The routing point (#3410): ``agent/llm/router.py::resolve`` and the site registry.

``resolve(task, project_key)`` is a pure function with six rules, in order
(lane A's Data Flow step 4 plus lane B's local encoder rule, #3420):

1. ``kind == THINKING`` or ``client_only`` -> Anthropic with the call's model.
2. ``backend == ANTHROPIC`` -> Anthropic with the call's model.
3. ``backend == LOCAL_ENCODER`` and ``is_eligible(project_key)`` -> the
   local encoder leg on ``task.site`` with an Anthropic fallback.
4. ``backend == LOCAL_ENCODER`` and not eligible -> Anthropic.
5. ``backend == OLLAMA`` and ``is_eligible(project_key)`` -> Ollama with an
   Anthropic fallback.
6. ``backend == OLLAMA`` and not eligible -> Anthropic (charter §7, fail
   closed: a ``None`` key, a cache miss, and a client key all land here).

The table-driven cases run over every declaration the repo carries
(``agent.llm.tasks.declared_sites``), so a site added later is covered
without touching this file, and ``gh`` is monkeypatched unavailable so the
``valor`` pin is the only thing that can make a key eligible.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent.llm import tasks as tasks_mod
from agent.llm.router import Route, resolve
from agent.llm.tasks import (
    DECLARATION_ROOTS,
    Backend,
    DeclaredSite,
    ErrorCost,
    LLMTask,
    TaskKind,
    declared_sites,
)
from config.models import MODEL_FAST, OLLAMA_CLASSIFIER_MODEL
from tools import improvement_eligibility

THINK = LLMTask(site="t.think", kind=TaskKind.THINKING, backend=Backend.ANTHROPIC)
THINK_ON_OLLAMA = LLMTask(site="t.think_ollama", kind=TaskKind.THINKING, backend=Backend.OLLAMA)
CLIENT_ONLY = LLMTask(
    site="t.client_only", kind=TaskKind.CLASSIFICATION, backend=Backend.OLLAMA, client_only=True
)
CLASSIFY_ANTHROPIC = LLMTask(
    site="t.classify_anthropic", kind=TaskKind.CLASSIFICATION, backend=Backend.ANTHROPIC
)
CLASSIFY_OLLAMA = LLMTask(
    site="t.classify_ollama", kind=TaskKind.CLASSIFICATION, backend=Backend.OLLAMA
)
CLASSIFY_ENCODER = LLMTask(
    site="t.classify_encoder", kind=TaskKind.CLASSIFICATION, backend=Backend.LOCAL_ENCODER
)

#: Captured before the autouse fixture patches ``subprocess.run`` module-wide.
_REAL_RUN = subprocess.run

ANTHROPIC_ROUTE = Route(Backend.ANTHROPIC, MODEL_FAST)
OLLAMA_ROUTE = Route(Backend.OLLAMA, OLLAMA_CLASSIFIER_MODEL, fallback=ANTHROPIC_ROUTE)


def _encoder_route(site: str) -> Route:
    return Route(Backend.LOCAL_ENCODER, site, fallback=ANTHROPIC_ROUTE)


@pytest.fixture(autouse=True)
def _gh_unavailable(monkeypatch):
    """No ``gh`` on this machine and a cold cache: only the ``valor`` pin can pass."""
    improvement_eligibility._clear_cache()

    def _no_gh(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(improvement_eligibility.subprocess, "run", _no_gh)
    yield
    improvement_eligibility._clear_cache()


class TestSixRules:
    @pytest.mark.parametrize("key", ["valor", "acme", None])
    def test_rule_1_thinking_never_leaves_anthropic(self, key):
        assert resolve(THINK, key) == ANTHROPIC_ROUTE
        assert resolve(THINK_ON_OLLAMA, key) == ANTHROPIC_ROUTE

    @pytest.mark.parametrize("key", ["valor", "acme", None])
    def test_rule_1_client_only_never_leaves_anthropic(self, key):
        assert resolve(CLIENT_ONLY, key) == ANTHROPIC_ROUTE

    @pytest.mark.parametrize("key", ["valor", "acme", None])
    def test_rule_2_declared_anthropic(self, key):
        assert resolve(CLASSIFY_ANTHROPIC, key) == ANTHROPIC_ROUTE

    def test_rule_3_eligible_local_encoder_carries_an_anthropic_fallback(self):
        """The route's model is the site id: the head ``heads/<site>.json`` is the model."""
        route = resolve(CLASSIFY_ENCODER, "valor")
        assert route == _encoder_route("t.classify_encoder")
        assert route.model == CLASSIFY_ENCODER.site
        assert route.fallback is not None and route.fallback.fallback is None

    @pytest.mark.parametrize("key", ["acme", None, ""])
    def test_rule_4_ineligible_local_encoder_fails_closed(self, key):
        assert resolve(CLASSIFY_ENCODER, key) == ANTHROPIC_ROUTE

    def test_rule_5_eligible_ollama_carries_an_anthropic_fallback(self):
        route = resolve(CLASSIFY_OLLAMA, "valor")
        assert route == OLLAMA_ROUTE
        assert route.fallback is not None and route.fallback.fallback is None

    @pytest.mark.parametrize("key", ["acme", None, ""])
    def test_rule_6_ineligible_ollama_fails_closed_to_anthropic(self, key):
        assert resolve(CLASSIFY_OLLAMA, key) == ANTHROPIC_ROUTE

    @pytest.mark.parametrize("task", [CLASSIFY_OLLAMA, CLASSIFY_ENCODER], ids=["ollama", "encoder"])
    def test_the_calls_model_rides_the_anthropic_route_and_the_fallback(self, task):
        assert resolve(THINK, "valor", model="claude-x").model == "claude-x"
        assert resolve(task, "valor", model="claude-x").fallback.model == "claude-x"
        assert resolve(task, "acme", model="claude-x").model == "claude-x"

    @pytest.mark.parametrize("task", [CLASSIFY_OLLAMA, CLASSIFY_ENCODER], ids=["ollama", "encoder"])
    def test_resolve_is_pure_for_the_same_inputs(self, task):
        assert resolve(task, "valor") == resolve(task, "valor")

    def test_only_the_local_rules_consult_eligibility(self, monkeypatch):
        calls: list[str | None] = []

        def _spy(key):
            calls.append(key)
            return False

        monkeypatch.setattr(improvement_eligibility, "is_eligible", _spy)
        resolve(THINK, "acme")
        resolve(CLIENT_ONLY, "acme")
        resolve(CLASSIFY_ANTHROPIC, "acme")
        assert calls == []
        resolve(CLASSIFY_OLLAMA, "acme")
        assert calls == ["acme"]
        resolve(CLASSIFY_ENCODER, "acme")
        assert calls == ["acme", "acme"]


def _expected(task: LLMTask, key: str | None) -> Route:
    """The plan's Success Criteria row, as a function of the declaration."""
    if task.kind is TaskKind.THINKING or task.client_only or task.backend is Backend.ANTHROPIC:
        return ANTHROPIC_ROUTE
    if key != "valor":
        return ANTHROPIC_ROUTE
    if task.backend is Backend.LOCAL_ENCODER:
        return _encoder_route(task.site)
    return OLLAMA_ROUTE


SITES = declared_sites()


class TestEveryDeclaration:
    """Table-driven over every ``LLMTask`` declared in the repo."""

    def test_the_repo_declares_sites(self):
        assert SITES, "no module-level LLMTask declarations found under the roots"

    @pytest.mark.parametrize("declared", SITES, ids=[d.task.site for d in SITES])
    @pytest.mark.parametrize("key", ["valor", "acme", None])
    def test_route_matches_the_declaration(self, declared: DeclaredSite, key):
        assert resolve(declared.task, key) == _expected(declared.task, key)


class TestDeclaredSites:
    """``declared_sites`` is the one site list doctor, ``--audit`` and Task 8 read."""

    def test_finds_the_job_router_and_intake_declarations(self):
        by_site = {d.task.site: d for d in SITES}
        assert by_site["job_router.route"].path == "bridge/job_router.py"
        assert by_site["job_router.route"].name == "JOB_ROUTE"
        assert by_site["job_router.route"].task.error_cost is ErrorCost.HIGH
        assert by_site["classifier.intake_intent"].task.backend is Backend.OLLAMA
        assert by_site["classifier.intake_intent"].task.client_only is False

    def test_sorted_by_site_with_positive_line_numbers(self):
        assert [d.task.site for d in SITES] == sorted(d.task.site for d in SITES)
        assert all(d.lineno > 0 for d in SITES)

    def test_never_reads_tests(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "tests").mkdir()
        (tmp_path / "pkg" / "tests" / "conftest.py").write_text(
            "from agent.llm.tasks import *\nX = LLMTask(site='t.x', kind=TaskKind.THINKING, "
            "backend=Backend.ANTHROPIC)\n"
        )
        (tmp_path / "pkg" / "test_mod.py").write_text(
            "from agent.llm.tasks import *\nY = LLMTask(site='t.y', kind=TaskKind.THINKING, "
            "backend=Backend.ANTHROPIC)\n"
        )
        (tmp_path / "pkg" / "real.py").write_text(
            "from agent.llm.tasks import Backend, LLMTask, TaskKind\n"
            "Z = LLMTask('t.z', TaskKind.CLASSIFICATION, Backend.OLLAMA, client_only=True)\n"
        )
        found = declared_sites(("pkg",), repo_root=tmp_path)
        assert [d.task.site for d in found] == ["t.z"]
        assert found[0].task == LLMTask(
            site="t.z", kind=TaskKind.CLASSIFICATION, backend=Backend.OLLAMA, client_only=True
        )

    def test_a_non_literal_declaration_fails_by_name(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "dyn.py").write_text(
            "from agent.llm.tasks import Backend, LLMTask, TaskKind\n"
            "B = Backend.OLLAMA\n"
            "W = LLMTask(site='t.w', kind=TaskKind.CLASSIFICATION, backend=B)\n"
        )
        with pytest.raises(ValueError, match=r"pkg/dyn\.py:3"):
            declared_sites(("pkg",), repo_root=tmp_path)

    def test_roots_are_the_plans_six(self):
        assert DECLARATION_ROOTS == ("agent", "bridge", "worker", "tools", "reflections", "scripts")

    def test_matches_a_text_census_of_the_roots(self):
        """Every module-level ``NAME = LLMTask(`` outside tests is a discovered declaration."""
        grep = _REAL_RUN(
            [
                "/usr/bin/grep",
                "-rlE",
                r"^[A-Za-z_][A-Za-z0-9_]* *(: *[A-Za-z_]+ *)?= *LLMTask\(",
                "--include=*.py",
                *DECLARATION_ROOTS,
            ],
            capture_output=True,
            text=True,
            cwd=Path(tasks_mod.__file__).resolve().parents[2],
        ).stdout.split()
        census = {
            p for p in grep if "/tests/" not in p and not p.rsplit("/", 1)[-1].startswith("test_")
        }
        assert census == {d.path for d in SITES}
