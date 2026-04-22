---
name: daily-integration-audit
description: "Run one integration audit per invocation, rotating through docs/features/ least-recently-audited-first. Audits the feature's code + doc accuracy/clarity/organization, then triages findings into three tracks: urgent hotfixes (spawn dev sessions now), real issues (create via /do-issue), and open investigations (raw GitHub issues labeled `investigation`). Use for periodic health checks or schedule as a cron."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, Skill
argument-hint: "[--feature <slug>] [--dry-run]"
---

# Daily Integration Audit

Picks the least-recently-audited feature doc from `docs/features/`, runs a full integration audit on it (code + docs), and routes the findings to the right place so they don't pile up as read-only reports. The point is to make audit output actionable by default, with deterministic rotation so every feature is covered on a predictable cadence.

## When to use

- Scheduled daily run (see `/schedule`) — keeps the whole system audited on rotation
- Ad-hoc periodic housekeeping
- When a specific feature is suspect — pass `--feature <slug>` to skip the random pick

## Inputs

- `--feature <slug>` (optional): skip the random pick and audit this feature. `<slug>` is the basename of a file in `docs/features/` without the `.md` extension.
- `--dry-run` (optional): produce the audit + triage plan but do NOT create dev sessions or GitHub issues. Prints the proposed actions for human review.

## Step 1: Pick the feature doc (least-recently-audited rotation)

If `--feature <slug>` is provided, use `docs/features/<slug>.md` and skip rotation.

Otherwise, pick the doc with the oldest (or missing) entry in `~/src/ai/data/audit-history.jsonl`:

```bash
python3 - <<'PY'
import json, os, pathlib
features_dir = pathlib.Path.home() / "src/ai/docs/features"
history_path = pathlib.Path.home() / "src/ai/data/audit-history.jsonl"

# Skip index/meta docs
SKIP = {"README.md"}
def is_feature_doc(p):
    return p.suffix == ".md" and p.name not in SKIP and not p.stem.endswith(("-overview", "-index"))

docs = sorted(p.stem for p in features_dir.iterdir() if is_feature_doc(p))

# Build slug -> most recent audit date from the log
last_seen = {}
if history_path.exists():
    for line in history_path.read_text().splitlines():
        try:
            entry = json.loads(line)
            slug, date = entry["slug"], entry["date"]
            if slug not in last_seen or date > last_seen[slug]:
                last_seen[slug] = date
        except (json.JSONDecodeError, KeyError):
            continue

# Pick the slug with no history, or the oldest last-seen date
never_audited = [s for s in docs if s not in last_seen]
if never_audited:
    pick = sorted(never_audited)[0]  # alphabetical tiebreak, deterministic
else:
    pick = min(docs, key=lambda s: last_seen[s])

print(pick)
PY
```

The feature *topic* for the audit is the returned slug (e.g. `session-steering`). The audit will discover related files semantically; the doc filename is just the seed.

Create `data/audit-history.jsonl` if it doesn't exist (an empty file is fine — first run will pick alphabetically).

## Step 2: Run the integration audit

Invoke the `do-integration-audit` skill on the feature topic. In addition to the 12 standard audit checks, include doc-level checks on the seed doc itself:

- **Accuracy**: do specific claims (file paths, function names, env var names, behavioral statements) match the current code?
- **Clarity**: can a new contributor understand the feature from this doc alone?
- **Organization**: is structure coherent? Are obvious sections missing?

**Use an Opus subagent via the Agent tool** for this — the audit is read-heavy and benefits from careful reasoning. The brief MUST be fully self-contained — the subagent will not ask follow-up questions. Pass every field below verbatim into the subagent prompt:

```
FEATURE_TOPIC: <slug>
SEED_DOC_PATH: docs/features/<slug>.md
VERIFICATION_PASS: required — re-read every cited file:line; grep the whole project for negative claims before asserting them; trace every dynamic-behavior claim into the relevant function body before writing a finding
OUTPUT_FORMAT: standard do-integration-audit format, followed by a separate `## Documentation Audit` section (accuracy, clarity, organization checks on the seed doc), followed by a `## Meta-observations` section capturing cross-cutting patterns that don't fit any single finding (e.g., "three separate findings all stem from the same god module" or "every doc in this feature describes a removed field") — these become Track C investigation checklist items
FINAL_LINE: must be exactly `SUMMARY: PASS=<n> WARN=<n> FAIL=<n>` so the parent skill can extract the counts
```

## Step 3: Triage into three tracks

Once the audit completes, read the findings and classify each one. The goal is to drop the audit into motion — no finding should end as a printed line the user has to copy around.

### Track A — Urgent hotfix (dev session now)

Criteria — all must be true:
1. **CRITICAL severity** in the audit report
2. **Clearly bounded**: the fix is a single file or a small diff, with no design question remaining
3. **High confidence**: the finding survived the verification pass and, ideally, a second-pass falsification check
4. **Safe to ship without a plan**: no cross-cutting refactor, no data migration, no API change that needs review

For each hotfix:

```bash
python -m tools.valor_session create \
  --role dev \
  --slug audit-hotfix-<short-slug> \
  --message "Hotfix from daily integration audit.

