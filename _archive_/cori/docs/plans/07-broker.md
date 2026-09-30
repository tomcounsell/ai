# 07. Broker

| | |
|---|---|
| Slug | `broker` |
| Milestone | M0 |
| Status | built |
| Seams version | 3 (2026-09-20) |
| Owns | `broker/`, `schemas/effect.py`, the `effect_ledger` table (seams §6, §7) |
| Depends on | tree (`kernel/tree.check_generation`, `schemas/brief.BriefToken`), spaces (`Space`, `InboundItem`, the `inbound_items` table) |
| Written | 2026-09-19, against `1123f53`; revised against seams v2 at `4cdd45c`; critique addressed 2026-09-20 at `69048fb`; cascaded 2026-09-20 to seams v3 (`docs/reviews/2026-09-20-budget-ruling.md`) |
| Built | 2026-09-22, merge 25d4245 |

**Cascade, 2026-09-20 (lead).** Seams v3 named the effect classes `read`, `propose`, `act`, collapsing the old 1 and 2 into `propose`. In this plan: `push_branch` is a `propose` action, `ConnectorRead` a `read`, the ledger's `effect_class` column is text, ceilings compare by `EFFECT_RANK`, and `perform` requires an approval for an `act` and for a proposal that leaves the space when the supervisor issued a card for it; at M0 every `act` and every unshipped action model is refused as before. Budget touches nothing here. The Findings and Critique sections below are the record of earlier rounds.

## Purpose

M0 proves one objective end to end at `propose` inside one client space's sandbox, in code and in a drafted message, and that a stop leaves durable state intact (tech stack §10). The broker is the third execution record and the only door to the world: every effect is an intent row, an action performed outside the sandbox with a credential the sandbox never holds, and an outcome row, keyed so a kill between the two is recoverable rather than merely visible. `push_branch` is the one `propose` effect built at M0; it is live-tested, and the code objective ends at commit (tech stack §10; seams §1.10). The message objective's client mail arrives through the Gmail connector, a `read` the broker performs with the person's token. The Verifier reads the ledger this plan writes (architecture §5). Without it, "durable" has no third column and effect class is a comment.

## What the documents say

- Architecture, five decisions, decision 4, KEEP: authority lives in the kernel; the system overview names the action broker as the component "through which every effect leaves the system", because the kernel can only enforce what it mediates.
- Architecture §1, REVISED: durable means "events in the store, artifacts on a retained sandbox disk, and effects the broker confirmed"; a lossless log gives exactly-once effects only when every effect carries an idempotency key the broker can query the target with.
- Architecture §4, REVISED 2026-09-20: the three effect classes `read`, `propose`, `act`, named and ordered; "the kernel refuses any action whose class exceeds the node's approved ceiling"; whether an effect leaves the space is a fact about its route, and everything that leaves goes through the broker.
- Architecture §5, KEEP: three records; "the effect ledger holds what the broker did to the world"; the Verifier reads the tool log and the effect ledger, never the gateway log.
- Architecture §7, KEEP: `unknown outcome` is an escalation type with its own card.
- Architecture §8: "Irreversible action without consent: effect class enforced in the kernel and again in the action broker"; "Kill lands between an effect's intent and its outcome: broker reconciles by idempotency key against the target before re-running; `unknown outcome` card when the target cannot be queried."
- Architecture §9, PATCHED: connector credentials are held by the broker, keyed by space; "a read through a connector is a `read` action performed outside the sandbox"; items no rule claims land in `unassigned`, header-only; "every action payload carries its space, and the broker refuses a target absent from the space's `allowed_targets`"; the credentials are the person's own tokens, deliberately few; every effect carries its space so spend is attributable.
- Tech stack §2, LOCKED (boundaries), COMMITTED (one process): the broker holds "credentials for external effects" and never model keys; the loop, gateway, and broker run as one Python process with module boundaries.
- Tech stack §7, LOCKED (design), PROVISIONAL (details): sandboxes hold no credential capable of any effect outside the space; `push_branch` is a typed `propose` action the broker performs outside the sandbox "restricted to the objective's branch name"; every effect that leaves the space is a typed action with a fixed class, requested through the kernel; the kernel checks the approval record for an `act` or a proposal the supervisor chose to ask about; credentials are keyed by space and target and the broker also holds connector credentials for `read` actions; every action carries an idempotency key the target can be queried by; the protocol is intent event, action, outcome event; at-most-once is the fallback for targets that cannot be queried, with `unknown outcome` as the card; "one module per action, every request and result an audit event"; `open_pr` ships first among the proposals that leave the space.
- Tech stack §10, PROVISIONAL, COMMITTED on scope: M0's first objective is a code change and the second a drafted reply to a client mail, both at `propose` inside the sandbox, exercising "the tree, budgets, sandbox, connector, and verification"; `open_pr` and `post_message_draft` arrive at M2 as the first proposals that leave the space; `act` and the passkey at M3.
- Tech stack §3, LOCKED: append-only by grant and trigger; every space-partitioned table under row-level security keyed by the minted read token.
- Spike 02, holds with caveat: reconcile-by-key reached 0 duplicated and 0 lost effects in 49 of 49 killed runs; at-least-once duplicated in 22%, at-most-once lost one in five and recorded `unknown` in two of five.
- Plans index, row 07: "effect protocol of intent, action, outcome; idempotency keys; `push_branch` as it exists; allowed targets by space; connector reads outside the sandbox; the Gmail connector as it exists (tech stack §7; spike 02)."
- Blind-spot review findings 9, 11, 14, 15: one credential pool was a cross-client channel; credentialed reads had no home; payloads bind target state; a push credential in the sandbox is an effect on the world held where the kernel cannot see it.

## What exists

- `broker/push_branch.py` (prereqs item 13): `check_branch` refuses `main`, `master`, malformed names, and anything outside `cori/` before any subprocess; `push_branch` pushes `HEAD:refs/heads/<branch>` with the token only on the git command line; `delete_branch` for tests. Lifted onto the `PushBranch` action of seams §1.10, with the branch rule tightened (Design). `tests/test_push_branch.py` (9 passed, one live against `yudame/cori-sandbox`) is updated with it.
- `broker/gmail.py` (prereqs item 12): loopback OAuth `authorize`, `access_token` from the refresh token, `query_for(sender_domain)`, `recent(sender_domain, n)`. Kept; `read_recent` and `fetch_body` are added beside them. `tests/test_gmail.py` is extended.
- `broker/README.md`: the Keychain names and setup. Kept and extended with the credential table.
- `migrations/versions/0001_roles_and_events.py`: the grant, trigger (`reject_mutation()`), RLS policy pattern, and `cori_current_space()`, which the broker's migration reuses.
- `schemas/space.py`: `Space`, `Connector`, `RoutingRule`, `Strict`, `EffectClass`, `load_space`. Consumed as is.
- `infra/secrets.py::read_secret`: the Keychain loader every credential goes through.
- `spikes/02-kill-is-lossless/loop.py::resolve_dangling`: the three-way branch (recovered, re-run, give up) is lifted into `reconcile_dangling`. The chaos harness stays in `spikes/`; the M0 chaos test belongs to `99-integration`.
- `spikes/05-capability-attenuation/test_caps.py`: the property style the tests below copy. Its code is lifted by the tree plan, not here.

