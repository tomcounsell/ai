"""Re-export shim test (issue #1306).

The single-file ``reflections/pm_audio_briefing.py`` module re-exports the
canonical ``run`` callable from ``reflections.pm_briefings`` so that vault
``reflections.yaml`` and code rename can land in any order on each bridge
machine without ImportError. Once every machine has pulled the rename and
the vault edit propagates (≥1 day), the shim is removed in a follow-up PR.

This test guards the shim so it cannot silently rot: it asserts both that
the shim resolves and that the re-exported ``run`` is identity-equal to the
canonical one (not just importable -- the SAME function object).
"""

from __future__ import annotations


def test_pm_audio_briefing_module_is_a_file_not_a_package():
    """The shim must be a single-file module (the directory is renamed)."""
    import reflections.pm_audio_briefing as shim

    # A package has __path__; a single-file module does not.
    assert not hasattr(shim, "__path__"), (
        "reflections.pm_audio_briefing must be a single-file module re-export "
        "shim, not a package directory"
    )


def test_run_callable_is_re_exported_identity_equal():
    """``shim.run`` must BE ``reflections.pm_briefings.run`` (same object)."""
    import reflections.pm_audio_briefing as shim
    import reflections.pm_briefings as canonical

    assert shim.run is canonical.run, (
        "reflections.pm_audio_briefing.run must be identity-equal to "
        "reflections.pm_briefings.run -- the shim is a re-export, not a copy"
    )


def test_dotted_callable_path_resolves_for_scheduler():
    """Vault ``callable: reflections.pm_audio_briefing.run`` must resolve.

    The reflection scheduler resolves the callable via importlib + getattr;
    this test mirrors that resolution path so the deploy-window race is
    explicitly covered.
    """
    import importlib

    module = importlib.import_module("reflections.pm_audio_briefing")
    fn = getattr(module, "run")
    assert callable(fn)

    # And the canonical path must resolve to the same object.
    canonical_module = importlib.import_module("reflections.pm_briefings")
    assert fn is canonical_module.run
