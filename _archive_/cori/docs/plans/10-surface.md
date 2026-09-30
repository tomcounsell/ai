# 10. Surface

| | |
|---|---|
| Slug | `surface` |
| Milestone | M0 |
| Status | built |
| Seams version | 3 (2026-09-20) |
| Owns | `adapters/cli.py`, `kernel/approvals.py`, `schemas/approval.py`, `ports/surface.py`; tables `conversations`, `cards`, `approvals`; migration `migrations/versions/0008_surface.py` |
| Depends on | events (append, read_for, single_flight), tree (project, approve, raise_budget), spaces (load_all) |
| Written | 2026-09-19, against 1123f53 (seams v1); revised 2026-09-19 against 4cdd45c (seams v2); revised 2026-09-20 after critique against 9d1ce70 (seams v2, round two); cascaded 2026-09-20 to seams v3 (`docs/reviews/2026-09-20-budget-ruling.md`) |
| Built | 2026-09-22, merge 57e498e |

**Cascade, 2026-09-20 (lead).** Seams v3 removed the standing budget and every `cards` charge, made the budget one dollar figure per objective, and named the effect classes `read`, `propose`, `act`. In this plan: `issue_card` charges nothing and `AttentionExhausted` is gone; `standing_budget_card` and the commit-time `budget_increase` are gone, and `budget_increase_card` is built on overrun with `spent`, `produced`, `requested`; the `commit` card carries `budget` as dollars and `basis`; `self_approve` refuses an `act`; a `grant` goes to `tree.raise_budget`. The Findings, Seam amendments 8 and 9, and the Critique replies about charges below are the record of earlier rounds.

## Purpose

M0 ships "CLI approval surface with a space on every conversation and kernel-minted approvals" (tech stack §10). This plan is the person's end of the harness: the one terminal the person types into, the typed cards the kernel renders from structured fields, and the approval records only the kernel can mint, each bound to a contract revision and an argument digest, consumed once, and expiring with the objective. The first objective (a code change at `propose`) needs it for the worker's `ask` to reach the person as a card and for the answer to come back as a record. The second (a drafted reply, also at `propose`) needs it for the same reason, plus the verification failure card if the draft fails its checks. Every line of authority in the system ends at the person, and this component is where that line is authenticated (tech stack §13).

## What the documents say

- Architecture §7, KEEP with the space rule ADDED: a conversation is always in one space; anything that changes authority is a typed card, never prose; the kernel authenticates the channel and mints every approval record; an adapter renders cards and relays replies over a session the kernel owns, so no adapter code can create an approval.
- Architecture §7, KEEP: escalation types are question, budget increase, scope change, effect class elevation, verification failure, unknown outcome, ethics flag; each card is compact, carries the id of the item it regards, and offers two or three concrete options with a recommendation; the reply is recorded as an approval record with the raw message attached; no answer means no new authority.
- Architecture §7, KEEP: cards are rendered by the kernel from structured fields; agent prose appears only in a marked, length-capped note region.
- Architecture §7, KEEP: a card carries an expiry tied to its objective's deadline; an unanswered card expires into a typed CANCELLED. Every card names its space; switching space is an explicit act on the surface.
- Architecture §7, PATCHED: away state from the read cursor. No milestone names it; see Out of scope.
- Architecture §2 Commit, REVISED: the approval record stores the person's literal message and the contract revision it approved; any later revision clears the approval; the supervisor cannot self-approve an `act`; at `read` and `propose` it self-approves and tells the person the estimate, or asks on a Commit card when it judges the person would want to be asked.
- Architecture §1, REVISED: a space switch seals the current thread to its space and starts a new one; a turn with no space is refused.
- Architecture §3.1, KEEP: a card for an effect that leaves the space shows the destination audience beside a payload summary.
- Architecture §4, REVISED 2026-09-20: attention is measured, never budgeted; whether and when a card goes to the person is the supervisor's judgment; the kernel records cards per objective, time to decision, and approve-without-edit rate; overrun is a `budget_increase` card whose grant raises the root; the commit card shows one dollar figure and its basis.
- Architecture §6, PATCHED: corrections and approvals are kernel-typed utterances on the surface, so they arrive with their class rather than as prose a Scribe has to recognize.
- Architecture §8: "Approval replayed with changed arguments" is caught by binding the approval to argument digest and contract revision and consuming it once.
- README, Effects have classes: an approval binds the capability, a digest of the arguments, and the revision of the task; a changed argument is a fresh ask; an approval is consumed once.
- Tech stack §13, port and passkey LOCKED, surfaces COMMITTED: typed cards out, approval record in, raw message attached; the CLI first; on the local surface an approval is an interactive confirmation the kernel attributes to a terminal session under the person's user, never a file or socket write a worker could make; `read` and `propose` need channel identity once the kernel has authenticated it; an `act` is a passkey at M3.
- Tech stack §1, LOCKED: kernel invariants are Hypothesis properties, among them "no approval is consumed twice; no stale contract revision is honored".
- Tech stack §2, LOCKED: `ApprovalRecord` is a schema in the contract; `adapters/` import `schemas/` and `ports/` and never `kernel/`.
- Tech stack §3, LOCKED: append-only by grant with the trigger as the second lock; row-level security on every space-partitioned table.
- Tech stack §9, PROVISIONAL, COMMITTED on the order of work: attention is recorded per card and computed later, at volume.
- Tech stack §10, COMMITTED on scope: approval adapters stay outside the trust boundary only because the kernel mints every approval record itself.
- Commit pass item 3: CLI at M0, FastHTML at M1, Telegram possible; all Python, no schema export.
- Blind-spot review findings 12 (kernel mints), 16 (attention exhaustion is never permission), 23 (cards expire with the objective deadline), 27 (conversation and approval surface are one surface).
- Seams v3 §0 (ids, timestamps, `Strict`, the `approval:<approval_id>` lock namespace under `hashtextextended`; money the one budget axis), §1.12 (`schemas/approval.py` as written; `regards` per kind; exactly one recommended option; the card field table, `commit` with `budget` and `basis`, `budget_increase` with `spent`, `produced`, `requested`; the option-to-kind table; route cards expire in seven days; the unissued commit card on self-approval; the self-approved contract as prose with its budget and basis; the architect's ruling that no cap exists on a self-approved budget, that the supervisor issues the `commit` card at `read` and `propose` by its own judgment and always for an `act`, and that `budget_increase` is issued on overrun only), §2.3 (`ApprovalSurface`), §3.2 (`tree.approve` calls `approvals.check` and refuses `self_approved` on an `act`; `tree.expire_due` cancels a node past its deadline with reason `deadline`; `raise_budget` takes an `approved` record from a `budget_increase` card), §3.3 (`serve` owns the inbound dispatch, calls both expiry sweeps once a minute; `approval.minted`, `card.expired`, and `objective.state_changed` to `FAILED` are supervisor triggers; the `verdict.recorded` turn issues the failure card), §3.7 (every `kernel/approvals.py` signature, `unknown_outcome_card` included; `issue_card` charges nothing), §4 (the seven surface events, `session.opened` among them), §6 and §7 (tables, modules, migration 0008; `kernel/__main__.py` is the integration plan's), Round two of 2026-09-20 as it survives (`approvals.expire_due` transitions nothing; `Reply.text`), and "Version 3" items 1, 3, 4, and 9.

## What exists

Nothing under the four modules this plan owns. What the plan builds on:

