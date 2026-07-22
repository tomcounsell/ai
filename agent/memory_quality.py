"""Shared junk-definition heuristics for the subconscious memory pipeline.

This is the Phase-1 (measure, issue #2200) *and* Phase-2 (write gate,
issue #2201) single source of truth for "what counts as junk" in a Memory
record's content. Both `tools/memory_eval/ingest_quality.py` (read-only
corpus aggregation) and a future `models/` write gate import this module,
so any refinement of the heuristics lands here once and both consumers move
together.

Deliberately dependency-light: no imports of popoto, redis, or `models` --
this module must be importable from both a pure-aggregation CLI/tool and
from `models/memory.py` (a hot write path) without pulling in Redis clients
or circular imports. Pure string classification only.
"""

from __future__ import annotations

import re

# Acknowledgement / filler lexicon for the ack-only heuristic. Tokens are
# matched after lowercasing and collapsing repeated-letter runs (see
# `_normalize_token`), so "Ahhh" and "Ohhh" match "ah" / "oh" without
# needing every elongation spelled out here.
_ACK_LEXICON: frozenset[str] = frozenset(
    {
        "yup",
        "yep",
        "yeah",
        "yea",
        "ya",
        "yes",
        "no",
        "nope",
        "nah",
        "ok",
        "okay",
        "k",
        "kk",
        "sure",
        "fine",
        "cool",
        "great",
        "nice",
        "thanks",
        "thank",
        "thx",
        "ty",
        "np",
        "welcome",
        "you",
        "right",
        "correct",
        "true",
        "false",
        "got",
        "it",
        "gotcha",
        "ah",
        "oh",
        "hm",
        "huh",
        "wow",
        "yikes",
        "oof",
        "lol",
        "haha",
        "hehe",
        "please",
        "sorry",
    }
)

_TOKEN_RE = re.compile(r"[a-zA-Z']+")
_REPEATED_CHAR_RE = re.compile(r"(.)\1+")
_LIST_MARKER_ONLY_RE = re.compile(r"^([-*•]|\d+[.)])\s*$")


def _normalize_token(token: str) -> str:
    """Collapse runs of a repeated character to one instance.

    Lets elongated interjections ("Ahhh", "Ohhh", "yesss") match their base
    lexicon entry ("ah", "oh", "yes") without enumerating every elongation.
    """
    return _REPEATED_CHAR_RE.sub(r"\1", token)


def _tokenize(content: str) -> list[str]:
    """Extract lowercase word tokens (letters/apostrophes only) from content."""
    return _TOKEN_RE.findall(content.lower())


def is_ack_only(content: str | None) -> bool:
    """Return True if ``content`` is a bare acknowledgement / filler utterance.

    Heuristic: stripped content tokenizes to <= 3 words, and every token
    (after collapsing repeated-letter runs, e.g. "Ahhh" -> "ah") matches the
    acknowledgement lexicon. Covers cases like "Yup", "Ahhh", "ok", "thanks".

    ``None`` and whitespace-only/empty input return False here (they are
    classified as "fragment" by `classify_content`, not "ack_only" -- see
    that function's docstring for the documented disposition).
    """
    if not content:
        return False
    stripped = content.strip()
    if not stripped:
        return False
    tokens = _tokenize(stripped)
    if not tokens or len(tokens) > 3:
        return False
    return all(_normalize_token(t) in _ACK_LEXICON or t in _ACK_LEXICON for t in tokens)


def _has_unbalanced_brackets(content: str) -> bool:
    """True if any of ()/[]/{} appear an unequal number of times."""
    for open_ch, close_ch in (("(", ")"), ("[", "]"), ("{", "}")):
        if content.count(open_ch) != content.count(close_ch):
            return True
    return False


def _has_dangling_colon(content: str) -> bool:
    """True if content ends with ':' and has no body following it.

    A single-line utterance ending in ':' (e.g. "includes:") is dangling.
    A multi-line utterance ending in ':' with a following body (e.g. a
    header followed by list items) is not -- only the trailing-line case
    with nothing after it counts.
    """
    if not content.endswith(":"):
        return False
    if "\n" not in content:
        return True
    body = content.split("\n", 1)[1].strip()
    return body == ""


def _is_bare_list_marker(content: str) -> bool:
    """True if content is only a list marker ("-", "*", "1.") with no body."""
    return bool(_LIST_MARKER_ONLY_RE.match(content))


def is_fragment(content: str | None) -> bool:
    """Return True if ``content`` is dangling/incomplete syntax.

    Covers: unbalanced brackets, a trailing colon with no body ("includes:"),
    and a bare list marker with no content ("-", "1.").

    ``None`` and whitespace-only/empty input return True (documented
    disposition: absent content cannot be a durable fact, so it is treated
    as a fragment).
    """
    if content is None:
        return True
    stripped = content.strip()
    if not stripped:
        return True
    if _has_unbalanced_brackets(stripped):
        return True
    if _has_dangling_colon(stripped):
        return True
    if _is_bare_list_marker(stripped):
        return True
    return False


def classify_content(content: str | None) -> str:
    """Classify Memory content as "durable", "ack_only", or "fragment".

    This is the Phase-1/2 shared junk definition (see module docstring).
    Order of precedence: ack-only utterances are checked first (a short
    acknowledgement takes priority over any coincidental dangling-syntax
    match), then dangling/incomplete fragments, and everything else is
    "durable".

    ``None``, ``""``, and whitespace-only input are deterministic and never
    raise: they classify as "fragment" (documented disposition -- absent
    content carries no acknowledgement signal either, so "fragment" is the
    more accurate bucket than "ack_only").
    """
    if content is None:
        return "fragment"
    stripped = content.strip()
    if not stripped:
        return "fragment"
    if is_ack_only(stripped):
        return "ack_only"
    if is_fragment(stripped):
        return "fragment"
    return "durable"
