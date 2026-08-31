# Enterprise Valor — alternative architecture choices

> **Read [`enterprise-valor-core.md`](enterprise-valor-core.md) first.** That note settles
> the nouns and the subsystems. This one is the technology appendix — it is deliberately
> more detail than the direction-orienting phase needs, and choosing from it before the
> core is agreed is premature.

Companion note to the *Enterprise Valor: Policy-Governed Autonomous Software Delivery*
proposal. The proposal argues *what* to build. This note argues *what to build it out
of*, on the premise that Enterprise Valor is a **new repository inspired by this one**,
not a refactor of it.

The framing question throughout: **which of this repo's technology choices are load-bearing
for a personal autonomous engineer, and which become disqualifying once the system's job is
to prove to someone else's security team that the agent could not have done what it was not
authorized to do?**

---

## 1. What the baseline actually is

Measured, not remembered:

| Plane | This repo today |
|---|---|
| Language | Python 3.11+ everywhere — ~137k lines app code, ~294k lines tests |
| Datastore | Redis, sole store, via the Popoto ORM. Sessions, memory, events, ledger, dedup, gates |
| Session record | `models/agent_session.py` — one mutable record, 60+ fields, `session_events` as an in-record `ListField` |
| Execution | `python -m worker`, one `claude -p` subprocess per turn, asyncio drain loop, per-project serialization |
| Isolation | git worktrees (`.worktrees/{slug}/`, branch `session/{slug}`) |
| Enforcement | Claude Code hooks — 23 `PreToolUse` validator scripts, registered from `.claude/hooks/manifest.toml` |
| Durability | Hand-rolled: startup lock cleanup, watchdog service, Redis crash tracker with git-commit correlation, 4-level escalation, wedge detector |
| Deployment | macOS launchd plists, one machine, `projects.<key>.machine` single-machine ownership |
| Secrets | `~/Desktop/Valor/.env` + 1Password `op`, injected into the agent's own process environment |

This is a well-built single-operator system. Every choice above is defensible *for that*.
Four of them stop being defensible the moment the system has to satisfy §12 of the proposal.

### 1.1 The load-bearing observation

The proposal's §4.3 — *the worker cannot expand its blast radius* — is the property this
repo does **not** have and cannot be patched into having.

Today's enforcement is `.claude/hooks/validators/*`: `validate_no_raw_redis_delete.py`,
`validate_no_broad_process_kill.py`, `validate_no_destructive_git_in_worktree.py`, and
twenty more. These are good rules. They are also:

- **in-process** — the agent's own harness invokes them;
- **tool-mediated** — they inspect the *arguments to a Bash tool call*, so `python -c` or a
  shell script written and then executed sidesteps every one of them;
- **fail-open by design in places** — the manifest's `exit_policy = "suppress"` and
  `"deny-only"` modes exist precisely because a crashing validator must not wedge the agent.

That is the correct trade for a system whose threat model is "Valor makes a mistake." It is
the wrong trade for a system whose threat model (§12) includes "repository content attempts
to manipulate the worker" and "the agent attempts unauthorized operations."

**The single most important structural change in the new repo is an inversion:** today the
agent process is the privileged process and rules run inside it. In Enterprise Valor the
agent process must be the *least* privileged process in the system, and every privileged
operation — credential fetch, network egress, git push, evidence submission — must be an RPC
to a broker on the other side of a boundary the agent cannot cross.

Everything below follows from that inversion.

---

## 2. Language: Go for the control plane, Python for the execution plane, Rust for one component

### The case for Rust

Rust is genuinely attractive for the authorization kernel. The kernel is small (policy
evaluation, envelope issuance, attestation signing, ledger append — call it 10–15k lines),
long-lived, security-critical, and needs to be deterministic. Rust gives you a single static
binary with a reproducible build and a signed SBOM, which is itself a selling point: "the
component that decides what the agent may do is a 12 MB binary you can rebuild bit-for-bit"
is a sentence that ends arguments with security reviewers. Memory safety, no GC pauses in
the signing path, and `cedar-policy` / `sigstore-rs` / `rustls` are all first-class.

