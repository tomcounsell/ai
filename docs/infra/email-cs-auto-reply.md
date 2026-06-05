# Infra: Email CS Auto-Reply Layer

Operational reference for the Cuttlefish email customer-service triage layer
(`tools/email_cs/`). For the design, see
[features/email-cs-auto-reply.md](../features/email-cs-auto-reply.md).

## External dependencies

| Dependency | Role | Failure mode |
|------------|------|--------------|
| **Ollama** (`gemma4:e2b`) | Tier 1 local classification. Already the repo's `OLLAMA_LOCAL_MODEL`. | Down/slow → triage fails-safe to **escalate** (logged, never crashes). |
| **Anthropic API** (`MODEL_FAST` = Haiku) | Tier 2 per-category action agent. | API error → **draft_for_human** (never auto). |
| **Cuttlefish `manage.py`** | Subprocess capability surface (`customer show/checkout-url/note/email draft`, `episode provision`). Runs in `~/src/cuttlefish/.venv`. | Timeout / non-zero exit / bad JSON → **escalate** + audit note recording the failure. |
| **Redis** | `email:outbox:{session_id}` (auto replies), `telegram:outbox:{session_id}` (pings). | Write failure logged; ping/reply best-effort. |

No new Python packages — `ollama` and `anthropic` are already in use. No new CLI
entry point: the layer is bridge-internal (imported directly by
`bridge/email_bridge.py`).

## Single-machine ownership

Cuttlefish is owned by **one** machine (per `projects.<key>.machine` in
`projects.json`). The triage layer runs only where the cuttlefish email block is
owned — consistent with the single-machine-ownership invariant
([single-machine-ownership.md](../features/single-machine-ownership.md)). No
multi-machine coordination.

## Cost & rate notes

- **Tier 1** is local (Ollama) — no API cost, bounded by local GPU/CPU.
- **Tier 2** runs only on `auto`-gated lanes (Phase ≥ 2), once per qualifying
  email, on Haiku (~$0.0001/call). Shadow mode (Phase 1) makes **zero** Tier 2
  calls — it stops after the gate and writes one audit note.
- Subprocess timeout: `DEFAULT_TIMEOUT_SECONDS = 20.0` per `manage.py` call,
  bounding worst-case latency on a hung command.

## Rollout phases (operational)

Phase flips are **manual, human-gated** decisions made after reviewing
shadow-mode verdict logs:

1. **Phase 1 — shadow** (`shadow_mode=true`, the deploy default): classify +
   audit note, send nothing. Review the `customer note` `[shadow]` entries to
   calibrate the 0.75 confidence threshold and the escalation-signal set.
2. **Phase 2 — read-only auto** (`shadow_mode=false`, `auto_mutations=false`):
   enable auto-replies for zero-mutation lookups.
3. **Phase 3 — mutating auto** (`auto_mutations=true`): enable mutating
   handlers once shadow data proves triage trustworthy. Blocked on the two
   companion cuttlefish commands (`episode regenerate`, `customer cancel
   --at-period-end`) that live in `yudame/cuttlefish`.

## Rollback

Two independent kill switches, both in private `~/Desktop/Valor/projects.json`:

1. **Flip `shadow_mode` back to `true`** — the layer classifies and audits but
   sends nothing to customers; the existing `AgentSession` path remains.
2. **Remove the `email.customer_service` block** (or the `customer_resolver`) —
   the layer goes fully inert (`handle_customer_email` returns `None`) and the
   bridge reverts to today's behavior (always spawn an `AgentSession`).

No code change, deploy, or schema migration is needed to roll back. There are no
schema changes in this repo (audit records live cuttlefish-side).

## Deploy / update

No `scripts/remote-update.sh` change required. `gemma4:e2b` is pulled by the
existing `/update` Ollama-sync steps. The `projects.json` wiring is private
config that syncs via iCloud, not via the update script.

## Monitoring

- Verdicts and dispositions log under the `[email_cs.*]` logger prefix
  (`triage`, `gate`, `agents`, `cuttlefish`, `handler`).
- Shadow-mode audit notes carry a `[shadow]` prefix in the `customer note` body
  for easy filtering on the cuttlefish side.
- A `manage.py` failure logs at WARNING (handler) and is recorded in the audit
  note; an escalate-after-successful-command (reply failed) logs at ERROR and
  pings the human.
