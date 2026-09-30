"""The seat file parses, its rules hold, and every seated model answers.
The live check needs the Anthropic key in the Keychain and skips without it."""

import pytest

from infra.models import SeatFile, load
from infra.secrets import MissingSecret, read_secret


def test_seat_file_loads_and_seats_are_pinned():
    seats = load()
    assert set(seats.seat_ids()) == {"frontier", "verifier", "summarizer"}
    assert seats.model("frontier").min_cache_prefix == 512
    assert seats.model("verifier").min_cache_prefix == 1024
    assert seats.model("summarizer").min_cache_prefix == 4096
    assert seats.seats.verifier.screened is False


def test_verifier_must_not_be_the_frontier_snapshot_even_if_screened():
    data = load().model_dump()
    data["seats"]["verifier"] = {"model": data["seats"]["frontier"], "screened": True}
    with pytest.raises(ValueError, match="frontier snapshot"):
        SeatFile.model_validate(data)


def test_unscreened_verifier_must_be_older_than_frontier():
    data = load().model_dump()
    older, newer = data["seats"]["verifier"]["model"], data["seats"]["frontier"]
    data["seats"]["frontier"] = older
    data["seats"]["verifier"] = {"model": newer, "screened": False}
    with pytest.raises(ValueError, match="unscreened"):
        SeatFile.model_validate(data)


def test_screened_verifier_may_be_newer_than_frontier():
    data = load().model_dump()
    older, newer = data["seats"]["verifier"]["model"], data["seats"]["frontier"]
    data["seats"]["frontier"] = older
    data["seats"]["verifier"] = {"model": newer, "screened": True}
    assert SeatFile.model_validate(data).model("verifier").id == newer


def test_verifier_seat_must_say_whether_it_was_screened():
    data = load().model_dump()
    data["seats"]["verifier"] = {"model": data["seats"]["verifier"]["model"]}
    with pytest.raises(ValueError):
        SeatFile.model_validate(data)


def test_every_seated_model_answers():
    try:
        key = read_secret("anthropic_api_key")
    except MissingSecret:
        pytest.skip("no Anthropic key in the Keychain")
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    for seat, model_id in load().seat_ids().items():
        r = client.messages.create(
            model=model_id,
            max_tokens=64,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
        )
        assert r.model == model_id, (seat, r.model)
        assert r.usage.output_tokens > 0


def test_a_seated_model_without_prices_is_refused():
    """The gateway reserves money before every call, so a seat whose model
    carries no prices is a seat it cannot enforce (plan 04, task 5)."""
    data = load().model_dump()
    seated = data["seats"]["frontier"]
    for entry in data["models"]:
        if entry["id"] == seated:
            entry["usd_per_mtok"] = None
    with pytest.raises(ValueError, match=seated):
        SeatFile.model_validate(data)


def test_prices_convert_to_micro_dollars_per_million_tokens():
    """Read through `Decimal`, so $6.25 is 6_250_000 and no binary float
    rounds a charge."""
    assert load().model("frontier").usd_micros_per_mtok == {
        "input": 5_000_000,
        "output": 25_000_000,
        "cache_write": 6_250_000,
        "cache_read": 500_000,
    }
    assert load().model("summarizer").usd_micros_per_mtok == {
        "input": 1_000_000,
        "output": 5_000_000,
        "cache_write": 1_250_000,
        "cache_read": 100_000,
    }
