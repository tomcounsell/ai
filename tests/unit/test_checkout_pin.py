"""tools/checkout_pin.py (#3141): a bare ``python scripts/<tool>.py`` imports
the checkout the script lives in, even through another checkout's venv.

The decision tests drive ``pin()`` with explicit ``argv``/``path`` lists and
never touch the running interpreter. The end-to-end test builds two fake
checkouts plus a fake site directory and runs a real subprocess through a
bootstrap interpreter isolated from the venv's own ambient pin (see
``_run_probe``), with the pin's ``.pth`` present and absent, so the mechanism
is proved at the moment it matters (``site`` processing) rather than by
inspection.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scripts.update.redis_flush_guard_pth import (
    _PIN_PTH_CONTENT,
    _PIN_PTH_FILENAME,
    _PIN_SHIM_FILENAME,
    _PIN_SOURCE_PATH,
)
from tools import checkout_pin

PYPROJECT = 'name = "not-a-table"\n[project]\nname = "valor-bridge"\n'


def _checkout(root: Path, *, linked: bool = False, name: str = "valor-bridge") -> Path:
    """A fake checkout: ``.git`` (file for a linked worktree), pyproject, scripts/."""
    root.mkdir(parents=True)
    if linked:
        (root / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    else:
        (root / ".git").mkdir()
    (root / "pyproject.toml").write_text(PYPROJECT.replace("valor-bridge", name))
    (root / "scripts").mkdir()
    (root / "scripts" / "probe.py").write_text("print('probe')\n")
    return root


@pytest.fixture
def checkouts(tmp_path):
    primary = _checkout(tmp_path / "primary")
    worktree = _checkout(tmp_path / "primary" / ".claude" / "worktrees" / "lane", linked=True)
    return primary, worktree


class TestPinDecision:
    def test_worktree_script_through_primary_venv_pins_worktree(self, checkouts):
        primary, worktree = checkouts
        path = ["/stdlib", "/site-packages", str(primary)]
        script = str(worktree / "scripts" / "probe.py")

        assert checkout_pin.pin([script], path) == str(worktree.resolve())
        assert path[0] == str(worktree.resolve())
        assert path[-1] == str(primary)

    def test_script_in_the_venvs_own_checkout_is_a_noop(self, checkouts):
        primary, _ = checkouts
        path = ["/stdlib", "/site-packages", str(primary)]

        assert checkout_pin.pin([str(primary / "scripts" / "probe.py")], path) is None
        assert path == ["/stdlib", "/site-packages", str(primary)]

    def test_already_pinned_by_pythonpath_is_a_noop(self, checkouts):
        primary, worktree = checkouts
        path = [str(worktree), "/stdlib", str(primary)]

        assert checkout_pin.pin([str(worktree / "scripts" / "probe.py")], path) is None
        assert path == [str(worktree), "/stdlib", str(primary)]

    @pytest.mark.parametrize("argv", [[], [""], ["-c"], ["-m", "--help"], ["-"]])
    def test_non_script_invocations_are_noops(self, argv, checkouts):
        primary, _ = checkouts
        path = ["/stdlib", str(primary)]

        assert checkout_pin.pin(argv, path) is None
        assert path == ["/stdlib", str(primary)]

    def test_script_outside_any_checkout_is_a_noop(self, tmp_path, checkouts):
        primary, _ = checkouts
        loose = tmp_path / "loose.py"
        loose.write_text("pass\n")
        path = ["/stdlib", str(primary)]

        assert checkout_pin.pin([str(loose)], path) is None
        assert path == ["/stdlib", str(primary)]

    def test_foreign_repository_is_a_noop(self, tmp_path, checkouts):
        primary, _ = checkouts
        foreign = _checkout(tmp_path / "other-project", name="other-project")
        path = ["/stdlib", str(primary)]

        assert checkout_pin.pin([str(foreign / "scripts" / "probe.py")], path) is None
        assert path == ["/stdlib", str(primary)]

    def test_foreign_repo_nested_in_a_checkout_stops_at_the_nearest_git(self, checkouts):
        primary, _ = checkouts
        nested = _checkout(primary / "vendor" / "dep", name="dep")
        path = ["/stdlib", str(primary)]

        assert checkout_pin.pin([str(nested / "scripts" / "probe.py")], path) is None

    def test_relative_script_path_resolves_against_cwd(self, checkouts, monkeypatch):
        primary, worktree = checkouts
        monkeypatch.chdir(worktree)
        path = ["/stdlib", str(primary)]

        assert checkout_pin.pin(["scripts/probe.py"], path) == str(worktree.resolve())

    def test_console_script_inside_a_venv_resolves_to_the_venvs_checkout(self, checkouts):
        """``.venv/bin/valor-*`` lives under the checkout that owns the venv,
        so it keeps importing the code it was installed from."""
        primary, _ = checkouts
        bin_dir = primary / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        entry = bin_dir / "valor-tts"
        entry.write_text("#!python\n")
        path = ["/stdlib", str(primary)]

        assert checkout_pin.pin([str(entry)], path) is None

    def test_never_raises(self, monkeypatch):
        def _boom(_path):
            raise RuntimeError("filesystem on fire")

        monkeypatch.setattr(checkout_pin, "checkout_root_of", _boom)
        path = ["/stdlib"]
        assert checkout_pin.pin([__file__], path) is None
        assert path == ["/stdlib"]


class TestDeclaresProject:
    def test_reads_the_project_table_not_a_top_level_name(self, tmp_path):
        root = tmp_path / "c"
        root.mkdir()
        (root / "pyproject.toml").write_text('name = "valor-bridge"\n[project]\nname = "x"\n')
        assert checkout_pin.declares_project(str(root)) is False

    def test_unparseable_pyproject_is_false(self, tmp_path):
        root = tmp_path / "c"
        root.mkdir()
        (root / "pyproject.toml").write_text("[project\nname = \n")
        assert checkout_pin.declares_project(str(root)) is False

    def test_this_repo_declares_itself(self):
        assert checkout_pin.declares_project(str(Path(__file__).resolve().parents[2])) is True


_BOOTSTRAP = textwrap.dedent(
    """
    import os
    import runpy
    import site
    import sys

    target, site_dir = sys.argv[1:3]
    sys.argv = [target]
    site.addsitedir(site_dir)
    sys.path.insert(0, os.path.dirname(os.path.abspath(target)))
    runpy.run_path(target, run_name="__main__")
    """
)


def _run_probe(site_dir: Path, script: Path, *, pinned: bool) -> str:
    """Run ``script`` through a bootstrap interpreter isolated from the venv
    that runs this test suite.

    The venv running this suite ships ``_valor_checkout_pin.pth`` in its own
    ``site-packages`` -- the production copy of the mechanism under test,
    installed fleet-wide by ``scripts/update/redis_flush_guard_pth.py`` since
    #3141. An ordinary ``[sys.executable, script]`` invocation processes that
    ambient ``.pth`` during ``site`` startup before this test's own fake site
    dir gets a say, so the "without the pin" run stops being a control at all
    (#3201): it measures the ambient shim instead of the fixture.

    The bootstrap script (``_BOOTSTRAP``) is what disarms the ambient shim:
    the child's ``argv[0]`` becomes the bootstrap itself, a path outside any
    checkout, so ``checkout_pin.checkout_root_of`` returns ``None`` and the
    ambient ``pin()`` no-ops even though its ``.pth`` still runs.
    ``sys.argv = [target]`` must happen *before* ``site.addsitedir`` processes
    any ``.pth``, so the fake site dir's own pin (when present) sees the real
    target rather than the bootstrap's path. ``-S`` additionally skips the
    venv's ``site-packages`` outright -- hermeticity against a *future*
    ambient shim that does not consult ``argv[0]`` (an unconditional
    ``sys.path`` append, a venv ``sitecustomize``); it is not what fixes
    today's bug. ``-P`` keeps the bootstrap's own directory off the child's
    ``sys.path`` so the probe's search path matches what real CPython startup
    produces (spikes 2b/2c/3 in the #3201 plan).
    """
    pth = site_dir / _PIN_PTH_FILENAME
    if pinned:
        pth.write_text(_PIN_PTH_CONTENT)
    elif pth.exists():
        pth.unlink()

    boot = site_dir.parent / "_boot.py"
    boot.write_text(_BOOTSTRAP)

    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PYTHONNOUSERSITE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-S", "-P", str(boot), str(script), str(site_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class TestEndToEnd:
    def test_worktree_script_imports_worktree_package_only_with_the_pin(self, tmp_path):
        """Two checkouts, each with an ``agentx`` package saying which one it
        is. The fake site dir carries an editable-style path line naming the
        primary (what the venv's ``.pth`` does) and a shim that imports
        ``agentx`` at site time (what the flush-guard shim does to ``tools``).
        Without the pin the worktree script sees the primary's package; with
        it, the worktree's, and the early import sees the worktree's too."""
        primary = _checkout(tmp_path / "primary")
        worktree = _checkout(tmp_path / "primary" / ".claude" / "worktrees" / "lane", linked=True)
        for root, label in ((primary, "primary"), (worktree, "worktree")):
            (root / "agentx").mkdir()
            (root / "agentx" / "__init__.py").write_text(f"WHICH = {label!r}\n")
        script = worktree / "scripts" / "probe.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import sys
                import agentx
                print(
                    agentx.WHICH,
                    sys.modules["_early_probe"].SEEN,
                    str({str(worktree.resolve())!r} in sys.path),
                )
                """
            )
        )

        site_dir = tmp_path / "site"
        site_dir.mkdir()
        (site_dir / _PIN_SHIM_FILENAME).write_text(_PIN_SOURCE_PATH.read_text())
        (site_dir / "_editable_impl_fake.pth").write_text(f"{primary}\n")
        (site_dir / "_early_probe.py").write_text("import agentx\nSEEN = agentx.WHICH\n")
        (site_dir / "zzz_early_probe.pth").write_text("import _early_probe\n")

        assert _run_probe(site_dir, script, pinned=False) == "primary primary False"
        assert _run_probe(site_dir, script, pinned=True) == "worktree worktree True"

    def test_primary_script_is_unaffected_by_the_pin(self, tmp_path):
        primary = _checkout(tmp_path / "primary")
        (primary / "agentx").mkdir()
        (primary / "agentx" / "__init__.py").write_text("WHICH = 'primary'\n")
        script = primary / "scripts" / "probe.py"
        script.write_text("import agentx, sys\nprint(agentx.WHICH, sys.path)\n")

        site_dir = tmp_path / "site"
        site_dir.mkdir()
        (site_dir / _PIN_SHIM_FILENAME).write_text(_PIN_SOURCE_PATH.read_text())
        (site_dir / "_editable_impl_fake.pth").write_text(f"{primary}\n")

        assert _run_probe(site_dir, script, pinned=False) == _run_probe(
            site_dir, script, pinned=True
        )
