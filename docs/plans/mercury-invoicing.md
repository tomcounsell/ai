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
   (stdlib only). Here, not yudame/skills: the token, sender and routine are
   Valor's, and this repo's merges reach every Mac. Commands:
   - `create SPEC.json --customer-email E [--cc ...] --account ID`: reads a
     yudame-invoice spec (the JSON the PDF is rendered from). Pages
     `GET /ar/invoices` first; an invoice with the spec's number is printed
     and nothing is created (no reliance on Mercury's undocumented refusal
     of reused numbers). Finds the customer by email (case-insensitive,
     skipping any with `deletedAt` set) or creates it from the spec's
     client. `POST /ar/invoices` with every field the reference marks
     required (`customerId`, `invoiceDate`, `dueDate`, `ccEmails`,
     `destinationAccountId`, `lineItems`, `achDebitEnabled: true`,
     `creditCardEnabled: false`, `useRealAccountNumber: false`) plus
     `sendEmailOption: DontSend`, `invoiceNumber`, `internalNote: "valor"`.
     Lines: one per section (`"CTO - Tom Counsell: 19.9 h"`, qty 1, price
     the section total), the budget adjustment and each adjustment as their
     own lines, so the amount equals the PDF's TOTAL DUE.
   - `unpaid`: every `Unpaid` invoice joined to its customer: number,
     customer, email, cc, amount, due date, days overdue, pay link, Valor's.
   - `remind`: the reminder set: Unpaid, Valor's (`internalNote` "valor"),
     at least 1 day overdue, no reminder for that number in valor@yuda.me's
     Sent mail in the last 7 days, fewer than 4 sent in all (read with
     `gws gmail users messages list`, `in:sent subject:"Reminder: Invoice
     <n>"`). Gmail's Sent folder is the record, so a re-fired or resumed
     run, on any day, finds what went out. Prints each as a ready
     `email.send` payload: to the customer's Mercury email, cc the
     invoice's `ccEmails`, never typed by hand, and `files: [{path,
     sha256}]`: the Mercury PDF written under `.valor/invoices/` in the
     current workspace (a regular file, no link; not `$TMPDIR`), sha256 of
     the bytes written, as `core/bridge.py` requires. An invoice past its
     4th reminder is listed for Tom instead.
   - `notice ID`: the same payload for the first notice of a created invoice.
   - `pdf ID -o PATH`, `accounts`.
   Token: `op read "op://m-valor/Mercury API Token/credential"` per
   call, sent as `Bearer secret-token:<token>`. `op` signs in with
   `OP_SERVICE_ACCOUNT_TOKEN`; when the environment lacks it, it is read
   from the vault `.env` (`VALOR_VAULT_ENV`, default
   `~/Desktop/Valor/.env`) exactly as `scripts/deploy-site.sh` does. Missing
   service token and missing item each fail with a plain message naming
   what is missing. The token is never in a file, argv, log or output. A
   refused call prints Mercury's body and this Mac's public IP.
   Pay link `https://app.mercury.com/pay/{slug}`: the docs say only that the
   slug builds it, so `SKILL.md` says the first live invoice's link is opened
   before any notice leaves, and the delivery says it is unconfirmed.
   `SKILL.md` gives the flow: Tom sees client, lines, amount, due date in his
   DM; `create` on his OK; notice by `email.send` from valor@yuda.me with
   pay link and PDF; reminders for Valor's invoices without asking.
2. **Weekly routine `invoices`** (`routines/invoices/routine.toml`, Monday
   09:00, ceiling `act`, project `valor`) with a generic runner `task`
   beside `_routine_runners`. It starts nothing, returning
   `Ran(None, "nothing_due", "not placed here: <machine>")`, unless the routine's project spec is placed on this
   Mac (`(spec.machine or settings.default_machine) == settings.machine`),
   so only the Cowboy (on the IP allowlist) sends. Otherwise it starts one
   project task through `ctx.start_project` with marker
   `{"sdlc": 1, routine objective}`, so `machine.fold` works it as an SDLC
   task. Its instruction: run `mercury.py remind`, request each payload as
   `email.send` with the Mercury PDF, then once outcomes are in write
   `.valor/stop.md` (`Done:` and what went out). The work happens in the
   clarify turn, which `stop.md` ends (skills/sdlc/clarify.md); no plan,
   build or merge runs. The weekly call also keeps the token inside
   Mercury's 45-day deletion window. Routine plists are installed by hand
   (docs/rollout.md step 9), so the build installs it on the Cowboy:
   `python -m core routine invoices --plist` with `VALOR_MACHINE` set, into
   `~/Library/LaunchAgents/com.valor.routine.invoices.plist`,
   `launchctl bootstrap gui/$UID`, confirmed with `launchctl print`; the
   delivery names that evidence, and rollout step 9 lists the routine.
