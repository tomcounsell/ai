---
name: mercury-invoicing
description: Create Yudame's client invoices in the company Mercury account (with a pay link), list who hasn't paid, and send the notice and reminders from valor@yuda.me. Use when an invoice is approved and should go to the client, for "who hasn't paid", or for an unpaid reminder.
---

# Mercury invoicing

`mercury.py` beside this file talks to Mercury's API (`https://api.mercury.com/api/v1`)
as Yudame. It prints JSON and calls nothing it does not name.

## The token

Yudame shares credentials through its shared 1Password vaults. The Mercury token is
the item **Mercury API Token** (field `credential`) in the vault **Yudame Secrets**,
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
   ids; payments go to Yudame's checking account.
   A spec with a discount or credit (the Cyndra budget line) becomes one services
   line priced net of it, since Mercury does not document negative prices; the PDF
   carries the detail.
3. **Notice.** `python3 mercury.py notice ID` prints an `email.send` effect addressed
   to the invoice's own customer email and cc in Mercury, with the pay link and
   Mercury's PDF written into `.valor/invoices/`. Write it unchanged to
   `.valor/effects/notice-<number>.json`. It leaves from valor@yuda.me without asking
   Tom again.
   The pay link, `https://app.mercury.com/pay/<slug>`, is built from the invoice's
   slug as Mercury's list reference describes; the docs do not state its form. Until
   one has been opened and shown Mercury's pay page, open the link before sending.
4. **Reminders** are the weekly `invoices` routine (`routines/invoices/`): `remind`
   picks Valor's Unpaid invoices at least a day overdue, at most one reminder per
   invoice every 7 days and 4 in all, reading valor@yuda.me's Sent mail (`gws`) for
   what already went out. An invoice past its fourth reminder is listed for Tom. A
   Sent-mail read that fails stops the run; it never counts as none sent.
   Invoices Tom made in Mercury's dashboard show in `unpaid` and get no reminder.

`unpaid` lists every Unpaid invoice with its customer, days overdue and pay link.
`pdf ID` writes one invoice's PDF.

## Cyndra

The `cyndra-invoice` skill (work-vault) ends here instead of in a Gmail draft from
tom@yuda.me: after Tom approves the PDF (its step 9), and once the budget-history row
is ready,

1. `create Cyndra/invoices/invoice_<n>.json --customer-email michael@cyndra.ai --cc johann@cyndra.ai jess@cyndra.ai tom@cyndra.ai --account <checking id>`,
   shown to Tom first as above (his approval of the PDF is the OK when the total matches).
2. `notice <id>` and send it. Then move the PDF into `PDFs sent/` and add the
   budget-history row.

Michael has paid by wire so far; the pay link offers ACH as well.