Finding: <one-line claim>
Evidence: <file:line>
Root cause: <one sentence>
Proposed fix: <one sentence>

Context: <paste the exact finding text from the audit report>

Please: implement the fix, run relevant tests, open a PR referencing this audit run."
```

If `--dry-run`, print the command instead of executing.

### Track B — Real issue (do-issue)

Criteria:
1. **WARNING or CRITICAL severity** that isn't a trivial hotfix (needs planning, has design questions, or touches multiple files)
2. **Clearly a bug or gap**, not an open question — the problem statement is precise even if the solution isn't

For each issue, invoke `/do-issue` with a pre-drafted title and body. The body must:
- State the problem from a new contributor's perspective (define terms)
- Cite exact file:line evidence from the audit
- Include the audit date and the feature slug in the footer: `Audit run: YYYY-MM-DD / feature: <slug>`

Invoke via the Skill tool: `Skill(skill="do-issue", args="...")`.

If `--dry-run`, print the proposed issue title + body instead of creating.

### Track C — Open investigation (`investigation` label)

Criteria:
1. Finding is **suspected but not confirmed** (the audit flagged uncertainty, or the verification pass couldn't prove it)
2. Finding names a **design question** rather than a bug (e.g., "is the dual-steering-queue split intentional?")
3. Finding is a **cross-cutting pattern** that deserves discussion before fixing (e.g., "god module with 5000+ lines")

These do NOT need the full `/do-issue` treatment — they are discussion starters, not spec'd work.

Invoke `/do-investigation-issue` for each Track C finding:

```
Skill(skill="do-investigation-issue", args="<component> — <brief finding>")
```

The skill handles label creation, the issue body template, and the label policy (`investigation` only — never `bug` until confirmed). Pass each finding as a separate invocation. The skill's "When to Err on the Side of Filing" section is the triage guide — when in doubt, file it.

If `--dry-run`, print the component + brief description for each finding instead of invoking the skill.

## Step 4: Log and report

Parse the `SUMMARY: PASS=<n> WARN=<n> FAIL=<n>` line from the audit report and append one entry to `~/src/ai/data/audit-history.jsonl` (even in `--dry-run` mode — the log is what drives rotation, so skipping it would make the same doc get picked again tomorrow; if you want a pure read-only rehearsal, use `--feature` explicitly):

```bash
python3 - <<PY
import json, pathlib, datetime
entry = {
    "slug": "<slug>",
    "date": datetime.date.today().isoformat(),
    "pass": <pass_count>,
    "warn": <warn_count>,
    "fail": <fail_count>,
    "hotfixes": <n_hotfixes>,
    "issues": <n_issues>,
    "investigation_issue": <investigation_issue_number_or_null>,
    "dry_run": <bool>,
}
log = pathlib.Path.home() / "src/ai/data/audit-history.jsonl"
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a") as f:
    f.write(json.dumps(entry) + "\n")
PY
```

Then post a short summary back to the user:

```
Daily integration audit — <feature-slug>

Audit: PASS N / WARN N / FAIL N
Hotfixes: N dev session(s) spawned — <list of slugs>
Issues: N created — <list of issue numbers>
Investigations: 1 issue #<N> (<count> items)
```

If `--dry-run`: same summary but with "(dry-run, no side effects)" appended.

## Guardrails

- **Never spawn more than 3 hotfix dev sessions in a single run.** If the audit finds more, downgrade the extras to Track B (issues). Bulk hotfixing makes review harder and burns worker slots.
- **Never create more than 5 issues in a single run.** If the audit finds more, fold the extras into the single Track C investigation issue as checklist items. A flood of low-priority issues is noise.
- **Skip the entire triage step if the audit returned zero WARN/FAIL.** Just log the entry and post a "PASS" summary. Not every audit needs an action.
- **Rotation is the only duplicate-protection.** The least-recently-audited pick in Step 1 guarantees a doc won't be re-audited until every other doc has been covered once. No separate cooldown is needed.

## Scheduling

To run this as a daily cron, use the `/schedule` skill:

```
/schedule create --cron "0 9 * * *" --command "/daily-integration-audit"
```

Morning runs make sense because the resulting dev sessions and issues land during a working window where they can be reviewed.
