"""Regression tests for the hoisted ``_HEAD_SHA_TRAILER_RE`` constant (#2193).

``_HEAD_SHA_TRAILER_RE`` used to be defined locally in
``tools/merge_predicate.py``. It has been hoisted into ``tools/_sdlc_utils.py``
as the single definition so later writers (finalize/selfcheck helpers) share
identical matching semantics with the merge-predicate reader.

Two things must hold after the hoist:

1. The constant matches both the raw ``REVIEW_CONTEXT head_sha=<hex>`` form
   the review skill emits and its normalized image
   ``REVIEW CONTEXT HEAD SHA=<HEX>`` (``sdlc-tool verdict record`` uppercases
   and maps underscores to spaces via ``agent.sdlc_router.normalize_verdict``).
2. ``tools.merge_predicate`` still imports cleanly. Hoisting adds exactly one
   new edge (``merge_predicate -> _sdlc_utils``); ``_sdlc_utils`` does not
   import ``merge_predicate``, so this edge is acyclic -- but only a live
   import proves it.
"""

from __future__ import annotations

import importlib

from tools._sdlc_utils import _HEAD_SHA_TRAILER_RE

SHA = "abcdef0123456789abcdef0123456789abcdef01"


def test_matches_raw_trailer_form():
    text = f"Looks good.\n\nREVIEW_CONTEXT head_sha={SHA}\n"
    match = _HEAD_SHA_TRAILER_RE.search(text)
    assert match is not None
    assert match.group(1).lower() == SHA.lower()


def test_matches_normalized_trailer_form():
    # normalize_verdict uppercases and maps underscores to spaces.
    text = f"APPROVED\n\nREVIEW CONTEXT HEAD SHA={SHA.upper()}\n"
    match = _HEAD_SHA_TRAILER_RE.search(text)
    assert match is not None
    assert match.group(1).lower() == SHA.lower()


def test_raw_and_normalized_forms_match_identically():
    raw_text = f"REVIEW_CONTEXT head_sha={SHA}"
    normalized_text = f"REVIEW CONTEXT HEAD SHA={SHA.upper()}"

    raw_match = _HEAD_SHA_TRAILER_RE.search(raw_text)
    normalized_match = _HEAD_SHA_TRAILER_RE.search(normalized_text)

    assert raw_match is not None
    assert normalized_match is not None
    assert raw_match.group(1).lower() == normalized_match.group(1).lower()


def test_no_trailer_present_returns_no_match():
    assert _HEAD_SHA_TRAILER_RE.search("APPROVED, no trailer here.") is None


def test_merge_predicate_imports_cleanly_after_hoist():
    """Cycle guard: merge_predicate -> _sdlc_utils is a new, acyclic edge."""
    module = importlib.import_module("tools.merge_predicate")
    assert module is not None
    # The constant is no longer defined on merge_predicate itself -- it is
    # imported lazily inside _check_verdict_freshness from _sdlc_utils.
    assert not hasattr(module, "_HEAD_SHA_TRAILER_RE")
