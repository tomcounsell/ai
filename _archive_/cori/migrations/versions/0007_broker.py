"""The effect ledger: what the broker did to the world.

Plan 07 task 2; seams §5.3, §6, §7; tech stack §7. Copies migration 0001 line
for line: the migrator owns, `kernel_rw` gets SELECT and INSERT and nothing
else, the `reject_mutation()` trigger of 0001 is attached BEFORE UPDATE OR
DELETE per row and BEFORE TRUNCATE per statement, and row-level security
carries the `kernel_rw` policy and the `context_ro` policy on
`cori_current_space()`. The table carries `space_id`, so the Verifier's slice
reads it per space (architecture §5, seams ruling 15).

One row per event and no status column, so a row is never updated and the
append-only grant holds. The state of an effect is a fold over its rows:
`intent` then at most one of `outcome` or `reconciled`, or exactly one
`refused`. `UNIQUE (effect_id, event)` is what makes that a fact of the table.

`objective_id`, `brief_id`, and `generation` are null only on the `read`
actions the kernel itself performs, the ingestion poll and the body fetch for
a render, which have no Brief (seams §5.3, amendment B).

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-22
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLE = "effect_ledger"


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE {TABLE} (
            id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            effect_id       text        NOT NULL,
            space_id        text        NOT NULL,
            objective_id    text        NULL,
            brief_id        text        NULL,
            generation      integer     NULL,
            action_type     text        NOT NULL,
            effect_class    text        NOT NULL,
            idempotency_key text        NOT NULL,
            target          text        NOT NULL,
            payload_sha256  text        NOT NULL,
            payload         jsonb       NOT NULL,
            event           text        NOT NULL,
            outcome_kind    text        NULL,
            result          jsonb       NULL,
            error           text        NULL,
            approval_id     text        NULL,
            schema_version  integer     NOT NULL DEFAULT 1,
            at              timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT {TABLE}_effect_class_check CHECK (
                effect_class IN ('read', 'propose', 'act')
            ),
            CONSTRAINT {TABLE}_event_check CHECK (
                event IN ('intent', 'outcome', 'refused', 'reconciled')
            ),
            CONSTRAINT {TABLE}_outcome_kind_check CHECK (
                outcome_kind IS NULL OR outcome_kind IN (
                    'done', 'refused', 'unknown', 'recovered', 'failed'
                )
            ),
            CONSTRAINT {TABLE}_intent_has_no_kind CHECK (
                event <> 'intent' OR outcome_kind IS NULL
            ),
            CONSTRAINT {TABLE}_effect_id_event_unique UNIQUE (effect_id, event)
        )
        """)
    op.execute(f"CREATE INDEX {TABLE}_key_idx ON {TABLE} (idempotency_key, id)")
    op.execute(f"CREATE INDEX {TABLE}_objective_idx ON {TABLE} (objective_id, id)")
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
