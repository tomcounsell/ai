"""Gmail connector: a `read` the broker performs outside the sandbox.
Architecture §9, tech stack §7. Prereqs item 12.

Secrets in the Keychain (service `cori`):
    google_oauth_client_id, google_oauth_client_secret   the desktop OAuth client
    gmail_refresh_token                                   minted by `authorize`

    uv run python -m broker.gmail authorize          # one-time consent in a browser
    uv run python -m broker.gmail recent psyoptimal.com
"""

from __future__ import annotations

import base64
import email.utils
import http.server
import re
import secrets
import subprocess
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from typing import Any

import httpx

from broker import ledger
from broker.credentials import Credential, credential_for
from broker.errors import EffectRefused
from broker.manifest import space_for, space_for_connector
from broker.module import TargetState
from infra.secrets import read_secret
from schemas.effect import ConnectorRead
from schemas.ids import new_id
from schemas.inbound import InboundItem
from schemas.space import UNASSIGNED_SPACE_ID, Connector, RoutingRule

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"


def _store(name: str, value: str) -> None:
    subprocess.run(
        [
            "security",
            "add-generic-password",
            "-s",
            "cori",
            "-a",
            name,
            "-w",
            value,
            "-U",
        ],
        check=True,
    )


def authorize(port: int = 8790) -> None:
    """Loopback OAuth flow for a desktop client. Stores the refresh token."""
    client_id = read_secret("google_oauth_client_id")
    client_secret = read_secret("google_oauth_client_secret")
    state = secrets.token_urlsafe(16)
    redirect = f"http://localhost:{port}/"
    code = {}

    class Catch(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state") == [state] and "code" in q:
                code["value"] = q["code"][0]
                body = b"Authorized. You can close this tab."
            else:
                body = b"Missing or mismatched code."
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    url = (
        AUTH_URL
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
                "login_hint": "tom@yuda.me",
            }
        )
    )
    print("opening browser for consent; if nothing opens, visit:\n", url)
    webbrowser.open(url)
    with http.server.HTTPServer(("localhost", port), Catch) as srv:
        while "value" not in code:
            srv.handle_request()
    r = httpx.post(
        TOKEN_URL,
        data={
            "code": code["value"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
    )
    r.raise_for_status()
    tok = r.json()
    if "refresh_token" not in tok:
        raise SystemExit("no refresh_token in the response; revoke the app and retry")
    _store("gmail_refresh_token", tok["refresh_token"])
    print("refresh token stored as gmail_refresh_token; scope:", tok.get("scope"))


def access_token() -> str:
    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": read_secret("google_oauth_client_id"),
            "client_secret": read_secret("google_oauth_client_secret"),
            "refresh_token": read_secret("gmail_refresh_token"),
            "grant_type": "refresh_token",
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


def query_for(sender_domain: str) -> str:
    """The routing rule as a Gmail query. Scoped to one sender domain."""
    return f"from:@{sender_domain}"


def recent(sender_domain: str, n: int = 10) -> list[dict]:
    """Headers (From, Subject, Date) of the n most recent messages from a domain."""
    headers = {"Authorization": f"Bearer {access_token()}"}
    with httpx.Client(headers=headers, timeout=30) as c:
        ids = (
            c.get(
                f"{API}/messages",
                params={"q": query_for(sender_domain), "maxResults": n},
            )
            .raise_for_status()
            .json()
            .get("messages", [])
        )
        out = []
        for m in ids:
            msg = (
                c.get(
                    f"{API}/messages/{m['id']}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject", "Date"],
                    },
                )
                .raise_for_status()
                .json()
            )
            h = {x["name"]: x["value"] for x in msg["payload"]["headers"]}
            out.append({"id": m["id"], **h})
    return out


# ---------------------------------------------------------------------------
# The kernel's own reads. Plan 07 task 6.

WORKER_QUERY = re.compile(r"id:[0-9a-f]+")
HEADERS = ("from", "to", "subject", "date", "message-id")
MAX_RESULTS = 25

model = ConnectorRead


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(account: str, query: str) -> str:
    """The derived key with the poll time appended.

    A read is repeatable by nature, and a poll that returned an earlier
    poll's outcome would never advance `since`, so two polls with one
    unchanged `since` have to be two effects (plan 07).
    """
    return f"gmail:{account}:{query}:{int(_now().timestamp())}"


def compose_query(rule: RoutingRule, since: datetime) -> str:
    """The routing rule and the window as one Gmail query.

    A rule with no `sender_domain` is refused rather than widened: the
    person's inbox belongs to no space (architecture §9), and a query without
    the domain clause is a read of the whole account.
    """
    if rule.sender_domain is None:
        raise EffectRefused(
            "a gmail rule with no sender_domain has no query: a read without "
            "the domain clause is a read of the whole account"
        )
    return f"from:@{rule.sender_domain} after:{int(since.timestamp())}"


