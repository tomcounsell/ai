"""Eligibility drives the leg (#3410): client keys stay on Anthropic, ``valor`` runs local.

Drives ``run_typed`` end to end with every leg faked at the ``_LEGS``
table, over every declared site (``agent.llm.tasks.declared_sites``) whose
backend is not ``ANTHROPIC``, so a site added later is covered without
touching this file, plus one synthetic ``LOCAL_ENCODER`` task (#3420) and
one synthetic ``DECISIONS`` task (#3421) so each leg path is exercised
before any site lands on it:

* a message mapped to a client project through every local-backed
  classification site reaches the Anthropic leg with no fallback;
* a ``valor`` message through the same sites, with ``gh`` unavailable and
  the cache cold, reaches the leg the declaration names
  (``legs[task.backend]``: the code pin, not the cache) with the fallback
  the plan gives that backend: Anthropic behind Ollama and the local
  encoder, Ollama behind the decisions leg;
* every ``client_only`` site (the two ``email_cs.*`` declarations) reaches
  the Anthropic leg for every project key, ``valor`` included.

No site on ``main`` declares ``DECISIONS`` yet (landings happen on the
Valor host, plan Step 6), so ``LOCAL_CLASSIFICATION`` carries a synthetic
``DECISIONS`` task beside the declared sites; a future declaration joins the
sweep on its own.

The ``email_cs`` assertion is written against ``client_only`` semantics: it
parametrizes over whatever the registry carries, and a dedicated case pins
the two email_cs site ids so a declaration that drops ``client_only`` fails
by name.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from agent.llm import run_typed
from agent.llm import wrapper as wrapper_mod
from agent.llm.tasks import Backend, LLMTask, TaskKind, declared_sites
from tools import improvement_eligibility

SITES = declared_sites()
SYNTHETIC_ENCODER = LLMTask(
    site="test.synthetic_encoder", kind=TaskKind.CLASSIFICATION, backend=Backend.LOCAL_ENCODER
)
SYNTHETIC_DECISIONS = LLMTask(
    site="t.synthetic_decisions", kind=TaskKind.CLASSIFICATION, backend=Backend.DECISIONS
)
LOCAL_CLASSIFICATION = [
    d.task
    for d in SITES
    if d.task.backend is not Backend.ANTHROPIC
    and d.task.kind is TaskKind.CLASSIFICATION
    and not d.task.client_only
] + [SYNTHETIC_ENCODER, SYNTHETIC_DECISIONS]
SYNTHETIC = (SYNTHETIC_ENCODER, SYNTHETIC_DECISIONS)
#: The leg each local backend falls back to (plan, Data Flow step 5).
FALLBACK_OF = {
    Backend.OLLAMA: Backend.ANTHROPIC,
    Backend.LOCAL_ENCODER: Backend.ANTHROPIC,
    Backend.DECISIONS: Backend.OLLAMA,
}
CLIENT_ONLY = [d.task for d in SITES if d.task.client_only]
EMAIL_CS_SITES = ("email_cs.triage", "email_cs.action")


class Decision(BaseModel):
    label: str


class _Leg:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict] = []

    async def __call__(self, prompt, output_type, route, **kwargs):
        self.calls.append({"route": route, **kwargs})
        return output_type(label=self.name)


@pytest.fixture
def legs(monkeypatch) -> dict[Backend, _Leg]:
    """Every backend's leg faked; keyed by ``Backend`` so a test asserts ``legs[task.backend]``."""
    improvement_eligibility._clear_cache()

    def _no_gh(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(improvement_eligibility.subprocess, "run", _no_gh)
    fakes = {backend: _Leg(backend.value) for backend in Backend}
    for backend, fake in fakes.items():
        monkeypatch.setitem(wrapper_mod._LEGS, backend, fake)
    yield fakes
    improvement_eligibility._clear_cache()


def _ids(tasks: list[LLMTask]) -> list[str]:
    return [t.site for t in tasks]


def _only(fakes: dict[Backend, _Leg], backend: Backend) -> None:
    """Exactly one call, on ``backend``; every other leg idle."""
    for other, fake in fakes.items():
        assert len(fake.calls) == (1 if other is backend else 0), (
            f"{other.value} leg saw {len(fake.calls)} call(s)"
        )


class TestLocalSitesByKey:
    def test_the_repo_declares_local_classification_sites(self):
        assert [t for t in LOCAL_CLASSIFICATION if t not in SYNTHETIC], (
            "no local-backed classification site declared"
        )

    def test_the_fake_table_covers_every_backend(self, legs):
        assert set(legs) == set(Backend)
        assert set(FALLBACK_OF) == set(Backend) - {Backend.ANTHROPIC}

    @pytest.mark.parametrize("task", LOCAL_CLASSIFICATION, ids=_ids(LOCAL_CLASSIFICATION))
    async def test_a_client_message_reaches_the_anthropic_leg(self, legs, task):
        result = await run_typed("fix the login bug", Decision, task=task, project_key="acme")
        assert result.label == "anthropic"
        _only(legs, Backend.ANTHROPIC)
        assert legs[Backend.ANTHROPIC].calls[0]["route"].fallback is None

    @pytest.mark.parametrize("task", LOCAL_CLASSIFICATION, ids=_ids(LOCAL_CLASSIFICATION))
    async def test_a_valor_message_reaches_the_declared_leg_with_gh_unavailable(self, legs, task):
        result = await run_typed("fix the login bug", Decision, task=task, project_key="valor")
        assert result.label == task.backend.value
        _only(legs, task.backend)
        route = legs[task.backend].calls[0]["route"]
        assert route.fallback.backend is FALLBACK_OF[task.backend]
        assert route.fallback.fallback is None
        if task.backend is Backend.LOCAL_ENCODER:
            assert route.model == task.site

    @pytest.mark.parametrize("task", LOCAL_CLASSIFICATION, ids=_ids(LOCAL_CLASSIFICATION))
    async def test_a_keyless_message_fails_closed_to_anthropic(self, legs, task):
        await run_typed("fix the login bug", Decision, task=task, project_key=None)
        _only(legs, Backend.ANTHROPIC)


class TestClientOnlySites:
    @pytest.mark.parametrize("key", ["valor", "acme", None])
    @pytest.mark.parametrize("task", CLIENT_ONLY, ids=_ids(CLIENT_ONLY))
    async def test_client_only_never_leaves_anthropic(self, legs, task, key):
        await run_typed("triage this email", Decision, task=task, project_key=key)
        _only(legs, Backend.ANTHROPIC)

    @pytest.mark.parametrize("site", EMAIL_CS_SITES)
    def test_email_cs_sites_are_declared_client_only(self, site):
        """Pins the two email_cs declarations (charter §7: client work)."""
        by_site = {d.task.site: d.task for d in SITES}
        task = by_site[site]
        assert task.client_only is True
        assert task.backend is Backend.ANTHROPIC
        assert task in CLIENT_ONLY
