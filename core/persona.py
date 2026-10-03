"""The persona: Valor's one identity, rendered at the top of every turn.

The text lives in `persona/` (`settings.persona_dir`) in the kernel's own
checkout and changes only by a reviewed diff: the identity as data in
`identity.toml`, then `turn.md` (what one turn is), `voice.md`,
`conduct.md`, and `delivery.md`. The
governance paragraph is not copied into any of them; it is read from the
first `**Governance` line of `CLAUDE.md` (`corrections.governance_paragraph`)
each time the persona is rendered, so it is the same words by construction.

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
# file's own key order does not matter; a key not listed here is refused.
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


class PersonaUnreadable(ValueError):
    """A persona file is missing, or the identity does not match `IDENTITY`."""


def _text(directory: Path, name: str) -> str:
    path = directory / name
    try:
        return path.read_text().strip()
    except OSError as exc:
        raise PersonaUnreadable(f"{path}: {exc.strerror or exc}") from None


def identity(directory: str | Path) -> dict[str, str]:
    path = Path(directory) / "identity.toml"
    try:
        data = tomllib.loads(path.read_text())
    except OSError as exc:
        raise PersonaUnreadable(f"{path}: {exc.strerror or exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise PersonaUnreadable(f"{path}: {exc}") from None
    known = {key for key, _ in IDENTITY}
    unknown = sorted(set(data) - known)
    if unknown:
        raise PersonaUnreadable(f"{path}: unknown identity key(s) {', '.join(unknown)}")
    missing = [key for key, _ in IDENTITY if not str(data.get(key, "")).strip()]
    if missing:
        raise PersonaUnreadable(f"{path}: missing identity key(s) {', '.join(missing)}")
    return {key: str(data[key]).strip() for key, _ in IDENTITY}


def render(directory: str | Path) -> str:
    """The persona as a turn reads it: the identity and what one turn is,
    the voice, the conduct,
    the governance paragraph from `CLAUDE.md`, the delivery format. The
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
    sections.append(corrections.governance_paragraph())
    sections.append(_text(directory, CLOSING))
    return "\n\n".join(sections)


def digest(text: str) -> str:
    return ledger.digest(text)
