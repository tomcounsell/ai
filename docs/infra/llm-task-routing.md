# LLM Task Routing: Ollama Service, Encoder Weights, Decisions Endpoint, Log Evidence, and Rollback

Operator notes for the routing layer under `agent.llm.run_typed` (#3410, #3420, #3421). The design is in [LLM Task Taxonomy](../features/llm-task-taxonomy.md), the transport in [Non-Harness LLM Wrapper](../features/nonharness-llm-wrapper.md), and the encoder backend in [Local Encoder Classifier](../features/local-encoder-classifier.md); this page is what a machine needs (the Ollama daemon, the encoder weights and heads, the TypeSafe key), what the logs prove, what the comparison runner spends, how a comparison runs on a host that serves live traffic, and how to back a landing out without a deploy.

## Ollama Service Requirements

Every machine that runs the bridge, the worker, or the reflections jobs serves the classification sites declared `backend=OLLAMA` (`job_router.route`, `classifier.intake_intent`, `memory_audit.classify`, and any site a later lane lands) from its local Ollama daemon. A machine that runs those processes without a daemon pays a connection-refused fallback to Anthropic on every call: nothing breaks, and every call costs a `transport` failure plus a Haiku request. `python -m tools.doctor` (full run) fails its `ollama_daemon` row in that state and names the sites.

| Requirement | Value | Why |
|-------------|-------|-----|
| Model pulled | `granite4.1:3b` (`config.models.OLLAMA_CLASSIFIER_MODEL`) | The only model the Ollama leg runs. `ollama pull granite4.1:3b`. |
| `OLLAMA_KEEP_ALIVE` | `-1` | Ollama unloads a model after five idle minutes by default; the first message after a quiet spell would then pay a cold load against the leg's 20 s timer. `-1` keeps granite resident. |
| `OLLAMA_NUM_PARALLEL` | `4` | Ollama serves one request at a time by default; a burst of messages (one message crosses several classifiers) serializes on the daemon. `4` matches the concurrency the comparison runner measures p95 at. |

Both values are environment variables on the Ollama process. They are read once at daemon start, so setting them means restarting the daemon.

### Setting them

Ollama runs one of two ways on a Mac. In both cases the variables must reach the daemon's process environment, and a shell profile (`~/.zshrc`, `~/.zshenv`) does not, because launchd-launched processes never read one.

**Ollama.app (menu-bar app, launched by launchd at login).** Set the variables in the user's launchd session, then relaunch the app so it inherits them:

```bash
launchctl setenv OLLAMA_KEEP_ALIVE -1
launchctl setenv OLLAMA_NUM_PARALLEL 4
osascript -e 'quit app "Ollama"'; open -a Ollama
```

`launchctl setenv` lasts until logout. To make it survive a reboot, a LaunchAgent that runs the two `setenv` calls at login (`RunAtLoad`) ahead of Ollama.app is the durable form; `launchctl getenv OLLAMA_KEEP_ALIVE` shows the current session value.

**Homebrew service (`brew services start ollama`).** Add an `EnvironmentVariables` dict to `~/Library/LaunchAgents/homebrew.mxcl.ollama.plist` and restart the service:

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>OLLAMA_KEEP_ALIVE</key><string>-1</string>
    <key>OLLAMA_NUM_PARALLEL</key><string>4</string>
</dict>
```

```bash
brew services restart ollama
```

### Verifying

```bash
python -m tools.doctor            # "LLM routing" section: ollama_daemon row
curl -s localhost:11434/api/ps    # expires_at far in the future means keep-alive -1
grep -m1 OLLAMA_NUM_PARALLEL ~/.ollama/logs/server.log   # the daemon's startup env dump
```

Doctor's `ollama_daemon` row reports whether `granite4.1:3b` is pulled, whether it is loaded and its `expires_at` (the keep-alive evidence), the current `local_typed_hard_s`, and the declared `OLLAMA` sites. The daemon's own startup log is the only readout of `OLLAMA_NUM_PARALLEL`.

## Local Encoder Weights

Every machine that runs the bridge, the worker, or the reflections jobs serves the sites declared `backend=LOCAL_ENCODER` from one pinned embedding model on its own CPU, in-process, with no daemon. A machine without the weights, the extra, or a site's head pays a fast `transport` fallback to Anthropic on every call for that site: nothing breaks, the answer comes from Haiku inside the budget, and doctor's `local_encoder` row fails naming the fix.

| Item | Value |
|------|-------|
| Model | `Xenova/bge-small-en-v1.5` (`config.models.LOCAL_ENCODER_MODEL`), the ONNX export of `BAAI/bge-small-en-v1.5`, MIT |
| Revision | `ea104dacec62c0de699686887e3f920caeb4f3e3` (`LOCAL_ENCODER_REVISION`) |
| `onnx/model_int8.onnx` | 34 MB, sha256 `bf64d05457cb391fa88d045faf5927a15ea36d96228ddf23ea970087afdc1197` |
| `tokenizer.json` | 0.7 MB, sha256 `d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66` |
| Cache dir | `~/.cache/valor-encoder/`, overridden by the ambient env var `LOCAL_ENCODER_MODELS_DIR` (`config.models.local_encoder_models_dir()`; no settings field, no `.env.example` entry). Shared across every worktree on the machine. Files keep their repo-relative path, so the model sits at `<dir>/onnx/model_int8.onnx`. |
| Extra | `classification-local` in `pyproject.toml` (`onnxruntime>=1.25.0`, `tokenizers>=0.21`), installed fleet-wide by `/update`'s `uv sync --all-extras`. |
| Heads | `agent/llm/backends/heads/<site>.json`, committed in the repo; `git pull` is the propagation. |

The `LOCAL_ENCODER_FILES` dict (file name to sha256) is read by the download script, the leg's loader, and the doctor row, so one pin governs all three.

### Fetching and verifying

```bash
python scripts/download_local_encoder_models.py            # download what is missing or mismatched
python scripts/download_local_encoder_models.py --dry-run  # show the plan
python scripts/download_local_encoder_models.py --force    # re-download every file
```

The script fetches each file from Hugging Face at the pinned revision, streams to a `.part` sibling, and renames into place only when the sha256 matches the pin. A present file whose digest matches is skipped; one whose digest differs is re-downloaded; a download whose digest still differs is deleted and the script exits 1 naming the expected and actual digests. The leg itself never downloads: `agent/llm/backends/local_encoder.py::_verified_paths` checks every file's sha256 at load and refuses on a mismatch (`LLMCallError(reason="transport")` naming the file and this script).

`/update` runs the script as Step 3.15 through `scripts/update/local_encoder.py::ensure_models`: skipped when every pinned file verifies, a subprocess run of the script otherwise, non-fatal (a failure is a warning in the update result and the router's Anthropic fallback answers until the next update). The extra installs through the existing `uv sync --all-extras` sync in the same update.

### Verifying

```bash
python -m tools.doctor            # "LLM routing" section: local_encoder row
ls -la ~/.cache/valor-encoder/onnx/model_int8.onnx ~/.cache/valor-encoder/tokenizer.json
```

Doctor's `local_encoder` row reports `extra=installed|missing`, `weights=ok (2 files verified under <dir>)` or the per-file `missing`/`mismatch` state, `heads=ok (<n> verified)` or the sites whose head is missing or fails the leg's validating loader (`heads=unreadable (<reason>): <site>`), and the declared `LOCAL_ENCODER` sites. With a declared site it fails when any of the three is broken, because that site would fall back to Anthropic on every call on this machine; the `fix` names `uv sync --all-extras`, the download script, or the head to commit (`python -m tools.classification_eval --site <site> --fit --land`). With no declared site the state is reported as info.

## Decisions Endpoint

Every classification site declared `backend=DECISIONS` runs on TypeSafe's Jev, a structured-decision model that takes a `state` and typed `questions` and answers with per-option probabilities in about a second, through the decisions leg (`agent/llm/backends/decisions.py`), with the local Ollama leg (granite) as the route's fallback. The daemon requirements above therefore apply to a `DECISIONS` site as well: granite is what answers when Jev does not.

| Item | Value | Why |
|------|-------|-----|
| Endpoint | `POST https://api.typesafe.ai/v1/systemone` (`config.models.TYPESAFE_DECISIONS_URL`, a plain constant) | TypeSafe's native API, reached directly with the TypeSafe key. One transport, no proxy in the path. |
| Model | `jev-1.13.0` (`config.models.JEV`) | Pinned to the version. The `jev-latest` and `jev-preview` aliases move with releases and would silently re-point the thresholds each site's record was tuned against; the live probe test fails by name on a re-point. |
| Pricing | 0.042 USD per million input tokens, output tokens free, 32k-token `state` (64k per request) | https://docs.typesafe.ai/models, retrieved 2026-09-21, recorded in `MODEL_INFO[JEV]` with `price_retrieved_at`. The metering constant is `JEV_PRICE_USD_PER_MTOKEN`; setting it to `None` (the price withdrawn at a re-read) makes every envelope settle `unknown`, never zero. |
| Timer | `decisions_sdk_s`, 3.0 s (`TIMEOUTS__DECISIONS_SDK_S`, floor 0.5 s) | The leg's single SDK-level timer; the endpoint answered in 1.0 to 1.5 s on every probe. See [Config Timeout Catalog](../features/config-timeout-catalog.md). |

### The key

`TYPESAFE_API_KEY` is a required credential in `.env.example`, read only through `settings.api.typesafe_api_key` (a `default_factory` read of the flat key, since the `API__` nested source never sees it). Every machine that runs a process serving a `DECISIONS` site (the bridge for C1 to C9, C12, and C13; the worker for C10 and C11; the reflection worker for C14 and C15) needs it in its vault `.env` (`~/Desktop/Valor/.env`). The fleet key lives in 1Password, vault `m-valor`, item "TypeSafe API", field `api_key`, through the service account:

```bash
OP_CACHE=false op read "op://m-valor/TypeSafe API/api_key"
```

Two keys exist by design: the Valor hosts carry the fleet key; Tom's own machine carries his own. Receipts live in the Redis of the machine that made the calls, so Jev spend attributes per machine and per key by construction. Never echo the value; the leg scrubs it from every log line and error message, and the doctor row never renders it.

Without the key, nothing breaks: the leg raises `LLMCallError(reason="transport")` before any I/O on every call and the wrapper runs granite on the same inputs, so every `DECISIONS` site answers from its fallback and the log reads `llm_fallback ... primary=decisions fallback=ollama reason=transport` once per call. `python -m tools.doctor` (full run) fails its `decisions_endpoint` row in that state, naming the sites and the fix (no network call is made), and the env completeness check reports the missing declaration on every machine whose vault `.env` lacks it.

### Metering

Jev calls are metered on the paid-inference meter (`tools/paid_inference_meter.py`) under purpose `structured_decision`, on RSI case `1ec40086ca1d422e90ef747775ff7f64`. The meter counts whole cents and writes one `spend_receipt` row per settlement, and one Jev call costs about 0.00002 USD, so the leg amortises through a one-cent envelope per process (`SpendEnvelope` in the leg): reserve 0.01 USD once, add `usage.input_tokens × 0.042 / 1e6` per answered call (output tokens are free), settle and re-reserve when the next call's 32k-state bound (0.001344 USD) would not fit or the UTC day rolls over. A typical call does no Redis I/O, and the case sees one receipt per cent of spend; at the observed 300 to 600 input tokens per call that is one receipt per roughly 500 calls. A response without `input_tokens` marks the envelope `unknown` (charter §8: uncertain metering is not zero cost), and a process that exits with an open envelope leaves a reservation the reconcile sweep receipts as `unknown` at one cent. `structured_decision` is not the `rsi` purpose, so it never counts against the RSI pool; it is visible in `valor-improve budget` receipts like `sdlc_review`. The comparison runner's decisions arm uses the same envelope class with a per-run reservation (below).

### The greps

Positive evidence that a `DECISIONS` landing is serving live traffic, per site:

```bash
grep -c "llm_route site=routing.needs_response backend=decisions" logs/bridge.log
```

The degraded state, per site and by reason:

```bash
grep -c "llm_route site=routing.needs_response backend=" logs/bridge.log
grep -c "llm_fallback site=routing.needs_response primary=decisions" logs/bridge.log
grep "llm_fallback .* primary=decisions" logs/bridge.log | grep -o "reason=[a-z_]*" | sort | uniq -c
```

The fallback share for a site over a window is the `llm_fallback` count over the `llm_route` count: `llm_route` is every answered call whichever leg answered (a fallback that granite answered reads `backend=ollama`), and `llm_fallback` is every call that left Jev for granite. After deploy, each landed hot-path site (C1 to C6) gets one observation window of at least one host day with both counts recorded beside its record id in the taxonomy page's [Lane C Outcome](../features/llm-task-taxonomy.md#lane-c-outcome) table; a share over 2% in the window is reported with the `reason=` split before the lane closes. `reason=timeout` on the lines is a stalled endpoint (the 3 s timer fired); `reason=transport` is a non-200 (a 429 or 529 falls to granite with no retry), a connection failure, or a missing key; `reason=validation` is a `choice` the leg abstained on (`min_confidence`) or an answer that failed the type.

### Deploy

After merge, `./scripts/valor-service.sh restart` (or fleet `/update`) picks up the leg in the bridge and the worker: `agent/llm/wrapper.py` imports it and the router's rule 7 routes to it, with no new service, plist, or dependency (`httpx` is already installed everywhere). The reflection worker imports `agent.llm` too and `valor-service.sh` never touches its plist, so on a machine that runs one, `scripts/install_reflection_worker.sh` after the restart reloads it on the new code. A machine that still needs the key takes it from the 1Password item above into its vault `.env` before the restart; the service plist merge carries it from there into every service process like its sibling keys.

## Log Lines and the Greps

The wrapper emits three fixed-prefix lines into the process log (`logs/bridge.log` for the bridge, the worker's and reflection worker's logs for the session-side and reflection sites).

| Line | Level | Fields |
|------|-------|--------|
| `llm_route site=<site> backend=<backend> elapsed_ms=<int>[ confidence=<score>]` | INFO | One per answered call, after whichever leg answered. `backend` is `local_encoder`, `ollama`, `anthropic`, or `decisions`; on a fallback it is the fallback's backend. `elapsed_ms` is the whole call, both legs included. `confidence=<score>` (`%.3f`) is present whenever the result carries a `confidence` attribute, on either leg. |
| `llm_fallback site=<site> primary=<backend> fallback=<backend> reason=<reason> elapsed_ms=<int>` | WARNING | Ahead of a fallback leg (`primary=ollama fallback=anthropic` on an `OLLAMA` site, `primary=local_encoder fallback=anthropic` on a `LOCAL_ENCODER` site, `primary=decisions fallback=ollama` on a `DECISIONS` site). `reason` is the primary's `LLMCallError.reason`: `timeout` (the daemon or the endpoint is up but slow), `transport` (the daemon refused or errored; the encoder's extra, weights, or head is missing; a non-200, a connection failure, or a missing key on the decisions leg), `validation` (granite's answer failed the schema twice; the encoder head cannot answer the output type; a Jev `choice` under `min_confidence` or outside the options), `slot_timeout` (Anthropic legs only). `elapsed_ms` is the primary's spend. |
| `llm_no_fallback site=<site> primary=<backend> reason=<reason> elapsed_ms=<int> budget_s=<float>` | WARNING | The primary failed with under 0.5 s of the caller's budget left, so no fallback ran and the site's fail-safe applied. |
| `eligibility warm-up done keys=<n> public=<n> failed=<n>` | INFO | Once per process start, when the eligibility cache warm-up finished. |

Positive evidence that a landing is serving live traffic:

```bash
grep -c "llm_route site=job_router.route backend=ollama" logs/bridge.log
```

The count rises with each message the site classifies on granite. A process where every line reads `backend=anthropic` and none reads `backend=ollama` for a declared `OLLAMA` site is resolving to the subscription backend: either the project key is `None` or a client key (expected, charter §7), or the cache never warmed (check the warm-up line below).

The same evidence for an encoder landing, and the score distribution behind it:

```bash
grep -c "llm_route site=<site> backend=local_encoder" logs/bridge.log
grep -o "llm_route site=<site> backend=local_encoder .*confidence=[0-9.]*" logs/bridge.log
```

The first count rises with each message the site classifies on its head. After a day of traffic the second grep is the site's score distribution: a cluster near `1/k` for `k` classes is the drift signal that calls for a measure-only re-fit (`python -m tools.classification_eval --site <site> --fit --candidate local_encoder,anthropic`, no `--land`), which writes a fresh record and leaves the serving head alone.

The degraded state:

```bash
grep -c llm_fallback logs/bridge.log
grep "llm_fallback" logs/bridge.log | grep -o "reason=[a-z_]*" | sort | uniq -c
```

`llm_no_fallback` has its own prefix, so the first count is real fallbacks only. `reason=timeout` on every line is a slow daemon (reloading, CPU-starved, queue-full); `reason=transport` on every line is a stopped one. The count over a window is the measurement for the rollback decision below.

The warm-up:

```bash
grep "eligibility warm-up done" logs/bridge.log
```

A process whose log has no such line after startup has a lost or failed warm-up; until it lands, client-keyed calls fail closed to Anthropic (the safe direction) and `valor` calls are unaffected (pinned in code). Per-key failures log `improvement eligibility: warm-up for '<key>' failed` above the summary line.

## Comparison Runner Spend

`python -m tools.classification_eval --site <id> --candidate <backend>[,<backend>]` runs a reference arm against candidate arms and writes the record the acceptance bar reads. The Haiku reference and the decisions candidate are paid inference; the granite arm is free.

| Arm | Transport | Spend |
|-----|-----------|-------|
| Haiku reference (C1 to C11) | `run_typed` on the Anthropic leg with the site's prompt verbatim | Subscription. Per-call cost is recorded on the record from the Haiku price with its retrieval date. |
| gemma reference (C15 only) | `google/gemma-4-26b-a4b-it:free` via OpenRouter chat/completions, paced to the free tier's rate (3.2 s between requests, four retries on 429) | Metered under purpose `promise_detector` on the paid-inference meter: one reservation per run (`0.002 USD` per planned call, floor `0.01`), settled from the accumulated `usage.cost`. |
| gemma reference, paid route (`--reference-model paid`) | Same weights without the `:free` suffix | For a run made while the free route is throttled upstream. Metered the same way; lane A's C15 run made 104 paid calls for 0.0025 USD. |
| granite reference (C12, C13, C14; `reference="ollama"`) | The Ollama leg, called directly | Free. These sites' landed backend is granite, so a decisions comparison there measures granite once, as the reference, and the audit judges that slot as the fallback. |
| granite candidate | The Ollama leg | Free. Dropped with a printed note at a granite-referenced site, so a record carries granite exactly once. |
| encoder candidate (`--candidate local_encoder`) | The encoder leg on the staged or served head | Free. |
| Haiku candidate (`--candidate anthropic`) | The Anthropic leg | Subscription. |
| decisions candidate (`--candidate decisions`) | The decisions leg, called directly with its own per-run `SpendEnvelope` (a leg failure is the arm's own error, never a fallback answer) | Metered under purpose `structured_decision`: one reservation per run of `max(0.01, 0.001344 × 2 × n)` USD (the 32k-state bound per planned call at two latency passes), settled once in `finally` from `input_tokens × 0.042 / 1e6` per call; a 200 without `input_tokens` marks it `unknown`. At the observed 300 to 600 input tokens a call costs 0.000013 to 0.000025 USD, so a 300-call run is about a cent and the full fifteen-site comparison at three iterations each about 0.30 USD. The fit path (`--fit`) refuses this candidate; it measures the encoder head. |
| Haiku training labels (`--fit`) | The reference arm over the training split, one call per input | Subscription; about 700 calls per routing site and 300 per 50-minimum site on a host with about 700 real messages, under one dollar per site at the recorded Haiku price. `--precheck` and `--preflight` spend nothing, and the precheck gate (`--precheck-agreement`) skips a site under `bar - 0.10` before any label is bought. |

The runner refuses to spend on a site whose inputs miss the minimum (50, or 200 for the routing sites): the shortfall prints and no reference call is made; a fit also refuses under half the minimum real in the held-out split, and refuses outright while contended because a `--land` changes what serves.

## Uncontended runs

A record clears the bar only with `contended: false`, and the runner reads all three contended labels once per run (`com.valor.bridge`, `com.valor.worker`, `com.valor.reflection-worker`; `CONTENDED_LABELS` in `tools/classification_eval/core.py`) and stamps the whole record, accuracy pass included. On a host that serves live traffic, every comparison therefore goes through `scripts/classification-eval-uncontended.sh <runner args>`, which is the one sanctioned way to run one there. It records which of the three labels `launchctl list` reports loaded and unloads exactly those (`./scripts/valor-service.sh stop` for the bridge, `./scripts/valor-service.sh worker-disable` for the worker, `launchctl bootout gui/$(id -u)/com.valor.reflection-worker` for the reflection worker; `stop` alone unloads only the bridge), installs its `EXIT` trap before the runner starts, runs `python -m tools.classification_eval "$@"` under a per-run bound (`RUN_TIMEOUT_S`, default 1800 s: a runner still going at the bound is sent SIGTERM, then SIGKILL after ten seconds, and the run exits 124), and on every exit path (exit 0, a shortfall exit 2, a traceback, Ctrl-C, the bound) restores what it unloaded, each label by its own command so one failure never skips the next: `./scripts/valor-service.sh start` when the bridge was loaded, `./scripts/valor-service.sh worker-start` when the worker was loaded (it re-enables and starts the worker; a worker-only host never gets a bridge started), `scripts/install_reflection_worker.sh` when the reflection worker was loaded. The restore ignores INT, TERM, and HUP from its first line (its children inherit that), so a terminal Ctrl-C or an SSH session dropping while `start` is in flight leaves the worker's restore and the reflection worker's reinstall to run. When the bridge was loaded it then waits up to 60 s for a `Connected to Telegram` line appended to `logs/bridge.log` after the stop (it records the log's size before stopping, so a marker from an earlier connect never counts; the check reads the appended bytes to their end, so a startup burst larger than a pipe buffer behind the marker still counts). It exits 1 naming every service that failed to return; otherwise the runner's exit code is propagated. The bridge is down for one bounded window per run, never for the whole iteration.

The wrapper refuses a thirteenth run in one UTC day (`MAX_UNCONTENDED_RUNS_PER_DAY=12`, counter file `data/classification_eval_runs.<YYYY-MM-DD>`, no override) and refuses to run beside another invocation (an atomic `mkdir` lock, `data/.classification_eval_uncontended.lock`, released only after the restores and the reconnect wait are done; a second wrapper would otherwise see the services already unloaded and run against a bridge the first's restore brings back mid-pass, or stop the bridge the first is bringing back), in both cases without stopping anything, so the production bridge is offline at most twelve windows a day. `tests/unit/test_classification_eval_uncontended.py` drives the script against fake executables through `VALOR_SERVICE_SH`, `INSTALL_REFLECTION_SH`, `LAUNCHCTL`, `CLASSIFICATION_EVAL_CMD`, `BRIDGE_LOG`, `RUNS_DIR`, `BRIDGE_WAIT_S`, and `RUN_TIMEOUT_S`, and proves the restore on each exit path, a Ctrl-C and a HUP during the restore that each still restore every label, the worker-only restore, a failed bridge start that still restores the worker, the stale-marker case, a startup burst past the pipe buffer, the timeout, the lock, and a second wrapper refused during the restore.

The promise judge itself (C15 in production, `reflections/improvement_collect.py::collect_promises`) is unmetered: it runs through `run_typed(task=PROMISE_JUDGE)` on the subscription leg. `promise_detector` remains the purpose only the runner's gemma reference arm meters under.

## Rollback

For a steady degraded backend, the levers below in order, each usable without a deploy (the last one excepted) and each with the log signature that confirms it took effect. Measure first: `grep -c llm_fallback logs/bridge.log` over the window, and the `reason=` split. Lever 0 is for the decisions leg (every `DECISIONS` call paying the 3 s timer before granite answers, or the endpoint answering non-200 on a share of calls); levers 1 to 3 are for a degraded Ollama daemon.

### Lever 0: turn the decisions leg off

Either of two settings in the vault `.env` (`~/Desktop/Valor/.env`), then `./scripts/valor-service.sh restart`:

```
# remove or comment out TYPESAFE_API_KEY, or
TIMEOUTS__DECISIONS_SDK_S=0.5
```

Unsetting the key makes the leg raise `reason=transport` before any I/O on every call at every site. The floor timer (0.5 s, under the endpoint's probed 1.0 to 1.5 s answer) makes the call raise `reason=timeout` at 0.5 s, but only at a site that passes no `sdk_timeout` (an explicit `sdk_timeout` always wins over the settings default, so C7 to C10, C14, and C15 keep their own timer under this lever). Either way the affected sites answer from granite on the same inputs, inside their budget: a site with no `sdk_timeout` keeps granite's full 20 s timer under the 35 s cap, and a site that passes `sdk_timeout=3.0` keeps about 3 s for granite with the key unset. The key edit is therefore the fleet-wide lever, and the doctor's `decisions_endpoint` row goes red naming the sites, which is the intended readout; the timer floor is the lever for a half-broken endpoint (answering, slowly) when the key should stay in place.

Expected signature: one `llm_fallback site=<site> primary=decisions fallback=ollama reason=transport` (key unset, a few milliseconds of `elapsed_ms`) or `reason=timeout` (floor timer, about 500 ms) per call on every `DECISIONS` site, followed by `llm_route ... backend=ollama`. Revert by restoring the line and restarting.

The code rollback for a `DECISIONS` site is the same one-word edit as lever 3: `backend=Backend.OLLAMA` on the declaration (its record's ollama arm is the passing fallback proof) or `backend=Backend.ANTHROPIC`, committed with the reason, then fleet `/update`.

### Lever 1: stop the Ollama daemon

Stop it however the machine runs it:

```bash
osascript -e 'quit app "Ollama"'      # Ollama.app
brew services stop ollama             # Homebrew service
```

Expected signature: one `llm_fallback ... reason=transport` warning per call on every `OLLAMA` site, each failing fast on connection refused (a few milliseconds of `elapsed_ms`), followed by `llm_route ... backend=anthropic`. Every call answers from Haiku inside its budget. Doctor's `ollama_daemon` row goes red naming the sites.

### Lever 2: cap the local leg's timer

In the vault `.env` (`~/Desktop/Valor/.env`):

```
TIMEOUTS__LOCAL_TYPED_HARD_S=3
```

then `./scripts/valor-service.sh restart`. `local_typed_hard_s` is the Ollama leg's single SDK-level request timer, read at call time, so every local leg is now capped at 3 s and the fallback fires early with most of the caller's budget intact. This is an existing catalog entry ([Config Timeout Catalog](../features/config-timeout-catalog.md)) and adds no switch.

Expected signature: `llm_fallback ... reason=timeout` with `elapsed_ms` around 3000 on the slow calls, then `llm_route ... backend=anthropic`; calls granite answers within 3 s still read `backend=ollama`. Revert by removing the line and restarting.

### Lever 3: the code rollback

Set the affected site's declaration to `backend=Backend.ANTHROPIC`, commit with the reason, and run fleet `/update`. Router rule 2 then routes the site to Anthropic for every key with no fallback and no local call.

Expected signature: no `llm_fallback` lines for the site; `llm_route site=<site> backend=anthropic` on every call. `python -m tools.classification_eval --audit` still passes because the site's record carries an Anthropic arm.

A landing is reinstated the same way: `backend=Backend.OLLAMA` on the declaration, with a record that clears the bar.

### Rolling back an encoder landing

There is no daemon to stop and no timer to cap: the leg answers from a file in a few milliseconds or raises. The one lever is the code rollback: set the site's declaration to `backend=Backend.ANTHROPIC`, delete `agent/llm/backends/heads/<site>.json` in the same commit (a head without a `LOCAL_ENCODER` declaration fails `tests/unit/test_classifier_heads.py`, and the audit refuses an orphan in either direction), commit with the reason, and run fleet `/update`. Router rule 2 then routes the site to Anthropic for every key.

Expected signature: no `llm_fallback` lines for the site; `llm_route site=<site> backend=anthropic` on every call. The audit still passes because the site's records carry an Anthropic arm. Reinstating is a fresh `--fit --land` run whose record clears the bar on both arms, then `backend=Backend.LOCAL_ENCODER` with the head it installed.

### Recovering an interrupted `--land` run

A `--land` run that dies between installing the served head and writing its record leaves a head on disk whose `run_id` matches no landed record, so the next `python -m tools.classification_eval --audit` goes red for that site. Re-run the same command replaying the saved draw (`--inputs` in place of `--save-inputs`): the split is a pure function of the draw and the fit is deterministic (zero init, fixed epochs, no randomness), so the re-run labels the same training split afresh (a new Haiku pass, so a label can differ), fits a head consistent with those labels, installs it under its own `run_id`, and writes the record that clears the audit:

```bash
python -m tools.classification_eval --site <site> --fit --land --candidate local_encoder,anthropic \
    --inputs data/classification_eval/<site>.jsonl
```

When the site should not have been touched at all, restore the committed head instead:

```bash
git checkout -- agent/llm/backends/heads/<site>.json
```

## See Also

- [LLM Task Taxonomy](../features/llm-task-taxonomy.md): the declarations, router rules, eligibility, and acceptance bar.
- [Non-Harness LLM Wrapper](../features/nonharness-llm-wrapper.md): the legs, the fallback budget, and the log lines' source.
- [Local Encoder Classifier](../features/local-encoder-classifier.md): the encoder leg, the head file, the fit protocol, the precheck gate.
- [Local Ollama Model Policy](../features/local-model-policy.md): which local models each machine runs and how `/setup` and `/update` check them.
- [Config Timeout Catalog](../features/config-timeout-catalog.md): `local_typed_hard_s`, `decisions_sdk_s`, and the Anthropic pair.
- [Local Doctor](../features/local-doctor.md): the "LLM routing" section.
- `docs/plans/structured-decision-transport-jev-behind-ollama-fallback.md`: lane C's plan (the decisions leg, the landing rule, the uncontended-run wrapper).
