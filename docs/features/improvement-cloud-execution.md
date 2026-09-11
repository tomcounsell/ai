# Improvement Cloud Execution

Charter section 2 expects RSI sessions to run mostly in cloud sandboxes, around the clock, on affordable
resources. The provider decision, the authentication verdict, the budget meter, and the progress report
behind that expectation exist. The sandbox itself is decided and unbuilt.

Tracking issue: [#3274](https://github.com/tomcounsell/ai/issues/3274).
Infra record: [`docs/infra/improvement-cloud-execution.md`](../infra/improvement-cloud-execution.md), which
carries the provider arithmetic, the auth verdict, and the export destination contract.
Budget meter: [Improvement Controller](improvement-controller.md#unit-3-metering-and-teardown).

## What exists today

`tools/infrastructure_budget.py` meters unit 3 (USD 50 per week for infrastructure) with an admission gate
and a teardown policy. `tools/improvement_operating_report.py` generates the five charter section 2 answers
from records. `spend_receipt` and `resource_probe` are first-class evidence kinds. The artifact retention
root resolves to `~/.popoto/improvement_content`, outside any checkout. The infra record holds the
Cloudflare Containers decision with its arithmetic under the 72-hour duty cycle, the mode-B auth verdict,
and the export destination contract lane 3 writes against. No provider account has been opened, no
credential issued, no dollar spent, and no sandbox provisioned.

## The decided topology (unbuilt)

The trial design called for one worker-only sandbox. It would have run `python -m worker` and the official
`claude` CLI, claimed one research session from the shared queue, and written its evidence where the
dashboard reads it. It would not have run the Telegram bridge, held no `projects.json` roster entry, and
stayed outside `/update` entirely. A worker-only host takes work off the queue and never resolves an
inbound bridge-contact identifier, so single-machine ownership had nothing to complain about.

Authentication decided whether the trial ran at all. Task 4 returned mode B: vendor documentation never
affirmed Cloudflare Containers as a remote host operated by us rather than a hosted service consuming the
subscription, so ambiguity resolved against running. Tasks 9 (the trial) and 10 (the concurrency revisit)
did not run, phase 3 spent nothing, and the mode-B verdict became the fifth answer of the section 2
progress report. No new consumer of the subscription OAuth credential was introduced. Under mode B even
the deploy-time handoff of the existing credential never happened. API-key auth stays a separate issue
with its own doctrine argument, never a fallback reached for mid-build.

Durable state was decided as sandbox-local Redis with an export path. The shared-Redis option needed
transport security that does not exist (no TLS, password, or `rediss://` handling in settings, and the
production Redis is machine-global), so the sandbox would have kept its own Redis and exported evidence
and artifacts to the durable store, with every teardown gated on a verified export that fails closed. The
cross-process round trip in `tests/unit/test_artifact_retention_root.py` is the phase-2 proof that the
retention root survives outside the checkout.

## Host updates

Code reaches a sandbox host by image rebuild and redeploy, never by `/update`, `remote-update.sh`, or
launchd. The sandbox is absent from the `projects.json` machine roster. Each redeploy mounts persistent
storage at the resolved retention root, or the redeploy destroys the evidence the trial gathered. The
retention root default is `~/.popoto/improvement_content`, beside the shared popoto content directory and
never inside it, with verification on every load and the archive path under `.versions/`.

## Evidence path

Sandbox evidence would have reached the dashboard through the export path: trial session records named by
the `lane7-sandbox-trial-` source-ref prefix, settlements as `spend_receipt` rows, and artifacts under the
retention root with verification on every load. The operating report reads the same records, so the
dashboard and the report agree by construction. With no trial run, the sessions answer reads "none", which
is the primary case the report was built for rather than an edge case.

## See also

- [Improvement Controller](improvement-controller.md): unit-3 metering, window computation, teardown policy
- [`docs/infra/improvement-cloud-execution.md`](../infra/improvement-cloud-execution.md): provider arithmetic, auth verdict, destination contract, rollback
