"""The gateway itself: the provider key, the tokens, the revoked set, and
the per-Brief call state the call path runs against.

Plan 04, Modules and Tokens; seams §3.9. A class rather than module globals,
because the tests build one against a fake upstream and a fake tree while
production builds one from the Keychain, the seat file, and the local
database. The module-level names of seams §3.9 live in `gateway/__init__.py`
and delegate to a default instance, and an instance is the `TokenIssuer` the
tree's `delegate` and `stop` accept (seams §3.2).

The tree functions are injected as an object carrying `remaining`,
`consume`, and `check_generation`, defaulting to the `kernel.tree` module
resolved inside the factory. The import is in the function body, never at
module load: `kernel/tree.py` names this module as its default
`TokenIssuer`, and two module-level imports would be a cycle (critique 10).
"""

import asyncio
import os
import secrets
from dataclasses import dataclass, field

import httpx
import psycopg
from pydantic import SecretStr

from gateway import budget as gwbudget
from gateway import log as gwlog
from gateway.log import TokenRow, token_sha256
from infra.models import SeatFile
from infra.models import load as load_seats
from schemas.budget import Budget
from schemas.ids import SpaceId

UPSTREAM = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# 10 s to connect, 300 s between chunks, no total: a 128k-token generation
# outlives any total timeout and a stream silent for five minutes is dead
# (plan 04, Serving).
TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

# Re-anchor the estimate with count_tokens every tenth call (plan 04, the
# budget pre-check): spike 03 saw the estimate track the billed count
# exactly across six calls, and 410 ms once in ten calls is under 3% of call
# latency.
ANCHOR_EVERY = 10


class GatewayError(RuntimeError): ...


class TokenRefused(GatewayError):
    """A mint the gateway will not make. Never a refusal of a call."""


def default_connect():
    """A `kernel_rw` connection to the local database, the way
    `tests/conftest.py` builds one. The gateway never holds write
    credentials beyond the append-only role (tech stack §2)."""
    host = os.environ.get("CORI_PGHOST", "localhost")
    port = os.environ.get("CORI_PGPORT", "5432")
    database = os.environ.get("CORI_PGDATABASE", "cori")
    return psycopg.AsyncConnection.connect(
        f"postgresql://kernel_rw@{host}:{port}/{database}"
    )


@dataclass
class BriefState:
    """Per-Brief call state, keyed by the id a token was minted for: a Brief
    id, or a turn id for the supervisor's own turns."""

    call: int = 0
    prev_body: dict | None = None
    prev_billed_input: int | None = None
    calls_since_anchor: int = 0
    prefixes: list[str] = field(default_factory=list)
    turn_spent: int | None = None
    warmed: bool = False
    # What the ladder decided for the call now in flight, read by the
    # closing transaction: the estimate it passed, whether that estimate was
    # exact, the remaining it was compared against, and whether the prefix
    # rule stripped the forwarded body.
    pending_estimate: int | None = None
    pending_exact: bool = False
    pending_remaining: int | None = None
    pending_stripped: bool = False
    # Set when the counting endpoint could not be reached for this call, so
    # the ladder does not ask it a second time before refusing.
    count_failed: bool = False


