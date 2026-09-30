"""Checks and verdicts: the kernel's own record of a verification.

Plan 11 task 1; seams §6, §7; architecture §5; tech stack §3. Both tables
copy migration 0001 line for line: the migrator owns, `kernel_rw` gets
SELECT and INSERT and nothing else, the `reject_mutation()` trigger of 0001
is attached BEFORE UPDATE OR DELETE per row and BEFORE TRUNCATE per
statement, and row-level security carries the `kernel_rw` policy and the
`context_ro` policy on `cori_current_space()`.

`checks` holds one row per deterministic check the kernel ran on an
Executor's snapshot, written before any prose reaches the Verifier;
`brief_id` is the Executor brief whose snapshot was checked. `verdicts`
holds one row per verdict, the model's or the kernel's; `brief_id` is the
Executor brief judged, `verifier_brief_id` is null for a kernel verdict,
and `model_ref` with `prompt_sha256` keys a calibration series
(architecture §5).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-22
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TABLES = ("checks", "verdicts")


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
        CREATE TABLE checks (
            id             bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            space_id       text        NOT NULL,
            objective_id   text        NOT NULL,
            brief_id       text        NOT NULL,
            name           text        NOT NULL,
            passed         boolean     NOT NULL,
            output_sha256  text        NOT NULL,
            detail         text        NOT NULL DEFAULT '',
            resolutions    jsonb       NULL,
            schema_version integer     NOT NULL DEFAULT 1,
            at             timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT checks_name_check CHECK (
                name IN (
                    'tests', 'build', 'citations_resolve', 'sections_present',
                    'length', 'recipient_allowed', 'no_operator_content'
                )
            ),
            CONSTRAINT checks_detail_check CHECK (char_length(detail) <= 2000)
        )
        """)
    op.execute("CREATE INDEX checks_space_idx ON checks (space_id, id)")
    op.execute("CREATE INDEX checks_objective_idx ON checks (objective_id, id)")

    op.execute("""
        CREATE TABLE verdicts (
            id                bigint           GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            space_id          text             NOT NULL,
            objective_id      text             NOT NULL,
            brief_id          text             NOT NULL,
            verifier_brief_id text             NULL,
            model_ref         text             NOT NULL,
            prompt_sha256     text             NOT NULL,
            outcome           text             NOT NULL,
            predicted_failure double precision NOT NULL,
            criteria          jsonb            NOT NULL,
            scope_findings    jsonb            NOT NULL DEFAULT '[]'::jsonb,
            summary           text             NOT NULL DEFAULT '',
            sampled_with      double precision NOT NULL,
            schema_version    integer          NOT NULL DEFAULT 1,
            at                timestamptz      NOT NULL DEFAULT now(),
            CONSTRAINT verdicts_outcome_check CHECK (
                outcome IN ('pass', 'fail', 'abstain')
            ),
            CONSTRAINT verdicts_predicted_failure_check CHECK (
                predicted_failure >= 0 AND predicted_failure <= 1
            ),
            CONSTRAINT verdicts_sampled_with_check CHECK (
                sampled_with >= 0 AND sampled_with <= 1
            )
        )
        """)
    op.execute("CREATE INDEX verdicts_space_idx ON verdicts (space_id, id)")
    op.execute("CREATE INDEX verdicts_objective_idx ON verdicts (objective_id, id)")

    for table in TABLES:
        _protect(table)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS verdicts")
    op.execute("DROP TABLE IF EXISTS checks")
