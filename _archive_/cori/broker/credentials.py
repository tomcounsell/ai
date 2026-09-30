"""Credentials keyed by space and target. Plan 07 task 4; tech stack §7.

One table, keyed by the pair, each row naming Keychain entries rather than
holding values. Blind-spot finding 9 is the reason for the pair: a credential
that belongs to a target alone is a credential every space can reach, and the
first cross-space leak would be a space using another's token on a target
they happen to share. A pair with no row is a refusal.

A code table rather than a manifest field or a third file, decided in plan
07: `broker/` is inside the trust boundary of `.github/CODEOWNERS`, so adding
a row is a reviewed change, while a manifest is edited more freely.

Values are read through `infra.secrets.read_secret` at use. Nothing here
holds a secret value, so nothing here can put one in a traceback, a row, or a
log line.
"""

from __future__ import annotations

from dataclasses import dataclass

from broker.errors import EffectRefused
from infra.secrets import load_secrets
from schemas.ids import SpaceId

__all__ = ["CREDENTIALS", "Credential", "credential_for"]


@dataclass(frozen=True)
class Credential:
    """What a space may use on a target: the Keychain names, not the values."""

    space: SpaceId
    target: str
    names: tuple[str, ...]

    def secrets(self) -> dict[str, str]:
        """Read every named secret now. Raises `MissingSecret` on the first
        one the Keychain does not have, naming it."""
        return load_secrets(self.names)

    def value(self, name: str) -> str:
        """One named secret, for the single-secret targets."""
        if name not in self.names:
            raise EffectRefused(
                f"{self.space!r} on {self.target!r} has no credential named {name!r}"
            )
        return self.secrets()[name]


CREDENTIALS: dict[tuple[str, str], tuple[str, ...]] = {
    ("psyoptimal", "github.com/yudame"): ("github_token",),
    ("psyoptimal", "mailto:tom@yuda.me"): (
        "google_oauth_client_id",
        "google_oauth_client_secret",
        "gmail_refresh_token",
    ),
}


def credential_for(space_id: SpaceId, target: str) -> Credential:
    """The credential this space may use on this target.

    Refuses the pair the table does not have, which covers both an unknown
    space and a space reaching a target that belongs to another.
    """
    names = CREDENTIALS.get((space_id, target))
    if names is None:
        raise EffectRefused(f"no credential for {space_id!r} on {target!r}")
    return Credential(space=space_id, target=target, names=names)
