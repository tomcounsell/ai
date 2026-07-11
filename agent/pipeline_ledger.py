"""PipelineLedger - durable, issue-keyed SDLC pipeline ledger (issue #2012).

The SDLC pipeline's stage/verdict/PR-number ledger historically lived on
``AgentSession.stage_states`` -- a JSON blob keyed by the *executor* (the
session doing the work). The executor is ephemeral: it crashes, completes,
gets killed, gets superseded, or gets taken over by a different session
(e.g. a foreign-slug takeover after the original driver goes terminal).
Every one of those lifecycle events was a potential state-loss event,
because the ledger lived on the thing most likely to disappear.

``PipelineLedger`` moves the ledger to the entity the pipeline is *about*:
the ``(target_repo, issue_number)`` pair. A driver session and a takeover
session working the same issue read and write the SAME ledger record --
the ledger never moves, because it never lived on either session. Write
authority over a given ledger is enforced separately, by the run_id issue
lock (see ``models/session_lifecycle.py::touch_issue_lock``) -- this model
is pure storage and does not itself gate writes.

No TTL: the ledger must survive indefinitely (unlike ``DedupRecord``'s
2-hour TTL). A ledger record persists even after its issue's PR merges and
every AgentSession that ever worked it is deleted -- see
``docs/features/sdlc-issue-keyed-stage-ledger.md``.
"""

from __future__ import annotations

from popoto import Field, IntField, KeyField, Model


def _build_key(target_repo: str, issue_number: int) -> str:
    """Assemble the composite ``{target_repo}:{issue_number}`` ledger key.

    Callers must supply an already-resolved, non-``None`` ``target_repo``
    (resolved once at lease-acquire time and pinned on the issue lock
    payload -- see ``tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind``).
    This module does not resolve or validate ``target_repo`` itself; a
    ``None`` or empty ``target_repo`` reaching here would mint a phantom
    ``None:{issue}`` key, which is exactly the failure mode Risk 5 of the
    plan guards against at the call sites (writers hard-fail, readers take
    the defined empty-ledger outcome) rather than here.
    """
    return f"{target_repo}:{issue_number}"


class PipelineLedger(Model):
    """Durable SDLC pipeline ledger, keyed by ``(target_repo, issue_number)``.

    Holds exactly what ``AgentSession.stage_states`` held before this model
    existed: the ``ALL_STAGES`` stage-status dict, the two cycle counters
    (``_patch_cycle_count``, ``_critique_cycle_count``), ``_verdicts``, and
    ``_sdlc_dispatches`` -- all serialized together as a single JSON blob in
    ``stage_states_json``, mirroring the wire format ``AgentSession.stage_states``
    already used. ``pr_number`` is a separate typed field (not embedded in
    the JSON blob) because ``AgentSession.pr_number`` is itself a field-backed
    attribute with a single writer (``sdlc-tool meta-set --key pr_number``),
    not a key inside the stage_states blob -- this model mirrors that shape.

    Fields:
        ledger_key: Composite string key ``"{target_repo}:{issue_number}"``.
            Built via :func:`_build_key`; never assembled with a ``None``
            component (see that function's docstring).
        target_repo: The GitHub ``owner/name`` slug this record belongs to,
            stored redundantly (also embedded in ``ledger_key``) so
            inspection/debugging/migration tooling can filter without
            parsing the composite key.
        issue_number: The GitHub issue number, stored redundantly for the
            same reason.
        stage_states_json: JSON-serialized dict holding the stage-status
            map plus all underscore-prefixed metadata keys. Defaults to
            ``"{}"`` for a freshly created, empty-but-valid ledger.
        pr_number: The PR number resolved for this issue's work, or
            ``None``. Field-backed, single-writer, mirrors
            ``AgentSession.pr_number``.

    No TTL (see module docstring) -- this record must outlive every
    AgentSession lifecycle event, indefinitely.
    """

    ledger_key = KeyField()
    target_repo = Field(null=True)
    issue_number = IntField(null=True)
    stage_states_json = Field(default="{}")
    pr_number = IntField(null=True)

    @classmethod
    def get_or_create(cls, target_repo: str, issue_number: int) -> PipelineLedger:
        """Return the ledger for ``(target_repo, issue_number)``, creating it if absent.

        An absent ledger is empty-but-valid, not an error: this is what lets
        ``PipelineStateMachine.for_issue()`` construct a fresh state machine
        for an issue that has never been written to before (predecessor
        backfill on first write, matching the pre-ledger session-keyed
        behavior of ``PipelineStateMachine.__init__`` on a session with no
        prior ``stage_states``).

        Args:
            target_repo: Already-resolved ``owner/name`` GitHub slug. Callers
                are responsible for never passing ``None``/empty here (see
                :func:`_build_key`'s docstring for why that responsibility is
                pushed to the caller rather than enforced in this model).
            issue_number: The GitHub issue number.

        Returns:
            The existing or newly created ``PipelineLedger`` record.
        """
        key = _build_key(target_repo, issue_number)
        existing = cls.query.filter(ledger_key=key)
        if existing:
            return existing[0]
        return cls.create(
            ledger_key=key,
            target_repo=target_repo,
            issue_number=issue_number,
            stage_states_json="{}",
        )
