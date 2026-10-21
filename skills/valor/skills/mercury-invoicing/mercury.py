#!/usr/bin/env python3
"""Yudame's invoices in Mercury: create one from a yudame-invoice spec, list
the unpaid, and build the notice and reminder emails Valor sends from
valor@yuda.me (SKILL.md beside this file says when).

The Mercury token is read from 1Password at each run (`op read`) and lives
only in this process's memory: never in a file, argv, a log, or output.
Stdlib only, so any Python on any Mac runs it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://api.mercury.com/api/v1"
TOKEN_REF = "op://m-valor/Mercury API Token/credential"
PAY = "https://app.mercury.com/pay/{slug}"
MARK = "valor"  # internalNote on every invoice Valor creates
EVERY_DAYS = 7  # at most one reminder per invoice in this many days
MOST = 4  # reminders per invoice; after these the invoice goes to Tom
VAULT_ENV = Path(os.environ.get("VALOR_VAULT_ENV", Path.home() / "Desktop" / "Valor" / ".env"))
SKIPPED = ("defer", "gratis", "included")


class Failed(Exception):
    """A plain message for the person running this; exit 1. `status` is
    Mercury's HTTP status when Mercury refused the call."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


# -- the token --------------------------------------------------------------


def op_env() -> dict[str, str]:
    """The environment `op` runs in: this one, with `OP_SERVICE_ACCOUNT_TOKEN`
    taken from the vault `.env` when absent, as scripts/deploy-site.sh does."""
    env = dict(os.environ)
    if env.get("OP_SERVICE_ACCOUNT_TOKEN"):
        return env
    if VAULT_ENV.is_file():
        for line in VAULT_ENV.read_text().splitlines():
            if line.startswith("OP_SERVICE_ACCOUNT_TOKEN="):
                env["OP_SERVICE_ACCOUNT_TOKEN"] = line.split("=", 1)[1].strip().strip('"')
                return env
    raise Failed(
        f"1Password is not signed in: no OP_SERVICE_ACCOUNT_TOKEN in the environment or in {VAULT_ENV}"
    )


def token() -> str:
    """The Mercury token, as the Authorization header's value."""
    got = subprocess.run(["op", "read", TOKEN_REF], env=op_env(), capture_output=True, text=True, check=False)
    value = got.stdout.strip()
    if got.returncode != 0 or not value:
        raise Failed(
            "no Mercury token in 1Password: vault 'm-valor', item 'Mercury API Token', field 'credential'"
        )
    return "Bearer " + (value if value.startswith("secret-token:") else "secret-token:" + value)


# -- Mercury ----------------------------------------------------------------


def public_ip() -> str:
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as r:
            return r.read().decode().strip()
    except OSError:
        return "unknown"


def call(method: str, path: str, body: dict | None = None, *, raw: bool = False):
    """One Mercury request; its JSON, or its bytes when `raw`. A refusal
    names Mercury's status and body and this Mac's public IP (the token's IP
    allowlist), never the token."""
    req = urllib.request.Request(
        API + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": token(),
            "Accept": "application/pdf" if raw else "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise Failed(
            f"Mercury {method} {path}: {e.code} {detail} (this Mac's public IP: {public_ip()})",
            e.code,
        ) from None
    except urllib.error.URLError as e:
        raise Failed(f"Mercury {method} {path}: {e.reason}") from None
    return data if raw else json.loads(data)


def paged(path: str, key: str) -> list[dict]:
    """Every item of a paginated list, following `page.nextPage`."""
    out, after = [], None
    while True:
        got = call("GET", f"{path}?limit=1000" + (f"&start_after={after}" if after else ""))
        out += got.get(key) or []
        after = (got.get("page") or {}).get("nextPage")
        if not after:
            return out


def find_customer(email: str) -> dict | None:
    """The live customer with this email; deleted customers are skipped."""
    want = email.strip().lower()
    for c in paged("/ar/customers", "customers"):
        if not c.get("deletedAt") and (c.get("email") or "").strip().lower() == want:
            return c
    return None


def pay_link(inv: dict) -> str:
    return PAY.format(slug=inv["slug"])


def overdue_days(inv: dict, today: date) -> int:
    return (today - date.fromisoformat(inv["dueDate"])).days


# -- the spec ---------------------------------------------------------------


