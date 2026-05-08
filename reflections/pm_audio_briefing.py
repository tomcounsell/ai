"""Compat re-export shim for issue #1306 deploy window.

Removed in a follow-up PR after every bridge machine has pulled this commit
AND the iCloud-synced vault has propagated the ``callable:`` field rename.

While the shim is in place, both ``reflections.pm_audio_briefing.run`` and
``reflections.pm_briefings.run`` resolve to the same callable, so the vault
edit and code rename can land in either order on a given machine.
"""

from reflections.pm_briefings import *  # noqa: F401,F403
from reflections.pm_briefings import run  # noqa: F401
