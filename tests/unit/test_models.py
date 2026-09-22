"""Live probes for pinned external model ids.

OpenRouter (issue #3338): `OPENROUTER_GEMMA4_FREE` named a free-tier id after
OpenRouter delisted it, so every default cheap-inference call 400d. The
listing probe checks the configured ids against the live public model listing
so the next delisting fails loudly by name at test time instead of 400ing
mid-cycle.

TypeSafe (issue #3421): `JEV` is the pinned decisions model
(`jev-1.13.0`, never a moving alias). There is no listing endpoint on the
native API, so the probe is one minimal authenticated POST asserting the
response `model` is the pin, the `noul` answer shape, and the `usage` shape
the leg meters from. A silent alias re-point, a withdrawn pin, or a usage
drift fails by name. The key is read through `settings.api.typesafe_api_key`
and never appears in any message.

Fail-closed on network error: a silent skip would re-create exactly the
silent-drift class these guards remove. The failure messages distinguish
"endpoint unreachable" from "id not listed" / the pin, the answer shape, and
the usage shape, so CI flakes are diagnosable. The one skip is the TypeSafe
probe on a machine with no `TYPESAFE_API_KEY`, and it names the credential.

Only the cheap-inference default (`OPENROUTER_GEMMA4_FREE`) hard-fails when
unlisted. Other configured ids that are absent from the listing surface as
warnings, never red builds: they are unrelated drift outside this fix's scope
(flagged in review when observed). `_configured_openrouter_ids()` reads only
`OPENROUTER_*` names, so `JEV` and `TYPESAFE_DECISIONS_URL` never reach the
listing check.
"""

import json
import urllib.error
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


def test_configured_openrouter_ids_exclude_the_typesafe_pin():
    """`JEV` is not an OpenRouter id; the listing warning stays quiet about it."""
    ids = _configured_openrouter_ids()
    assert models.JEV not in ids.values()
    assert models.TYPESAFE_DECISIONS_URL not in ids.values()
    assert "JEV" not in ids and "TYPESAFE_DECISIONS_URL" not in ids


@pytest.mark.integration
def test_typesafe_jev_pinned_model_answers():
    """The pinned decisions model answers one `noul` question by its own name (#3421).

    About 300 input tokens, 0.00001 USD. Skips only when the credential is
    absent, naming it; every other outcome is a named failure.
    """
    from config.settings import settings

    key = settings.api.typesafe_api_key
    if key is None:
        pytest.skip("TYPESAFE_API_KEY is not set on this machine (settings.api.typesafe_api_key)")

    body = {
        "model": models.JEV,
        "state": "hello there",
        "questions": {
            "q": {
                "type": "noul",
                "instructions": "Is this state a greeting?",
                "criteria": {"true": "the text greets someone", "false": "it does not"},
            }
        },
    }
    request = urllib.request.Request(
        models.TYPESAFE_DECISIONS_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "valor-test-probe",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        # The exception class and HTTP status only: never the body or the request.
        status_note = f" HTTP {exc.code}" if isinstance(exc, urllib.error.HTTPError) else ""
        pytest.fail(
            f"TypeSafe decisions endpoint unreachable ({models.TYPESAFE_DECISIONS_URL}): "
            f"{type(exc).__name__}{status_note}. This is an endpoint/network/auth failure "
            "(a 529 is upstream overload), not a model problem: retry before treating it "
            "as a withdrawn pin."
        )

    assert status == 200, f"TypeSafe decisions endpoint answered HTTP {status}, not 200"
    assert payload.get("model") == models.JEV, (
        f"the pinned id drifted: asked for JEV={models.JEV!r}, the response names "
        f"{payload.get('model')!r}. Repin config.models.JEV at a served version."
    )
    answer = (payload.get("answers") or {}).get("q")
    assert isinstance(answer, dict) and answer.get("type") == "noul", (
        f"the answer shape drifted: expected answers.q of type noul, got {answer!r}"
    )
    noul = answer.get("noul")
    assert isinstance(noul, float) and 0.0 <= noul <= 1.0, (
        f"the answer shape drifted: expected answers.q.noul as a float in [0, 1], got {noul!r}"
    )
    input_tokens = (payload.get("usage") or {}).get("input_tokens")
    assert isinstance(input_tokens, int) and input_tokens > 0, (
        f"the usage shape drifted: expected usage.input_tokens as a positive int, got "
        f"{input_tokens!r}. The decisions leg meters spend from this field."
    )
