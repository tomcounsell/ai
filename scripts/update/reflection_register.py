"""Update-system step: ensure the ``crash-recovery`` reflection is registered.

Issue #1917 (the dominant gap). ``reflections/crash_recovery.py`` ships a
callable (``run_crash_recovery``) that fingerprints session crashes, warms a
signature library, and auto-resumes transient tool-wedge deaths -- but it was
never added to the reflections registry. ``python -m reflections --dry-run``
therefore never lists it, and ``valor-session crash-signatures`` stays empty
after months of crashes. #1539 built the whole layer but left registration as
a manual vault edit that was never performed.

Why this needs its own update step (mirrors ``reflection_arm.py``'s rationale):
``config/reflections.yaml`` is gitignored -- an install-time COPY of the iCloud
vault source (``~/Desktop/Valor/reflections.yaml``), refreshed by
``env_sync.sync_reflections_yaml()`` (Step 1.66) on every ``/update``. Appending
the entry only to the in-repo copy is silently clobbered the next time that copy
step runs, so registration for real means appending the entry to the *vault*
file. The target is resolved via
``agent.reflection_scheduler._resolve_registry_path()`` (critique C6), which
prioritizes the vault over the config copy -- a builder who hardcoded the config
copy would reproduce #1539's "looks wired, never lands" failure. This step runs
BEFORE Step 1.66's vault->config copy (critique NIT) so the appended entry
propagates into the per-machine ``config/reflections.yaml`` on the same cycle.

Guarded on (mirroring ``reflection_arm.py``):
  - the vault ``reflections.yaml`` existing (fresh machines with no vault copy
    skip -- nothing to register into yet; the in-repo fallback stays as authored)
  - this machine owning the ``valor`` project per ``config/projects.json`` --
    a machine that isn't the single owner of this repo's own project never
    mutates the shared iCloud vault file. Non-owners receive the entry via the
    vault's iCloud sync + Step 1.66 copy, then run the reflection in propose mode.

The entry is UNSCOPED (``enabled: true``, no ``project_key``): every machine runs
the reflection in propose mode. Auto-resume is gated separately (the
``FEATURES__CRASH_AUTORESUME_ENABLED`` env flag plus the per-project
machine-ownership check inside ``run_crash_recovery``), so no
``reflection_machine_filter`` change is needed.

Append-only and idempotent: a no-op when a ``crash-recovery`` entry already
exists. The write is atomic (temp file + ``os.replace``) and validated by
re-loading the YAML before it replaces the original, so a crash or iCloud sync
race never leaves a truncated registry.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

REFLECTION_NAME = "crash-recovery"
OWNING_PROJECT_KEY = "valor"

# The documented registry entry, appended verbatim when absent. Kept as a text
# block (not a yaml.safe_dump) so the surrounding hand-authored registry keeps
# its header docs and inline comments -- a dump round-trip would strip them.
_ENTRY_BLOCK = """\

  - name: crash-recovery
    description: "Fingerprint crashes, warm signatures, auto-resume tool-wedge deaths (#1917)"
    every: 300s # 5 minutes
    priority: normal
    execution_type: function
    callable: "reflections.crash_recovery.run_crash_recovery"
    enabled: true
