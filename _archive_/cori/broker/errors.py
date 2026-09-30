"""The one refusal the broker raises. Plan 07 task 4.

`EffectRefused` lived in `broker/push_branch.py` while that module was the
only thing that refused. `credentials.py`, `gmail.py`, and `actions.py` all
raise it now, and `actions.py` imports every module, so the exception moved
here to keep the imports a tree. `broker/push_branch.py` re-exports it, so
the name a caller already imports still resolves.
"""

__all__ = ["EffectRefused"]


class EffectRefused(PermissionError):
    """The broker refused an effect before performing it.

    Its message is the reason, and the reason is what `perform` writes to the
    `refused` row's `error` column. A refusal is a fact about the space's
    boundary, never a failure of the target, so nothing outside the process
    was touched when this is raised.
    """
