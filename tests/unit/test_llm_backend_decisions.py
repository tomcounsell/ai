"""The decisions (TypeSafe Jev) leg of ``run_typed`` (#3421).

Task 1 slice: the constants, the timer, and the ``LLMStack`` seam the leg
is built on. The leg's own tests follow in Task 2.
"""

from __future__ import annotations

from agent.llm.backends import default_sdk_timeout
from agent.llm.tasks import Backend
from config.models import JEV, JEV_PRICE_USD_PER_MTOKEN, MODEL_INFO, TYPESAFE_DECISIONS_URL
from config.settings import settings


class TestConstants:
    def test_jev_is_a_pinned_version_never_an_alias(self):
        assert JEV == "jev-1.13.0"
        assert "latest" not in JEV and "preview" not in JEV

    def test_endpoint_is_typesafe_native(self):
        assert TYPESAFE_DECISIONS_URL == "https://api.typesafe.ai/v1/systemone"

    def test_price_and_model_info_agree(self):
        """Input tokens cost 0.042 USD per million, output tokens are free
        (https://docs.typesafe.ai/models, retrieved 2026-09-21)."""
        assert JEV_PRICE_USD_PER_MTOKEN == 0.042
        info = MODEL_INFO[JEV]
        assert info["provider"] == "typesafe"
        assert info["endpoint"] == "systemone"
        assert info["context_window"] == 32_000
        assert info["input_cost_per_mtoken"] == JEV_PRICE_USD_PER_MTOKEN
        assert info["output_cost_per_mtoken"] == 0.0
        assert info["price_retrieved_at"] == "2026-09-21"


class TestTimer:
    def test_default_sdk_timeout_reads_the_settings_field(self, monkeypatch):
        assert default_sdk_timeout(Backend.DECISIONS) == settings.timeouts.decisions_sdk_s == 3.0
        monkeypatch.setattr(settings.timeouts, "decisions_sdk_s", 1.25)
        assert default_sdk_timeout(Backend.DECISIONS) == 1.25


class TestStackSeam:
    def test_stack_carries_httpx_async_client(self):
        import httpx

        from agent.anthropic_client import _load_stack

        assert _load_stack().AsyncHTTPClient is httpx.AsyncClient
