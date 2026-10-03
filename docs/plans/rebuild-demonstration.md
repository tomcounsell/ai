---
tracking: none
slug: rebuild-demonstration
type: record
status: recorded
---

# Rebuild demonstration

The record of the setup phase plan's step 4 demonstration:
one meaningful request from Tom's actual work, carried by Valor inside the
minimal kernel to a usable result, with Tom doing nothing except answer the
questions Valor chose to ask.

## Setup

**The replay.** The request is the one Tom gave for psyoptimal PR #894. Valor
starts from the commit that request was made against,
`ebdbf645a0c3302a90b77652852142f45983e84b` of `yudame/psyoptimal` (a private
Django and Postgres app), with no knowledge of how it was answered. The
merged PR is the reference the result is compared against afterward; it
stays out of this record and out of Valor's reach.

**The workspace.** `scripts/demo_workspace.sh` builds it under
`/Users/tomcounsell/src/valor-demo/`:

- `psyoptimal/`: a clone holding only the history up to the base commit
  (no later commits, branches, or tags), on branch `valor/profile-completion`.
- `origin.git/`: a local bare repository, the clone's only remote. A push is
  an `act` effect: Valor requests it, the broker holds it, and it lands only
  after Tom approves and releases it. Nothing reaches GitHub.
- `pg/`: a Postgres cluster of the workspace's own, separate from the one
  holding the kernel's ledger, on `127.0.0.1:5439` with password auth. Its
  `test` role (password `test`, CREATEDB) is what the app's tests use; the
  turn's environment points them at it (`TEST_DB_*`, `DATABASE_URL`, `PG*`).
- The leak check found no plan doc, code, or note describing the feature.
  The base tree mentions the sports career start-date fields (the existing
  data the request refers to) and an existing "Complete your profile" link;
  both are the app as it stood, and nothing was deleted.

**Isolation.** Every turn runs `claude -p` in safe mode (no hooks, skills,
plugins, CLAUDE.md, or MCP servers from this machine), with web fetch and
web search off, an allowlisted environment carrying no tokens or agent
sockets, an empty gh config, a git config with no credential helper, and a
sandbox-exec profile. Under that profile a turn cannot read Tom's other
checkouts, notes, earlier Claude Code transcripts and plans, or keys; cannot
write the bare origin or run git's keychain helper; and on loopback reaches
only the gateway, the workspace's Postgres, and ports 8000 to 8009; this
Mac's own Postgres (port 5432 and its socket) is out of reach, and the
workspace cluster's data directory is neither readable nor writable. It can
reach the public internet, so package installs work. It runs as Tom's user,
so a deliberate keychain read through the `security` tool is not fenced.

**Authority and money.** Effect ceiling `act`, governance grant none,
$15.00 committed to the task, model Claude Opus 5.5 (`claude-opus-5-5`, $4 input and $20
output per million tokens). Every model call of every turn, including
Claude Code's own side calls and subagents, passes through the kernel's
gateway and is metered onto that one task. (Superseded 2026-10-03: metered spending only; nothing refuses on money.) Correction 1 (the
governance-restraint paragraph, global, direct) renders into every turn's
Brief.

**How it runs.** `python -m core start` once; then `python -m core run`
until it prints a question, a delivery, or the end of the committed amount. Each question
is answered with `python -m core answer`; a held push is approved with
`python -m core approve` and performed with `python -m core release`.

## The request, verbatim

From Tom's Notion card, 2026-04-09:

> Home page notification if user profile settings not complete. Example: At
> least one of the **Sports Career Start Dates** should be completed.

