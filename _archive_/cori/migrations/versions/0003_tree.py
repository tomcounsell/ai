"""The tree's four tables: objectives, objective_revisions, briefs, budget_ledger.

Plan 02 Design; seams §6. Every table carries `space_id` and copies the
pattern of migration 0001 line for line: the migrator owns, `kernel_rw` gets
SELECT and INSERT, `context_ro` gets SELECT filtered by
`space_id = cori_current_space()`, and the one `reject_mutation()` trigger of
0001 is attached BEFORE UPDATE OR DELETE per row and BEFORE TRUNCATE per
statement.

`briefs.brief` is the Brief with `gateway_token` removed; the token's sha256
sits beside it. `briefs.objective_id` is null for the Scribe of an
objective-less turn. `budget_ledger.node_id` and `brief_id` carry no foreign
key because a node may be a root Scribe's brief id; `kernel/tree.py` is the
only writer and checks the id it writes. `parent_id` and `depth` exist
because seams §6 lists them; at M0 every row has `parent_id NULL` and
`depth 0` because the Planner ships at M1.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-21
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = ("objectives", "objective_revisions", "briefs", "budget_ledger")


def _lock_down(table: str) -> None:
    """Owner, grants, triggers, and RLS as migration 0001 gave `events`."""
    op.execute(f"ALTER TABLE {table} OWNER TO migrator")
    op.execute(f"GRANT SELECT, INSERT ON {table} TO kernel_rw")
    op.execute(f"GRANT SELECT ON {table} TO context_ro")
    op.execute(f"""
        CREATE TRIGGER {table}_append_only
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION reject_mutation()
        """)
    op.execute(f"""
        CREATE TRIGGER {table}_no_truncate
        BEFORE TRUNCATE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()
        """)
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {table}_kernel ON {table}
        TO kernel_rw USING (true) WITH CHECK (true)
        """)
    op.execute(f"""
        CREATE POLICY {table}_context_by_token ON {table}
        FOR SELECT TO context_ro USING (space_id = cori_current_space())
        """)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE objectives (
            id              text        PRIMARY KEY,
            parent_id       text        NULL REFERENCES objectives(id),
            space_id        text        NOT NULL,
            conversation_id text        NOT NULL,
            depth           integer     NOT NULL CHECK (depth >= 0),
            created_at      timestamptz NOT NULL DEFAULT now()
        )
        """)
    op.execute("CREATE INDEX objectives_space_id_idx ON objectives (space_id, id)")

    op.execute("""
        CREATE TABLE objective_revisions (
            id           bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            space_id     text        NOT NULL,
            objective_id text        NOT NULL REFERENCES objectives(id),
            revision     integer     NOT NULL,
            contract     jsonb       NOT NULL,
            at           timestamptz NOT NULL DEFAULT now(),
            UNIQUE (objective_id, revision)
        )
        """)

    op.execute("""
        CREATE TABLE briefs (
            id                   text        PRIMARY KEY,
            space_id             text        NOT NULL,
            objective_id         text        NULL REFERENCES objectives(id),
            agent_class          text        NOT NULL,
            generation           integer     NOT NULL,
            brief                jsonb       NOT NULL,
            gateway_token_sha256 text        NOT NULL,
            issued_at            timestamptz NOT NULL
        )
        """)
    op.execute("CREATE INDEX briefs_objective_id_idx ON briefs (objective_id)")

    op.execute("""
        CREATE TABLE budget_ledger (
            id          bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            space_id    text        NOT NULL,
            node_id     text        NOT NULL,
            brief_id    text        NULL,
            kind        text        NOT NULL
                        CHECK (kind IN ('grant', 'allocate', 'consume', 'release')),
            usd_micros  bigint      NOT NULL CHECK (usd_micros >= 0),
            approval_id text        NULL,
            at          timestamptz NOT NULL DEFAULT now()
        )
        """)
    op.execute("CREATE INDEX budget_ledger_node_id_idx ON budget_ledger (node_id)")
    op.execute("CREATE INDEX budget_ledger_brief_id_idx ON budget_ledger (brief_id)")

    for table in TABLES:
        _lock_down(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