"""

_EXPECTED_CALLABLE = "reflections.crash_recovery.run_crash_recovery"


@dataclass
class RegisterResult:
    """Outcome of a single register-reflection attempt.

    Fields:
        success: True unless an unexpected error occurred (best-effort step;
            callers treat success=False as a non-fatal warning).
        action: One of "registered" (entry appended to the vault), "noop"
            (entry already present), "skipped" (guard not met -- no vault file,
            or this machine doesn't own the project), or "error".
        detail: Human-readable explanation for logging.
    """

    success: bool
    action: str
    detail: str = ""


def _vault_reflections_path() -> Path:
    """The iCloud vault registry, honoring an explicit REFLECTIONS_YAML override.

    Mirrors ``reflection_arm._vault_reflections_path`` so the two update steps
    resolve the same target file (and so tests can point both at a tmp file).
    """
    env_path = os.environ.get("REFLECTIONS_YAML")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / "Desktop" / "Valor" / "reflections.yaml"


def _resolve_target() -> Path:
    """Resolve the registry file to write, prioritizing the vault (critique C6).

    Delegates to ``agent.reflection_scheduler._resolve_registry_path`` -- the
    same vault-first resolver the scheduler reads at runtime -- so the entry
    lands where the scheduler will actually look, not in the soon-clobbered
    config copy. Imported lazily because the scheduler transitively imports
    heavy models; the update step only needs it at call time.
    """
    from agent.reflection_scheduler import _resolve_registry_path

    return _resolve_registry_path()


def _this_machine_owns_valor(project_dir: Path) -> bool:
    """True iff config/projects.json's 'valor' project.machine == this host.

    Fails closed (returns False) on any missing file/field so a misconfigured
    or partial checkout never mutates the shared vault file.
    """
    from tools.machine_identity import computer_name

    projects_path = project_dir / "config" / "projects.json"
    if not projects_path.exists():
        return False
    try:
        data = json.loads(projects_path.read_text())
    except Exception:
        return False

    owner = (data.get("projects", {}).get(OWNING_PROJECT_KEY) or {}).get("machine", "")
    if not isinstance(owner, str) or not owner.strip():
        return False

    machine = computer_name()
    if not machine:
        return False

    return owner.strip().lower() == machine.strip().lower()


def _has_entry(text: str) -> bool:
    """True iff a reflection named ``crash-recovery`` already exists in ``text``.

    Parses the YAML rather than grepping so a commented-out mention or a
    substring in another entry's description never reads as present.
    """
    import yaml

    try:
        data = yaml.safe_load(text)
    except Exception:
        # A malformed registry is not ours to repair here -- treat as "present"
        # so we do not append into a broken file. The caller reports io-error
        # via the failed re-parse in _append_entry instead.
        return True
    if not isinstance(data, dict):
        return True
    entries = data.get("reflections") or []
    if not isinstance(entries, list):
        return True
    return any(isinstance(e, dict) and e.get("name") == REFLECTION_NAME for e in entries)


def _append_entry(path: Path) -> str:
    """Append the ``crash-recovery`` entry to the registry at ``path``.

    Returns a verdict, never raises:
      "present"    -- entry already there (no write)
      "appended"   -- file rewritten with the entry, re-parse validated
      "not-found"  -- file absent
      "invalid"    -- post-append YAML failed to parse or lacked the entry
      "io-error"   -- read or atomic-write failure
    """
    if not path.exists():
        return "not-found"
    try:
        text = path.read_text()
    except Exception:
        return "io-error"

    if _has_entry(text):
        return "present"

    new_text = text
    if not new_text.endswith("\n"):
        new_text += "\n"
    new_text += _ENTRY_BLOCK

    # Validate before replacing: the appended text must parse and the entry must
    # be readable with the expected callable. Guards against appending into a
    # file whose top-level shape would make the new list item invalid.
    import yaml

    try:
        data = yaml.safe_load(new_text)
    except Exception:
        return "invalid"
    if not isinstance(data, dict):
        return "invalid"
    entries = data.get("reflections") or []
    match = next(
        (e for e in entries if isinstance(e, dict) and e.get("name") == REFLECTION_NAME),
        None,
    )
    if match is None or match.get("callable") != _EXPECTED_CALLABLE:
        return "invalid"

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(new_text)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        return "io-error"
    return "appended"


def register_crash_recovery(project_dir: Path) -> RegisterResult:
    """Ensure the ``crash-recovery`` reflection is registered in the vault.

    See the module docstring for the guard conditions and why the vault file
    (not the config copy) is written. Idempotent -- a no-op when the entry is
    already present.
    """
    vault_path = _vault_reflections_path()
    if not vault_path.exists():
        return RegisterResult(True, "skipped", f"vault reflections.yaml not found at {vault_path}")

    if not _this_machine_owns_valor(project_dir):
        return RegisterResult(True, "skipped", "this machine does not own the 'valor' project")

    # Resolve via the scheduler's vault-first resolver (critique C6). With the
    # vault present and not running under launchd, this returns the vault file.
    try:
        target = _resolve_target()
    except Exception as e:  # pragma: no cover - defensive
        target = vault_path
        _ = e

    verdict = _append_entry(target)
    if verdict == "present":
        return RegisterResult(True, "noop", f"{REFLECTION_NAME} already registered")
    if verdict == "not-found":
        return RegisterResult(
            False,
            "error",
            f"registry not found at {target}; will retry next /update",
        )
    if verdict == "invalid":
        return RegisterResult(
            False,
            "error",
            f"appended entry failed re-parse at {target}; file left untouched",
        )
    if verdict == "io-error":
        return RegisterResult(
            False,
            "error",
            f"could not write registry at {target}; will retry next /update",
        )

    # Best-effort: also append to the in-repo copy so the entry is live even if
    # Step 1.66's vault->config copy is skipped this cycle. The copy is clobbered
    # from the vault anyway, so a failure here is non-fatal.
    repo_copy = project_dir / "config" / "reflections.yaml"
    if repo_copy.exists() and repo_copy != target:
        _append_entry(repo_copy)

    return RegisterResult(True, "registered", f"{REFLECTION_NAME} appended to {target}")


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent.parent
    result = register_crash_recovery(project_dir)
    print(f"{result.action}: {result.detail}")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