def access_token_from(credential: Credential) -> str:
    """An access token minted from the credential's named secrets."""
    values = credential.secrets()
    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": values["google_oauth_client_id"],
            "client_secret": values["google_oauth_client_secret"],
            "refresh_token": values["gmail_refresh_token"],
            "grant_type": "refresh_token",
        },
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _headers_of(message: dict) -> dict[str, str]:
    """The five headers of seams §1.11, lowercased by the reader."""
    found = {
        h["name"].lower(): h["value"]
        for h in message.get("payload", {}).get("headers", [])
    }
    return {name: found[name] for name in HEADERS if name in found}


def _received_at(message: dict, headers: dict[str, str]) -> datetime:
    """The `date` header when it parses, else Gmail's own receipt time.

    The header is the sender's clock and can be absent or malformed, so the
    fallback is `internalDate`, which is the account's.
    """
    raw = headers.get("date")
    if raw:
        try:
            parsed = email.utils.parsedate_to_datetime(raw)
        except TypeError, ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
    internal = message.get("internalDate")
    if internal:
        return datetime.fromtimestamp(int(internal) / 1000, tz=timezone.utc)
    return _now()


def _plain_text(payload: dict) -> str:
    """The decoded `text/plain` part, walking a multipart tree.

    Attachments are never fetched: a part with a `filename` is skipped, and
    only the text body reaches a Brief (architecture §9).
    """
    if payload.get("filename"):
        return ""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
    parts = payload.get("parts") or []
    for part in parts:
        text = _plain_text(part)
        if text:
            return text
    return ""


async def _ledger_pair(conn, space_id, account, query, key, run):
    """Intent, commit, read, outcome. The protocol of spike 02 for a read.

    The kernel's own reads take no part in `closed_for_key` or `in_flight`:
    they are repeatable by nature, and a poll that returned an earlier poll's
    outcome would never advance `since`. The rows are the audit record that
    the credential was used (tech stack §7), not a lock.
    """
    action = ConnectorRead(
        space=space_id,
        objective_id=None,
        brief_id=None,
        connector="gmail",
        account=account,
        query=query,
        idempotency_key=key,
    )
    fields = {
        "action_type": action.action_type,
        "effect_class": action.effect_class,
        "space": space_id,
        "target": action.target(),
        "idempotency_key": key,
        "payload": action.model_dump(mode="json"),
    }
    effect_id = await ledger.intent(conn, **fields)
    try:
        result, value = await run()
    except Exception as exc:
        await ledger.outcome(
            conn, effect_id=effect_id, kind="failed", error=str(exc), **fields
        )
        raise
    await ledger.outcome(
        conn, effect_id=effect_id, kind="done", result=result, **fields
    )
    return value


async def read_recent(conn, connector: Connector, since: datetime) -> list[InboundItem]:
    """Headers of everything the rule matches since `since`.

    Every item comes back `unassigned` with `routed_by=None`. Routing is
    `kernel.spaces.route`'s alone, so the connector never decides where an
    item lands (seams §3.4).
    """
    if connector.kind != "gmail":
        raise EffectRefused(f"this module reads gmail, not {connector.kind!r}")
    space = space_for_connector(connector)
    credential = credential_for(space.id, "mailto:" + connector.account)
    query = compose_query(connector.route, since)

    async def run():
        items = await _read_headers(credential, connector.account, query)
        return {"items": [item.model_dump(mode="json") for item in items]}, items

    return await _ledger_pair(
        conn, space.id, connector.account, query, _key(connector.account, query), run
    )


async def _read_headers(
    credential: Credential, account: str, query: str
) -> list[InboundItem]:
    auth = {"Authorization": f"Bearer {access_token_from(credential)}"}
    async with httpx.AsyncClient(headers=auth, timeout=30) as client:
        listing = (
            (
                await client.get(
                    f"{API}/messages", params={"q": query, "maxResults": MAX_RESULTS}
                )
            )
            .raise_for_status()
            .json()
        )
        items = []
        for stub in listing.get("messages", []):
            message = (
                (
                    await client.get(
                        f"{API}/messages/{stub['id']}",
                        params={
                            "format": "metadata",
                            "metadataHeaders": [h.title() for h in HEADERS],
                        },
                    )
                )
                .raise_for_status()
                .json()
            )
            headers = _headers_of(message)
            items.append(
                InboundItem(
                    id=new_id(),
                    connector="gmail",
                    account=account,
                    external_id=stub["id"],
                    headers=headers,
                    received_at=_received_at(message, headers),
                    space=UNASSIGNED_SPACE_ID,
                    routed_by=None,
                )
            )
    return items


def _connector_of(space, item: InboundItem) -> Connector:
    for connector in space.connectors:
        if connector.kind == item.connector and connector.account == item.account:
            return connector
    raise EffectRefused(
        f"space {space.id!r} has no {item.connector} connector on {item.account!r}"
    )