## Seams

**Consumed**

- §0 conventions: UUIDv7 ids as `str`, `EffectId` and `new_id()` from `schemas/ids.py`, `*_sha256` hex, `schema_version` 1, `UNASSIGNED_SPACE_ID`, and the advisory lock form `pg_advisory_xact_lock(hashtextextended(key, 0))` with the `effect:<idempotency_key>` namespace.
- §1.1 `Space`, `Connector`, `RoutingRule`, `EffectClass`, `load_space`.
- §1.2 capability names `push_branch` and `connector.read`, checked by `kernel/api.py::request_effect` before the broker is reached; the broker checks the space, never the capability.
- §1.5 `BriefToken`.
- §1.11 `InboundItem`.
- §1.12 `ApprovalRecord` (`argument_sha256`, `kind`); the `unknown_outcome` card kind and its fields (effect id, action type, idempotency key), issued by whoever calls `reconcile_dangling`.
- §2.4 `KernelAPI.request_effect(token, action) -> EffectOutcome`, the worker plan's implementation, which calls `perform`.
- §3.2 `kernel.tree.check_generation(conn, token)` raising `StaleGeneration`.
- §3.4 `kernel.spaces.record_inbound(conn, item)`, which the ingestion caller feeds with `read_recent`'s items.
- §6 `reject_mutation()` and `cori_current_space()` from migration 0001.

**Provided**

- §1.10 `schemas/effect.py`: `Action`, `PushBranch`, `ConnectorRead`, `EffectOutcomeKind`, `EffectOutcome`, exactly as listed, plus methods and class variables.
- §3.10 `broker.perform`, `broker.reconcile_dangling`, `broker.read_recent`, `broker.fetch_body`, as v2 lists them (amendments A and B, accepted).
- §5.3 the `effect_ledger` table and its migration.
- §7 everything under `broker/`.

## Design

### Modules

| Module | Holds |
|---|---|
| `schemas/effect.py` | the action models of seams §1.10. Each subclass fixes `effect_class: ClassVar[EffectClass]` and `action_type: ClassVar[str]` (`push_branch`, `connector_read`). A `model_validator` recomputes `idempotency_key` from the fields and refuses a supplied key that differs, so a requester never chooses its own key. `payload_sha256()` is sha256 over `json.dumps(model_dump(mode="json"), sort_keys=True, separators=(",", ":"))`. `objective_id` and `brief_id` are `| None`, None only on a `read` the kernel itself performs (amendment G); `perform` refuses a worker action with either missing. |
| `broker/ledger.py` | the only writer of `effect_ledger`: `intent`, `outcome`, `refused`, `reconciled`, `dangling`, `closed_for_key`. Every writer takes a connection and the row's fields (`action_type`, `effect_class`, `space`, `target`, `idempotency_key`, `payload: dict`, and the three brief fields, each `| None`) rather than an `Action`, and inserts one row; `perform` unpacks its `Action` into them, and the kernel reads pass the `ConnectorRead` dict with the two ids `None`. |
| `broker/__main__.py` | `python -m broker reconcile`: opens a `kernel_rw` connection, runs `reconcile_dangling`, prints one line per close. |
| `broker/credentials.py` | `credential_for(space_id, target) -> Credential`, a table keyed by the pair, each value naming Keychain entries; `EffectRefused` on a missing pair. |
| `broker/manifest.py` | `space_for(space_id) -> Space`, reading `infra/spaces/<id>.yaml` through `schemas.space.load_space`. |
| `broker/push_branch.py` | the `propose` action module: `check(action)`, `run(action, credential) -> dict`, `query(action, credential) -> TargetState`. |
| `broker/gmail.py` | the connector module: the existing OAuth code, `compose_query`, `read_recent`, `fetch_body`, and `run`/`query` for a `ConnectorRead`. `query` returns `absent` always: a read leaves nothing on the target to find, so a dangling read is re-run. |
| `broker/actions.py` | `MODULES: dict[str, ActionModule]`, `action_type` to module. Tests register a fake target here. |
| `broker/__init__.py` | `perform` and `reconcile_dangling`, the two doors. `space_for` and `check_generation` are module-level names bound at import, so the property tests monkeypatch them the way the tree plan does. |

Decided here: the broker imports `schemas/`, `infra/`, and exactly one kernel function, `kernel.tree.check_generation`. Tech stack §4 names the broker as a second refuser of a stale generation, and the tree is the one source of the current generation. The space manifest is read from disk by the broker itself rather than through `kernel/spaces.py`, because a second lock that reads the first lock's data structure is one lock.

Decided here: `perform` runs on a connection with no open transaction (`conn.info.transaction_status == IDLE`) and opens two of its own, one around the intent row and one around the closing row, because spike 02's protocol needs the intent committed before the action starts, and a caller's enclosing transaction would turn that commit into a savepoint. A connection inside a transaction is refused before any row is written.

### The `effect_ledger` table

Columns as seams §5.3: `id bigint identity`, `effect_id text`, `space_id text not null`, `objective_id text`, `brief_id text`, `generation int`, `action_type text not null`, `effect_class text not null check (effect_class in ('read', 'propose', 'act'))`, `idempotency_key text not null`, `target text not null`, `payload_sha256 text not null`, `payload jsonb not null`, `event text not null`, `outcome_kind text`, `result jsonb`, `error text`, `approval_id text`, `schema_version int not null default 1`, `at timestamptz not null default now()`. Unique on `(effect_id, event)`; index on `(idempotency_key, id)` and `(objective_id, id)`. Owner `migrator`; `kernel_rw` gets SELECT and INSERT; `context_ro` gets SELECT through the policy `space_id = cori_current_space()`; the `reject_mutation()` triggers on UPDATE, DELETE, and TRUNCATE.

