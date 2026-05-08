# /do-pr-review Bot Identity

**Issues:** #1300 (bot identity), #1301 (conflict-state no-op)
**PR:** sdlc-1300/1301

Pipeline-driven PR reviews now post under a dedicated service-account identity
instead of the operator's personal `gh` credential. Reviews are prefixed with
a machine-readable marker. `BLOCKED_ON_CONFLICT` and `PR_CLOSED` paths use
`gh pr comment` exclusively — never `gh pr review`. `UNKNOWN` mergeability
after retry is treated conservatively as `CONFLICTING`.

## Why This Exists

Three problems were observed on yudame/cuttlefish PR #354 (2026-05-06):

1. **Wrong identity:** Two `CHANGES_REQUESTED` and one `APPROVED` review were
   attributed to `tomcounsell` (the operator). The `APPROVED` can satisfy a
   "1 approving review required" branch-protection rule without any human reviewing
   the code.

2. **Conflict-state false review:** When the PR had `mergeable=CONFLICTING`, the
   skill posted a formal `CHANGES_REQUESTED` review whose body was "rebase first."
   This is not a code-review verdict — it's a mechanical gate failure that should
   be surfaced as a comment, not counted as a review pass.

3. **Duplicate decision tree:** `SKILL.md §6` had a parallel Tier 1/2/3 decision
   tree that didn't consult `$PREFLIGHT_VERDICT`, allowing conflict-state PRs to
   receive formal reviews.

## How It Works

### Bot Identity (`SDLC_AGENT_GH_TOKEN`)

When the skill runs in pipeline context (`CLAUDE_AGENT_REVIEW=1`), it injects
`GH_TOKEN=$SDLC_AGENT_GH_TOKEN` for the single `gh pr review` or `gh pr comment`
subprocess that posts the review. All other `gh` calls (read-only) use the
operator's credential.

**Token resolution (in `post-review.md §0`):**
```bash
GH_TOKEN_FOR_REVIEW=""
if [ "${CLAUDE_AGENT_REVIEW:-0}" = "1" ]; then
  if [ -z "${SDLC_AGENT_GH_TOKEN:-}" ]; then
    echo "ERROR: agent context but bot token missing — refusing to post under operator identity"
    exit 1
  fi
  GH_TOKEN_FOR_REVIEW="$SDLC_AGENT_GH_TOKEN"
fi
```

**Important safety:** The `env GH_TOKEN=...` wrapper is only applied when
`GH_TOKEN_FOR_REVIEW` is non-empty. An empty `GH_TOKEN` would corrupt `gh`'s
stored credential.

### Machine-Readable Marker

Every agent-posted review body begins with:
```
<!-- SDLC-AGENT-REVIEW v1 sha=<HEAD_SHA> -->
```

- Present when `CLAUDE_AGENT_REVIEW=1` and a code-review verdict is posted
- Absent for local developer runs
- Absent for `BLOCKED_ON_CONFLICT` / `PR_CLOSED` comment-only paths
- Invisible in GitHub's rendered UI; queryable via the API

### `sdk_client.py` Injection

PM/Teammate sessions automatically receive:
- `CLAUDE_AGENT_REVIEW=1`
- `SDLC_AGENT_GH_TOKEN` forwarded from the process environment

This means all SDLC pipeline sessions running `/do-pr-review` get bot identity
automatically. Local developer runs (no `CLAUDE_AGENT_REVIEW`) are unchanged.

### Single Decision Tree

The Tier 1/2/3 block in `SKILL.md §6` was deleted. All review-posting logic
now lives exclusively in `post-review.md §3`. The preflight branches are checked
first in §3 — `gh pr review` is unreachable when `PREFLIGHT_VERDICT` is
`BLOCKED_ON_CONFLICT` or `PR_CLOSED`.

### UNKNOWN Mergeability → CONFLICTING

When `mergeable=UNKNOWN` persists after a 2-second retry, the skill treats it
conservatively as `CONFLICTING` and emits `BLOCKED_ON_CONFLICT`. This prevents
approving a PR that GitHub hasn't finished evaluating.

