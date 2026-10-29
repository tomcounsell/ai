"""Mercury invoicing (skills/valor/skills/mercury-invoicing): the line items
against the rendered TOTAL DUE, the create body, the customer lookup, the
unpaid list, the reminder set and its emails, the token, and the weekly
`invoices` routine. Mercury, `op` and `gws` are fakes shaped as their docs
describe."""

import asyncio
import hashlib
import importlib.util
import io
import json
import subprocess
import urllib.error
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from core import db, machine, routines
from tests import bridges
from tests.bridges import of_type

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "mercury", ROOT / "skills/valor/skills/mercury-invoicing/mercury.py"
)
mercury = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mercury)

TODAY = date(2026, 11, 20)
NOW = datetime(2026, 11, 20, 2, 0, tzinfo=UTC)
SECRET = "secret-token:mercury-test-0123456789"


def section(title, *items):
    return {"title": title, "items": [dict(i) for i in items]}


def spec(**more):
    """A Northwind-shaped spec: labor 10,406.50 over four sections, with lines
    the renderer leaves out of the subtotal."""
    base = {
        "number": "260901CA",
        "client": {"name": "Northwind AI LLC", "address": ["1 Example Rd"]},
        "date": "2026-09-01",
        "due_date": "2026-09-10",
        "sections": [
            section("Project Management - P", {"qty": 18, "rate": 60}),
            section(
                "CTO - Tom Counsell",
                {"qty": 19, "rate": 389},
                {"qty": 0.5, "rate": 389, "amount": "included"},
                {"qty": 2, "rate": 389, "amount": "defer"},
            ),
            section("Senior Developer - Valor", {"qty": 18.9, "rate": 70}, {"amount": 9, "memo": True}),
            section("Marketing - C", {"qty": 12.25, "rate": 50}),
        ],
        "adjustments": [],
    }
    return {**base, **more}


def total(items):
    return round(sum(i["unitPrice"] * i["quantity"] for i in items), 2)


# -- line items -----------------------------------------------------------------


def test_a_budget_invoice_is_one_net_services_line_plus_pass_throughs():
    s = spec(budget=8000, adjustments=[{"label": "OpenAI Token Spend", "qty": "34M", "amount": 3.61}])
    items, cents = mercury.lines(s)
    assert cents == 800361 and total(items) == 8003.61  # 260901CA's sent total
    assert [i["unitPrice"] for i in items] == [8000.0, 3.61]
    assert "$10,406.50 less adjustment on $8k budget $2,406.50" in items[0]["name"]
    assert items[1]["name"] == "OpenAI Token Spend (34M)"
    assert all(i["unitPrice"] >= 0 for i in items)


def test_several_adjustments_sum_to_the_total_due_with_no_negative_price():
    s = spec(
        budget=9000,
        adjustments=[
            {"label": "OpenAI", "amount": 3.61},
            {"label": "Credit for June", "amount": -250},
            {"label": "Hosting", "amount": 120.4},
        ],
    )
    items, cents = mercury.lines(s)
    # render_invoice.build: 10,406.50 + (9,000 - 10,406.50) + 3.61 - 250 + 120.40
    assert cents == 887401 and total(items) == 8874.01
    assert all(i["unitPrice"] >= 0 for i in items)
    assert "less Credit for June $250.00" in items[0]["name"]


def test_under_budget_bills_each_section_with_no_adjustment():
    items, cents = mercury.lines(spec(budget=20000))
    assert [i["name"] for i in items] == [
        "Project Management - P",
        "CTO - Tom Counsell",
        "Senior Developer - Valor",
        "Marketing - C",
    ]
    assert [i["unitPrice"] for i in items] == [1080.0, 7391.0, 1323.0, 612.5]
    assert cents == 1040650


def test_at_exactly_budget_the_total_is_the_budget():
    s = spec(budget=8000, sections=[section("CTO - T", {"qty": 20, "rate": 400})])
    items, cents = mercury.lines(s)
    assert (cents, items) == (800000, [{"name": "CTO - T", "unitPrice": 8000.0, "quantity": 1}])


