"""Named secrets from the macOS Keychain, read once at process start.
Tech stack §10: provider keys and broker credentials live in the Keychain;
nothing in a dotfile. A missing secret stops the process with its name.

Items are generic passwords under service "cori", account = secret name:

    security add-generic-password -s cori -a anthropic_api_key -w '<key>' -U

The durable copy of every secret is the 1Password vault `m-tomcounsell`
(items named CORI_*). When Cori runs on a host that is not Tom's Mac, the
loader reads from 1Password through `op` with a service account scoped to
a Cori vault; a personal `op` session is never the process's credential.
"""

import subprocess
from collections.abc import Iterable

SERVICE = "cori"


class MissingSecret(RuntimeError):
    def __init__(self, name: str):
        super().__init__(
            f"secret {name!r} is not in the Keychain under service {SERVICE!r}; "
            f"add it with: security add-generic-password -s {SERVICE} -a {name} -w"
        )
        self.name = name


def read_secret(name: str) -> str:
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:  # no Keychain on this machine (CI)
        raise MissingSecret(name) from None
    if r.returncode != 0 or not r.stdout.strip():
        raise MissingSecret(name)
    return r.stdout.strip()


def load_secrets(names: Iterable[str]) -> dict[str, str]:
    """Read every named secret, or fail on the first one that is absent."""
    return {name: read_secret(name) for name in names}
