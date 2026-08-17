"""Update-system step: ensure code-shipped reflections are registered in the vault.

:func:`register_reflection` is the general entry point (subtask 3a of #2004):
any reflection whose callable ships in the repo can be appended to the vault
registry through the same guarded, idempotent, comment-preserving machinery.
Current registrations:

- ``crash-recovery`` (:func:`register_crash_recovery`, issue #1917)
- ``memory-distill-backfill`` (:func:`register_memory_distill_backfill`, #2202)
  -- distills provisional human-ingest Memory records; see
  ``reflections/memory/memory_distill_backfill.py`` and
  ``docs/plans/memory-distilled-ingest.md``. Registered here for the same
  reason as the one above: ``config/reflections.yaml`` is gitignored and a
  hand-edit to it (or even to the main repo's checked-out copy) never ships
  via git history and is clobbered by the next vault->config sync -- only a
  committed code path that writes the vault file makes the registration real.

History -- issue #1917 (the dominant gap). ``reflections/crash_recovery.py`` ships a
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
``agent.reflection_scheduler._resolve_registry_path()`` (critique C6), which in
the primary checkout -- where this module always runs, via the ``/update`` step
-- picks the vault ahead of the config copy: that resolver's fourth,
owning-checkout-fallback level (issue #2734) is only reached when the local
config copy is absent, and in the primary checkout it is present. A builder who
hardcoded the config copy would reproduce #1539's "looks wired, never lands"
failure; see ``_resolve_target``'s docstring for the worktree-caller caveat this
qualification exists to flag. This step runs BEFORE Step 1.66's vault->config
copy (critique NIT) so the appended entry propagates into the per-machine
``config/reflections.yaml`` on the same cycle.

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
import re
from dataclasses import dataclass
from pathlib import Path

OWNING_PROJECT_KEY = "valor"

CRASH_RECOVERY_NAME = "crash-recovery"
CRASH_RECOVERY_CALLABLE = "reflections.crash_recovery.run_crash_recovery"

# Reflections whose callables have been deleted from the repo. Each name is
# removed from the vault registry on /update (the reflection counterpart of
# hardlinks.py's RENAMED_REMOVALS) so no machine keeps scheduling an entry
# whose callable no longer imports. Safe to prune a name from this tuple once
# every machine has run an /update that removed it.
REMOVED_REFLECTIONS = (
    "test-baseline-refresh",  # merge-gate baseline ecosystem deleted (#2376)
)

MEMORY_DISTILL_BACKFILL_NAME = "memory-distill-backfill"
MEMORY_DISTILL_BACKFILL_CALLABLE = "reflections.memory_management.run_memory_distill_backfill"

UPVOTE_PICKUP_NAME = "sdlc-upvote-pickup"
UPVOTE_PICKUP_CALLABLE = "reflections.sdlc_upvote_lanes.run_sdlc_upvote_lanes"

# Matches the leading whitespace of an existing ``reflections:`` list item so the
# appended entry adopts the file's own indentation (the hand-authored registry
# uses a 2-space dash; a yaml.safe_dump copy uses a column-0 dash).
_LIST_ITEM_RE = re.compile(r"^(\s*)-\s")


def _detect_dash_indent(text: str) -> str:
    """Return the leading whitespace of the first block-sequence item in ``text``.

    Falls back to a 2-space indent (the hand-authored registry's convention)
    when the list is empty or no item is found.
    """
    for line in text.splitlines():
        m = _LIST_ITEM_RE.match(line)
        if m:
            return m.group(1)
    return "  "


def _build_entry_block(
    dash_indent: str,
    *,
    name: str,
    callable_path: str,
    description: str,
    priority: str,
    cadence: str | None = None,
    cron: str | None = None,
    cron_tz: str | None = None,
    timeout: int | None = None,
) -> str:
    """Render one reflection entry as text at the file's own indentation.

    Kept as a text block (not a yaml.safe_dump) so the surrounding hand-authored
    registry keeps its header docs and inline comments -- a dump round-trip would
    strip them. Exactly one of ``cadence`` / ``cron`` must be supplied by the
    caller (:func:`register_reflection` enforces this before calling here);
    ``cadence`` is the ``every:`` duration body (e.g. ``300s``, ``7d`` -- see
    ``agent.reflection_schedule.parse_every_duration``), ``cron`` is a raw
    5-field cron expression rendered with a following ``cron_tz:`` line when
    set (see ``agent.reflection_schedule._next_cron``). ``timeout:`` is
    emitted only when ``timeout is not None`` -- omitting it preserves every
    existing caller's block byte-for-byte.
    """
    field = dash_indent + "  "
    lines = [
        f"\n{dash_indent}- name: {name}\n",
        f'{field}description: "{description}"\n',
    ]
    if cadence is not None:
        lines.append(f"{field}every: {cadence}\n")
    else:
        lines.append(f"{field}cron: {cron}\n")
        if cron_tz is not None:
            lines.append(f"{field}cron_tz: {cron_tz}\n")
    lines.append(f"{field}priority: {priority}\n")
    lines.append(f"{field}execution_type: function\n")
    lines.append(f'{field}callable: "{callable_path}"\n')
    if timeout is not None:
        lines.append(f"{field}timeout: {timeout}\n")
    lines.append(f"{field}enabled: true\n")
    return "".join(lines)


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
    same resolver the scheduler reads at runtime. That resolver gained a
    fourth fallback level (issue #2734): when *this* checkout's
    ``config/reflections.yaml`` is absent, it reads the owning checkout's copy
    instead of the vault. C6 -- "the entry lands where the scheduler will
    actually look, not in the soon-clobbered config copy" -- still holds for
    every real caller of this function, but only because of a reachability
    condition, not because the resolver always prefers the vault: the fourth
    level is reached only when this checkout's local ``config/reflections.yaml``
    is absent, which never holds in the **primary checkout**, where ``/update``
    (and therefore this function) runs. There the vault branch is hit first,
    exactly as C6 assumed.

    That reachability condition is caller-specific, not resolver-enforced. A
    hypothetical caller of this function running from a **worktree** under
    ``VALOR_LAUNCHD=1`` -- where the local config copy is genuinely absent --
    would have this function resolve to the *primary checkout's* install-time
    ``config/reflections.yaml`` and write the registration there. That copy is
    live scheduler input, so the write would silently succeed, but the next
    ``/update`` overwrites it from the vault and the registration is lost with
    no error. See Risk 3 in
    ``docs/plans/reflection-registry-schedule-contract.md`` for why this is a
    contained CONCERN rather than a blocker: no such caller exists today,
    because this module runs only as an ``/update`` step in the primary
    checkout. That is the whole of the containment, and it is sufficient under
    every real invocation path.

    ``_this_machine_owns_valor`` is **not** a second, independent leg. It fails
    closed in a worktree only because ``config/projects.json`` is gitignored and
    therefore absent there -- and ``scripts/update/run.py`` calls
    ``env_sync.sync_projects_json(project_dir)`` at Step 1.65, which runs *before*
    the registration steps, so a ``run.py --project-dir <worktree>`` invocation
    would materialize that file and arm the ownership guard within the same run.
    Treat the primary-checkout leg as the containment; do not relax it on the
    assumption that ownership would catch a worktree caller. The containment must
    not be relaxed without first splitting this shared resolver into distinct read
    and write functions (the ``[ORDERED]`` No-Go in that plan; a separate,
    sequenced change, not done here).

    Imported lazily because the scheduler transitively imports heavy models;
    the update step only needs it at call time.
    """
    from agent.reflection_scheduler import _resolve_registry_path

    return _resolve_registry_path()


def _this_machine_owns_valor(project_dir: Path) -> bool:
    """True iff config/projects.json's 'valor' project.machine == this host.

    Fails closed (returns False) on any missing file/field so a misconfigured
    or partial checkout never mutates the shared vault file.
    """
    from config.machine import get_machine_name

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

    machine = get_machine_name()
    if not machine:
        return False

    return owner.strip().lower() == machine.strip().lower()


def _has_entry(text: str, name: str) -> bool:
    """True iff a reflection named ``name`` already exists in ``text``.

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
    return any(isinstance(e, dict) and e.get("name") == name for e in entries)


def _append_entry(
    path: Path,
    *,
    name: str,
    callable_path: str,
    description: str,
    priority: str,
    cadence: str | None = None,
    cron: str | None = None,
    cron_tz: str | None = None,
    timeout: int | None = None,
) -> str:
    """Append the named reflection entry to the registry at ``path``.

    Mirrors :func:`_build_entry_block`'s signature (see there for the
    cadence/cron/cron_tz/timeout contract) and forwards it unchanged.

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

    if _has_entry(text, name):
        return "present"

    new_text = text
    if not new_text.endswith("\n"):
        new_text += "\n"
    new_text += _build_entry_block(
        _detect_dash_indent(text),
        name=name,
        callable_path=callable_path,
        description=description,
        priority=priority,
        cadence=cadence,
        cron=cron,
        cron_tz=cron_tz,
        timeout=timeout,
    )

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
        (e for e in entries if isinstance(e, dict) and e.get("name") == name),
        None,
    )
    if match is None or match.get("callable") != callable_path:
        return "invalid"

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(new_text)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        return "io-error"
    return "appended"