`objective_id`, `brief_id`, and `generation` are nullable, null only on the `read` actions the kernel itself performs (the ingestion poll and the body fetch for a render); every worker-requested effect carries all three (seams v2 §5.3, amendment B accepted). Tech stack §7 wants every request and result as an audit event, and the poll has no brief. For a kernel read, `payload` is the `ConnectorRead` dict with `objective_id` and `brief_id` `None` (amendment G) and `payload_sha256` is over that dict; `result` is `{"items": [<InboundItem dict>, ...]}` for `read_recent` and `{"headers": {...}, "body": str}` for `fetch_body`.

Decided here: a row per event and no status column, so the row is never updated and the append-only grant holds. The state of an effect is a fold over its rows: `intent` then at most one of `outcome` or `reconciled`; or exactly one `refused`.

### `perform`

```python
async def perform(conn, action: Action, approval: ApprovalRecord | None, *, token: BriefToken) -> EffectOutcome
```

Checks, in order. Each failure writes one `refused` row with the reason in `error` and returns `EffectOutcome(kind="refused")`; nothing after a refusal touches the target.

1. `action.objective_id is None or action.brief_id is None` refuses with `worker_action_without_brief`; the `None` shape belongs to the kernel's own reads, which never pass through this door. `tree.check_generation(conn, token)`; `StaleGeneration` refuses with `stale_generation`. `token.brief_id != action.brief_id` refuses with `brief_mismatch`.
2. `action.effect_class == "act"` refuses with `class_not_shipped`, and so does any `action_type` outside `MODULES`. Tech stack §10 puts the first proposals that leave the space (`open_pr`, `post_message_draft`, `calendar_hold`) at M2 and every `act` at M3; only `push_branch` and `connector_read` exist in `schemas/effect.py`. When an `act` arrives, this line becomes: an `approval` of kind `approved` or `approved_with_edit` whose `argument_sha256 == action.payload_sha256()` is required, and its id goes on the rows. When a proposal that leaves the space arrives, the rule is architecture §4's: it is performed on the grant, and when the supervisor issued a card for it the `approval` is required and bound the same way; how the door learns that a card was issued is the M2 planner's (`worker-effects`).
3. `space = space_for(action.space)`; a missing manifest refuses. `EFFECT_RANK[action.effect_class] > EFFECT_RANK[space.max_effect_class]` refuses with `above_space_ceiling`. `action.target() not in space.allowed_targets` refuses with `target_not_allowed`.
4. `credential_for(space.id, action.target())`; missing refuses with `no_credential`.
5. `MODULES[action.action_type].check(action)`; `EffectRefused` from the module refuses with its message.
6. In one transaction under `pg_advisory_xact_lock(hashtextextended("effect:" + idempotency_key, 0))` (seams v2 §0): `tree.check_generation(conn, token)` again, so the fence and the intent read one committed history and a `tree.stop` that commits after step 1 still refuses with `stale_generation` before any intent exists (tech stack §4). Then `closed_for_key(key)` returns the earlier `EffectOutcome` when a `done` or `recovered` close exists for the key, and `perform` returns it without a new row (a repeated request for the same effect is the same effect). An open intent for the key refuses with `in_flight` (seams v2 §5.3). Otherwise `intent(...)` with a fresh `EffectId`; commit. Step 1's check is the cheap early refusal; step 6's is the one that binds.
7. `result = await module.run(action, credential)`. On success, a second transaction writes `outcome` with `outcome_kind="done"` and `result`; commit; return `EffectOutcome(kind="done", result=result)`.
8. On an exception from `run`, the broker asks the target at once: `query(action, credential)`. `present` writes `reconciled/recovered`; `absent` writes `outcome/failed` with the error text (seams v2 §1.10: the action raised and the target confirms the key is absent; no card, only a report); `unreachable` or `differs` writes `outcome/unknown` with the error. The return carries the kind and error. A kill between steps 6 and 7 leaves the intent dangling for `reconcile_dangling`.

The advisory lock keyed on the idempotency key and the `in_flight` refusal give the process-local half of exactly-once; spike 02 measured the cross-restart half and this plan lifts it. The lock is transaction-scoped so a kill releases it with the connection. Decided here: the lock is taken inside the intent transaction only, never across the action, because a lock held across a network call is a lock held across a kill.

Decided here: the refusal row carries the same `effect_id` shape as an intent (a fresh id) so the Verifier's slice and the person's audit read one table with one shape.

### `reconcile_dangling`

```python
async def reconcile_dangling(conn) -> list[EffectOutcome]
```