class Gateway:
    def __init__(
        self,
        *,
        provider_key: str,
        connect=default_connect,
        tree=None,
        seats: SeatFile | None = None,
        upstream: str = UPSTREAM,
        client: httpx.AsyncClient | None = None,
    ):
        self.provider_key = provider_key
        self.connect = connect
        self._tree = tree
        self._seats = seats
        self.upstream = upstream
        self._client = client
        self.revoked: set[str] = set()
        self._tokens: dict[str, TokenRow] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Closing transactions still running after their client went away.
        self.closing: set = set()
        self._state: dict[str, BriefState] = {}

    # -- late-bound collaborators -------------------------------------------

    @property
    def tree(self):
        """`kernel.tree` by default, imported here rather than at module
        load so the two modules can name each other (critique 10)."""
        if self._tree is None:
            import kernel.tree

            self._tree = kernel.tree
        return self._tree

    @property
    def seats(self) -> SeatFile:
        if self._seats is None:
            self._seats = load_seats()
        return self._seats

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.upstream, timeout=TIMEOUT)
        return self._client

    async def drain(self) -> None:
        """Wait for every closing transaction whose client disconnected. A
        call outlives its reader; no call outlives the process."""
        while self.closing:
            await asyncio.gather(*list(self.closing), return_exceptions=True)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def prices(self, model_ref: str) -> dict[str, int]:
        """The four prices of a pinned model, in micro-dollars per million
        tokens. A token names a model the seat file pinned, since the kernel
        minted it from a seat (tech stack §4.1)."""
        for entry in self.seats.models:
            if entry.id == model_ref:
                return entry.usd_micros_per_mtok
        raise GatewayError(f"{model_ref} is not a pinned model")

    def upstream_headers(self, incoming=None) -> dict[str, str]:
        """The provider key is added here and nowhere else. The client's own
        key never reaches the provider, and `anthropic-version` and
        `anthropic-beta` are passed through (plan 04, step 5)."""
        headers = {
            "x-api-key": self.provider_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        for name in ("anthropic-version", "anthropic-beta"):
            value = (incoming or {}).get(name)
            if value:
                headers[name] = value
        return headers

    # -- the input estimate (seams §3.9) --------------------------------------

    async def count_tokens(self, model_ref: str, body: dict) -> int:
        """The provider's own count of the body's input, which is what makes
        a refusal exact rather than an estimate's margin (plan 04, step 3).
        Raises `GatewayError` naming the failure, which the caller records.
        """
        payload = {
            k: body[k]
            for k in ("system", "messages", "tools", "tool_choice", "thinking")
            if k in body
        }
        payload["model"] = model_ref
        try:
            response = await self.client.post(
                "/v1/messages/count_tokens",
                json=payload,
                headers=self.upstream_headers(),
            )
        except httpx.TimeoutException:
            raise GatewayError("count_tokens:timeout") from None
        except httpx.HTTPError:
            raise GatewayError("count_tokens:connect") from None
        if response.status_code != 200:
            raise GatewayError(f"count_tokens:{response.status_code}")
        return int(response.json()["input_tokens"])

    async def estimate_input(
        self, conn, *, token: TokenRow, body: dict, state: BriefState, anchor=False
    ) -> tuple[int, bool]:
        """The call's `estimated_input` and whether it is exact.

        Exact on a brief's first call, on every tenth call, whenever the
        previous body is not a prefix of this one, and before any refusal.
        Otherwise the previous billed input plus what was appended. When the
        counting endpoint cannot be reached the failure is a row of its own
        and the estimate falls back to the whole body's bytes over 3, which
        forwards but never refuses (plan 04, step 3).
        """
        estimate = gwbudget.estimate_input(
            body,
            prev_body=state.prev_body,
            prev_billed_input=state.prev_billed_input,
        )
        if (
            estimate is not None
            and not anchor
            and state.calls_since_anchor < ANCHOR_EVERY
        ):
            return estimate, False
        try:
            return await self.count_tokens(token.model_ref, body), True
        except GatewayError as failure:
            await gwlog.insert_log(
                conn,
                event="upstream_error",
                brief_id=token.brief_id,
                generation=token.generation,
                space_id=token.space_id,
                model=token.model_ref,
                call=None,
                reason=str(failure),
            )
            state.count_failed = True
            return gwbudget.fallback_estimate(body), False

    # -- per-Brief bookkeeping ----------------------------------------------

    def lock(self, brief_id: str) -> asyncio.Lock:
        """Calls are serialized per Brief, so the pre-check and the consume
        of one call are ordered before the pre-check of the next. A
        PydanticAI run makes one model request at a time, so the lock costs
        nothing in the normal case."""
        if brief_id not in self._locks:
            self._locks[brief_id] = asyncio.Lock()
        return self._locks[brief_id]

    def state(self, brief_id: str) -> BriefState:
        if brief_id not in self._state:
            self._state[brief_id] = BriefState()
        return self._state[brief_id]

    # -- start ---------------------------------------------------------------

    async def start(self) -> None:
        """Warm the revoked set from the log and close every request the
        last process left open. Both directions fail closed: a brief that
        was revoked stays revoked, and a call nobody saw finish is charged
        its reserve."""
        async with await self.connect() as conn:
            self.revoked |= await gwlog.revoked_briefs(conn)
            await conn.commit()
        await self.reconcile_dangling()

    async def reconcile_dangling(self) -> None:
        """Close every `request` row the last process left open.

        A gateway killed mid-stream must leave a conservative ledger, the
        way the broker reconciles a dangling intent (spike 02): the call is
        charged its reserve, the stored body's `max_tokens` at the output
        price plus the row's estimate at the cache write rate, marked
        `charged_reserved`, and consumed as incurred so a Brief stopped
        while the gateway was down still takes the charge.
        """
        from schemas.gateway import Usage

        async with await self.connect() as conn:
            dangling = await gwlog.dangling_requests(conn)
            for brief_id, generation, call, space_id, model, estimate, body in dangling:
                model_ref = model or body.get("model")
                prices = self.prices(model_ref)
                counts = {
                    "input_tokens": estimate,
                    "output_tokens": int(body.get("max_tokens") or 0),
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                }
                charge = gwbudget.cost(counts, prices)
                await gwlog.insert_log(
                    conn,
                    event="cut",
                    usage=Usage(**counts, usd_micros=charge, charged_reserved=True),
                    brief_id=brief_id,
                    generation=generation,
                    space_id=space_id,
                    model=model_ref,
                    call=call,
                    estimated_input=estimate,
                    reason="reconciled",
                )
                token = await gwlog.newest_token_for_brief(conn, brief_id)
                if token is not None and not token.is_turn:
                    await self.tree.consume(
                        conn, brief_id, Budget(usd_micros=charge), incurred=True
                    )
            await conn.commit()

    # -- tokens (seams §3.9) --------------------------------------------------

    async def issue_token(
        self,
        conn,
        *,
        brief_id: str,
        generation: int,
        model_ref: str,
        space: SpaceId,
        cap: Budget | None = None,
    ) -> SecretStr:
        """Mint a token, write its row and a `token_issued` row on the
        caller's connection, and return the plaintext.

        Nothing is cached: the caller's transaction may still roll back, and
        a cached entry would authenticate a token no row backs (critique 1).
        The plaintext is never stored; `brief.issued` carries its sha256.
        The token encodes nothing, so a leaked prefix reveals nothing.

        `generation = 0` is a turn token and carries a `cap`; a Brief's
        generation is 1 plus its stops and carries none.
        """
        if generation < 0:
            raise TokenRefused(f"generation {generation} is not a generation")
        if generation == 0 and cap is None:
            raise TokenRefused(
                f"{brief_id} is a turn id (generation 0) and a turn token "
                f"carries the cap its one model phase is checked against"
            )
        if generation > 0 and cap is not None:
            raise TokenRefused(
                f"{brief_id} carries generation {generation}, so it is a Brief, "
                f"and a Brief's budget is the tree's, never a cap"
            )
        plaintext = "cori-" + secrets.token_urlsafe(32)
        await gwlog.insert_token(
            conn,
            token_sha256=token_sha256(plaintext),
            brief_id=brief_id,
            generation=generation,
            model_ref=model_ref,
            space_id=space,
            cap_usd_micros=None if cap is None else cap.usd_micros,
        )
        await gwlog.insert_log(
            conn,
            event="token_issued",
            brief_id=brief_id,
            generation=generation,
            space_id=space,
            model=model_ref,
        )
        return SecretStr(plaintext)

    async def revoke(self, conn, brief_id: str) -> None:
        """Write the `token_revoked` row on the caller's connection and add
        the Brief to the in-memory revoked set. The stream generators check
        that set before every chunk, so a cut lands at the next upstream
        chunk (spike 03: 41 ms).

        A Brief with no token row has nothing to revoke and writes no row.
        If the caller's transaction later rolls back, the Brief stays
        refused in this process until restart, which is the fail-closed
        direction.
        """
        row = await gwlog.newest_token_for_brief(conn, brief_id)
        if row is None:
            return None
        await gwlog.insert_log(
            conn,
            event="token_revoked",
            brief_id=brief_id,
            generation=row.generation,
            space_id=row.space_id,
            model=row.model_ref,
        )
        self.revoked.add(brief_id)
        return None

    async def token(self, conn, plaintext: str) -> TokenRow | None:
        """A presented token, by hash. The row is read once and cached from
        that read: a row is immutable, so the cache is never stale, and a
        token whose issuing transaction rolled back is refused because no
        row exists."""
        sha = token_sha256(plaintext)
        if sha in self._tokens:
            return self._tokens[sha]
        row = await gwlog.token_row(conn, sha)
        if row is not None:
            self._tokens[sha] = row
        return row