Tom delegated the choice ("you can select from one of the last 20 PRs in
the yudame/psyoptimal repo and identify the best size item for the
demonstration"); the orchestrator picked PR #894, which closed #649. Kernel
task `32f800bce8a2`, ledger rows 88 to 255.

## Attention log

Valor asked no questions in any of its four turns. Everything Tom (or the
orchestrator speaking for him) told Valor came as review feedback on a
delivery, through `python -m core feedback`.

| # | Kind | Ledger row | Text | Changed the outcome | Changed the authority |
|---|---|---|---|---|---|
| | Question | none | Valor wrote no `.valor/question.md` in any turn | | |
| 1 | PM feedback on delivery 1, Tom's point sent by the orchestrator | 177 (`7d47c36872a4`) | "The most glaring miss: 'incomplete' means the user's whole profile settings, not just the sports career dates. The sports dates were an example." | Yes. Scope went from one of Tom's seven items to five | No |
| 2 | PM feedback on delivery 2, **role-played** by the orchestrator from Tom's recorded Notion answers, with his standing permission ("sure we can role-play anytime") | 207 (`3b30449600f0`) | "Closer. Three things: the SMS and notification consents count as profile settings too. Sports career dates apply to members of a Sports-sector team (including past and inactive memberships), not by role. And this should replace the existing first-name nudge, not sit beside it." | Yes. Two consents added, audience rule changed, old nudge removed, and Valor found and added a missing Settings control | No |

**Against the reference answers** Tom gave on the Notion card to the six
questions the earlier system asked:

| Tom's answer | Delivery 1 | Delivery 2 | Delivery 3 |
|---|---|---|---|
| Incomplete means name, phone, birthdate, gender, SMS and notification consents, sports career dates | Sports dates only | All but the consents | Matches |
| At least one of the three sports dates suffices | Matches | Matches | Matches |
| Everyone; sports dates only for members of a Sports-sector team, past and inactive memberships included | Active Individual role only | Individual role only | Matches |
| Replace the existing first-name nudge | Kept beside it | Kept beside it, not mentioned | Removed |
| Recompute every visit, no dismiss | Matches | Matches | Matches |
| Deep-link to the Settings tab of the first missing item | Matches (one item) | Matches | Matches |
| (Surfaced only in code) notification consent had no Settings control | n/a | n/a | Found and fixed |

Valor inferred three of the six correctly and missed three; the two
feedback rounds carried exactly those three. **The counterfactual:** one
message before building, holding three questions, collapses three rounds
into one:

1. Is the sports-dates example the whole requirement or one item of a
   profile check? If one item, which fields count (the consents too)?
2. Who is asked for sports dates: by role or by team sector, past
   memberships included?
3. Does the banner replace the first-name "Complete your profile" chip?

Each answer materially changed the outcome, the bar Mission item 3 sets
("Ask only when the answer materially changes the outcome or the authority
required"). Question 1 most of all: delivery 1 covered one of seven items.
Item 3 also says an unclear brief "produces a useful first version before
it produces a questionnaire", and delivery 1 was usable and listed two
decisions Tom might change (role scope, Settings wording). It did not list
the one that mattered most: its reading of the example as the whole spec.

Cost of not asking: two review rounds (Tom on delivery 1, his stand-in on
delivery 2), each ending in a feedback note, instead of three short answers
before one build; delivery 2 put two profile prompts on one page (old chip and
new banner); rounds 2 and 3 cost $1.74. Mission item 6 counts the attention
on the same footing as the dollars.

## Where Tom acted as project manager

PM work (judging and steering the product):

- **Item selection.** The plan has Tom pick the request; he delegated it,
  and the orchestrator picked #894 for its size.
- **Intervention 1.** Tom read delivery 1: "the first line item on what
  'incomplete' means is the most glaring error". Sent as row 177.
- **Intervention 2, role-played.** Tom did not review delivery 2. The
  orchestrator checked it against Tom's Notion answers and sent the gaps as
  row 207. No live attention from Tom, but it is PM work he had already
  done once, for the earlier system.

Authority, not PM work: the three pushes to the local bare origin, approved
in rows 174 ("Tom approved in session"), 204 and 253 (under Tom's standing
"you can play around all you want with local copies of repos") and done in
rows 176, 206, 255; and the role-play permission itself. Operator work by
the orchestrator, neither: running `core run` and fixing kernel defects
mid-demo.

Under the plan's rule ("Tom does nothing except answer questions Valor
chooses to ask") the demonstration would have ended at delivery 1, which
Tom judged wrong on the most important line.

## What was delivered

Valor's branch `home-profile-incomplete-nudge` (8228891, 0ad48a3, 040d597
on `ebdbf645`; 9 files, +312/-6) against PR #894 (12 files, +683/-19,
merged 2026-09-30), which the earlier system built after Tom's six answers.

**Whether it was used.** No. It is a replay of a shipped feature; the branch
exists only in the workspace and its bare origin.

**Behavior compared.**