Runs once at process start, before any brief runs (tech stack §4: the broker reconciles any dangling intent before a replacement Brief runs). For every `intent` without a closing row, ordered by id: rebuild the action from `payload` (`MODULES[action_type].model.model_validate(payload)`, which admits the kernel read's `None` ids under amendment G), resolve the credential, call `module.query(action, credential)`:

| Target says | Row written | Outcome kind |
|---|---|---|
| `present` (the key is there, in the state the payload named) | `reconciled` | `recovered` |
| `absent` | run the action, then `reconciled` | `done`, or `unknown` with the error when the re-run fails |
| `differs` (the key is there in another state) | `reconciled` | `unknown` |
| `unreachable` | `reconciled` | `unknown` |

A dangling `read` takes the `absent` row, because `gmail.query` answers `absent` by rule: the re-run fetches again and closes `reconciled/done` with a fresh `result`, so no read is ever "recovered" without its data. When the re-run raises, the close is `outcome/failed` with the error and no card, since seams §1.10 defines `failed` as the action raising while the target confirms the key absent, which a read's query does by construction; `unknown` is reserved for effects whose state on the target is in doubt. `kernel/__main__.py` (integration) issues one card per `unknown` returned through `approvals.unknown_outcome_card` (seams Round two); the broker never issues a card. Decided here: `differs` is `unknown` rather than a re-run, because for `push_branch` a re-run onto a branch at another SHA would be the force-push blind-spot finding 15 exists to prevent, and the person is the right reader of that state.

Decided here: reconcile is single-process. Two kernel processes on one ledger is a stated non-case at M0 (tech stack §2, one process), and spike 02 left the two-loop case for M1.

### `push_branch`

The action module lifted from prereqs item 13. `PushBranch(Action)` carries `repo`, `branch`, `source_dir`, `head_sha`; `idempotency_key = f"{repo}#{branch}@{head_sha}"`; `target() = "github.com/" + owner`.

- `check(action)`: the existing malformed and `main`/`master` refusals stay. The branch must equal `cori/<objective_id>` (seams v2 §1.10, amendment D accepted). Tech stack §7 restricts the push to "the objective's branch name" and the prereq code accepted any `cori/*`; with one Executor per leaf and the worktree keyed by objective id, the objective's branch is a function of its id and needs no second field. `source_dir` must exist, be a git work tree, and have `HEAD == head_sha`; a moved HEAD refuses with `head_moved`, which is the source-side form of tech stack §7's state binding.
- `run(action, credential)`: `git push https://x-access-token:<token>@github.com/<repo>.git <head_sha>:refs/heads/<branch>` from `source_dir`, without `--force`; the token appears only on the command line and is scrubbed from any error. Returns `{"sha": head_sha}`.
- `query(action, credential)`: `git ls-remote <url> refs/heads/<branch>`; the SHA equal to `head_sha` is `present`, no line is `absent`, another SHA is `differs`, a non-zero exit is `unreachable`.

The push runs from the host side of the worktree mount, so no credential enters the sandbox. `source_dir` is the profile's `mount_source`, filled by the tool bridge that arrives with `worker-effects` at M2 and never by the model (seams v2 §1.10; Questions, 3). At M0 the only callers are the live test and `reconcile_dangling`. `delete_branch` stays for tests.

### The Gmail connector

The connector module lifted from prereqs item 12. Two entry points and one action.

- `read_recent(conn, connector: Connector, since: datetime) -> list[InboundItem]`, the kernel's ingestion read. `kernel/spaces.ingest` routes and records what it returns, and the dispatch and its timer belong to `kernel/__main__.py` (integration; seams §7, ruling 13). `compose_query(rule, since)` builds `from:@<sender_domain> after:<since epoch seconds>` and raises `EffectRefused` for a Gmail rule with no `sender_domain`, so at M0 no query can widen to the whole inbox (architecture §9). Every item comes back with `space=UNASSIGNED_SPACE_ID` and `routed_by=None`; `kernel/spaces.route` assigns the space, so routing authority stays in one module. Headers are `from`, `to`, `subject`, `date`, `message-id`. Recorded as an intent and outcome pair of `action_type="connector_read"` with null brief fields and `idempotency_key = f"gmail:{account}:{query}:{int(now.timestamp())}"`, the poll time included so two polls with one unchanged `since` are two effects.
- `fetch_body(conn, item: InboundItem) -> str`, for an item already routed. Refused for `item.space == UNASSIGNED_SPACE_ID` (architecture §9: header-only), for a space whose manifest has no connector on `item.account`, and when the fetched message's sender domain fails the connector's rule. Returns the decoded `text/plain` part, attachments never fetched. Recorded as above with `idempotency_key = f"gmail:{account}:id:{external_id}:{int(now.timestamp())}"`.
- Both kernel reads write their intent and outcome rows directly through `broker/ledger.py` and take no part in `closed_for_key` or `in_flight`. A read is repeatable by nature, and a poll that returned an earlier poll's outcome would never advance `since`; the second M0 objective's input depends on this line.
- `ConnectorRead(Action)`, a `read`, served by `perform` through this module's `run`. The only worker-requestable query is `id:<external_id>`; `check` refuses every other form (seams v2 §1.10, amendment C accepted). The person's inbox is a shared surface that belongs to no space (architecture §9), so a free query string would be the cross-space read channel blind-spot findings 7 and 9 closed. `run` finds its item with one `SELECT` on `inbound_items` by `(connector, account, external_id, space_id = action.space)` on the caller's connection, refuses with `item_not_in_space` when no row exists, then calls `fetch_body` and returns `{"headers": {...}, "body": str}`. `query` returns `absent` always. No worker tool reaches `request_effect` at M0, so `run` is exercised at M0 by one unit test against a seeded `inbound_items` row (task 6) and by `perform`'s tests; its first live requester arrives with `worker-effects` at M2.

The ingestion poll writes ledger rows (seams v2 §3.10, amendment B accepted), and the inbound events `kernel/spaces.ingest` writes through `record_inbound` are the routing record. Two records for one read is the cost of tech stack §7's "every request and result an audit event"; the ledger row says the credential was used, the inbound event says where the item went.

### Credentials

`broker/credentials.py` holds a table keyed by `(space_id, target)`:

| Space | Target | Keychain names |
|---|---|---|
| `psyoptimal` | `github.com/yudame` | `github_token` |
| `psyoptimal` | `mailto:tom@yuda.me` | `google_oauth_client_id`, `google_oauth_client_secret`, `gmail_refresh_token` |

Decided here: a code table rather than a manifest field or a third file. Tech stack §7 keys credentials by space and target, blind-spot finding 9 says why, and at M0 there are two rows. A second space gets its own rows and never shares one by accident; a missing pair is a refusal. `broker/` is inside the trust boundary, so a new row is a reviewed change. Values are read through `infra.secrets.read_secret` at use, never at import.

### Control flow for the two M0 paths

Code objective at `propose`. The Executor commits in `/work` and the walkthrough ends there (tech stack §10; seams v2 §1.10): the push is not exercised at M0, and the broker's part in this objective is the ledger the Verifier reads, which stays empty for it. The full path exists and is live-tested end to end in task 7: `perform(conn, PushBranch(...), None, token=...)` checks generation, space ceiling, target, credential, branch, HEAD; intent; push from the host mount; outcome `done`; and if the process dies between intent and outcome, the next start's `reconcile_dangling` asks `ls-remote` and writes `recovered` or re-runs. The caller that turns an Executor's request into that call is `worker-effects` at M2.

Message objective at `propose`. `kernel/__main__.py` runs `read_recent` for the PsyOptimal connector on integration's timer and hands the items to `kernel/spaces.ingest`. The Executor's Brief for "draft the reply" needs the body: the render calls `fetch_body(conn, item)` for the routed item, and both reads are ledger rows with null brief fields. The draft is a file on the worktree; nothing is sent, since `send` is an `act` at M3.

## Tasks

1. **`schemas/effect.py`.** `Action`, `PushBranch`, `ConnectorRead`, `EffectOutcomeKind`, `EffectOutcome` as seams §1.10 with amendment G, with `effect_class` and `action_type` class variables, key derivation, `target()`, `payload_sha256()`. *Accept:* `uv run pytest tests/test_effect_schema.py` green: a `PushBranch` with a hand-set key that differs from the derived one fails validation; `target()` matches the strings in `infra/spaces/psyoptimal.yaml`; two dumps of one action hash equal and a one-field change hashes different; a `ConnectorRead` with both ids `None` validates and round-trips through `model_validate(model_dump(mode="json"))`.
2. **Migration `migrations/versions/0007_broker.py`, `down_revision = "0006"`** (seams §7). The `effect_ledger` table, indexes, grants, triggers, policy. *Accept:* `uv run alembic upgrade head` then `downgrade -1` round-trip; `uv run pytest tests/test_effect_ledger.py::test_grants_are_exact tests/test_effect_ledger.py::test_update_refused_by_grant_then_trigger tests/test_effect_ledger.py::test_context_ro_reads_one_space tests/test_space_partition.py` green, the last being the spaces plan's catalog conformance test that replaced `tests/test_grants.py`.
3. **`broker/ledger.py`.** The six row writers and readers, each taking fields rather than an `Action`; `dangling` returns intents with no closing row; `closed_for_key` returns the last `done` or `recovered` close. *Accept:* `uv run pytest tests/test_effect_ledger.py::test_fold_per_effect_id tests/test_effect_ledger.py::test_kernel_read_rows_have_null_brief_fields` green: after intent and outcome the effect is closed; after intent alone it is dangling; a second intent for one `effect_id` raises `UniqueViolation`; a row written with the three brief fields `None` reads back with a `payload` that `ConnectorRead.model_validate` accepts.
4. **`broker/credentials.py` and `broker/manifest.py`.** The pair table and the manifest reader. *Accept:* `uv run pytest tests/test_broker_credentials.py` green: `("psyoptimal", "github.com/yudame")` resolves, `("other", "github.com/yudame")` raises `EffectRefused`, and `space_for("psyoptimal").allowed_targets` contains both M0 targets.
5. **`broker/push_branch.py` on the action.** `check`, `run`, `query`; `delete_branch` kept. *Accept:* `uv run pytest tests/test_push_branch.py` green with the refusal cases re-parametrised for `cori/<objective_id>` (`cori/other`, `cori/<objective_id>/x`, `main`, `master`, malformed) and none reaching `subprocess`; `test_head_moved_is_refused`; the live test pushes `cori/<fresh uuid>` to `yudame/cori-sandbox`, `query` returns `present`, delete, `query` returns `absent` (skips without `github_token`).
6. **`broker/gmail.py` extended.** `compose_query`, `read_recent`, `fetch_body`, the `ConnectorRead` module. *Accept:* `uv run pytest tests/test_gmail.py` green: `compose_query` always carries `from:@<domain>` and raises `EffectRefused` for a rule with no `sender_domain`; `fetch_body` on an unassigned item raises `EffectRefused` before any HTTP; a worker query other than `id:<hex>` is refused; `run` against a seeded `inbound_items` row in another space refuses with `item_not_in_space` before any HTTP, and against a row in the action's space calls a stubbed `fetch_body`; two `read_recent` calls with one `since` write two intents with distinct keys; the live test reads ten headers and one body from `psyoptimal.com` and every `from` matches (skips without the refresh token).
7. **`broker/actions.py` and `perform`.** The module registry and the door, checks in the order above. *Accept:* `uv run pytest tests/test_broker_perform.py` green against a fake target registered in tests: each refusal reason writes exactly one `refused` row and zero target calls, including an action with `brief_id=None`; a good action writes `intent` then `outcome/done` in two commits (asserted by a second connection seeing the intent before `run` returns); a run that raises with the target absent closes `outcome/failed`, with the target unreachable closes `outcome/unknown`; a repeated action returns the first outcome with no new row; a call inside an open transaction raises before any row; a generation bump between step 1 and step 6 (the stubbed `check_generation` flips on its second call) leaves one `refused` row and no intent. One live case, `test_perform_pushes_a_branch_to_cori_sandbox`: `perform` with a `PushBranch` for a fresh objective id and the `psyoptimal` credential row, branch `cori/<objective id>` on `yudame/cori-sandbox`, closes `outcome/done`, `ls-remote` shows the SHA, then `delete_branch` (skips without `github_token`).
8. **`reconcile_dangling` and `broker/__main__.py`.** *Accept:* `uv run pytest tests/test_broker_reconcile.py` green: three dangling intents against the fake target in states present, absent, differs close as `recovered`, `done`, `unknown`; a dangling kernel read with null brief fields re-runs through a stubbed `gmail` module and closes `reconciled/done`, and closes `outcome/failed` with no card when the stub raises; `uv run python -m broker reconcile` on the local database prints one line per dangling intent and its close.
9. **Properties.** The Hypothesis tests below. *Accept:* `uv run pytest tests/test_broker_properties.py` green at the example counts stated.
10. **README and records.** `broker/README.md` gains the credential table and the reconcile command; `broker/__init__.py` docstring names the protocol. *Accept:* `uv run black --check .` and `uv run lint-imports` clean; `uv run pytest` green with the live tests skipped.

## Properties

The pattern is spike 05: a strategy over the operations, a statement that must hold, thousands of examples for the pure ones. The four that touch Postgres use a fresh space id per example, `max_examples=200`, `deadline=None`, and a fake target module that records writes in a dict and can be told to raise before or after its write. Harness: Hypothesis runs a synchronous `@given` body, and a psycopg async connection is bound to one event loop, so each example runs under `asyncio.run` on a fresh `kernel_rw` connection opened inside the example and closed with it. `space_for` and `check_generation` are the module-level names in `broker/__init__.py`, monkeypatched per example with the generated manifest and generation, the way the tree plan patches its seat file.

- **Exactly once per key under kills.** Ranges over sequences of `perform(action)` for a small pool of actions, each call with a crash point in {none, after the intent commit before the action, after the action before the outcome commit}, and `reconcile_dangling()` inserted at arbitrary positions. Holds: every key was written to the fake target at most once; every key whose sequence contains a call with no crash, or a crash followed later by a reconcile, was written exactly once; the ledger has one `intent` per `effect_id` and at most one close; no example ever holds the `effect:` advisory lock outside the intent transaction (asserted by `pg_locks` from a second connection while the fake target runs). `tests/test_broker_properties.py::test_exactly_once_per_key_under_kills`. This is spike 02's reconcile column as a property.
- **The second lock holds before the target.** Ranges over generated `Space` manifests (any of the three ceilings, arbitrary target lists) and generated actions at `read` and `propose` with arbitrary targets. Holds: the fake target is called only if `EFFECT_RANK[effect_class] <= EFFECT_RANK[space.max_effect_class]` and `target() in allowed_targets`; every other example leaves exactly one `refused` row. `test_broker_refuses_outside_the_space_before_touching_the_target`.
- **A stale generation never reaches the target.** Ranges over tokens with `generation` from 1 to the objective's current generation plus two, against a stub `check_generation` that raises below current, and over a bump point in {none, between step 1 and step 6} at which the stub's notion of current increments. Holds: below current at either check, one `refused` row, no intent, and zero target calls; at current through both, the normal protocol. `test_stale_generation_never_reaches_the_target`.
- **The ledger fold is total.** Ranges over arbitrary interleavings of the row writers for many `effect_id`s, including the unique-violation retries. Holds: for every id the rows are `[intent]`, `[intent, outcome]`, `[intent, reconciled]`, or `[refused]`, and `dangling()` returns exactly the ids in the first shape. `test_ledger_fold_is_total`.
- **The branch is the objective's.** Ranges over objective ids and branch strings from a grammar that includes the id, other ids, prefixes, `..`, and the refused names. Holds: `check_branch(branch, objective_id)` accepts iff `branch == f"cori/{objective_id}"`. 2,000 examples. `test_push_branch_accepts_only_the_objectives_branch`.
- **A connector query never escapes the rule.** Ranges over `RoutingRule`s with any subset of the four optional fields set, `since` timestamps, and worker query strings. Holds: `compose_query` output contains `from:@<sender_domain>` whenever the rule has one and raises `EffectRefused` whenever it has none, and `ConnectorRead.check` accepts iff the query matches `id:[0-9a-f]+`. 2,000 examples. `test_connector_query_never_escapes_the_routing_rule`.

## Out of scope

- **The proposals that leave the space**, `open_pr`, `post_message_draft`, `calendar_hold`: M2 (tech stack §10). With them arrive the card-bound approval in `perform` step 2 for a proposal the supervisor asked about, the `argument_sha256` binding, and the target-state digest re-read of tech stack §7. The reserved names stay as a comment in `schemas/effect.py`.
- **`act` actions** `merge`, `send`, `deploy`, `pay`, the WebAuthn assertion check in the broker, and the passkey round trip: M3 (tech stack §7, §13, §14 spike 4).
- **A worker tool that reaches `request_effect`**: `worker-effects` at M2 (lead's answer to question 2). At M0 `push_branch` and `ConnectorRead` are reached by tests and by `reconcile_dangling` only. With that slug arrives the card for a `perform`-time `unknown`: `worker-effects` issues `approvals.unknown_outcome_card` for an `unknown` that `request_effect` returns, since at M0 only `kernel/__main__.py` issues that card and only for `reconcile_dangling`'s results (seams Round two).
- **Instructing Valor through the broker** (architecture §3.4, tech stack §5): the `valor` harness has no adapter at M0 (seams §2.1).
- **Calendar and messaging connectors**: the manifest admits them; M0 has one mail connector (tech stack §10).
- **The ingestion dispatch and its timer**: `read_recent` is the read; `kernel/__main__.py` (integration; seams §7, ruling 13) decides when it runs and hands the items to `kernel/spaces.ingest`.
- **Two kernel processes reconciling one ledger**: spike 02 left it for M1 with the Planner; one process at M0 (tech stack §2).
- **Batching proposals, away-state pauses, the attention record** (architecture §4, §7): kernel and surface concerns that begin when proposals leave the space (M2).
- **The 1Password service account path** for credentials off this Mac (prereqs finding 8): a TODO triggered by a second machine.
- TODOs from the refinement index that name the broker: none. The allowlisting proxy (tech stack §6) is the sandbox plan's and is out of scope there too.

## Risks

- **The fine-grained GitHub token expires 2026-10-19** (prereqs item 13). The live `push_branch` test and the code objective stop working that day unless the token is reissued; the Keychain name stays the same.
- **`ls-remote` as the query.** A network failure reads as `unreachable` and closes `unknown`, which is the conservative answer, but it makes an unattended restart during an outage produce a card rather than a retry. Spike 02 measured the policy against a local table, never a remote.
- **Gmail body decoding.** Multipart messages with no `text/plain` part return an empty body; the message objective's mail is from a known sender and the live test reads one real message, so the shape is checked once.
- **`push_branch` and `ConnectorRead` through `perform` have no live requester until M2.** Their M0 proof is the live test and the property suite; the first real call arrives with a tool bridge this plan does not own, so a mismatch between what the bridge fills and what `check` expects surfaces then.
- **The `reconcile` return value is a list, and the card is someone else's.** An `unknown` that nobody turns into a card is the silence architecture §7 forbids; `kernel/__main__.py` issues it (seams Round two) and integration task 8 asserts it appears.

## Questions for the architect

1. **Does M0's code objective push?** Tech stack §10 lists branch, edit, tests, commit; the plans index gives this plan `push_branch`. Assumption: the broker ships `push_branch` as a ledgered effect either way, tested live against `cori-sandbox`, and the walkthrough decides whether the first objective calls it.
2. **How does an Executor reach `request_effect`?** Tech stack §5 gives the loop five tools and seams §2.4 gives the kernel door, with nothing between them. Assumption: the worker plan bridges it (a sixth tool or a bridged command); the broker's door is `perform` and is unchanged either way. For the message objective the render can call `fetch_body` directly, so the second objective has a path without a new tool.
3. **Who vouches for `source_dir`?** The broker checks the directory is a git work tree at `head_sha`; it cannot check that it is the objective's mount without reading the tree's `briefs` table. Assumption: the adapter fills it from `Brief.sandbox_profile.mount_source` and the model never sets it; if the architect wants the broker to check, `perform` reads the brief's profile through a second kernel function.
4. **Should the ingestion poll be a ledger row?** Assumption yes (amendment B), for tech stack §7's "every request and result an audit event".

**Answered 2026-09-19 (lead, from the documents):** question 1: the M0 walkthrough ends at commit (tech stack §10); `push_branch` is built and live-tested against `cori-sandbox` and not called by the first objective. Question 2: no bridge at M0; the worker plan's `worker-effects` slug at M2 adds the tool, and `perform` is unchanged. Question 3: the tool bridge fills `source_dir` from `Brief.sandbox_profile.mount_source` at M2; the model never sets it. Question 4: yes, amendment B.

## Seam amendments

- **A. §3.10 `perform`.** Change to `async def perform(conn, action: Action, approval: ApprovalRecord | None, *, token: BriefToken) -> EffectOutcome`. Reason: the ledger row carries `generation` and tech stack §4 has the broker refuse a stale one; the token is the only source of both.
- **B. §3.10 `read_recent` and `fetch_body`.** Change to `async def read_recent(conn, connector: Connector, since: datetime) -> list[InboundItem]` and `async def fetch_body(conn, item: InboundItem) -> str`, and add to §5.3: "`objective_id`, `brief_id`, and `generation` are null on the class 0 reads the kernel itself performs." Reason: tech stack §7, every request and result an audit event; the poll has no brief.
- **C. §1.10 `ConnectorRead`.** Add after the class: "A worker-requested `query` is `id:<external_id>` and nothing else; the broker composes every other query from the connector's routing rule." Reason: the inbox is a shared surface (architecture §9); a free query is a cross-space read.
- **D. §1.10 `PushBranch`.** Replace "must start with cori/" with "equals `cori/<objective_id>`". Reason: tech stack §7, "restricted to the objective's branch name".
- **E. §5.3.** Add: "On `reconciled`, `outcome_kind` is `recovered` (the target had the key), `done` (re-run succeeded), or `unknown`." Reason: the table lists the kinds and the rows without saying which pair.
- **F. §1.10 and §5.3.** Add `failed` to `EffectOutcomeKind` and `outcome_kind`: the action raised and the target confirms the key is absent. Reason: `unknown` overstates the doubt when the target answered; a `failed` needs no card, only a report. Until accepted the plan writes `unknown` with the error.

**Lead, reconcile (seams v2, 2026-09-19):** amendments A to F accepted as written (seams §1.10, §3.10, §5.3). Ruling that touches this file: the effect lock is `pg_advisory_xact_lock(hashtextextended("effect:" + key, 0))` (seams §0).

- **G. §1.10 `Action`** (raised 2026-09-20 from critique finding 1). Change `objective_id: ObjectiveId` and `brief_id: BriefId` to `objective_id: ObjectiveId | None` and `brief_id: BriefId | None`, with the note: "None only on a class 0 read the kernel itself performs (`read_recent`, `fetch_body`); `perform` refuses a worker action with either missing." Reason: §5.3 already makes the ledger's `objective_id`, `brief_id`, and `generation` null for those reads and `reconcile_dangling` rebuilds every dangling payload as an `Action`, so the model has to be able to express the row it is rebuilt from. Until accepted, `broker/ledger.py`'s writers take fields rather than an `Action` (they do either way), a kernel read's payload is the `ConnectorRead` dict with the two ids absent, and `reconcile_dangling` dispatches a `connector_read` payload without ids to `gmail` directly instead of through `model_validate`.

## Findings

1. **Tech stack §2 against §4 and §7.** The boundary table says the broker never holds DB credentials; §4 says "the effect ledger, written by the broker", and §7 makes every request and result an audit event. The commit pass (item 6) made the broker a module in the kernel's process, and seams §3.10 hands it a connection. This plan follows the seams: the broker writes through a connection the kernel opens under `kernel_rw` and reads no database secret itself. §2's line should say that.
2. **Tech stack §10's M0 row omits the broker** while its "cheap now" list needs the connector for the second objective and the plans index assigns `push_branch` and the connector to M0. This plan follows the index (more recent). The row should name the ledger, `push_branch`, and the connector.
3. **Five tools and a kernel door with no bridge.** Tech stack §5 (five tools) and §7 ("an agent requests one through the kernel") with seams §2.4 (`request_effect`) leave no tool that calls it. Questions, 2.
4. **`broker/push_branch.py` accepts any `cori/*` branch**; tech stack §7 line 219 says "restricted to the objective's branch name". Tightened here (amendment D); the prereq test parametrisation changes with it.
5. **`broker/README.md` describes an External consent screen with test users**; prereqs item 12 recorded the audience as Internal under the Workspace org, with no seven-day expiry. The README is corrected in task 10.
6. **Seams §1.10 `PushBranch.source_dir` is a host path on a worker-visible action.** A model that could set it could push any repository the kernel process can read. Questions, 3; the tool bridge, not the model, fills it.

### Critique, 2026-09-20

Read against seams version 2 with its Round two section, the design documents cited, `broker/`, migration 0001, `tests/`, spike 02, and the spaces, worker, tree, surface, supervisor, and integration plans. Where this list touches the seams, the seams win and the disagreement is noted.

1. **Kernel-performed reads have no row shape.** Design, "The Gmail connector" (lines 148 to 152) and the table (line 88). `read_recent` and `fetch_body` write `intent` and `outcome` rows with null brief fields, the table has `payload jsonb not null` holding "the Action", and `reconcile_dangling` (line 121) loads an action from every dangling payload. Seams §1.10 makes `Action.objective_id` and `Action.brief_id` required, so no `ConnectorRead` can be built for these rows, and the plan does not say what `payload` or `result` holds for a poll. The seams stand: keep `Action` as listed and give `broker/ledger.py` writers that take the fields (`action_type`, `space`, `target`, `idempotency_key`, `payload: dict`) rather than an `Action`; state that a kernel read's payload is the `ConnectorRead` dict with the two ids absent and its `result` is the list of item headers for `read_recent`; state how reconcile treats a dangling kernel read (finding 5).
2. **The closed-key short circuit would freeze the poll.** Step 6 (line 107) against line 148. `read_recent`'s key is `gmail:{account}:{query}` with `since` inside the query, and the spaces plan feeds `since` as the greatest `received_at` already recorded, which stays fixed while no mail arrives. If the poll passes through step 6, the second poll with an unchanged `since` returns the first poll's outcome with no read, `since` never advances, and later mail is never fetched: the second M0 objective loses its input. Say that `read_recent` and `fetch_body` write their rows directly and take no part in `closed_for_key` and `in_flight`, or put the poll time in the key. The fold property (line 191) already tolerates many intents per key, so the property text stands.
3. **The generation check runs outside the intent transaction.** `perform` steps 1 and 6. `check_generation` runs first and the intent commits four checks later, so a `tree.stop` that commits between them lets a fenced Brief write an intent and push. Tech stack §4 has the broker refuse every later request carrying the old generation. Repeat `check_generation(conn, token)` inside the intent transaction under the effect lock, so the intent and the fence read one committed history, and extend `test_stale_generation_never_reaches_the_target` with a generation bump between the first check and the intent.
4. **`ConnectorRead.run` has no way to reach its item.** Line 150. `run` "calls `fetch_body` after checking the item's space against `action.space`", and `fetch_body(conn, item: InboundItem)` takes an item while the action carries `account`, `space`, and `id:<external_id>` only. Say where the item comes from: a read of `inbound_items` by `(connector, account, external_id, space_id = action.space)` on the caller's connection, refused when no row exists. Task 6 tests `check` and task 7 tests `perform` against a fake target, so `run` has no acceptance at all; add one or say plainly that `run` is unexercised at M0.
5. **Reconcile of a class 0 read contradicts itself.** Line 150 against the table at lines 123 to 128. Line 150 says a read re-runs on reconcile because it has no effect on the target; the table closes `present` as `recovered` without a re-run, and `gmail.query` is `present` when the message still exists. A recovered read has no `result`, and an unreachable Gmail at restart yields an `unknown_outcome` card for a read that touched nothing. Give the connector module its own rule: re-run when the target answers, close `failed` with no card when it does not; or make `gmail.query` return `absent` always and say so in the module table.
6. **A `perform`-time `unknown` has no card.** Step 8 and Risks (line 214). Step 8 returns `EffectOutcome(kind="unknown")` to `Door.request_effect` and nothing turns it into the card architecture §7 and §8 require; the card is assigned only to the caller of `reconcile_dangling`. The path is dormant at M0 because no worker reaches `perform`. Name the owner under Out of scope: the `worker-effects` slug at M2 issues `approvals.unknown_outcome_card` for an `unknown` returned by `request_effect`, so the M2 planner starts from this list.
7. **The migration number is still open after reconcile.** Task 2 (line 174). Seams §7 fixed `0007_broker.py` with `down_revision` `0006` (worker) and the plan's status is reconciled; the task still says "number and `down_revision` left for reconcile" and names `NNNN_broker.py`. Write the number in. Add to the acceptance that `tests/test_space_partition.py` (spaces) stays green after the migration, since it is the catalog conformance test that replaced `tests/test_grants.py` (seams Round two).
8. **`python -m broker reconcile` needs `broker/__main__.py`.** Modules table (line 80) and task 8. The table gives `__init__.py` the two doors and the command; `-m broker` runs `__main__.py`, which no row names. Add it to the table.
9. **Hypothesis over async Postgres is unstated.** Properties (line 186). Four properties drive async `perform` against Postgres at `max_examples=200`; Hypothesis runs synchronous bodies, and a psycopg async connection is bound to one event loop. State the harness: a synchronous `@given` body that runs each example with `asyncio.run` on a fresh `kernel_rw` connection, `deadline=None` as written. State how `space_for` and `tree.check_generation` are replaced in the two properties that generate manifests and generations: module-level names bound at import and monkeypatched, as the tree plan does.
10. **`compose_query` on a rule without `sender_domain`.** Line 148 and the property at line 193. `RoutingRule` has four optional fields and the property asserts `from:@<domain>` in every output. State that `compose_query` raises `EffectRefused` for a Gmail connector whose rule carries no `sender_domain` at M0, so the query can never widen to the whole inbox (architecture §9), and add that case to task 6.
11. **"Live-tested in task 5" overstates.** Line 167 and task 5. Task 5 live-tests the module's `run` and `query`; task 7 drives `perform` against the fake target only, so no test runs `perform` end to end against GitHub. Add one live `perform` case to task 7 (skipped without `github_token`, the `psyoptimal` credential row, branch `cori/<fresh objective id>` on `cori-sandbox`), or reword line 167 to say the module is what the live test covers.
12. **Header and Out of scope name the wrong neighbours.** Line 10 lists `record_inbound` under spaces, which the broker never calls; `kernel/spaces.ingest` calls it with `read_recent`'s items. `BriefToken` lives in `schemas/brief.py`, owned by tree, and can be named there. Line 202 gives the poll's timer to "the spaces or supervisor plan"; seams §7 (ruling 13) and the spaces plan give the dispatch and timer to integration. Align both lines with the seams.

Verdict: return to author. Findings 1 to 3 change what tasks 3, 6, and 7 build: the ledger writers for kernel reads, the poll's exemption from the closed-key rule, and the generation check inside the intent transaction.

**Author, 2026-09-20**, one line per finding:

1. Changed. `Action.objective_id` and `brief_id` are `| None` for the kernel's own reads (amendment G, proposed under Seam amendments with the fallback if refused); the door refuses a worker action with either missing (`perform` step 1); `broker/ledger.py` writers take fields rather than an `Action`; a kernel read's `payload` and `result` shapes are stated under the ledger table; reconcile of a kernel read is under finding 5.
2. Changed. `read_recent` and `fetch_body` write their rows directly and take no part in `closed_for_key` or `in_flight`, and both keys carry the poll time, so an unchanged `since` is two effects; task 6 checks two polls with one `since` produce two intents.
3. Changed. `check_generation` runs again inside the intent transaction under the effect lock (step 6); step 1 stays as the cheap early refusal; task 7 and the stale-generation property gain a bump between the two checks.
4. Changed. `run` reads `inbound_items` by `(connector, account, external_id, space_id = action.space)` on the caller's connection and refuses `item_not_in_space`; task 6 exercises `run` against a seeded row in each case.
5. Changed, within the seams. `gmail.query` returns `absent` always, so a dangling read takes the table's `absent` row and re-runs to `reconciled/done` with a fresh result; a re-run that raises closes `outcome/failed` with no card, which is §1.10's definition of `failed`; the `reconciled` kinds stay the three §5.3 lists.
6. Changed. Out of scope names `worker-effects` at M2 as the owner of `approvals.unknown_outcome_card` for a `perform`-time `unknown`; at M0 the path is dormant and `kernel/__main__.py` cards only `reconcile_dangling`'s results.
7. Changed. Task 2 names `0007_broker.py` with `down_revision = "0006"` and adds `tests/test_space_partition.py` to its acceptance.
8. Changed. `broker/__main__.py` has its row in the modules table; task 8 names it.
9. Changed. The Properties preamble states the harness: synchronous `@given` bodies, `asyncio.run` per example on a fresh `kernel_rw` connection, and `space_for` and `check_generation` as module-level names in `broker/__init__.py` monkeypatched per example.
10. Changed. `compose_query` raises `EffectRefused` for a Gmail rule with no `sender_domain`; task 6 and the connector-query property cover rules with and without one.
11. Changed. Task 7 gains one live `perform` case against `cori-sandbox` (skipped without `github_token`), and the control-flow paragraph now cites task 7 rather than task 5.
12. Changed. The header names `schemas/brief.BriefToken` under tree and `inbound_items` under spaces in place of `record_inbound`; the Gmail section, the message path, and Out of scope give the dispatch and timer to `kernel/__main__.py` (integration; seams §7, ruling 13) with `kernel/spaces.ingest` as the consumer of `read_recent`'s items.
