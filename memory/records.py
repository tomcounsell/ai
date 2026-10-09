"""What memory holds, and the one backend it holds it in.

Every model is popoto's, on its Postgres backend in the schema `memory`,
connected as memory's own role. No Redis: each model names the `postgres`
backend, and `use` installs it before any read or write.
"""

import popoto
from popoto.backends import get_backend, reset_bindings, set_backend
from popoto.backends.postgres import PostgresBackend, close_pools

SCHEMA = "memory"

_dsn: str | None = None


class Record(popoto.Model):
    """One raw entry: a row's words, or one text entry of a turn's
    transcript. `key` is `<ledger id>.<entry index>`, so saving a row's
    records again overwrites them. `meta` holds what the label is rendered
    from (origin, task, turn, provenance, correction number)."""

    key = popoto.KeyField()
    project = popoto.KeyField()
    ledger_id = popoto.SortedField(type=int, partition_by="project")
    origin = popoto.StringField()
    text = popoto.StringField()
    words = popoto.BM25Field(source="text")
    meta = popoto.DictField()

    class Meta:
        backend = "postgres"


class Ingested(popoto.Model):
    """One per ledger row taken, saved after its records."""

    ledger_id = popoto.KeyField()

    class Meta:
        backend = "postgres"


def use(dsn: str) -> PostgresBackend:
    """Install the backend for `dsn`, replacing one installed for another
    database."""
    global _dsn
    if _dsn != dsn:
        reset()
        set_backend(PostgresBackend(dsn=dsn, schema=SCHEMA))
        _dsn = dsn
    return get_backend()


def reset() -> None:
    """Forget the installed backend, its cached tables, and its pools."""
    global _dsn
    set_backend(None)
    reset_bindings()
    close_pools()
    _dsn = None
