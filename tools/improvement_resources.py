"""Charter §8: verify each named resource before relying on it.

Charter §8 lists what the loop may use (a browser, personal and work Google
Workspace accounts, a virtual debit card, a funded Cloudflare account and its
connected CLI) and then says the part that matters here: "These are expected
capabilities, not a claim that every credential or integration currently works.
Verify availability before relying on it." ``probe`` is that verification.

Three states, and the default is the honest one:

- ``verified``: the resource was found.
- ``absent``: a successful, parseable listing did not contain it.
- ``unknown``: anything else at all.

``unknown`` is written on every uncertainty, never ``absent``. Reporting a
resource absent when it exists sends the next lane out to acquire something
already sitting in the vault, and older ``op`` builds could exit 0 on
unrecognized server errors, so a zero exit code proves nothing by itself.

**A credential never leaves this module.** The presence leg reads
``op item list --format json``, which returns titles, ids, vaults, and
timestamps and never a field value, so most of this module cannot leak by
construction. Where a fingerprint is wanted the credential is read, hashed
immediately, and reported as ``sha256:<hex>``; the plaintext is never returned,
never logged, and never placed in an argv. This repo disables 1Password's own
output masking by design, so that masking is not available as a backstop here
and this discipline is the only guard. ``runner`` is injectable so a test can
seed a distinctive fake credential and assert it appears nowhere in the result.

**Nothing here raises.** A probe that throws inside a controller tick is worse
than one that reports ``unknown``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

#: The vault of record for this machine's service account.
VAULT = "m-valor"

#: The three states a resource may be reported in.
STATES: tuple[str, ...] = ("verified", "absent", "unknown")

#: Every resource charter §8 names, in report order.
RESOURCES: tuple[str, ...] = (
    "workspace_personal",
    "workspace_work",
    "virtual_debit_card",
    "cloudflare_account",
    "cloudflare_cli",
    "vault_write",
)

#: Vault-backed resources, and the title keyword sets that identify each. A
#: title matches when every keyword in any one set appears in it, compared
#: case-insensitively. Heuristic by necessity: the probe classifies whatever
#: titles the vault happens to carry, and a near-miss reports absent rather
#: than inventing a match.
_VAULT_TITLE_KEYWORDS: dict[str, tuple[tuple[str, ...], ...]] = {
    "workspace_personal": (("google", "personal"), ("workspace", "personal")),
    "workspace_work": (("google", "work"), ("workspace", "work")),
    "virtual_debit_card": (("virtual", "card"), ("debit", "card")),
    "cloudflare_account": (("cloudflare",),),
}

#: The sanctioned vault writer. It belongs to the lane that builds the
#: acquisition path, so its absence here is a correct answer, not a defect.
_VAULT_WRITER = Path(__file__).resolve().parent / "vault_write.py"

Runner = Callable[[list[str]], "subprocess.CompletedProcess"]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess:
    """Shell out with a bound timeout, capturing output rather than printing it."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def _entry(state: str, detail: str, fingerprint: str | None = None) -> dict:
    return {"state": state, "detail": detail, "fingerprint": fingerprint}


def _run(runner: Runner, argv: list[str]) -> subprocess.CompletedProcess | None:
    """Run a command, returning None on any failure at all."""
    try:
        completed = runner(argv)
    except Exception as e:
        logger.debug("resource probe: %s failed: %s", argv[0], e)
        return None
    if not isinstance(completed, subprocess.CompletedProcess):
        return None
    return completed


def _vault_titles(runner: Runner) -> list[str] | None:
    """Titles in the vault, or None when the listing could not be trusted.

    None is the ``unknown`` signal. It covers a failed call, a non-zero exit,
    empty output, unparseable JSON, and a parseable payload that is not the
    list of objects this command is documented to return.
    """
    completed = _run(runner, ["op", "item", "list", "--vault", VAULT, "--format", "json"])
    if completed is None or completed.returncode != 0:
        return None

    stdout = (completed.stdout or "").strip()
    if not stdout:
        return None

    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(payload, list):
        return None

    return [str(item.get("title", "")) for item in payload if isinstance(item, dict)]