### The case against Rust for *all* of it

The entire ecosystem this system must integrate with is Go-native: OPA, Cedar's server-side
tooling, SPIFFE/SPIRE, sigstore (cosign, Fulcio, Rekor), in-toto, Trillian, Kubernetes,
Kata Containers, Temporal. Choosing Rust for the control plane means writing FFI bindings or
shelling out to Go binaries for half the supply-chain stack, and doing it during the phase
where the domain is still being discovered. That is the wrong place to spend the budget.

### Recommendation

| Plane | Language | Why |
|---|---|---|
| Control plane — policy service, envelope issuer, authorization service, ledger, brokers | **Go** | Ecosystem gravity is decisive. Every integration target is Go. Static binaries, good gRPC, boring concurrency |
| Execution plane — agent harness, SDLC skills, evidence collectors | **Python** | This is where the Claude Agent SDK, the model tooling, and this repo's transplantable logic already live. It runs *inside* the sandbox and holds no privilege, so its blast radius is bounded by construction |
| Sandbox supervisor / seccomp broker, *if hand-rolled* | **Rust** | Hot path, hard security boundary, no GC, and `firecracker`/`cloud-hypervisor` integration is Rust-native. Skip entirely if you adopt Kata Containers |

The important property is not the language. It is that **the boundary between control plane
and execution plane is a process and network boundary, not a Python import.** This repo's
`agent/` package imports `models/` imports Redis — one address space, one trust level. That
must not be reproduced.

---

## 3. Storage: Postgres as the system of record, and no Redis in Phase 1

Redis-as-sole-store is the choice that most directly blocks the proposal, for four concrete
reasons.

1. **No cross-record transactions.** "Issue the work envelope AND append the audit event"
   must be atomic. In Redis it is two writes and a prayer; Popoto v1.8.0's Lua-based index
   maintenance makes *one model's* save atomic, not a multi-entity invariant.
2. **Durability you would not sign.** AOF `everysec` loses up to a second of writes on power
   loss. An authorization ledger cannot lose a second.
3. **Mutable records defeat the audit model.** §11 requires history reconstructable
   *without* relying on the agent's own state. Today the history is `session_events`, a
   `ListField` inside the same mutable record the worker rewrites every turn.
4. **Policy simulation is a relational query.** §8's "if this policy had been active for six
   months, what would have changed?" is a join across historical evidence, decisions, and
   artifacts. That is Postgres's home turf and Redis's worst case.

### Recommended shape

**Append-only, hash-chained event log in Postgres.**

```sql
CREATE TABLE ledger (
    seq         BIGSERIAL PRIMARY KEY,
    org_id      UUID NOT NULL,
    work_id     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    payload     JSONB NOT NULL,          -- RFC 8785 canonical form
    prev_hash   BYTEA NOT NULL,
    hash        BYTEA NOT NULL,          -- H(prev_hash || canonical(payload))
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- tamper-evidence in-DB
CREATE TRIGGER ledger_immutable BEFORE UPDATE OR DELETE ON ledger
    FOR EACH ROW EXECUTE FUNCTION raise_immutable();
```

Then make it tamper-*resistant*, not just tamper-*evident*, with role separation Postgres
gives you for free:

- role `ledger_writer` — `INSERT`, `SELECT`. No `UPDATE`, no `DELETE`, not the table owner.
- role `governance` — owns policy tables. The execution plane has **zero grants** on them.
- role `execution` — reads its own work item, writes nothing outside it.
- Row-level security keyed on `org_id`, enabled from day one (see §9 on tenancy).

Everything else derives from the log. Session state, dashboards, "what is work item PAY-1842
doing" — all **projections**, rebuildable by replaying the ledger. That is what makes replay
and simulation honest rather than aspirational: if the projection can be rebuilt, the log is
demonstrably sufficient.