def _remove_entry(path: Path, name: str) -> str:
    """Remove the reflection entry named ``name`` from the registry at ``path``.

    Text-based block removal (mirroring :func:`_append_entry`'s text-based
    append) so the surrounding hand-authored registry keeps its header docs and
    comments. The block runs from the entry's ``- name:`` line to the next list
    item or top-level key. Returns a verdict, never raises:
      "absent"     -- no such entry (no write)
      "removed"    -- file rewritten without the entry, re-parse validated
      "not-found"  -- file absent
      "invalid"    -- post-removal YAML failed to parse or still had the entry
      "io-error"   -- read or atomic-write failure
    """
    if not path.exists():
        return "not-found"
    try:
        text = path.read_text()
    except Exception:
        return "io-error"

    if not _has_entry(text, name):
        return "absent"

    # Anchor on the entry's ``name:`` field (with or without the leading dash —
    # a yaml.safe_dump'd registry sorts keys, so ``name`` may be mid-block),
    # then walk back to the block's ``- `` line and forward to the next list
    # item or top-level key.
    name_re = re.compile(rf"^\s*(-\s+)?name:\s*[\"']?{re.escape(name)}[\"']?\s*$")
    lines = text.splitlines(keepends=True)
    anchor = next((i for i, line in enumerate(lines) if name_re.match(line)), None)
    if anchor is None:
        # _has_entry saw it via YAML but the text shape is unexpected (e.g.
        # flow-style entry); do not guess at a text surgery.
        return "invalid"
    start = anchor
    while start >= 0 and not _LIST_ITEM_RE.match(lines[start]):
        if lines[start].strip() and not lines[start][0].isspace():
            return "invalid"  # hit a top-level key before the block's dash
        start -= 1
    if start < 0:
        return "invalid"
    end = anchor + 1
    while end < len(lines):
        line = lines[end]
        if _LIST_ITEM_RE.match(line) or (line.strip() and not line[0].isspace()):
            break
        end += 1
    # Absorb one preceding blank line so removals don't accumulate gaps.
    if start > 0 and not lines[start - 1].strip():
        start -= 1
    new_text = "".join(lines[:start] + lines[end:])

    import yaml

    try:
        data = yaml.safe_load(new_text)
    except Exception:
        return "invalid"
    if not isinstance(data, dict):
        return "invalid"
    if _has_entry(new_text, name):
        return "invalid"

    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(new_text)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        return "io-error"
    return "removed"