def _matches(title: str, keyword_sets: tuple[tuple[str, ...], ...]) -> bool:
    lowered = title.lower()
    return any(all(word in lowered for word in words) for words in keyword_sets)


def _fingerprint_cloudflare_token(runner: Runner, title: str) -> str | None:
    """``sha256:<hex>`` of the Cloudflare credential, or None.

    The plaintext lives in a local for exactly as long as it takes to hash it.
    It is never returned, never logged, and never placed in an argv.
    """
    completed = _run(runner, ["op", "read", f"op://{VAULT}/{title}/credential"])
    if completed is None or completed.returncode != 0:
        return None

    secret = (completed.stdout or "").strip()
    if not secret:
        return None
    return "sha256:" + hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _probe_vault_resources(runner: Runner) -> dict[str, dict]:
    titles = _vault_titles(runner)
    if titles is None:
        return {
            name: _entry(
                "unknown",
                f"the {VAULT} vault listing could not be read; op may be unauthenticated",
            )
            for name in _VAULT_TITLE_KEYWORDS
        }

    report: dict[str, dict] = {}
    for name, keyword_sets in _VAULT_TITLE_KEYWORDS.items():
        matched = next((t for t in titles if _matches(t, keyword_sets)), None)
        if matched is None:
            report[name] = _entry("absent", f"no item in {VAULT} matches this resource")
            continue

        fingerprint = None
        if name == "cloudflare_account":
            fingerprint = _fingerprint_cloudflare_token(runner, matched)
        report[name] = _entry("verified", f"found in {VAULT}", fingerprint)
    return report


def _probe_cloudflare_cli(runner: Runner) -> dict:
    """Classify the wrangler CLI, distinguishing "not installed" from "uncertain".

    Calls the runner directly rather than through :func:`_run`, because
    ``_run`` collapses every exception into a single ``None`` and loses the
    one distinction that matters here: ``FileNotFoundError`` means the binary
    genuinely is not on PATH (``absent``), while a timeout, an ``OSError``, or
    any other failure means the check was inconclusive (``unknown``). Reusing
    ``_run`` would make a `wrangler` timeout report `absent`: a certain
    answer to an uncertain question, and the specific harm the three-state
    vocabulary exists to prevent.
    """
    try:
        completed = runner(["wrangler", "--version"])
    except FileNotFoundError:
        return _entry("absent", "wrangler is not installed or not on PATH")
    except Exception as e:
        logger.debug("resource probe: wrangler --version failed: %s", e)
        return _entry("unknown", f"wrangler check did not complete: {e}")
    if not isinstance(completed, subprocess.CompletedProcess):
        return _entry("unknown", "wrangler check returned an unexpected result")
    if completed.returncode != 0:
        return _entry("unknown", f"wrangler exited {completed.returncode}")
    return _entry("verified", "wrangler answers on PATH")


def _probe_vault_write() -> dict:
    if _VAULT_WRITER.exists():
        return _entry("verified", "the sanctioned vault writer is present")
    return _entry("absent", "no sanctioned vault writer exists yet; acquisition is not wired")


def probe(*, runner: Runner | None = None) -> dict[str, dict]:
    """Classify every charter §8 resource without emitting a credential.

    Returns one entry per name in :data:`RESOURCES`, each
    ``{"state", "detail", "fingerprint"}``. Never raises: every failure becomes
    an ``unknown`` on the affected resource, and one dead call does not blank
    the rest of the report.
    """
    runner = runner or _default_runner
    report: dict[str, dict] = {}

    try:
        report.update(_probe_vault_resources(runner))
    except Exception as e:
        logger.warning("resource probe: vault leg failed: %s", e)

    try:
        report["cloudflare_cli"] = _probe_cloudflare_cli(runner)
    except Exception as e:
        logger.warning("resource probe: wrangler leg failed: %s", e)

    try:
        report["vault_write"] = _probe_vault_write()
    except Exception as e:
        logger.warning("resource probe: vault-writer leg failed: %s", e)

    for name in RESOURCES:
        report.setdefault(name, _entry("unknown", "the probe could not run this check"))
    return {name: report[name] for name in RESOURCES}
