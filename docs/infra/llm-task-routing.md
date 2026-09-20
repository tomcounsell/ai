# LLM Task Routing: Ollama Service, Log Evidence, and Rollback

Operator notes for the routing layer under `agent.llm.run_typed` (#3410). The design is in [LLM Task Taxonomy](../features/llm-task-taxonomy.md) and the transport in [Non-Harness LLM Wrapper](../features/nonharness-llm-wrapper.md); this page is what a machine needs, what the logs prove, what the comparison runner spends, and how to back a landing out without a deploy.

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

## Log Lines and the Greps

The wrapper emits three fixed-prefix lines into the process log (`logs/bridge.log` for the bridge, the worker's and reflection worker's logs for the session-side and reflection sites).

| Line | Level | Fields |
|------|-------|--------|
| `llm_route site=<site> backend=<backend> elapsed_ms=<int>` | INFO | One per answered call, after whichever leg answered. `backend` is `ollama` or `anthropic`; on a fallback it is the fallback's backend. `elapsed_ms` is the whole call, both legs included. |
| `llm_fallback site=<site> primary=<backend> fallback=<backend> reason=<reason> elapsed_ms=<int>` | WARNING | Ahead of a fallback leg. `reason` is the primary's `LLMCallError.reason`: `timeout` (the daemon is up but slow), `transport` (the daemon refused or errored), `validation` (granite's answer failed the schema twice), `slot_timeout` (Anthropic legs only). `elapsed_ms` is the primary's spend. |
| `llm_no_fallback site=<site> primary=<backend> reason=<reason> elapsed_ms=<int> budget_s=<float>` | WARNING | The primary failed with under 0.5 s of the caller's budget left, so no fallback ran and the site's fail-safe applied. |
| `eligibility warm-up done keys=<n> public=<n> failed=<n>` | INFO | Once per process start, when the eligibility cache warm-up finished. |

Positive evidence that a landing is serving live traffic:

```bash
grep -c "llm_route site=job_router.route backend=ollama" logs/bridge.log
```

The count rises with each message the site classifies on granite. A process where every line reads `backend=anthropic` and none reads `backend=ollama` for a declared `OLLAMA` site is resolving to the subscription backend: either the project key is `None` or a client key (expected, charter §7), or the cache never warmed (check the warm-up line below).

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

`python -m tools.classification_eval --site <id> --candidate <backend>` runs a reference arm against candidate arms and writes the record the acceptance bar reads. The reference arm is paid inference; the candidate granite arm is free.

| Arm | Transport | Spend |
|-----|-----------|-------|
| Haiku reference (C1 to C11) | `run_typed` on the Anthropic leg with the site's prompt verbatim | Subscription. Per-call cost is recorded on the record from the Haiku price with its retrieval date. |
| gemma reference (C15 only) | `google/gemma-4-26b-a4b-it:free` via OpenRouter chat/completions, paced to the free tier's rate (3.2 s between requests, four retries on 429) | Metered under purpose `promise_detector` on the paid-inference meter: one reservation per run (`0.002 USD` per planned call, floor `0.01`), settled from the accumulated `usage.cost`. |
| gemma reference, paid route (`--reference-model paid`) | Same weights without the `:free` suffix | For a run made while the free route is throttled upstream. Metered the same way; lane A's C15 run made 104 paid calls for 0.0025 USD. |
| granite candidate | The Ollama leg | Free. |
| Haiku candidate (`--candidate anthropic`) | The Anthropic leg | Subscription. |

The runner refuses to spend on a site whose inputs miss the minimum (50, or 200 for the routing sites): the shortfall prints and no reference call is made. Stop the local services first (`./scripts/valor-service.sh stop`; `worker-disable` if launchd would relaunch the worker) so the record's `contended` flag stays `false`; a loaded `com.valor.*` service stamps `contended: true` and the record cannot clear the bar.

The promise judge itself (C15 in production, `reflections/improvement_collect.py::collect_promises`) is unmetered: it runs through `run_typed(task=PROMISE_JUDGE)` on the subscription leg. `promise_detector` remains the purpose only the runner's gemma reference arm meters under.

## Rollback

For a steady degraded Ollama daemon (every call paying the 20 s timer before Haiku answers, or every call failing fast to Haiku), three levers in order, each usable without a deploy and each with the log signature that confirms it took effect. Measure first: `grep -c llm_fallback logs/bridge.log` over the window, and the `reason=` split.

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

## See Also

- [LLM Task Taxonomy](../features/llm-task-taxonomy.md): the declarations, router rules, eligibility, and acceptance bar.
- [Non-Harness LLM Wrapper](../features/nonharness-llm-wrapper.md): the legs, the fallback budget, and the log lines' source.
- [Local Ollama Model Policy](../features/local-model-policy.md): which Ollama models each machine runs and how `/setup` and `/update` check them.
- [Config Timeout Catalog](../features/config-timeout-catalog.md): `local_typed_hard_s` and the Anthropic pair.
- [Local Doctor](../features/local-doctor.md): the "LLM routing" section.
- #3421 adds the decisions endpoint section to this page when lane C lands.
