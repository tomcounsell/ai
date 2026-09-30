"""The gateway's three tables: the log, the request bodies, the tokens.

Plan 04 Design, "Tables"; seams §5.1, §6, §7. All three copy migration 0001
line for line: the migrator owns, `kernel_rw` gets SELECT and INSERT and
nothing else, and the `reject_mutation()` trigger of 0001 is attached BEFORE
UPDATE OR DELETE per row and BEFORE TRUNCATE per statement, so the grant is
the first lock and the trigger the second.

`gateway_log` carries `space_id`, so it gets the `context_ro` SELECT grant
and the `space_id = cori_current_space()` policy: a later render can total
spend per space. `request_bodies` holds conversation text and
`gateway_tokens` holds credential hashes, so `context_ro` holds nothing on
either (seams §5.1, last line). `gateway_tokens` still enables row-level
security with only the kernel policy, the shape migration 0004 gave
`read_tokens`; `request_bodies` has no `space_id` and no policy to write.

`brief_id`, `generation`, and `space_id` on `gateway_log` are nullable: an
`unknown_token` refusal knows none of them and is still a row, because a
probe against the gateway is worth seeing (seams §5.1).

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TABLES = ("gateway_log", "request_bodies", "gateway_tokens")


def _append_only(table: str) -> None:
    op.execute(f"ALTER TABLE {table} OWNER TO migrator")
    op.execute(f"GRANT SELECT, INSERT ON {table} TO kernel_rw")
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


def upgrade() -> None:
    op.execute("""
        CREATE TABLE gateway_log (
            id              bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            brief_id        text        NULL,
            generation      integer     NULL,
            space_id        text        NULL,
            call            integer     NULL,
            event           text        NOT NULL,
            model           text        NULL,
            request_sha256  text        NULL,
            estimated_input integer     NULL,
            usage           jsonb       NULL,
            stop_reason     text        NULL,
            reason          text        NULL,
            cache_state     text        NULL,
            schema_version  integer     NOT NULL DEFAULT 1,
            at              timestamptz NOT NULL DEFAULT now()
        )
        """)
    op.execute(
        "CREATE INDEX gateway_log_brief_call_idx ON gateway_log (brief_id, call)"
    )
    op.execute("CREATE INDEX gateway_log_space_id_idx ON gateway_log (space_id, id)")
    op.execute(
        "CREATE INDEX gateway_log_revoked_idx ON gateway_log (brief_id) "
        "WHERE event = 'token_revoked'"
    )

    op.execute("""
        CREATE TABLE request_bodies (
            sha256     text        PRIMARY KEY,
            body       jsonb       NOT NULL,
            first_seen timestamptz NOT NULL DEFAULT now()
        )
        """)

    op.execute("""
        CREATE TABLE gateway_tokens (
            token_sha256   text        PRIMARY KEY,
            brief_id       text        NOT NULL,
            generation     integer     NOT NULL,
            model_ref      text        NOT NULL,
            space_id       text        NOT NULL,
            cap_usd_micros bigint      NULL,
            issued_at      timestamptz NOT NULL DEFAULT now()
        )
        """)
    op.execute(
        "CREATE INDEX gateway_tokens_brief_idx ON gateway_tokens (brief_id, issued_at)"
    )

    for table in TABLES:
        _append_only(table)

    op.execute("GRANT SELECT ON gateway_log TO context_ro")
    op.execute("""
        CREATE POLICY gateway_log_context_by_token ON gateway_log
        FOR SELECT TO context_ro USING (space_id = cori_current_space())
        """)


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")
