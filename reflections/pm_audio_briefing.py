"""Compat re-export shim for issue #1306 deploy window.

The Python package previously living at ``reflections/pm_audio_briefing/`` is
now ``reflections/pm_briefings/``. The vault ``reflections.yaml`` callable
field is being updated in lockstep, but iCloud propagates the vault edit
across machines independently of the code rename. This shim makes the
``callable: reflections.pm_audio_briefing.run`` registry entry resolve in the
in-between state, regardless of which side of the rename a given machine
sits on.

Remove this file in a follow-up PR after every active bridge machine has
pulled the rename AND the vault has propagated (≥1 day window).

A unit test (``tests/unit/reflections/test_pm_audio_briefing_reexport_shim.py``)
asserts that ``reflections.pm_audio_briefing.run is reflections.pm_briefings.run``
so this shim cannot silently rot.
"""

from __future__ import annotations

from reflections.pm_briefings import *  # noqa: F401,F403
from reflections.pm_briefings import run  # noqa: F401  -- explicit for grep
