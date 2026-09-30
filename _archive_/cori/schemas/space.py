"""The space manifest. Architecture §9: a space is a typed manifest the
kernel reads, not a directory. Fields are the ones §9 names and no more."""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Architecture §4: named, never numbered. `read` on the grant; `propose` on
# the grant with the supervisor asking first when it judges the person would
# want to be asked; `act` always a person. EFFECT_RANK is the one place the
# order is written; `covers`, `ceiling`, and `Ceilings.fits_within` compare
# ranks, never strings.
EffectClass = Literal["read", "propose", "act"]
EFFECT_RANK: dict[EffectClass, int] = {"read": 0, "propose": 1, "act": 2}
DataClass = Literal["PROJECT", "OPERATOR"]

# Architecture §9: the reserved space where an item no rule claims lands.
# It has no manifest file, so a manifest claiming the id is a parse error
# (seams §0). The kernel refuses to open an objective there.
UNASSIGNED_SPACE_ID = "unassigned"

# Decided in the plan: a space id is the first segment of every capability
# scope (`<space_id>/<path>`, seams §1.2) and the first field of every lock
# key and rule text, both of which separate on `:`. An id holding `/` or `:`
# would break `covers`, `space_of`, and `matches`, so the id is lowercase
# alphanumerics and hyphens, starting with a letter or a digit.
SPACE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoutingRule(Strict):
    """Deterministic: applied by the kernel at ingestion, never by a model."""

    sender_domain: str | None = None
    label: str | None = None
    folder: str | None = None
    calendar: str | None = None


class Connector(Strict):
    kind: Literal["gmail", "calendar", "messaging"]
    account: str
    route: RoutingRule


class Secret(Strict):
    """Kernel-injected into a worktree sandbox's environment, class-labeled.
    The value lives in the Keychain under `keychain_name`; never here."""

    name: str
    keychain_name: str
    data_class: DataClass


class Retention(Strict):
    """How long episodic memory and artifacts live, in days."""

    memory_days: int = 365
    artifacts_days: int = 365


class Audience(Strict):
    """Who an outbound message may go to. Architect, 2026-09-19 (seams §1.1):
    sourced by hand from the client's directory README in the work vault,
    because the manifest is inside the trust boundary and the vault is not.
    The verifier's `recipient_allowed` check reads it; routing never does."""

    addresses: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    source: str | None = None


class Space(Strict):
    id: str
    kind: Literal["client", "personal", "company"]
    roots: list[str] = Field(min_length=1)
    max_effect_class: EffectClass
    allowed_targets: list[str] = Field(default_factory=list)
    retention: Retention = Field(default_factory=Retention)
    connectors: list[Connector] = Field(default_factory=list)
    secrets: list[Secret] = Field(default_factory=list)
    audience: Audience = Field(default_factory=Audience)
    # No budget field. Architecture §4 (2026-09-20): the budget is money per
    # objective, estimated by the supervisor; nothing on the manifest budgets
    # anything.

    @field_validator("id")
    @classmethod
    def id_is_available_and_well_formed(cls, value: str) -> str:
        if value == UNASSIGNED_SPACE_ID:
            raise ValueError(
                f"{UNASSIGNED_SPACE_ID!r} is the reserved space of architecture "
                "§9 and has no manifest"
            )
        if not SPACE_ID_PATTERN.match(value):
            raise ValueError(
                f"space id {value!r} must match {SPACE_ID_PATTERN.pattern}: the id "
                "is the first segment of every capability scope and rule text"
            )
        return value


def load_space(path: Path) -> Space:
    return Space.model_validate(yaml.safe_load(path.read_text()))
