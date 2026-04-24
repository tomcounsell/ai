"""Build-time probe test — Haiku vision via Anthropic's OpenAI-compat endpoint.

Imports ``HAIKU`` from ``config.models`` (per N1) so future Anthropic model
rotations propagate here automatically. Uses a tiny PNG fixture to
confirm that markitdown's Python API path can send an image through the
OpenAI-compat client pointed at ``https://api.anthropic.com/v1/`` and
get back a non-empty description.

This is a HARD GATE at build time — if the vision path is broken, the
plan's ``MARKITDOWN_LLM_MODEL=HAIKU`` contract is broken. The production
converter's ``_llm_path_available`` cache is a runtime safety net (log
once, fall back to subprocess), not an escape hatch for CI: if the probe
fails here, the test FAILS. Per C5, there is no gpt-4o-mini fallback.

Skipped when ``ANTHROPIC_API_KEY`` is not set in the environment so
offline development still works — but the test gate engages on CI where
the key is present.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.models import HAIKU

pytestmark = pytest.mark.integration


FIXTURE_PNG = Path(__file__).resolve().parent.parent / "fixtures" / "sample.png"


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    from tools.knowledge import converter

    converter.reset_llm_probe_cache()
    yield
    converter.reset_llm_probe_cache()


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; Haiku vision probe requires a live key",
)
def test_haiku_vision_probe_succeeds(monkeypatch, tmp_path):
    """Live probe: the OpenAI-compat ping to Anthropic must succeed on HAIKU."""
    from tools.knowledge import converter

    monkeypatch.setenv("MARKITDOWN_LLM_MODEL", HAIKU)

    assert converter._probe_llm_client() is True, (
        "Haiku vision probe failed against Anthropic OpenAI-compat — "
        "MARKITDOWN_LLM_MODEL contract is broken (see plan Risk 3 / C5). "
        "Production falls back to subprocess, but the build gate must not."
    )


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set; Haiku vision probe requires a live key",
)
@pytest.mark.skipif(
    not FIXTURE_PNG.exists(),
    reason="sample.png fixture missing",
)
def test_haiku_vision_describes_image(tmp_path, monkeypatch):
    """End-to-end: convert sample.png via the Python API path and assert the
    sidecar carries an LLM-generated description (not just a filename)."""
    from tools.knowledge.converter import convert_to_sidecar

    monkeypatch.setenv("MARKITDOWN_LLM_MODEL", HAIKU)

    # Copy the fixture into a tmp dir so we don't pollute tests/fixtures/.
    src = tmp_path / "sample.png"
    src.write_bytes(FIXTURE_PNG.read_bytes())

    sidecar = convert_to_sidecar(src)
    assert sidecar is not None
    body = sidecar.read_text(encoding="utf-8")
    # Frontmatter must record the resolved HAIKU (from config.models), not
    # hardcode the version string.
    assert f"llm_model: {HAIKU}" in body, body
    # The body after frontmatter must contain SOME non-filename content —
    # the threshold is loose: anything beyond the filename + whitespace.
    body_after_frontmatter = body.split("---", 2)[-1].strip()
    assert len(body_after_frontmatter) > len(src.name) + 10, (
        "vision path produced filename-only output — Haiku description did "
        f"not land. Body: {body_after_frontmatter!r}"
    )
