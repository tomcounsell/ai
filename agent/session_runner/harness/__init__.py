"""HarnessAdapter seam: turn-based headless CLI knowledge lives here.

See ``docs/features/harness-adapter.md`` and plan #2000 (Phase 2 of
``harness-cross-compat.md``, #1996). ``base.py`` defines the protocol and
normalized dataclasses; ``events.py`` defines the fixed normalized
``TurnEvent`` vocabulary; ``claude.py`` is the (today, only) concrete
adapter for the ``claude -p`` CLI.
"""

from __future__ import annotations
