"""Guard the deploy-window re-export shim at reflections/pm_audio_briefing.py.

Issue #1306 ships a single-file module that re-exports ``run`` from the
renamed ``reflections.pm_briefings`` package so the iCloud-synced vault
edit and the code rename can land in either order on each bridge machine.

This test ensures the shim does not silently rot — the two import paths
must resolve to the identical callable while the shim exists.
"""

import reflections.pm_audio_briefing as legacy
import reflections.pm_briefings as canonical


def test_run_callable_is_re_exported() -> None:
    assert legacy.run is canonical.run
