# Refinement pass, 2026-09-19

Run after the commit pass, with three sources of new information: an independent design review of the enforcement claims, eight findings from the session that worked [prereqs.md](../prereqs.md), and Noam Brown's account of multi-agent coordination at OpenAI. The rule for the pass, from the architect: lean toward simplifying; where complexity is recommended but not required, leave it as an inline note and a TODO rather than a design.

Every item has a disposition: **applied** (the design now says so, section cited), **todo** (a paragraph in the document marked TODO, with the condition that triggers building it), or **declined** with the reason.

## From the independent review

| # | Finding | Disposition |
|---|---|---|
| 1 | Token revocation does not make an agent inert; sandbox processes keep running | applied: stop is generation fence, revoke, and compute stop with disk retained, in one transaction; "lossless" redefined as durable state; tech stack §4, architecture §1 |
| 2 | The gateway records model requests, not execution; one bash call is many operations | applied: three records named, gateway log, tool log, effect ledger; the Verifier reads the last two; tech stack §4, architecture §5 |
| 3 | An older Verifier is not automatically a trusted one | applied: fixture screen at M1 before a seat judges real work; a newer model may take the seat after the same screen; "never newer" softened; tech stack §4.1 |
| 4 | Data class must propagate to derived text; the Scribe was capped at PROJECT yet saw the supervisor's context | applied in the simple form: the Scribe holds OPERATOR and writes only to the store and the person's surface; drafts for outside audiences are Executor work at PROJECT; the Commit card is the release act for class 2 and above; class 0 and 1 stated as a limit; architecture §3.1, §3.5. Classification propagation by rule is a todo, triggered only if the audit sample finds a leak |
| 5 | Ordinary work needs a fast path or Cori spends attention extravagantly | todo: task templates as standing grants, in architecture §7, built once the ordinary path shows which tasks recur |
| 6 | Audit passes only and you cannot measure false rejection | applied: a smaller fraction of failures and abstentions is sampled, each with its inclusion probability recorded; architecture §5 |

## From the prerequisites session

| # | Finding | Disposition |
|---|---|---|
| 1 | Any role can set a session variable | applied: the kernel mints read tokens into a table `context_ro` cannot touch; a policy resolves them; tech stack §3 |
| 2 | Row-level security does not apply to materialized views | applied: `context_ro` reads base tables through RLS and is never granted a materialized view; tech stack §3 |
| 3 | Snapshot ids no longer carry dates | applied: the seat file records `created_at` beside each id; tech stack §4.1 |
| 4 | Rulesets need a paid plan | resolved by the account moving to Pro; superseded by finding 7 |
| 5 | Host-only networking gives one host; a registry allowlist needs a proxy | applied: the worktree profile's network is the Mac only, dependencies are baked into the image from the lockfile; the proxy is a todo triggered by an Executor being blocked; tech stack §6 |
| 6 | The append-only trigger blocks the migrator too | applied: the destroy migration disables the trigger inside its own transaction; tech stack §3 |
| 7 | One human cannot satisfy a required review; builders under Tom's identity inherit the admin bypass | applied as a stated limit: the ruleset stops accidents and records merges; a builder identity with no bypass is a todo triggered by unattended builders; tech stack §10 |
| 8 | Secrets live in the Keychain and in a 1Password vault | applied: the vault is the durable copy; a service account is the path off this Mac; tech stack §10 |

## From Noam Brown

The frame that mattered: most multi-agent structure compensates for a limitation, and when the limitation goes the structure can go. Cori has a third kind of structure, which compensates for missing trust rather than missing context, and that kind stays.

| # | Insight | Disposition |
|---|---|---|
| 1 | Return-only workers guess or abort on ambiguity | applied: the `ask` tool; a question blocks the worker until the supervisor or the person answers; escalation rules and open questions removed from Brief and Report; tech stack §5, architecture §3.1 |
| 2 | Cold-start briefs compensate for lost context; fork instead | applied where the boundary is not deliberate: framing moves into the supervisor's own turn and the Framer class is gone; the Scribe forks the supervisor's rendered context. Declined for Executor and Verifier, where the brief is the data-class cap and the blindness; architecture §2, §3.5 |
| 3 | One norms document over N role definitions | applied: VOICE.md is the shared prior; a class prompt is a paragraph on top; classes stay as kernel facts; architecture §6 |
| 4 | Mandatory check-ins interrupt deep thought | applied: `steer` removed from the Worker port; the only messages into a running worker are an answer to its own question and abort; tech stack §5 |
| 5 | Agent count is a latency setting | already applied by the commit pass: one Executor per leaf; cited now as the reason |
| 6 | Hub-and-spoke routing is lossy | declined: with one Executor per leaf there are no peers; when there are, reports through the tree are the audit log, which is a trust structure |
| 7 | Swarming on coherence work | already applied: the supervisor frames and a single Planner decomposes before any fan-out |

## TODO index

Every TODO left in the documents, with the condition that triggers it:

- **Second provider for the Verifier seat** (architecture §5): the audit sample shows the Verifier passing work a stronger judge would fail. Added after Brown's point that a weaker judge underestimates the executor by construction; the seat is now justified by independence, not weakness.
- **Delegated Framer** (architecture §2): a framing that outgrows a turn.
- **Classification propagation by rule** (architecture §3.1): the audit sample finds OPERATOR-derived text in an artifact.
- **Fast path via task templates** (architecture §7): the ordinary path has shown which tasks recur.
- **Allowlisting proxy on the Mac** (tech stack §6): an Executor is blocked by a host the image cannot pre-install.
- **Builder identity with no bypass** (tech stack §10): builders run unattended.
- **Structure library** (architecture §3.2, DEFERRED): an objective needs more than one Executor per leaf.
- **Paraphrase before review and hidden criteria** (architecture §5, DEFERRED): a measured collusion case, or enough audit labels to compare.