def test_credits_beyond_the_labor_are_refused():
    with pytest.raises(mercury.Failed, match="credits exceed"):
        mercury.lines(spec(adjustments=[{"label": "Refund", "amount": -20000}]))


# -- a fake Mercury -------------------------------------------------------------


def inv(n, *, status="Unpaid", due="2026-11-10", note="valor", customer="c-1", amount=8003.61, **more):
    return {
        "id": f"i-{n}",
        "invoiceNumber": n,
        "customerId": customer,
        "status": status,
        "amount": amount,
        "dueDate": due,
        "invoiceDate": "2026-11-01",
        "slug": f"slug-{n}",
        "ccEmails": ["someone@client.example"],
        "internalNote": note,
        **more,
    }


class Mercury:
    """GET and POST on the documented paths; lists in pages of `size`."""

    def __init__(self, invoices=(), customers=(), size=2):
        self.invoices, self.customers, self.size = list(invoices), list(customers), size
        self.posts, self.paths = [], []

    def page(self, items, key, path):
        start = 0
        if "start_after=" in path:
            after = path.split("start_after=")[1]
            start = next(i for i, x in enumerate(items) if x["id"] == after) + 1
        chunk = items[start : start + self.size]
        more = start + self.size < len(items)
        return {key: chunk, "page": {"nextPage": chunk[-1]["id"] if more else None, "previousPage": None}}

    def __call__(self, method, path, body=None, *, raw=False):
        self.paths.append((method, path))
        if method == "POST":
            self.posts.append((path, body))
            if path == "/ar/customers":
                c = {"id": "c-new", **body, "address": None, "deletedAt": None}
                self.customers.append(c)
                return c
            made = inv(body["invoiceNumber"], customer=body["customerId"], amount=total(body["lineItems"]))
            self.invoices.append(made)
            return made
        if path.endswith("/pdf"):
            return b"%PDF-1.4 " + path.encode()
        if path.startswith("/ar/invoices?"):
            return self.page(self.invoices, "invoices", path)
        if path.startswith("/ar/customers?"):
            return self.page(self.customers, "customers", path)
        if path.startswith("/ar/invoices/"):
            return next(i for i in self.invoices if i["id"] == path.rsplit("/", 1)[1])
        return next(c for c in self.customers if c["id"] == path.rsplit("/", 1)[1])


NORTHWIND = {"id": "c-1", "name": "Northwind AI LLC", "email": "someone@client.example", "deletedAt": None}


@pytest.fixture
def fake(monkeypatch):
    def make(**kw):
        m = Mercury(**kw)
        monkeypatch.setattr(mercury, "call", m)
        return m

    return make


# -- create ---------------------------------------------------------------------


def test_create_posts_every_required_field_and_mercury_sends_nothing(fake):
    m = fake(customers=[{"id": "c-0", "name": "Other", "email": "a@b.c", "deletedAt": None}, NORTHWIND])
    s = spec(budget=8000, adjustments=[{"label": "OpenAI", "amount": 3.61}])
    out = mercury.create(s, "someone@client.example", ["someone@client.example"], "acct-1")
    ((path, body),) = m.posts
    assert path == "/ar/invoices"
    required = {
        "customerId",
        "invoiceDate",
        "dueDate",
        "ccEmails",
        "destinationAccountId",
        "lineItems",
        "achDebitEnabled",
        "creditCardEnabled",
        "useRealAccountNumber",
    }
    assert required <= set(body)
    assert body["customerId"] == "c-1"  # found on the second page, case-insensitive
    assert (body["sendEmailOption"], body["internalNote"], body["invoiceNumber"]) == (
        "DontSend",
        "valor",
        "260901CA",
    )
    assert (body["invoiceDate"], body["dueDate"], body["ccEmails"]) == (
        "2026-09-01",
        "2026-09-10",
        ["someone@client.example"],
    )
    assert (body["destinationAccountId"], body["achDebitEnabled"], body["creditCardEnabled"]) == (
        "acct-1",
        True,
        False,
    )
    assert body["useRealAccountNumber"] is False
    assert out["pay_link"] == "https://app.mercury.com/pay/slug-260901CA"
    assert out["existing"] is False and "warning" not in out


