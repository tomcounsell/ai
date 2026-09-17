"""Live-listing probe for configured OpenRouter model ids.

Regression guard for issue #3338: `OPENROUTER_GEMMA4_FREE` named a free-tier
id after OpenRouter delisted it, so every default cheap-inference call 400d.
This probe checks the configured ids against the live public model listing
so the next delisting fails loudly by name at test time instead of 400ing
mid-cycle.

Fail-closed on network error: a silent skip would re-create exactly the
silent-drift class this guard removes. The failure message distinguishes
"endpoint unreachable" from "id not listed" so CI flakes are diagnosable.

Only the cheap-inference default (`OPENROUTER_GEMMA4_FREE`) hard-fails when
unlisted. Other configured ids that are absent from the listing surface as
warnings, never red builds: they are unrelated drift outside this fix's scope
(flagged in review when observed).
"""

import urllib.request
import warnings

import pytest

import config.models as models

MODELS_LISTING_URL = "https://openrouter.ai/api/v1/models"


def _configured_openrouter_ids() -> dict:
    """Every configured OpenRouter model id, excluding the endpoint URL itself."""
    ids = {}
    for name in dir(models):
        if not name.startswith("OPENROUTER_") or name == "OPENROUTER_URL":
            continue
        value = getattr(models, name)
        if isinstance(value, str) and value and not value.startswith("http"):
            ids[name] = value
    return ids


def _fetch_listing_ids() -> set:
    import json

    request = urllib.request.Request(MODELS_LISTING_URL, headers={"User-Agent": "valor-test-probe"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        pytest.fail(
            f"OpenRouter model listing endpoint unreachable ({MODELS_LISTING_URL}): "
            f"{exc}. This is an endpoint/network failure, not a delisting: "
            "retry before treating it as a model problem."
        )
    try:
        return {entry["id"] for entry in payload["data"]}
    except (KeyError, TypeError) as exc:
        pytest.fail(
            f"OpenRouter model listing at {MODELS_LISTING_URL} returned an unexpected shape: {exc}."
        )


@pytest.mark.integration
def test_openrouter_gemma4_free_is_listed():
    """The cheap-inference default must name a listed model id (issue #3338)."""
    listed = _fetch_listing_ids()
    assert models.OPENROUTER_GEMMA4_FREE in listed, (
        f"OPENROUTER_GEMMA4_FREE ({models.OPENROUTER_GEMMA4_FREE}) is not in the "
        f"live OpenRouter listing ({MODELS_LISTING_URL}): delisted or renamed. "
        "Repoint the constant in config/models.py at a listed free id."
    )


@pytest.mark.integration
def test_other_openrouter_ids_warn_when_unlisted():
    """Unrelated unlisted ids warn by name instead of failing the build."""
    listed = _fetch_listing_ids()
    for name, model_id in sorted(_configured_openrouter_ids().items()):
        if name == "OPENROUTER_GEMMA4_FREE":
            continue
        if model_id not in listed:
            warnings.warn(
                f"{name} ({model_id}) is not in the live OpenRouter listing "
                f"({MODELS_LISTING_URL}): possibly delisted or renamed. "
                "Flagged for review; not a build failure.",
                stacklevel=2,
            )