## Provisioning the Bot Account

### 1. Create the Bot GitHub Account

1. Register a new GitHub account (e.g. `yudame-sdlc-bot`).
2. Add the account as a collaborator with "Write" access on each repo you want
   it to review (Settings → Collaborators → Add people).
3. Accept the invitation from the bot account.

### 2. Generate a PAT

1. Log in as the bot account.
2. Go to Settings → Developer settings → Personal access tokens → Fine-grained tokens.
3. Create a token with **repository permissions** on the target repos:
   - Pull requests: Read and write (to post reviews and comments)
   - Contents: Read (to read PR diffs — optional, `gh pr view` works without it)
4. Set a reasonable expiration (90 days) and note the rotation date.
5. Copy the token.

### 3. Add to Vault

```bash
# Edit ~/Desktop/Valor/.env
SDLC_AGENT_GH_TOKEN=ghp_your_bot_token_here
```

The iCloud-synced vault propagates this to all machines automatically.

### 4. Verify

```bash
GH_TOKEN=ghp_your_bot_token_here gh api user --jq .login
# Should print: yudame-sdlc-bot (or your chosen bot username)
```

## Configuring Branch Protection

The marker and bot identity are **forensic only** — they do NOT prevent the
bot's `APPROVED` from satisfying branch protection without additional config.

### Pattern A — CODEOWNERS (recommended for existing repos)

1. Create or update `.github/CODEOWNERS`:
   ```
   * @yudame/human-reviewers
   ```
2. In the repo's branch protection rules for `main`, enable:
   - "Require a pull request before merging"
   - "Require review from Code Owners"
3. Ensure the bot account is NOT in the `human-reviewers` team.

**Effect:** The bot's `APPROVED` satisfies the general approval count, but the
CODEOWNERS gate still requires a human-team approval.

### Pattern B — GitHub Rulesets (recommended for new repos)

1. Go to Settings → Rules → Rulesets → New ruleset.
2. Create a Branch ruleset targeting `main`.
3. Under "Required approvals", set count to 1.
4. Under "Bypass list", do NOT add the bot account (bypass = skip the rule).
5. Under "Required reviewers" or equivalent, restrict to a human team that
   excludes the bot.

### Historical Reviews

Existing reviews on yudame/cuttlefish PR #354 (three reviews attributed to
`tomcounsell`) are left untouched. GitHub's API does not support editing review
bodies via the standard REST surface (`PATCH /repos/.../pulls/reviews/<id>`
updates state, not body). The forensic cutover applies only to reviews posted
after this change is deployed.

## Troubleshooting

### "agent context but bot token missing"

```
ERROR: agent context (CLAUDE_AGENT_REVIEW=1) but SDLC_AGENT_GH_TOKEN is unset or empty.
Refusing to post review under operator identity in pipeline context.
```

**Fix:** Add `SDLC_AGENT_GH_TOKEN=<token>` to `~/Desktop/Valor/.env` and restart
the worker (`./scripts/valor-service.sh worker-restart`).

### Marker Missing on Agent Review

If a review was posted in pipeline context but lacks `<!-- SDLC-AGENT-REVIEW v1 -->`:

1. Check that `CLAUDE_AGENT_REVIEW=1` was set in the session's env:
   ```bash
   python -m tools.valor_session inspect --id <session_id> | grep CLAUDE_AGENT
   ```
2. Verify the review path reached `post-review.md §0` (not a legacy code path).

### Token Revoked / Non-Zero Exit from `gh pr review`

If the bot PAT expires or is revoked, the pipeline emits:
```
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"IDENTITY_MISSING",...} -->
```

The PM session surfaces this as a failure. No silent fallback to the operator
credential occurs.

### Review Counted Toward Branch Protection Unexpectedly

The marker alone does not affect branch protection gating. You must configure
CODEOWNERS or a Ruleset to exclude the bot from the approver-counting. See
"Configuring Branch Protection" above.
