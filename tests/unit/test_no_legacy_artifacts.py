"""Guard: deleted shim modules and removed symbols stay gone.

The #2872 / #2873 / #2874 cleanup batch deleted compatibility shim modules and
removed a set of deprecated attributes from ``AgentSession`` and
``SessionType``; the related #2875 rename deleted one more shim, whose row
arrived later for the reason recorded at the end of this docstring. Two sibling
tests already assert those names are absent from the *live object graph*
(``test_agent_session_legacy_surface.py`` and ``test_enums.py``). Neither
notices a deleted module *file* reappearing, and neither notices a brand-new
module defining one of the removed names itself. This guard closes that gap at
the source-text and file-index level.

Two hard constraints, both learned the expensive way. Do not relax either
without reading the issue named next to it.

1. **Tracked content only.** Every check here shells out to git so that it sees
   the index and tracked worktree files and nothing else. It must never walk
   the filesystem recursively: compiled bytecode caches embed their module's
   string literals verbatim, so a stale cache sitting next to a source file
   produces a phantom match that is unreproducible in a fresh checkout. That is
   the documented root cause of #2807.

2. **Exemptions are keyed by repo-relative file path, never by position within
   a file.** A guard in this repo once carried a positional exemption list;
   unrelated merges shifted the file around and the exemptions silently stopped
   applying to the sites they were written for, surviving in form while doing
   nothing. See #2805.

A third rule falls out of how git reports a clean result: an empty match set is
signalled by an exit status, not by a printed count, so nothing here compares a
printed tally against zero. The helpers below branch on the exit status and
treat any status other than "matched" or "clean" as a hard failure — a broken
invocation must never read as "nothing found".

A fourth rule: every git subprocess call below carries a bounded timeout. A
wedged git invocation (index-lock contention, a stalled filesystem) must
surface as a named failure, not hang the run indefinitely, so a timeout is
never caught here — it propagates.

Deliberately not guarded here by name matching: three of the removed
``AgentSession`` aliases are ordinary English words or strict substrings of an
already-listed name, so a fixed-string search for them has an unbounded
false-positive surface. They stay covered by the runtime attribute assertions
in ``test_agent_session_legacy_surface.py``, which is the right layer for them.

The last module row arrived after the rest, via #3008. Its shim was still on the
default branch when this guard shipped, so a row naming it then would have
turned that branch red for reasons the guard's own lane did not own. The
deletion has since landed. That row's exemption set is by far the longest here,
and deliberately so: the rename it belonged to left behind a migration script,
an update-time probe, a standalone verifier, and their tests, every one of which
has to keep recognizing the pre-rename import path in order to repair registry
copies still in the field.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: This module necessarily spells every banned string in the tables below, so it
#: appears on every row's exemption set. That self-reference is the only one;
#: everywhere else the prose paraphrases, so no comment edit can widen what the
#: guard tolerates.
GUARD_FILE = "tests/unit/test_no_legacy_artifacts.py"


class BannedModule(NamedTuple):
    """A deleted module: its import path, its file path, and who may name it."""

    dotted_path: str
    file_path: str
    allowed: frozenset[str]


class BannedSymbol(NamedTuple):
    """A removed symbol name and the tracked files allowed to mention it."""

    symbol: str
    allowed: frozenset[str]


BANNED_MODULES: tuple[BannedModule, ...] = (
    # Only the bridge-side copy was deleted. There is a live, separate module of
    # the same base name under agent/ that several production modules import, so
    # the banned pattern is the fully-qualified dotted path and never the bare
    # module name.
    BannedModule(
        dotted_path="bridge.session_logs",
        file_path="bridge/session_logs.py",
        allowed=frozenset({GUARD_FILE}),
    ),
    BannedModule(
        dotted_path="models.reflections",
        file_path="models/reflections.py",
        allowed=frozenset({GUARD_FILE}),
    ),
    # Deleted by #2875, the last step of moving the self-healing reflections out
    # from under agent/ and into their own package. The rename left a migration
    # script, an update-time probe, a standalone verifier, and their tests
    # behind, and all of them must keep recognizing the pre-rename import path
    # to repair registry copies still in the field. Stripping the old path out
    # of those files would disarm the self-heal, so they are exempted by path
    # rather than edited. That makes this the longest exemption set in the
    # table; the length is the migration's shape, not accumulated laxity.
    #
    # reflections/redis_access.py is deliberately absent below. Its docstring
    # names the deleted module's *file* path only, never the import path this
    # row matches, so it is not an offender and needs no exception.
    BannedModule(
        dotted_path="agent.sustainability",
        file_path="agent/sustainability.py",
        allowed=frozenset(
            {
                GUARD_FILE,
                "scripts/migrate_reflections_callables.py",
                "scripts/update/reflections_callables.py",
                "scripts/update/run.py",
                "scripts/verify_registry_without_shim.py",
                "tests/unit/test_migrate_reflections_callables.py",
                "tests/unit/test_reflection_scheduler.py",
                "tests/unit/test_sustainability_namespace.py",
                "tests/unit/test_update_reflections_callables.py",
                "tests/unit/test_verify_registry_without_shim.py",
            }
        ),
    ),
)


BANNED_SYMBOLS: tuple[BannedSymbol, ...] = (
    BannedSymbol(
        symbol="get_parent_chat_session",
        allowed=frozenset({GUARD_FILE, "tests/unit/test_agent_session_legacy_surface.py"}),
    ),
    BannedSymbol(
        symbol="get_dev_sessions",
        allowed=frozenset({GUARD_FILE, "tests/unit/test_agent_session_legacy_surface.py"}),
    ),
    BannedSymbol(
        symbol="_get_history_list",
        allowed=frozenset(
            {
                GUARD_FILE,
                "tests/unit/test_agent_session_legacy_surface.py",
                "tests/unit/test_session_event_formatting.py",
            }
        ),
    ),
    BannedSymbol(
        symbol="SessionType.GRANITE",
        allowed=frozenset({GUARD_FILE}),
    ),
    BannedSymbol(
        symbol="stale_granite_env_keys",
        allowed=frozenset(
            {
                GUARD_FILE,
                "scripts/scan_module_scope_env.py",
                "tests/unit/test_enums.py",
            }
        ),
    ),
)


def _tracked_python_matches(pattern: str) -> set[str]:
    """Repo-relative tracked ``.py`` files containing ``pattern`` as a literal.

    Searches tracked content only — never a recursive filesystem walk, which
    would match stale compiled-bytecode caches (#2807).
    """
    result = subprocess.run(
        ["git", "grep", "-l", "-F", pattern, "--", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode == 0:
        return set(result.stdout.strip().splitlines())
    if result.returncode == 1:
        # Clean: no tracked Python names the pattern.
        return set()
    raise RuntimeError(
        f"git search for {pattern!r} failed with exit status "
        f"{result.returncode}; the guard cannot report a clean result from a "
        f"broken invocation. git said: {result.stderr.strip()!r}"
    )


def _is_tracked(file_path: str) -> bool:
    """Whether ``file_path`` is present in the git index."""
    result = subprocess.run(
        ["git", "ls-files", "--", file_path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git index lookup for {file_path!r} failed with exit status "
            f"{result.returncode}; the guard cannot report a clean result from "
            f"a broken invocation. git said: {result.stderr.strip()!r}"
        )
    return bool(result.stdout.strip())


def _remedies(artifact: str, table_name: str) -> str:
    """Render the two-remedy message shared by the content-search-backed checks."""
    return (
        f"Two legitimate remedies, and only these two:\n"
        f"  1. Remove the reference to {artifact!r}.\n"
        f"  2. If the reference is genuinely warranted, add the offending "
        f"repo-relative file path to that row's exemption set in "
        f"{GUARD_FILE} ({table_name}), in the same pull request. "
        f"Paths only — never a position within a file (#2805)."
    )


@pytest.mark.parametrize(
    "row",
    BANNED_MODULES,
    ids=[row.dotted_path for row in BANNED_MODULES],
)
def test_deleted_module_file_absent(row: BannedModule):
    """A deleted shim module must not reappear in the git index.

    Catches a reintroduced file that nothing imports yet — the case the two
    runtime attribute guards cannot see at all.
    """
    if _is_tracked(row.file_path):
        raise AssertionError(
            f"Deleted module file {row.file_path!r} is tracked again.\n"
            f"It was deleted by a deliberate cleanup and must stay removed; "
            f"this guard's rows and docstring in {GUARD_FILE} record which "
            f"cleanup retired which module.\n"
            f"Remedy: delete the file. If it is genuinely a new module that "
            f"happens to share the path, this guard's row in {GUARD_FILE} "
            f"must be revisited deliberately, in the same pull request."
        )


@pytest.mark.parametrize(
    "row",
    BANNED_MODULES,
    ids=[row.dotted_path for row in BANNED_MODULES],
)
def test_deleted_module_not_imported(row: BannedModule):
    """No tracked Python may name a deleted module's import path.

    Catches a reintroduced caller. Matched as a fully-qualified fixed string so
    a live module sharing the base name is never collateral damage.
    """
    offenders = _tracked_python_matches(row.dotted_path) - row.allowed
    if offenders:
        listed = "\n".join(f"  - {path}" for path in sorted(offenders))
        raise AssertionError(
            f"Deleted module import path {row.dotted_path!r} is referenced by "
            f"tracked Python:\n{listed}\n" + _remedies(row.dotted_path, "BANNED_MODULES")
        )


@pytest.mark.parametrize(
    "row",
    BANNED_MODULES,
    ids=[row.dotted_path for row in BANNED_MODULES],
)
def test_banned_module_file_path_matches_dotted_path(row: BannedModule):
    """A row's file path must be the mechanical translation of its import path.

    ``_is_tracked`` reports a path absent from the git index as a clean
    result — that's also what it reports for a path that never existed at
    all. A row whose ``file_path`` was mistyped would make
    ``test_deleted_module_file_absent`` permanently green with no signal:
    the check would be querying a path that was never the real one. This
    assertion keeps the two fields locked together so a future row can't
    drift that way silently.

    This assumes a single-file module: the translation is dots-to-slashes
    plus a ``.py`` suffix. A future row for a deleted *package* (an
    ``__init__.py`` under a directory) would not satisfy this shape, and the
    failure message below would misdiagnose that case. If that ever happens,
    this assertion needs to be relaxed deliberately for that row, not
    silently.
    """
    expected = row.dotted_path.replace(".", "/") + ".py"
    if row.file_path != expected:
        raise AssertionError(
            f"BANNED_MODULES row {row.dotted_path!r} has file_path {row.file_path!r}, "
            f"but the mechanical translation of its import path is {expected!r}. "
            f"A mismatch here means the file-absence check queries the wrong path "
            f"and would stay green even if the real file reappeared."
        )


@pytest.mark.parametrize(
    "row",
    BANNED_SYMBOLS,
    ids=[row.symbol for row in BANNED_SYMBOLS],
)
def test_removed_symbol_absent(row: BannedSymbol):
    """No tracked Python may name a removed symbol outside its exemption set."""
    offenders = _tracked_python_matches(row.symbol) - row.allowed
    if offenders:
        listed = "\n".join(f"  - {path}" for path in sorted(offenders))
        raise AssertionError(
            f"Removed symbol {row.symbol!r} is referenced by tracked Python:\n"
            f"{listed}\n" + _remedies(row.symbol, "BANNED_SYMBOLS")
        )
