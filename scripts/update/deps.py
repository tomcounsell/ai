"""Dependency management for update system."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path


class PinDeclarationError(RuntimeError):
    """A dependency declaration could not be resolved unambiguously.

    Raised instead of silently returning a wrong-but-plausible answer or
    silently no-opping. Both failure modes produced the 2026-08-24 half-bump
    incident: a reader that scraped a version out of a comment, and a writer
    whose regex could not match an extras pin and reported ``False`` into an
    error list that ``auto_bump_deps`` recorded and then continued past.
    """


@dataclass
class DepSyncResult:
    """Result of dependency sync operation."""

    success: bool
    method: str  # "uv", "pip", or "skipped"
    output: str
    error: str | None = None
    # True iff `markitdown` was NOT importable in the project venv before
    # this sync AND IS importable after. Used by scripts/update/run.py to
    # append a one-time valor-ingest --scan backfill reminder to the
    # Telegram summary on the run that actually installs the [knowledge]
    # extra (per plan C6). Probing the venv (rather than diffing uv.lock)
    # survives the run.py ordering — by the time we sync, git pull has
    # already updated uv.lock so the lockfile diff is always empty.
    # Not set on pip/skipped paths — those are fallback code paths that
    # don't own the venv state machine.
    backfill_reminder_needed: bool = False


@dataclass
class VersionInfo:
    """Installed version of a package."""

    package: str
    version: str | None
    expected: str | None = None
    matches: bool = True


def run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def has_uv() -> bool:
    """Check if uv is available."""
    return shutil.which("uv") is not None


def install_uv() -> bool:
    """Install uv package manager. Returns True if successful."""
    try:
        result = subprocess.run(
            ["curl", "-LsSf", "https://astral.sh/uv/install.sh"],
            capture_output=True,
            text=True,
            check=True,
        )
        install_result = subprocess.run(
            ["sh"],
            input=result.stdout,
            capture_output=True,
            text=True,
            check=True,
        )
        return install_result.returncode == 0
    except Exception:
        return False


def _markitdown_importable(project_dir: Path) -> bool:
    """Return True if `import markitdown` succeeds inside the project venv.

    Probes actual environment state, not lockfile artifacts — the lockfile
    is rewritten by `git pull` before sync_with_uv runs, so a lockfile diff
    can never see a first-time install. We invoke the venv's python
    explicitly (not `sys.executable`) so this works correctly when the
    update script itself is launched from the system python.

    Returns False when no venv python exists yet (fresh clone, pre-sync).
    """
    python_path = project_dir / ".venv" / "bin" / "python"
    if not python_path.exists():
        return False
    try:
        result = run_cmd(
            [str(python_path), "-c", "import markitdown"],
            cwd=project_dir,
            check=False,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def sync_with_uv(project_dir: Path, reinstall: bool = False, frozen: bool = True) -> DepSyncResult:
    """Sync dependencies using uv.

    `frozen=True` (default) installs strictly from `uv.lock` without
    re-resolving — this is what every machine should do during routine
    `/update` so the lockfile stays byte-stable across the fleet. Only
    `auto_bump_deps` (which has just edited `pyproject.toml`) passes
    `frozen=False` to regenerate the lock.
    """
    had_markitdown_before = _markitdown_importable(project_dir)

    cmd = ["uv", "sync", "--all-extras"]
    if frozen:
        cmd.append("--frozen")
    if reinstall:
        cmd.append("--reinstall")

    try:
        result = run_cmd(cmd, cwd=project_dir, timeout=600)

        # Also install in editable mode
        run_cmd(["uv", "pip", "install", "-e", "."], cwd=project_dir)

        has_markitdown_after = _markitdown_importable(project_dir)

        return DepSyncResult(
            success=True,
            method="uv",
            output=result.stdout + result.stderr,
            backfill_reminder_needed=(not had_markitdown_before and has_markitdown_after),
        )
    except subprocess.CalledProcessError as e:
        return DepSyncResult(
            success=False,
            method="uv",
            output=e.stdout + e.stderr if e.stdout else "",
            error=str(e),
        )
    except subprocess.TimeoutExpired:
        return DepSyncResult(
            success=False,
            method="uv",
            output="",
            error="Timeout: uv sync took longer than 10 minutes",
        )


def sync_with_pip(project_dir: Path) -> DepSyncResult:
    """Sync dependencies using pip (fallback)."""
    pip_path = project_dir / ".venv" / "bin" / "pip"

    if not pip_path.exists():
        return DepSyncResult(
            success=False,
            method="pip",
            output="",
            error="No pip found at .venv/bin/pip",
        )

    try:
        result = run_cmd(
            [str(pip_path), "install", "-e", str(project_dir)],
            cwd=project_dir,
            timeout=600,
        )
        return DepSyncResult(
            success=True,
            method="pip",
            output=result.stdout + result.stderr,
        )
    except subprocess.CalledProcessError as e:
        return DepSyncResult(
            success=False,
            method="pip",
            output=e.stdout + e.stderr if e.stdout else "",
            error=str(e),
        )
    except subprocess.TimeoutExpired:
        return DepSyncResult(
            success=False,
            method="pip",
            output="",
            error="Timeout: pip install took longer than 10 minutes",
        )


def sync_dependencies(
    project_dir: Path, reinstall: bool = False, frozen: bool = True
) -> DepSyncResult:
    """
    Sync dependencies using best available method.

    Prefers uv, falls back to pip. `frozen` is forwarded to uv (pip has no
    lockfile concept here, so it's ignored on the pip path).
    """
    if has_uv():
        return sync_with_uv(project_dir, reinstall=reinstall, frozen=frozen)

    # Try to install uv
    if install_uv() and has_uv():
        return sync_with_uv(project_dir, reinstall=reinstall, frozen=frozen)

    # Fall back to pip
    return sync_with_pip(project_dir)


def get_installed_version(project_dir: Path, package: str) -> str | None:
    """Get installed version of a package."""
    python_path = project_dir / ".venv" / "bin" / "python"

    if not python_path.exists():
        return None

    # Map package names to import names
    import_map = {
        "claude-agent-sdk": "claude_agent_sdk",
    }
    import_name = import_map.get(package, package)

    try:
        result = run_cmd(
            [
                str(python_path),
                "-c",
                f"import {import_name}; print({import_name}.__version__)",
            ],
            cwd=project_dir,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Declaration-aware pin helpers
# ---------------------------------------------------------------------------
#
# A `pyproject.toml` dependency line is NOT a substring haystack. The real file
# carries all three shapes that broke the naive helpers (#3001 spike-2):
#
#     "pydantic-ai-slim[anthropic]==2.9.0", # ... avoids the openai/... extras
#     "anthropic==0.125.0",
#     "openai>=1.0.0", # Embedding API ...
#
#   1. `openai` occurs inside the FIRST line's trailing comment, so a substring
#      scan for `openai` on a line containing `==` returns `2.9.0`. The real
#      `openai` declaration is a floor with no `==` at all.
#   2. `anthropic` also occurs in that first line, as an EXTRA. A line-ordered
#      scan resolves the wrong declaration; today it happens to be right only
#      because of where the lines sit.
#   3. The writer's `"{package}==[^"]*"` cannot match an extras pin, so it
#      no-ops and reports failure into a list `auto_bump_deps` records and then
#      continues past — the half-bump this lane exists to prevent.
#
# So: strip comments, extract whole quoted requirement strings, parse each into
# (normalized name, extras, specifier), and match on the NAME. Refuse loudly on
# ambiguity. Deliberately regex-on-text rather than tomlkit/tomllib — the writer
# must preserve the CRITICAL comments verbatim (see plan Rabbit Holes).

# A PEP 508 requirement's leading name, optional extras, and the rest.
_REQUIREMENT_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?P<extras>\[[^\]]*\])?"
    r"\s*(?P<spec>.*?)\s*$"
)
# An exact pin, and only an exact pin: `==1.2.3`. A compound specifier
# (`==1.2.3,<2`) or an environment marker is not one, and must not be
# mistaken for one by either helper.
_EXACT_PIN_RE = re.compile(r"^==\s*(?P<version>[^,\s;]+)$")


def _normalize_package(name: str) -> str:
    """PEP 503 normalization so `Claude_Agent.SDK` matches `claude-agent-sdk`."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _strip_comment(line: str) -> str:
    """Drop the trailing `#` comment, ignoring `#` inside a quoted string."""
    in_string = False
    for i, char in enumerate(line):
        if char == '"':
            in_string = not in_string
        elif char == "#" and not in_string:
            return line[:i]
    return line


@dataclass
class _Declaration:
    """One parsed dependency declaration and where its text lives."""

    package: str  # normalized name
    requirement: str  # the full quoted body, e.g. pydantic-ai-slim[anthropic]==2.9.0
    extras: str  # "[anthropic]" or ""
    specifier: str  # "==2.9.0", ">=1.0.0", ""
    line_index: int

    @property
    def pinned_version(self) -> str | None:
        """The exact-pinned version, or None for a floor/range/unconstrained."""
        match = _EXACT_PIN_RE.match(self.specifier)
        return match.group("version") if match else None


def _find_declarations(content: str, package: str) -> list[_Declaration]:
    """Every declaration of `package` in a pyproject.toml body, comment-blind."""
    wanted = _normalize_package(package)
    found: list[_Declaration] = []

    for line_index, line in enumerate(content.split("\n")):
        for requirement in re.findall(r'"([^"]*)"', _strip_comment(line)):
            match = _REQUIREMENT_RE.match(requirement)
            if not match or _normalize_package(match.group("name")) != wanted:
                continue
            found.append(
                _Declaration(
                    package=wanted,
                    requirement=requirement,
                    extras=match.group("extras") or "",
                    specifier=match.group("spec"),
                    line_index=line_index,
                )
            )

    return found


def _resolve_declaration(content: str, package: str, source: Path) -> _Declaration | None:
    """The single declaration of `package`, or None if absent.

    Raises `PinDeclarationError` when the file declares it more than once —
    a rewrite would then have to guess which one the coupled set means.
    """
    declarations = _find_declarations(content, package)
    if not declarations:
        return None
    if len(declarations) > 1:
        raise PinDeclarationError(
            f"{source}: {package!r} is declared {len(declarations)} times "
            f"({[d.requirement for d in declarations]}); refusing to guess "
            "which declaration to read or rewrite"
        )
    return declarations[0]


def get_pinned_version(project_dir: Path, package: str) -> str | None:
    """The exact-pinned version of `package` in pyproject.toml, if it has one.

    Comment-blind and extras-tolerant: matches the declaration's own name,
    never text inside a neighbouring line's comment or extras marker.

    Returns None when the package is not declared at all, or is declared
    without an exact `==` pin (a floor such as `openai>=1.0.0` is not a pin,
    and reporting one would invent a version nobody wrote).

    Raises `PinDeclarationError` on duplicate declarations.
    """
    pyproject = project_dir / "pyproject.toml"

    if not pyproject.exists():
        return None

    declaration = _resolve_declaration(pyproject.read_text(), package, pyproject)
    return declaration.pinned_version if declaration else None


def verify_critical_versions(project_dir: Path) -> list[VersionInfo]:
    """Verify critical dependency versions match pins."""
    critical_deps = ["telethon", "anthropic", "claude-agent-sdk"]
    results = []

    for dep in critical_deps:
        installed = get_installed_version(project_dir, dep)
        expected = get_pinned_version(project_dir, dep)

        matches = True
        if installed and expected:
            matches = installed == expected
        elif expected and not installed:
            matches = False

        results.append(
            VersionInfo(
                package=dep,
                version=installed,
                expected=expected,
                matches=matches,
            )
        )

    return results


def check_dep_files_changed(changed_files: list[str]) -> bool:
    """Check if dependency files are in the changed files list."""
    dep_files = {"pyproject.toml", "uv.lock", "requirements.txt"}
    return bool(dep_files & set(changed_files))


# ---------------------------------------------------------------------------
# PyPI version checking and auto-bump
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoupledSet:
    """Packages that move together or not at all.

    A set is the atomic unit of every stage of an auto-bump: resolve,
    rewrite, sync, gate, and rollback. Record-an-error-and-continue over
    individual packages is precisely how one member stayed behind while the
    others moved and killed the whole LLM layer twice on 2026-08-24.

    ``gates`` defaults to ``("import", "pytest")`` so a newly declared set
    never silently inherits the billed ``llm`` phase; a set that needs a
    real API call must ask for it by name.

    ``import_names`` are the set's *own* importable module names — the
    ``import`` phase imports these, so the gate always probes what the set
    actually moved rather than a hardcoded package list.

    ``hold`` parks a set in the declaration without executing it: the bump
    is skipped and the reason is recorded, which is how a member can be
    described here (and gated by ``/update``'s verify leg) while a human
    still owns the decision to move it.
    """

    members: Sequence[str]
    import_names: tuple[str, ...]
    reason: str
    gates: tuple[str, ...] = ("import", "pytest")
    hold: str | None = None


AUTO_BUMP_SETS: list[CoupledSet] = [
    CoupledSet(
        members=["anthropic", "pydantic-ai-slim"],
        import_names=("anthropic", "pydantic_ai"),
        gates=("llm", "import", "pytest"),
        reason=(
            "anthropic 1.0.0 removed temperature/top_p/top_k from the Messages "
            "API and pydantic_ai/models/anthropic.py forwards all three "
            "unconditionally, so a partial bump kills every non-harness LLM "
            "call at argument binding — before any network I/O. An import "
            "check cannot see it (`import anthropic` succeeds fine on a "
            "version whose call signature we cannot satisfy), which is why "
            "this set carries the `llm` phase."
        ),
        hold="#3001 Step 2",
    ),
    CoupledSet(
        members=["claude-agent-sdk"],
        import_names=("claude_agent_sdk",),
        reason=(
            "The headless session runner's transport. It moves alone — no "
            "other pinned package's API is coupled to it — so an import "
            "check plus the fast unit-test file is the whole gate."
        ),
    ),
]

# `openai` is in NO coupled set, and this assertion is the enforcement.
#
# spike-5 established there is no packaging coupling: `pydantic-ai-slim`'s
# locked dependencies contain no `openai` (it appears only under the
# `[openai]` extra, which this repo does not install). The ImportError that
# looked like coupling was self-inflicted — a module-scope
# `from pydantic_ai.models.openai import OpenAIChatModel` in
# `agent/llm/wrapper.py` — and it is fixed at the import, not by widening a
# set. Its declaration is also a floor (`openai>=1.0.0`), not an exact pin,
# so an auto-bump could not rewrite it without inventing a pin nobody chose.
assert "openai" not in {member for s in AUTO_BUMP_SETS for member in s.members}, (
    "`openai` must not be a coupled-set member (spike-5): it has no packaging "
    "coupling to the anthropic stack and is declared as a floor, not a pin."
)


@dataclass
class BumpResult:
    """Result of a single package version bump."""

    package: str
    old_version: str | None
    new_version: str | None
    bumped: bool
    error: str | None = None


@dataclass
class AutoBumpResult:
    """Result of auto-bumping all critical deps."""

    bumps: list[BumpResult] = field(default_factory=list)
    synced: bool = False
    sync_error: str | None = None
    smoke_passed: bool = False
    smoke_output: str = ""
    rolled_back: bool = False
    # The gate phase (or "sync") that failed and triggered the rollback, so
    # "the LLM pair is incompatible" reads differently from "an unrelated
    # unit test is flaky" in the /update warning.
    failed_phase: str | None = None
    # A rollback whose own re-sync failed. The environment is NOT back to
    # its pre-bump state, so `run.py` must not commit anything this run —
    # a later successful bump would otherwise push a poisoned lockfile
    # fleet-wide.
    restore_failed: bool = False

    @property
    def any_bumped(self) -> bool:
        return any(b.bumped for b in self.bumps)


def get_pypi_latest(package: str, timeout: int = 10) -> str | None:
    """Fetch the latest version of a package from PyPI.

    Tries ``pip index versions`` first (works regardless of SSL config),
    falls back to the PyPI JSON API.
    """
    # Method 1: pip index versions (most reliable)
    try:
        result = run_cmd(
            ["pip", "index", "versions", package],
            check=False,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout:
            # Output like: "anthropic (0.84.0)\nAvailable versions: ..."
            first_line = result.stdout.strip().split("\n")[0]
            if "(" in first_line and ")" in first_line:
                return first_line.split("(")[1].split(")")[0]
    except Exception:
        pass

    # Method 2: PyPI JSON API
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("info", {}).get("version")
    except Exception:
        return None


def bump_pin_in_pyproject(project_dir: Path, package: str, new_version: str) -> bool:
    """Rewrite `package`'s exact pin in pyproject.toml to `new_version`.

    Extras-tolerant: `pydantic-ai-slim[anthropic]==2.9.0` is rewritten in
    place with its extras marker and trailing comment intact. Only the
    version portion of the one matched declaration changes.

    Returns True on a successful rewrite. Never returns False — every refusal
    raises `PinDeclarationError`, because a silent no-op reported as a
    recorded-and-continued error is precisely how one member of a coupled set
    stayed behind while the others moved.

    Raises `PinDeclarationError` when pyproject.toml is missing, when the
    package is not declared, when it is declared more than once, or when its
    declaration is a floor/range rather than an exact pin (rewriting
    `openai>=1.0.0` would invent a pin the maintainer never chose).
    """
    pyproject = project_dir / "pyproject.toml"
    if not pyproject.exists():
        raise PinDeclarationError(f"{pyproject}: no pyproject.toml to rewrite")

    content = pyproject.read_text()
    declaration = _resolve_declaration(content, package, pyproject)
    if declaration is None:
        raise PinDeclarationError(f"{pyproject}: {package!r} has no dependency declaration to bump")
    if declaration.pinned_version is None:
        raise PinDeclarationError(
            f"{pyproject}: {package!r} is declared as {declaration.requirement!r}, "
            f"not an exact `==` pin; refusing to invent a pin at {new_version}"
        )

    lines = content.split("\n")
    old_text = f'"{declaration.requirement}"'
    new_text = f'"{package}{declaration.extras}=={new_version}"'
    line = lines[declaration.line_index]
    if line.count(old_text) != 1:
        raise PinDeclarationError(
            f"{pyproject}: {old_text} is not uniquely locatable on line "
            f"{declaration.line_index + 1}; refusing an ambiguous rewrite"
        )
    lines[declaration.line_index] = line.replace(old_text, new_text)

    pyproject.write_text("\n".join(lines))
    return True


# ---------------------------------------------------------------------------
# Gate phases
# ---------------------------------------------------------------------------

# The one fast test file every set runs as its `pytest` phase.
GATE_PYTEST_TARGET = "tests/unit/test_docs_auditor_substrate.py"

# Per-phase subprocess bounds. `llm` is the only phase that makes a network
# call, so it gets the widest window; `import` is a bare import and `pytest`
# is a single fast file.
_GATE_TIMEOUTS = {"llm": 120, "import": 30, "pytest": 60}


def llm_gate_argv(venv_python: Path) -> list[str]:
    """The argv of the `llm` gate phase — the ONE construction of it.

    Both the phase runner below and the manual two-leg rollback verification
    call this helper, so a hand-run invocation can never drift from the
    production one while still appearing to prove the gate works.

    `venv_python` must be the TARGET venv's interpreter
    (`{project_dir}/.venv/bin/python`), never the update process's own: that
    interpreter imported its modules before the sync and would report on a
    stack that no longer exists on disk.
    """
    return [str(venv_python), "-m", "agent.llm.compat", "--json", "--allow-network"]


def _gate_argv(project_dir: Path, phase: str, coupled_set: CoupledSet) -> list[str]:
    """Argv for one gate phase of one set."""
    python_path = project_dir / ".venv" / "bin" / "python"

    if phase == "llm":
        return llm_gate_argv(python_path)
    if phase == "import":
        # Set-derived, never a hardcoded package list: the gate probes what
        # this set actually moved.
        program = "; ".join(f"import {name}" for name in coupled_set.import_names)
        return [str(python_path), "-c", program]
    if phase == "pytest":
        return [str(python_path), "-m", "pytest", GATE_PYTEST_TARGET, "-x", "-q"]
    raise ValueError(f"unknown gate phase {phase!r}")


def run_gate_phases(project_dir: Path, coupled_set: CoupledSet) -> tuple[bool, str | None, str]:
    """Run every gate phase `coupled_set` declares, in order.

    Returns ``(passed, failed_phase, output)``. Stops at the first failing
    phase and names it, so the rollback warning can distinguish an
    incompatible LLM pair from a flaky unrelated unit test.

    Fail-closed throughout: a phase that cannot be run at all (no venv,
    timeout, OSError) is a failed phase, never a skipped one.
    """
    if not coupled_set.gates:
        return True, None, "no gate phases declared"

    python_path = project_dir / ".venv" / "bin" / "python"
    if not python_path.exists():
        return False, coupled_set.gates[0], "No Python venv found at .venv/bin/python"

    passed_phases: list[str] = []
    for phase in coupled_set.gates:
        try:
            proc = run_cmd(
                _gate_argv(project_dir, phase, coupled_set),
                cwd=project_dir,
                check=False,
                timeout=_GATE_TIMEOUTS.get(phase, 60),
            )
        except subprocess.TimeoutExpired:
            return False, phase, f"{phase} gate timed out"
        except OSError as exc:
            return False, phase, f"{phase} gate could not run: {exc}"

        if proc.returncode != 0:
            detail = (proc.stdout + proc.stderr).strip()
            return False, phase, f"{phase} gate failed:\n{detail}"
        passed_phases.append(phase)

    return True, None, f"gates passed: {', '.join(passed_phases)}"


# ---------------------------------------------------------------------------
# Auto-bump
# ---------------------------------------------------------------------------


def _safe_pinned(project_dir: Path, package: str) -> str | None:
    """`get_pinned_version` for logging contexts where a refusal is not fatal."""
    try:
        return get_pinned_version(project_dir, package)
    except PinDeclarationError:
        return None


def _record_set(
    result: AutoBumpResult,
    coupled_set: CoupledSet,
    current: dict[str, str | None],
    latest: dict[str, str | None],
    *,
    bumped: set[str] | None = None,
    error: str | None = None,
) -> None:
    """Append one `BumpResult` per member, so `run.py`'s per-package log stays legible."""
    for member in coupled_set.members:
        result.bumps.append(
            BumpResult(
                package=member,
                old_version=current.get(member),
                new_version=latest.get(member),
                bumped=bool(bumped and member in bumped),
                error=error,
            )
        )


def _restore_set(project_dir: Path, snapshot: str, result: AutoBumpResult, resync: bool) -> None:
    """Put this set's pins back, and record it loudly if that fails.

    The snapshot is PER-SET, taken immediately before the set's own rewrite.
    A whole-file snapshot taken once before the loop would revert every
    other set's good bump on one bad set (spike-4).
    """
    (project_dir / "pyproject.toml").write_text(snapshot)
    if not resync:
        return

    restore_sync = sync_dependencies(project_dir, frozen=False)
    if restore_sync.success:
        return

    # The environment is NOT back to its pre-bump state. Say so — a
    # swallowed restore failure is how a poisoned lockfile ships fleet-wide.
    result.restore_failed = True
    try:
        run_cmd(["git", "checkout", "--", "uv.lock"], cwd=project_dir, check=False, timeout=60)
    except (subprocess.SubprocessError, OSError):
        pass


def _bump_coupled_set(project_dir: Path, coupled_set: CoupledSet, result: AutoBumpResult) -> None:
    """Resolve, rewrite, sync, and gate one coupled set — all or nothing."""
    pyproject = project_dir / "pyproject.toml"
    current: dict[str, str | None] = {}
    latest: dict[str, str | None] = {}

    if coupled_set.hold:
        for member in coupled_set.members:
            current[member] = _safe_pinned(project_dir, member)
        _record_set(result, coupled_set, current, latest, error=f"held: {coupled_set.hold}")
        return

    # Resolve EVERY member before touching the file. One unresolvable
    # member skips the whole set — a half-resolved set is a half-bump.
    blocker: str | None = None
    for member in coupled_set.members:
        try:
            current[member] = get_pinned_version(project_dir, member)
        except PinDeclarationError as exc:
            current[member] = None
            blocker = blocker or str(exc)
            continue
        latest[member] = get_pypi_latest(member)
        if blocker is None and (current[member] is None or latest[member] is None):
            blocker = f"could not determine current or latest version for {member!r}"

    if blocker:
        _record_set(result, coupled_set, current, latest, error=f"set skipped: {blocker}")
        return

    changed = {m: latest[m] for m in coupled_set.members if current[m] != latest[m]}
    if not changed:
        # Nothing moved — no rewrite and, critically, no sync. A quiet cycle
        # must not re-resolve the lockfile.
        _record_set(result, coupled_set, current, latest)
        return

    snapshot = pyproject.read_text() if pyproject.exists() else ""

    try:
        for member, new_version in changed.items():
            bump_pin_in_pyproject(project_dir, member, new_version)
    except PinDeclarationError as exc:
        # No sync has happened yet, so restoring the file is the whole undo.
        _restore_set(project_dir, snapshot, result, resync=False)
        _record_set(
            result,
            coupled_set,
            current,
            latest,
            error=f"set abandoned: could not rewrite pyproject.toml: {exc}",
        )
        return

    # `frozen=False` because we just edited pyproject.toml and need uv to
    # re-resolve and rewrite uv.lock.
    sync_result = sync_dependencies(project_dir, frozen=False)
    result.synced = result.synced or sync_result.success
    if not sync_result.success:
        result.sync_error = sync_result.error
        result.rolled_back = True
        result.failed_phase = result.failed_phase or "sync"
        _restore_set(project_dir, snapshot, result, resync=True)
        _record_set(
            result,
            coupled_set,
            current,
            latest,
            error=f"set rolled back: sync failed: {sync_result.error}",
        )
        return

    passed, failed_phase, output = run_gate_phases(project_dir, coupled_set)
    result.smoke_output = (
        f"{result.smoke_output}\n{output}".strip() if result.smoke_output else output
    )

    if not passed:
        result.rolled_back = True
        result.failed_phase = result.failed_phase or failed_phase
        _restore_set(project_dir, snapshot, result, resync=True)
        _record_set(
            result,
            coupled_set,
            current,
            latest,
            error=f"set rolled back: {failed_phase} gate failed",
        )
        return

    result.smoke_passed = True
    _record_set(result, coupled_set, current, latest, bumped=set(changed))


def auto_bump_deps(project_dir: Path) -> AutoBumpResult:
    """Bump every coupled set that has moved on PyPI, one set at a time.

    Each set is resolved, rewritten, synced, and gated as a unit; a failure
    at any stage restores that set's own snapshot and moves to the next set.
    A member is reported `bumped=True` only once its whole set has survived
    every gate, so `run.py`'s commit list never names a pin that was rolled
    back underneath it.
    """
    result = AutoBumpResult()
    for coupled_set in AUTO_BUMP_SETS:
        _bump_coupled_set(project_dir, coupled_set, result)
    return result
