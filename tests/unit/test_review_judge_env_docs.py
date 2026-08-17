"""Every `SDLC_REVIEW_*` env control named in the review docs must have a reader (#2831).

`SDLC_REVIEW_JUDGES` and `SDLC_REVIEW_K` were documented as operator kill
switches in three files for 100 days while nothing anywhere read either one.
An operator setting `SDLC_REVIEW_JUDGES=none` got two judges anyway, with no
diagnostic, and three documents telling them it should have worked.

This encodes the property that actually broke, as a POSITIVE predicate: every
`SDLC_REVIEW_*` token appearing in the review docs must resolve to a field in
`config/settings.py`. A blocklist of the two retired names would go green the
moment they were deleted and catch nothing thereafter; this stays useful
against the third variable someone documents tomorrow.

Why this shape rather than an execution test: in this repo a review skill
"reads" configuration by being told to in prose, so there is no function to
call that would honor `SDLC_REVIEW_JUDGES`. The honest testable property is
declaration consistency between the docs and the settings surface, following
the precedent in `test_skill_agent_tool_consistency.py`.

Scanning is by explicit path, never a tree walk, so `docs/plans/` — which
legitimately carries historical mentions of both retired names — cannot trip
it, and this file is free to name them in its own assertions.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The three files that carried the false claim. `docs/features/README.md` is
# green under this predicate today and contributes nothing to the original red
# run; it is scanned so a future re-addition to the index is caught by the same
# guard that cleaned it.
SCANNED_DOCS: tuple[Path, ...] = (
    REPO_ROOT / "docs" / "features" / "multi-judge-consensus.md",
    REPO_ROOT / "docs" / "sdlc" / "do-pr-review.md",
    REPO_ROOT / "docs" / "features" / "README.md",
)

SETTINGS_PATH = REPO_ROOT / "config" / "settings.py"

_TOKEN_RE = re.compile(r"SDLC_REVIEW_[A-Z0-9_]+")


def _scan(paths: tuple[Path, ...]) -> set[str]:
    """Return every ``SDLC_REVIEW_*`` token appearing in ``paths``.

    Takes paths as a parameter rather than closing over ``SCANNED_DOCS`` so the
    same predicate can be pointed at pre-edit copies (``git show main:<path>``)
    to reproduce the original red observation after the fix has landed.
    """
    found: set[str] = set()
    for path in paths:
        found.update(_TOKEN_RE.findall(path.read_text(encoding="utf-8")))
    return found


def _settings_fields(settings_src: str) -> set[str]:
    """Return the field names declared in ``config/settings.py``.

    Anchored to a field declaration at the start of a line. A substring test
    would be wrong twice over: `SDLC_REVIEW_CROSS_VENDOR` would resolve against
    the longer `sdlc_review_cross_vendor_model` by prefix collision, and the
    uppercase env names appear inside `description=` strings, so any
    case-insensitive substring match would let a doc-only variable pass merely
    because someone mentioned it in prose.
    """
    return set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\s*:", settings_src, re.MULTILINE))


def test_documented_review_env_vars_have_a_settings_field():
    """A documented env control with no reader is the #2831 defect."""
    fields = _settings_fields(SETTINGS_PATH.read_text(encoding="utf-8"))

    undeclared = sorted(tok for tok in _scan(SCANNED_DOCS) if tok.lower() not in fields)

    assert not undeclared, (
        "These SDLC_REVIEW_* env controls are documented but have no field in "
        f"config/settings.py, so nothing reads them: {undeclared}. Either add a "
        "settings field and a prose instruction telling the review skill to "
        "consult it, or stop documenting them as operator controls (#2831)."
    )


def test_scanned_docs_exist():
    """Guard the guard: a rename must not make the check vacuously pass."""
    for path in SCANNED_DOCS:
        assert path.is_file(), f"scanned doc missing: {path}"
        assert path.read_text(encoding="utf-8").strip(), f"scanned doc empty: {path}"


def test_settings_field_extraction_finds_a_known_field():
    """Guard the guard: prove the extractor resolves real fields and only real ones.

    The negative half is what proves the anchoring works — a regex matching
    everything would satisfy the positive assertion alone, and would then let
    every documented token pass as "declared".
    """
    fields = _settings_fields(SETTINGS_PATH.read_text(encoding="utf-8"))

    assert "sdlc_review_cross_vendor" in fields
    assert "sdlc_review_cross_vendor_model" in fields
    # Plausible, but not a declared field — a prefix of a real one.
    assert "sdlc_review_cross" not in fields
    # Never declared anywhere; the retired names must not resolve.
    assert "sdlc_review_judges" not in fields
    assert "sdlc_review_k" not in fields
