"""DeadLetter model — one terminal sink for every stage of the pipeline.

Every place work leaves the pipeline without being delivered writes a row
here: the Telegram relay's retry cap, an unparseable outbox / steering /
notify payload, a session finalized at its recovery cap, a corrupted queue
row the reaper dropped, a post-session side effect that exhausted its
attempts. ``stage`` names the sink, ``payload_json`` holds enough to replay
it, and ``replayable`` says whether replaying is even meaningful.

The row is TERMINAL. Live retry state (an attempt schedule, a next-attempt
time) belongs on ``models/side_effect_job.SideEffectJob`` instead; a
``DeadLetter`` records what was lost and, when replay is possible, what to
re-send. ``attempts`` here counts *replay* attempts, seeded from whatever
attempt counter the writer had exhausted.

Replay and eviction are driven by the ``dead-letter-replay`` reflection
(``reflections/housekeeping/dead_letter_replay.py``) through the per-stage
handler registry in ``bridge/dead_letters.py``.
"""

from popoto import (
    AutoKeyField,
    DatetimeField,
    Field,
    IndexedField,
    IntField,
    KeyField,
    Model,
    SortedField,
)


class DeadLetter(Model):
    """Work that left the pipeline undelivered, with enough context to replay it.

    Fields:
        letter_id: Generated row key.
        stage: Which terminal sink wrote this row. Indexed so the dashboard
            tile and the replay reflection can select one stage at a time.
        chat_id: Telegram peer, for ``stage="telegram_send"``. Null on every
            other stage.
        project_key: Owning project, when the writer knew one.
        reply_to / text: The Telegram send payload, kept in their original
            columns so ``telegram_send`` replay reads exactly what it always
            read.
        payload_json: The JSON-encoded payload for every non-Telegram stage.
        reason: Why the work died, in the writer's own words.
        replayable: Whether the ``stage``'s handler can meaningfully re-run
            this row. False for parse failures, poison inputs, and rows that
            only mirror a durable record living elsewhere.
        first_seen / last_seen: When the row was written and when a replay
            last touched it.
        created_at: Legacy sort key, kept for the existing replay ordering.
        attempts: Replay attempts spent so far.
    """

    letter_id = AutoKeyField()
    chat_id = KeyField(null=True)
    project_key = KeyField(null=True)
    stage = IndexedField(default="telegram_send")
    reply_to = Field(type=int, null=True)
    text = Field(null=True)
    payload_json = Field(null=True)
    reason = Field(null=True)
    replayable = Field(type=bool, default=True)
    first_seen = DatetimeField(null=True)
    last_seen = DatetimeField(null=True)
    created_at = SortedField(type=float)
    attempts = IntField(default=0)

    class Meta:
        # Risk 4 (#3183): dead letters are unbounded on the write path, so the
        # row itself ages out. 30 days is long enough for a human to notice a
        # stage on the dashboard tile and short enough that a misbehaving relay
        # cannot fill Redis. Existing rows acquire this only when re-saved,
        # which is why `dead_letter_stage_backfill` re-saves every row.
        ttl = 30 * 24 * 60 * 60
