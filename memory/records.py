"""What memory holds, and the one backend it holds it in.

Every model is popoto's, on its Postgres backend in the schema `memory`,
connected as memory's own role. No Redis: each model names the `postgres`
backend, and `use` installs it before any read or write.

The store may be shared by the four Macs (docs/machine.md, The shared
memory store), so every record and every taken row names its `ledger`, the
kernel database it came from (`<machine>/<database>`): ledger ids are only
unique within one. A record is named across the store by `ref`,
`<ledger>/<key>`.
"""

import popoto
from popoto.backends import get_backend, reset_bindings, set_backend
from popoto.backends.postgres import PostgresBackend, close_pools

SCHEMA = "memory"

_dsn: str | None = None


class Record(popoto.Model):
    """One raw entry: a row's words, or one text entry of a turn's
    transcript. `key` is `<ledger id>.<entry index>` within its `ledger`, so
    saving a row's records again overwrites them. It carries the client and
    the project of the task it came from. `meta` holds what the label is
    rendered from (origin, task, turn, provenance, correction number) and
    `ledger_at`, the row's time in epoch seconds."""

    key = popoto.KeyField()
    ledger = popoto.KeyField()
    client = popoto.KeyField()
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

    ledger = popoto.KeyField()
    ledger_id = popoto.KeyField()

    class Meta:
        backend = "postgres"


class Standard(popoto.Model):
    """A record Valor has judged a standard of working: recalled for any
    client's task. Made from a `memory.marked` ledger row."""

    record = popoto.KeyField()

    class Meta:
        backend = "postgres"


class Confined(popoto.Model):
    """A record a correction named as a client fact found in another
    client's Brief: recalled for its own client's tasks only, mark or no
    mark. Made from that `correction.recorded` row."""

    record = popoto.KeyField()

    class Meta:
        backend = "postgres"


def ref(ledger: str, key: str) -> str:
    """A record's name across the store."""
    return f"{ledger}/{key}"


def split(name: str) -> tuple[str, str]:
    """`ref`'s inverse: the ledger and the key."""
    ledger, _, key = str(name).rpartition("/")
    return ledger, key


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
