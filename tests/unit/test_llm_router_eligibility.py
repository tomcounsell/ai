"""Eligibility drives the leg (#3410): client keys stay on Anthropic, ``valor`` runs local.

Drives ``run_typed`` end to end with both legs faked at the ``_LEGS``
table, over every declared site (``agent.llm.tasks.declared_sites``) so a
site added later is covered without touching this file:

* a message mapped to a client project through every ``OLLAMA``-backed
  classification site reaches the Anthropic leg;
* a ``valor`` message through the same sites, with ``gh`` unavailable and
  the cache cold, reaches the Ollama leg (the code pin, not the cache);
* every ``client_only`` site (the two ``email_cs.*`` declarations) reaches
  the Anthropic leg for every project key, ``valor`` included.

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
OLLAMA_CLASSIFICATION = [
    d.task
    for d in SITES
    if d.task.backend is Backend.OLLAMA
    and d.task.kind is TaskKind.CLASSIFICATION
    and not d.task.client_only
]
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
def legs(monkeypatch):
    improvement_eligibility._clear_cache()

    def _no_gh(*args, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(improvement_eligibility.subprocess, "run", _no_gh)
    ollama, anthropic = _Leg("ollama"), _Leg("anthropic")
    monkeypatch.setitem(wrapper_mod._LEGS, Backend.OLLAMA, ollama)
    monkeypatch.setitem(wrapper_mod._LEGS, Backend.ANTHROPIC, anthropic)
    yield ollama, anthropic
    improvement_eligibility._clear_cache()


def _ids(tasks: list[LLMTask]) -> list[str]:
    return [t.site for t in tasks]


class TestOllamaSitesByKey:
    def test_the_repo_declares_ollama_classification_sites(self):
        assert OLLAMA_CLASSIFICATION, "no OLLAMA classification site declared"

    @pytest.mark.parametrize("task", OLLAMA_CLASSIFICATION, ids=_ids(OLLAMA_CLASSIFICATION))
    async def test_a_client_message_reaches_the_anthropic_leg(self, legs, task):
        ollama, anthropic = legs
        result = await run_typed("fix the login bug", Decision, task=task, project_key="acme")
        assert result.label == "anthropic"
        assert ollama.calls == [] and len(anthropic.calls) == 1
        assert anthropic.calls[0]["route"].fallback is None

    @pytest.mark.parametrize("task", OLLAMA_CLASSIFICATION, ids=_ids(OLLAMA_CLASSIFICATION))
    async def test_a_valor_message_reaches_the_ollama_leg_with_gh_unavailable(self, legs, task):
        ollama, anthropic = legs
        result = await run_typed("fix the login bug", Decision, task=task, project_key="valor")
        assert result.label == "ollama"
        assert anthropic.calls == [] and len(ollama.calls) == 1
        assert ollama.calls[0]["route"].fallback.backend is Backend.ANTHROPIC

    @pytest.mark.parametrize("task", OLLAMA_CLASSIFICATION, ids=_ids(OLLAMA_CLASSIFICATION))
    async def test_a_keyless_message_fails_closed_to_anthropic(self, legs, task):
        ollama, anthropic = legs
        await run_typed("fix the login bug", Decision, task=task, project_key=None)
        assert ollama.calls == [] and len(anthropic.calls) == 1


class TestClientOnlySites:
    @pytest.mark.parametrize("key", ["valor", "acme", None])
    @pytest.mark.parametrize("task", CLIENT_ONLY, ids=_ids(CLIENT_ONLY))
    async def test_client_only_never_leaves_anthropic(self, legs, task, key):
        ollama, anthropic = legs
        await run_typed("triage this email", Decision, task=task, project_key=key)
        assert ollama.calls == [] and len(anthropic.calls) == 1

    @pytest.mark.parametrize("site", EMAIL_CS_SITES)
    def test_email_cs_sites_are_declared_client_only(self, site):
        """Pins the two email_cs declarations (charter §7: client work)."""
        by_site = {d.task.site: d.task for d in SITES}
        task = by_site[site]
        assert task.client_only is True
        assert task.backend is Backend.ANTHROPIC
        assert task in CLIENT_ONLY
