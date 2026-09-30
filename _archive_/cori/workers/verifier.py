"""What differs for the Verifier class and nothing else (tech stack §5).
Plan 11; seams §1.5, §1.6, §3.6, §7; architecture §5, §6.

The Verifier holds `read` and `bash` at `read`, scoped to the space, and no
`ask`: the supervisor answers questions and has read the Executor's report,
so a question would be a channel from the narrative into the blind context
that architecture §5 closes. A Verifier that cannot judge abstains.

`load_prompt` reads the seat paragraph and the kind checklist from
`prompts/verifier/`, both inside the trust boundary (`.github/CODEOWNERS`);
its sha256 with the seat's `model_ref` keys a calibration series
(architecture §5: a change of model or prompt starts a fresh series).
`derive_outcome` is the rule the `Verdict` validator states, re-exported
so the kernel verdict and the tests share the schema's one implementation.
`validate_verdict` checks what the schema cannot: that the criterion
strings are the contract's, once each.

This module imports `schemas/` only.
"""

import hashlib
from pathlib import Path

from schemas.capability import CapabilityName
from schemas.objective import ArtifactKind
from schemas.report import Verdict, derive_outcome

__all__ = [
    "CLASS_PROMPT",
    "INSTRUCTION",
    "TOOLS",
    "PROMPTS_DIR",
    "load_prompt",
    "derive_outcome",
    "validate_verdict",
]

TOOLS: tuple[CapabilityName, ...] = ("read", "bash")
# What the adapter prepends for this class (its `prompts` mapping): the seat
# paragraph and the checklist travel in the slice's `instruction` block, so
# the class line only points at them.
CLASS_PROMPT = (
    "This seat's prompt is the instruction block below, on top of the voice "
    "block; the criteria, artifacts, checks, tool log, and effect ledger "
    "blocks are what it judges."
)
# The user turn the Verifier's run opens with. Decided in build: the
# adapter's default says "return the Report", which is the Executor's
# shape, so the Verifier's Brief names its own.
INSTRUCTION = (
    "Judge the artifact against every success criterion using the blocks "
    "above and the tools you hold, then return the Verdict."
)
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts" / "verifier"
CHECKLISTS: dict[ArtifactKind, str] = {
    "code": "code.md",
    "document": "document.md",
    "message": "message.md",
}


def load_prompt(artifact_kind: ArtifactKind) -> tuple[str, str]:
    """(text, sha256) of `seat.md` followed by the kind's checklist. The
    hash is over the exact bytes joined, so an edit to either file starts a
    fresh series."""
    try:
        checklist = CHECKLISTS[artifact_kind]
    except KeyError:
        raise ValueError(f"no Verifier checklist for {artifact_kind!r}") from None
    seat = (PROMPTS_DIR / "seat.md").read_text()
    kind = (PROMPTS_DIR / checklist).read_text()
    text = seat.strip() + "\n\n" + kind.strip() + "\n"
    return text, hashlib.sha256(text.encode()).hexdigest()


def validate_verdict(verdict: Verdict, *, criteria: list[str]) -> list[str]:
    """Problems with a Verdict against a contract's criteria: a criterion
    the Verdict does not name, one it names that the contract does not, one
    it names twice. Empty when the strings are the contract's, once each.
    The outcome rule is the schema's validator and is not repeated here."""
    named = [c.criterion for c in verdict.criteria]
    problems: list[str] = []
    for c in criteria:
        n = named.count(c)
        if n == 0:
            problems.append(f"criterion not judged: {c!r}")
        elif n > 1:
            problems.append(f"criterion judged {n} times: {c!r}")
    for c in named:
        if c not in criteria:
            problems.append(f"criterion not in the contract: {c!r}")
    return problems
