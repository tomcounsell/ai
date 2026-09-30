-- Spike 02: event store for a stateless control loop, plus a simulated
-- external world the loop has effects on.
DROP SCHEMA IF EXISTS es CASCADE;
CREATE SCHEMA es;

CREATE TABLE es.events (
  seq          bigserial PRIMARY KEY,
  objective_id text NOT NULL,
  type         text NOT NULL,
  payload      jsonb NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON es.events (objective_id, seq);

CREATE OR REPLACE FUNCTION es.reject_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'es.events is append-only (% rejected)', TG_OP;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON es.events
  FOR EACH ROW EXECUTE FUNCTION es.reject_mutation();

-- "The world": an external system the loop writes to. It is written on a
-- separate autocommit connection so it is never inside the loop's transaction.
-- No unique constraint on key, on purpose: duplicates must be observable.
CREATE TABLE es.world (
  id           bigserial PRIMARY KEY,
  objective_id text NOT NULL,
  key          text NOT NULL,
  payload      text NOT NULL,
  created_at   timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX ON es.world (objective_id, key);
