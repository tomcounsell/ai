# 03. Spaces

| | |
|---|---|
| Slug | `spaces` |
| Milestone | M0 |
| Status | built |
| Seams version | 3 (2026-09-20) |
| Owns | `schemas/space.py` (exists), `schemas/inbound.py`, `kernel/spaces.py`, tables `read_tokens` (exists) and `inbound_items`, migration `0004_spaces.py`, the row-level security pattern, and `tests/test_space_partition.py`, which replaces `tests/test_grants.py` (the tree plan retires that file, seams Round two) |
| Depends on | events (`kernel/events.py::append`, `schemas/ids.py`, `schemas/events.py`, migration 0002); tree for `schemas/capability.py` and migration 0003 |
| Written | 2026-09-19, against 1123f53; revised against seams v2 at 4cdd45c; cascaded 2026-09-20 to seams v3 (the budget and effect class ruling, `docs/reviews/2026-09-20-budget-ruling.md`) |
| Built | 2026-09-21, merge 63324d2 |

**Cascade, 2026-09-20 (lead).** Seams v3 removed the standing budget from the manifest and named the effect classes `read`, `propose`, `act`. In this plan: `StandingBudget`, `usd_micros_per_day`, the budget refusal in `check_may_open`, and the tests that asserted the architect's daily values are gone; `EffectClass` and `EFFECT_RANK` are the named form, and PsyOptimal's manifest reads `max_effect_class: propose`. The lead's cascade commit made the code changes in `schemas/space.py`, the manifest, and `tests/test_space.py`, so task 1 no longer waits on anything. The Findings, Seam amendments, and Critique sections below are the record of earlier rounds and still quote the old shape where they quote it; seams §3.4 has the current `check_may_open`, with two refusals.

## Purpose

M0 ships "spaces as manifests, row-level security" and proves "one objective end to end at `propose` inside one client space's sandbox, in code and in a drafted message" (tech stack §10, PROVISIONAL, COMMITTED on scope). This component is the "inside one client space" part: it loads the manifests, derives each space's root capability set, mints the read tokens that make the partition a database fact, routes inbound mail to a space or to `unassigned`, and refuses an objective in a space that does not exist. The first objective needs the PsyOptimal manifest and its root capabilities; the second needs the client's mail to arrive already routed to PsyOptimal so the supervisor can frame a reply in that space and nowhere else.

## What the documents say

- Architecture, five decisions, item 6 (ADDED): every conversation, objective, sandbox, and retrieval is scoped to exactly one space; a space's data never crosses.
- Architecture §9 (PATCHED, ending COMMITTED): a space is a typed manifest the kernel reads; the fields are id, kind, roots, max_effect_class, allowed_targets, retention, connectors, secrets, audience; the standing budget was removed 2026-09-20. Provider allowlist and sandbox locality wait for a client that asks.
- Architecture §9 (PATCHED): each connector carries a deterministic routing rule the kernel applies at ingestion; items no rule claims land in a reserved `unassigned` space where headers may be read and nothing else; explicit assignment stays the law and the rule is the explicit act, made once.
- Architecture §9 (PATCHED): credentials for connectors are held by the broker, keyed by space; a connector read is a `read` action outside the sandbox.
- Architecture §9 (COMMITTED): ending a space is a migration under `migrator` that deletes rows and appends `space.destroyed` with counts.
- Architecture §1 (REVISED): the space filter is a database fact; every space-partitioned table carries row-level security keyed to a per-render session variable; a turn with no space is refused.
- Architecture §3.1 (KEEP): capability subset is the covers relation on (name, effect class, scope) with the space on the scope axis.
- Architecture §4 (REVISED 2026-09-20): the budget is money per objective, estimated by the supervisor; there is no daily allowance and no per-space pool, so nothing on the manifest budgets anything. Effect classes are `read`, `propose`, `act`, named and ordered.
- Architecture §7 (KEEP, space rule ADDED): a conversation always has a space; switching is an explicit act on the surface.
- Tech stack §3 (LOCKED): three roles; row-level security on every space-partitioned table keyed to a read token the kernel mints per render into `read_tokens`, a table `context_ro` has no privilege on; the policy resolves the token through a `SECURITY DEFINER` function; `context_ro` cannot mint a token and a guessed value reads nothing; `context_ro` reads base tables through RLS and is never granted a materialized view.
- Tech stack §3 (LOCKED): append-only by grant; the trigger is the second lock; the destroy migration disables the trigger inside its own transaction.
- Tech stack §7 (LOCKED): every action payload carries its space; the broker refuses a target absent from `allowed_targets`. The broker's side, cited so this plan does not build it.
- Tech stack §10 (PROVISIONAL): space manifests are inside the trust boundary; M1 ships the morning brief.
- Prereqs, decisions table: first space PsyOptimal, roots, routing rule `sender_domain psyoptimal.com`, ceiling `propose`, connector account `tom@yuda.me`.
- Prereqs finding 1 (applied by the refinement pass): any role can set a session variable; the read-token table is the lock.
- Prereqs finding 2 (applied): RLS does not apply to materialized views.
- Prereqs finding 6 (applied): the append-only trigger blocks the migrator too.
- Prereqs item 15, disagreement 3: superseded 2026-09-20; `StandingBudget` is removed from the code with the cascade.
- Seams v3 §0: `UNASSIGNED_SPACE_ID = "unassigned"` is a constant in `schemas/space.py` with a validator on `Space.id` refusing it; it has no manifest and the kernel refuses to open an objective there. Ids are UUIDv7 strings minted by the kernel. Money is `usd_micros` and the only budget axis. Advisory locks, where taken, are `pg_advisory_xact_lock(hashtextextended(key, 0))` (ruling 11), in the namespaces `thread:`, `objective:`, `brief:`, `approval:`, `effect:`.
- Seams v3 §1.1: `schemas/space.py` unchanged except the constant, the validator, the `audience` field, the removal of `StandingBudget` and `Space.standing_budget`, and `EffectClass = Literal["read", "propose", "act"]` with `EFFECT_RANK`, the one place order is written.
- Seams v3 §1.1, architect 2026-09-20: PsyOptimal's manifest reads `max_effect_class: propose` and carries no standing budget; the lead's cascade commit wrote both.
- Seams v2 §1.1, architect 2026-09-19: `Audience` (`addresses`, `domains`, `source`) and `Space.audience` with an empty default are the allowed recipients of an outbound message, sourced by default from the client's directory README in the work vault; the verifier's `recipient_allowed` check reads it (§3.6); the connector's `sender_domain` rule is no longer a source of audience; a reader that syncs the README into the manifest is not built at M0, because the manifest is in the trust boundary and the vault is not.
- Seams §1.2: a space's root capability set is every `CapabilityName` at the manifest's `max_effect_class`, scoped to the space id, derived by `kernel/spaces.py`.
- Seams v2 §1.11: `InboundItem` fields, `headers` with lowercase keys; the body is a separate `read` the broker performs only for an assigned item; at M0 the Gmail read is scoped by the connector's rule, so `unassigned` is reached by tests and by a later connector.
- Seams v3 §3.4: the nine signatures of `kernel/spaces.py`, including `load_all(directory)` and its three refusals, `check_may_open` with its two refusals (`unassigned` and an unknown space), `mint_read_token` as `secrets.token_urlsafe`, `bind_read_token` transaction-local, `route` total with `unassigned` on zero or many matches, `record_inbound -> bool`, `ingest`, and `poll_connectors` with `read_recent` passed in.
- Seams §4: this component emits `inbound.routed`, `inbound.unassigned`, `space.destroyed`.
- Seams v2 §6: `read_tokens` exists; `inbound_items` is this plan's, insert-only, unique on `(connector, account, external_id, space_id)`; every table with a `space_id` carries RLS in the pattern of migration 0001; `context_ro` is granted `SELECT` only on tables that carry `space_id` and the policy (`request_bodies` and `gateway_tokens` never); `tests/test_space_partition.py` enforces both and replaces `tests/test_grants.py`, which no other plan extends (rulings 12, 15).
- Seams v2 §7: this plan owns `schemas/space.py`, `schemas/inbound.py`, `kernel/spaces.py`, `tests/test_space_partition.py`, and the retirement of `tests/test_grants.py`; its migration is `0004_spaces.py` after tree's 0003 (ruling 16). `kernel/__main__.py` and the inbound dispatch belong to integration (ruling 13).
- Seams v2, ruling 8, superseded by version 3: there is no standing budget; this plan supplies no budget value of any kind.