async def fetch_body(conn, item: InboundItem) -> str:
    """The `text/plain` body of one routed item.

    Three refusals before any HTTP: an unassigned item stays header-only
    forever (architecture §9), a space with no connector on the account has
    no claim to the message, and a message whose sender fails the rule is not
    the item the routing recorded.
    """
    if item.space == UNASSIGNED_SPACE_ID:
        raise EffectRefused(
            f"item {item.external_id!r} is unassigned and stays header-only"
        )
    space = space_for(item.space)
    connector = _connector_of(space, item)
    credential = credential_for(space.id, "mailto:" + item.account)
    query = f"id:{item.external_id}"

    async def run():
        auth = {"Authorization": f"Bearer {access_token_from(credential)}"}
        async with httpx.AsyncClient(headers=auth, timeout=30) as client:
            message = (
                (
                    await client.get(
                        f"{API}/messages/{item.external_id}", params={"format": "full"}
                    )
                )
                .raise_for_status()
                .json()
            )
        headers = _headers_of(message)
        fetched = InboundItem(
            id=item.id,
            connector=item.connector,
            account=item.account,
            external_id=item.external_id,
            headers=headers,
            received_at=_received_at(message, headers),
            space=item.space,
            routed_by=item.routed_by,
        )
        _refuse_off_rule(connector, fetched)
        body = _plain_text(message.get("payload", {}))
        return {"headers": headers, "body": body}, body

    return await _ledger_pair(
        conn, space.id, item.account, query, _key(item.account, query), run
    )


def _sender_domain(item: InboundItem) -> str | None:
    """The domain of the address in the `from` header, lowercased.

    The same reading `kernel.spaces` applies when it routes: an empty local
    part is not an address, so a `from` of "@psyoptimal.com" is nobody. The
    rule is restated here rather than imported, because the broker imports
    one kernel function and no more (plan 07, Modules).
    """
    _, address = email.utils.parseaddr(item.headers.get("from", ""))
    local, at, domain = address.rpartition("@")
    if not at or not local.strip() or not domain.strip():
        return None
    return domain.strip().lower()


def _refuse_off_rule(connector: Connector, item: InboundItem) -> None:
    """The fetched message has to still match the rule that routed it.

    Routing read the headers the listing returned; the fetch reads the
    message itself. A disagreement between the two is the case where a body
    from outside the rule would enter a space, so it is refused rather than
    reconciled.
    """
    wanted = connector.route.sender_domain
    if wanted is None or _sender_domain(item) != wanted.strip().lower():
        raise EffectRefused(
            f"message {item.external_id!r} does not match the rule on "
            f"{item.account!r}, so its body stays outside {item.space!r}"
        )


# ---------------------------------------------------------------------------
# The `ConnectorRead` action module, for a worker request through `perform`.


def check(action: ConnectorRead) -> None:
    """The only worker-requestable query is one message id.

    A free query string would be the cross-space read channel: the inbox is a
    shared surface that belongs to no space (architecture §9, findings 7 and
    9), so a worker names an item and never a search.
    """
    if action.connector != "gmail":
        raise EffectRefused(f"this module reads gmail, not {action.connector!r}")
    if not WORKER_QUERY.fullmatch(action.query):
        raise EffectRefused(
            f"query {action.query!r} is refused: a worker may name one message "
            "as id:<external id> and may not search"
        )


async def run(conn, action: ConnectorRead, credential: Credential) -> dict[str, Any]:
    """The body of an item already routed into the action's space."""
    external_id = action.query.removeprefix("id:")
    row = await (
        await conn.execute(
            "SELECT item_id, headers, received_at, routed_by FROM inbound_items "
            "WHERE connector = %s AND account = %s AND external_id = %s "
            "AND space_id = %s",
            ("gmail", action.account, external_id, action.space),
        )
    ).fetchone()
    if row is None:
        raise EffectRefused(
            f"item_not_in_space: {external_id!r} has no row in {action.space!r}"
        )
    item_id, headers, received_at, routed_by = row
    item = InboundItem(
        id=item_id,
        connector="gmail",
        account=action.account,
        external_id=external_id,
        headers=headers,
        received_at=received_at,
        space=action.space,
        routed_by=routed_by,
    )
    body = await fetch_body(conn, item)
    return {"headers": item.headers, "body": body}


async def query(conn, action: ConnectorRead, credential: Credential) -> TargetState:
    """`absent`, always. A read leaves nothing on the target to find, so a
    dangling read is re-run rather than recovered, and no read is ever called
    recovered without its data."""
    return "absent"


if __name__ == "__main__":
    if sys.argv[1:2] == ["authorize"]:
        authorize()
    elif sys.argv[1:2] == ["recent"]:
        for row in recent(sys.argv[2] if len(sys.argv) > 2 else "psyoptimal.com"):
            print(
                f"{row.get('Date', ''):32} {row.get('From', ''):40} {row.get('Subject', '')}"
            )
    else:
        raise SystemExit(__doc__)