**Evidence blobs go to object storage with Object Lock** (S3 compliance mode, or MinIO
self-hosted), content-addressed by digest, with only the digest in Postgres. Object Lock is
WORM enforced below your application, and it is a control auditors already recognize.

**Drop Redis for Phase 1.** At the proposal's volumes — tens of work items per day — a
Postgres queue with `SELECT ... FOR UPDATE SKIP LOCKED` plus a `leased_until` column is
about 200 lines and removes an entire failure domain, an entire operational surface, and the
temptation to put truth back in it. Reintroduce Redis later only as an explicitly ephemeral
cache/rate-limiter, with a lint that forbids it appearing in any authorization path.

### On the ORM

Popoto is well-suited to what it does here and irrelevant to the new repo. In Go the natural
choice is **sqlc** (compile SQL to typed Go; no runtime reflection, no query surprises) or
**pgx** directly, with **golang-migrate** or **Atlas** for migrations. Avoid a heavyweight
ORM: the schema *is* the contract, and hiding it behind an ORM is precisely the wrong
instinct for a system whose value proposition is auditability.

---

## 4. Policy engine: structured documents with CEL leaves, distributed as signed OCI bundles

The proposal's example policies (`sast.critical == 0`) are a DSL wearing a YAML costume.
Left alone it grows conditionals, then quantifiers, then helper functions, and you will have
written a worse Rego by Phase 3. Decide the expression language now.

| Option | For | Against |
|---|---|---|
| **OPA / Rego** | Mature. Signed bundles (`opa build --signing-key`). `opa test` + coverage. Decision logs. Partial evaluation. Explanation built in | Rego is genuinely hard for non-engineers to read. §6 requires security, legal, and privacy to *review* policy — if counsel cannot read it, the governance model is theater |
| **Cedar** | Formal semantics with machine-checked proofs. Schema validation. Very readable. Rust core, Go bindings | Shaped around principal/action/resource. Evidence-threshold evaluation is an awkward fit. Thinner bundling/distribution story |
| **CEL** | Tiny, embeddable, fast, non-Turing-complete so termination is guaranteed. Kubernetes chose it for `ValidatingAdmissionPolicy` for exactly these reasons. Readable by anyone who has seen a spreadsheet formula | Only an expression language — you supply the document structure, scoping, and composition yourself |
| **Custom DSL** | Perfectly shaped to the domain | You will spend two years rediscovering why CEL has the semantics it has |

**Recommendation: keep the proposal's document structure, replace the string comparisons with
CEL.** You retain the reviewable YAML shape (`id`, `version`, `owner`, `scope`,
`requirements`, `human_required_when`) that makes cross-functional review possible, and you
get a deterministic, testable, terminating expression language in the leaves instead of a
hand-rolled `==` parser.

```yaml
id: SEC-042
version: 7
owner: security-eng
scope:
  environments: [production]
requirements:
  - id: no-critical-sast
    expr: "evidence.sast.critical == 0 && evidence.sast.high == 0"
    explain: "SAST reports no critical or high findings"
  - id: no-known-critical-cves
    expr: "evidence.deps.critical_cve.filter(c, !c.vex_not_affected).size() == 0"
human_required_when:
  - expr: "evidence.diff.auth_boundary_changed"
    explain: "change touches an authentication or authorization boundary"
```

Two non-negotiables regardless of engine:

- **Decisions must be explanatory, not boolean.** The evaluator returns a per-requirement
  trace with the inputs it read. §11's "every decision can be reconstructed" is impossible
  otherwise, and a `DENY` a human cannot understand becomes a rubber-stamped exception.
- **Bundles are signed, versioned artifacts.** Publish policy bundles as **OCI artifacts** in
  a registry, signed with **cosign**. Then `policy_bundle_sha256` in the attestation is a
  real registry digest that the deployment-side verifier can independently fetch and check —
  reusing container-supply-chain infrastructure instead of inventing a distribution channel.

