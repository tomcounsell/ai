"""A database of a test's own, with the contents `db.migrate(name,
fresh=True)` gives, made by copying a template migrated once per session.

The template is `<name>_tpl`: `name` carries the session's test database
(`VALOR_TEST_DB`), so each session, and each xdist worker, has templates of
its own. It is migrated by `db.migrate` the first time a session asks for
`name`, marked a template that takes no connection (so `CREATE DATABASE
... TEMPLATE` never finds a session on it), and dropped when the process
exits. Every database made from it is dropped first (`WITH (FORCE)`) and
created as a copy, owned by the owner as `migrate` would create it. The
copy's rows are the template's, so the seeded rows' `at` is the template's
migrate time, not the test's.
"""

import atexit

import psycopg
from psycopg import sql

from core import db
from core.settings import settings

_made: set[str] = set()


def _owner() -> psycopg.Connection:
    return psycopg.connect(settings.dsn(owner=True, database="postgres"), autocommit=True)


def _template(name: str) -> str:
    template = f"{name}_tpl"
    if template in _made:
        return template
    with _owner() as conn:  # one a session that died left: `migrate` cannot drop a template
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (template,)).fetchone():
            conn.execute(sql.SQL("ALTER DATABASE {} WITH IS_TEMPLATE false").format(sql.Identifier(template)))
    db.migrate(template, fresh=True)
    with _owner() as conn:
        conn.execute(
            sql.SQL("ALTER DATABASE {} WITH IS_TEMPLATE true ALLOW_CONNECTIONS false").format(
                sql.Identifier(template)
            )
        )
    _made.add(template)
    return template


def fresh(name: str) -> str:
    """`name` dropped and created again from its template; the kernel DSN."""
    template = _template(name)
    with _owner() as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template))
        )
    return settings.dsn(database=name)


@atexit.register
def _drop_templates() -> None:
    if not _made:
        return
    with _owner() as conn:
        for template in sorted(_made):
            conn.execute(sql.SQL("ALTER DATABASE {} WITH IS_TEMPLATE false").format(sql.Identifier(template)))
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(template)))
