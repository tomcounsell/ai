# 00. Seams

**Version 4**, 2026-09-28, the build's rulings on restart recovery (list at the end). **Version 3**, 2026-09-20, applying the architect's budget and effect class ruling (`docs/reviews/2026-09-20-budget-ruling.md`): the budget is money alone, per objective, with no standing budget and no ceiling on the estimate; the effect classes are `read`, `propose`, `act`. Version 2 (2026-09-19) applied the seam amendments the lead accepted at reconcile and the architect's answers of that day; version 1 was the contract the eleven component plans were first written against. The lists of changes are at the end; a version 2 or Round two entry that names the standing node, a turn allocation, or a numbered class is superseded by version 3.

The contract between the M0 component plans. It is exact where a plan may be rough: every schema as fields, every Protocol as a signature, the kernel API a worker can call, the event types, the three execution records as rows, and which plan creates which table and module. A component plan cites this file by section number. A plan that needs a change proposes it under its own "Seam amendments" and builds against the text here until the lead applies it.

Sources: architecture §2, §3.1, §3.5, §4, §5, §6, §7, §9; tech stack §2, §3, §4, §4.1, §5, §6, §7, §8, §10, §13; spikes 01 to 08; the code in `schemas/space.py`, `migrations/versions/0001_roles_and_events.py`, `infra/models.py`, `broker/push_branch.py`, `broker/gmail.py`, `kernel/memory.py`. Where the documents are silent a line begins "Decided here:" with the reason.

## 0. Conventions

- **Ids.** Every id is a `str` holding a UUIDv7 from `uuid.uuid7()`, minted by the kernel and never by a worker or adapter. Aliases live in `schemas/ids.py`: `SpaceId`, `ObjectiveId`, `BriefId`, `ConversationId`, `TurnId`, `CardId`, `ApprovalId`, `EffectId`, `QuestionId`, `SnapshotId`, `EpisodeId`, and `new_id()`. Rows in the append-only tables use the table's bigint identity, and `EventId = int`. Decided here: UUIDv7 sorts by time, so no table needs a second ordering column.
- **Quantities are integers.** Money is `usd_micros` (1 USD = 1,000,000) and is the only budget axis (architecture §4). Time is a `deadline`, never a consumable. Decided here: spike 01 measured conservation as integer SQL arithmetic; a float in a ledger is a rounding dispute.
- **Timestamps** are timezone-aware `datetime`, stored as `timestamptz`, and never appear inside a cached prompt prefix (tech stack §8).
- **Every model in `schemas/`** inherits `Strict` from `schemas/space.py` (`extra="forbid"`). Records are frozen. Enumerations are `Literal` types in the style of `EffectClass` and `DataClass`, which exist.
- **Hashes** are lowercase hex sha256 strings in fields named `*_sha256`.
- **`schema_version`** starts at 1 on every event and record row. A shape change is a new version, never an edit; readers upcast (tech stack §10).
- **Space id `unassigned`** is the reserved space of architecture §9. It has no manifest file. `schemas/space.py` gains one constant, `UNASSIGNED_SPACE_ID = "unassigned"`, and a validator on `Space.id` refuses it, so no manifest can claim it. The kernel refuses to open an objective there. Decided here: a constant rather than a manifest, because the manifest model requires roots and the unassigned space has none.
- **Advisory locks** are `pg_advisory_xact_lock(hashtextextended(key, 0))`, 64-bit, keyed by a namespaced string. Namespaces: `thread:<conversation_id>`, `objective:<objective_id>`, `brief:<brief_id>` (a root Scribe with no objective), `approval:<approval_id>`, `effect:<idempotency_key>`. Lock order is thread before objective; nothing holding an objective lock takes a thread lock.

## 1. Schemas (`schemas/`)

One module per concept. Fields are fixed here; the module's owner (§7) writes the code exactly as listed and may add methods and validators, never fields.

### 1.1 `schemas/space.py` (exists)

`Space`, `Connector`, `RoutingRule`, `Secret`, `Retention`, `EffectClass`, `DataClass`, `Strict`, `load_space`, from architecture §9 and prereqs item 14. Unchanged except the constant and the id validator in §0, the `audience` field below, and the two changes of version 3:

Architect, 2026-09-20: `StandingBudget` and `Space.standing_budget` are removed; nothing on the manifest budgets anything (architecture §4, §9). `EffectClass` is named and ordered:

```python
EffectClass = Literal["read", "propose", "act"]
EFFECT_RANK: dict[EffectClass, int] = {"read": 0, "propose": 1, "act": 2}   # the one place order is written; covers, ceiling, and fits_within compare ranks
```

PsyOptimal's manifest reads `max_effect_class: propose`. The lead's cascade commit makes both changes in `schemas/space.py`, the manifest, and `tests/test_space.py`.

Architect, 2026-09-19: the allowed audience of an outbound message is a manifest field, sourced by default from the client's directory README in the work vault (its Contacts table), which lists addresses and may name a whole domain:

```python
class Audience(Strict):
    addresses: list[str] = Field(default_factory=list)   # exact recipient addresses
    domains: list[str] = Field(default_factory=list)     # a recipient at any of these domains is allowed
    source: str | None = None                            # where the list came from, e.g. "~/work-vault/PsyOptimal/README.md#contacts"

class Space(Strict):
    ...
    audience: Audience = Field(default_factory=Audience)
```

`recipient_allowed` (verifier, §3.6) passes when the `To:` address is in `audience.addresses`, or its domain is in `audience.domains`, or it is a `mailto:` entry in `allowed_targets`. The connector's `sender_domain` rule is no longer a source of audience. PsyOptimal's manifest gets `audience: {domains: [psyoptimal.com], addresses: [], source: "~/work-vault/PsyOptimal/README.md#contacts"}`; the README's Contacts table carries no addresses today, and adding an address column there is the architect's to do. A reader that syncs the README into the manifest is not built at M0; the manifest is the value the kernel reads, because the manifest is in the trust boundary and the vault is not.

### 1.2 `schemas/capability.py` (architecture §3.1; spike 05)

```python
CapabilityName = Literal[
    "read", "write", "bash", "ask",                      # the tool bridges, tech stack §5
    "delegate",                                          # spawn, architecture §3
    "push_branch",                                       # the `propose` typed action, tech stack §7
    "connector.read",                                    # a `read` through the broker, architecture §9
    "memory.episodic.write", "memory.operator.propose",  # the memory family, architecture §3.5
]

class Capability(Strict, frozen=True):
    name: CapabilityName
    effect_class: EffectClass   # the highest class the holder may exercise under this name
    scope: str                  # "<space_id>" or "<space_id>/<path>"; the first segment is always a space id

    def covers(self, other: "Capability") -> bool:
        # same name; EFFECT_RANK[other.effect_class] <= EFFECT_RANK[self.effect_class];
        # other.scope == self.scope or other.scope.startswith(self.scope + "/")

Capabilities = frozenset[Capability]

class Refused(Exception): ...

def issue(parent: Capabilities, requested: Capabilities) -> Capabilities:
    # returns `requested` unchanged when every element is covered by some parent element,
    # else raises Refused. Never clips, never invents. Spike 05, lifted.

def within(child: Capabilities, parent: Capabilities) -> bool: ...
def ceiling(caps: Capabilities, effect_class: EffectClass) -> Capabilities:
    # lowers every element's class to the ceiling; how the kernel derives a node's set from the
    # space's, never an answer to a request
def space_of(cap: Capability) -> SpaceId: ...   # the first scope segment
```

Subset means the covers relation on (name, effect class, scope) with the space on the scope axis (architecture §3.1). Because a scope always begins with a space id, no capability can cover a scope in another space. A space's root capability set is derived from its manifest by `kernel/spaces.py` (§3.4): every name at the manifest's `max_effect_class`, scoped to the space id. The supervisor holds `read`, `ask`, `delegate`, and the memory family, never `write` or `bash` (architecture §3.1). At M0 there is no Planner, so an Executor's `write` and `bash` are issued by the kernel on an APPROVED node from `ceiling(root_capabilities(space), contract.ceilings.max_effect_class)` (tech stack §10 over architecture §3.1; a finding records the wording gap).

### 1.3 `schemas/budget.py` (architecture §4)

```python
class Budget(Strict, frozen=True):
    """Money, and nothing else (architecture §4). Tokens are what the gateway meters; the seat's price converts."""
    usd_micros: int = Field(ge=0)

    def fits_within(self, other: "Budget") -> bool: ...   # usd_micros <= other's
    def __add__(self, other) -> "Budget": ...
    def __sub__(self, other) -> "Budget": ...             # raises ValueError below zero

ZERO = Budget(usd_micros=0)

class Ceilings(Strict, frozen=True):  # never summed, never exceeded by a child
    max_effect_class: EffectClass
    deadline: datetime
    max_data_class: DataClass
    def fits_within(self, other: "Ceilings") -> bool: ...  # class rank not above, deadline not later, data class not above (PROJECT < OPERATOR)
```

`budget_consumed` fields everywhere are a `Budget`. Conservation is the tree's property (§3.2): for every node, allocations to children plus own consumption never exceed the node's budget. Version 3: `Budget` is one integer. The five other axes (tokens, wall clock, tool calls, descendants, cards) are gone, with what replaced each in architecture §4; a caller that once passed `Budget(tool_calls=1)` or `Budget(cards=1)` now passes nothing.

### 1.4 `schemas/objective.py` (architecture §2)

```python
AssumptionStatus = Literal["open", "supported", "challenged", "refuted"]

class Assumption(Strict, frozen=True):
    statement: str
    falsification_test: str
    status: AssumptionStatus = "open"

ObjectiveState = Literal[
    "FRAMED", "AWAITING_APPROVAL", "APPROVED", "RUNNING", "VERIFYING",
    "SUCCEEDED", "FAILED", "PAUSED", "CANCELLED",
]

StateReason = Literal[
    # AWAITING_APPROVAL
    "commit", "awaiting_decision", "challenged_assumption", "budget_increase", "effect_class_elevation",
    # CANCELLED
    "deadline", "card_expired", "stopped_by_person", "subtree_revoked", "space_ended",
    # FAILED
    "verification_failed", "budget_exhausted", "worker_failed",
    # PAUSED
    "away", "paused_by_person",
    # SUCCEEDED
    "verified", "verification_sampled_out",
    # the rest
    "framed", "approved", "running", "verifying",
]

ArtifactKind = Literal["code", "document", "message", "decision_brief"]

class Contract(Strict, frozen=True):
    """The part of a node the person approves. Any change is a new revision."""
    premise: str
    non_goals: list[str]
    success_criteria: list[str] = Field(min_length=1)
    assumptions: list[Assumption]
    budget: Budget                 # the supervisor's estimate in money; the node's root once approved (architecture §4)
    basis: str = Field(max_length=500)   # one sentence: which seats, roughly how many turns, what verifying this artifact kind costs
    ceilings: Ceilings
    task_class: str                # ledger key; M0 uses "code.change" and "message.draft"
    artifact_kind: ArtifactKind    # what the Verifier judges, architecture §5
    root: str                      # one of Space.roots; where the work happens; chosen by the supervisor at framing
    inputs: list[str] = []         # inbound item ids and paths the work is about; rendered into the Executor's node block

class ReportRef(Strict, frozen=True):
    brief_id: BriefId
    event_id: int                  # the report.landed event
    summary: str

class Objective(Strict, frozen=True):
    """The projection kernel/tree.py folds from the store. Never a row itself."""
    id: ObjectiveId
    parent_id: ObjectiveId | None
    depth: int
    space: SpaceId
    conversation_id: ConversationId
    contract: Contract
    contract_revision: int
    approved_revision: int | None
    state: ObjectiveState
    state_reason: StateReason
    generation: int                # 1 + count of brief.stopped events for this node, tech stack §4
    budget_consumed: Budget
    budget_allocated: Budget       # sum of live allocations to children
    owner_brief: BriefId | None
    reports: list[ReportRef]
    evidence: list["EvidenceRef"]
    children: list[ObjectiveId]
```