3. **Cyndra.** `cyndra-invoice` lives in `tomcounsell/work-vault`. Add
   `projects/work-vault.toml` (Cowboy, `merge_url` the vault, suite
   `python3 -m py_compile .claude/skills/yudame-invoice/render_invoice.py`)
   and, in `SKILL.md` ("Cyndra"), the replacement for its steps 10-11: after
   Tom approves the PDF, `create` with customer michael@cyndra.ai, cc
   johann@, jess@, tom@cyndra.ai, then `notice` from valor@yuda.me instead
   of the Gmail draft from tom@yuda.me. The delivery asks the chat session
   to start the work-vault task that pastes it in.
4. **Docs.** `docs/machine.md` Keychain: the "a turn holds none" passage
   is rewritten to say a turn holds the 1Password service sign-in (the
   turn's environment carries `OP_SERVICE_ACCOUNT_TOKEN` today, checked
   2026-10-21) and that company credentials are read from the shared vaults
   (`Yudame Secrets` for company services, `m-valor`/`Valor env` for Valor's
   own) at use time with `op read`. `docs/routines.md` lines 44 and 184
   (Keychain inside a performer) say the same. `skills/README.md`,
   `routines/README.md`: the skill and the `task` runner.

## Out of scope

Card payments, recurring invoices, cancelling or editing invoices,
reminders for dashboard-made invoices (listed by `unpaid` only), other
clients' invoice skills.

## Governance

No check, gate, hook, validator or approval step in code. Tom's OK before
`create` is the Brief's flow as skill text; the placement test and the
reminder cadence are what the job does, not limits on Valor.

## Tests (`tests/test_mercury_invoicing.py`, fake HTTP, `op`, `gws`)

- Amount: 260901CA spec gives 8003.61; 261001CA 8000.00; a spec with three
  adjustments equals the renderer's TOTAL DUE; under budget adds no
  adjustment; `included`, `defer`, `memo` left out.
- Create body has every required field and `DontSend`; an existing number
  prints that invoice and posts nothing.
- Customer: found on page 2 by email; a deleted match is skipped and a new
  customer created; created when absent.
- Unpaid: Paid, Cancelled, Processing out; pages followed to the end; days
  overdue (due today 0, future negative); pay link from slug.
- Remind: dashboard-made overdue invoice excluded; due today excluded;
  reminded 3 days ago excluded, 8 days ago included; 4 sent listed for Tom;
  payload recipients are the customer's email and invoice cc; the payload's
  file exists at its path inside the workspace and its sha256 matches.
- Token: read per call; prefix once; service token taken from vault `.env`
  when absent from env; missing service token, missing item each named;
  token absent from stdout/stderr, including on 401/403.
- Routine: toml loads; spec placed elsewhere starts no task and returns
  `nothing_due` naming the machine;
  spec placed here starts one task whose `fold` is not legacy, with the
  instruction and ceiling.

## What Tom does (sent in the delivery, one message)

Mercury: Settings > Tokens > Create an API Token, **Custom**, scopes for
invoices (accounts receivable) read and write, customers read and write,
accounts read, nothing that sends money. Write scopes need an IP allowlist:
**171.100.191.13** (the Cowboy; a cable line, likely dynamic, and a refused
call names the new IP). Saved (Tom, 2026-10-21) in 1Password vault **m-valor**, item
**Mercury API Token** (API Credential, field `credential`). The scope labels
are not public; the delivery says so.
