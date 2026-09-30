# Env Completeness Validation

Detects missing **required** environment variables during update runs by comparing the live `.env` file against the canonical `.env.example` declaration list.

## Problem

New features frequently add variables to `.env.example`, but existing machines' vault `.env` files are never automatically alerted. The gap is silent — no warning appears during `scripts/update/run.py --verify`. Operators discover missing variables only when runtime errors occur.

`.env.example` also accumulated behaviour toggles and per-machine switches over time (#1968 and successors), most with an in-code default — treating every declared key as equally required flooded operators with noise dominated by keys nothing actually needs, and its own documented remediation (copying a declaration's placeholder into the shared vault) broke `Settings()` construction fleet-wide for keys declared with an empty value (#2845).

## How It Works

The completeness check runs automatically as part of `python scripts/update/run.py --verify` and `--full`. It performs three steps:

1. **Parse `.env.example`**: reads all `KEY=` declarations and their comment block, resolving the required/optional axis, the passthrough axis, and the description (see below).
2. **Parse live `.env`**: reads all keys present in `.env`, treating blank values (`KEY=`) as present. The `.env` file is a symlink to `~/Desktop/Valor/.env`.
3. **Diff**: keys declared **required** (unmarked) in `.env.example` but absent from `.env` are surfaced as a `WARN` line. Keys marked `@optional` never appear in the missing-key report, but an unset count for them still rides along in the output — filtered, not silently dropped.

### The `@optional` and `@passthrough` sigils

`.env.example`'s comment convention carries two independent sigils, each on its own comment line within a key's comment block:

- **`# @optional`** — this declaration has a traced read site in tracked code with an in-code default, **and** it is not a credential (not an API key, token, password, DSN, connection URL, or account identifier). Unmarked means required — the fail-closed default: forgetting the marker costs one spurious warning; a wrong marker silences a real secret forever. The marker must be the bare token `@optional`; prose containing the word "optional" (seven such lines exist in `.env.example`, one directly above a credential) is never treated as the marker.
- **`# @passthrough <binary>`** — an external binary (`op`, `headscale`) reads this key straight out of the environment; no tracked Python names it. A passthrough key is **still required** — the axis is orthogonal to `@optional`. It exempts the key from the reader-recurrence guard (`tests/unit/test_env_declaration_readers.py`), never from the completeness check.

Both sigil lines, and the marker lines themselves, are excluded from description candidates.

### Output Format

When required keys are missing, the verify output shows:

```
[update]   ACTION REQUIRED — env-completeness: 3 missing: REDIS_URL (Redis connection URL); OPENROUTER_API_KEY (OpenRouter API Key); STRIPE_API_KEY (Stripe API key) (2 optional unset)
```

The inline list caps at 5 keys with a `(+N more — run python -m scripts.update.verify for the full list)` suffix, and each description is capped and ellipsis-truncated, so a long or numerous missing set never reproduces the ~2,100-character unusable warning this replaces. The suffix points at `render_env_completeness_report`, which renders every missing key with no cap on either axis — the cap belongs to the cron summary and the Telegram reply, where an 80-key dump is unreadable, and the uncapped surface exists so nothing the summary elides is unreachable. Pointing the suffix at `--verify` would be circular: `--verify` re-runs the same `check_env_completeness` and prints the same capped string. The `(N optional unset)` residual appears on **both** the missing-keys branch (above) and the all-present branch:

```
[update]   env-completeness: all 80 required vars present (6 optional unset)
```

The check appears in `result.valor_tools` in the `VerificationResult`, and is a member of `human_gated_tools` in `scripts/update/run.py` — its warning emits once per state transition via `warn_state` rather than on every 30-minute cron cycle (see [`update-warning-channel.md`](update-warning-channel.md)), because the missing-required-key set is a property of the machine's vault, not something `/update` can fix.

## .env.example Comment Convention

Every `KEY=` declaration in `.env.example` must be preceded by at least one comment line. The convention:

```
# Short description of what this controls (default if unset, where to get it)
# @optional        <- only if it clears the two-leg criterion below
KEY_NAME=placeholder-value
```

The description is the **first** non-empty, non-sigil comment line in the block — the topic sentence, not a wrapped fragment's tail:

```
# Prefix used for all macOS launchd Label fields.
# Changing this after install requires uninstall + reinstall.
# The canonical Valor install uses com.valor.
SERVICE_LABEL_PREFIX=com.valor
```

reports as "Prefix used for all macOS launchd Label fields." — the first line, not the last.

Section separator lines (`# ======...`) are ignored by the parser and do not contribute to descriptions. **A documentation block must end with a blank line**: the comment accumulator resets on a blank line, a non-comment line, or a `# ====` separator — never on the sigil lines themselves — so a header block that runs straight into the next declaration without a break marks *that* declaration `@optional` by accident.

## Deciding whether a key is `@optional`

A declaration gets `# @optional` when **both** hold:

1. it has a read site in tracked code, and that read site supplies an in-code default so an unset value is a defined, safe state; and
2. it is **not a credential**.

Never derive the marked set by running `check_env_completeness` on your own machine and annotating whatever it reports — the flagged set is a per-machine measurement (the check's output depends on which keys your vault happens to hold) and its wider form sweeps in genuine credentials. Six keys are traced and marked as the floor: `CONTEXT_RECALL_HISTORY_DEPTH`, `WORKER_SUPERVISOR_MAX_RESTARTS`, `SDLC_REVIEW_CROSS_VENDOR`, `HOOK_DETACH_DEADLINE_SECONDS`, `DISK_RECLAIM_APPLY`, `MEMORY_DECAY_PRUNE_APPLY` — a lower bound, not a target; mark more where the criterion clears it.

## Interpreting Warnings

An `env-completeness` warning means your vault `.env` is missing one or more **required** declared variables:

1. **Check the description** — it tells you what the variable controls.
2. **Add the required value to `~/Desktop/Valor/.env`** (the vault), then run `--verify` again to confirm the warning clears. **Do not** add an `@optional`-marked or empty-value declaration to the vault just to silence the check — several such declarations are empty by design, and writing an empty value for a typed setting (e.g. `FEATURES__CRASH_AUTORESUME_MAX_ATTEMPTS=`) breaks `Settings()` construction for every process on the machine.
3. A missing `@passthrough` key (`OP_SERVICE_ACCOUNT_TOKEN`, `HEADSCALE_SERVER_URL`, `HEADSCALE_PREAUTH_KEY`) needs the credential provisioned per its own runbook (1Password service-account token; Headscale mesh setup) — the check names it because it is genuinely required, even though no tracked Python reads it directly.

## No Declaration Without a Reader

`tests/unit/test_env_declaration_readers.py` asserts every `.env.example` declaration clears one of two legs: a literal occurrence in a tracked, non-markdown file, or a `@passthrough <binary>` sigil. A declaration satisfying neither is dead weight advertised as live configuration — three such declarations (`OLLAMA_URL`, `OLLAMA_VISION_MODEL`, `SESSION_RUNNER_SESSION_EVENTS_MAX_ENTRIES`) were deleted rather than marked, because nothing reads them at all.

## Graceful Degradation

The check never crashes the update run:

- **`.env` not found** — returns `skipped (.env not found)`. Expected on a fresh machine before vault sync, or a transient iCloud-sync gap on the symlinked vault.
- **`.env.example` not found** — returns `skipped (.env.example not found)`.
- **`OSError` reading either file** — returns `skipped (read error)`. Covers TCC permission errors and iCloud eviction.

Because `env-completeness` is a `human_gated_tools` member, its `warn_state` resolution branch is gated on the check having genuinely passed (`version` not starting with `"skipped"`) — a transient vault outage must never clear the stored suppression signature, or the next healthy run re-emits the entire missing-key report and (post-#2845) queues an unnecessary fix session.

## Implementation

**`scripts/update/verify.py`**:
- `_parse_env_example(path)` — returns `(key, description, optional, passthrough)` tuples, resolving both sigils and the first-line description rule
- `_parse_env_keys(path)` — returns the set of keys present in `.env` (blank values count as present)
- `check_env_completeness(project_dir)` — orchestrates the comparison against the required-key set and returns a `ToolCheck`
- `verify_environment()` — calls `check_env_completeness()` and appends to `result.valor_tools`

**`scripts/update/run.py`**:
- `env-completeness` is a member of `human_gated_tools` (Step 6's `valor_tools` loop) — one emission per state transition via `warn_state.should_emit`, resolution gated on genuine success

## Tests

`tests/unit/test_env_completeness.py` — covers missing-required-key detection, description extraction (first-line rule), blank-value tolerance, multiple missing keys, the `@optional`/`@passthrough` sigils, prose "Optional" not matching, the inline-key cap, the residual count on both return branches, all-present happy path, skipped results for missing files, OSError graceful recovery, multi-line comment block parsing, and section separator exclusion.

`tests/unit/test_env_declaration_readers.py` — the reader-recurrence guard covering both legs and the passthrough-set pin.