Revisit Rego at Phase 3 if policy *composition* (overrides, precedence, org→service→env
layering) genuinely outgrows what a document structure can express. Do not start there.

---

## 5. Sandbox: a VM boundary, not a hook

This is where the proposal's exit criteria live, and where the new repo diverges most sharply
from this one.

| Option | Boundary | Cost | Verdict |
|---|---|---|---|
| git worktree + harness hooks (today) | none — same user, same kernel, same process tree | ~0 | Insufficient. Bypassable by any subprocess |
| OCI container (containerd) | namespaces + cgroups, shared kernel | low | Adequate for "incorrect agent", not for "adversarial content in the repo" |
| **gVisor (runsc)** | user-space kernel, large syscall-surface reduction | ~2× syscall overhead | Good cost/benefit, drop-in OCI runtime |
| **Firecracker / Cloud Hypervisor microVM** | hardware virtualization | ~125 ms boot, ~5 MiB overhead | The right answer. What every code-execution-as-a-service runs on |
| **Kata Containers** (Firecracker or CH under the hood) | hardware virtualization, via a Kubernetes `RuntimeClass` | K8s dependency | **Recommended** — VM isolation without hand-building the orchestration |

Three enforcement details matter more than the runtime choice:

**Filesystem.** Do not implement `writable_paths` as a path check the agent could be talked
out of. Mount *only* the writable subtree into the guest; `forbidden_paths` becomes "not
present in the VM." Then derive the diff from the overlay upper layer rather than trusting
`git diff` run by the agent. The envelope becomes an infrastructure fact instead of a rule.

