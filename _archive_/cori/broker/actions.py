"""The registry: one action type, one module. Plan 07 task 7; tech stack §7.

"One module per action" is the tech stack's line, and this file is the only
place that knows which is which. `perform` and `reconcile_dangling` look an
`action_type` up here and call the four names of `broker/module.py`; neither
of them imports git or Gmail, so a new action is a new module and a new row
rather than a branch in the door.

The registry sits above every module and below the two doors, which is why
`TargetState`, `ActionModule`, and `EffectRefused` live in their own files:
this one imports every module, so nothing a module needs can live here.

A type absent from this table is refused by `perform` with
`class_not_shipped`. The reserved names of seams §1.10 are absent on purpose:
`open_pr`, `post_message_draft`, and `calendar_hold` arrive at M2 and the
`act` actions at M3, and until then a request for one is a refusal with a
row, not an import error.
"""

from __future__ import annotations

from broker import gmail, push_branch
from broker.module import ActionModule

__all__ = ["MODULES", "module_for"]

MODULES: dict[str, ActionModule] = {
    push_branch.PushBranch.action_type: push_branch,
    gmail.ConnectorRead.action_type: gmail,
}


def module_for(action_type: str) -> ActionModule | None:
    """The module that performs this action type, or None when none does.

    None rather than an exception, because the caller that asks is `perform`
    and its answer to an unknown type is a `refused` row like any other.
    """
    return MODULES.get(action_type)
