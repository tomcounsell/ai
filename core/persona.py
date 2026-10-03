"""The persona: Valor's one identity, rendered at the top of every turn.

The text lives in `persona/` (`settings.persona_dir`) in the kernel's own
checkout and changes only by a reviewed diff: the identity as data in
`identity.toml`, then `turn.md` (what one turn is), `voice.md`,
`conduct.md`, `governance.md` (the heading and the line that introduces
the paragraph), and `delivery.md`. The
governance paragraph and the "Tests are not governance." paragraph are not
copied into any of them; each is read from its line of `CLAUDE.md`
(`corrections.GOVERNANCE_SOURCE`, the same file and the same first-line rule
as `corrections.governance_paragraph`) each time the persona is rendered,
so they are the same words by construction.

Nothing here reads a task's workspace, and nothing here decides anything:
the persona shapes what a turn says, the kernel and the broker bound what
it may do. `tasks.dispatch` puts the rendered text first in every turn's
text, so `turn.started`'s `brief_sha256` covers it, and records its own
digest as `persona_sha256`.
"""

import tomllib
from pathlib import Path

from core import corrections, ledger

# The identity fields, in the order they render, with their labels. The
# file's own key order does not matter; a key not listed here is ignored.
IDENTITY = (
    ("name", "Name"),
    ("email", "Email and Google Workspace account"),
    ("organization", "Organization"),
    ("timezone", "Timezone"),
    ("handles", "Handles"),
    ("supervisor", "Supervisor"),
)

TEXTS = ("voice.md", "conduct.md")
CLOSING = "delivery.md"


# The paragraphs of `CLAUDE.md` the persona carries under its governance
# heading, each found as the first line starting with its prefix.
RULES = ("**Governance", "**Tests are not governance.")


class PersonaUnreadable(ValueError):
    """A persona file is missing, the identity lacks a field `IDENTITY`
    names, or `CLAUDE.md` is missing or lacks a paragraph `RULES` names."""


def _text(directory: Path, name: str) -> str:
    path = directory / name
    try:
        return path.read_text().strip()
    except OSError as exc:
        raise PersonaUnreadable(f"{path}: {exc.strerror or exc}") from None


def rules() -> list[str]:
    """The `RULES` paragraphs from `CLAUDE.md` (`corrections.GOVERNANCE_SOURCE`),
    byte for byte, read each time the persona is rendered."""
    path = corrections.GOVERNANCE_SOURCE
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise PersonaUnreadable(f"{path}: {exc.strerror or exc}") from None
    found = []
    for prefix in RULES:
        line = next((line for line in lines if line.startswith(prefix)), None)
        if line is None:
            raise PersonaUnreadable(f"{path}: no line starting {prefix}")
        found.append(line)
    return found


def identity(directory: str | Path) -> dict[str, str]:
    path = Path(directory) / "identity.toml"
    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        raise PersonaUnreadable(f"{path}: {exc.strerror or exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise PersonaUnreadable(f"{path}: {exc}") from None
    missing = [key for key, _ in IDENTITY if not str(data.get(key, "")).strip()]
    if missing:
        raise PersonaUnreadable(f"{path}: missing identity key(s) {', '.join(missing)}")
    return {key: str(data[key]).strip() for key, _ in IDENTITY}


def render(directory: str | Path) -> str:
    """The persona as a turn reads it: the identity and what one turn is,
    the voice, the conduct, the governance section with the governance
    and tests paragraphs from `CLAUDE.md` under it, the delivery format. The
    same files give the same bytes."""
    directory = Path(directory)
    who = identity(directory)
    head = (
        "# Persona\n\n"
        + f"You are {who['name']}.\n\n"
        + "\n".join(f"- {label}: {who[key]}" for key, label in IDENTITY)
        + "\n\n"
        + _text(directory, "turn.md")
    )
    sections = [head, *(_text(directory, name) for name in TEXTS)]
    sections.append(_text(directory, "governance.md"))
    sections.extend(rules())
    sections.append(_text(directory, CLOSING))
    return "\n\n".join(sections)


def digest(text: str) -> str:
    return ledger.digest(text)