def test_an_invoice_number_already_in_mercury_is_printed_and_nothing_posted(fake):
    m = fake(invoices=[inv("A"), inv("B"), inv("260901CA", status="Paid")], customers=[NORTHWIND])
    out = mercury.create(spec(), "someone@client.example", [], "acct-1")
    assert m.posts == []
    assert (out["existing"], out["id"], out["status"]) == (True, "i-260901CA", "Paid")


def test_a_deleted_customer_with_the_email_is_skipped_and_a_new_one_made(fake):
    gone = {**NORTHWIND, "id": "c-gone", "deletedAt": "2026-10-01T00:00:00Z"}
    m = fake(customers=[gone])
    mercury.create(spec(), "someone@client.example", [], "acct-1")
    (cust_path, cust), (inv_path, body) = m.posts
    assert (cust_path, cust) == (
        "/ar/customers",
        {"name": "Northwind AI LLC", "email": "someone@client.example"},
    )
    assert (inv_path, body["customerId"]) == ("/ar/invoices", "c-new")


def test_a_mercury_amount_that_differs_from_the_spec_is_named(fake, monkeypatch):
    m = fake(customers=[NORTHWIND])
    real = m.__call__

    def short(method, path, body=None, *, raw=False):
        got = real(method, path, body, raw=raw)
        return {**got, "amount": 1.0} if method == "POST" and path == "/ar/invoices" else got

    monkeypatch.setattr(mercury, "call", short)
    assert (
        "$1.00 is not the spec's total"
        in mercury.create(spec(), "someone@client.example", [], "a")["warning"]
    )


# -- unpaid ---------------------------------------------------------------------


def test_unpaid_lists_only_unpaid_across_every_page_with_days_overdue(fake):
    fake(
        invoices=[
            inv("P", status="Paid"),
            inv("X", status="Cancelled"),
            inv("R", status="Processing"),
            inv("LATE"),
            inv("TODAY", due="2026-11-20"),
            inv("SOON", due="2026-11-25", note=None),
        ],
        customers=[NORTHWIND],
    )
    got = {u["number"]: u for u in mercury.unpaid(TODAY)}
    assert set(got) == {"LATE", "TODAY", "SOON"}
    assert [got[n]["days_overdue"] for n in ("LATE", "TODAY", "SOON")] == [10, 0, -5]
    assert got["LATE"]["pay_link"] == "https://app.mercury.com/pay/slug-LATE"
    assert (got["LATE"]["valors"], got["SOON"]["valors"]) == (True, False)
    assert (got["LATE"]["customer"], got["LATE"]["email"]) == ("Northwind AI LLC", "someone@client.example")


# -- reminders ------------------------------------------------------------------


def sent(**by_number):
    """A fake `reminders_sent`: the times reminders for each number left."""
    return lambda n: [NOW - timedelta(days=d) for d in by_number.get(n, [])]


def test_remind_picks_valors_overdue_invoices_not_reminded_this_week(fake, monkeypatch, tmp_path):
    fake(
        invoices=[
            inv("NEW"),
            inv("WEEK", due="2026-10-01"),
            inv("RECENT", due="2026-10-01"),
            inv("DASH", note=None),
            inv("TODAY", due="2026-11-20"),
            inv("PAID", status="Paid"),
            inv("MANY", due="2026-09-01"),
        ],
        customers=[NORTHWIND],
    )
    monkeypatch.setattr(mercury, "reminders_sent", sent(WEEK=[8], RECENT=[3], MANY=[1, 8, 15, 22]))
    out = mercury.remind(TODAY, NOW, tmp_path / "invoices")
    assert [e["payload"]["subject"] for e in out["send"]] == [
        "Reminder: Invoice NEW from Yudame",
        "Reminder: Invoice WEEK from Yudame",
    ]
    assert [(t["number"], t["reminders"]) for t in out["for_tom"]] == [("MANY", 4)]


