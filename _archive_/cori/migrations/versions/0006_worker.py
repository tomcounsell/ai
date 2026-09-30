"""The tool log: what the PydanticAI loop invoked, before and after each call.

Plan 05 task 3; seams §5.2, §6, §7; tech stack §4. Copies migration 0001
line for line: the migrator owns, `kernel_rw` gets SELECT and INSERT and
nothing else, the `reject_mutation()` trigger of 0001 is attached BEFORE
UPDATE OR DELETE per row and BEFORE TRUNCATE per statement, and RLS carries
the `kernel_rw` policy and the `context_ro` policy on `cori_current_space()`.
The table carries `space_id`, so `context_ro` reads it per space (seams
ruling 15): the Verifier's slice is rendered from it.

`seq` is null on a `terminal` row and Postgres treats nulls as distinct, so
`UNIQUE (brief_id, seq, event)` bounds nothing there; the partial unique index
on `(brief_id, generation) WHERE event = 'terminal'` is what makes one
terminal per generation a fact of the table rather than of the door alone
(plan 05, critique 11).

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLE = "tool_log"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {TABLE} (
            id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            brief_id        text        NOT NULL,
            generation      integer     NOT NULL,
            space_id        text        NOT NULL,
            seq             integer     NULL,
            event           text        NOT NULL,
            tool            text        NULL,
            input           jsonb       NULL,
            input_sha256    text        NULL,
            exit_status     integer     NULL,
            stdout_sha256   text        NULL,
            stderr_sha256   text        NULL,
            artifact        text        NULL,
            artifact_sha256 text        NULL,
            duration_ms     integer     NULL,
            question_id     text        NULL,
            text            text        NULL,
            outcome         text        NULL,
            report          jsonb       NULL,
            schema_version  integer     NOT NULL DEFAULT 1,
            at              timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT {TABLE}_event_check CHECK (
                event IN ('tool.start', 'tool.end', 'question', 'answer', 'terminal')
            ),
            CONSTRAINT {TABLE}_seq_event_unique UNIQUE (brief_id, seq, event)
        )
        """)
    op.execute(
        f"CREATE UNIQUE INDEX {TABLE}_one_terminal_idx ON {TABLE} "
        "(brief_id, generation) WHERE event = 'terminal'"
    )
    op.execute(
        f"CREATE INDEX {TABLE}_brief_generation_seq_idx ON {TABLE} "
        "(brief_id, generation, seq)"
    )
    op.execute(f"CREATE INDEX {TABLE}_space_id_idx ON {TABLE} (space_id, id)")

    op.execute(f"ALTER TABLE {TABLE} OWNER TO migrator")
    op.execute(f"GRANT SELECT, INSERT ON {TABLE} TO kernel_rw")
    op.execute(f"GRANT SELECT ON {TABLE} TO context_ro")
    op.execute(f"""
        CREATE TRIGGER {TABLE}_append_only
        BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION reject_mutation()
        """)
    op.execute(f"""
        CREATE TRIGGER {TABLE}_no_truncate
        BEFORE TRUNCATE ON {TABLE}
        FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation()
        """)
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY {TABLE}_kernel ON {TABLE}
        TO kernel_rw USING (true) WITH CHECK (true)
        """)
    op.execute(f"""
        CREATE POLICY {TABLE}_context_by_token ON {TABLE}
        FOR SELECT TO context_ro USING (space_id = cori_current_space())
        """)


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