def cents(x) -> int:
    return round(float(x) * 100)


def money(c: int) -> str:
    return f"${c / 100:,.2f}"


def lines(spec: dict) -> tuple[list[dict], int]:
    """Mercury line items for a yudame-invoice spec, and the total in cents,
    which is the rendered PDF's TOTAL DUE (render_invoice.build's math: the
    budget discount, then the adjustments). No line has a negative price,
    since Mercury does not document accepting one: when the spec has a
    discount or credit, the sections become one line priced net of them."""
    sections = []
    for s in spec["sections"]:
        total = 0
        for it in s.get("items", []):
            amount, qty, rate = it.get("amount"), it.get("qty"), it.get("rate")
            if amount is None and qty is not None and rate is not None:
                amount = round(float(qty) * float(rate), 2)
            if amount is None or amount in SKIPPED or it.get("memo"):
                continue
            total += cents(amount)
        sections.append((s.get("title") or "Services", total))
    labor = sum(t for _, t in sections)
    adjustments = [(a["label"], cents(a["amount"]), a.get("qty")) for a in spec.get("adjustments", [])]
    budget = spec.get("budget")
    if budget is not None and labor > cents(budget):
        label = spec.get("budget_label") or f"adjustment on ${budget / 1000:g}k budget"
        adjustments.insert(0, (label, cents(budget) - labor, None))
    credits = [(label, c) for label, c, _ in adjustments if c < 0]
    if credits:
        net = labor + sum(c for _, c in credits)
        if net < 0:
            raise Failed(f"invoice {spec['number']}: the credits exceed the labor")
        less = ", ".join(f"less {label} {money(-c)}" for label, c in credits)
        out = [
            {
                "name": f"Services per invoice {spec['number']}: {money(labor)} {less}",
                "unitPrice": net / 100,
                "quantity": 1,
            }
        ]
    else:
        out = [{"name": title, "unitPrice": t / 100, "quantity": 1} for title, t in sections if t]
    for label, c, qty in adjustments:
        if c > 0:
            out.append({"name": f"{label} ({qty})" if qty else label, "unitPrice": c / 100, "quantity": 1})
    return out, labor + sum(c for _, c, _ in adjustments)


def body(spec: dict, customer_id: str, account: str, cc: list[str]) -> dict:
    items, _ = lines(spec)
    return {
        "customerId": customer_id,
        "invoiceDate": spec["date"],
        "dueDate": spec["due_date"],
        "invoiceNumber": spec["number"],
        "ccEmails": cc,
        "destinationAccountId": account,
        "lineItems": items,
        "achDebitEnabled": True,
        "creditCardEnabled": False,
        "useRealAccountNumber": False,
        "sendEmailOption": "DontSend",
        "internalNote": MARK,
    }


# -- emails -----------------------------------------------------------------