def test_a_reminder_is_addressed_from_the_invoice_itself_with_its_pdf(fake, monkeypatch, tmp_path):
    fake(invoices=[inv("NEW")], customers=[NORTHWIND])
    monkeypatch.setattr(mercury, "reminders_sent", sent())
    monkeypatch.chdir(tmp_path)
    ((effect,),) = [mercury.remind(TODAY, NOW, Path(".valor/invoices"))["send"]]
    p = effect["payload"]
    assert (effect["action_type"], effect["target"]) == ("email.send", "someone@client.example")
    assert (p["to"], p["cc"]) == (["someone@client.example"], ["someone@client.example"])
    assert "https://app.mercury.com/pay/slug-NEW" in p["body"] and "$8,003.61" in p["body"]
    assert "10 days ago" in p["body"] and p["body"].startswith("Hello Northwind AI LLC,")
    ((f),) = p["files"]
    path = Path(f["path"])
    assert path.is_absolute() and path == (tmp_path / ".valor/invoices/invoice_NEW.pdf").resolve()
    assert path.is_file() and not path.is_symlink()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == f["sha256"]


def test_a_notice_names_the_due_date_and_no_lateness(fake, tmp_path):
    fake(invoices=[inv("N1", due="2026-12-01")], customers=[NORTHWIND])
    e = mercury.email(
        mercury.call("GET", "/ar/invoices/i-N1"), NORTHWIND, tmp_path, reminder=False, today=TODAY
    )
    assert e["payload"]["subject"] == "Invoice N1 from Yudame"
    assert "due on 2026-12-01" in e["payload"]["body"] and "ago" not in e["payload"]["body"]


def run_result(code=0, out="", err=""):
    return subprocess.CompletedProcess([], code, out, err)


def test_reminders_sent_reads_valors_sent_mail_through_gws(monkeypatch):
    calls = []

    def gws(argv, **kw):
        calls.append(argv)
        params = json.loads(argv[argv.index("--params") + 1])
        if argv[4] == "list":
            return run_result(out=json.dumps({"messages": [{"id": "m1"}, {"id": "m2"}]}))
        ms = {"m1": NOW - timedelta(days=2), "m2": NOW - timedelta(days=9)}[params["id"]]
        return run_result(out=json.dumps({"internalDate": str(int(ms.timestamp() * 1000))}))

    monkeypatch.setattr(mercury.subprocess, "run", gws)
    got = mercury.reminders_sent("261101CA")
    assert got == [NOW - timedelta(days=2), NOW - timedelta(days=9)]
    q = json.loads(calls[0][calls[0].index("--params") + 1])["q"]
    assert q == 'in:sent subject:"Reminder: Invoice 261101CA"'


def test_a_failed_sent_mail_read_stops_the_reminders(fake, monkeypatch, tmp_path):
    fake(invoices=[inv("NEW")], customers=[NORTHWIND])
    monkeypatch.setattr(mercury.subprocess, "run", lambda *a, **k: run_result(1, "", "invalid_grant"))
    with pytest.raises(mercury.Failed, match="cannot be read: invalid_grant"):
        mercury.remind(TODAY, NOW, tmp_path)


# -- the token ------------------------------------------------------------------


class Op:
    def __init__(self, value=SECRET, code=0):
        self.value, self.code, self.envs = value, code, []

    def __call__(self, argv, env=None, **kw):
        assert argv == ["op", "read", "op://m-valor/Mercury API Token/credential"]
        self.envs.append(env)
        return run_result(self.code, self.value + "\n" if self.code == 0 else "", "isn't an item")