| Aspect | Valor (040d597) | PR #894 |
|---|---|---|
| Items checked | First name and last name as two items, birthdate, gender, phone, SMS consent, notification consent; sports dates | Same fields, with first and last name as one "Name" item |
| Blank test | Python truthiness, so a whitespace-only name counts as filled | Strips whitespace first |
| Sports-date audience | Any membership, any status, on a team whose sector is in the Sports group (derived from `SECTOR_CHOICES`) | Same query and same derivation; also tests archived teams and each Sports sector |
| Sports-membership query | Runs on every home request | Runs only when all three dates are empty |
| Banner content | Each missing item is a link to its own Settings tab, plus a "Complete my profile" button to the first | Plain list plus one "Complete profile" button to the first tab |
| Button markup | Inline classes copied from the existing button style | Shared `components/button.html`; PR also ran `check_design_tokens.py` |
| Placement | Below the core-values nudge | Above it |
| Old first-name chip | Removed | Removed |
| "¿Hablas Español?" hint | Stays in the header, shown when first name is empty | Moved inside the banner, shown whenever the banner shows |
| Notification-consent checkbox | Added to Basic Information, keeps the original timestamp on re-save, clears on untick | Same markup, same semantics |
| Structure | `PROFILE_SETTINGS` list and helper in `apps/public/views/account/home.py`, plus a `User.has_sports_career_start_date` property | Separate module `apps/common/profile_completion.py` with a frozen dataclass and a docstring stating the rules |
| Tests | 10: 7 home-view tests (including a coach on a Sports team and an inactive past Sports membership) and 3 Settings tests | 35 across three files: helper unit tests (every Sports sector, archived team, another user's membership, item order), view tests, Settings tests |
| Translations | +24 lines in `django.po`, reusing existing labels where they existed | +24 lines, six new msgids, fuzzy count unchanged |
| Docs | None | `docs/features/profile-completion-banner.md` and an index entry |
| Product notes raised | SMS consent and phone labelled "(optional)" yet required by the banner; Sports Career tab also says "(Optional)"; saving Basic Information already resets the SMS consent date | SMS consent labelled optional means a permanent banner for anyone who leaves it unticked |

Valor's is better on per-item links, size (under half), and noticing the
SMS-consent-date reset. It is worse on docs, helper-level tests (archived
teams and per-sector rules untested), an extra query per home load,
whitespace names, the bypassed button component, and the language hint's
old condition. End-user behavior is close; the PR is easier to maintain.
Valor never looked at the result in a browser in any round, and said so
each time.

PR #894's tests were not run against Valor's branch: the workspace keeps no
Python environment for the app (no virtualenv, no `uv` on the path), and
the PR's helper and view tests import names Valor's branch lacks. Only the
Settings consent test shares an interface, over identical code.

**What Tom would have done differently.** In Tom's words, on delivery 1:
"the first line item on what 'incomplete' means is the most glaring error".
Open for Tom: whether delivery 3 would have been mergeable as is, and
whether its structure (no helper module, no doc) matters to him.

## Money

$15.00 committed, effect ceiling `act`, governance grant none. Every model call
was `claude-opus-5-5`; no other model was used.

| Turn | Rows | Model calls | Gateway metered | Harness reported | Output tokens | Cache read | Cache write |
|---|---|---|---|---|---|---|---|
| Delivery 1 (`3f17a7a4aeba`) | 89 to 177 | 40 | $1.233656 | $1.23364 | 13,552 | 2,095,120 | 67,907 |
| Failed resume (`79ff46fd3a58`) | 178 to 180 | 0 | $0 | $0 | 0 | 0 | 0 |
| Delivery 2 (`2ecea10fecfb`) | 181 to 207 | 9 | $0.891182 | $0.89118 | 7,742 | 577,220 | 77,602 |
| Delivery 3 (`02d29ba5a9c8`) | 208 to 255 | 20 | $0.847811 | $0.84781 | 13,819 | 1,831,766 | 25,613 |
| **Total** | | 69 | **$2.972649** | $2.972625 | 35,113 | 4,504,106 | 171,122 |

Harness figures are differences of its cumulative session cost; it agrees
with the gateway to $0.000024. $12.03 was left. Each resumed turn carries
the whole session: about 30,000 input tokens on the first call, 109,000 on
the last. Delivery 2 resumed over an hour after delivery 1, consistent with
its large cache write (77,602 tokens in 9 calls, against 25,613 in 20).

**What a judgement layer would have taken off the frontier model
(estimates).** A cheap Jev-class classifier reading the request before the
first turn, asking "does this request give an example that may stand for a
wider requirement, or name existing UI it may replace?", reads the
request and a short summary of the matching code: well under $0.05 at
Haiku-class prices. A yes sends Valor to ask the three questions first.
Rounds 2 and 3 cost $1.74 of Opus; one build of the final scope would likely
cost somewhat more than delivery 1, say $1.20 to $1.60. Estimated saving:
$1.40 to $1.80 of $2.97, plus both review rounds. Estimates, not
measurements; the emulator can measure them by replaying this case with and
without the classifier.

## Kernel findings

Incidents during the demo, from this branch's history after 46e6ac0a9:

1. **Postgres trust hole** (923e16e7f, before the first turn). The machine
   cluster trusted all loopback connections, so a turn could have written
   ledger rows as `valor_kernel` or a superuser. The workspace now has its
   own scram-auth cluster on 5439, and the sandbox denies 5432. Constraint:
   "A ledger the system cannot edit records every effect."
2. **No feedback path** (d45a92853, after delivery 1). Only questions could
   reach Valor; Tom could not send a delivery back. `core feedback` now
   records `feedback.given` and resumes the session. Mission item 1 and the
   Evidence item "Tom's feedback, both directions".
3. **Sandbox rule order, EPERM** (37ef3fb88). The turn after feedback 1
   failed with "Unable to connect to API (EPERM)" (row 179): sandbox-exec
   refused allowed loopback ports at some gateway ports when a network rule
   followed the allows. Denies now come first, with a 24-port probe test.
   Constraint "Reliable stop, recovery, and correction": it failed cleanly,
   metered $0, lost nothing.

Two further defects this record found, since fixed:

4. **A failed turn consumes the feedback.** `core/session.py` clears pending
   feedback on any `turn.ended`, failed or not, so the retry (row 181) was
   prompted "Continue." Valor saw the feedback only because Claude Code had
   saved the failed turn's prompt in session `d0f7ce5e`. Fixed: only a
   turn that finishes spends a pending answer or feedback.
5. **Feedback provenance cannot name its author.** Rows 177 and 207 both
   read `"by": "tom"`; 207 was role-played. Constraint: corrections "carry
   provenance". Fixed: answers and feedback record `by` and `role_played`.

**What the kernel lacked.** Nothing in the structure forced or prompted a
question. The Brief told Valor how to ask and quoted item 3's restraint
("Ask only when..."), but nothing in the persona said an example might be
standing in for the whole requirement. Valor's "decisions you may want to
change" lists were the right instinct, placed after the build.

**Recommendations (not implemented).**

- Persona text, not a check (Mission item 3): when a request leans on an
  example or names existing UI, and another reading would change what gets
  built, ask before building.
- A judgement-tier ambiguity classifier before the first turn that can send
  Valor to ask first. It is a gate, so it needs Tom's tap and a ledgered
  guard with a ninety-day expiry. Incident: this demonstration, task
  `32f800bce8a2`, two PM rounds for three decisions one question would have
  settled. Mission items 3 and 6.
- A way to run the app and view it in a headless browser in the workspace
  (Mission item 1, "testing actual use"). A capability, not a gate.

## Correction 1 rendering, verified

Every `turn.started` row of the task carries `"corrections": [1]` and the
same `brief_sha256` (`a97acdbbae71...e5648a`), and the full correction 1
paragraph appears twice in each row: in the recorded Brief and in the
`--append-system-prompt` argument that reached `claude -p`.

| Row | Turn | Prompt | Correction 1 present |
|---|---|---|---|
| 89 | `3f17a7a4aeba` | the request | yes |
| 178 | `79ff46fd3a58` | feedback 1 | yes |
| 181 | `2ecea10fecfb` | "Continue." | yes |
| 208 | `02d29ba5a9c8` | feedback 2 | yes |

The ledger is not broken on entry one. The kernel sends one Brief per turn
and has no separate supervisor prompt, so these rows are every Brief it
sent. Subagents Claude Code may have started inside a turn were not checked.
