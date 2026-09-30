"""What an action module is. Plan 07 tasks 5 to 7.

`perform` and `reconcile_dangling` know nothing about git or Gmail. They know
that every `action_type` has a module with four names, and `broker/actions.py`
maps one to the other. The protocol lives here rather than in `actions.py`
because `actions.py` imports every module and a module needs `TargetState`
for its own signature.

`TargetState` is the question reconcile asks after a kill: the intent row says
an effect was about to happen, and only the target knows whether it did.
`present` means the key is there in the state the payload named, `differs`
means it is there in another state, which is the case a person reads rather
than a re-run (blind-spot finding 15).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

__all__ = ["ActionModule", "TargetState"]

TargetState = Literal["present", "absent", "differs", "unreachable"]


class ActionModule(Protocol):
    """The four names `perform` and `reconcile_dangling` call."""

    model: type
    """The action model, so reconcile can rebuild one from a `payload` row."""

    def check(self, action) -> None:
        """Refuse the action with `EffectRefused` before any intent is
        written. Everything a module can know without touching the target."""

    async def run(self, conn, action, credential) -> dict[str, Any]:
        """Do it. The returned dict lands in the closing row's `result`."""

    async def query(self, conn, action, credential) -> TargetState:
        """What the target says about this action's key, right now."""