The index carries no line for `spaces` under "Requirements carried from the spikes". Nothing to pick up.

## What exists

- `schemas/space.py`: `Space`, `Connector`, `RoutingRule`, `Secret`, `Retention`, `EffectClass`, `EFFECT_RANK`, `DataClass`, `Strict`, `load_space`, as the lead's cascade commit left it (no `StandingBudget`; named classes). Kept; gains the constant and one validator.
- `infra/spaces/psyoptimal.yaml`: the first manifest, with `max_effect_class: propose` and no standing budget. Read, never edited by this plan; the lead writes the architect's `audience` value into it (seams §1.1).
- `migrations/versions/0001_roles_and_events.py`: the roles, `events`, `read_tokens`, `cori_current_space()`, `reject_mutation()`, and the two policies on `events`. The RLS pattern this plan generalizes is the last four statements of that file.
- `tests/test_rls.py`: five tests of the token path on `events`. Kept; this plan's tests extend the property to `inbound_items` and to any table that arrives later.
- `tests/test_grants.py`: an exact table of grants. The tree plan's task 4 retires it (seams Round two); this plan's `tests/test_space_partition.py` replaces it one step later in the build order and touches the old file in no way.
- `tests/test_space.py`: the manifest parses. Kept and extended.
- `broker/gmail.py::recent(sender_domain)`: the live read this plan's live test builds items from until the broker plan lands `read_recent`. Nothing from it is lifted into `kernel/`.
- No spike code is lifted. Spike 01's advisory-lock pattern is the tree's; spike 02's reconcile-by-key shaped the idempotent insert below.

## Seams

**Consumed**

- §0 `SpaceId` from `schemas/ids.py` (events); `uuid.uuid7()` for `InboundItem.id`.
- §1.2 `Capability`, `Capabilities`, `CapabilityName` from `schemas/capability.py` (tree), for `root_capabilities`.
- §1.8 `Event`, `EventType` (events).
- §3.1 `kernel/events.py::append(conn, *, space_id, type, payload)` (events).
- §6 the grant and trigger pattern of migration 0001 (events).

**Provided**

- §0 `UNASSIGNED_SPACE_ID`.
- §1.1 `schemas/space.py`, including `EffectClass` and `EFFECT_RANK`, which the tree's `covers`, `ceiling`, and `Ceilings.fits_within` compare by; §1.11 `schemas/inbound.py`.
- §3.4 all nine functions: `load_all`, `root_capabilities`, `check_may_open`, `mint_read_token`, `bind_read_token`, `route`, `record_inbound`, `ingest`, `poll_connectors`. `matches` is internal.
- §4 `inbound.routed`, `inbound.unassigned`. `space.destroyed` is reserved and emitted by no M0 code (Out of scope).
- §6 table `inbound_items`; RLS on `read_tokens`; the RLS pattern every other plan's migration copies; `tests/test_space_partition.py`, which fails when a plan forgets it and replaces `tests/test_grants.py`, which the tree plan retires (seams Round two).

## Design

No spike requirement is keyed to this slug.

### Modules

`schemas/space.py` (exists). Add `UNASSIGNED_SPACE_ID = "unassigned"` and a `field_validator` on `Space.id` that refuses it (seams v2 §0, §1.1), so a manifest claiming the reserved id is a parse error at the file. Decided here: the same validator requires `id` to match `^[a-z0-9][a-z0-9-]*$`, because `/` is the scope separator seams §1.2 splits on for `space_of`, `:` is the separator in every lock key of seams §0 and in this plan's rule text, and an id holding either would break `covers`, the lock namespace, and `matches`; `psyoptimal` fits the pattern. `EffectClass` is `Literal["read", "propose", "act"]` and `EFFECT_RANK` maps each to 0, 1, 2 (seams v3 §1.1); a manifest that writes a number is a parse error. Add `Audience` and `Space.audience: Audience = Field(default_factory=Audience)` exactly as seams v2 §1.1 gives them; this plan stores the field and the verifier plan reads it. Nothing in `kernel/spaces.py` consults `audience`: routing is by the connector's rule, and audience is about who may receive, which is the verifier's `recipient_allowed` check.

`schemas/inbound.py`. `InboundItem` exactly as seams v2 §1.11, frozen, `Strict`, `headers` keys lowercase (`from`, `to`, `subject`, `date`, `message-id`); the reader lowercases them before constructing the item, so `route` never has to guess a header's case.

`kernel/spaces.py`. One module, no state beyond what `load_all` returns. It takes no advisory lock: `record_inbound` relies on the unique constraint, and nothing here has a second writer.

