"""Roles, the append-only event table, and row-level security by space.

Tech stack §3. Three roles: kernel_rw (insert and select on events),
context_ro (select only, filtered by space through a read token), migrator
(owns everything and is the only role that can delete). The trigger is belt
to the grant's suspenders: a space-destroy migration run by the migrator
disables it inside its own transaction and appends the tombstone.

Revision ID: 0001
Revises:
Create Date: 2026-09-19
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Roles. Idempotent so a rerun against an existing cluster does nothing.
    # No passwords: local trust auth on localhost, and CI sets the same.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') THEN
                CREATE ROLE migrator LOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kernel_rw') THEN
                CREATE ROLE kernel_rw LOGIN;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'context_ro') THEN
                CREATE ROLE context_ro LOGIN;
            END IF;
        END
        $$
        """)
    op.execute("""
        DO $$
        BEGIN
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO migrator, kernel_rw, context_ro',
                current_database()
            );
        END
        $$
        """)
    op.execute("GRANT USAGE ON SCHEMA public TO kernel_rw, context_ro")
    op.execute("GRANT ALL ON SCHEMA public TO migrator")

    # The event table. Every event has type and schema_version from day one
    # (tech stack §10); readers upcast, rows are never rewritten.
    op.execute("""
        CREATE TABLE events (
            id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            space_id       text        NOT NULL,
            type           text        NOT NULL,
            schema_version integer     NOT NULL DEFAULT 1,
            occurred_at    timestamptz NOT NULL DEFAULT now(),
            payload        jsonb       NOT NULL DEFAULT '{}'::jsonb
        )
        """)
    op.execute("CREATE INDEX events_space_id_idx ON events (space_id, id)")
    op.execute("ALTER TABLE events OWNER TO migrator")

    # Grants are the first lock.
    op.execute("GRANT SELECT, INSERT ON events TO kernel_rw")
    op.execute("GRANT SELECT ON events TO context_ro")

    # The trigger is the second lock.
    op.execute("""
        CREATE FUNCTION reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'events is append-only: % refused by trigger', TG_OP;
        END
        $$
        """)
    op.execute("ALTER FUNCTION reject_mutation() OWNER TO migrator")
    op.execute("""
        CREATE TRIGGER events_append_only
        BEFORE UPDATE OR DELETE ON events
        FOR EACH ROW EXECUTE FUNCTION reject_mutation()
        """)
    op.execute("""
        CREATE TRIGGER events_no_truncate
        BEFORE TRUNCATE ON events
        FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()
        """)

    # Read tokens: the kernel mints one per render, per space. The session
    # variable cori.read_token carries it. context_ro can set the variable to
    # anything, and only a live token the kernel minted resolves to a space.
    op.execute("""
        CREATE TABLE read_tokens (
            token      text        PRIMARY KEY,
            space_id   text        NOT NULL,
            expires_at timestamptz NOT NULL
        )
        """)
    op.execute("ALTER TABLE read_tokens OWNER TO migrator")
    op.execute("GRANT INSERT ON read_tokens TO kernel_rw")

    # SECURITY DEFINER so the policy can resolve the token while context_ro
    # itself has no privilege on read_tokens.
    op.execute("""
        CREATE FUNCTION cori_current_space() RETURNS text
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public AS $$
            SELECT space_id FROM read_tokens
            WHERE token = current_setting('cori.read_token', true)
              AND expires_at > now()
        $$
        """)
    op.execute("ALTER FUNCTION cori_current_space() OWNER TO migrator")

    # Row-level security on every space-partitioned table.
    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY events_kernel ON events
        TO kernel_rw USING (true) WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY events_context_by_token ON events
        FOR SELECT TO context_ro USING (space_id = cori_current_space())
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS events")
    op.execute("DROP FUNCTION IF EXISTS cori_current_space()")
    op.execute("DROP TABLE IF EXISTS read_tokens")
    op.execute("DROP FUNCTION IF EXISTS reject_mutation()")
    # Roles are cluster-wide and may own objects elsewhere; they stay.
