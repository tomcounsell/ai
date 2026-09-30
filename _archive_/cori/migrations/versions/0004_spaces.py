"""The inbound item table, and row-level security on the read tokens.

Plan 03 Design; seams §6. `inbound_items` carries `space_id`, so it copies
the pattern of migration 0001 line for line: the migrator owns, `kernel_rw`
gets SELECT and INSERT, `context_ro` gets SELECT filtered by
`space_id = cori_current_space()`, and the one `reject_mutation()` trigger of
0001 is attached BEFORE UPDATE OR DELETE per row and BEFORE TRUNCATE per
statement.

Unique on `(connector, account, external_id, space_id)`: a re-read of the
same message is a no-op, and an item later assigned to a second space gets a
row in that space, which is what row-level security needs for that space's
render to see it.

`read_tokens` carries a `space_id` too, and seams §6 says every table that
does carries row-level security; migration 0001 left it off. It gets the
`kernel_rw` policy and no `context_ro` grant, so the table stays the lock it
was. `cori_current_space()` still resolves through it: the function is
SECURITY DEFINER owned by `migrator`, which owns the table and is not subject
to its policies.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE inbound_items (
            id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            item_id        text        NOT NULL,
            space_id       text        NOT NULL,
            connector      text        NOT NULL,
            account        text        NOT NULL,
            external_id    text        NOT NULL,
            headers        jsonb       NOT NULL DEFAULT '{}'::jsonb,
            received_at    timestamptz NOT NULL,
            routed_by      text        NULL,
            schema_version integer     NOT NULL DEFAULT 1,
            at             timestamptz NOT NULL DEFAULT now(),
            UNIQUE (connector, account, external_id, space_id)
        )
        """)
    op.execute(
        "CREATE INDEX inbound_items_space_id_idx ON inbound_items (space_id, id)"
    )

    op.execute("ALTER TABLE inbound_items OWNER TO migrator")
    op.execute("GRANT SELECT, INSERT ON inbound_items TO kernel_rw")
    op.execute("GRANT SELECT ON inbound_items TO context_ro")
    op.execute("""
        CREATE TRIGGER inbound_items_append_only
        BEFORE UPDATE OR DELETE ON inbound_items
        FOR EACH ROW EXECUTE FUNCTION reject_mutation()
        """)
    op.execute("""
        CREATE TRIGGER inbound_items_no_truncate
        BEFORE TRUNCATE ON inbound_items
        FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()
        """)
    op.execute("ALTER TABLE inbound_items ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY inbound_items_kernel ON inbound_items
        TO kernel_rw USING (true) WITH CHECK (true)
        """)
    op.execute("""
        CREATE POLICY inbound_items_context_by_token ON inbound_items
        FOR SELECT TO context_ro USING (space_id = cori_current_space())
        """)

    op.execute("ALTER TABLE read_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY read_tokens_kernel ON read_tokens
        TO kernel_rw USING (true) WITH CHECK (true)
        """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS read_tokens_kernel ON read_tokens")
    op.execute("ALTER TABLE read_tokens DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS inbound_items")