**Network.** Allowlisting `github.com` and `pypi.org` by DNS or IP is weaker than the
proposal implies — both serve arbitrary attacker-supplied content. Route all egress through a
**mandatory TLS-terminating proxy** (Envoy or mitmproxy) with the CA injected into the guest
and everything else dropped at the tap device, so the allowlist can be per-host *and*
per-path (`github.com/acme/payments-api/*`, not `github.com`). Every allowed and denied
destination becomes an audit event, satisfying §11's `capability granted` / `capability
denied` records. *This session is itself running behind exactly this pattern, which is decent
evidence the shape is workable.*

**Credentials.** Never inject a long-lived secret into the sandbox — that is what this repo
does today with `.env`, correctly for its context and fatally for this one. Instead:
**SPIFFE/SPIRE** issues the sandbox a short-lived SVID; a credential broker *outside* the VM
exchanges it for a scoped token only if the envelope permits that credential; the token
expires with the work item. "Valor cannot access an unapproved credential" then holds because
the broker enforces it, and every exchange is a ledger event.

**Model access is a credential too.** §9's `max_model_usd` cannot be enforced by an agent
counting its own tokens. Put an **LLM gateway** between the sandbox and the model API that
holds the API key, enforces the per-envelope budget, and logs every call as evidence. Cheap
to build, removes the model key from the sandbox entirely, and gives you prompt/response
retention for audit as a side effect.

---

## 6. Durable execution: Postgres state machine now, Temporal later

This repo's self-healing layer — watchdog service, Redis crash tracker with git-commit
correlation, 4-level escalation, update-loop-wedge detector with positive-evidence verdicts —
is a hand-rolled workflow engine's recovery half. It is impressively built and it exists
because there was no coordination layer to lean on.

The SDLC pipeline is, structurally, a long-running workflow with human-in-the-loop signals,
timers, retries, and compensations. **Temporal** (or Restate, or DBOS) is built for exactly
that, and would delete the watchdog, the crash tracker, the wedge detector, and most of the
escalation ladder. Its history is also independently queryable, which is a second audit
source for free.

The counter is that Temporal is operationally heavy (server + persistence + workers), imposes
determinism constraints on workflow code, and adds a vendor at the precise moment you are
trying to get one vertical slice working.

**Recommendation:** Phase 1 runs an explicit Postgres-backed state machine — states as rows,
transitions as an append to the ledger, `SKIP LOCKED` for pickup, `leased_until` for
liveness. Roughly 800 lines, zero new infrastructure. **Write the transition table as data,
not as code**, so the Phase 2/3 migration to Temporal is mechanical rather than a rewrite.
Adopt Temporal when concurrent work items and multi-day human escalations make hand-rolled
recovery expensive again — which is the same pressure that produced this repo's watchdog.

---

## 7. Evidence and attestation: adopt formats, do not invent them

Every primitive in proposal §5 already has a standard. Using them is worth more than the
engineering time saved, because each one is a format an enterprise security team already
recognizes and has tooling for.

| Proposal primitive | Adopt |
|---|---|
| Attestation | **in-toto Attestation Framework (ITE-6)** in a **DSSE** envelope. `subject` = artifact digest, so §10's "changing the artifact invalidates the authorization" is structural |
| Build provenance | **SLSA provenance** predicate |
| Work Envelope, Decision, Evidence | Custom predicate types inside the same in-toto envelope: `valor.dev/WorkEnvelope/v1`, `valor.dev/PolicyDecision/v1` |
| Signing | **Sigstore / cosign**, keyless via **Fulcio** with certs bound to the SPIFFE workload identity — no long-lived signing key for anything in the execution plane to steal |
| Audit tamper-resistance | **Rekor** transparency log (private instance). An append-only Merkle log with inclusion proofs is a stronger answer to §12's "audit tampering" than any database you would write. Complements the hash chain; does not replace it |
| Static analysis evidence | **SARIF** — every scanner emits it, so you write one parser instead of N |
| Dependency evidence | **CycloneDX** SBOM + **VEX**. VEX matters specifically because `dependencies.critical_cve == 0` needs a way to record "not exploitable here" that is not an unbounded exception process |
| Execution traces | **OpenTelemetry**. The trace is evidence too |

### The landmine: canonicalization

If you hash evidence, envelopes, and policy bundles across a Go control plane and a Python
execution plane, you need one canonical serialization or your digests will silently diverge.
Use **JCS (RFC 8785)** for JSON and treat "compute the same digest from both languages for
1,000 fuzzed documents" as a Phase 1 acceptance test. Protobuf's deterministic marshaling has
enough caveats that it is not a safe substitute here.

### The weak joint: evidence provenance

§16 flags this and it deserves promoting to a Phase 1 decision, not a Phase 1 question.
**Evidence produced inside the sandbox is agent-influenced.** A test suite the agent could
edit is not evidence that the tests pass. The only sound resolution is that any evidence
which *gates authorization* is produced by a **separate execution, in a fresh sandbox the
agent never touched, from the committed source, orchestrated by the control plane.** That
costs a second full run per work item. Budget for it in Phase 1 rather than discovering it in
Phase 3, because retrofitting it invalidates every attestation issued before the change.

Corollary: the authorized subject for a code change is a **git commit digest plus a build
output digest**, not "a PR". A PR is a mutable view; the deploy-time verifier must check both.

---

## 8. Interface contracts: protobuf between services, JSON Schema for humans

The envelope, evidence, decision, and attestation schemas *are* the product. They need
versioning discipline and cross-language codegen (Go + Python at minimum).

- **Protobuf + buf** for inter-service contracts. buf gives a schema registry and
  breaking-change detection in CI, which is exactly the governance the schemas warrant.
- **JSON Schema** for human-authored policy documents, because reviewers edit YAML and need
  editor completion and validation errors in the language they are writing.
- Generate one from the other where they overlap; do not maintain two hand-written copies.

gRPC between planes, with a thin JSON/OpenAPI gateway for humans and CI systems.

---

## 9. Tenancy, identity, and deployment target

**Tenancy from the schema up.** This repo is explicitly single-tenant — `projects.<key>.machine`
exists as a coordination strategy precisely because there is no coordination layer. Enterprise
Valor must carry `org_id` on every row with RLS enabled *from the first migration*, separate
KMS keys per tenant, and per-tenant sandbox pools. Even with one design-partner customer.
Retrofitting tenancy is the classic expensive mistake and it is nearly free to avoid now.

**Identity, per §16's list.** SPIFFE gives you the namespace directly:

```
spiffe://valor.example/human/<oidc-subject>      # via the org IdP, for approvals
spiffe://valor.example/policy-service
spiffe://valor.example/orchestrator
spiffe://valor.example/worker/<work-id>          # one identity per work item, expires with it
spiffe://valor.example/authorizer                # separate trust domain from execution
```

Separation of duties on policy approval is then group membership at the IdP, checked by the
governance plane, and the agent's conversational identity never appears in an authorization
decision — which is §16's actual requirement.

**Deployment target: Linux, containers, Kubernetes.** Not launchd, not macOS. Kata, SPIRE,
cert-manager, and the whole sigstore stack assume it. Nomad is a legitimate lighter
alternative if K8s is disproportionate, at the cost of doing the Kata integration by hand.

---

## 10. Testing: adversarial tests are the product

This repo's testing philosophy — real integrations, no mocks, AI judges — is right for
validating agent behavior and should be carried over for the execution plane.

For the authorization kernel the analogue is different and more important:

- **Property-based testing** (Hypothesis / `proptest`) on the policy evaluator: no evidence
  set should ever produce `ALLOW` when a required control is absent; evaluation must be
  deterministic under input reordering; every `DENY` must carry a non-empty explanation.
- **A red-team harness as a first-class deliverable.** Re-read §13's exit criteria: *"Valor
  cannot write outside an allowed path"*, *"cannot access an unapproved network destination"*,
  *"cannot access an unapproved credential"*. Those are negative tests. Build a suite whose
  explicit job is to escape the envelope — write outside the mount, resolve a blocked host,
  exfiltrate through DNS, exhaust the budget, forge an attestation, replay a stale
  authorization — and treat every success as a P0.

That suite is the proof the whole proposal rests on, and it should be runnable by a
prospective customer's security team on their own hardware. Design it to be handed over.

---

## 11. What to transplant from this repo, and what to leave

**Transplant:**

- **The SDLC stage decomposition** and the single-stage-router discipline (`/sdlc` assesses,
  dispatches one sub-skill, returns). It is a good decomposition and it maps cleanly onto
  workflow states.
- **Skills as versioned artifacts**, and the generic-body / context-addendum split
  (`skills-global/` + `.claude/skill-context/`). In the new repo the context addendum
  becomes per-tenant, per-service configuration — same mechanism, better justified.
- **Lane identity** (`docs/features/sdlc-lane-identity.md`): one slug tying task list,
  branch, and worktree together. Generalizes directly to a work item tying envelope, sandbox,
  branch, evidence, and attestation.
- **The hook validator catalog itself.** Those 23 validators are empirically discovered
  policies — real rules that real failures taught this system. They are the seed corpus for
  the policy library. Re-home them from in-process hooks to out-of-process policy; the
  *content* is the asset, the *mechanism* is not.
- **The escalate-only-on-a-genuine-open-question discipline**, which is already this repo's
  version of `HUMAN_REQUIRED`, and the instinct behind the §15 metric.
- **Fail-closed-by-default reasoning**, exactly as argued in the `@optional` env-completeness
  rule: *"a forgotten marker costs one spurious warning, a wrong one silences a real secret
  forever."* That is the correct default-selection argument, and it generalizes to every
  policy default in the new system.

**Leave behind:**

- Redis as a system of record.
- launchd, macOS, and single-machine ownership as a coordination strategy.
- In-process advisory hooks as the *enforcement* mechanism (keep them as fast local feedback
  for developers; never as the authorization boundary).
- The single 60-field mutable session record — replaced by an event log plus projections.
- A 179-file `tools/` surface inside the trusted process. In the new architecture those tools
  run *in the sandbox*, unprivileged; anything privileged becomes a brokered RPC with an
  envelope check.

---

## 12. Recommended Phase 1 stack, in one place

| Concern | Choice |
|---|---|
| Control plane | Go |
| Execution plane | Python + Claude Agent SDK, unprivileged, inside the VM |
| System of record | Postgres — append-only hash-chained ledger, projections, RLS, role separation per plane |
| Blob/evidence store | S3-compatible with Object Lock, content-addressed |
| Queue | Postgres `SKIP LOCKED`. No Redis |
| Policy | Structured YAML documents, CEL expressions, JSON Schema validated |
| Policy distribution | OCI artifacts, cosign-signed, digest-pinned in attestations |
| Sandbox | Kata Containers on Firecracker, via a K8s `RuntimeClass` |
| Network | Mandatory TLS-terminating egress proxy, per-envelope host+path allowlist |
| Credentials | SPIFFE/SPIRE SVID → external broker → short-lived scoped tokens |
| Model access | LLM gateway holding the key, enforcing per-envelope budget, logging calls as evidence |
| Attestation | in-toto / DSSE, SLSA provenance, custom predicates |
| Signing | Sigstore keyless (Fulcio), Rekor transparency log |
| Evidence formats | SARIF, CycloneDX + VEX, OpenTelemetry |
| Contracts | Protobuf + buf between services; JSON Schema for human-authored policy |
| Workflow | Explicit Postgres state machine, transition table as data. Temporal at Phase 2/3 |

---

## 13. Four spikes that de-risk the choices, roughly a week each

Run these **before** committing to the Phase 1 build. Each one can invalidate a decision
above, and the third can invalidate the economics of the whole proposal.

1. **Escape harness.** Firecracker or Kata plus a minimal envelope. Write twenty escape
   attempts; count successes. Do this first — if the boundary does not hold, no other choice
   in this document matters.
2. **Canonicalization and hash-chain spike.** Go and Python producing byte-identical digests
   over 1,000 fuzzed evidence documents, plus ledger verification. Cheap, and getting it
   wrong late is very expensive.
3. **Historical policy replay against this repo.** Take six months of merged PRs from
   `tomcounsell/ai`, synthesize the evidence that would have existed, and run a candidate
   policy set against it. Measure the escalation rate. **The proposal's entire value depends
   on `HUMAN_REQUIRED` being rare**, and that number can be estimated *today*, from history
   that already exists, before a line of the kernel is written. If routine changes escalate
   40% of the time, the design needs rethinking, not building. This is the highest-value
   spike on the list.
4. **Egress proxy fidelity.** Can you actually express "github.com, but only this repo" and
   "pypi.org, but only these package names at these versions"? If not, the network capability
   is coarser than §9's table implies, and the policy schema needs to represent that honestly
   rather than promising a granularity the enforcement layer cannot deliver.

---

## 14. Two honest tensions in the proposal worth resolving early

**Policy simulation cannot work until there is attested history.** §8's replay requires
evidence you did not collect before the system existed. Spike 3 above is an approximation
using synthesized evidence, which is useful for sizing but is not the real thing. Simulation
should be promised for Phase 3, not Phase 1 — and the Phase 1 evidence schema should be
designed with replay in mind so the history accumulated during Phase 2 is actually replayable.

**"Deny by default" and "escalation is exceptional" pull in opposite directions.** §4.2 says
unknown conditions deny or escalate; §15 optimizes for fewer human decisions per change. Early
on, when policy coverage is thin, almost everything is an unknown condition, so the system
will escalate constantly and look like a failure by its own primary metric. Plan for that
explicitly: measure escalation rate as a *coverage* metric during Phases 1–2 and only as an
*efficiency* metric from Phase 3, and build the "recurring settled escalation → new policy"
conversion loop early, since it is the mechanism by which the system actually gets better.
