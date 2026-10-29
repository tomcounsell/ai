---
name: mercury-invoicing
description: Create Yudame's client invoices in the company Mercury account (with a pay link), list who hasn't paid, and send the notice and reminders from valor@yuda.me. Use when an invoice is approved and should go to the client, for "who hasn't paid", or for an unpaid reminder.
---

# Mercury invoicing

`mercury.py` beside this file talks to Mercury's API (`https://api.mercury.com/api/v1`)
as Yudame. It prints JSON and calls nothing it does not name.

## The token

Yudame shares credentials through its shared 1Password vaults. The Mercury token is
the item **Mercury API Token** (field `credential`) in the vault **m-valor**,
read with `op read` at each run and held only in memory: never write it to a file,
a commit, a log or a message. `op` signs in with `OP_SERVICE_ACCOUNT_TOKEN`, taken
from the vault `.env` (`~/Desktop/Valor/.env`) when the environment lacks it.

The token is a Custom token whose write scopes need Mercury's IP allowlist, which
holds the Cowboy's public IP, so run it on the Cowboy. A refused call prints
Mercury's answer and this Mac's public IP: when the IP has changed, ask Tom to add
the new one to the token on Mercury's token page. Mercury deletes a token unused for
45 days; the weekly `invoices` routine is its use.

## How an invoice goes out

1. **Tom approves it first.** Show Tom, in his DM, the client, each line, the total
   and the due date (`create SPEC --dry-run ...` prints the lines Mercury will get).
   Create it only after he says OK.
2. **Create.** `python3 mercury.py create SPEC.json --customer-email E --cc A B --account ID`.
   `SPEC.json` is the yudame-invoice spec the PDF was rendered from, so Mercury's
   amount is the PDF's TOTAL DUE. Mercury does not email it (`DontSend`), and the
   invoice is marked as Valor's (`internalNote: valor`), which is what makes it
   eligible for reminders. A number already in Mercury is printed and nothing is
   created. The customer is found by email or created. `accounts` lists the account
   ids; payments go to Yudame's active checking account (••3031). A token without the
   accounts scope is refused `/accounts` (403 `tokenNotInScope`); `accounts` then lists
   the account each existing invoice pays into.
   A spec with a discount or credit (a client's budget line) becomes one services
   line priced net of it, since Mercury does not document negative prices; the PDF
   carries the detail.
3. **Notice.** `python3 mercury.py notice ID` prints an `email.send` effect addressed
   to the invoice's own customer email and cc in Mercury, with the pay link and
   Mercury's PDF written into `.valor/invoices/`. Write it unchanged to
   `.valor/effects/notice-<number>.json`. It leaves from valor@yuda.me without asking
   Tom again.
   The pay link is `https://app.mercury.com/pay/<slug>`; it opened on Mercury's pay page
   with the right number, amount and due date for 250901DF and 251215DF (2026-10-21).
4. **Reminders** are the weekly `invoices` routine (`routines/invoices/`): `remind`
   picks Valor's Unpaid invoices at least a day overdue, at most one reminder per
   invoice every 7 days and 4 in all, reading valor@yuda.me's Sent mail (`gws`) for
   what already went out. An invoice past its fourth reminder is listed for Tom. A
   Sent-mail read that fails stops the run; it never counts as none sent.
   Invoices Tom made in Mercury's dashboard show in `unpaid` and get no reminder.

`unpaid` lists every Unpaid invoice with its customer, days overdue and pay link.
`pdf ID` writes one invoice's PDF.

## Collecting an old balance

Learned on Fuse (UserCRO, Inc.), 2026-10-21.

- **Where the invoices are.** Each client's sent invoices are PDFs in Tom's work vault,
  `<Client>/invoices/PDFs sent/`, or `_archive_/<Client>/invoices/PDFs sent/` for a past
  client. Read them from `origin/main` (`git -C ~/work-vault fetch`, then `git show
  origin/main:<path>`); the local checkout lags and carries Tom's untracked files.
  Numbers are `YYMMDD` + the client code (`DF` for DeckFusion/Fuse), and a revised
  invoice keeps its number with a suffix (`-adjusted`, `_a`), so read the latest one.
  `pdftotext -layout` gives the bill-to, the dates and TOTAL DUE.
- **What was paid.** `received <NAME> --since <date>` lists every incoming payment
  naming the client on every account, archived ones included. Clients put the invoice
  number in the memo, so set each invoice's TOTAL DUE against the payments naming it.
  A total that matches no invoice is a partial payment or a renegotiated rate: show
  Tom the evidence and ask, since Tom's memory of what is owed is approximate.
- **Who to send to.** The invoice PDFs carry no email. Ask Tom, then confirm each
  address from valor@yuda.me's mail (`gws gmail users messages list`, `q: from:<domain>`).
- **The draft Tom approves** is the reconciliation table, each invoice (number, amount,
  due date), the recipients and the email text. Keep the client's original numbers, so
  they match the client's books. A partial balance is one line naming what was billed
  and what was received.
- **Checking a pay link.** The pay page is a single-page app: `curl` gets only the
  shell, and BYOB timed out on it. Headless Chrome renders it:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --no-sandbox --disable-gpu --virtual-time-budget=25000 --window-size=1000,1400 --screenshot=<png> <link>`,
  then read the screenshot for the amount, number and due date.
- **BCC.** `email.send` has no BCC field. When Tom asks for one, send him a separate
  copy, marked as such.

## A client's own invoice skill

A client's invoice skill in work-vault (`.claude/skills/<client>-invoice`) ends
here instead of in a Gmail draft from tom@yuda.me: after Tom approves the PDF, and
once its budget-history row is ready,

1. `create <Client>/invoices/invoice_<n>.json --customer-email <to> --cc <the rest> --account <checking id>`,
   with the addresses that skill's Email line names, shown to Tom first as above
   (his approval of the PDF is the OK when the total matches).
2. `notice <id>` and send it. Then move the PDF into `PDFs sent/` and add the
   budget-history row.
