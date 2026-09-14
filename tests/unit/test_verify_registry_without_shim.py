"""Pin `scripts/verify_registry_without_shim.py` against the enumerations it mirrors.

The probe is the acceptance gate for issue #2875: `scripts/update/run.py` Step
4.65 runs it and suppresses the service restart when it fails. Its coverage is
only as good as `_registry_copies()`, a hand-maintained mirror of
`scripts/migrate_reflections_callables.py::default_targets()` (the set that gets
rewritten) plus level 4 of `agent.reflection_scheduler._resolve_registry_path()`
(the owning checkout's copy). Nothing else pins that mirror.

The failure mode these tests exist for: the mirror drifts, the probe passes over
a subset, and the copy the scheduler actually reads goes unchecked — a green
gate that greenlights a worker restart onto an unimportable registry.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name: str, rel: str):
    """Import a `scripts/` module by path — neither is an importable package.

    Yields the module and then removes it from `sys.modules`. The entry has to
    exist during `exec_module` (dataclasses and `typing` resolution look the
    module up by name), but leaving it behind pollutes every later test on the
    worker with a private name that shadows nothing today and could shadow
    something tomorrow.
    """
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def probe():
    yield from _load("_probe_under_test", "scripts/verify_registry_without_shim.py")


@pytest.fixture(scope="module")
def migrate():
    yield from _load("_migrate_under_test", "scripts/migrate_reflections_callables.py")


@pytest.fixture(autouse=True)
def _clean_registry_env(monkeypatch):
    """Both enumerations read os.environ; start every case from a known state."""
    monkeypatch.delenv("REFLECTIONS_YAML", raising=False)
    monkeypatch.delenv("VALOR_LAUNCHD", raising=False)


class TestRegistryCopiesMirrorsDefaultTargets:
    """`_registry_copies(None)` must equal `default_targets()` under every env shape.

    `owning_root=None` is the comparable case: level 4 is the probe's documented
    ADDITION to the migration's target set, so passing None isolates the mirror.
    """

    def test_clean_env(self, probe, migrate):
        assert probe._registry_copies(None) == migrate.default_targets()

    def test_explicit_override(self, probe, migrate, monkeypatch, tmp_path):
        override = tmp_path / "custom-reflections.yaml"
        monkeypatch.setenv("REFLECTIONS_YAML", str(override))
        copies = probe._registry_copies(None)
        assert copies == migrate.default_targets()
        assert override in copies, "REFLECTIONS_YAML must be probed, not just rewritten"

    def test_launchd_skips_the_vault(self, probe, migrate, monkeypatch):
        """Under launchd the vault is TCC-unreadable; both sides must skip it."""
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        copies = probe._registry_copies(None)
        assert copies == migrate.default_targets()
        vault = Path.home() / "Desktop" / "Valor" / "reflections.yaml"
        assert vault not in copies

    def test_launchd_and_override_together(self, probe, migrate, monkeypatch, tmp_path):
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        monkeypatch.setenv("REFLECTIONS_YAML", str(tmp_path / "r.yaml"))
        assert probe._registry_copies(None) == migrate.default_targets()

    def test_owning_root_adds_exactly_one_level(self, probe, monkeypatch, tmp_path):
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        base = probe._registry_copies(None)
        with_owner = probe._registry_copies(tmp_path)
        assert with_owner == base + [tmp_path / "config" / "reflections.yaml"]


class TestRegistryCopiesCoversTheScheduler:
    """The probe's set must contain whatever the *scheduler* would actually read.

    `default_targets()` is the set the migration REWRITES;
    `_resolve_registry_path()` is the single path the running scheduler LOADS.
    Pinning only against the former leaves the probe free to drift away from the
    latter — a future level 5 would be silently unprobed with every mirror test
    still green, which is the exact failure this file's docstring names.

    These assert the containment directly, by asking the real resolver what it
    picks on this machine and requiring the probe to have enumerated it. That
    also covers the resolver's exhausted-candidates fallback, which returns the
    level-3 path unchanged.
    """

    @staticmethod
    def _assert_covered(probe):
        from agent.reflection_scheduler import _owning_checkout_root, _resolve_registry_path

        resolved = _resolve_registry_path()
        copies = probe._registry_copies(_owning_checkout_root())
        assert resolved.expanduser() in copies, (
            f"the scheduler would load {resolved}, which the probe does not check; "
            f"probed set was {copies}"
        )

    def test_clean_env(self, probe):
        self._assert_covered(probe)

    def test_launchd(self, probe, monkeypatch):
        monkeypatch.setenv("VALOR_LAUNCHD", "1")
        self._assert_covered(probe)

    def test_explicit_override(self, probe, monkeypatch, tmp_path):
        override = _write(tmp_path / "r.yaml", "reflections: []\n")
        monkeypatch.setenv("REFLECTIONS_YAML", str(override))
        self._assert_covered(probe)

    def test_level_count_is_pinned(self, probe):
        """A level added to the resolver must be added to the probe too.

        The behavioral checks above only exercise whichever level wins on THIS
        machine. This one is structural: it counts the numbered levels in the
        resolver's own priority list, which is what a maintainer edits when
        adding one, and fails if the probe's documented count no longer matches.
        """
        import re

        from agent.reflection_scheduler import _resolve_registry_path

        levels = re.findall(r"^\s*(\d+)\. ", _resolve_registry_path.__doc__ or "", re.MULTILINE)
        assert levels == ["1", "2", "3", "4"], (
            "agent.reflection_scheduler._resolve_registry_path grew or lost a level; "
            "scripts/verify_registry_without_shim.py::_registry_copies mirrors levels 1-4 "
            "and must be updated in lockstep"
        )


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


class TestCheckCopy:
    """`_check_copy` returns a count, or None on any failure — never a sentinel int."""

    def test_counts_resolved_callables(self, probe, tmp_path):
        import yaml

        registry = _write(
            tmp_path / "r.yaml",
            "reflections:\n  - name: a\n    callable: os.getcwd\n"
            "  - name: b\n    callable: os.getpid\n",
        )
        assert probe._check_copy(registry, yaml, lambda dotted: object()) == 2

    def test_zero_callables_fails_closed(self, probe, tmp_path, capsys):
        """A registry with entries but no `callable:` keys proves nothing.

        Returning 0 here would print "OK: 0 callables resolved" and exit 0 — a
        silent pass, which is the exact shape the gate must not have.
        """
        import yaml

        registry = _write(tmp_path / "r.yaml", "reflections:\n  - name: a\n    enabled: true\n")
        assert probe._check_copy(registry, yaml, lambda dotted: object()) is None
        err = capsys.readouterr().err
        assert "FAIL" in err and "`callable:` entries" in err

    def test_unresolvable_callable_fails(self, probe, tmp_path):
        import yaml

        registry = _write(tmp_path / "r.yaml", "reflections:\n  - name: a\n    callable: x.y\n")

        def boom(dotted):
            raise ImportError("agent.sustainability is banned")

        assert probe._check_copy(registry, yaml, boom) is None

    def test_malformed_yaml_reports_instead_of_raising(self, probe, tmp_path, capsys):
        """A corrupt copy must fail in the same shape as the others (nit N2).

        Before the guard this raised out of `_check_copy`, aborting the whole
        run — so the verdict on every *subsequent* copy was never reported.
        """
        import yaml

        registry = _write(tmp_path / "r.yaml", "reflections: [oops\n  - broken: {{{\n")
        assert probe._check_copy(registry, yaml, lambda dotted: object()) is None
        assert "could not read registry" in capsys.readouterr().err

    def test_unreadable_file_reports_instead_of_raising(self, probe, tmp_path, capsys):
        """TCC denial / iCloud dataless placeholder shape: open() itself fails."""
        import yaml

        missing = tmp_path / "does-not-exist.yaml"
        assert probe._check_copy(missing, yaml, lambda dotted: object()) is None
        assert "could not read registry" in capsys.readouterr().err

    def test_non_mapping_registry_reports(self, probe, tmp_path, capsys):
        import yaml

        registry = _write(tmp_path / "r.yaml", "- just\n- a\n- list\n")
        assert probe._check_copy(registry, yaml, lambda dotted: object()) is None
        assert "expected a mapping" in capsys.readouterr().err


class TestMainExitCodes:
    """`main()` returns three distinct verdicts, and callers branch on all three.

    The zero-candidates case is the one this file most needs to pin: it is the
    only verdict whose *direction* was changed after the probe shipped, and the
    mirror tests above cannot see it — they assert
    `_registry_copies(None) == default_targets()`, which stays true no matter
    what `_owning_checkout_root()` returns, and that locator returning `None`
    (relative-paths worktrees, unfamiliar layouts) is exactly how a machine
    reaches zero candidates.
    """

    def test_no_registry_copy_exits_with_its_own_code(self, probe, monkeypatch, tmp_path, capsys):
        """Neither 0 nor 1: proving nothing is not passing, and is not failing."""
        monkeypatch.setattr(
            probe, "_registry_copies", lambda owning_root: [tmp_path / "absent.yaml"]
        )

        assert probe.main() == probe.EXIT_NO_REGISTRY
        assert probe.EXIT_NO_REGISTRY not in (0, 1)
        err = capsys.readouterr().err
        assert "no reflections registry found" in err
        assert "nothing was probed" in err

    def test_a_resolvable_copy_still_exits_zero(self, probe, monkeypatch, tmp_path, capsys):
        """Green control: the vacuous branch must not swallow a real pass."""
        registry = _write(
            tmp_path / "r.yaml", "reflections:\n  - name: a\n    callable: os.getcwd\n"
        )
        monkeypatch.setattr(probe, "_registry_copies", lambda owning_root: [registry])

        assert probe.main() == 0
        assert "1 callables resolved" in capsys.readouterr().out

    def test_an_unresolvable_copy_still_exits_one(self, probe, monkeypatch, tmp_path, capsys):
        """Red control: a real registry that cannot import is still a failure."""
        registry = _write(
            tmp_path / "r.yaml",
            "reflections:\n  - name: a\n    callable: agent.sustainability.circuit_health_gate\n",
        )
        monkeypatch.setattr(probe, "_registry_copies", lambda owning_root: [registry])

        assert probe.main() == 1
        assert "did not resolve" in capsys.readouterr().err

    def test_update_helper_mirrors_the_no_registry_exit_code(self, probe):
        """Two files, no shared constant — this assertion is what keeps them equal."""
        from scripts.update.reflections_callables import _PROBE_EXIT_NO_REGISTRY

        assert _PROBE_EXIT_NO_REGISTRY == probe.EXIT_NO_REGISTRY


class TestMainRestoresProcessGlobals:
    """`main()` arms a `sys.meta_path` ban; called in-process it must disarm it.

    `TestMainExitCodes` above calls `main()` three times inside the pytest
    process. A finder left armed raises `ImportError` for anything under
    `agent.sustainability` for the rest of that worker's life, which reddens
    `tests/unit/test_sustainability_namespace.py` with an error message claiming
    a registry callable still names a shim that no longer exists. Asserting the
    two lists directly is what keeps the `finally` in place; the observable
    symptom only appears under some file-scheduling orders, so it cannot be
    relied on to catch a regression.
    """

    def _assert_restored(self, probe, call):
        meta_before = list(sys.meta_path)
        path_before = list(sys.path)
        call()
        assert list(sys.meta_path) == meta_before
        assert list(sys.path) == path_before

    def test_globals_restored_on_the_vacuous_verdict(self, probe, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(
            probe, "_registry_copies", lambda owning_root: [tmp_path / "absent.yaml"]
        )
        self._assert_restored(probe, probe.main)
        capsys.readouterr()

    def test_globals_restored_on_a_pass(self, probe, monkeypatch, tmp_path, capsys):
        registry = _write(
            tmp_path / "r.yaml", "reflections:\n  - name: a\n    callable: os.getcwd\n"
        )
        monkeypatch.setattr(probe, "_registry_copies", lambda owning_root: [registry])
        self._assert_restored(probe, probe.main)
        capsys.readouterr()

    def test_the_sys_path_entry_is_removed_when_main_added_it(
        self, probe, monkeypatch, tmp_path, capsys
    ):
        """Force `repo_root_added` true, which it never is under pytest.

        The other cases in this class assert on `sys.path`, but that half is
        trivially satisfied there: pytest already has the repo root on the path,
        so `main()` never inserts it and never takes the removal branch.
        Stripping the entry first is what makes the branch execute — without
        this case, replacing the removal with `pass` leaves the class green.
        """
        monkeypatch.setattr(
            sys, "path", [p for p in sys.path if p != str(probe._REPO_ROOT)], raising=False
        )
        monkeypatch.setattr(
            probe, "_registry_copies", lambda owning_root: [tmp_path / "absent.yaml"]
        )

        self._assert_restored(probe, probe.main)
        assert str(probe._REPO_ROOT) not in sys.path
        capsys.readouterr()

    def test_globals_restored_on_a_failure(self, probe, monkeypatch, tmp_path, capsys):
        """The path that matters most: a raising probe must still disarm the ban."""
        registry = _write(
            tmp_path / "r.yaml",
            "reflections:\n  - name: a\n    callable: agent.sustainability.circuit_health_gate\n",
        )
        monkeypatch.setattr(probe, "_registry_copies", lambda owning_root: [registry])
        self._assert_restored(probe, probe.main)
        capsys.readouterr()

    def test_the_banned_module_is_importable_again_afterwards(
        self, probe, monkeypatch, tmp_path, capsys
    ):
        """The symptom itself, not just the mechanism.

        After `main()`, `find_spec` on the banned name must answer rather than
        raise. The shim is deleted, so the answer is `None` — the point is that
        asking is no longer an error.
        """
        monkeypatch.setattr(
            probe, "_registry_copies", lambda owning_root: [tmp_path / "absent.yaml"]
        )
        probe.main()
        capsys.readouterr()

        assert importlib.util.find_spec(probe.BANNED_MODULE) is None