def test_the_token_is_read_per_call_with_the_prefix_once(monkeypatch):
    op = Op()
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "ops_x")
    monkeypatch.setattr(mercury.subprocess, "run", op)
    assert mercury.token() == mercury.token() == "Bearer " + SECRET
    assert len(op.envs) == 2
    monkeypatch.setattr(mercury.subprocess, "run", Op("bare-value"))
    assert mercury.token() == "Bearer secret-token:bare-value"


def test_the_service_sign_in_comes_from_the_vault_env_when_absent(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text('OTHER=1\nOP_SERVICE_ACCOUNT_TOKEN="ops_from_vault"\n')
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setattr(mercury, "VAULT_ENV", env)
    op = Op()
    monkeypatch.setattr(mercury.subprocess, "run", op)
    mercury.token()
    assert op.envs[0]["OP_SERVICE_ACCOUNT_TOKEN"] == "ops_from_vault"


def test_a_missing_service_sign_in_is_named(monkeypatch, tmp_path):
    monkeypatch.delenv("OP_SERVICE_ACCOUNT_TOKEN", raising=False)
    monkeypatch.setattr(mercury, "VAULT_ENV", tmp_path / "absent.env")
    with pytest.raises(mercury.Failed, match="1Password is not signed in.*absent.env"):
        mercury.token()


def test_a_missing_item_is_named(monkeypatch):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "ops_x")
    monkeypatch.setattr(mercury.subprocess, "run", Op(code=1))
    with pytest.raises(mercury.Failed, match="vault 'm-valor', item 'Mercury API Token'"):
        mercury.token()


@pytest.mark.parametrize("code", [401, 403])
def test_a_refused_call_names_the_ip_and_never_the_token(monkeypatch, capsys, code):
    monkeypatch.setenv("OP_SERVICE_ACCOUNT_TOKEN", "ops_x")
    monkeypatch.setattr(mercury.subprocess, "run", Op())
    seen = []

    def urlopen(req, timeout=None):
        if "ipify" in str(getattr(req, "full_url", req)):
            return io.BytesIO(b"203.0.113.9")
        seen.append(req.get_header("Authorization"))
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b'{"errors":"ip not allowed"}'))

    monkeypatch.setattr(mercury.urllib.request, "urlopen", urlopen)
    assert mercury.main(["unpaid"]) == 1
    out = capsys.readouterr()
    assert seen == ["Bearer " + SECRET]
    assert f"{code}" in out.err and "ip not allowed" in out.err and "203.0.113.9" in out.err
    assert "mercury-test" not in out.out + out.err


# -- accounts ---------------------------------------------------------------------


def test_the_token_is_tom_s_item_in_m_valor():
    assert mercury.TOKEN_REF == "op://m-valor/Mercury API Token/credential"


def test_accounts_lists_mercury_s_accounts_when_the_token_may_read_them(monkeypatch):
    acct = {"id": "a-1", "name": "Checking ••1234", "kind": "checking", "type": "mercury", "status": "active"}
    monkeypatch.setattr(mercury, "call", lambda m, path, *a, **k: {"accounts": [acct], "page": {}})
    assert mercury.accounts() == [{**acct, "nickname": None}]


def test_accounts_out_of_scope_come_from_the_existing_invoices(fake, monkeypatch):
    m = fake(invoices=[inv("A", destinationAccountId="a-1"), inv("B", destinationAccountId="a-1"),
                       inv("C", destinationAccountId="a-2")])  # fmt: skip
    real = m.__call__

    def scoped(method, path, body=None, *, raw=False):
        if path == "/accounts":
            raise mercury.Failed("Mercury GET /accounts: 403 tokenNotInScope", 403)
        return real(method, path, body, raw=raw)

    monkeypatch.setattr(mercury, "call", scoped)
    assert mercury.accounts() == [
        {"id": "a-1", "from_invoices": ["A", "B"]},
        {"id": "a-2", "from_invoices": ["C"]},
    ]


