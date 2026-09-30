-- Spike 01: event-sourced objective tree with budget conservation.
-- Run as the cluster superuser (the "migrator"). The kernel role gets INSERT and
-- SELECT on the event tables and nothing else.

DROP SCHEMA IF EXISTS tree CASCADE;
CREATE SCHEMA tree;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cori_kernel') THEN
    CREATE ROLE cori_kernel LOGIN PASSWORD 'kernel';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'cori_kernel_fu') THEN
    -- Variant role: same as cori_kernel plus UPDATE on nodes, so that
    -- SELECT ... FOR UPDATE is permitted. A trigger still rejects real UPDATEs.
    CREATE ROLE cori_kernel_fu LOGIN PASSWORD 'kernel';
  END IF;
END $$;

-- A node's budget is fixed at creation: the amount its parent allocated to it.
CREATE TABLE tree.nodes (
  id         text PRIMARY KEY,
  parent_id  text REFERENCES tree.nodes(id),
  budget     bigint NOT NULL CHECK (budget >= 0),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Append-only ledger. Every change to remaining budget is a row here.
--   kind = 'allocate': parent gives `amount` of its budget to child_id
--   kind = 'consume' : node_id spends `amount` of its own budget
CREATE TABLE tree.budget_events (
  seq        bigserial PRIMARY KEY,
  kind       text NOT NULL CHECK (kind IN ('allocate', 'consume')),
  node_id    text NOT NULL REFERENCES tree.nodes(id),
  child_id   text REFERENCES tree.nodes(id),
  amount     bigint NOT NULL CHECK (amount > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON tree.budget_events (node_id);

CREATE OR REPLACE FUNCTION tree.reject_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'table % is append-only (% rejected)', TG_TABLE_NAME, TG_OP;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER nodes_append_only BEFORE UPDATE OR DELETE ON tree.nodes
  FOR EACH ROW EXECUTE FUNCTION tree.reject_mutation();
CREATE TRIGGER events_append_only BEFORE UPDATE OR DELETE ON tree.budget_events
  FOR EACH ROW EXECUTE FUNCTION tree.reject_mutation();
-- TRUNCATE is a separate privilege, not granted below, but belt and braces:
CREATE OR REPLACE FUNCTION tree.reject_truncate() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'table % is append-only (TRUNCATE rejected)', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER nodes_no_truncate BEFORE TRUNCATE ON tree.nodes
  FOR EACH STATEMENT EXECUTE FUNCTION tree.reject_truncate();
CREATE TRIGGER events_no_truncate BEFORE TRUNCATE ON tree.budget_events
  FOR EACH STATEMENT EXECUTE FUNCTION tree.reject_truncate();

-- Remaining budget of a node, derived purely from the ledger.
CREATE OR REPLACE VIEW tree.remaining AS
SELECT n.id,
       n.budget,
       n.budget - COALESCE((SELECT SUM(amount) FROM tree.budget_events e
                            WHERE e.node_id = n.id), 0) AS remaining
FROM tree.nodes n;

GRANT USAGE ON SCHEMA tree TO cori_kernel, cori_kernel_fu;
GRANT SELECT, INSERT ON tree.nodes, tree.budget_events TO cori_kernel, cori_kernel_fu;
GRANT SELECT ON tree.remaining TO cori_kernel, cori_kernel_fu;
GRANT USAGE ON SEQUENCE tree.budget_events_seq_seq TO cori_kernel, cori_kernel_fu;
-- The variant role may take row locks on nodes (needs UPDATE privilege).
GRANT UPDATE ON tree.nodes TO cori_kernel_fu;