def remove_reflection(project_dir: Path, *, name: str) -> RegisterResult:
    """Ensure the named reflection is absent from the vault registry.

    The counterpart of :func:`register_reflection` for reflections whose
    callables have been deleted from the repo (see ``REMOVED_REFLECTIONS``).
    Same guards: vault file present, this machine owns the ``valor`` project.
    Idempotent -- a no-op when no entry with ``name`` exists.
    """
    vault_path = _vault_reflections_path()
    if not vault_path.exists():
        return RegisterResult(True, "skipped", f"vault reflections.yaml not found at {vault_path}")

    if not _this_machine_owns_valor(project_dir):
        return RegisterResult(True, "skipped", "this machine does not own the 'valor' project")

    try:
        target = _resolve_target()
    except Exception:  # pragma: no cover - defensive
        target = vault_path

    verdict = _remove_entry(target, name)
    if verdict == "absent":
        return RegisterResult(True, "noop", f"{name} not present")
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
            f"removal failed re-parse at {target}; file left untouched",
        )
    if verdict == "io-error":
        return RegisterResult(
            False,
            "error",
            f"could not write registry at {target}; will retry next /update",
        )

    # Best-effort: also remove from the in-repo copy so the entry stops being
    # scheduled even if Step 1.66's vault->config copy is skipped this cycle.
    repo_copy = project_dir / "config" / "reflections.yaml"
    if repo_copy.exists() and repo_copy != target:
        _remove_entry(repo_copy, name)

    return RegisterResult(True, "removed", f"{name} removed from {target}")