Decided here: `artifact_kind` sits on the contract. Architecture §5 verifies per artifact kind and §2 does not say where the kind is recorded; the contract is the only place the person sees before work starts. Decided here: `conversation_id` is on every node, inherited from the root, so a card can find its conversation without walking the tree. `root` and `inputs` were added at reconcile: a space may have several roots (PsyOptimal has a repository and a vault directory) and the mail a reply answers is part of what the person approves. `basis` was added in version 3: the estimate is model work and the ledger records the estimate beside the actual per task class, so the reasoning behind the number travels with it. The default choice of `root` when the person names none: `code` takes the first root that is a repository, every other kind the first plain directory. At M0 a contract whose `artifact_kind` is `decision_brief` is refused at framing.

### 1.5 `schemas/brief.py` (architecture §3.1, §3.5; tech stack §4.1, §5)

```python
AgentClass = Literal["Planner", "Executor", "Verifier", "Scribe"]
Harness = Literal["pydantic_ai", "valor"]
ReportSchemaName = Literal["Report", "Verdict", "ScribeReport"]
# SandboxProfileName lives in schemas/sandbox.py (§1.9) and is imported here

ContextBlockKind = Literal[
    "voice", "instruction", "node", "path", "memory", "corrections",
    "criteria", "artifacts", "checks", "tool_log", "effect_ledger",
    "thread", "inbox", "operator_digest", "rollup", "trigger",
]

class ContextBlock(Strict, frozen=True):
    kind: ContextBlockKind
    data_class: DataClass
    text: str
    sources: list[str]     # event ids, episode ids, artifact paths, tool log seqs this block was rendered from

class ContextSlice(Strict, frozen=True):
    space: SpaceId
    blocks: list[ContextBlock]   # rendered into the prompt in this order, each under a heading naming its kind
    sha256: str                  # ContextSlice.digest(blocks): sha256 over json.dumps([b.model_dump() ...], sort_keys=True, separators=(",", ":"))

class Brief(Strict, frozen=True):
    id: BriefId
    objective_id: ObjectiveId | None   # None only for a Scribe brief fired from a conversation turn with no open objective
    space: SpaceId
    agent_class: AgentClass
    harness: Harness
    generation: int
    context_slice: ContextSlice
    instruction: str | None        # Scribe explicit trigger; None otherwise
    max_data_class: DataClass      # the kernel refuses a Brief with a block above it
    budget: Budget
    ceilings: Ceilings
    capabilities: Capabilities
    sandbox_profile: SandboxProfile
    model_ref: str                 # pinned id from infra/models.yaml
    gateway_token: SecretStr       # the only field never stored; its sha256 is
    report_schema: ReportSchemaName
    issued_at: datetime

class BriefToken(Strict, frozen=True):
    """What every kernel API call carries. Refused when generation is stale."""
    brief_id: BriefId
    generation: int

class DelegateRequest(Strict, frozen=True):
    """What a caller hands kernel/tree.py; the kernel fills the rest of the Brief."""
    objective_id: ObjectiveId | None   # None only for the implicit Scribe of an objective-less turn
    agent_class: AgentClass
    budget: Budget
    capabilities: Capabilities
    sandbox_profile: SandboxProfileName
    report_schema: ReportSchemaName
    max_data_class: DataClass
    context_slice: ContextSlice
    instruction: str | None = None
    snapshot: SnapshotRef | None = None   # required for verify: the snapshot to judge; ignored otherwise
```

