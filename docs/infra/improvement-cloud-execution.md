# Improvement cloud execution

Owner of record: capacity-spiker (tasks 3 and 4, #3274). Task 7 appended the
destination contract; task 11 completes this file. Infra docs are never
archived when plans ship: the next person to ask "why Cloudflare" or "why not
Cloudflare" finds the answer here rather than in an archived plan.

## Current State

No RSI session has ever run anywhere but a developer workstation. The worker
runs on macOS under launchd, reads `.env` through an iCloud symlink, and talks
to a Redis on `localhost:6379`. Every one of those three is machine-local.

The resource position behind charter section 2 is measured but thin. Lane 2b
(#3255) probed six resources and returned four `unknown`s, one `absent` for
`wrangler`, and one `absent` for the vault writer. Unit 3 (USD 50 per week for
infrastructure) has a meter and a teardown policy on this branch, and no
acquisition has passed through either. No provider account has been opened by
this lane, no credential has been issued, and no dollar has been spent. That
is deliberate: task 4 is forbidden from acquiring anything, and phase 3
(acquisition, trial, concurrency revisit) runs only under auth mode A with
lane 3's vault writer landed. The verdict below is mode B, so phase 3 does
not run. Tasks 9 and 10 do not run, and the fifth answer of the section 2
progress report carries the verdict instead. That is a first-class outcome,
not a consolation prize.

## New Requirements

### The duty cycle

Every forecast in this file is computed against one named duty cycle: 72
hours of continuous availability inside one ISO week, idle otherwise. The
containers sleep outside the window; no keepAlive persists past it. This is
task 9's default bound, pinned there precisely so the provider decision is not
blocked on a preference answer. Seventy-two hours crosses several
access-token refreshes (the failure mode in `anthropics/claude-code#50743`)
while staying well inside a USD 50 week. Forecast arithmetic that assumes any
other duty cycle is not comparable to the numbers below and must say so.

### Provider decision

Decision: Cloudflare Containers, trial instance type standard-1 (1/2 vCPU,
4 GiB RAM, 8 GB disk), admitted under the 72-hour duty cycle. The Sandbox SDK
is an implementation choice on the same platform, not a separate provider: the
Sandbox pricing page states its pricing "is determined by the underlying
Containers platform it's built on" and defers to the Containers pricing page,
which was re-read live on 2026-09-11 (page last updated Aug 28, 2026) because
spike-6's rates came partly from third-party calculators.

Live rates (vendor page, the authority): Workers Paid USD 5 per month
includes 25 GiB-hours of memory, 375 vCPU-minutes, and 200 GB-hours of disk
per month, then USD 0.0000025 per GiB-second, USD 0.000020 per vCPU-second,
and USD 0.00000007 per GB-second. Billing is per 10 ms of active running;
charges stop when the instance sleeps. CPU is billed on active usage only.
Egress in North America and Europe is USD 0.025 per GB with 1 TB included per
month. Containers are unavailable on the Free plan (the pricing table lists
Free as N/A for all three resources), so the USD 5 base is mandatory, not
optional. The monthly base is carried in weekly arithmetic as USD 1.15
(5 divided by 4.349 weeks per average month).

Constants used below: 72 hours equals 259,200 seconds. Monthly inclusions
equal 90,000 GiB-seconds, 22,500 vCPU-seconds, and 720,000 GB-seconds.

Candidate 1, Cloudflare Containers standard-1 (SELECTED). 72-hour math:
memory 4 GiB times 259,200 s equals 1,036,800 GiB-s; overage 946,800 times
USD 0.0000025 equals USD 2.37. CPU at the upper bound (100 percent active at
the 1/2 vCPU cap) 0.5 times 259,200 equals 129,600 vCPU-s; overage 107,100
times USD 0.000020 equals USD 2.14, with a lower bound near USD 0 when the
worker idles. Admission forecasts the max, USD 2.14. Disk 8 GB times
259,200 s equals 2,073,600 GB-s; overage 1,353,600 times USD 0.00000007
equals USD 0.09. Total worst case: 1.15 plus 2.37 plus 2.14 plus 0.09 equals
USD 5.75 per trial week, against USD 50. Verdict: forecastable under the
bounded duty cycle (the CPU range has a stated max, which is what makes it a
forecast rather than a guess) and admitted. Egress and the per-request
Workers and Durable Object companions forecast at USD 0 at single-session
scale; settlement confirms, and missing metering settles at the forecast per
the meter rule, never at zero.

Candidate 2, Cloudflare Containers basic (1/4 vCPU, 1 GiB RAM, 4 GB disk).
72-hour math: memory overage 169,200 GiB-s equals USD 0.42; CPU upper bound
overage 42,300 vCPU-s equals USD 0.85; disk overage 316,800 GB-s equals
USD 0.02; plus the 1.15 base equals USD 2.44 per week. Verdict: admitted on
price, REFUSED as the trial instance on fitness. Claude Code's published
system requirements (setup docs, re-read 2026-09-11) demand 4 GB or more of
RAM, which basic's 1 GiB does not meet. Price cannot rescue an instance the
vendor's own minimum excludes.

Candidate 3, flat-rate VPS class (Hetzner CX22-class exemplar: 2 shared vCPU,
4 GB RAM, flat monthly rate). A low-single-digit-euro monthly list price
converts to roughly USD 1.20 to 1.50 per week equivalent with unlimited hours
inside the week, which admits on price and is trivially forecastable. Verdict:
admitted on price, NOT selected. Four reasons, none of them price. Charter
section 8 names the funded Cloudflare account, so a decision away from it
needs its evidence and this is it: no funded account or working card exists
for a second provider (funding is Tom's external action); the list rate could
not be machine-verified in this spike because hetzner.com/cloud renders
prices client-side and the Cloud pricing API requires a token, so the input
is provisional and selection requires verified inputs; the Hetzner-range
IP-reputation report (see Auth verdict) attaches to exactly this class of
host; and a second provider doubles the credential and teardown surface for
no measured gain while the first trial has not run. Revisit only with a
verified rate and a funded account.

Candidate 4, variable-market spare capacity (Spot and preemptible exemplar).
REFUSED under Gap D: a resource whose charge cannot be forecast is refused.
The optimistic bound (a fraction of on-demand while capacity lasts) fits
USD 50 easily; the pessimistic bound (interruption mid-trial, lost evidence,
fallback re-run at on-demand rates) cannot be bounded in advance, and
availability itself is unforecastable. Stating a spot price here would prove
the point by going stale on arrival, so no price is stated. That absence is
the finding.

Candidate 5, Cloudflare Workers Free plan for containers. REFUSED as
ineligible: the live pricing table lists Free as N/A for memory, CPU, and
disk. There is no forecastable container rate on Free because there is no
container on Free.

Always-on bound (informational, not the duty cycle): standard-1 running the
full 168-hour week forecasts USD 12.86 worst case (memory USD 5.82, CPU
USD 5.60, disk USD 0.29, base USD 1.15). Even unbounded running fits the
USD 50 unit. Price was never the risk; authentication is.

Free tiers, credits, and what follows them. Cloudflare's monthly inclusions
(25 GiB-hours, 375 vCPU-minutes, 200 GB-hours) recur with the USD 5 Paid
subscription and reset monthly; they are not a time-limited credit, and the
paid rate that follows is the overage table above. One 72-hour standard-1
trial consumes essentially the whole month's memory and CPU inclusion, so a
second trial in the same calendar month pays full overage on those legs;
admission must forecast against remaining inclusion, not the headline
allowance. No Hetzner free tier was found in published pricing, so none is
carried. The AWS-style 12-month new-account free tier was surveyed and not
pursued: no account exists and charter section 8 names Cloudflare.

### Auth verdict

Auth verdict: mode B. The trial does not run on subscription auth, tasks 9
and 10 do not run, and this finding goes straight into the fifth answer of
the section 2 progress report.

Mode A required an affirmative vendor-documentation finding that Cloudflare
Containers is a remote host we operate rather than a hosted service
consuming the subscription on our behalf. That finding does not exist.
Cloudflare's own Containers overview describes "serverless containers",
running "without worrying about managing infrastructure", with instances
"spun up on-demand and controlled by code you write in your Worker" and
deployed "to Region: Earth". That is managed-infrastructure language: we
supply the image and the lifecycle code, Cloudflare operates the hosts.
Ambiguity is resolved against us by task 4's own rule, so the verdict is
mode B. No code change was made to reach for mode C; API-key auth stays a
tagged separate issue with its own doctrine argument.

Supporting evidence, all re-read in this spike. Anthropic's Consumer Terms
restrict accessing the services "through automated or non-human means,
whether through a bot, script, or otherwise", except via an API key or where
explicitly permitted. A worker driving scheduled headless sessions is
automated means; no vendor statement was found explicitly permitting
subscription-CLI fleets in managed containers. (This language also covers the
existing workstation operation, so it is not new exposure created by the
sandbox, but a fleet amplifies it.) Claude Code's setup docs bless the
runtime shape (Ubuntu 20.04 plus, Debian 10 plus, Alpine 3.19 plus; 4 GB or
more RAM; x64 or ARM64) and say nothing about the hosting model, which is
exactly the gap: OS support is affirmed, host-operatedness is not. The
headless refresh defect is confirmed precedent, not rumor:
`anthropics/claude-code#50743` (closed 2026-04-19) reports a valid
refreshToken ignored on headless Linux with 401 at accessToken expiry
(about 6 hours in that report) and a 403 on manual OAuth refresh from a
non-browser request. The repo's prevention credential is the long-lived
subscription OAuth credential documented in `docs/infra/granite-oauth-token.md`
(minted by an interactive browser flow, roughly annual life), which is why
the trial criterion is drawn at access-token refreshes inside that
credential's life, never at rotation of the credential itself.

Token distribution sub-question, answered explicitly. How the credential
reaches the sandbox: as a deploy-time provider secret (wrangler secret or the
dashboard secret store, surfaced as an environment variable the existing
`subscription_auth_env` path consumes unchanged), set by the operator at
deploy time from the m-valor vault. Never in a commit, never in a log, never
in an argv; lane 3's vault writer owns the write path and phase 3 waits on
it. Rotation: manual, from a browser-capable machine, roughly annually, per
the granite token record; the sandbox can neither mint (minting opens a
browser) nor receive rotation by iCloud (a Linux container has none). Expiry
unattended: at long-lived-credential expiry the CLI falls back to the
short-lived path and fails within about an hour with a loud error, never a
silent hang, per the granite doc's graceful-degradation contract; the trial
watchdog treats repeated auth failure as a stop, and no auto-renewal exists
inside the sandbox. Under mode B none of this is exercised, and it stays
recorded rather than assumed.

Provisional assumption under charter section 9 (the "ordinary, individual
usage" scale question). Statement: continuously-running sandbox sessions
consuming subscription concurrency count as ordinary individual usage while
they stay within the task-9 bound and show no crowding-out of client-work
capacity. Evidence: spike-1's rule (official CLI on a remote host is the
supported shape; token plus third-party harness is blocked) plus Decision 8,
in which Tom authorized proceeding on subscription CLI sessions on remote
hosts within ordinary use with a recorded overturn bound. Confidence: low on
the scale question, high on the rule. Consequence: if wrong, rate limits or
account action, borne first by client-work capacity sharing the same
subscription. Overturn observation: a provider or Anthropic statement
treating remote subscription-CLI use as outside ordinary use, metering that
shows sandbox usage crowding out client work, or any auth failure
attributable to sandbox origin. On overturn, the lane pauses remote
execution and proposes a charter amendment; it does not litigate terms.

Half-a-vCPU finding. RAM first, because the vendor settled it: the 4 GB
minimum rules out the basic instance, so standard-1 (4 GiB, 1/2 vCPU) is the
minimum viable trial instance. CPU second: no vendor-published minimum vCPU
exists, and whether half a vCPU carries a `claude -p` subprocess plus a
worker is UNMEASURED. No acquisition was permitted in this task, so no
measurement was taken; a negative would have been a finding, and the absence
of a measurement is recorded instead of filled with optimism. Settlement
criterion, owned by task 9 whenever it runs: an OOM-free 72 hours with turn
latency comparable to the workstation baseline. Spike-6's half-vCPU ceiling
referred to sandbox-managed instances; the live Containers table lists
standard-1 at exactly 1/2 vCPU with larger types above, so the trial
instance sits on the half-vCPU line either way and the question stands.

Datacenter IP reputation. The Hetzner-range blocking report and the 403 on
non-browser OAuth refresh inside #50743 are two observations of one
phenomenon: network origin is used as a trust signal on auth-adjacent paths.
A Cloudflare Container egresses from Cloudflare's network, which neither
clears nor confirms Anthropic-side treatment of datacenter origin for the
subscription OAuth path. No test was performed (acquiring anything to test
with is forbidden to this task), so this stays a residual risk and a named
candidate for the fifth answer, not a decided point.

Note for regeneration: the operating report's recorded provisional
assumptions currently phrases the sandbox in mode-A terms. Under this mode-B
verdict the first regeneration replaces that assumption with the verdict
reference rather than carrying mode-A prose forward.

### Export/import destination

The export/import destination contract lane 3's future valor-improve
export and import writes against. Location: `POPOTO_IMPROVEMENT_CONTENT_PATH`
resolving to `~/.popoto/improvement_content`, outside any repo checkout;
deployments mount persistent storage at the resolved root so image rebuilds
and redeploys cannot destroy gathered artifacts, and the root stays beside,
never inside, the shared popoto content directory. Verification-on-load
guarantee: `VerifyingArtifactStore` re-hashes on every load, including loads
served from the `.versions/` fallback, and raises `ArtifactIntegrityError` on
mismatch. Archive path: `.versions/{prefix}/{hash}{ext}` under the root.
This is task 7's supersession (commit `6a70e1611`), proven phase-2 by the
cross-process round trip in `tests/unit/test_artifact_retention_root.py`;
the literal sandbox-to-local round trip is task 9's recorded evidence and
does not run under mode B. Topology choice, recorded: sandbox-local Redis
with an export path. The shared-Redis option was refused because reaching it
needs transport security that does not exist (no TLS, password, or
`rediss://` handling in settings; the production Redis is machine-global),
and that security work is named work for its own task, not a footnote. The
export path is therefore load-bearing, and every teardown is gated on a
verified export that fails closed.

## Rules & Constraints

Rate and quota constraints: the USD 50 weekly unit-3 meter admits the
selected trial at a USD 5.75 worst-case forecast; the USD 15 trial stop reads
`max(settled, forecast)` on the trial's own watchdog tick, so on forecast
alone the stop never binds a single planned 72-hour trial and stands instead
as the backstop against the keepAlive run-forever failure mode (a container
left alive past the window accrues per-second charges until `destroy()`).
Containers sleep after 10 minutes idle by default; the trial sets keepAlive
for its window only, and teardown treats any resource it cannot confirm torn
down as still running and still charging.

Budget separation: unit 3's meter reads only the infrastructure pool. No
code path moves spend between unit 2 (paid inference) and unit 3, and none
may be added to route around a limit.

Credential handling: the vault (m-valor) is the sole owner of credentials.
No credential lands in a log, a message, an argv, or a committed file. The
lane introduces no new consumer of the subscription OAuth credential; mode A
would have handed the existing consumer the credential it already expects,
and under mode B even that handoff does not happen.

Update contract: the sandbox is not a fleet machine. It is absent from the
`projects.json` machine roster, and `/update`, `remote-update.sh`, and
launchd never touch it. Code reaches it by image rebuild and redeploy. The
redeploy mounts persistent storage at the resolved retention root (see the
export/import destination above) or the redeploy destroys the evidence the
trial gathered.

Fifth-answer duty: every unresolved claim in this file (the scale
assumption, the unmeasured half-vCPU question, the IP-reputation risk, the
provisional flat-rate input) is a named input to the operating report's
fifth answer, not prose hedging.

## Rollback Plan

If the trial never runs (the mode-B present): nothing to tear down, no
account to close, no dollar spent. Rollback is deleting nothing and
recording everything, which this file does.

If a future mode-A trial proceeds and the window exhausts or the lane is
stopped: admission closes for the window; `standing` resources are torn down
at window end; `trial` resources run to the trial's end or the end of the
following window, whichever comes first, with the continuation booked as a
forecast overrun against the next window before it accrues. Every teardown
is gated on a verified evidence export and fails closed: a teardown that
cannot confirm the export leaves the resource running and writes the
escalation `spend_receipt` row. Teardown-and-destroy-account is the final
rollback: destroy the container instances with explicit `destroy()`, confirm
the destroy, close or empty the provider account's paid plan as applicable,
and confirm the export of all trial evidence to the destination above before
confirming anything else. An unconfirmed destroy is treated as still running
and still charging, mirroring the probe's `unknown` bias.
