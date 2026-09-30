"""Conversations, cards, and approvals: the person's end of the harness.

Plan 10 task 2; seams §6, §7; architecture §7; tech stack §3, §13. All three
tables copy migration 0001 line for line: the migrator owns, `kernel_rw` gets
SELECT and INSERT and nothing else, the `reject_mutation()` trigger of 0001
is attached BEFORE UPDATE OR DELETE per row and BEFORE TRUNCATE per
statement, and row-level security carries the `kernel_rw` policy and the
`context_ro` policy on `cori_current_space()`.

`approvals.card_id` is UNIQUE, so "one decision per card" is a fact of the
table before any code asserts it: a second reply, or an expiry sweep that
races a reply, fails at the database rather than in a check the kernel could
forget. Consumption is the event `approval.consumed` and never a column
(seams §1.12), so the row stays immutable and "consumed once" is a count.

`cards.objective_id` and `cards.issued` are columns beside the Card's own
fields (seams §6): `mint` and `expire_due` find the node without resolving a
brief, and a self-approval's unissued card is distinguishable from one the
person was shown.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-22
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TABLES = ("conversations", "cards", "approvals")


def _protect(table: str) -> None:
    """The grant, the trigger, and the policy pattern of migration 0001."""
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
        CREATE TABLE conversations (
            id             text        PRIMARY KEY,
            space_id       text        NOT NULL,
            opened_at      timestamptz NOT NULL DEFAULT now(),
            schema_version integer     NOT NULL DEFAULT 1
        )
        """)
    op.execute("CREATE INDEX conversations_space_idx ON conversations (space_id, id)")

    op.execute("""
        CREATE TABLE cards (
            id                text        PRIMARY KEY,
            kind              text        NOT NULL,
            space_id          text        NOT NULL,
            conversation_id   text        NOT NULL REFERENCES conversations (id),
            regards           text        NOT NULL,
            objective_id      text        NULL,
            contract_revision integer     NULL,
            fields            jsonb       NOT NULL DEFAULT '{}'::jsonb,
            options           jsonb       NOT NULL DEFAULT '[]'::jsonb,
            note              text        NOT NULL DEFAULT '',
            expires_at        timestamptz NOT NULL,
            issued_at         timestamptz NOT NULL,
            issued            boolean     NOT NULL DEFAULT true,
            schema_version    integer     NOT NULL DEFAULT 1
        )
        """)
    op.execute("CREATE INDEX cards_space_idx ON cards (space_id, id)")
    op.execute("CREATE INDEX cards_conversation_idx ON cards (conversation_id, id)")
    # `expire_due` sweeps issued, undecided cards by deadline.
    op.execute("CREATE INDEX cards_expiry_idx ON cards (expires_at) WHERE issued")

    op.execute("""
        CREATE TABLE approvals (
            id                text        PRIMARY KEY,
            card_id           text        NOT NULL UNIQUE REFERENCES cards (id),
            kind              text        NOT NULL,
            space_id          text        NOT NULL,
            objective_id      text        NULL,
            contract_revision integer     NULL,
            argument_sha256   text        NULL,
            raw_message       text        NOT NULL DEFAULT '',
            session_id        text        NOT NULL,
            decided_at        timestamptz NOT NULL,
            schema_version    integer     NOT NULL DEFAULT 1,
            CONSTRAINT approvals_kind_check CHECK (
                kind IN (
                    'approved', 'approved_with_edit', 'rejected',
                    'answered', 'self_approved', 'expired'
                )
            )
        )
        """)
    op.execute("CREATE INDEX approvals_space_idx ON approvals (space_id, id)")
    op.execute("CREATE INDEX approvals_objective_idx ON approvals (objective_id, id)")

    for table in TABLES:
        _protect(table)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approvals")
    op.execute("DROP TABLE IF EXISTS cards")
    op.execute("DROP TABLE IF EXISTS conversations")