def register_reflection(
    project_dir: Path,
    *,
    name: str,
    callable_path: str,
    description: str,
    priority: str,
    cadence: str | None = None,
    cron: str | None = None,
    cron_tz: str | None = None,
    timeout: int | None = None,
) -> RegisterResult:
    """Ensure the named reflection is registered in the vault registry.

    See the module docstring for the guard conditions and why the vault file
    (not the config copy) is written. Idempotent -- a no-op when an entry with
    ``name`` is already present.

    Exactly one of ``cadence`` / ``cron`` must be supplied -- ``ValueError``
    otherwise, so a caller cannot accidentally emit an entry with neither
    scheduling mechanism (which ``load_registry`` would silently skip as
    invalid -- the Risk 1 failure class this whole module exists to avoid).
    ``cron_tz`` is only meaningful alongside ``cron``. ``timeout`` is
    additive and optional for either scheduling mechanism.
    """
    if bool(cadence) == bool(cron):
        raise ValueError(
            f"register_reflection({name!r}): exactly one of cadence/cron must be "
            f"supplied (got cadence={cadence!r}, cron={cron!r})"
        )

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

    entry_kwargs = {
        "name": name,
        "callable_path": callable_path,
        "description": description,
        "priority": priority,
        "cadence": cadence,
        "cron": cron,
        "cron_tz": cron_tz,
        "timeout": timeout,
    }
    verdict = _append_entry(target, **entry_kwargs)
    if verdict == "present":
        return RegisterResult(True, "noop", f"{name} already registered")
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
        _append_entry(repo_copy, **entry_kwargs)

    return RegisterResult(True, "registered", f"{name} appended to {target}")


def register_crash_recovery(project_dir: Path) -> RegisterResult:
    """Ensure the ``crash-recovery`` reflection is registered (issue #1917).

    Thin wrapper over :func:`register_reflection` -- same guards, same target
    resolution, same idempotence.
    """
    return register_reflection(
        project_dir,
        name=CRASH_RECOVERY_NAME,
        callable_path=CRASH_RECOVERY_CALLABLE,
        description="Fingerprint crashes, warm signatures, auto-resume tool-wedge deaths (#1917)",
        cadence="300s",
        priority="normal",
    )


def register_memory_distill_backfill(project_dir: Path) -> RegisterResult:
    """Ensure the ``memory-distill-backfill`` reflection is registered (#2202).

    The callable (``reflections/memory/memory_distill_backfill.py``, re-exported
    as ``reflections.memory_management.run_memory_distill_backfill``) ships with
    the memory-distilled-ingest feature but, per this module's docstring, a
    hand-edited ``config/reflections.yaml`` never actually ships it -- that file
    is gitignored and clobbered from the vault on every ``/update``. 300s
    cadence, normal priority: matches ``session-liveness-check``'s interval so
    freshly-ingested provisional records are distilled within a few minutes.
    """
    return register_reflection(
        project_dir,
        name=MEMORY_DISTILL_BACKFILL_NAME,
        callable_path=MEMORY_DISTILL_BACKFILL_CALLABLE,
        description=(
            "Distill provisional human-ingest Memory records into standalone "
            "facts with content-derived importance (#2202)"
        ),
        cadence="300s",
        priority="normal",
    )


def register_sdlc_upvote_pickup(project_dir: Path) -> RegisterResult:
    """Ensure the ``sdlc-upvote-pickup`` reflection is registered (#2717).

    Thin wrapper over :func:`register_reflection` -- same guards, same target
    resolution, same idempotence. Registration is a **code change, not a
    config edit**: ``config/reflections.yaml`` is gitignored and clobbered
    from the vault on every ``/update`` (see this module's docstring), so a
    hand-added entry never ships. This is the first cron-scheduled entry in
    the registry (every prior entry uses ``every:``); the explicit
    ``timeout:`` is imported from ``reflections.sdlc_upvote_lanes`` rather
    than repeated as a literal, so the registered value and the module's own
    budget arithmetic cannot drift apart.
    """
    from reflections.sdlc_upvote_lanes import UPVOTE_ENTRY_TIMEOUT_S

    return register_reflection(
        project_dir,
        name=UPVOTE_PICKUP_NAME,
        callable_path=UPVOTE_PICKUP_CALLABLE,
        description=(
            "Start half of autonomous SDLC: pick the oldest open `upvote`-labeled "
            "issue per project, announce it in that project's `Eng:` Telegram "
            "group, and create an Eng session anchored to the announcement. "
            "Derives start/skip from artifacts only -- no claim key, no label "
            "mutation. Set SDLC_UPVOTE_PICKUP_ENABLED=false to disable without "
            "editing config."
        ),
        cron="0 6-22/2 * * *",
        cron_tz="America/Los_Angeles",
        priority="low",
        timeout=UPVOTE_ENTRY_TIMEOUT_S,
    )


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent.parent
    exit_code = 0
    for register in (
        register_crash_recovery,
        register_memory_distill_backfill,
        register_sdlc_upvote_pickup,
    ):
        result = register(project_dir)
        print(f"{result.action}: {result.detail}")
        if not result.success:
            exit_code = 1
    for name in REMOVED_REFLECTIONS:
        result = remove_reflection(project_dir, name=name)
        print(f"{result.action}: {result.detail}")
        if not result.success:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