The block kinds each agent class receives (architecture §3.1, §3.5, §5): Executor and Planner get `voice`, `node`, `path`, `memory`, `corrections`, all PROJECT. Verifier gets `voice`, `instruction` (the seat paragraph and the kind checklist from `prompts/verifier/`), `criteria` (the approved Contract's premise, non_goals, success_criteria, artifact_kind, task_class, max_data_class), `artifacts`, `checks`, `tool_log`, `effect_ledger`, all PROJECT, and never a Report's summary or evidence, a `node`, `path`, or `memory` block. Scribe gets the supervisor's rendered turn as blocks, which may be OPERATOR, plus `instruction`; the roll-up travels as `rollup` and the trigger as `trigger`. The `path` block is the compressed path to root. The adapter offers a class only the tools its Brief's capabilities cover; a Verifier Brief carries `read` and `bash` and no `ask`.

### 1.6 `schemas/report.py` (architecture §3.1, §5)

```python
class ArtifactRef(Strict, frozen=True):
    kind: ArtifactKind
    path: str            # inside the mount, e.g. /work/src/x.py or /work/reply.md
    sha256: str          # taken on the host side of the mount by the tool bridge, never by the model

EvidenceKind = Literal["test_output", "diff", "measurement", "excerpt", "tool_log_seq", "effect"]

class EvidenceRef(Strict, frozen=True):
    kind: EvidenceKind
    ref: str             # a path, a tool log seq, an effect id, or a URL
    sha256: str | None

class AssumptionDelta(Strict, frozen=True):
    statement: str                                        # equals Contract.assumptions[i].statement
    status: AssumptionStatus
    evidence: list[EvidenceRef] = Field(min_length=1)     # a challenge without evidence is refused, architecture §2

class Report(Strict, frozen=True):
    """output_type of the Executor and Planner loop."""
    artifact_refs: list[ArtifactRef]
    evidence: list[EvidenceRef]
    assumption_deltas: list[AssumptionDelta]
    summary: str = Field(max_length=2000)

class CheckResult(Strict, frozen=True):
    """A deterministic check the kernel ran before the Verifier read any prose."""
    name: str            # tests | build | citations_resolve | sections_present | length | recipient_allowed | no_operator_content
    passed: bool
    output_sha256: str
    detail: str = Field(max_length=2000)

class CitationResolution(Strict, frozen=True):
    citation: str
    resolved: bool
    excerpt: str | None          # required when resolved is True
    excerpt_sha256: str | None

class CriterionResult(Strict, frozen=True):
    criterion: str               # equals Contract.success_criteria[i]
    met: bool | None             # None is abstain
    reason: str = Field(max_length=1000)

VerdictOutcome = Literal["pass", "fail", "abstain"]

class Verdict(Strict, frozen=True):
    """output_type of the Verifier loop."""
    outcome: VerdictOutcome
    predicted_failure: float = Field(ge=0, le=1)
    criteria: list[CriterionResult] = Field(min_length=1)
    scope_findings: list[str]
    summary: str = Field(max_length=2000)
    # validator: outcome equals the outcome derived from criteria: any met False is fail,
    # else any None is abstain, else pass; a disagreement fails validation

class ScribeReport(Strict, frozen=True):
    """Architecture §3.5: what was written and where, what was proposed, what it could not do."""
    written: list[EpisodeId]
    proposed: list[str]          # belief proposal ids
    could_not: list[str]
```

Decided here: the ~500 token summary cap of architecture §3.1 is enforced as 2,000 characters, because a character cap is checkable without a tokenizer. The artifact hash on `ArtifactRef` is filled by the tool bridge at report time from a host-side read it records in the tool log, whatever the model wrote, and `kernel/runs.py` fails a terminal whose hashes disagree with the durable rows. The `citations_resolve` check writes one `CitationResolution` per citation; a document whose resolved citations carry no excerpt fails the check (prereqs item 18). A URL citation carries the passage it relies on in quotes; the check confirms the passage verbatim in the fetched body and records it as the excerpt. A `message` artifact begins with `To:` and `Subject:` header lines, a blank line, then the body; the Executor prompt says so.

### 1.7 `schemas/trace.py` (tech stack §5; spike 08)

```python
class Question(Strict, frozen=True):
    question_id: QuestionId
    text: str = Field(max_length=2000)
    tool_seq: int                # the tool.start seq of the ask that raised it

class Terminal(Strict, frozen=True):
    outcome: Literal["report", "aborted", "failed"]
    report: Report | Verdict | ScribeReport | None   # present iff outcome == "report"; validated against Brief.report_schema
    error: str | None

class TraceEvent(Strict, frozen=True):
    brief_id: BriefId
    generation: int
    at: datetime
    kind: Literal["question", "terminal"]
    question: Question | None
    terminal: Terminal | None
```

Tool invocations do not travel as trace events. They are tool log rows (§5.2) that the adapter records through the kernel API before and after each call. A `question` pauses the worker until `answer()`; a `terminal` is the last event of a run.

### 1.8 `schemas/events.py` (tech stack §3, §10)

```python
class Event(Strict, frozen=True):
    id: int
    space_id: SpaceId
    type: EventType          # the Literal of every name in §4; EVENT_TYPES and CURRENT_VERSION beside it
    schema_version: int
    occurred_at: datetime
    payload: dict[str, Any]  # the fields §4 lists for the type
```

Mirrors the `events` table that migration 0001 created.

### 1.9 `schemas/sandbox.py` (tech stack §6; spikes 06, 07, 08)

```python
SandboxProfileName = Literal["scratch", "verify", "worktree"]

class SandboxProfile(Strict, frozen=True):
    name: SandboxProfileName
    space: SpaceId
    mount_source: str                    # host path: a root itself (scratch), the extracted snapshot (verify), the objective worktree (worktree)
    mount_target: str = "/work"
    readonly: bool                       # True for scratch and verify
    network: Literal["hostonly", "none"] # Architect, 2026-09-19: hostonly for every profile at M0; "none" is reserved for the day the runtime offers a no-network mode and is refused by the validator until then
    key: str                             # objective id for worktree; brief id otherwise
    env: dict[str, str]                  # env var name -> Keychain item name for class-labeled space secrets, worktree only; values are read by the adapter at create() and never stored
    image: str = "cori-base:3.14"
    # validator: the profile table above is pinned; mount_source is absolute

class SandboxHandle(Strict, frozen=True):
    id: str                              # container name
    profile: SandboxProfile
    created_at: datetime

class ExecResult(Strict, frozen=True):
    exit_status: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool

class StopReceipt(Strict, frozen=True):
    handle_id: str
    killed_at: datetime
    confirmed_dead_at: datetime          # by a probe from outside the VM, never by the CLI's return (spike 07)
    probe: str                           # what was probed

class SnapshotRef(Strict, frozen=True):
    id: SnapshotId
    handle_id: str
    path: str                            # host path of the archive of the mount, never the rootfs (spike 06)
    sha256: str                          # of the archive
    files: dict[str, str]                # relative path -> sha256, hashed on the host side
    taken_at: datetime
```

### 1.10 `schemas/effect.py` (tech stack §7; spike 02)

```python
class Action(Strict, frozen=True):
    """Base of every typed action. Each subclass fixes effect_class and action_type as ClassVars."""
    space: SpaceId
    objective_id: ObjectiveId
    brief_id: BriefId
    idempotency_key: str                 # derived from the fields by a validator; a supplied key that differs is refused
    effect_class: ClassVar[EffectClass]
    action_type: ClassVar[str]
    def target(self) -> str: ...         # matched against Space.allowed_targets
    def payload_sha256(self) -> str: ... # over canonical JSON of model_dump(mode="json")

class PushBranch(Action):                # effect_class = "propose"; action_type = "push_branch"; broker/push_branch.py as it exists
    repo: str                            # owner/name
    branch: str                          # equals f"cori/{objective_id}"; anything else is refused before any subprocess
    source_dir: str                      # the worktree's host mount; filled by the tool bridge, never by the model
    head_sha: str
    # idempotency_key = f"{repo}#{branch}@{head_sha}"; target() = "github.com/" + owner

class ConnectorRead(Action):             # effect_class = "read"; action_type = "connector_read"; broker/gmail.py as it exists
    connector: Literal["gmail"]
    account: str
    query: str                           # a worker-requested query is "id:<external_id>" and nothing else; the broker composes every other query from the connector's routing rule
    # target() = "mailto:" + account

# Reserved names, models arrive with their milestones (tech stack §7, §10):
# `propose` at M2: OpenPr, PostMessageDraft, CalendarHold. `act` at M3: Merge, Send, Deploy, Pay.

EffectOutcomeKind = Literal["done", "refused", "unknown", "recovered", "failed"]

class EffectOutcome(Strict, frozen=True):
    effect_id: EffectId
    kind: EffectOutcomeKind              # failed: the action raised and the target confirms the key is absent; no card, only a report
    result: dict[str, Any]               # push_branch: {"sha": ...}; connector read: {"headers": {...}, "body": str}
    error: str | None
```

The effect protocol is intent row, action, outcome row (§5.3). On restart with a dangling intent the broker queries the target by the idempotency key before re-running; a target that cannot be queried yields `unknown` and an `unknown_outcome` card. At M0 the code objective ends at commit (tech stack §10): `push_branch` is built and live-tested and no worker tool calls `request_effect`; the first worker-requested effect arrives at M2.

### 1.11 `schemas/inbound.py` (architecture §9)

```python
class InboundItem(Strict, frozen=True):
    id: str
    connector: Literal["gmail", "calendar", "messaging"]
    account: str
    external_id: str                     # the provider's message id
    headers: dict[str, str]              # lowercase keys: from, to, subject, date, message-id
    received_at: datetime
    space: SpaceId                       # the routed space, or "unassigned"
    routed_by: str | None                # the rule that matched, rendered as text
```

The body of an item is a separate `read` the broker performs only for an item in an assigned space; an unassigned item is header-only (architecture §9). At M0 the Gmail read is scoped by the connector's rule, as `broker/gmail.py` exists, so `unassigned` is reached by tests and by any connector added later.

### 1.12 `schemas/approval.py` (architecture §6, §7; tech stack §13)

```python
class Conversation(Strict, frozen=True):
    id: ConversationId
    space: SpaceId
    opened_at: datetime

CardKind = Literal[
    "commit", "question", "budget_increase", "scope_change", "effect_class_elevation",
    "verification_failure", "unknown_outcome", "ethics_flag", "route_inbound",
]

class Option(Strict, frozen=True):
    key: str
    label: str
    recommended: bool = False

class Card(Strict, frozen=True):
    """Rendered by the kernel from structured fields. Agent prose only in `note`."""
    id: CardId
    kind: CardKind
    space: SpaceId
    conversation_id: ConversationId
    regards: str                         # the objective id for objective cards; the effect id for unknown_outcome; the inbound item id for route_inbound
    contract_revision: int | None        # the objective's current revision on objective cards; None otherwise
    fields: dict[str, str]               # kind-specific, table below; CARD_FIELDS in the module
    options: list[Option] = Field(min_length=2, max_length=3)   # exactly one recommended
    note: str = Field(max_length=500)
    expires_at: datetime                 # the objective's deadline; route_inbound: seven days
    issued_at: datetime

class Reply(Strict, frozen=True):
    """What an adapter relays. Never an approval; the kernel mints that."""
    card_id: CardId
    option: str
    edit: dict[str, str] | None          # edited contract fields, commit cards only
    raw_message: str
    session_id: str
    at: datetime

class Utterance(Strict, frozen=True):
    conversation_id: ConversationId
    text: str
    session_id: str
    at: datetime

class Correction(Strict, frozen=True):
    """A kernel-typed utterance, architecture §6."""
    conversation_id: ConversationId
    space: SpaceId
    what_was_wrong: str
    what_is_right: str
    regards: str | None
    raw_message: str
    session_id: str
    at: datetime

class SpaceSwitch(Strict, frozen=True):
    conversation_id: ConversationId
    to_space: SpaceId
    session_id: str
    at: datetime

Inbound = Utterance | Reply | Correction | SpaceSwitch

ApprovalKind = Literal["approved", "approved_with_edit", "rejected", "answered", "self_approved", "expired"]

class ApprovalRecord(Strict, frozen=True):
    """Minted by kernel/approvals.py and nowhere else."""
    id: ApprovalId
    card_id: CardId
    kind: ApprovalKind
    space: SpaceId
    objective_id: ObjectiveId | None
    contract_revision: int | None        # the revision the person said yes to
    argument_sha256: str | None          # digest of the exact invocation batch; effect cards only
    raw_message: str
    session_id: str
    decided_at: datetime
```

Card fields by kind: `commit` carries premise, non_goals, success_criteria, budget (dollars, one figure), basis, effect_ceiling, deadline, stop_conditions, audience; `question` carries brief_id, question_id, question; `verification_failure` carries brief_id, verdict_summary, failed_criteria; `unknown_outcome` carries effect id, action type, idempotency key; `budget_increase` carries brief_id, spent, produced (artifact paths from the tool log), requested; `route_inbound` carries the headers and candidate space ids. Options and the approval kind each reply maps to: `commit` approve, edit, reject; `question` answer, abort; `verification_failure` retry, cancel; `budget_increase` grant, deny; `route_inbound` the candidate space ids (kind `answered`). Consumption is the event `approval.consumed`, never a field, so the record stays immutable and "consumed once" is a count.

Architect, 2026-09-20 (superseding 2026-09-19): there is no cap on a self-approved contract's budget; the supervisor's estimate is the only bound at `read` and `propose`, and the ledger of estimate against actual is what would justify one. At `read` and `propose` the supervisor self-approves and tells the person the figure, or issues the `commit` card when it judges the person would want to be asked (architecture §2, §7); an `act` contract always waits on the `commit` card. A self-approval is still an `ApprovalRecord` of kind `self_approved` with an empty raw message and session id `kernel`; the commit card is written unissued (`cards.issued = false`) so `card_id` stays required and the ledger has one shape for every commit. The person sees a self-approved contract as prose in the conversation, budget and basis included, and an objection is a correction and a revision. The `budget_increase` card is issued only on overrun (architecture §4): the node is `FAILED/budget_exhausted`, and a `grant` reply mints an `approved` record, raises the root through `tree.raise_budget`, and re-approves through `tree.approve`. Decided here: at M0 a `Correction` is recorded as the event `correction.recorded` and the supervisor renders the `corrections` block from those events for the space; at M1 it becomes a Belief of source class `correction` and the render moves to the operator record.

### 1.13 `schemas/belief.py` (architecture §6; schema only at M0)

```python
BeliefKind = Literal["goal", "preference"]
SourceClass = Literal["direct", "correction", "decision", "inferred"]
BeliefStatus = Literal["ACTIVE", "QUARANTINED", "RETIRED", "SUPERSEDED"]

class Belief(Strict, frozen=True):
    id: str
    statement: str
    kind: BeliefKind
    scope: SpaceId | Literal["global"]
    domain: str
    source_class: SourceClass
    supporting_events: list[int]
    status: BeliefStatus
    supersedes: str | None
    last_confirmed_at: datetime | None
    test: str
```

### 1.14 `schemas/memory.py` (architecture §6; tech stack §3.1)

```python
EpisodeKind = Literal["turn", "report", "summary", "decision"]

class EpisodeWrite(Strict, frozen=True):
    space: SpaceId
    kind: EpisodeKind
    text: str
    provenance: list[int]        # event ids; min_length=1 for summary and decision
    data_class: DataClass
    regards: str | None = None   # conversation, objective, or brief id

class MemoryHit(Strict, frozen=True):
    space: SpaceId
    episode_id: EpisodeId
    kind: EpisodeKind
    text: str
    provenance: list[int]
    written_at: datetime
    score: float
    regards: str | None
    data_class: DataClass
    text_sha256: str

class SliceQuery(Strict, frozen=True):
    space: SpaceId
    query: str
    k: int = Field(ge=1, le=20)
    kinds: list[EpisodeKind] | None = None
    regards: str | None = None
    max_data_class: DataClass = "PROJECT"

class BeliefProposal(Strict, frozen=True):
    space: SpaceId
    statement: str
    kind: BeliefKind
    domain: str
    supporting_events: list[int] = Field(min_length=1)
    test: str
    proposed_source_class: SourceClass   # the kernel derives the ceiling from the evidence authors; M0 records and never reads
```

### 1.15 `schemas/context.py` (architecture §1, §6; tech stack §8)

```python
SliceName = Literal["procedural", "voice", "operator_digest", "rollup", "thread_summary", "recent_turns", "inbox", "on_demand"]

class SliceStat(Strict, frozen=True):
    name: SliceName
    tokens: int
    sha256: str
    cache_breakpoint: bool

class ContextManifest(Strict, frozen=True):
    turn_id: TurnId
    space: SpaceId
    trigger_event_id: int
    model_ref: str
    slices: list[SliceStat]      # in render order
    corrections_rendered: list[int]   # event ids at M0
    total_tokens: int
    sha256: str                  # over the rendered text
```

### 1.16 `schemas/gateway.py` (tech stack §4)

```python
class Usage(Strict, frozen=True):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    usd_micros: int              # computed by the gateway from infra/models.yaml::usd_per_mtok with ceiling rounding per usage field
    charged_reserved: bool       # True when the call ended without a message_delta and was charged max_tokens

GatewayEvent = Literal["request", "response", "cut", "refused", "upstream_error", "token_issued", "token_revoked"]
RefusalReason = Literal["unknown_token", "revoked", "model", "stale_generation", "budget"]
CacheState = Literal["write", "read", "none", "unstable"]
```

### 1.17 `schemas/records.py` (tech stack §4; spikes 03, 08)

Pydantic mirrors of the three record rows in §5: `GatewayLogRecord`, `ToolLogRecord`, `EffectLedgerRecord`, with the same field names and types as the columns. `ToolLogRecord` is what the adapter passes to `KernelAPI.record_tool`. Beside them, `Invocation(start: ToolLogRecord, end: ToolLogRecord | None, closed_by: Literal["end", "terminal"] | None)`, the pairing the Verifier reads.

## 2. Ports (`ports/`)

Signatures only. One implementation each (tech stack, selection rule).

### 2.1 `ports/worker.py` (tech stack §5)

```python
class Worker(Protocol):
    def run(self, brief: Brief, handle: SandboxHandle) -> AsyncIterator[TraceEvent]: ...
    async def answer(self, brief_id: BriefId, question_id: QuestionId, text: str) -> None: ...
    async def abort(self, brief_id: BriefId) -> None: ...
```

Semantics the implementation must meet, from spike 08: after yielding a `question`, `run` makes zero gateway requests until `answer()` arrives; the last event of every run is a `terminal`; `abort()` during a wait ends the run with `terminal(outcome="aborted")` and nothing after it; tool calls in one model turn may run concurrently, so tool log rows are paired by `seq`, never by position; every `tool.start` is durable through `KernelAPI.record_tool` before the sandbox call begins. The kernel creates the sandbox and passes the handle; the adapter never calls `create`, `snapshot`, `stop`, or `destroy`. The adapter offers only the tools the Brief's capabilities cover. The PydanticAI adapter takes a `SandboxProvider`, a `KernelAPI`, the gateway base URL, and the class prompts. The `valor` harness has no adapter at M0.

### 2.2 `ports/sandbox.py` (tech stack §6)

```python
class SandboxProvider(Protocol):
    async def create(self, profile: SandboxProfile) -> SandboxHandle: ...
    async def exec(self, h: SandboxHandle, cmd: str, *, timeout: float) -> ExecResult: ...
    async def read(self, h: SandboxHandle, path: str) -> bytes: ...
    async def write(self, h: SandboxHandle, path: str, data: bytes) -> None: ...
    async def snapshot(self, h: SandboxHandle, *, snapshot_id: SnapshotId) -> SnapshotRef: ...
    async def stop(self, h: SandboxHandle) -> StopReceipt: ...
    async def destroy(self, h: SandboxHandle) -> None: ...
```

Decided here: `stop` is the seventh operation. Tech stack §4 requires a compute stop with the disk retained and §6 lists six operations without one; `destroy` cannot serve because it removes the container. `stop` is `container kill` followed by a probe from outside the VM, and returns only when the probe confirms execution is dead (spike 07). Architect, 2026-09-19: every profile boots on the host-only network and nothing more is done to restrict it at M0; no link-down inside the guest, no ping probe, no packet filter. The Mac's own listeners are the boundary (the gateway refuses anything without a token, and Postgres and Redis accept loopback only), and the tech stack §6 table's `none` rows change to host-only until apple/container offers a no-network mode. `read` and `write` of a path under the mount are served from the host side of the mount, so the tool bridge hashes artifacts without an exec (spike 08); a path that escapes the mount by symlink is refused. `snapshot` archives and hashes the mount on the host, never the rootfs (spike 06), with the id the kernel passes; it is taken after `stop`, so the archive is of a quiet disk. `destroy` after `stop` removes the container and, for `verify`, the extracted artifact directory; `scratch` binds a root itself and `destroy` removes nothing for it; a `worktree` mount outlives its containers and is removed only by retention or by ending the space. A `verify` sandbox is always a fresh container from the kernel-built image (architecture §5). `infra/sandbox/mounts.py::profile_for(name, space, key, *, artifact_kind, max_data_class="PROJECT", snapshot=None) -> SandboxProfile` prepares the host directory and fills `mount_source`, `image`, and `env`; `tree.delegate` calls it and never builds a profile by hand.

### 2.3 `ports/surface.py` (tech stack §13)

```python
class ApprovalSurface(Protocol):
    async def open(self, conversation: Conversation) -> None: ...
    async def show(self, card: Card) -> None: ...
    async def say(self, conversation_id: ConversationId, text: str) -> None: ...
    def inbound(self) -> AsyncIterator[Inbound]: ...
```

An adapter renders cards and relays replies over a session the kernel owns. It never constructs an `ApprovalRecord`; `kernel/approvals.py` mints one from a `Reply`. On the CLI, `session_id` is the terminal session the kernel attributed to the person's user at start (`approvals.open_terminal_session`).

### 2.4 `ports/kernel.py`: the worker's door

```python
class KernelAPI(Protocol):
    async def record_tool(self, token: BriefToken, record: ToolLogRecord) -> int: ...
    async def raise_question(self, token: BriefToken, text: str, tool_seq: int) -> QuestionId: ...
    async def request_effect(self, token: BriefToken, action: Action) -> EffectOutcome: ...
    async def read_slice(self, token: BriefToken, query: SliceQuery) -> list[MemoryHit]: ...
    async def write_episode(self, token: BriefToken, write: EpisodeWrite) -> EpisodeId: ...
    async def propose_belief(self, token: BriefToken, proposal: BeliefProposal) -> str: ...
    async def delegate(self, token: BriefToken, request: DelegateRequest) -> BriefId: ...
```

Decided here: tech stack §2 names "an authenticated kernel API call carrying a Brief-scoped token" as the one way a worker reaches the store, and the import rule forbids `workers/` and `adapters/` from importing `kernel/`, so the door is a Protocol in `ports/` that the adapter receives at construction and `kernel/api.py` implements. Every call: refuses a stale generation (`brief.generation < objective.generation`), refuses a space other than the Brief's, checks the capability the call needs (`record_tool` needs the tool's name, with `edit` under `write`; `raise_question` needs `ask`; `request_effect` needs the action's name at its class; `read_slice` needs `read`; `write_episode` needs `memory.episodic.write`; `propose_belief` needs `memory.operator.propose`; `delegate` needs `delegate`). No call consumes budget: money is spent at the gateway and nowhere else (version 3). `record_tool` returns after the row is durable, refuses any row after a `terminal`, and refuses a `question` row whose id was not minted by `raise_question`. `read_slice` replaces `query.max_data_class` with the Brief's `max_data_class`, so a worker cannot widen its own slice. `request_effect` is refused for every worker at M0 (ruling 9). `delegate` is refused for every worker at M0; the Planner arrives at M1 and the supervisor delegates through `kernel/tree.py` directly.

## 3. Kernel API: internal signatures

Functions inside `kernel/`, called by other kernel modules. The `conn` argument is a psycopg async connection inside a transaction the caller opened, except where a signature says otherwise.

### 3.1 `kernel/events.py`

```python
async def append(conn, *, space_id: SpaceId, type: EventType, payload: dict, schema_version: int = 1) -> int
async def read(conn, *, space_id: SpaceId, after: int = 0, types: Sequence[EventType] | None = None, limit: int = 1000) -> list[Event]
async def read_for(conn, *, space_id: SpaceId, key: str, value: str) -> list[Event]   # payload[key] == value, e.g. objective_id
@asynccontextmanager
async def single_flight(conn, key: str) -> AsyncIterator[None]   # pg_advisory_xact_lock(hashtextextended(key, 0)); refuses an autocommit connection
def upcast(event: Event) -> Event       # to the current schema_version of its type; read and read_for always upcast
```

### 3.2 `kernel/tree.py`

```python
class TokenIssuer(Protocol):            # the §3.9 pair; None means the gateway module
    async def issue_token(self, conn, *, brief_id: str, generation: int, model_ref: str, space: SpaceId) -> SecretStr: ...
    async def revoke(self, conn, brief_id: str) -> None: ...

async def open_objective(conn, *, space: SpaceId, conversation_id: ConversationId, contract: Contract, spaces: Mapping[SpaceId, Space] | None = None) -> ObjectiveId
    # spaces.check_may_open first; refuses a ceiling above the space's and a past deadline; state FRAMED, revision 1
async def revise_contract(conn, objective_id: ObjectiveId, contract: Contract) -> int          # FRAMED or AWAITING_APPROVAL only; new revision; clears approved_revision
async def approve(conn, objective_id: ObjectiveId, approval: ApprovalRecord) -> None          # approvals.check then sets approved_revision; refuses a stale revision and a self_approved record on an `act` contract
async def transition(conn, objective_id: ObjectiveId, state: ObjectiveState, reason: StateReason) -> None
    # only the edges in the tree plan's table; a transition into CANCELLED, FAILED, or AWAITING_APPROVAL calls stop() in the same transaction
async def delegate(conn, request: DelegateRequest, *, issuer: Capabilities, parent_brief: BriefId | TurnId | None,
                   token_issuer: TokenIssuer | None = None, spaces: Mapping[SpaceId, Space] | None = None) -> Brief
    # under single_flight("objective:<id>"), or none for an objective-less Scribe: capabilities via issue()
    # from the effective issuer (the node's set when parent_brief is None or a TurnId; the worker's Brief set otherwise);
    # sandbox profile from infra.sandbox.mounts.profile_for(...); model_ref from the seat file by agent class;
    # gateway token from token_issuer.issue_token; budget allocated in budget_ledger from the objective, whatever
    # parent_brief is (a TurnId only says who asked); an objective-less Scribe is its own root, one `grant` row under
    # node_id = its brief id for the kernel-set amount (version 3); refuses while a stopped Brief on the node is
    # unconfirmed; events brief.issued and, for an Executor on APPROVED, objective.state_changed to RUNNING
async def land_report(conn, token: BriefToken, terminal: Terminal) -> None
    # event report.landed or brief.failed; an Executor's challenged assumptions transition the holding ancestor;
    # an Executor report transitions RUNNING to VERIFYING; a Verdict terminal transitions the node: pass to
    # SUCCEEDED/verified, fail to FAILED/verification_failed, abstain to AWAITING_APPROVAL/awaiting_decision;
    # this is the one writer of the verdict transition
async def stop(conn, objective_id: ObjectiveId, reason: StateReason, *, token_issuer: TokenIssuer | None = None) -> list[BriefId]
    # one transaction: event brief.stopped (generation += 1) and token_issuer.revoke for every live brief;
    # the caller passes the returned briefs to kernel.runs.stop outside the transaction
async def confirm_stop(conn, brief_id: BriefId, receipt: StopReceipt) -> None            # event brief.stop_confirmed; delegate refuses on a node with a stopped, unconfirmed brief
async def check_generation(conn, token: BriefToken) -> None                               # raises StaleGeneration
async def remaining(conn, id: BriefId | ObjectiveId) -> Budget
async def consume(conn, brief_id: BriefId, amount: Budget, *, incurred: bool = False) -> Budget
    # under the node's advisory lock; incurred=False refuses past zero (BudgetExceeded); incurred=True records the
    # full amount, clamps remaining at zero, and appends budget.overrun with the shortfall; no generation check,
    # because a cut call's charge lands after the stop that cut it. The gateway is the only caller (version 3).
async def release(conn, brief_id: BriefId) -> Budget                                       # unspent allocation back to the parent on terminal; idempotent
async def raise_budget(conn, objective_id: ObjectiveId, *, by: Budget, approval_id: ApprovalId) -> None
    # a person raises the root (architecture §4): only with an `approved` record from a budget_increase card; one `grant` row
    # under the objective's lock; the supervisor calls it on the grant reply, then approve
async def expire_due(conn) -> list[ObjectiveId]                                            # every live node past its deadline transitions CANCELLED/deadline, which stops its briefs
async def project(conn, objective_id: ObjectiveId) -> Objective                            # fold(events, ledger rows, children); the fold is a pure function
```

### 3.3 `kernel/supervisor.py` and `kernel/context.py`

```python
# supervisor
async def serve(surface: ApprovalSurface, worker: Worker, spaces: dict[SpaceId, Space], seats: SeatFile) -> None
    # the process loop: inbound dispatch, event polling with the projection check, run_brief starts
async def turn(trigger: Event) -> TurnId                       # under single_flight("thread:<conversation_id>"); renders, calls the model through the gateway, acts
async def answer(brief_id: BriefId, question_id: QuestionId, text: str, *, answered_by: str = "supervisor") -> None
    # appends question.answered and calls kernel.runs.answer; the adapter writes the answer row
async def frame(conversation_id: ConversationId, utterance: Utterance) -> ObjectiveId | None  # the framing turn; None when it asked instead
# context
async def render_turn(space: SpaceId, trigger: Event) -> tuple[str, ContextManifest]  # deterministic; volatility-ordered; breakpoints per the seat file's min_cache_prefix
async def render_brief_slice(objective_id: ObjectiveId, agent_class: AgentClass) -> ContextSlice
```

Triggers at M0: `message.received`, `correction.recorded`, `verdict.recorded`, `brief.failed`, `question.raised`, `approval.minted`, `card.expired`, `objective.state_changed` to `AWAITING_APPROVAL/challenged_assumption`. `report.landed` is not a trigger: verification is kernel-driven from `kernel/runs.py` (§3.5). On `verdict.recorded` the turn issues the `verification_failure` card for a fail or abstain and says; it never transitions.

### 3.4 `kernel/spaces.py`

```python
def load_all(directory: Path = SPACES_DIR) -> dict[SpaceId, Space]       # infra/spaces/*.yaml; refuses duplicate ids, the reserved id, and identical rules across spaces
def root_capabilities(space: Space) -> Capabilities
def check_may_open(space_id: SpaceId, spaces: dict[SpaceId, Space]) -> None   # raises SpaceRefused for "unassigned" or an unknown space
async def mint_read_token(conn, space: SpaceId, ttl_s: int = 60) -> str        # row in read_tokens; secrets.token_urlsafe
async def bind_read_token(conn_ro, token: str) -> None                         # set_config('cori.read_token', token, true): transaction-local; the render runs inside one transaction
def route(item: InboundItem, spaces: dict[SpaceId, Space]) -> tuple[SpaceId, str | None]   # deterministic and total; "unassigned" when no rule or more than one matches
async def record_inbound(conn, item: InboundItem) -> bool                      # row in inbound_items; event inbound.routed or inbound.unassigned; False when the row already existed
async def ingest(conn, spaces: dict[SpaceId, Space], items: Iterable[InboundItem]) -> list[InboundItem]   # route, copy with space and routed_by, record; returns the newly recorded items
async def poll_connectors(conn, spaces, read_recent, *, lookback_days: int = 7) -> list[InboundItem]     # read_recent is broker.read_recent, passed in so kernel/spaces.py never imports broker/
```

### 3.5 `kernel/runs.py`

```python
async def run_brief(brief: Brief) -> Terminal
    # profile is brief.sandbox_profile; handle = sandbox.create(profile); register with the door; drive Worker.run(brief, handle)
    # on question: event question.raised (the supervisor's trigger)
    # on terminal: check every ArtifactRef.sha256 against the durable tool log (fail the brief on disagreement);
    #   tree.land_report; tree.release; sandbox.stop; tree.confirm_stop; for worktree with outcome report:
    #   snapshot (id minted here) and event snapshot.taken; destroy for scratch and verify;
    #   for an Executor report: verify.verify_objective(objective_id, snapshot)
async def answer(brief_id: BriefId, question_id: QuestionId, text: str) -> None    # Worker.answer
async def stop(brief_ids: list[BriefId]) -> list[StopReceipt]
    # for each: Worker.abort, await the task, sandbox.stop (probe-confirmed), tree.confirm_stop, the terminal row
    # for the old generation written kernel-side, tree.release; the disk stays
```

### 3.6 `kernel/verify.py`

```python
def should_verify(effect_class: EffectClass, *, leaves_space: bool) -> tuple[bool, float]   # 1.0 for anything that leaves the space and for `act`; sampled for sandbox-only work; M0 is 1.0 everywhere (architecture §5)
async def run_checks(objective: Objective, snapshot: SnapshotRef) -> list[CheckResult]   # in a fresh verify sandbox from profile_for("verify", ..., snapshot=...); rows in checks and event checks.recorded before any prose is read
async def render_verifier_slice(objective: Objective, checks: list[CheckResult]) -> ContextSlice   # the seven Verifier blocks of §1.5; never the gateway log or the Report summary
async def record_verdict(conn, brief: Brief | None, verdict: Verdict, sampled_with: float, *, model_ref: str, prompt_sha256: str) -> None
    # records the row and the event; never transitions
async def verify_objective(objective_id: ObjectiveId, snapshot: SnapshotRef) -> Verdict | None
    # called by runs.run_brief: VERIFYING; sample; checks; a failed check records a kernel verdict (model_ref "kernel")
    # and transitions FAILED/verification_failed directly; otherwise delegate the Verifier (issuer root_capabilities(space),
    # capabilities read@read and bash@read, profile verify, snapshot) and run_brief it; the Verifier's terminal reaches
    # tree.land_report, which transitions
```

### 3.7 `kernel/approvals.py`

```python
def open_terminal_session(fd: int = 0) -> str          # refuses unless the fd is a tty owned by the process uid; event session.opened
def session_is_live(session_id: str) -> bool
def attach(surface: ApprovalSurface) -> None
async def open_conversation(conn, space: SpaceId) -> Conversation
async def latest_conversation(conn, space: SpaceId) -> Conversation | None
async def switch_space(conn, conversation_id: ConversationId, to_space: SpaceId) -> Conversation   # events space.switched (with new_conversation_id) then conversation.opened; seals the thread
async def pending_cards(conn, conversation_id: ConversationId) -> list[Card]
def question_card(objective: Objective, brief: Brief, question: Question) -> Card
def verification_failure_card(objective: Objective, brief: Brief, verdict: Verdict) -> Card
def budget_increase_card(objective: Objective, brief_id: BriefId, spent: Budget, produced: list[str], requested: Budget) -> Card
def commit_card(objective: Objective, audience: str) -> Card
def route_inbound_card(item: InboundItem, candidates: list[SpaceId]) -> Card
async def issue_card(conn, card: Card, *, now: datetime | None = None) -> None   # event card.issued; ApprovalSurface.show; charges nothing (version 3)
async def mint(conn, reply: Reply) -> ApprovalRecord    # the only constructor; event approval.minted; refuses an unknown session, a decided or expired card
async def self_approve(conn, objective_id: ObjectiveId, revision: int) -> ApprovalRecord   # read and propose only; unissued commit card plus the record
async def consume(conn, approval_id: ApprovalId) -> None        # event approval.consumed with by = the card's regards; refuses a second consumption
async def expire_due(conn, *, now: datetime | None = None) -> list[CardId]   # event card.expired; an approval of kind expired; objective cards call tree.stop(..., "card_expired")
def argument_digest(actions: Sequence[Action]) -> str
def check(approval: ApprovalRecord, *, contract_revision: int | None = None, argument_sha256: str | None = None) -> None   # pure; raises NotAnApproval, StaleRevision, ArgumentMismatch
```

### 3.8 `kernel/memory.py`

```python
async def write(conn, write: EpisodeWrite) -> EpisodeId          # event episode.written first, then the Redis row; refuses foreign or missing provenance and the unassigned space
async def ingest(conn, event: Event) -> EpisodeId | None         # message.received and message.sent as OPERATOR turns, report.landed as a PROJECT report; None otherwise
async def retrieve(query: SliceQuery) -> list[MemoryHit]         # names exactly one space; structure first, then lexical rank; deterministic for identical state
async def latest_summary(space: SpaceId, regards: str) -> MemoryHit | None   # the newest summary episode for a conversation or objective
async def propose(conn, proposal: BeliefProposal) -> str        # event belief.proposed with derived_ceiling; nothing reads it at M0
def derive_ceiling(event_types: Sequence[EventType], proposed: SourceClass) -> SourceClass
```

### 3.9 `gateway/`

```python
async def issue_token(conn, *, brief_id: str, generation: int, model_ref: str, space: SpaceId, cap: Budget | None = None) -> SecretStr
    # rows in gateway_tokens and gateway_log (token_issued) on the caller's connection; brief_id may be a turn id, and then
    # `cap` is required: the turn's one model phase is checked against it (counted prompt plus max_tokens at the seat's price,
    # set by the supervisor) and recorded in gateway_log with the turn id, never in budget_ledger (version 3)
async def revoke(conn, brief_id: str) -> None                   # row in gateway_log (token_revoked) on the caller's connection; in-flight streams cut at the next chunk
async def count_tokens(model_ref: str, body: dict) -> int       # the provider's counting endpoint, for in-process callers
app: FastAPI                                                    # POST /v1/messages, Anthropic wire format, streaming forced upstream; loopback 127.0.0.1:8788
```

The gateway checks budget by calling `kernel.tree.remaining` before forwarding, converting the remaining money to a token allowance at the resolved seat's price (input and output priced separately, `max_tokens` reserved at the output price), and records consumption with `kernel.tree.consume(..., incurred=True)` after every response, cut or complete, as `Budget(usd_micros=cost)`. A turn token is checked against its `cap` and consumes nothing from the tree. An unstable prefix is stripped and recorded as `unstable`, never refused.

### 3.10 `broker/`

```python
async def perform(conn, action: Action, approval: ApprovalRecord | None, *, token: BriefToken) -> EffectOutcome
    # conn with no open transaction; intent row committed before the action; outcome row after; approval required for an `act`,
    # and for a `propose` that leaves the space when the supervisor issued a card for it (the card's argument digest binds it)
async def reconcile_dangling(conn) -> list[EffectOutcome]       # at start: for every intent without an outcome, query the target by key
async def read_recent(conn, connector: Connector, since: datetime) -> list[InboundItem]   # a `read`; broker/gmail.py as it exists; ledger rows with null brief fields
async def fetch_body(conn, item: InboundItem) -> str            # refused for the unassigned space; ledger rows with null brief fields
```

### 3.11 `kernel/api.py`

Implements §2.4 as `Door`, with `register(brief)` and `forget(brief_id)` called by `kernel/runs.py`, plus the tool log reader:

```python
async def read_tool_log(conn, brief_id: BriefId, generation: int) -> list[ToolLogRecord]
def invocations(rows: list[ToolLogRecord]) -> list[Invocation]   # pair by seq; a terminal closes open seqs
```

## 4. Event types

All in the `events` table, `schema_version` 1, grouped by the component that emits them. Every payload carries the ids named; nothing else is promised.

| Emitter | `type` | Payload |
|---|---|---|
| tree | `objective.opened` | objective_id, parent_id, conversation_id, contract (Contract), revision 1 |
| tree | `objective.contract_revised` | objective_id, revision, contract |
| tree | `objective.approved` | objective_id, revision, approval_id |
| tree | `objective.state_changed` | objective_id, from, to, reason |
| tree | `brief.issued` | brief (Brief minus gateway_token), gateway_token_sha256 |
| tree | `brief.stopped` | objective_id, brief_id, reason, new_generation |
| tree | `brief.stop_confirmed` | brief_id, objective_id, receipt (StopReceipt) |
| tree | `brief.failed` | brief_id, error |
| tree | `report.landed` | brief_id, objective_id, report (Report) |
| tree | `assumption.challenged` | objective_id, ancestor_id, statement, evidence |
| tree | `budget.overrun` | objective_id, brief_id, amount (Budget), shortfall (Budget) |
| spaces | `inbound.routed` | item (InboundItem), rule |
| spaces | `inbound.unassigned` | item, candidates |
| spaces | `space.destroyed` | space_id, counts by table |
| worker | `question.raised` | brief_id, question (Question) |
| worker | `snapshot.taken` | brief_id, objective_id, snapshot (SnapshotRef) |
| supervisor | `turn.started` | turn_id, conversation_id, trigger_event_id |
| supervisor | `turn.rendered` | turn_id, manifest (ContextManifest) |
| supervisor | `turn.completed` | turn_id, usage (Usage), actions |
| supervisor | `message.received` | conversation_id, text, session_id |
| supervisor | `message.sent` | conversation_id, text |
| supervisor | `correction.recorded` | correction (Correction) |
| supervisor | `question.answered` | brief_id, question_id, text, answered_by (`supervisor` or approval_id) |
| supervisor | `scribe.fired` | turn_id, brief_id, trigger (`implicit` or `explicit`) |
| surface | `session.opened` | session_id, uid, tty |
| surface | `conversation.opened` | conversation (Conversation) |
| surface | `space.switched` | conversation_id, from, to, new_conversation_id |
| surface | `card.issued` | card (Card) |
| surface | `card.expired` | card_id |
| surface | `approval.minted` | approval (ApprovalRecord) |
| surface | `approval.consumed` | approval_id, by (the card's regards) |
| memory | `episode.written` | episode_id, space, kind, provenance, data_class, regards, text, text_sha256 |
| memory | `belief.proposed` | proposal (BeliefProposal), proposal_id, derived_ceiling |
| verifier | `verification.sampled` | objective_id, effect_class, leaves_space (bool), selected (bool), probability |
| verifier | `checks.recorded` | objective_id, brief_id, checks (list[CheckResult]) |
| verifier | `verdict.recorded` | objective_id, brief_id, verdict (Verdict), sampled_with, model_ref |

Budget movements, gateway calls, tool invocations, and effects are rows in their own tables (§5, §6) and are not mirrored here. `objective.generation` is `1 + count(brief.stopped for the objective)`. The `EventType` Literal in `schemas/events.py` is exactly this table; a test asserts the two match.

## 5. The three execution records

Typed append-only tables under the same grant and trigger as `events` (tech stack §3). The Verifier reads `tool_log` and `effect_ledger` and never `gateway_log` (architecture §5).

### 5.1 `gateway_log` (tech stack §4; spike 03)

| Column | Type | Note |
|---|---|---|
| id | bigint identity | |
| brief_id, generation, space_id | text, int, text | brief_id may be a turn id; null on an `unknown_token` refusal |
| call | int | per brief, from 1; continues from the log's maximum after a restart |
| event | text | `GatewayEvent`: `request`, `response`, `cut`, `refused`, `upstream_error`, `token_issued`, `token_revoked` |
| model | text | the pinned id the call asked for |
| request_sha256 | text | key into `request_bodies` |
| estimated_input | int | the pre-check's estimate; `count_tokens` on a Brief's first call, then billed plus delta |
| usage | jsonb | `Usage`; on `cut`, `charged_reserved` is true |
| stop_reason | text | |
| reason | text | for `refused`: `RefusalReason`; on a `response` row `overrun` when the ledger clamped |
| cache_state | text | on a `request` row `none` or `unstable`; on a `response` row `write`, `read`, `none`, or `unstable` |
| schema_version | int | |
| at | timestamptz | |

`request_bodies(sha256 text primary key, body jsonb, first_seen timestamptz)` holds each distinct request once. `gateway_tokens(token_sha256 text primary key, brief_id, generation, model_ref, space_id, issued_at)` is insert-only; revocation is the `token_revoked` row and the gateway holds the revoked set in memory, warmed from the log at start. `context_ro` has no privilege on `gateway_tokens` or `request_bodies`.

### 5.2 `tool_log` (tech stack §4, §5; spike 08)

| Column | Type | Note |
|---|---|---|
| id | bigint identity | |
| brief_id, generation, space_id | text, int, text | |
| seq | int | per brief, assigned by the adapter, monotonic; `tool.start` and `tool.end` share it |
| event | text | `tool.start`, `tool.end`, `question`, `answer`, `terminal` |
| tool | text | `read`, `write`, `edit`, `bash`, `ask`, `write_episode`, `propose_belief`; null on `terminal` |
| input | jsonb | on `tool.start`; `write` carries `content_sha256`, never the content; `edit` carries `old_sha256`, `new_sha256`, and lengths, never the text |
| input_sha256 | text | |
| exit_status | int | on `tool.end` |
| stdout_sha256, stderr_sha256 | text | |
| artifact, artifact_sha256 | text | on `write` and `edit` ends, and on the `read` the adapter makes of each `artifact_ref` at report time; hashed on the host side of the mount |
| duration_ms | int | |
| question_id, text | text | on `question` and `answer` |
| outcome, report | text, jsonb | on `terminal` |
| schema_version | int | |
| at | timestamptz | |

Unique on `(brief_id, seq, event)`. Reader rules: pair by `(brief_id, seq)`, never by adjacency, because same-turn tool calls run concurrently; a `terminal` row closes every open `seq`, including an `ask` that was waiting; a `tool.start` is durable before the sandbox call begins; no row is accepted after a `terminal`. The one reader is `kernel/api.py::invocations` (§3.11).

### 5.3 `effect_ledger` (tech stack §7; spike 02)

| Column | Type | Note |
|---|---|---|
| id | bigint identity | |
| effect_id | text | one per intent; a refusal also gets a fresh one |
| space_id, objective_id, brief_id, generation | text, text, text, int | `objective_id`, `brief_id`, and `generation` are null on the `read` actions the kernel itself performs |
| action_type | text | `push_branch`, `connector_read`, and the reserved names |
| effect_class | text | `read`, `propose`, `act` |
| idempotency_key | text | |
| target | text | as `Action.target()` |
| payload_sha256, payload | text, jsonb | the Action |
| event | text | `intent`, `outcome`, `refused`, `reconciled` |
| outcome_kind | text | `done`, `unknown`, `recovered`, `refused`, `failed`; null on `intent`. On `reconciled`: `recovered` (the target had the key), `done` (re-run succeeded), or `unknown` |
| result, error | jsonb, text | |
| approval_id | text | null on a `read` and on a `propose` the supervisor chose not to ask about |
| schema_version | int | |
| at | timestamptz | |

Exactly one `intent` per `effect_id`, written and committed before the action runs; at most one `outcome` or `reconciled` after. Unique on `(effect_id, event)`. An open intent for a key refuses a second request with `in_flight`; a closed `done` or `recovered` for the key returns the earlier outcome without a new row.

## 6. Table ownership

The plan named creates the table in its own migration. Two plans never create the same table. `events`, `read_tokens`, and the roles exist in migration 0001.

| Table | Owner | Note |
|---|---|---|
| `events` | events | exists; the plan adds `events_type_id_idx (type, id)` and `events_payload_gin (payload jsonb_path_ops)` |
| `objectives` | tree | id, parent_id, space_id, conversation_id, depth, created_at; insert-only |
| `objective_revisions` | tree | space_id, objective_id, revision, contract jsonb, at; insert-only; unique (objective_id, revision) |
| `briefs` | tree | space_id, the Brief minus its token, plus token sha256; insert-only |
| `budget_ledger` | tree | space_id, node_id (an objective id, or the brief id of a root Scribe), brief_id (nullable), kind (`grant`, `allocate`, `consume`, `release`), usd_micros, approval_id (nullable; on a `grant` that raised the root), at; insert-only; conservation checked under `pg_advisory_xact_lock(hashtextextended(lock_key(node_id), 0))` (spike 01) |
| `read_tokens` | spaces | exists |
| `inbound_items` | spaces | the InboundItem columns plus `space_id` and `routed_by`; insert-only; unique on `(connector, account, external_id, space_id)` so a re-read is a no-op and a later assignment is a second row in the target space |
| `gateway_log`, `request_bodies`, `gateway_tokens` | gateway | §5.1 |
| `tool_log` | worker | §5.2 |
| `effect_ledger` | broker | §5.3 |
| `context_manifests` | supervisor | sha256 primary key, space_id, turn_id, manifest jsonb, rendered text; the `turn.rendered` event cites the hash |
| `conversations`, `cards`, `approvals` | surface | insert-only; `approvals.card_id` unique; `cards` carries `objective_id` and `issued`; `approvals` is written only by `kernel/approvals.py` |
| `checks`, `verdicts` | verifier | `checks`: space_id, objective_id, brief_id (the Executor brief whose snapshot was checked), the CheckResult columns, `resolutions` jsonb; `verdicts`: space_id, objective_id, brief_id (the Executor brief judged), verifier_brief_id (null for a kernel verdict), model_ref (a pinned id or `kernel`), prompt_sha256, the Verdict columns, sampled_with, at |
| Redis: `Episode` model (kinds `turn`, `report`, `summary`, `decision`) and `Belief` model | memory | popoto, keyed by space; `Belief` is defined and unread at M0 |

Every table with a `space_id` carries row-level security in the pattern of migration 0001, with `kernel_rw` allowed everything the grant gives it and `context_ro` filtered by `cori_current_space()`. `context_ro` is granted `SELECT` only on tables that carry `space_id` and the policy; a table without `space_id` (`request_bodies`, `gateway_tokens`) is never granted to it. `tests/test_space_partition.py` (spaces) enforces both by predicate over the catalog and replaces `tests/test_grants.py`, which the spaces plan retires; no other plan extends the retired file. The `verify` and `scratch` profiles never see a database.

## 7. Module ownership

| Path | Owner |
|---|---|
| `schemas/ids.py`, `schemas/events.py` | events |
| `schemas/capability.py`, `schemas/budget.py`, `schemas/objective.py`, `schemas/brief.py` | tree |
| `schemas/space.py` (exists), `schemas/inbound.py` | spaces |
| `schemas/gateway.py`; the `usd_per_mtok` price fields in `infra/models.yaml` and their loading in `infra/models.py` | gateway |
| `schemas/report.py`, `schemas/trace.py`, `schemas/records.py`, `ports/worker.py`, `ports/kernel.py` | worker |
| `schemas/sandbox.py`, `ports/sandbox.py` | sandbox |
| `schemas/effect.py` | broker |
| `schemas/context.py` | supervisor |
| `schemas/memory.py`, `schemas/belief.py` | memory |
| `schemas/approval.py`, `ports/surface.py` | surface |
| `schemas/verifier_fixture.py` (exists) | verifier |
| `kernel/events.py` | events |
| `kernel/tree.py` | tree |
| `kernel/spaces.py`, `tests/test_space_partition.py`, retiring `tests/test_grants.py` | spaces |
| `gateway/` | gateway |
| `workers/` (except `workers/verifier.py`), `adapters/pydantic_ai.py`, `kernel/api.py`, `kernel/runs.py`, `prompts/executor.md`, `prompts/scribe.md` | worker |
| `adapters/apple_container.py`, `infra/sandbox/` (including `mounts.py`, `images.py`, `setup.sh`) | sandbox |
| `broker/` | broker |
| `kernel/supervisor.py`, `kernel/context.py`, `prompts/supervisor/` | supervisor |
| `kernel/memory.py` (exists) | memory |
| `adapters/cli.py`, `kernel/approvals.py` | surface |
| `workers/verifier.py`, `kernel/verify.py`, `prompts/verifier/` | verifier |
| `kernel/__main__.py`, `tests/chaos/`, `tests/e2e/` | integration |

`workers/verifier.py` and `prompts/verifier/` are carved out of `workers/` and `prompts/` for the verifier plan; everything else under those paths is the worker plan's. Each plan that owns a table writes one migration file, `migrations/versions/NNNN_<slug>.py`, with raw `op.execute`; `NNNN` and `down_revision` follow the build order in `docs/plans/README.md`: 0002 events, 0003 tree, 0004 spaces, 0005 gateway, 0006 worker, 0007 broker, 0008 surface, 0009 verifier, 0010 supervisor. Sandbox and memory own no Postgres table.

`schemas/records.py` depends on nothing outside `schemas/`; `ports/kernel.py` imports only `schemas/`. The import rule of tech stack §2 holds: `workers/` and `adapters/` import `schemas/` and `ports/` and never `kernel/`. `prompts/` is in the trust boundary where CODEOWNERS already names it.

## Changes in version 2

Rulings on conflicts between plans:

1. One writer of the verdict transition: `tree.land_report` (§3.2). `verify.record_verdict` records only. A kernel verdict transitions from `verify_objective`. The supervisor's `verdict.recorded` turn issues the card and says.
2. Verification is kernel-driven from `runs.run_brief` through `verify.verify_objective` (§3.5, §3.6). `report.landed` is not a supervisor trigger.
3. Terminal order in `run_brief`: land, release, stop, confirm, snapshot, destroy, verify (§3.5). Stop before snapshot (spike 07).
4. Stop path: `tree.stop` fences and revokes; `runs.stop` aborts, stops the sandbox, confirms, writes the terminal row, releases (§3.2, §3.5).
5. `verify` mounts come from the snapshot through `profile_for`; `DelegateRequest.snapshot` replaces a host path (§1.5, §2.2).
6. Overrun is recorded, never refused, for incurred spend: `consume(..., incurred=True)` and `budget.overrun` (§3.2, §4). The gateway uses it.
7. `question.raised` and `snapshot.taken` are the worker's events (§4).
8. ~~The standing budget exists at M0 as a ledger node per space and day.~~ Superseded by version 3: there is no standing budget.
9. `push_branch` is built and live-tested; the M0 walkthrough ends at commit; no worker tool requests an effect at M0 (§1.10).
10. The Verifier holds `read` and `bash` and no `ask` (§1.5).
11. Advisory locks use `hashtextextended(key, 0)` everywhere (§0).
12. `tests/test_grants.py` is retired by the spaces plan's catalog conformance test (§6, §7).
13. `kernel/__main__.py` and the inbound dispatch belong to integration (§7).
14. `Contract` gains `root` and `inputs` (§1.4).
15. `context_ro` is granted only on tables with `space_id` (§6).
16. Migration numbers follow the build order (§7).

Every seam amendment a plan proposed was accepted, with three modified: tree 4 (a `snapshot` field rather than `mount_source`), supervisor H (`regards` plus `latest_summary` rather than a new `conversation_id` field), and supervisor J with verifier 4 (the caller of `verify_objective` is `run_brief`).

## Round two, 2026-09-20

Amendments raised by the plans' revisions against version 2, ruled on by the lead. All accepted; the text below is the seam.

- **§2.2 `profile_for` takes `root: str | None = None`** (tree 10, sandbox 8): when given, the root to mount for `worktree` and `scratch`; `tree.delegate` passes `contract.root`; `choose_root` by artifact kind is the fallback only when `root` is None.
- **§0 and §6, one spelling of the standing lock** (tree 11): the ledger lock is `pg_advisory_xact_lock(hashtextextended(lock_key(node_id), 0))` where `lock_key` is `objective:<id>` for an objective and `standing:<space>:<day>` for a standing node.
- **§3.2 `stop_brief(conn, brief_id, reason, *, token_issuer=None) -> None`** (tree 12): fence and revoke for one Brief, for a Scribe with no objective; `stop` on an objective calls it per live Brief.
- **§3.2 `standing_remaining(conn, *, space, day) -> Budget`** and **`raise_standing(conn, *, space, day, by: Budget, approval_id) -> None`** (supervisor K, L): the second raises the day's allowance under the node's lock and only with an `approved` record from a `budget_increase` card; a person raises the root, never the model.
- **§3.2 `delegate` with `parent_brief: TurnId`** carves the Brief's budget from the standing node of the turn's space and day, never from the turn's own allocation (tree finding 11): the turn reserves one hundredth of the allowance for its model call and the implicit Scribe's `SCRIBE_BUDGET` is a separate allocation from the same standing node.
- **§3.7 `budget_increase_card(objective, brief: Brief | TurnId, requested, remaining)`** (surface 8): the commit-time card charges the standing turn's `cards`.
- **§3.5 `run_brief`, before `tree.land_report` on a Verdict terminal**: `verify.validate_verdict_terminal(brief, terminal) -> Terminal` (verifier 7) checks the criterion strings against the contract and turns a mismatch into `abstain` with the problems prepended to `summary`.
- **Tree state table gains one edge** (supervisor finding 11): `FAILED` to `APPROVED`, reason `approved`, written by `approve` only, when the approval is the `retry` reply to a `verification_failure` card. Architecture §8 makes revival an escalation, and the card is that escalation; the node keeps its history.
- **The `unknown_outcome` card** (supervisor finding 12): `kernel/approvals.py` gains `unknown_outcome_card(outcome: EffectOutcome) -> Card` (surface), and `kernel/__main__.py` (integration) issues one per `unknown` that `broker.reconcile_dangling` returns at start; the supervisor never does. Integration task 8 reads accordingly.
- **`tests/test_grants.py` is retired by the tree plan**, the first plan in the build order that adds a table (tree finding 13); the spaces plan's `tests/test_space_partition.py` is the replacement and lands one step later. §6 and §7 read accordingly.
- **§3.9, turn tokens** (gateway critique, 2026-09-20): a token issued for a turn id names no objective and has no generation, so the gateway skips `check_generation` for it; revocation alone ends a turn's calls, and `tree.remaining` and `consume` resolve the turn id to its standing allocation.
- **Card expiry and attention** (surface critique, 2026-09-20): `approvals.expire_due` writes the `expired` record and `card.expired` and transitions nothing; `tree.expire_due` cancels a node past its deadline with reason `deadline`, and a card's expiry coincides with it because the card carries the deadline. `issue_card` charges one `cards` to the objective node (`tree.consume` accepts an objective id as a node, own-consumption row with null brief) and, for a card issued before an objective exists, to the standing node of the space and day; never to a brief. `Reply` gains `text: str | None = None`, the free text after the option, which `answered` replies carry. A `grant` reply on a `budget_increase` card mints an `approved` record bound to the contract revision; the supervisor's turn calls `tree.raise_standing` with it and then `tree.approve`, and the commit proceeds. `supervisor.serve` calls `tree.expire_due` and `approvals.expire_due` once a minute.
- **§1.10 `Action.objective_id` and `brief_id` are `| None`** (broker critique, 2026-09-20): null only on the class 0 reads the kernel itself performs (the ingestion poll, the body fetch for a render); `kernel/api.py` refuses a worker action with either missing. Kernel reads bypass the closed-key short circuit and carry the poll time in their idempotency key, so a poll never freezes.
- **The turn is two transactions** (supervisor critique, 2026-09-20): the first appends `turn.started` and commits `allocate_turn`, so the gateway's own connection sees the turn's allocation; the second renders, calls the model, acts, appends `turn.completed`, and releases the unspent reservation. A trigger is handled iff a `turn.completed` names it; a `turn.started` with no `turn.completed` after a restart is rerun under the same turn id, reusing its allocation, so the render is byte-identical and the spike 02 property holds. `gateway.issue_token` for a turn is called inside the first transaction and the token lives in the turn's memory only.
- **`run_brief` is called with the Brief the caller holds** (supervisor critique): `tree.delegate` returns the Brief with its token to the caller, which calls `kernel.runs.run_brief(brief)` directly after its transaction commits; `brief.issued` is the record, never the trigger, since it carries no token. After a kernel restart a `brief.issued` with no terminal is stopped through `tree.stop_brief` and `runs.stop`, not resumed (recovery of live runs is M1).
- **`tree.transition(...) -> list[BriefId]`** (supervisor critique): returns the Briefs it fenced and revoked when the target state stops the node, and the caller hands them to `kernel.runs.stop` after its transaction commits. `tree.approve` writes the `APPROVED` transition itself; callers add none.
- **Verifier rulings** (verifier critique, 2026-09-20): a kernel verdict (`model_ref = "kernel"`, a failed deterministic check) carries `outcome = "fail"` and every criterion `met = False` with a reason naming the failed check, so it passes the §1.6 validator; the artifact failed the kernel's own bar and its criteria are unmet. The tree's `VERIFYING` to `FAILED` edge admits reasons `verification_failed`, `worker_failed`, and `budget_exhausted`, written by `land_report` or by `verify.verify_objective`. The Verifier's budget is sized from `tree.remaining(objective_id)`, the node's remaining after the Executor's release, never from the Executor's brief. `verify.validate_verdict_terminal(brief, terminal)` is the verifier plan's to build and `run_brief`'s to call.
- **Payloads carry `objective_id` at the top level** (events critique, 2026-09-20): `brief.issued`, `brief.failed`, `brief.stopped`, `brief.stop_confirmed`, `question.raised`, `question.answered`, and `snapshot.taken` each carry `objective_id` beside their brief id (null for an objective-less Scribe), so `read_for(key="objective_id")` reaches every event of a node.
- **Surface amendments 9 to 14 accepted** (2026-09-20): `standing_budget_card(space, day, requested, remaining)` for the commit-time budget card; `Reply.text` rides in the `approval.minted` payload; `record_session(conn, session_id)` appends `session.opened`; `broker.reconcile_dangling` fills `result` with the target's answer on an `unknown`; `verification_failure_card` takes the Executor's `BriefId`; `issue_card(conn, card, *, now=None)`.
- **Tree amendments 13 and 14 accepted** (2026-09-20): `budget_ledger.kind` gains `grant` (a person's raise of a standing node's allowance, carrying the approval id), and a standing node's allowance is written once at first use and only ever raised by `grant` rows, so `standing_remaining` and `raise_standing` read the ledger and nothing else. The `retry` reply on a `verification_failure` card goes through `approve` (which writes the `FAILED` to `APPROVED` edge), never a bare `transition`.
- **Supervisor amendments M to P accepted** (2026-09-20): a card's `regards` may be a standing node id; a turn's reservation is the counted prompt plus `max_tokens`, so there is one number and no "hundredth"; `ApprovalRecord.text: str | None` carries the reply's free text; a turn token is minted with `generation = 0`, which the gateway reads as "no fence". Two rulings: `tree.land_report` on a Verifier `failed` terminal transitions `VERIFYING` to `FAILED/worker_failed`, and the supervisor's turn on `brief.failed` issues the `verification_failure` card. A card whose node has spent its attention is still issued (architecture §4: exhaustion never lowers an approval requirement); it charges the standing node, and when that too is spent it is issued anyway with `consume(..., incurred=True)` recording the overrun.
- **Verifier amendments accepted** (2026-09-20): `objective.state_changed` to `FAILED` with reason `worker_failed`, `budget_exhausted`, or `verification_failed` is a supervisor trigger that issues the `verification_failure` card, so the edges `verify_objective` writes reach the person; `run_brief` returns the landed `Terminal`; `verify.recover_verdicts()` runs at start and closes any objective left in `VERIFYING` with no Verifier terminal by `stop_brief` on its live Briefs and `FAILED/worker_failed`, so the person can retry.

## Version 3, 2026-09-20

The architect's budget and effect class ruling (`docs/reviews/2026-09-20-budget-ruling.md`), applied by the lead. Where a version 2 or Round two entry above says otherwise, this section wins.

1. **`Budget` is `usd_micros` alone** (§1.3). Tokens, wall clock, tool calls, descendants, and cards are gone as axes. Nothing consumes budget except the gateway, in money, at the seat's price (§3.9). `record_tool` consumes nothing (§2.4); `issue_card` charges nothing (§3.7); `delegate` consumes no `descendants`; `run_brief` records no wall clock; `deadline` is the time ceiling.
2. **No standing budget.** `StandingBudget`, `Space.standing_budget`, the `standing:<space>:<day>` node and lock, `allocate_turn`, `standing_remaining`, `raise_standing`, `standing_budget_card`, and the refusal in `check_may_open` for an unset budget are gone (§0, §1.1, §3.2, §3.4, §3.7). The supervisor's turns are recorded, never budgeted: a turn token carries a `cap` the supervisor sets and the gateway checks it against the cap (§3.9). The turn is still two transactions, for the reason the supervisor critique gave (a `turn.started` with no `turn.completed` is rerun under the same id), but the first transaction writes no ledger row.
3. **The root is the objective and only a person raises it.** `Contract.budget` is the supervisor's estimate with a one-sentence `basis` (§1.4). `tree.raise_budget(objective_id, by, approval_id)` writes one `grant` row on an `approved` record from a `budget_increase` card (§3.2). The card is issued on overrun only, on the `FAILED/budget_exhausted` trigger, and its grant reply goes `raise_budget`, then `approve` (the `FAILED` to `APPROVED` edge), then the Executor delegation (§1.12).
4. **No self-approval ceiling.** At `read` and `propose` the supervisor self-approves and tells the person the figure, or issues the `commit` card when it judges the person would want to be asked; an `act` always waits on the `commit` card (§1.12). `approve` refuses `self_approved` on an `act` contract (§3.2).
5. **The implicit Scribe** is carved from the turn's objective when there is one, and is its own root under `brief:<brief_id>` when there is none: one `grant` row under `node_id = brief_id` for the kernel-set amount, sized from the fork at the summarizer seat's price (§3.2). `parent_brief: TurnId` names who asked and changes no allocation.
6. **Effect classes are `read`, `propose`, `act`** (§1.1 `EffectClass`, `EFFECT_RANK`). The capability grammar reads `name@class` with the name. `PushBranch` is `propose`; `ConnectorRead` is `read`; the reserved actions are `propose` at M2 and `act` at M3 (§1.10). `effect_ledger.effect_class` is text (§5.3). The Verifier's capabilities are `read@read` and `bash@read`; the Executor's are `read@read`, `write@propose`, `bash@propose`, `ask@read`; the Scribe's `read@read`, `memory.episodic.write@propose`, `memory.operator.propose@propose`; the objective-less Scribe's ceiling is `propose`.
7. **Verification by route** (§3.6): `should_verify(effect_class, leaves_space=...)` is 1.0 for anything that leaves the space and for `act`, and a sampling rate for sandbox-only work; M0 is 1.0 everywhere. `verification.sampled` carries `leaves_space` (§4). `broker.perform` requires an approval for an `act`, and for a `propose` that leaves the space when the supervisor issued a card for it (§3.10).
8. **`budget_ledger`** columns are `usd_micros` and `approval_id` in place of the six axis columns; `node_id` is an objective id or a root Scribe's brief id; `kind` is `grant`, `allocate`, `consume`, `release` (§6).
9. **Card fields**: `commit` carries `budget` and `basis`; `budget_increase` carries `brief_id`, `spent`, `produced`, `requested` (§1.12).


## Version 4, build

Rulings the lead made during the M0 build, from the build log (`docs/reviews/2026-09-21-build-log.md`). Where an entry above says otherwise, this section wins.

1. **Recovery at start stops the space's containers before anything else** (Round two, "a `brief.issued` with no terminal is stopped through `tree.stop_brief` and `runs.stop`"; supervisor review, finding 124). A kernel process serves one space and holds no run at start, so every running sandbox of that space is an orphan. The supervisor's recovery, before any open turn reruns and before any delegation: (a) lists `sandbox.running()` through the Sandbox bound in `kernel.runs`, keeps the handles whose profile names the space, and stops each through `sandbox.stop`, which is probe-confirmed; (b) fences every Brief with `brief.issued` and no terminal, and confirms each stop with a `StopReceipt` taken from (a): the container's own receipt when one was stopped, otherwise a receipt whose probe records that `running()` listed no container of the space after the sweep; (c) writes the kernel-side `aborted` terminal and releases, as `runs.stop` does; (d) calls `verify.recover_verdicts()` (entry 2); (e) reruns open turns; (f) re-delegates an Executor once, as the supervisor plan says. `runs.stop` keeps skipping Briefs this process is not running. Raised by: supervisor. Touches: supervisor, integration; worker and sandbox unchanged.
2. **`verify.recover_verdicts()` is built by the supervisor build in `kernel/verify.py`** (Round two, verifier amendments; finding 108). The verifier plan had no task for it. Behaviour as Round two writes it: every objective in `VERIFYING` with no Verifier terminal transitions `FAILED/worker_failed` after its live Briefs are stopped through entry 1, so the `verification_failure` card reaches the person. Raised by: verifier, supervisor. Touches: supervisor, integration, verifier (merged; an addition, no change to what it built).
3. **The implicit Scribe is its own root when the turn's objective cannot carry its data class** (version 3 item 5; supervisor finding 125). `tree.delegate` refuses a Brief whose `max_data_class` exceeds the contract's, and the Scribe's fork holds OPERATOR text, so under a PROJECT contract the Scribe takes a `grant` row under its own brief id. Every M0 contract is PROJECT, so at M0 the Scribe is always its own root and an objective's spend excludes its Scribes. Raised by: supervisor. Touches: supervisor, integration.
4. **Event payloads, supersets of §4** (findings 105, 126): `verdict.recorded` carries `verifier_brief_id` and `prompt_sha256`; `scribe.fired` carries `objective_id` and `carved_from`; `turn.completed` carries `trigger_event_id`, which the poll's projection check reads. Raised by: verifier, supervisor. Touches: verifier, supervisor, integration.
5. **An action refused inside the turn's second transaction completes the turn** (supervisor plan, "The model call"; finding 127). Trigger admissibility (the objective or question an action names must be the trigger's) is checked in validation, where the one retry covers it; an `ActionRefused` that still reaches the second transaction completes the turn with `actions: []` and a kernel line, so no trigger can leave an open turn that reruns forever. Raised by: supervisor review. Touches: supervisor.
6. **§3.4 `record_inbound(conn, item, *, candidates: Sequence[SpaceId] = ())`** (finding 39): the keyword carries the `candidates` that §4 requires on `inbound.unassigned`, which the item alone cannot supply. Raised by: spaces. Touches: spaces (built as written), integration.
7. **§0, space ids** (finding 40): a `SpaceId` matches `^[a-z0-9][a-z0-9-]*$` (`schemas/space.py::SPACE_ID_PATTERN`), as the spaces plan's critique decided. Raised by: spaces. Touches: spaces (built as written).
8. **§5.1, the restart `cut` row** (finding 61): `reason` may also be `reconciled`, written by the gateway on the `cut` row that closes a reservation left open by a restart. Raised by: gateway. Touches: gateway (built as written), integration.
9. **§3.5, the landed closing order** (finding 69): a landed run calls no `tree.confirm_stop`, because the tree confirms only a stop it fenced; the sandbox's probe-confirmed receipt for a landed run is not recorded at M0. A durable receipt for a voluntary stop is M1. Raised by: worker. Touches: worker (built; its call and `NotLive` catch are harmless and stay until M1).
10. **§1.5 and §2, the adapter's terminal row** (finding 71): at M0 the adapter's terminal tool-log write swallows any exception, since `StaleGeneration` lives in `kernel/`. Moving a stale-fence exception into `schemas/` so the adapter can catch narrowly is M1. Raised by: worker. Touches: worker.
11. **§5.3, idempotency keys** (finding 80): keys are global at M0, which is safe while one space holds the mail credential; scoping `closed_for_key` and `open_intent_for_key` by space comes with a second credentialed space (M1). Raised by: broker. Touches: broker.
12. **§3.7 and Round two, the `unknown` outcome** (finding 91): an `unknown` `EffectOutcome.result` carries `space_id`, `action_type`, `idempotency_key`, and `objective_id` beside the target's answer (`target_state`), so `approvals.unknown_outcome_card` can build its card. The broker, merged, fills only `target_state`; the integration build, which issues these cards at start, adds the four keys in `broker.reconcile_dangling` and nowhere else. Raised by: surface, broker. Touches: broker (merged; the integration build makes the addition), surface, integration.
13. **§3.7, `record_session(conn, space)`** (finding 93): the space form stands; the acceptance line in Round two that writes `session_id` is superseded. Raised by: surface. Touches: surface (built as written), integration.
14. **§3.7's code block** (finding 94) reads with `record_session(conn, space)` and `unknown_outcome_card(outcome) -> Card` added, `verification_failure_card` taking `Brief | BriefId`, and `expire_due` writing `expired` and `card.expired` and stopping nothing, as Round two says. Raised by: surface. Touches: surface, supervisor, integration.
15. **§1.12, the `commit` card's `stop_conditions`** (finding 96): rendered by `commit_card` from the contract's own limits as canonical JSON; `Contract` gains no field. Raised by: surface. Touches: surface (built as written).
16. **§3.6, `record_verdict(..., objective_id: ObjectiveId | None = None)` and `verify_objective(objective_id, snapshot: SnapshotRef | None)`** (findings 103, 104): the first names the node when `brief` is None; with a None snapshot `verify_objective` transitions `FAILED/worker_failed`. Raised by: verifier. Touches: verifier (built as written), worker, supervisor.
17. **§1.5, `Brief.instruction`** (finding 106): set for the Scribe's explicit trigger and for the Verifier (`workers.verifier.INSTRUCTION`); None for the Executor and Planner, whose default the adapter supplies. Raised by: verifier. Touches: verifier, worker (built as written).
18. **§1.9, the data class carrier** (finding 15): `SandboxProfile.env` keys prefixed `CORI_DATA_CLASS_` carry the class to the container literally; a manifest secret may not use that prefix. A field of its own is M1. Raised by: sandbox. Touches: sandbox, worker (built as written).
19. **§2, `Sandbox.running() -> list[SandboxHandle]`** (finding 139): the port names the method recovery calls (entry 1); the Apple adapter and the fakes already have it. The port line is added in the closing pass with the design edits. Raised by: supervisor. Touches: sandbox, supervisor.
