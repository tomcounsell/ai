-- Kernel state: JSONB documents plus one append-only events table.
-- Applied by the database owner. The kernel connects as valor_kernel, which
-- may read and insert and nothing else. No foreign keys: a row names what it
-- belongs to by id inside its payload.

CREATE TABLE IF NOT EXISTS documents (
    kind       text        NOT NULL,
    id         text        NOT NULL,
    body       jsonb       NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (kind, id)
);

-- The ledger. Every model call and its charge, turn, stop, effect, and
-- approval is a row here, and a row is never changed.
CREATE TABLE IF NOT EXISTS events (
    id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id text        NOT NULL,
    type    text        NOT NULL,
    payload jsonb       NOT NULL DEFAULT '{}'::jsonb,
    at      timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS events_task_idx ON events (task_id, id);
CREATE INDEX IF NOT EXISTS events_payload_gin ON events USING gin (payload jsonb_path_ops);

-- One open and one charge per gateway call, one of each effect row per
-- effect, an approval consumed by at most one intent, and one correction
-- per number. These make
-- every fold over the ledger total.
-- `gateway.reserved` is how ledgers written before 2026-10-03 opened a call.
-- The index replaced `events_one_call_row`, which covered only the legacy
-- open and the charge; migrate drops that one.
CREATE UNIQUE INDEX IF NOT EXISTS events_one_gateway_row
    ON events (type, (payload->>'call_id'))
    WHERE type IN ('gateway.opened', 'gateway.reserved', 'gateway.charged');
DROP INDEX IF EXISTS events_one_call_row;
CREATE UNIQUE INDEX IF NOT EXISTS events_one_effect_row
    ON events (type, (payload->>'effect_id'))
    WHERE type IN ('effect.held', 'effect.intent', 'effect.outcome', 'effect.refused');
CREATE UNIQUE INDEX IF NOT EXISTS events_approval_used_once
    ON events ((payload->>'approval_id'))
    WHERE type = 'effect.intent' AND payload->>'approval_id' IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS events_one_correction_number
    ON events ((payload->>'number'))
    WHERE type = 'correction.recorded';
CREATE UNIQUE INDEX IF NOT EXISTS events_one_stop
    ON events (task_id) WHERE type = 'task.stopped';

CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % refused', TG_TABLE_NAME, TG_OP;
END
$$;

DROP TRIGGER IF EXISTS events_append_only ON events;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
DROP TRIGGER IF EXISTS events_no_truncate ON events;
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();

-- Grants are the first lock, the triggers the second.
REVOKE ALL ON events, documents FROM PUBLIC;
GRANT SELECT, INSERT ON events TO valor_kernel;
GRANT SELECT, INSERT ON documents TO valor_kernel;

-- The state machine's rows (core/machine.py). One judge verdict per task;
-- one row of each turn type per turn; one grant per guard id, and one
-- grant per governance instance on a task. The verdict enum is a CHECK
-- constraint `db.migrate` adds from `machine.VERDICTS`.
CREATE UNIQUE INDEX IF NOT EXISTS events_one_judge
    ON events (task_id) WHERE type = 'judge.decided';
CREATE UNIQUE INDEX IF NOT EXISTS events_one_turn_row
    ON events (type, (payload->>'turn_id'))
    WHERE type IN ('turn.started', 'turn.ended', 'turn.collected', 'turn.reaped');
CREATE UNIQUE INDEX IF NOT EXISTS events_one_guard
    ON events ((payload->>'guard_id')) WHERE type = 'guard.granted';
CREATE UNIQUE INDEX IF NOT EXISTS events_one_instance_grant
    ON events (task_id, (payload->>'instance_id'))
    WHERE type = 'guard.granted' AND payload->>'instance_id' IS NOT NULL;

-- The judgement port's rows (core/judgement.py): one outcome per judgement.
CREATE UNIQUE INDEX IF NOT EXISTS events_one_judgement
    ON events ((payload->>'judgement_id'))
    WHERE type IN ('judgement.answered', 'judgement.failed');