- `migrations/versions/0001_roles_and_events.py`: the roles, `reject_mutation()`, `cori_current_space()`, and the RLS policy pattern this plan's migration copies for its three tables.
- `schemas/space.py`: `Strict`, `EffectClass`, `DataClass`, `load_space`; the space list the CLI validates `/space` against comes from `kernel/spaces.load_all()` (seams §3.4).
- `tests/conftest.py`: the `kernel`, `context`, `migrator`, and `space` fixtures and the `requires_postgres` skip.
- `tests/test_append_only.py`, `tests/test_rls.py`: the shape of the grant, trigger, and RLS tests this plan repeats for its tables (`tests/test_grants.py` is retired by the tree plan; the spaces plan's `tests/test_space_partition.py` covers the catalog).
- Spike 08: the `ask` tool emits a `question` and blocks with zero gateway calls until `answer()`. The question card and its `answered` record are the person-facing half of that path. No spike code is lifted into this component.

## Seams

**Consumed**

- §0: UUIDv7 ids via `uuid.uuid7()`, `CardId`, `ApprovalId`, `ConversationId`, `SpaceId`, `ObjectiveId`, `BriefId`, `QuestionId` from `schemas/ids.py` (events plan).
- §1.3 `Budget` (tree) for the dollar figures on the `commit` and `budget_increase` cards; §1.4 `Objective`, `Contract` (tree) for card construction, the basis, and the deadline; §1.5 `Brief` (tree); §1.7 `Question` (worker); §1.6 `Verdict` (worker); §1.10 `Action`, `EffectOutcome` (broker) for the argument digest and the unknown-outcome card; §1.11 `InboundItem` (spaces) for the route card.
- §3.1 `events.append`, `events.read_for`, `events.single_flight` (key `approval:<id>`).
- §3.2 `tree.project`, `tree.approve` (the caller of `check`), `tree.raise_budget` (what a `grant` reaches through the supervisor's turn).
- §3.3 `supervisor.serve`: the loop that iterates `inbound()` and calls `mint` and `switch_space`, and calls `tree.expire_due` and `approvals.expire_due` once a minute; the turns on `question.raised`, `verdict.recorded`, and `approval.minted` that call the constructors, `issue_card`, `supervisor.answer`, and `consume`.
- §3.4 `spaces.load_all` (valid space ids for `/space` and for `route_inbound` options).
- §7: `kernel/__main__.py` (integration) calls `open_terminal_session`, `attach`, `latest_conversation` or `open_conversation`, `record_session`, `pending_cards`, and `unknown_outcome_card` for each `unknown` from `broker.reconcile_dangling` at boot.

**Provided**

- §1.12 `schemas/approval.py`: `Conversation`, `CardKind`, `Option`, `Card`, `Reply` (with `text`), `Utterance`, `Correction`, `SpaceSwitch`, `Inbound`, `ApprovalKind`, `ApprovalRecord`, fields exactly as written, plus `CARD_FIELDS` and `OPTIONAL_CARD_FIELDS`.
- §2.3 `ports/surface.py`: `ApprovalSurface`.
- §3.7 `kernel/approvals.py`: every signature listed there, from `open_terminal_session` to `check`, plus `unknown_outcome_card` (Round two) and `record_session` (Seam amendments 11); `standing_budget_card` went with the standing budget (version 3).
- §4 surface events: `session.opened`, `conversation.opened`, `space.switched`, `card.issued`, `card.expired`, `approval.minted`, `approval.consumed`.
- §6 tables: `conversations`, `cards`, `approvals`.
- `adapters/cli.py`: `CliSurface`, the one `ApprovalSurface` implementation.

## Design

### Modules

**`schemas/approval.py`** holds seams §1.12 verbatim, `Reply.text: str | None = None` included (Round two). Two validators, as methods, never fields:

- `Card`: exactly one option has `recommended=True` (seams §1.12); `fields` contains every key in `CARD_FIELDS[kind]`, and any other key is in `OPTIONAL_CARD_FIELDS[kind]`.
- `Reply`: `edit` is present only when `option == "edit"`.

Two module constants fix the keys. `CARD_FIELDS: dict[CardKind, tuple[str, ...]]`:

| Kind | Keys, in render order |
|---|---|
| `commit` | `premise`, `non_goals`, `success_criteria`, `budget`, `basis`, `effect_ceiling`, `deadline`, `stop_conditions`, `audience` |
| `question` | `brief_id`, `question_id`, `question` |
| `verification_failure` | `brief_id`, `verdict_summary`, `failed_criteria` |
| `budget_increase` | `brief_id`, `spent`, `produced`, `requested` |
| `unknown_outcome` | `effect_id`, `action_type`, `idempotency_key` |
| `route_inbound` | `headers`, `candidates` |
| `scope_change`, `effect_class_elevation`, `ethics_flag` | `()` until their constructors arrive; a card of these kinds fails validation at M0 |

`OPTIONAL_CARD_FIELDS` is `{"commit": ("argument_sha256",)}`, for the M2 effect approval, and empty for every other kind. Every value is a `str`: a string field is copied as is; a `Budget` renders as dollars with two decimals (`$12.50`), because the person reads money and never micros (architecture §4); every other value (a list, a `datetime`, the header dict) is canonical JSON, `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)`, with `datetime` as ISO 8601 UTC. Decided here: `stop_conditions` is the canonical JSON of the contract's ceilings (`deadline`, `max_effect_class`, `max_data_class`), because `Contract` (seams §1.4) has no `stop_conditions` field and the ceilings are what stops a node (see Findings 6).

**`ports/surface.py`** holds seams §2.3 verbatim.

**`kernel/approvals.py`** is the only writer of `approvals` and the only constructor of `ApprovalRecord`. Its signatures are seams §3.7, repeated here with the behavior each carries:

```python
# sessions: the kernel authenticates the channel (tech stack §13)
def open_terminal_session(fd: int = 0) -> str      # refuses unless os.isatty(fd) and the tty's owner uid == os.getuid(); mints a UUIDv7, adds it to the live set; holds (uid, tty) for the session.opened event
def session_is_live(session_id: str) -> bool
def attach(surface: ApprovalSurface) -> None       # once at boot; issue_card and open_conversation call the attached surface
async def record_session(conn, space: SpaceId) -> None   # appends session.opened once per session, into the space of the conversation the entry point chose (Seam amendments 11)

# conversations
async def open_conversation(conn, space: SpaceId) -> Conversation        # row, event conversation.opened, surface.open; remembers the conversation as the space's current one
async def latest_conversation(conn, space: SpaceId) -> Conversation | None   # a read; writes nothing; remembers the result as current when found
async def switch_space(conn, conversation_id: ConversationId, to_space: SpaceId) -> Conversation
    # event space.switched on the old conversation, then open_conversation(to_space); refuses a space id absent from spaces.load_all() and not "unassigned"
async def pending_cards(conn, conversation_id: ConversationId) -> list[Card]   # issued, undecided, unexpired; re-shown at start

# cards: rendered by the kernel from structured fields (architecture §7)
def question_card(objective: Objective, brief: Brief, question: Question) -> Card
def verification_failure_card(objective: Objective, brief: Brief | BriefId, verdict: Verdict) -> Card   # the Executor brief, or its id from the verdict.recorded payload for a kernel verdict
def budget_increase_card(objective: Objective, brief_id: BriefId, spent: Budget, produced: list[str], requested: Budget) -> Card   # on overrun: the Executor brief that ran out, what it cost, the artifact paths it left, what the supervisor asks for
def commit_card(objective: Objective, audience: str) -> Card
def route_inbound_card(item: InboundItem, candidates: list[SpaceId]) -> Card
def unknown_outcome_card(outcome: EffectOutcome) -> Card                # Round two; fields from outcome.result, conversation from the space's current one
async def issue_card(conn, card: Card, *, now: datetime | None = None) -> None     # seams §3.7; charges nothing (version 3)

# approvals
async def mint(conn, reply: Reply) -> ApprovalRecord                  # seams §3.7; wall clock, never reply.at
async def self_approve(conn, objective_id: ObjectiveId, revision: int) -> ApprovalRecord
async def consume(conn, approval_id: ApprovalId) -> None
async def expire_due(conn, *, now: datetime | None = None) -> list[CardId]   # writes the expired record and card.expired; transitions nothing (Round two)
def argument_digest(actions: Sequence[Action]) -> str                 # sha256 over the canonical JSON of the batch, in order
def check(approval: ApprovalRecord, *, contract_revision: int | None = None, argument_sha256: str | None = None) -> None
    # pure; NotAnApproval for any kind outside {approved, approved_with_edit, self_approved}; StaleRevision; ArgumentMismatch,
    # including when a digest is passed and the record's argument_sha256 is None; called by tree.approve and, at M2, broker.perform
```

Exceptions, all in this module: `UnknownSession`, `UnknownCard`, `UnknownOption`, `AlreadyDecided`, `AlreadyConsumed`, `CardExpired`, `SpaceMismatch`, `NoConversation`, `NotAnApproval`, `StaleRevision`, `ArgumentMismatch`.

Decided here: `open_terminal_session` is synchronous and has no connection, so the `session.opened` event (seams §4) is appended by `record_session(conn, space)`, one explicit call the entry point makes after it has chosen the conversation, into that space. A read (`latest_conversation`) writes nothing. Each session is recorded once; a second `record_session` for the same session is a no-op.

Decided here: the module remembers the current conversation per space (set by `open_conversation`, `latest_conversation`, and `switch_space`), so a constructor that receives no conversation (`unknown_outcome_card`) can name one; a space with no current conversation raises `NoConversation`.

**`adapters/cli.py`** holds `CliSurface(reader, writer, session_id)`, constructed by the kernel's boot code with the session id from `open_terminal_session` and asyncio streams over the process's own stdin and stdout. It imports `schemas/` and `ports/` only.

### Tables

All three are insert-only under `kernel_rw`, carry `reject_mutation()` triggers for UPDATE, DELETE, and TRUNCATE, and carry row-level security in the pattern of migration 0001 (`kernel_rw` everything the grant gives, `context_ro` filtered by `cori_current_space()`).

| Table | Columns |
|---|---|
| `conversations` | `id text pk`, `space_id text`, `opened_at timestamptz`, `schema_version int` |
| `cards` | `id text pk`, `kind text`, `space_id text`, `conversation_id text references conversations`, `regards text`, `objective_id text null`, `contract_revision int null`, `fields jsonb`, `options jsonb`, `note text`, `expires_at timestamptz`, `issued_at timestamptz`, `issued bool`, `schema_version int` |
| `approvals` | `id text pk`, `card_id text references cards unique`, `kind text`, `space_id text`, `objective_id text null`, `contract_revision int null`, `argument_sha256 text null`, `raw_message text`, `session_id text`, `decided_at timestamptz`, `schema_version int` |

`approvals.card_id` is unique (seams §6), so "one decision per card" is a constraint the grant enforces before any code does; a second reply, or an expiry after a reply, fails at the database. `cards.objective_id` and `cards.issued` are columns beside the Card's own fields (seams §6), so `mint` and `expire_due` find the objective without resolving a brief and a self-approval's card is distinguishable from a shown one. Consumption is the event `approval.consumed`, never a column (seams §1.12). The migration is `0008_surface.py` with `down_revision = "0007"` (seams §7). All three tables carry `space_id` and the policy, so the spaces plan's `tests/test_space_partition.py` covers them by predicate.

### Regards, revision, expiry

An **objective card** is a card whose `cards.objective_id` column is non-null. The constructors set it: `question_card`, `verification_failure_card`, `budget_increase_card`, and `commit_card` set it to the objective's id; `route_inbound_card` and `unknown_outcome_card` leave it null. The lock and the revision binding key on that column, never on the kind. Seams §1.12: an objective card's `regards` is the objective id and its `contract_revision` is the objective's current revision, so `check` refuses a stale one uniformly (architecture §2, "any later revision clears the approval"); `fields["brief_id"]` names the brief the card concerns and is information for the reader and for `supervisor.answer`. `unknown_outcome` regards the effect id and `route_inbound` the inbound item id, both with `contract_revision` None.

`expires_at` is the objective's `contract.ceilings.deadline` for objective cards (architecture §7), seven days after issue for `route_inbound` (seams §1.12), and, decided here, seven days after issue for `unknown_outcome` as well, since the constructor holds no objective deadline. A non-objective card's expiry closes only the card: the item stays header-only in `unassigned`, the dangling effect stays in the ledger, and a later card may be issued for either.

**Attention.** `issue_card` charges nothing (seams v3 item 1). Whether a card is issued is the caller's judgment, and at M0 every card a turn may issue stands on a fact of the store (a question, a verdict, an overrun, a challenged assumption) except the `commit` card, which the supervisor issues when it chooses to ask first. What the kernel keeps is the record architecture §4 and tech stack §9 name: `card.issued` with `issued_at`, `approval.minted` with `decided_at` and `kind`, and whether the reply carried an edit. Nothing computes over it at M0.

### Options and the approval kind

The constructors fix each kind's option keys, and `mint` maps `(kind, option)` to an `ApprovalKind` from one table (seams §1.12); a key outside the table is `UnknownOption`.

| Kind | Options (recommended first) | ApprovalKind |
|---|---|---|
| `commit` | `approve`, `edit`, `reject` | approved, approved_with_edit, rejected |
| `question` | `answer`, `abort` | answered, rejected |
| `verification_failure` | `retry`, `cancel` | approved, rejected |
| `budget_increase` | `grant`, `deny` | approved, rejected |
| `route_inbound` | the candidates, first recommended; with one candidate, that candidate and `keep_unassigned`; with more than three, the first three | answered |
| `unknown_outcome` | `done` (the person checked the target and the effect is there), `rerun`, `drop` | answered, approved, rejected |
| `scope_change`, `effect_class_elevation`, `ethics_flag` | `approve`, `reject` | approved, rejected; constructors arrive with the plans that raise them |

`answered` is the kind for any reply that supplies information rather than authority (`question`, `route_inbound`, `done` on an unknown outcome), per seams §1.12. Decided here: the unknown-outcome options, because architecture §7 lists the card and no document names its choices; `done` is recommended because the card exists so the person can look at the target, and `rerun` and `drop` are the two errors spike 02 measured (duplicate and loss), so neither is the default. The broker at M2 acts on `rerun` as an `approved` record bound to the effect's digest.

### Control flow

**Boot.** `kernel/__main__.py` belongs to the integration plan (seams §7); this plan provides what it calls, in this order: `open_terminal_session()`, `CliSurface(reader, writer, session_id)`, `attach(surface)`, `latest_conversation(conn, space) or open_conversation(conn, space)` for the `--space` named on the command line (`python -m kernel --space psyoptimal`, integration plan), `record_session(conn, space)`, `surface.show` for each of `pending_cards`, then `issue_card(unknown_outcome_card(outcome))` for each `unknown` that `broker.reconcile_dangling` returned (Round two). A restart is a resume, so the conversation id survives it (architecture §1: a kill is lossless for durable state). No conversation exists without a space because the entry point requires the flag.

**Issue.** A caller builds a card with a constructor and calls `issue_card(conn, card)`: the supervisor's turn on `question.raised` when it relays rather than answers, its turn on `verdict.recorded` for a fail or abstain, its turn on `FAILED/budget_exhausted` for a `budget_increase`, its framing step for a `commit` card when it asks first (seams §3.3), and the entry point for an unknown outcome. Under `single_flight("objective:<id>")` for an objective card, and no lock otherwise, since a non-objective card binds no revision: refuse when `card.space` differs from the conversation's space (`SpaceMismatch`); insert the row with `issued = true`; append `card.issued`; call the attached surface's `show`. No ledger row is written. For a kernel verdict (`record_verdict(conn, None, ...)`) the `verdict.recorded` payload still names the Executor `brief_id` (seams §4), and the constructor takes that id for `fields["brief_id"]`.

**Reply.** `supervisor.serve` (seams §3.3) hands every `Reply` from `inbound()` to `mint(conn, reply)`. `mint` reads the wall clock (`datetime.now(UTC)`) and never `reply.at`, which is the adapter's. It refuses a session id outside the live set (`UnknownSession`); loads the card (`UnknownCard`); refuses when the clock is past `expires_at` (`CardExpired`), whether or not `expire_due` has run; maps the option (`UnknownOption`); requires `edit` for `approved_with_edit`; inserts the `approvals` row, where a unique violation is `AlreadyDecided`, the outcome for a second reply or for a reply that races an expiry inside one instant; appends `approval.minted` with payload `{approval, text}`, `text` being `reply.text`; returns the record. `objective_id` and `contract_revision` come from the card row; `argument_sha256` comes from `fields["argument_sha256"]` when present, which no M0 card carries. `approval.minted` is a supervisor trigger (seams §3.3): for an `answered` question the turn calls `supervisor.answer(brief_id, question_id, text, answered_by=approval.id)` with `text` from the event payload, and `consume`; for a rejected question it transitions the node. Decided here: `text` rides in the `approval.minted` payload because the turn holds the event and the record, and the record has no text field (Seam amendments 10).

**Self-approval.** `self_approve(conn, objective_id, revision)` for a node whose ceiling is `read` or `propose` (architecture §2; seams v3 §1.12): build `commit_card(objective, audience="sandbox")`, insert it with `issued = false` and no `card.issued` event and no `show`, then insert an approval of kind `self_approved` with empty `raw_message` and `session_id = "kernel"`, and append `approval.minted`. The unissued card row is the record of the contract in the exact form the person would have seen (seams §1.12), budget and basis included. An `act` ceiling raises `ValueError`. The supervisor's commit step then calls `tree.approve(conn, objective_id, record)`, which calls `check` (seams §3.2), and `consume` in the same transaction; the person sees the contract as prose in the conversation with its dollar figure and basis (seams v3 §1.12). A `commit` card is issued at M0 only when the supervisor chooses to ask first; nothing about the size of the budget forces one.

**Budget increase on overrun.** When an Executor's run ends for `budget_exhausted` and the tree writes `FAILED/budget_exhausted`, the supervisor's turn on that state change decides what to ask for and builds `budget_increase_card(objective, brief_id, spent=project().budget_consumed, produced=<the artifact paths from the tool log>, requested=<the turn's RequestBudget amount>)` and calls `issue_card` (seams v3 §1.12; architecture §4, overrun is a card). A `grant` reply mints an `approved` record bound to the objective and its current revision. The supervisor's turn on `approval.minted` then calls `tree.raise_budget(conn, objective_id, by=requested, approval_id=record.id)`, which writes the `grant` ledger row citing the record, then `tree.approve` with the same record (the `FAILED` to `APPROVED` edge, seams v3), then `consume`, and delegates a fresh Executor at the next generation. `deny` mints `rejected` and the supervisor cancels. The `approved` record passes `check` at the bound revision, which is what the plan's test asserts.

**Consume.** `consume(conn, approval_id)` under `single_flight("approval:<id>")` (seams §0 namespace, `hashtextextended`): count `approval.consumed` events for the id with `events.read_for`; refuse when the count is one (`AlreadyConsumed`); append `approval.consumed` with `by` equal to the card's `regards` (seams §4). Callers: the supervisor's commit step after `tree.approve`; its `approval.minted` turn after `raise_budget` and `approve` for a `grant`, and for an answered question; the broker at M2 for effect approvals.

**Expire.** `expire_due(conn, now=...)` selects issued cards with `expires_at <= now` and no approval row, and for each inserts an approval of kind `expired` with `session_id = "kernel"` and appends `card.expired`. It transitions nothing (Round two): an objective card's deadline is the node's deadline, and `tree.expire_due` cancels the node with reason `deadline` on the same sweep, so the card's expiry and the node's cancellation coincide without either calling the other, and a card on a node that is already terminal (a `verification_failure` card on a `FAILED` node) expires the same way. Returns the expired card ids. Idempotent: the unique constraint on `card_id` makes a rerun a no-op. `supervisor.serve` calls `tree.expire_due` and `approvals.expire_due` once a minute (seams §3.3, Round two), and `card.expired` is a supervisor trigger.

**Switch.** `/space <id>` on the CLI yields a `SpaceSwitch`; `serve` calls `switch_space(conn, conversation_id, to_space)` (seams §3.3, §3.7), which appends `space.switched` with `new_conversation_id` on the current conversation and then opens a new one in the target space. The supervisor's thread for the old conversation is sealed by that event (architecture §1). `unassigned` is a legal target so the person can see `route_inbound` cards there; opening an objective in it stays refused by the tree (seams §0).

### The CLI

Rendering is deterministic text, ASCII, one card per block, numbered per session:

```
--- card #1  question  space psyoptimal  regards 0199…  rev 1  expires 2026-09-20T09:00:00Z
brief_id      0199…
question_id   0199…
question      What is your first name?
options       [answer] Answer the worker (recommended)   [abort] Abort the worker
note (agent prose, capped): I could not find it in the repository.
reply with    #1 <option> [text]
```

Fields print in `CARD_FIELDS` order for the kind; the note line prints only when `note` is non-empty and only under its marker, so agent prose is never in a field line (architecture §7). The input grammar has four forms and nothing else:

| Line | Yields |
|---|---|
| `#<n> <option> [text]` | `Reply(card_id, option, edit=None, text=<the rest of the line after the option, or None>, raw_message=<the whole line>, session_id, at)` |
| `/space <id>` | `SpaceSwitch` |
| `/correct` then two prompted lines, "What was wrong?" and "What is right?" | `Correction` with `regards` None |
| anything else | `Utterance` |

Decided here: the CLI produces no `edit` at M0, because the only card that takes one is `commit`, which the supervisor self-approves unless it chooses to ask first (seams v3 §1.12); a `commit` card it does issue at M0 takes `approve` or `reject` from the CLI, and the edit grammar arrives with the first objective whose contract the person wants to change on the card rather than in prose. `#<n>` numbers are the adapter's, mapped to `CardId` in memory; `pending_cards` at boot re-shows and renumbers. `open` prints one line naming the conversation and its space; `say` prints the text as is.

## Tasks

1. **Schemas and port.** `schemas/approval.py` per seams §1.12 with the two validators, `CARD_FIELDS`, and `OPTIONAL_CARD_FIELDS`; `ports/surface.py` per §2.3. *Accept:* `uv run pytest tests/test_approval_schema.py` green, including `test_card_requires_exactly_one_recommendation`, `test_card_requires_kind_fields` (a missing required key and a key outside the optional set both fail; `argument_sha256` on `commit` passes), `test_reply_edit_only_with_edit_option`, `test_reply_text_defaults_to_none`, `test_every_seams_field_present_and_no_extra`; `uv run lint-imports` reports the contract kept.
2. **Migration.** `migrations/versions/0008_surface.py`, `down_revision = "0007"`: the three tables with `space_id`, grants, triggers, RLS policies, `approvals.card_id` unique, `cards.objective_id` and `cards.issued`. *Accept:* `uv run alembic upgrade head` then `downgrade -1` round-trips; `uv run pytest tests/test_surface_tables.py` green: `test_kernel_rw_grants_are_insert_and_select_only`, `test_update_and_delete_refused_by_grant_then_trigger`, `test_context_ro_reads_one_space_only`, `test_second_approval_for_a_card_violates_unique`.
3. **Sessions and conversations.** `open_terminal_session`, `session_is_live`, `attach`, `record_session`, `open_conversation`, `latest_conversation`, `switch_space`, `pending_cards`. *Accept:* `uv run pytest tests/test_approvals.py -k "session or conversation"` green: `test_terminal_session_refuses_a_pipe` (a `pty.openpty()` slave passes, an `os.pipe()` end is refused), `test_open_conversation_writes_row_event_and_calls_surface_open`, `test_record_session_appends_session_opened_once_with_uid_and_tty` (a second call appends nothing; `latest_conversation` alone appends nothing), `test_switch_space_seals_and_opens` (events `space.switched` carrying `new_conversation_id`, then `conversation.opened`, new id, old space unchanged), `test_switch_to_unknown_space_refused`, `test_latest_conversation_resumes_after_restart`.
4. **Cards.** The seven constructors and `issue_card`. *Accept:* `uv run pytest tests/test_approvals.py -k card` green: `test_question_card_regards_objective_and_binds_revision`, `test_issue_card_appends_event_and_shows`, `test_issue_card_refuses_space_mismatch`, `test_issue_card_writes_no_ledger_row` (a `verification_failure` card issues after `tree.release` on the Executor brief and `budget_ledger` is unchanged), `test_commit_card_renders_budget_as_dollars_and_carries_basis`, `test_budget_increase_card_carries_spent_produced_requested` (`fields["brief_id"]` is the Executor brief; `spent` and `requested` render as dollars; `produced` is the artifact paths), `test_unknown_outcome_card_fields_options_and_expiry` (fields from `outcome.result`, `done` recommended, seven days, `NoConversation` without a current conversation), `test_route_inbound_card_options` (one candidate gives `keep_unassigned`; five give three; first recommended; seven days).
5. **Mint, self-approve, check.** `mint`, `self_approve`, `argument_digest`, `check`. *Accept:* `uv run pytest tests/test_approvals.py -k "mint or self_approve or check"` green: `test_mint_refuses_unknown_session`, `test_mint_maps_option_to_kind_per_table`, `test_mint_uses_wall_clock_not_reply_at`, `test_mint_payload_carries_reply_text`, `test_budget_increase_grant_is_approved_bound_to_revision` (`check` passes at the card's revision and raises `StaleRevision` after `revise_contract`), `test_mint_refuses_second_reply_as_already_decided`, `test_mint_refuses_expired_card_before_and_after_expire_due` (`CardExpired` both times), `test_self_approve_writes_unissued_card_and_record_with_budget_and_basis`, `test_self_approve_refuses_act`, `test_check_refuses_stale_revision_and_argument_mismatch`, `test_check_refuses_answered_rejected_expired`, `test_check_refuses_a_digest_against_a_record_without_one`; plus the properties in task 7.
6. **Consume and expire.** `consume`, `expire_due`. *Accept:* `uv run pytest tests/test_approvals.py -k "consume or expire"` green: `test_consume_appends_event_with_by_equal_to_regards`, `test_sixteen_concurrent_consumes_succeed_exactly_once` (sixteen `kernel_rw` connections opened by the test, each in its own transaction, as spike 01's race did; `asyncio.gather` of one `consume` per connection on one id; one success, fifteen `AlreadyConsumed`, one event), `test_expire_due_writes_record_and_event_and_transitions_nothing` (on a live node and on a `FAILED` node alike: one `expired` row, one `card.expired`, no `objective.state_changed` from this call; rerun is a no-op).
7. **Properties.** `tests/test_approvals_properties.py`, the six listed below. *Accept:* `uv run pytest tests/test_approvals_properties.py` green with Hypothesis at 200 examples for the pure properties and 50 for the database ones.
8. **CLI adapter.** `adapters/cli.py::CliSurface`. *Accept:* `uv run pytest tests/test_cli_surface.py` green: `test_render_matches_golden` (the block above, byte for byte, for one card per kind that has a constructor, list and Budget fields as canonical JSON), `test_render_puts_note_only_under_marker`, `test_grammar_reply_space_correct_utterance` (four lines over an in-memory stream pair yield the four `Inbound` types with `raw_message` equal to the line; `#1 answer Tom` gives `text == "Tom"` and `#1 abort` gives `text is None`), `test_unknown_card_number_is_an_utterance`; `uv run lint-imports` still kept.
9. **Round trip.** `tests/test_surface_roundtrip.py::test_question_card_round_trip`: a pty pair, `open_terminal_session(slave)`, `attach`, `open_conversation`, `record_session`, `issue_card(question_card(...))` against a real objective and brief from the tree fixtures, write `#1 answer Tom\n` into the pty, read the `Reply` from `inbound()`, `mint`, `consume`. *Accept:* the test is green and the event log for the space reads `conversation.opened`, `session.opened`, `card.issued`, `approval.minted`, `approval.consumed` in that order, the `approvals` row has kind `answered` and `raw_message == "#1 answer Tom"`, the `approval.minted` payload has `text == "Tom"`, and `budget_ledger` gained no row.

## Properties

All in `tests/test_approvals_properties.py`, in the style of spikes 01 and 05.

1. **Consumed once.** Over any sequence drawn from {mint a reply to a fresh card, consume an approval id, consume the same id again, consume concurrently with N tasks}: for every approval id the count of `approval.consumed` events is at most one and every consume after the first raises `AlreadyConsumed`. `test_consume_at_most_once`.
2. **One decision per card.** Over any sequence drawn from {reply at clock t, reply again at t, sweep `expire_due(now=t)`} with t drawn on either side of each card's deadline: every card has at most one `approvals` row; a reply at a clock past the deadline raises `CardExpired` whether or not a sweep has run; a second reply before the deadline raises `AlreadyDecided`; a sweep after a reply changes nothing; and when a reply and a sweep race inside one instant, exactly one row exists and the loser sees `AlreadyDecided`. `test_one_decision_per_card`.
3. **Revision and argument binding.** Pure. For any approval minted from a card at revision r and any r' with r' != r, `check(approval, contract_revision=r')` raises `StaleRevision` and `check(approval, contract_revision=r)` returns; for any two action batches that differ in any field or in order, their digests differ and `check` with the other's digest raises `ArgumentMismatch`; `check` with any digest against a record whose `argument_sha256` is None raises `ArgumentMismatch`; for every kind outside {`approved`, `approved_with_edit`, `self_approved`}, that is `answered`, `rejected`, and `expired`, `check` raises `NotAnApproval` whatever the arguments. `test_check_binds_revision_and_arguments`.
4. **Session refusal.** Pure over the live set. For any reply whose `session_id` is not in the set returned by `open_terminal_session`, `mint` raises `UnknownSession` before touching the database. `test_mint_refuses_every_unminted_session`.
5. **Card space.** For any card and any conversation, `issue_card` succeeds only when `card.space == conversation.space` and `card.conversation_id == conversation.id`; on success exactly one `cards` row and one `card.issued` event exist and `budget_ledger` is unchanged; nothing is written on refusal. `test_card_space_matches_conversation_and_touches_no_ledger`.
6. **Expiry is monotone, idempotent, and transitions nothing.** For any set of issued cards on nodes in any state (live or terminal) with random deadlines and any non-decreasing sequence of `now` values, `expire_due` returns each card id exactly once, at the first `now` at or past its deadline, never a card with a later deadline, and appends no `objective.state_changed` event. `test_expire_due_monotone_idempotent`.

An example test accompanies each property in `tests/test_approvals.py` (Tasks 5 and 6).

## Out of scope

- **FastHTML surface, websockets, HTMX, cards on a phone.** Tech stack §13 and §10, M1.
- **Passkey step-up, `act` cards, the WebAuthn route, `cori.yudame.dev` as relying party.** Tech stack §13, M3; the tunnel and domain exist from prereqs item 11 and nothing here touches them.
- **Batching proposals, the effect approval card, `argument_sha256` filled live.** Architecture §7 batches the proposals the supervisor asks about and tech stack §10 puts the first proposal that leaves the space at M2. `argument_digest` and `check` exist so the broker plan has a seam; no M0 card carries a digest. Seams `CardKind` has no kind for a batch of proposals; the M2 planner adds one or reuses `commit` (see Findings).
- **The commit card's edit path.** The supervisor self-approves at `propose` and the person sees the contract as prose with its budget and basis, or the supervisor asks first and a `commit` card is issued (architecture §2; seams v3 §1.12). Both M0 objectives are at `propose`, so the card is issued at M0 only when the supervisor judges the person would want to be asked. The `commit` constructor and its option table stay for the self-approval record, for that case, and for M2, because `approved_with_edit` and revision binding are kernel properties (tech stack §1). The CLI edit grammar waits for the first card whose contract the person wants to change on the card.
- **Away state and the read cursor.** Architecture §7, PATCHED, no milestone. It pauses effects that leave the space and every `act`, none of which exists at M0, and needs a read event the seams do not define; the M1 planner adds `card.read` or a cursor row.
- **Attention metrics: cards per objective, time to decision, approve-without-edit rate, rubber-stamp escalation.** Architecture §4; tech stack §9, COMMITTED: recorded now, computed at volume. The rows and events this plan writes carry every input (`issued_at`, `decided_at`, `kind`), and no computation runs.
- **Route cards flowing.** `route_inbound_card` and its tests are built here; nothing issues one at M0. Seams §3.3 has no trigger on `inbound.unassigned` and the spaces plan defers assignment by card to `inbound-assign` at M1, which also needs a conversation in `unassigned` that only a `/space unassigned` switch opens.
- **Corrections as beliefs.** At M0 a `Correction` is the event `correction.recorded`, emitted by the supervisor (seams §1.12, §4); the operator record is M1.
- **Cross-space count line and the morning brief.** Architecture §1 and §9; the supervisor renders the count, the brief is M1.
- **Task templates as a fast path.** Architecture §7 TODO; trigger is the ordinary path showing which tasks recur.
- **Telegram.** Tech stack §13, possible third surface.
- **Constructors for `scope_change`, `effect_class_elevation`, `ethics_flag`.** Their option table is fixed here; the constructors arrive with the plans that raise them (tree for scope change at M1 with the Planner). `unknown_outcome_card` is this plan's (Round two) and is in Design and task 4.

## Risks

- **Reading stdin inside the kernel's event loop.** asyncio on macOS needs `connect_read_pipe` on the tty or a reader thread; whichever is chosen, `inbound()` must not block the loop that also runs the gateway. The round-trip test over a pty catches a blocking implementation.
- **The pty in CI.** `pty.openpty()` works on the Linux runner; the tty-owner check reads `os.fstat(fd).st_uid`, which is the runner's uid. If CI lacks a controlling tty for some other reason the session tests skip with a reason rather than pass vacuously.
- **A card that outlives its node.** `approvals.expire_due` and `tree.expire_due` coincide because the card carries the node's deadline; a card whose node was cancelled or failed early stays pending until the deadline. At M0 the supervisor's turn on `objective.state_changed` can leave it, since a reply to it mints a record that `check` refuses on the terminal node; closing such cards early is an M1 nicety.
- **`EffectOutcome.result` for an unknown outcome.** `unknown_outcome_card` reads `space_id`, `action_type`, and `idempotency_key` from `outcome.result`, which `broker.reconcile_dangling` has at hand from the intent row (Seam amendments 12). Until that amendment lands, the constructor raises `KeyError` on a result without them and the entry point logs and continues.
- **Output while the person is typing.** `show` and `say` write to the same terminal the person types into. At M0 the block is reprinted on the next prompt; cosmetic, no property depends on it.
- **Two decisions racing.** A reply and an expiry for the same card at the same instant both insert; the unique constraint decides, so one side sees `AlreadyDecided` and neither corrupts the other. Property 2 covers it.

## Questions for the architect

1. **Does the person see the framed contract at class 0 and 1 before work starts?** Architecture §2 says the Approval Surface presents the contract and the supervisor "cannot self-approve anything above class 1"; seams §1.12 records a `self_approved` approval at class 0 and 1. Assumption while the question waits: the supervisor says the contract in prose in the conversation and self-approves; the person's objection is an utterance the supervisor treats as a correction and a revision. If the answer is that the contract is a card at every class, the `commit` constructor already exists, `issue_card` replaces `self_approve` in the supervisor's commit step, and the CLI gains the `#<n> edit <field>: <text>` grammar, about half a day.
2. **Who runs the process and iterates `inbound()`?** No plan in the index owns the entry point. Assumption: the integration plan (`99-integration.md`) wires `open_terminal_session`, `CliSurface`, `attach`, the conversation resume, and the inbound dispatch table (Utterance to the supervisor, Reply to `mint`, Correction to `correction.recorded`, SpaceSwitch to `switch_space`); this plan provides every function that table calls.

**Answered 2026-09-19 (lead, from the documents):** question 1: the person sees the class 0 and 1 contract as prose in the conversation and the supervisor self-approves (architecture §3.1; seams §1.12 says so now); an objection is a correction and a revision. Question 2: the integration plan owns the entry point and the inbound dispatch table. *Superseded in part 2026-09-20 (seams v3): the classes are `read` and `propose`, and the supervisor may issue the `commit` card at either when it judges the person would want to be asked; the answer otherwise stands.*

## Seam amendments

1. **§3.7, add the helpers this plan exposes**, so the supervisor, runs, verifier, and broker plans call constructors instead of assembling `Card` by hand: `open_terminal_session`, `session_is_live`, `attach`, `open_conversation`, `latest_conversation`, `switch_space`, `pending_cards`, `question_card`, `verification_failure_card`, `budget_increase_card`, `commit_card`, `route_inbound_card`, `argument_digest`, `check`, with the signatures in Design. Reason: architecture §7 says cards are rendered by the kernel from structured fields, and one module doing it keeps the field table in one place.
2. **§1.12, card field table:** `question` carries `brief_id, question_id, question`; `verification_failure` carries `brief_id, verdict_summary, failed_criteria`; `budget_increase` carries `brief_id, requested, remaining`. Reason: attention is consumed from the brief and the answer is routed to it.
3. **§1.12, `regards`:** state that objective cards regard the objective id, `unknown_outcome` the effect id, `route_inbound` the item id. Reason: one rule per kind; `mint` needs no lookup.
4. **§4, `space.switched` payload:** add `new_conversation_id`. Reason: architecture §1 starts a new thread on a switch, and a reader of the old conversation's events should find the successor without scanning `conversation.opened`.
5. **§4, new surface event `session.opened`** with payload `session_id, uid, tty`. Reason: every `approval.minted` and `message.received` names a session id, and the ledger should say where that session came from. Until accepted, the live set is in memory and the origin goes to telemetry only.
6. **§3.7, `expire_due(conn, *, now: datetime | None = None)`.** Reason: testability; the default is unchanged.
7. **§7, entry point.** Assign `kernel/__main__.py` (or a `cori` console script) to the integration plan. Reason: Question 2.

**Lead, reconcile (seams v2, 2026-09-19):** amendments 1 to 7 accepted as written (seams §1.12, §3.7, §4, §7). `kernel/__main__.py` is the integration plan's.

8. **§3.7, `budget_increase_card(objective, brief: Brief | TurnId, requested, remaining)`.** Proposed after the architect's answer on self-approval. Reason: the commit-time card exists before any Brief, and `tree.consume` already accepts a turn id (§3.2), so the card charges the turn's standing allocation. Until applied, the plan passes the turn id where the signature names `brief`.

**Lead, round two (2026-09-20):** amendment 8 accepted. One constructor added to your §3.7 list: `unknown_outcome_card(outcome: EffectOutcome) -> Card`, issued by `kernel/__main__.py` after `broker.reconcile_dangling` (seams, Round two).

Raised after the critique of 2026-09-20:

9. **§3.7, `standing_budget_card(space, turn_id, requested, remaining, expires_at) -> Card`**, for the supervisor's card when `allocate_turn` refuses; `regards` the space id, `objective_id` null, charged to the standing node. Reason: amendment 1 set out to end hand-assembled cards, and the supervisor plan builds this one by hand.
10. **§4, `approval.minted` payload:** `approval (ApprovalRecord), text (Reply.text)`. Reason: the turn that answers a worker holds the event and the record, and the record has no text field.
11. **§3.7, `record_session(conn, space) -> None`:** appends `session.opened` once; the entry point calls it after choosing the conversation. Reason: `open_terminal_session` has no connection and a read must not write.
12. **§3.10, `reconcile_dangling`:** an `unknown` outcome's `result` carries `space_id`, `action_type`, `idempotency_key`, `objective_id` from the intent row. Reason: `unknown_outcome_card(outcome)` has nothing else to build its fields from.
13. **§3.7, `verification_failure_card(objective, brief: Brief | BriefId, verdict)`.** Reason: a kernel verdict has no Brief object, only the Executor brief id in the `verdict.recorded` payload.
14. **§3.7, `issue_card(conn, card, *, now: datetime | None = None)`.** Reason: the standing node's day is derived from the clock; testability, default unchanged.

## Findings

1. **`ApprovalRecord.card_id` is required, and seams v1 §1.12 said a self-approval is an `ApprovalRecord` with no card shown.** Resolved in seams v2 §1.12: the commit card is written unissued (findings record 53).
2. **Seams §1.12 says `argument_sha256` is for "effect cards only", and `CardKind` has no effect-batch kind.** Architecture §7 batches class 2 requests and §3.1 puts the audience on "every class 2 card". Either the `commit` card is the class 2 approval for its objective's batch, or M2 adds `effect_batch`. Nothing at M0 depends on it; noted for the M2 planner (findings record 54).
3. **Seams v1 §4 `space.switched` carried one `conversation_id`, while architecture §1 says a switch "starts a new one".** Resolved in seams v2 §4: `new_conversation_id` (findings record 55).
4. **Seams v1 §3.7 `consume(conn, approval_id)` had no `by`, and seams §4 `approval.consumed` requires one.** Resolved in seams v2 §3.7 and §4: `by` is the card's `regards`.
5. **Tech stack §13 says the CLI session is one "the kernel attributed to a terminal session under the person's user at start", and nothing recorded that attribution.** Resolved in seams v2 §4: `session.opened` (findings record 56).
6. **The `commit` card carries `stop_conditions` (seams §1.12, architecture §2) and `Contract` (seams §1.4) has no such field.** This plan renders the ceilings under that key. Either the seams field table drops the key or `Contract` gains `stop_conditions: list[str]`; for the lead.

### Critique, 2026-09-20

1. **`expire_due` transitions a node that is already terminal.** Expire, line 173; Property 6; task 6. An objective card's `expires_at` is the contract deadline (line 142), and `tree.expire_due` (seams §3.2) cancels every live node past its deadline with reason `deadline` on the same tick, so `tree.transition(objective_id, "CANCELLED", "card_expired")` lands on a node the tree already cancelled. A `verification_failure` card regards a node that is already `FAILED`. The tree plan's table admits `CANCELLED` only from the six live states and raises `IllegalTransition` otherwise, so both cases fail, and the first is the ordinary case for every unanswered question card. Fix: `expire_due` projects the node and calls the transition only when the state is live; the `expired` approval row and `card.expired` are written in every case. Add `test_expire_due_on_a_terminal_node_writes_the_record_and_skips_the_transition` to task 6 and restate Property 6 over nodes in any state.
   *Author, 2026-09-20:* changed per the lead's ruling, further than the fix asked: `expire_due` writes the `expired` record and `card.expired` and transitions nothing; `tree.expire_due` cancels the node at its deadline with reason `deadline`. Expire paragraph, task 6 test, Property 6, and the Risks entry rewritten.
2. **The verification failure card charges a released brief.** Issue, line 163; `verification_failure_card(objective, brief, verdict)`, line 104; task 4. The brief in `verdict.recorded` is the Executor brief (seams §4, §6 `verdicts.brief_id`), and by the time the supervisor's turn runs, `run_brief` has called `tree.release` on it (seams §3.5, ruling 3), so `remaining(brief)` is `ZERO` on every field and `tree.consume(brief_id, cards=1)` raises `BudgetExceeded`, which `issue_card` re-raises as `AttentionExhausted`. Every verification failure card at M0 fails to issue, and for a kernel verdict (`record_verdict(conn, None, ...)`) the plan does not say which brief the constructor receives. Seams §3.7 fixes the charge to `fields["brief_id"]`, so the seams win; the plan should state the precondition plainly (the brief named must hold an unreleased `cards` allocation), say that the kernel-verdict card names the Executor brief from the event payload, and raise a seam amendment for the lead: charge the objective node when the named brief is released, or let the constructor name the objective in `fields` instead of a brief for this kind.
   *Author, 2026-09-20:* changed per the lead's ruling: `issue_card` charges the objective node through `tree.consume(objective_id, ...)`, never a brief; `fields["brief_id"]` is information only; the kernel-verdict card takes the Executor brief id from the payload (`verification_failure_card(objective, brief: Brief | BriefId, verdict)`, amendment 13). Task 4 test asserts the card issues after `tree.release`.
3. **The commit-time `budget_increase` card charges a turn allocation that carries no cards.** Budget increase at commit, line 169; task 4, `test_budget_increase_card_at_commit_charges_the_turn`. The supervisor plan reserves one hundredth of the allowance "in tokens and money" per turn (its Standing budget section), so the turn's `cards` allocation is zero and `tree.consume(turn_id, cards=1)` refuses. Seams Round two says the card charges the standing turn's `cards`, so the seams win; the plan should list the precondition under Risks (the turn's reservation carries at least one card, which is the supervisor plan's line to add) and correct the test's observable: a consume on a turn moves the turn's `remaining`, and the standing node's `allocated_live` is unchanged by it, so "the standing node's `cards` fell by one" is the wrong assertion. Assert `tree.remaining(turn_id).cards` fell by one.
   *Author, 2026-09-20:* changed per the lead's ruling, which supersedes the turn charge: the commit-time card charges the FRAMED objective node, and the test asserts `tree.remaining(objective_id).cards` fell by one. The precondition moved to Risks (the contract's `budget.cards` is at least one). The supervisor's own standing card gets `standing_budget_card`, charged to the standing node (amendment 9).
4. **`unknown_outcome_card` is assigned to this plan and absent from its body.** Lines 142, 155, and 240 leave the constructor to the broker or to "the plans that raise them"; line 271 (lead, round two) and the seams Round two give `unknown_outcome_card(outcome: EffectOutcome) -> Card` to this plan, and integration task 8 calls it at boot. Fix: add the constructor to Design and to task 4 with its field keys (`effect_id`, `action_type`, `idempotency_key`), its options and approval kinds, and its expiry, and remove the kind from Out of scope. Note for the lead: `EffectOutcome` carries neither `space` nor `conversation_id`, both required on `Card`; the effect ledger row has `space_id`, and the boot conversation is the only target. The seams signature stands; the plan should say where the two fields come from.
   *Author, 2026-09-20:* changed: `unknown_outcome_card` is in Design (fields `effect_id`, `action_type`, `idempotency_key` from `outcome.result`; options `done`, `rerun`, `drop`; seven days; charged to the standing node) and in task 4; removed from Out of scope. `space` comes from `result["space_id"]` (amendment 12) and the conversation from the module's current conversation for that space (`NoConversation` otherwise).
5. **Nothing calls `expire_due` at M0.** Expire, line 173: "the timer that calls `expire_due` is the supervisor plan's". The supervisor plan puts timer triggers at M1 (its Out of scope), the integration plan's entry point does not call it, and the tree plan says the same timer calls `tree.expire_due`. Expiry exists only in tests, which contradicts architecture §7 ("never an indefinite wait") and finding 23 of the blind-spot review, both cited at lines 22 and 38. Fix: add a question for the lead naming the caller this plan assumes (a tick in `serve` or `kernel/__main__.py`), with the assumption stated so work continues.
   *Author, 2026-09-20:* closed by the lead's ruling: `supervisor.serve` calls `tree.expire_due` and `approvals.expire_due` once a minute (seams §3.3, Round two). Cited in the Expire paragraph and the Seams consumed list; no question needed.
6. **The answer text has no field and no parser.** Reply, line 165; the grammar table, line 195; task 9. `Reply` (seams §1.12) carries `option`, `edit` (commit only), and `raw_message`, and the CLI's `raw_message` is the whole line `#1 answer Tom`. The `approval.minted` turn calls `supervisor.answer(brief_id, question_id, text, ...)`, and the supervisor plan passes `raw_message`, so the worker receives `#1 answer Tom`. The seams win on the `Reply` shape; within them the plan should say that the text a worker receives is `raw_message` with the `#<n> <option> ` prefix removed, name the module that removes it (the supervisor's turn, since the surface cannot add a field), and make task 9 assert what `runs.answer` receives (`"Tom"`). Note for the lead: a `text: str | None` on `Reply` would make this typed.
   *Author, 2026-09-20:* changed per the lead's ruling: `Reply.text: str | None = None`; the CLI fills it with the rest of the line after the option; `mint` puts it in the `approval.minted` payload (amendment 10) so the `approval.minted` turn passes it to `supervisor.answer`. Tasks 8 and 9 assert `text == "Tom"`.
7. **`mint` and Property 2 disagree on the exception after expiry, and `mint`'s clock is unstated.** Reply, line 165: `now > expires_at` raises `CardExpired` before the insert. Property 2, line 219: "a reply after an expiry raises `AlreadyDecided`". Risks, line 248, says the unique constraint decides. Fix: state the clock (wall clock in `mint`, never the adapter's `at`), and rewrite Property 2 so a reply past the deadline raises `CardExpired` whether or not `expire_due` has run, and `AlreadyDecided` is the outcome only for a second reply or for the race inside one instant.
   *Author, 2026-09-20:* changed as asked: `mint` reads the wall clock and never `reply.at`; `CardExpired` past the deadline whether or not a sweep ran; `AlreadyDecided` only for a second reply or the same-instant race. Reply paragraph, Property 2, and task 5 (`test_mint_uses_wall_clock_not_reply_at`, `test_mint_refuses_expired_card_before_and_after_expire_due`) updated.
8. **`check` accepts `answered` as authority.** Line 117; Property 3, line 220. `NotAnApproval` covers `rejected` and `expired` only, so an `answered` record from a question or route card passes `check`, and `tree.approve` would take it as a contract approval; line 157 says `answered` supplies information, never authority, and the tree plan lists `approved`, `approved_with_edit`, `self_approved` as the only kinds that pass. Fix: `check` raises `NotAnApproval` for any kind outside those three; add `answered` to Property 3's refused set. Say also what `check` does when the record's `argument_sha256` is `None` and a digest is passed (M2 reads it).
   *Author, 2026-09-20:* changed as asked: `check` raises `NotAnApproval` for every kind outside {`approved`, `approved_with_edit`, `self_approved`}, and `ArgumentMismatch` when a digest is passed against a record whose `argument_sha256` is None. Property 3 and task 5 tests updated.
9. **`CARD_FIELDS` keys are fixed for three kinds only, and the string form of list and Budget values is unstated.** Line 83; the validator at line 80; task 1's `test_every_seams_field_present_and_no_extra`; task 8's byte-for-byte golden. Seams §1.12 gives `commit`, `unknown_outcome`, and `route_inbound` their fields as prose ("the headers and candidate space ids"), and `commit` carries `stop_conditions`, which `Contract` (seams §1.4) has no field for. `Card.fields` is `dict[str, str]`, so `non_goals`, `success_criteria`, `budget`, `requested`, `remaining`, and the headers need one fixed rendering before a golden test can exist. Fix: list the exact keys per kind in Design, say what fills `stop_conditions` (the deadline and ceilings, or an empty string with a finding for the lead), and name the serialization (JSON with sorted keys, or one item per line). Also reconcile "no extra" with `fields["argument_sha256"]` at line 165.
   *Author, 2026-09-20:* changed as asked: a per-kind key table in Design, `OPTIONAL_CARD_FIELDS` (`argument_sha256` on `commit` only), canonical JSON for every non-string value, and `stop_conditions` rendered from the ceilings with Finding 6 for the lead. Task 1 and task 8 read accordingly.
10. **"Objective card" is keyed by kind, and the supervisor's standing card breaks the rule.** Lines 140, 163, 173. The supervisor plan builds a `budget_increase` card with `regards` the space id and `contract_revision` None when `allocate_turn` refuses. Under this plan's kind-based rule `issue_card` would lock `objective:<space id>` and `expire_due` would transition a space id. Fix: define an objective card as one whose `objective_id` column is non-null (set by the constructors, `NULL` on the standing card), and key the lock, the revision binding, and the expiry transition on that column. Note for the lead: the supervisor assembling a `Card` by hand is what amendment 1 (line 259) set out to end; a `standing_budget_card(space, requested, remaining, expires_at)` here would keep the field table in one place.
   *Author, 2026-09-20:* changed as asked: an objective card is one whose `objective_id` column is non-null; lock, revision binding, and charge key on it. `standing_budget_card` added to Design and task 4 and proposed as amendment 9.
11. **`route_inbound_card` has no issuer and no consumer at M0.** Lines 107, 142, 154, 175; task 4. Seams §3.3 has no trigger on `inbound.unassigned`, the spaces plan defers assignment by card to M1 (`inbound-assign`), and a route card needs a conversation in `unassigned`, which exists only after a `/space unassigned` switch. The constructor and its expiry test are cheap and can stay; the Design reads as if route cards flow at M0. Fix: one line in Out of scope ("constructor and test only; nothing issues a route card until `inbound-assign`"), plus which candidate is recommended (the validator requires exactly one) and what the constructor does with one candidate or more than three (`options` is two to three).
   *Author, 2026-09-20:* changed as asked: Out of scope says nothing issues a route card until `inbound-assign`; the option table says the first candidate is recommended, one candidate pairs with `keep_unassigned`, more than three are cut to three; task 4 tests it.
12. **Two plans disagree on which record approves a contract after `grant`.** Line 169: the `budget_increase` `approved` record goes to `tree.approve` and is consumed, "so the granted increase is the contract's approval". The supervisor plan's `approval.minted` row: `tree.raise_standing(..., approval_id)` cites it, then `approvals.self_approve` and `tree.approve` with the `self_approved` record. Both pass `check`; `objective.approved` cites a different approval id in each, and `test_budget_increase_grant_is_approved_bound_to_revision` encodes this plan's version. Fix: state that the plan supports the supervisor's sequence (the test still holds, since `tree.approve` accepts the record), and ask the lead to rule which id the ledger cites; the record is consumed exactly once either way.
   *Author, 2026-09-20:* changed per the lead's ruling: the `approved` record goes to `tree.raise_standing`, which consumes it, and the commit proceeds by `self_approve` and `tree.approve`, so `objective.approved` cites the self-approval. The test now asserts only that `check` passes at the bound revision and refuses after a revision.
13. **`session.opened` is appended by a reader.** Line 122; task 3. Seams §3.7 puts the event on `open_terminal_session`, which has no connection, so a deferral is necessary; the plan defers it to `open_conversation` or `latest_conversation`, and a lookup that may return `None` should not write. Fix: one explicit `record_session(conn, space)` the entry point calls after the conversation is chosen, or state plainly that `latest_conversation` writes on a resume and why. Noted as a seams gap for the lead.
   *Author, 2026-09-20:* changed as asked: `record_session(conn, space)` is an explicit call the entry point makes after choosing the conversation; `latest_conversation` writes nothing. Amendment 11; task 3 and task 9 updated.
14. **`test_sixteen_concurrent_consumes_succeed_exactly_once` needs sixteen connections.** Task 6. `single_flight` is a transaction lock, and the `kernel` fixture in `tests/conftest.py` is one connection; sixteen `consume` calls on it serialize trivially and prove nothing. Fix: say the test opens its own sixteen `kernel_rw` connections, each in its own transaction, as spike 01's race test did.
   *Author, 2026-09-20:* changed as asked in task 6.
15. **Stale references.** Line 48 cites `tests/test_grants.py` as a pattern; the tree plan retires it (seams Round two), so the pattern is `tests/test_append_only.py` and `tests/test_rls.py`. Line 74 cites an index section, "Requirements carried from the spikes", that `docs/plans/README.md` does not have. Fix: drop or correct both lines.
   *Author, 2026-09-20:* both corrected: the pattern line names `tests/test_append_only.py` and `tests/test_rls.py` and notes the retirement; the index line is gone.

Verdict: return to author. Findings 1 and 2 break the two card paths M0 exercises (an unanswered card's expiry raises on the tree, and the verification failure card cannot pay for itself), and finding 4 leaves a constructor the seams assign here out of the plan's body.
