"""Events: two indexes and a table-naming reject_mutation().

Seams §6: `events_type_id_idx (type, id)` serves the supervisor's cross-space
poll for trigger types; `events_payload_gin (payload jsonb_path_ops)` serves
`read_for`, which uses only `@>`. Plan findings 2026-09-19, finding 4: every
insert-only table attaches the one `reject_mutation()` function, so its
message names the table it fires on rather than `events`. CREATE OR REPLACE
keeps the function's oid, so the triggers already bound stay bound.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX events_type_id_idx ON events (type, id)")
    op.execute(
        "CREATE INDEX events_payload_gin ON events USING gin (payload jsonb_path_ops)"
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % refused by trigger', TG_TABLE_NAME, TG_OP;
        END
        $$
        """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS events_payload_gin")
    op.execute("DROP INDEX IF EXISTS events_type_id_idx")
    op.execute("""
        CREATE OR REPLACE FUNCTION reject_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'events is append-only: % refused by trigger', TG_OP;
        END
        $$
        """)