def test_accounts_other_refusals_are_not_hidden(monkeypatch):
    def down(*a, **k):
        raise mercury.Failed("Mercury GET /accounts: 500", 500)

    monkeypatch.setattr(mercury, "call", down)
    with pytest.raises(mercury.Failed, match="500"):
        mercury.accounts()


# -- the weekly routine -----------------------------------------------------------


VALOR = 'name = "valor"\nrepo = "https://example.invalid/valor.git"\nkind = "plain"\nsuite = "true"\n'


def fire(dsn, tmp_path, placed):
    projects = tmp_path / "projects"
    projects.mkdir(exist_ok=True)
    (projects / "valor.toml").write_text(VALOR + f'machine = "{placed}"\n')
    started = []

    async def start_project(conn, **kw):
        started.append(kw)
        return "t-run"

    async def go():
        async with await db.connect(dsn) as conn:
            return await routines.run(
                conn,
                "invoices",
                {"task": routines.task_runner},
                start_project=start_project,
                directory=ROOT / "routines",
                dsn=dsn,
            )

    with bridges.configure(machine="m-here", default_machine="m-here", projects_dir=str(projects)):
        asyncio.run(go())
    return started


def test_the_invoices_routine_starts_one_sdlc_task_on_the_placed_mac(fresh, tmp_path):
    (kw,) = fire(fresh, tmp_path, "m-here")
    r = routines.load("invoices", ROOT / "routines")
    assert (kw["instruction"], kw["ceiling"], kw["project"]) == (r.instruction, "act", "valor")
    assert "mercury.py remind" in kw["instruction"] and "stop.md" in kw["instruction"]
    assert machine.fold([{"id": 1, "type": "task.started", "payload": kw["marker"]}]).legacy is False
    (ran,) = asyncio.run(of_type(fresh, "routine.ran"))
    assert (ran["run"], ran["outcome"]) == ("t-run", "started")
    assert r.schedule == {"weekday": 1, "hour": 9, "minute": 0}


def test_the_invoices_routine_starts_nothing_on_another_mac(fresh, tmp_path):
    assert fire(fresh, tmp_path, "m-cowboy") == []
    (ran,) = asyncio.run(of_type(fresh, "routine.ran"))
    assert (ran["run"], ran["outcome"]) == (None, "nothing_due")
    assert "not placed here" in ran["summary"] and "m-cowboy" in ran["summary"]


def test_received_lists_a_client_s_incoming_payments_on_every_account_oldest_first(monkeypatch):
    accounts = [{"id": "old", "name": "Checking ••6834"}, {"id": "new", "name": "Checking ••3031"}]
    txs = {
        "old": [{"amount": 10000.0, "postedAt": "2025-06-09T00:00:00Z", "counterpartyName": "USERCRO INC",
                 "externalMemo": "Invoice: 250601DF. DeckFusion."}],
        "new": [
            {"amount": 6000.0, "postedAt": "2025-09-11T00:00:00Z", "counterpartyName": "USERCRO INC",
             "externalMemo": "INVOICE: 250901DF. Fuse."},
            {"amount": -50.0, "postedAt": "2025-09-12T00:00:00Z", "counterpartyName": "UserCRO refund"},
            {"amount": 8000.0, "postedAt": "2025-09-08T00:00:00Z", "counterpartyName": "NORTHWIND AI LLC"},
        ],
    }  # fmt: skip
    seen = []

    def call(method, path, *a, **k):
        seen.append(path)
        if path == "/accounts":
            return {"accounts": accounts}
        return {"transactions": txs[path.split("/")[2]]}

    monkeypatch.setattr(mercury, "call", call)
    got = mercury.received("usercro", "2024-05-01")
    assert [(g["date"], g["amount"], g["memo"]) for g in got] == [
        ("2025-06-09", 10000.0, "Invoice: 250601DF. DeckFusion."),
        ("2025-09-11", 6000.0, "INVOICE: 250901DF. Fuse."),
    ]
    assert "/account/old/transactions?start=2024-05-01&limit=500&offset=0" in seen
