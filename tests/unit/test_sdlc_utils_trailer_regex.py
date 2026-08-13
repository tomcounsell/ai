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

from tools._sdlc_utils import _HEAD_SHA_TRAILER_RE, head_sha_of_record, head_sha_of_text

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


# ---------------------------------------------------------------------------
# head_sha_of_record / head_sha_of_text -- the two-entry-point read helper over
# one body (#2769). The head SHA now rides as its own `head_sha` record field;
# the regex over the verdict text is the PERMANENT legacy fallback for ledgers
# written before the split (there is deliberately no migration).
# ---------------------------------------------------------------------------

OTHER_SHA = "1234567890abcdef1234567890abcdef12345678"


def test_head_sha_of_record_prefers_the_field():
    record = {"verdict": "APPROVED", "head_sha": SHA}
    assert head_sha_of_record(record) == SHA


def test_head_sha_of_record_falls_back_to_legacy_mangled_verdict():
    """A pre-split ledger stores the SHA inside the normalize-mangled token."""
    record = {"verdict": f"APPROVED REVIEW CONTEXT HEAD SHA={SHA.upper()}"}
    assert head_sha_of_record(record).lower() == SHA


def test_head_sha_of_record_field_and_trailer_agreeing():
    record = {"verdict": f"APPROVED REVIEW_CONTEXT head_sha={SHA}", "head_sha": SHA}
    assert head_sha_of_record(record) == SHA


def test_head_sha_of_record_field_wins_when_they_disagree():
    """Defined precedence: the FIELD is what the current writer recorded
    deliberately; an in-token trailer is legacy residue."""
    record = {"verdict": f"APPROVED REVIEW_CONTEXT head_sha={OTHER_SHA}", "head_sha": SHA}
    assert head_sha_of_record(record) == SHA


def test_head_sha_of_record_returns_empty_when_neither_present():
    assert head_sha_of_record({"verdict": "APPROVED"}) == ""


def test_head_sha_of_record_tolerates_non_dict_and_empty_field():
    assert head_sha_of_record(None) == ""
    assert head_sha_of_record("APPROVED") == ""
    # get_verdict coerces a legacy bare-string record to {"verdict": <str>},
    # a shape with no head_sha key at all.
    assert head_sha_of_record({"verdict": "APPROVED", "head_sha": "   "}) == ""


def test_head_sha_of_record_rejects_a_malformed_field_like_a_malformed_trailer():
    """Both read paths enforce the same 40-hex shape. `record_verdict`'s
    `head_sha` kwarg is public, so a field holding arbitrary text must not
    satisfy `_review_trailer_present` -- the gate that refuses a malformed
    trailer and tells the operator the verdict carries no well-formed head
    SHA."""
    for bad in ("not-a-sha", SHA[:39], SHA + "0", "z" * 40, f"  {SHA[:20]}  "):
        assert head_sha_of_record({"verdict": "APPROVED", "head_sha": bad}) == "", bad


def test_a_malformed_field_falls_back_to_a_well_formed_legacy_trailer():
    """Treated as absent, not as a hard failure: the permanent legacy read path
    still resolves the record."""
    record = {"verdict": f"APPROVED REVIEW_CONTEXT head_sha={SHA}", "head_sha": "not-a-sha"}
    assert head_sha_of_record(record) == SHA


def test_a_well_formed_field_is_accepted_in_either_case():
    assert head_sha_of_record({"verdict": "APPROVED", "head_sha": SHA.upper()}) == SHA.upper()
    assert head_sha_of_record({"verdict": "APPROVED", "head_sha": f"  {SHA}  "}) == SHA


def test_head_sha_of_text_is_regex_only_and_never_none():
    assert head_sha_of_text(f"APPROVED REVIEW_CONTEXT head_sha={SHA}") == SHA
    assert head_sha_of_text("APPROVED") == ""
    assert head_sha_of_text("") == ""
    # Non-str input must not raise -- the common ledger path passes None.
    assert head_sha_of_text(None) == ""