```python
SPACES_DIR = Path(__file__).parents[1] / "infra" / "spaces"

class SpaceRefused(Exception): ...          # reason in the message

def load_all(directory: Path = SPACES_DIR) -> dict[SpaceId, Space]
def root_capabilities(space: Space) -> Capabilities
def check_may_open(space_id: SpaceId, spaces: dict[SpaceId, Space]) -> None   # raises SpaceRefused
async def mint_read_token(conn, space: SpaceId, ttl_s: int = 60) -> str
async def bind_read_token(conn_ro, token: str) -> None
def matches(item: InboundItem, spaces: dict[SpaceId, Space]) -> list[tuple[SpaceId, str]]
def route(item: InboundItem, spaces: dict[SpaceId, Space]) -> tuple[SpaceId, str | None]
async def record_inbound(conn, item: InboundItem) -> bool                      # True when a row was written
async def ingest(conn, spaces: dict[SpaceId, Space], items: Iterable[InboundItem]) -> list[InboundItem]
async def poll_connectors(conn, spaces, read_recent, *, lookback_days: int = 7) -> list[InboundItem]   # conn is autocommit; read_recent(conn, connector, since)
```

**`load_all`** reads every `*.yaml` under `infra/spaces/` with `load_space` and refuses duplicate ids, the reserved id, and identical `(kind, account, route)` connectors across spaces (seams v2 §3.4). The reason for the third refusal: an identical rule in two spaces would send every matching item to `unassigned` forever, and the manifest is the place to catch it. Decided here, a fourth refusal: a `gmail` connector whose rule sets no `sender_domain`. An empty `RoutingRule` is valid to the schema and would claim every item on its account, a rule setting only `label`, `folder`, or `calendar` matches nothing at M0 and would send the whole account to `unassigned` in silence, and the broker composes its Gmail query from `sender_domain` (broker plan, "The Gmail connector"), so a rule without one has no read either. The `directory` argument is how tests load fixture manifests.

**`root_capabilities`** returns `frozenset(Capability(name=n, effect_class=space.max_effect_class, scope=space.id) for n in CapabilityName)` (seams §1.2). Nothing else. The supervisor's smaller set is issued from this by the tree.

**`check_may_open`** raises `SpaceRefused` when `space_id == UNASSIGNED_SPACE_ID` or when `space_id` is not in `spaces` (seams v3 §3.4). One function for the two refusals, so `tree.open_objective` and the supervisor's turn call it once and the reason text is the same on every surface. It is pure; the caller holds the loaded manifests.

**`mint_read_token`** inserts `(token, space, now() + ttl_s)` into `read_tokens` on the kernel connection and returns the token, which is `secrets.token_urlsafe(32)` (seams v2 §3.4): a read token is a secret, and a UUIDv7 is time-ordered and guessable. Decided here: `ttl_s` must be between 1 and 600; a render that needs longer is a bug.

**`bind_read_token`** runs `SELECT set_config('cori.read_token', %s, true)` on the `context_ro` connection, transaction-local, and the render runs inside one transaction (seams v2 §3.4). The scope dies with the render's transaction, so a connection handed to the next render carries no space. `tests/test_rls.py` used session scope; the property it tests is unchanged.

**`matches`** returns every `(space_id, rule_text)` whose connector has the item's `connector` kind and `account` and whose rule matches the item, in space id order (seams §3.4, deterministic). Decided here, rule semantics for M0: a rule matches when every field it sets matches; `sender_domain` compares case-insensitively against the domain of the address `email.utils.parseaddr` extracts from `headers["from"]`, exact match only (a subdomain is its own rule); `label`, `folder`, and `calendar` compare against `headers.get("label")`, `headers.get("folder")`, `headers.get("calendar")`, which the M0 reader does not populate, so a rule setting them matches nothing at M0. `rule_text` is `"<space_id>:<kind>:<account>:<field>=<value>[,<field>=<value>]"`.

**`route`** is `matches` reduced: exactly one match returns it; zero or more than one returns `("unassigned", None)` (seams v2 §3.4). An item two spaces both claim is never guessed at, because a wrong guess between two clients is the leak the partition exists to prevent; `ingest` records the candidates on the event so the person can see why.

