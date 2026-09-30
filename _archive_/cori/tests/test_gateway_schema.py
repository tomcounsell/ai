"""`schemas/gateway.py` is the seam the rows are written through. Plan 04
task 1; seams §1.16."""

import typing

import pytest
from pydantic import ValidationError

from schemas.gateway import CacheState, GatewayEvent, RefusalReason, Usage


def a_usage(**over) -> Usage:
    fields = dict(
        input_tokens=10,
        output_tokens=20,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        usd_micros=123,
        charged_reserved=False,
    )
    fields.update(over)
    return Usage(**fields)


def test_usage_is_frozen():
    usage = a_usage()
    with pytest.raises(ValidationError):
        usage.input_tokens = 11


def test_usage_refuses_an_extra_field():
    with pytest.raises(ValidationError):
        Usage(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            usd_micros=0,
            charged_reserved=False,
            cut=True,
        )


def test_charged_reserved_has_no_default():
    """A row that does not say whether it was charged its reserve is a row
    the kill rerun cannot read (tech stack §14 item 1)."""
    with pytest.raises(ValidationError) as exc:
        Usage(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            usd_micros=0,
        )
    assert "charged_reserved" in str(exc.value)


def test_the_literals_are_the_values_seams_5_1_names():
    assert set(typing.get_args(GatewayEvent)) == {
        "request",
        "response",
        "cut",
        "refused",
        "upstream_error",
        "token_issued",
        "token_revoked",
    }
    assert set(typing.get_args(RefusalReason)) == {
        "unknown_token",
        "revoked",
        "model",
        "stale_generation",
        "budget",
    }
    assert set(typing.get_args(CacheState)) == {"write", "read", "none", "unstable"}
