# Mercury invoicing

Task 04f8d562ea78. Tom, 2026-10-21 (Telegram 11674, 11676): Valor creates
invoice pay links for Yudame's clients in the company Mercury account, lists
who hasn't paid, and sends notices and reminders from valor@yuda.me. Creds
come from the shared 1Password vaults. Done when Tom has a real client
invoice created in Mercury through it and an unpaid reminder has gone out
from valor@yuda.me (rule 5; the live check waits on Tom's token).

## What is built

1. **Skill `mercury-invoicing`** in Valor's own plugin,
   `skills/valor/skills/mercury-invoicing/`: `SKILL.md` plus `mercury.py`
   (stdlib `urllib`, no new dependency). Here, not yudame/skills: the token,
   the sender and the routine are Valor's; this repo's merges reach every
   Mac (skills/README.md). Commands:
   - `create SPEC.json --customer-email E [--cc ...] --account ID`: reads a
     yudame-invoice spec (the same JSON the PDF is rendered from), finds the
     Mercury customer by email (paging `GET /ar/customers`) or creates it
     from the spec's client, and `POST /ar/invoices` with
     `sendEmailOption: DontSend`, `invoiceNumber` = the spec's number (a
     reused number is refused, so a retry cannot double-invoice),
     `internalNote: "valor"`, ACH debit on, card off. Lines: one per
     section (`"CTO - Tom Counsell: 19.9 h"`, quantity 1, unit price the
     section total), the budget adjustment and each adjustment as their own
     line, so the Mercury amount equals the PDF's TOTAL DUE. Prints id,
     amount, due date and pay link `https://app.mercury.com/pay/{slug}`.
   - `unpaid`: every invoice (paging `GET /ar/invoices`) with status
     `Unpaid`, joined to its customer: number, customer, email, cc, amount,
     due date, days overdue, pay link, and whether Valor created it.
   - `pdf ID -o PATH`: `GET /ar/invoices/{id}/pdf`, for attaching.
   - `accounts`: `GET /accounts`, to pick the receiving account once.
   Token: `op read "op://Yudame Secrets/Mercury API Token/credential"` at
   each call, sent as `Bearer secret-token:<token>` (prefix added once if
   missing). Never written to a file, argv, log or output; an HTTP error
   prints status and Mercury's body only. A refused call names this Mac's
   public IP, so a changed IP is visible in the error.
   `SKILL.md` says how invoices go out (the Brief's flow): show Tom the
   invoice (client, lines, amount, due date) in his DM, create it on his OK,
   then send the notice by `email.send` from valor@yuda.me with the pay
   link and the rendered PDF; reminders for invoices Valor created go out
   without asking. Text and template for notice and reminder live there.
2. **Weekly routine `invoices`** (`routines/invoices/routine.toml`, Monday
   09:00 Bangkok, ceiling `act`, project `valor`): runs `unpaid`, and for
   each overdue invoice Valor created sends one reminder (`email.send`, pay
   link + Mercury PDF), then ends with `stop.md` naming what went out. It is
   also what touches the token inside Mercury's 45-day deletion window.
   Needs a generic runner `task` (start one project task with the routine's
   instruction, as `verify.run` does with `ctx.start_project`), about 15
   lines beside `_routine_runners`; no runner today starts a plain task.
3. **Cyndra wiring.** `cyndra-invoice` lives in `tomcounsell/work-vault`,
   which this task cannot push. Add `projects/work-vault.toml` (Cowboy,
   `merge_url` the vault; Valor's account has push) and write the
   replacement for its steps 10-11 into `mercury-invoicing/SKILL.md`
   ("Cyndra"): after Tom approves the PDF at step 9, `create` with
   `--customer-email michael@cyndra.ai --cc johann@cyndra.ai jess@cyndra.ai
   tom@cyndra.ai`, then the notice from valor@yuda.me replaces the Gmail
   draft from tom@yuda.me. The delivery asks the chat session to start the
   one-line work-vault task that pastes it into `cyndra-invoice`.
4. **Docs.** `docs/machine.md`: credentials come from the shared 1Password
   vaults (`Yudame Secrets` for company services, `m-valor`/`Valor env` for
   Valor's own), read at use time with `op read`. `skills/README.md`,
   `routines/README.md`, `docs/routines.md`: the new skill and runner.

## Out of scope

Card payments (Stripe), recurring invoices, cancelling or editing invoices
(the dashboard does it), reminders for invoices Tom made in the dashboard
(they show in `unpaid`; Mercury's own reminders may cover them), other
clients' invoice skills.

## Governance

Adds no check, gate, hook, validator or approval step in code. "Tom OKs an
invoice before it is created" is the Brief's flow written as skill text;
for Cyndra it is the review the skill already has (step 9).

## Tests (`tests/test_mercury_invoicing.py`, fake HTTP, fake `op`)

- Lines from spec: 260901CA rebuilt (budget 8000, OpenAI 3.61) sums to
  8003.61; 261001CA to 8000.00; under budget adds no adjustment;
  `included`, `defer`, `memo` lines left out; cents exact.
- Create body: every required field, `DontSend`, number, note, account.
- Customer: found on page 2 by email (case-insensitive); created when absent.
- Unpaid: only `Unpaid` (Paid, Cancelled, Processing out); pages followed
  until no `nextPage`; days overdue from `dueDate` (due today is 0, future
  negative); Valor-created flag from `internalNote`; pay link from slug.
- Token: read through `op` per call, prefix added once; missing item gives a
  plain error naming the vault and item; token never in stdout/stderr,
  including on a 401/403.
- Routine: `invoices` toml loads; `task` runner starts one project task with
  the instruction and ceiling.

## What Tom does (sent in the delivery, one message)

In Mercury: Settings > Tokens > Create an API Token, type **Custom**, scopes
for invoices (accounts receivable) read and write, customers read and write,
accounts read, nothing that sends money. Write scopes require an IP
allowlist: **171.100.191.13** (the Cowboy, a True/Asianet cable line, likely
dynamic; if it changes, a call fails naming the new IP and he edits the
allowlist). Save it in 1Password vault **Yudame Secrets**, item
**Mercury API Token** (type API Credential, field `credential`). The exact
scope labels are not public; the builder names them as the docs give them
and the delivery says so.