**`record_inbound`** inserts one row into `inbound_items` with `ON CONFLICT DO NOTHING` on `(connector, account, external_id, space_id)`, and when a row was written appends `inbound.routed` (payload `item`, `rule`) or `inbound.unassigned` (payload `item`, `candidates`) to `events` under the item's space; it returns `False` when the row already existed (seams v2 §3.4, §4, §6). The key makes polling's re-reads harmless (spike 02's lesson applied to reads) and lets an item assigned later to another space get a row in that space, which RLS requires for that space's render to see it. The `unassigned` row stays header-only forever, which is what architecture §9 asks. `candidates` on `inbound.unassigned` is seams §4 text: it is `list[SpaceId]`, the space ids `matches` returned, in space id order, and empty when no rule matched; the surface's `route_inbound_card(item, candidates)` reads it as space ids.

**`ingest`** routes each item, copies it with the routed `space` and `routed_by`, records it, and returns the items that were newly recorded (seams v2 §3.4). Callers use the returned items' ids, since a re-read mints a fresh `id` for an item already stored and only the first is kept.

**`poll_connectors`** iterates every connector of every loaded space, calls `read_recent(conn, connector, since)` for each (the arity of seams §3.10), and `ingest`s the result; `read_recent` is passed in so `kernel/spaces.py` never imports `broker/`, which depends on this plan (seams v2 §3.4). Decided here, the connection: `poll_connectors` takes a kernel connection opened with `autocommit=True` and refuses one that is inside a transaction (`conn.info.transaction_status != IDLE` raises `RuntimeError` before any read), because the broker's `read_recent` writes its own intent and outcome rows and needs the intent committed before the network call, which a caller's enclosing transaction would demote to a savepoint (broker plan, `perform`). The poll then runs its `since` query in autocommit, calls `read_recent` and lets it manage its own transactions, and wraps `ingest(conn, spaces, items)` for that connector in `async with conn.transaction():`, so a connector's rows and events land together or not at all. Decided here, `since`: `min(max(received_at), now()) - timedelta(hours=1)` over the rows recorded for that `(connector, account)`, or `now() - lookback_days` when none. The clamp to `now()` keeps one future-dated `Date` header from moving `since` past the present and starving the account; the hour of overlap covers delivery delay, and `record_inbound`'s key makes the overlap harmless. The dispatch that calls it is integration's (seams v2 §7, ruling 13): at M0 the integration plan's task 3 calls `poll_connectors` once by hand with `broker.read_recent`, and no timer exists yet. At M0 the Gmail read is scoped by the rule (seams v2 §1.11), so every item it returns routes to `psyoptimal` and `unassigned` is reached by tests.

### Table

`inbound_items`, created by `migrations/versions/0004_spaces.py` with `down_revision = "0003"` (seams v2 §7, ruling 16), so this migration lands after tree's:

| Column | Type | Note |
|---|---|---|
| id | bigint identity | |
| item_id | text | `InboundItem.id`, UUIDv7 |
| space_id | text | the routed space or `unassigned` |
| connector, account, external_id | text | the provider's message id |
| headers | jsonb | lowercase keys |
| received_at | timestamptz | |
| routed_by | text, null | the rule text; null in `unassigned` |
| schema_version | integer | default 1 |
| at | timestamptz | default now() |

Unique on `(connector, account, external_id, space_id)`. Index on `(space_id, id)`. Owner `migrator`. Grants `SELECT, INSERT` to `kernel_rw`, `SELECT` to `context_ro`. Triggers `inbound_items_append_only` (UPDATE, DELETE) and `inbound_items_no_truncate` on the existing `reject_mutation()`. RLS enabled with the two policies of migration 0001 renamed for the table.

The same migration enables row-level security on `read_tokens` with the single policy `read_tokens_kernel ON read_tokens TO kernel_rw USING (true) WITH CHECK (true)` and grants `context_ro` nothing on it, as today. Seams §6 says every table with a `space_id` carries RLS, and `read_tokens` has one; migration 0001 left it off. `cori_current_space()` still resolves: it is `SECURITY DEFINER` owned by `migrator`, which owns the table and bypasses its policies. `tests/test_rls.py` runs unchanged.

### The row-level security pattern

Every plan that creates a table with a `space_id` column copies these four statements into its migration, with its table name:

```sql
ALTER TABLE <t> ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON <t> TO context_ro;
CREATE POLICY <t>_kernel ON <t> TO kernel_rw USING (true) WITH CHECK (true);
CREATE POLICY <t>_context_by_token ON <t> FOR SELECT TO context_ro USING (space_id = cori_current_space());
```

Decided here: the pattern is four lines of SQL each plan copies rather than a shared helper module, because a migration that imports a Python helper is one more thing the reviewer has to open, and the conformance test below catches a plan that forgets. The test, `tests/test_space_partition.py` (seams v2 §6, §7), queries the catalog once per run and asserts, for every table in `public`: every table with a `space_id` column has `relrowsecurity` true and a policy for `kernel_rw`; if `context_ro` holds any privilege on a table, the table has a `space_id` column and a `SELECT` policy for `context_ro` whose expression contains `cori_current_space()`; `context_ro` holds nothing but `SELECT` anywhere; `kernel_rw` holds no `UPDATE`, `DELETE`, or `TRUNCATE` anywhere; only `migrator` holds `DELETE`. The first two predicates are the two rules seams v2 §6 states (ruling 15): a `space_id` table always carries RLS, and `context_ro` reads only such tables, because a table without one (`request_bodies`, `gateway_tokens`) has no row a policy can filter. The first predicate is why 0004 enables RLS on `read_tokens`. The test replaces `tests/test_grants.py`, which stated the same rules as an exact table and broke for every new table; the tree plan retires that file one step earlier in the build order (seams Round two), and this plan leaves it alone.

### Control flow that matters

Render scope. The supervisor's Context Builder, on the kernel connection, calls `mint_read_token(conn, space)`; on its `context_ro` connection it opens a transaction, calls `bind_read_token(conn_ro, token)`, runs every read of the render, and rolls back. Inside that transaction it sees exactly the rows of `space` in every RLS table; outside it, nothing. A bug in the render code that queries another space's rows returns an empty set, which is architecture §1's claim made concrete.

Inbound. Integration's task 3 calls `poll_connectors` once by hand on an autocommit connection; a timer arrives with integration when it does. For the PsyOptimal connector, `read_recent` returns items on `tom@yuda.me`; `route` sends mail from `psyoptimal.com` to `psyoptimal` with rule text `psyoptimal:gmail:tom@yuda.me:sender_domain=psyoptimal.com` and everything else to `unassigned`; `record_inbound` writes each new row and its event. The supervisor's next turn in `psyoptimal` sees the routed item as an `inbox` slice line; the `unassigned` item is a `route_inbound` card (surface plan) that this plan does not answer at M0 (Out of scope).

## Tasks

1. **Reserve `unassigned` in the schema, add `Audience`.** First `grep -q 'max_effect_class: propose' infra/spaces/psyoptimal.yaml && grep -q 'audience:' infra/spaces/psyoptimal.yaml`; when the second fails, stop and hand the manifest to the lead, whose design-edit commit writes the architect's `audience` value (seams §1.1). The builder never edits the manifest. Then add `UNASSIGNED_SPACE_ID`, the id validator (reserved id and charset), `Audience`, and `Space.audience` to `schemas/space.py`. *Accept:* the two greps exit 0; `uv run pytest tests/test_space.py::test_unassigned_id_is_refused tests/test_space.py::test_space_id_charset_is_restricted tests/test_space.py::test_psyoptimal_manifest_parses tests/test_space.py::test_effect_class_is_named tests/test_space.py::test_audience_defaults_empty tests/test_space.py::test_psyoptimal_audience_parses` green, where `test_space_id_charset_is_restricted` refuses `a/b`, `a:b`, `A`, and an empty id and accepts `psyoptimal` and `my-space-2`, `test_effect_class_is_named` (exists since the cascade) refuses `2` and `merge` and accepts the three names, and `test_psyoptimal_audience_parses` asserts that `infra/spaces/psyoptimal.yaml` parses with `audience.domains == ["psyoptimal.com"]`, `audience.addresses == []`, and `audience.source == "~/work-vault/PsyOptimal/README.md#contacts"`.
2. **`schemas/inbound.py`.** `InboundItem` per seams §1.11. *Accept:* `uv run pytest tests/test_inbound.py::test_inbound_item_is_strict_and_frozen tests/test_inbound.py::test_headers_keys_are_lowercase` green.
3. **Manifests and root capabilities.** `load_all`, `root_capabilities`, `check_may_open`, `SpaceRefused`. *Accept:* `uv run pytest tests/test_spaces_manifests.py` green: `test_load_all_finds_psyoptimal`, `test_duplicate_ids_refused`, `test_reserved_id_refused`, `test_identical_rules_across_spaces_refused`, `test_rule_without_sender_domain_refused` (an empty rule and a label-only rule on a gmail connector both refused), `test_root_capabilities_are_every_name_at_the_ceiling`, `test_root_capabilities_never_cover_another_space` (property), `test_unassigned_refuses_objectives`, `test_unknown_space_refuses_objectives`, `test_psyoptimal_may_open` (the live manifest).
4. **Migration `0004_spaces.py`.** The `inbound_items` table, grants, triggers, RLS pattern, RLS on `read_tokens` with the `kernel_rw` policy, `down_revision = "0003"`. Waits for tree's 0003 to exist. *Accept:* `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` succeeds; `uv run pytest tests/test_inbound_items.py::test_kernel_cannot_update_or_delete tests/test_rls.py tests/test_read_tokens.py::test_read_tokens_has_rls_and_no_context_grant` green.
5. **Read tokens.** `mint_read_token`, `bind_read_token`. *Accept:* `uv run pytest tests/test_read_tokens.py` green: `test_token_reads_exactly_one_space` (property, over `events` and `inbound_items`), `test_bind_is_transaction_local`, `test_ttl_out_of_range_refused`, `test_token_is_not_time_ordered`.
6. **Conformance test for the partition.** Write `tests/test_space_partition.py` as described (seams v2 §6, §7). `tests/test_grants.py` is the tree plan's to retire (seams Round two) and this task touches no other file. *Accept:* `uv run pytest tests/test_space_partition.py` green on `events`, `read_tokens`, `inbound_items`, and the four tree tables: `test_every_space_table_has_rls`, `test_every_context_ro_table_is_filtered_by_token`, `test_context_ro_reads_only_space_tables`, `test_context_ro_holds_only_select`, `test_kernel_rw_never_mutates`, `test_only_migrator_deletes`. Three scratch negatives, each applied under `migrator` in a test transaction that rolls back: a table with `space_id`, RLS off, no grants fails `test_every_space_table_has_rls`; a table with `space_id`, granted `SELECT` to `context_ro`, no policy fails `test_every_context_ro_table_is_filtered_by_token`; a table with no `space_id` column granted `SELECT` to `context_ro` fails `test_context_ro_reads_only_space_tables`. The negatives live in the test file as `test_negatives_are_caught` so they run at build time and at every later build.
7. **Routing.** `matches`, `route`. *Accept:* `uv run pytest tests/test_route.py` green: `test_psyoptimal_mail_routes_to_psyoptimal`, `test_other_domain_routes_to_unassigned`, `test_display_name_and_case_do_not_matter`, `test_subdomain_does_not_match`, `test_route_is_total_and_deterministic` (property), `test_route_never_claims_across_accounts` (property), `test_overlapping_rules_never_guess` (property).
8. **Recording.** `record_inbound`, `ingest`. *Accept:* `uv run pytest tests/test_inbound_items.py` green: `test_routed_item_writes_row_and_event`, `test_unassigned_item_writes_row_and_event_with_candidates`, `test_record_inbound_is_idempotent` (property: rows equal distinct keys and events equal rows), `test_ingest_returns_only_new_items`.
9. **Polling.** `poll_connectors` with a stub reader, plus the live check. *Accept:* `uv run pytest tests/test_poll.py` green: `test_poll_reads_every_connector_and_ingests`, whose stub reader has the seams §3.10 arity, asserts `conn.info.transaction_status == IDLE` when called, runs `INSERT` and `commit()` of its own on that connection mid-poll (as the broker's ledger write does), and returns items, after which the poll's `inbound_items` rows and `inbound.*` events for the connector are both present and equal in count; `test_poll_refuses_a_connection_inside_a_transaction` (a non-autocommit connection with an open transaction raises `RuntimeError` and the stub is never called); `test_since_is_the_last_received_at`, with three cases: no rows gives `now() - lookback_days`, a past `received_at` gives that time minus one hour, and a `received_at` one day in the future gives at most `now()` minus one hour; and `test_live_psyoptimal_mail_routes_to_psyoptimal`, which builds items from `broker.gmail.recent("psyoptimal.com")` by lowercasing the header keys, parsing `Date` with `email.utils.parsedate_to_datetime`, and using the Gmail id as `external_id`, asserts `route(item, spaces)[0] == "psyoptimal"` for every built item, ingests them, selects `inbound_items` by each `external_id` and asserts every row has `space_id = 'psyoptimal'`, and asserts a second ingest writes zero rows; the live test skips when `gmail_refresh_token` is absent from the Keychain, as `tests/test_gmail.py` does.
10. **Format and contract.** *Accept:* `uv run black --check .` and `uv run lint-imports` clean; `uv run pytest -q` green with the same skips as before.

## Properties

All under `kernel/spaces.py`. Database properties open their connections inside the example, use fresh space ids per example, and run with `max_examples=25, deadline=None`. The manifest generator draws space ids from `^[a-z0-9][a-z0-9-]*$` (the validator's pattern) and never `unassigned`, so a generated manifest is one `load_all` would accept.

- **Routing is total and deterministic.** Over generated manifest sets (one to four spaces, each zero to three gmail connectors with distinct accounts and rules) and generated items (any kind, account, and `from` header): `route(item, spaces)` returns an id in `spaces | {"unassigned"}`, returns the same value on a second call, and returns a non-null rule text exactly when the id is a real space. `tests/test_route.py::test_route_is_total_and_deterministic`.
- **A rule never claims across accounts.** Same generators: when `route` returns a space, that space declares a connector with the item's `connector` and `account`. `tests/test_route.py::test_route_never_claims_across_accounts`.
- **Overlap never guesses.** Over items constructed so that two or more spaces match: `route` returns `("unassigned", None)` and `matches` lists every candidate. `tests/test_route.py::test_overlapping_rules_never_guess`.
- **Root capabilities stay in their space.** Over pairs of generated manifests with different ids and any ceilings: every capability in `root_capabilities(a)` has `scope == a.id` and `effect_class == a.max_effect_class`, and `within(root_capabilities(b), root_capabilities(a))` is false; `issue(root_capabilities(a), requested)` raises `Refused` for any non-empty `requested` scoped to `b.id`. `tests/test_spaces_manifests.py::test_root_capabilities_never_cover_another_space`.
- **A token reads exactly one space.** Over two to four fresh space ids with generated row counts in `events` and `inbound_items`: for each space, mint a token, bind it in a `context_ro` transaction, and `SELECT count(*)` from both tables returns that space's counts and nothing from the others; after rollback both counts are zero. `tests/test_read_tokens.py::test_token_reads_exactly_one_space`.
- **Recording is idempotent.** Over lists of items with repeats and mixed routed and unassigned targets: after `ingest`, `inbound_items` holds one row per distinct `(connector, account, external_id, space_id)` and `events` of types `inbound.*` under those spaces number exactly the rows. `tests/test_inbound_items.py::test_record_inbound_is_idempotent`.

## Out of scope

- **Assigning an `unassigned` item to a space by card.** The `route_inbound` card is the surface's (seams §1.12); answering it needs an `inbound.assigned` event and a second row in the target space, which this table already allows. No M0 objective exercises it; tech stack §10 M0 routes the client's mail by rule. Proposed slug `inbound-assign`, M1.
- **Budgets of any kind.** The manifest carries none (architecture §4, §9; seams v3). The objective's budget is the tree's and the estimate is the supervisor's.
- **Syncing the README's Contacts table into `Space.audience`.** Seams v2 §1.1: the manifest is the value the kernel reads because it is in the trust boundary and the vault is not; a reader that copies the README into the manifest is not built at M0. The manifest value is written by hand, and the README's address column is the architect's to add.
- **The inbound dispatch and its timer.** `kernel/__main__.py` and the dispatch that calls `poll_connectors` belong to integration (seams v2 §7, ruling 13).
- **Ending a space.** Architecture §9 (COMMITTED) and tech stack §3 describe the migrator's migration and the tombstone; `tests/test_append_only.py::test_owner_can_disable_trigger_to_destroy_a_space` already proves the mechanism on `events`. No space ends at M0. The `space.destroyed` type is reserved in seams §4 and emitted by no M0 code. Proposed slug `space-destroy`, built when the first engagement ends.
- **Retention.** `Retention.memory_days` and `artifacts_days` are enforced by the memory and sandbox plans when a sweep exists; nothing at M0 is a year old.
- **Secrets injection.** `Space.secrets` is empty in the first manifest and the worktree profile's `env` is the tree's to fill (seams §1.9); a Keychain resolver is written when a manifest lists a secret.
- **Cross-space views** (architecture §9, PATCHED): the morning brief is M1.
- **A retention sweep on `read_tokens`.** Rows accumulate at one per render; the migrator deletes expired rows when the count matters.
- **Provider allowlist and sandbox locality on the manifest** (architecture §9, dropped): they return with the client that asks. Not designed, no hook left.
- **Per-space encryption** (commit pass item 7, dropped).
- TODOs from the refinement index that touch a space and are left alone: task templates fix a space (architecture §7), the allowlisting proxy (tech stack §6), classification propagation (architecture §3.1).

## Risks

- **The Gmail read is scoped by the rule at M0** (seams v2 §1.11, Question 2 answered), so the `unassigned` path runs only under test until a second connector exists. The routing code is total, so widening the read later touches the broker alone.
- **The `audience` value lands in a separate commit by the lead** (seams §1.1). Until then `test_psyoptimal_audience_parses` fails; task 1 is built after that commit or against it.
- **Hypothesis against a live database** needs care with async fixtures; spike 01 ran its properties by opening connections inside the example, and this plan does the same. Prereqs item 2 says the database is reachable in CI.
- **Between the tree's retirement of `tests/test_grants.py` and this plan's task 6,** no test states the grant rules; the window is one plan in the build order (seams Round two). If a table lands in that window without RLS, task 6's first green run reports it.
- **`poll_connectors` on the wrong connection.** A caller that passes a non-autocommit connection is refused before any read; a caller that passes an autocommit connection shared with other work sees the poll's `conn.transaction()` blocks interleave with its own statements. Integration owns the caller and opens a connection for the poll alone.
- **Transaction-local binding** assumes the Context Builder renders in one transaction. If the supervisor plan renders across several, the bind is repeated per transaction; the token is still valid for `ttl_s`.

## Questions for the architect

1. **What is PsyOptimal's standing budget?** `infra/spaces/psyoptimal.yaml` holds `standing_budget: null`, the manifest is inside the trust boundary, and no plan edits it. Until it is set, `check_may_open` refuses every objective in the space, so the M0 walkthrough cannot start. *Assumption while waiting:* the refusal is built as the seams say; the integration plan's first step is the architect's one-line edit. A starting value consistent with tech stack §9 ("on the order of ten class 2 batches a day"), matching the lead's open question 1 in the findings record: `tokens_per_day: 2000000`, `usd_per_day: 20`, `cards_per_day: 10`.
2. **Does the Gmail connector read the whole account and let the kernel route, or read only what the rule selects?** Architecture §9 says the kernel routes at ingestion and unclaimed items land in `unassigned`; the code and seams §3.10 keep the read scoped to the rule. *Assumption:* scoped at M0, as the code exists; `route` and `ingest` handle both, and the broker plan owns the change if the answer is "whole account".

**Answered 2026-09-19 (lead, from the documents), question 2:** rule-scoped at M0, as `broker/gmail.py` exists and seams §1.11 now states; `unassigned` is reached by tests and by any later connector.

**Answered 2026-09-19 (architect), question 1:** `tokens_per_day: 2000000`, `usd_per_day: 20`, `cards_per_day: 10` (seams v2 §1.1). The lead writes the value into `infra/spaces/psyoptimal.yaml`; `check_may_open` keeps the refusal for any space whose budget is unset, and `test_psyoptimal_refuses_until_budget_is_set` becomes a fixture-manifest test, `test_unset_standing_budget_refuses_objectives`, since the live manifest now has a value.

**Superseded 2026-09-20 (architect, seams v3):** the standing budget is removed; question 1 is moot and the manifest carries no budget.

## Seam amendments

1. **§3.4, add four signatures** after `record_inbound`:

   ```python
   async def bind_read_token(conn_ro, token: str) -> None   # set_config('cori.read_token', token, true): transaction-local; the render runs inside one transaction
   def check_may_open(space_id: SpaceId, spaces: dict[SpaceId, Space]) -> None   # raises SpaceRefused for "unassigned", an unknown space, or standing_budget None; tree.open_objective calls it first
   async def ingest(conn, spaces: dict[SpaceId, Space], items: Iterable[InboundItem]) -> list[InboundItem]   # route, copy with space and routed_by, record; returns the newly recorded items
   async def poll_connectors(conn, spaces, read_recent, *, lookback_days: int = 7) -> list[InboundItem]   # read_recent is broker.read_recent, passed in so kernel/spaces.py never imports broker/
   ```

   Reason: the tree needs one call for the two refusals seams §0 and §1.1 assign to "the kernel"; the Context Builder needs the bind semantics written down; the integration plan needs a poll to wire to a timer.

2. **§6, after the RLS sentence, add:** "`context_ro` is granted `SELECT` only on tables that carry `space_id` and the policy; a table without `space_id` (`request_bodies`, `context_manifests`) is never granted to it. `tests/test_space_partition.py` (spaces) enforces both." Reason: a grant on an unpartitioned table is a cross-space read the policy cannot filter.

3. **§1.1, change** "Unchanged except the constant in §0" **to** "Unchanged except the constant in §0 and a validator refusing the reserved id on `Space.id`". Reason: the collision becomes a parse error at the manifest.

4. **§6, `inbound_items` row,** replace the note with: "the InboundItem columns plus `space_id` and `routed_by`; insert-only; unique on `(connector, account, external_id, space_id)` so a re-read is a no-op and a later assignment is a second row in the target space". Reason: RLS means an assigned item needs a row in the space that will render it.

**Lead, reconcile (seams v2, 2026-09-19):** amendments 1 to 4 accepted as written (seams §0, §1.1, §3.4, §6). Rulings that touch this file: `tests/test_grants.py` is retired by this plan's `tests/test_space_partition.py`, and no other plan extends it; the `Depends on` line in the index gains `tree` for `schemas/capability.py`; the Gmail read stays rule-scoped at M0.

**Author, 2026-09-20:** the first ruling above is superseded by seams Round two, which moves the retirement of `tests/test_grants.py` to the tree plan's task 4; this plan's test is the replacement and lands one step later. The plan reads accordingly.

## Findings

Dispositions are the lead's, from `docs/reviews/2026-09-19-plan-findings.md`.

1. **Routing at ingestion versus a rule-scoped read.** Architecture §9 (PATCHED) line 530: connectors arrive on "shared surfaces that belong to no space" and the kernel routes at ingestion, with `unassigned` for the rest. Seams §3.10 keeps `broker/gmail.py` "as it exists", and `broker/gmail.py::query_for` line 122 builds `from:@<domain>`, so the read returns only what the rule already selects and Gmail can never produce an `unassigned` item. This plan keeps `route` total so the broker can widen the read without touching `kernel/spaces.py`. *Disposition (findings 46):* noted; rule-scoped at M0, seams v2 §1.11.
2. **`tests/test_grants.py` enumerates exact grants.** `EXPECTED` at line 7 lists four (role, table) pairs and `test_grants_are_exactly_as_stated` fails on any grant outside them, so every plan that creates a table breaks it. *Disposition (findings 5, then seams Round two):* resolved; the tree plan retires the file in its task 4, the first table-adding plan, and this plan's `tests/test_space_partition.py` replaces it in task 6.
3. **`reject_mutation()` names `events` in its message** (migration 0001 line 79) and is reused by every insert-only table. `TG_TABLE_NAME` would name the right table. *Disposition (findings 4):* code; the events plan uses `TG_TABLE_NAME`.
4. **Tables without `space_id` in seams §6.** At v1, `request_bodies` and `context_manifests` had no column a policy could filter on. *Disposition (findings 6):* resolved; `context_ro` is granted only on tables with `space_id`, seams v2 §6; `context_manifests` gained `space_id` at v2 and the unpartitioned pair is now `request_bodies` and `gateway_tokens`.
5. **Index dependency line.** `docs/plans/README.md` said `spaces` depends on `events` only; `root_capabilities` consumes `schemas/capability.py`, which seams §7 gives to `tree`. *Disposition (findings 47):* the index line now reads "events; tree for `schemas/capability.py`". Task 3 and task 4 (migration order) wait on tree; tasks 1, 2, and 5 to 9 do not.

### Critique, 2026-09-20

Read against seams v2 with its Round two section, the code the plan cites, and the tree, broker, supervisor, and integration plans. Three independent critics (risk, scope, consistency) ran beside the read; where two or more reached a finding on their own it says so.

1. **Stale against seams Round two: this plan no longer retires `tests/test_grants.py`.** Header `Owns` (line 9), Seams Provided §6 (line 78), Design (line 158), task 6 (line 173), Risks (line 210), the reconcile note (line 241), and Findings 2 (line 248) all have this plan run `git rm tests/test_grants.py`. Seams Round two, last bullet, and findings record row 63 give the retirement to the tree plan's task 4, one step earlier in the build order; this plan's `tests/test_space_partition.py` is the replacement that lands one step later. As written, task 6 removes a file that is already gone and cites ruling 12, which Round two supersedes. The seams §6 and §7 body still names spaces; Round two is the newer text and is the seam. *Fix:* task 6 becomes "write `tests/test_space_partition.py`", its acceptance the green run plus the two scratch-negative checks; `Owns`, Provided §6, Design, Risks, and Findings 2 say "replaces `tests/test_grants.py`, which the tree plan retires (seams Round two)"; the header's seams line records Round two. Three critics converged on this.

   *Author, 2026-09-20:* changed. Header, What exists, Provided §6, the RLS paragraph, task 6, Risks, the reconcile note, and Findings 2 now say the tree plan retires the file and this test replaces it; task 6 writes the test and touches nothing else; the seams line reads "2, with Round two".
2. **`poll_connectors` calls `read_recent` with the wrong arity and on the wrong kind of connection.** Line 127 has it call `read_recent(connector, since)`; seams §3.10 fixes `read_recent(conn, connector, since)`, and integration passes `broker.read_recent` unwrapped, so the call raises `TypeError`. Deeper: the seams §3 preamble makes `conn` a connection inside a transaction the caller opened, while the broker records `read_recent` as an intent and outcome pair (broker plan line 148) under `perform`'s protocol, which needs the intent committed before the network call on a connection with no open transaction (broker plan line 84). One `conn` cannot serve both `read_recent` and `ingest` unless the plan says who opens what. Task 9's stub reader commits nothing, so the stub test cannot catch it, and the live test skips without the Keychain secret. *Fix:* Design states that `poll_connectors` takes an idle connection (`conn.info.transaction_status == IDLE`, refused otherwise), calls `read_recent(conn, connector, since)` and lets it manage its own transactions, then opens `async with conn.transaction():` around `ingest(conn, spaces, items)` per connector; the stub test gains a reader that calls `conn.commit()` mid-poll and asserts the poll's rows and events still land together. Two critics converged on this.

   *Author, 2026-09-20:* changed. Design: `poll_connectors` takes an autocommit connection, refuses one inside a transaction with `RuntimeError` before any read, calls `read_recent(conn, connector, since)` and lets it commit its own ledger rows, and wraps each connector's `ingest` in `conn.transaction()`. Task 9's stub reader asserts the idle state, writes and commits mid-poll, and the test checks rows and events land together; a second test checks the refusal.
3. **`since` trusts sender-controlled time.** Line 127 sets `since` to the greatest `received_at` recorded for the `(connector, account)`. The broker plan never says whether `received_at` comes from Gmail's internal date or the message's `Date` header; if it is the header, one future-dated mail moves `since` past now and the poll misses every later item on that account for good. *Fix:* `since = min(max(received_at), now()) - overlap` with `overlap` a small constant, or derive `since` from the row's `at` column; `test_since_is_the_last_received_at` gains a case with a future `received_at` asserting `since <= now()`.

   *Author, 2026-09-20:* changed. `since = min(max(received_at), now()) - timedelta(hours=1)`, or `now() - lookback_days` with no rows; task 9's test has the three cases, including the future-dated one.
4. **The `Space.id` validator constrains the reserved word and nothing else.** Line 86 refuses `unassigned` only; `schemas/space.py` line 62 leaves `id: str` open. Seams §1.2 reads scope as `<space_id>/<path>` with `space_of` the first segment, §0 keys the standing lock as `standing:<space>:<day>`, and this plan's rule text uses `:` separators. An id holding `/` or `:` breaks `covers`, `space_of`, and the lock namespace, and the property on line 186 is falsified by ids `a` and `a/b`. *Fix:* the same validator enforces `^[a-z0-9][a-z0-9-]*$` (Decided here, with the reason), `tests/test_space.py::test_space_id_charset_is_restricted` joins task 1, and the manifest generator for the properties draws ids from that pattern. Two critics converged on this.

   *Author, 2026-09-20:* changed. The validator enforces `^[a-z0-9][a-z0-9-]*$` with the reason in Design; `test_space_id_charset_is_restricted` joins task 1; the Properties preamble says the manifest generator draws ids from the pattern.
5. **Empty and M0-unevaluable rules are undefined.** Line 119 says a rule matches when every field it sets matches. `RoutingRule` with every field `None` is valid (`schemas/space.py` lines 19 to 25), so an empty rule claims every item on its account, and a rule setting only `label`, `folder`, or `calendar` matches nothing at M0 and sends the whole account to `unassigned` without a word; the broker composes `from:@<sender_domain>` from the rule, so an empty rule also breaks the read. *Fix:* `load_all` gains a fourth refusal, a gmail connector whose rule sets no `sender_domain` at M0, with `test_rule_without_sender_domain_refused` in task 3, and the Design says so beside the three refusals seams §3.4 names.

   *Author, 2026-09-20:* changed. `load_all` refuses a gmail connector whose rule sets no `sender_domain`, with the reason beside the other three; `test_rule_without_sender_domain_refused` joins task 3.
6. **The conformance test enforces less than seams §6 says it enforces.** Line 158 conditions the RLS check on `context_ro` holding a privilege. Seams §6 states two rules and says the test "enforces both": every table with a `space_id` carries row-level security, and `context_ro` is granted only on tables with `space_id` and the policy. Under the plan's predicate a table with `space_id`, RLS off, and no `context_ro` grant passes; `read_tokens` is that table today (migration 0001, lines 96 to 103). The seams win. *Fix:* add the predicate "every table in `public` with a `space_id` column has `relrowsecurity` true", and either enable RLS on `read_tokens` in 0004 with the `kernel_rw` policy (the owner bypasses RLS, so `cori_current_space()` under `migrator` still resolves) or record `read_tokens` in Design as the one exemption with the reason. My own view is that `read_tokens` is a key table rather than a partitioned one and the exemption is the better reading; noted, since the seams' sentence is unconditional.

   *Author, 2026-09-20:* changed, the seams' way. The test gains `test_every_space_table_has_rls` (unconditional on `space_id`), and migration 0004 enables RLS on `read_tokens` with the `kernel_rw` policy and no `context_ro` grant; `cori_current_space()` is `SECURITY DEFINER` under the owning role, so it still resolves, and `tests/test_rls.py` stays in task 4's acceptance to prove it.
7. **Task 6's `request_bodies` scratch check cannot run when task 6 runs.** Line 173 asks for a scratch `GRANT SELECT ON request_bodies TO context_ro` "once that table exists"; `request_bodies` arrives with the gateway's migration 0005, after this plan's 0004, and no later plan is told to come back. *Fix:* replace it with a scratch table that has no `space_id` column, granted to `context_ro`, discarded after; the predicate is the same and it runs at build time.

   *Author, 2026-09-20:* changed. Task 6's negatives are three scratch tables applied under `migrator` in a rolled-back test transaction, one per predicate, kept in the file as `test_negatives_are_caught`; the `request_bodies` grant is gone.
8. **Task 1's acceptance depends on a commit no plan owns.** Line 168 asserts the architect's budget and audience values from `infra/spaces/psyoptimal.yaml`; seams §1.1 has the lead write them in a design-edit commit, and the manifest's history ends at b7641ae with `standing_budget: null`. Risks (line 208) names this and leaves the builder to notice. *Fix:* task 1 opens with `grep -q 'tokens_per_day: 2000000' infra/spaces/psyoptimal.yaml` and stops for the lead when it fails; the builder never edits the manifest.

   *Author, 2026-09-20:* changed. Task 1 opens with the two greps (budget and `audience:`) and stops for the lead when either fails; the greps are in the acceptance.

9. **A lock key for a writer that does not exist.** Line 90 names `hashtextextended("inbound:<connector>:<account>", 0)` "should one arrive" and cites seams §0, whose namespace list (`thread`, `objective`, `standing`, `approval`, `effect`) has no `inbound`. The plan rules forbid a hook for a trigger that has not fired. *Fix:* delete the sentence; `record_inbound` rests on the unique constraint, and the plan that adds a second writer picks the key and amends §0 then.

   *Author, 2026-09-20:* changed. The sentence is deleted; `record_inbound` rests on the unique constraint and the plan names no lock.
10. **`candidates` is seams text, and its type is unstated.** Line 123 marks `candidates` as "Decided here"; seams §4 line 903 already lists `item, candidates` for `inbound.unassigned`. The surface's `route_inbound_card(item, candidates: list[SpaceId])` needs space ids. *Fix:* cite seams §4 and say `candidates` is the list of space ids from `matches`, empty on zero matches. Three critics converged on this.

   *Author, 2026-09-20:* changed. The `record_inbound` paragraph cites seams §4 and types `candidates` as `list[SpaceId]`, the ids `matches` returned in space id order, empty on zero matches.
11. **Task 9's live assertion is wrong on a second run.** Line 176 asserts every item from `broker.gmail.recent("psyoptimal.com")` "landed in `psyoptimal`" and that a second ingest writes zero rows; on any run after the first, the first ingest also writes zero rows, so "landed" has nothing to inspect. `recent()` also returns only `id`, `From`, `Subject`, `Date` (`broker/gmail.py` lines 145 to 150), so the test builds `headers` and `received_at` itself. *Fix:* assert on `route(item, spaces)` for every built item and on the rows selected by `external_id`, and say the test lowercases the keys and parses `Date` with `email.utils.parsedate_to_datetime`.

   *Author, 2026-09-20:* changed. The live test asserts `route(item, spaces)[0]` per built item and the `space_id` of rows selected by `external_id`, lowercases keys, parses `Date` as named, and still asserts the second ingest writes zero rows.
12. **"A timer calls `poll_connectors`"** (line 164) names a component no plan builds at M0; the integration plan's task 3 calls `poll_connectors` once by hand. *Fix:* say so, and leave the timer to integration when it arrives.

   *Author, 2026-09-20:* changed. Control flow and the `poll_connectors` paragraph say integration's task 3 calls it once by hand and no timer exists at M0.
13. **`StandingBudget.usd_micros_per_day` is exposed only here.** Line 86 adds the property; the supervisor plan (lines 135 and 327) converts `usd_per_day` on its own without knowing it exists. Seams §1.1 allows methods on a schema, so no amendment is needed. *Fix:* list the property under Seams Provided §1.1 so the supervisor's builder finds one conversion rather than writing a second.

   *Author, 2026-09-20:* changed. Seams Provided §1.1 lists `StandingBudget.usd_micros_per_day` as the one conversion, named for the tree and the supervisor.

Verdict: return to author. Items 1 and 2 have task 6 and `poll_connectors` contradict the seams as they stand today, and items 3 to 9 are defects in "Decided here" text a builder would otherwise carry into `kernel/spaces.py`.