def write_pdf(inv: dict, folder: Path) -> dict:
    """Mercury's PDF of the invoice, written as a regular file in `folder`
    (inside the task's workspace), as an `email.send` file entry."""
    data = call("GET", f"/ar/invoices/{inv['id']}/pdf", raw=True)
    folder = folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"invoice_{inv['invoiceNumber']}.pdf"
    path.unlink(missing_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def email(inv: dict, customer: dict, folder: Path, *, reminder: bool, today: date) -> dict:
    """The `email.send` effect for an invoice's notice or reminder, addressed
    from the invoice's own customer and cc in Mercury."""
    n, amount, due = inv["invoiceNumber"], money(cents(inv["amount"])), inv["dueDate"]
    hello = f"Hello {customer['name']},\n\n"
    if reminder:
        subject = f"Reminder: Invoice {n} from Yudame"
        late = overdue_days(inv, today)
        text = (
            f"Yudame's invoice {n} for {amount} was due on {due}, {late} day{'s' * (late != 1)} ago, "
            "and our records show it unpaid. The invoice is attached."
        )
        tail = "\n\nIf it is already on its way, thank you, and please ignore this note."
    else:
        subject = f"Invoice {n} from Yudame"
        text = f"Yudame's invoice {n} for {amount} is attached. It is due on {due}."
        tail = ""
    to = [customer["email"].strip().lower()]
    payload = {
        "to": to,
        "cc": [c.strip().lower() for c in inv.get("ccEmails") or []],
        "subject": subject,
        "body": f"{hello}{text}\n\nPay by bank transfer here: {pay_link(inv)}{tail}\n\nThank you,\nValor Engels\nYudame, Inc.\n",
        "in_reply_to": None,
        "references": [],
        "files": [write_pdf(inv, folder)],
    }
    return {"action_type": "email.send", "target": ",".join(sorted(to)), "payload": payload}


def reminders_sent(number: str) -> list[datetime]:
    """When each reminder for invoice `number` left valor@yuda.me, from its
    Sent mail (gws). A failed read is an error, never zero."""
    q = f'in:sent subject:"Reminder: Invoice {number}"'
    listed = gws("gmail", "users", "messages", "list", params={"userId": "me", "q": q, "maxResults": 50})
    out = []
    for m in listed.get("messages") or []:
        got = gws(
            "gmail", "users", "messages", "get", params={"userId": "me", "id": m["id"], "format": "minimal"}
        )
        out.append(datetime.fromtimestamp(int(got["internalDate"]) / 1000, timezone.utc))  # noqa: UP017  (Python 3.9: macOS python3)
    return out


def gws(*args: str, params: dict) -> dict:
    got = subprocess.run(
        ["gws", *args, "--params", json.dumps(params)], capture_output=True, text=True, check=False
    )
    try:
        if got.returncode == 0:
            return json.loads(got.stdout)
    except ValueError:
        pass
    raise Failed(
        f"gws {' '.join(args)} failed, so the reminders already sent cannot be read: {got.stderr.strip()[:300]}"
    )


# -- commands ---------------------------------------------------------------


def received(name: str, since: str) -> list[dict]:
    """Every incoming payment since `since` (YYYY-MM-DD) on every account,
    archived ones included, whose counterparty or memo names `name`
    (case-insensitive), oldest first. A client's memo usually names the
    invoice it pays, which is how a balance is reconciled."""
    want, out = name.lower(), []
    for a in call("GET", "/accounts")["accounts"]:
        offset = 0
        while True:
            got = call("GET", f"/account/{a['id']}/transactions?start={since}&limit=500&offset={offset}")
            txs = got.get("transactions") or []
            for t in txs:
                text = " ".join(
                    str(t.get(k) or "") for k in ("counterpartyName", "bankDescription", "externalMemo")
                )
                if (t.get("amount") or 0) > 0 and want in text.lower():
                    out.append({
                        "date": (t.get("postedAt") or t.get("createdAt") or "")[:10], "amount": t["amount"],
                        "from": t.get("counterpartyName"), "memo": t.get("externalMemo") or t.get("bankDescription"),
                        "account": a.get("name"),
                    })  # fmt: skip
            if len(txs) < 500:
                break
            offset += 500
    return sorted(out, key=lambda x: x["date"])


def unpaid(today: date) -> list[dict]:
    """Every Unpaid invoice, joined to its customer."""
    customers = {c["id"]: c for c in paged("/ar/customers", "customers")}
    out = []
    for inv in paged("/ar/invoices", "invoices"):
        if inv.get("status") != "Unpaid":
            continue
        c = customers.get(inv["customerId"]) or {}
        out.append(
            {
                "id": inv["id"],
                "number": inv["invoiceNumber"],
                "customer": c.get("name"),
                "email": c.get("email"),
                "cc": inv.get("ccEmails") or [],
                "amount": inv["amount"],
                "due": inv["dueDate"],
                "days_overdue": overdue_days(inv, today),
                "pay_link": pay_link(inv),
                "valors": inv.get("internalNote") == MARK,
            }
        )
    return out


def create(spec: dict, email_to: str, cc: list[str], account: str) -> dict:
    number = spec["number"]
    for inv in paged("/ar/invoices", "invoices"):
        if inv.get("invoiceNumber") == number:
            return {"existing": True, **summary(inv)}
    customer = find_customer(email_to) or call(
        "POST", "/ar/customers", {"name": spec["client"]["name"], "email": email_to}
    )
    inv = call("POST", "/ar/invoices", body(spec, customer["id"], account, cc))
    _, total = lines(spec)
    out = {"existing": False, **summary(inv)}
    if cents(inv["amount"]) != total:
        out["warning"] = (
            f"Mercury's amount {money(cents(inv['amount']))} is not the spec's total {money(total)}"
        )
    return out


def summary(inv: dict) -> dict:
    return {
        "id": inv["id"],
        "number": inv["invoiceNumber"],
        "status": inv["status"],
        "amount": inv["amount"],
        "due": inv["dueDate"],
        "pay_link": pay_link(inv),
    }


def remind(today: date, now: datetime, folder: Path) -> dict:
    """The reminders due: Valor's Unpaid invoices at least a day overdue,
    none reminded in the last EVERY_DAYS days, fewer than MOST in all.
    Those past MOST are listed for Tom."""
    send, for_tom = [], []
    customers = None
    for inv in paged("/ar/invoices", "invoices"):
        if inv.get("status") != "Unpaid" or inv.get("internalNote") != MARK or overdue_days(inv, today) < 1:
            continue
        sent = reminders_sent(inv["invoiceNumber"])
        if len(sent) >= MOST:
            for_tom.append({**summary(inv), "reminders": len(sent), "days_overdue": overdue_days(inv, today)})
            continue
        if sent and now - max(sent) < timedelta(days=EVERY_DAYS):
            continue
        customers = customers or {c["id"]: c for c in paged("/ar/customers", "customers")}
        send.append(email(inv, customers[inv["customerId"]], folder, reminder=True, today=today))
    return {"send": send, "for_tom": for_tom}


def accounts() -> list[dict]:
    """The accounts payments can go to. A token without the accounts scope
    is refused `/accounts` (403), so then each account an existing invoice
    pays into, with the invoices that use it, since every invoice carries
    its `destinationAccountId`."""
    try:
        return [
            {k: x.get(k) for k in ("id", "name", "nickname", "kind", "type", "status")}
            for x in call("GET", "/accounts")["accounts"]
        ]
    except Failed as e:
        if e.status != 403:
            raise
    used: dict[str, list[str]] = {}
    for inv in paged("/ar/invoices", "invoices"):
        used.setdefault(inv["destinationAccountId"], []).append(inv["invoiceNumber"])
    if not used:
        raise Failed("the token cannot read /accounts and no invoice names an account yet")
    return [{"id": a, "from_invoices": ns} for a, ns in used.items()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser(
        "create", help="create a Mercury invoice from a yudame-invoice spec (not emailed by Mercury)"
    )
    c.add_argument("spec")
    c.add_argument("--customer-email", required=True)
    c.add_argument("--cc", nargs="*", default=[])
    c.add_argument("--account", required=True, help="destinationAccountId (see `accounts`)")
    c.add_argument("--dry-run", action="store_true", help="print the line items and total; call nothing")
    sub.add_parser("unpaid", help="every Unpaid invoice, with days overdue and pay link")
    for name in ("remind", "notice", "pdf"):
        s = sub.add_parser(name)
        if name != "remind":
            s.add_argument("id")
        s.add_argument(
            "--dir", default=".valor/invoices", help="where the PDFs are written (inside the workspace)"
        )
    sub.add_parser("accounts", help="the account ids payments can go to")
    g = sub.add_parser("received", help="incoming payments naming a client, to reconcile its invoices")
    g.add_argument("name", help="matched against counterparty and memo, e.g. USERCRO")
    g.add_argument("--since", default="2024-01-01")
    a = p.parse_args(argv)
    now = datetime.now(timezone.utc)  # noqa: UP017
    today = now.date()
    try:
        if a.cmd == "create":
            spec = json.loads(Path(a.spec).read_text())
            if a.dry_run:
                items, total = lines(spec)
                out = {"lineItems": items, "total": total / 100}
            else:
                out = create(spec, a.customer_email, a.cc, a.account)
        elif a.cmd == "received":
            out = received(a.name, a.since)
        elif a.cmd == "unpaid":
            out = unpaid(today)
        elif a.cmd == "remind":
            out = remind(today, now, Path(a.dir))
        elif a.cmd == "notice":
            inv = call("GET", f"/ar/invoices/{a.id}")
            customer = call("GET", f"/ar/customers/{inv['customerId']}")
            out = email(inv, customer, Path(a.dir), reminder=False, today=today)
        elif a.cmd == "pdf":
            inv = call("GET", f"/ar/invoices/{a.id}")
            out = write_pdf(inv, Path(a.dir))
        else:
            out = accounts()
    except Failed as e:
        print(f"mercury: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
