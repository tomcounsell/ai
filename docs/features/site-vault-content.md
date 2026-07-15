# Site Vault Content

<!-- This file is being built incrementally across the vault-site-integration plan (#2084).
     The AC6 decision below lands first; Research/Overview/persona documentation is added
     by a later task (document-site) in the same plan. -->

## AC6 Decision: "Shipped This Week" Feed

**Recommendation: no-build.** Do not build a public "shipped this week" feed as part of
this plan. Neither candidate source is fit to drive a zero-review public widget today, and
the ongoing upkeep cost outweighs the marginal value of a vanity feed. If the idea is
revisited later, it should spin off its own issue rather than expand this plan's scope.

**What `daily-logs/` actually is.** The vault's `daily-logs/` holds machine-generated
aggregator output, not curated narrative. As of this evaluation there are five files
(2026-05-02 through 2026-05-06) and the newest is roughly ten weeks stale — the aggregator
has not produced a log since early May, so any feed sourced from it would silently go dark.
Volume is wildly uneven: one day is 42 KB, another is a 73-byte "(No system activity
recorded)" stub. The content is raw and leaky: an `## Aggregator Notes` block full of
`[ERROR: gh:...]` GraphQL and token-policy failures that name internal orgs and repos
(`yudame`, `chainstarters/gato`, `yudame/pba.ai`), followed by unfiltered commit dumps —
raw SHAs, dependency-bump churn, and repeated "Plan revision" commits. None of this is
structured or clean enough to auto-summarize into something we'd want on a public site
without a heavy curation and redaction pass every single time.

**What `/weekly-review` already produces.** The `weekly-review` skill is the stronger
source: it is purely git-based, and its output is deliberately stakeholder-facing — named
categories with plain-language bullets, contributor stats, and an explicit "would a product
manager or executive understand this?" test that strips jargon, code paths, and file
references. Its format could plausibly feed a site widget with modest formatting work. The
catch is that it is not a deterministic pipeline — it is an interactive skill whose Phase 2
is an LLM analysis pass Claude runs on demand. Wiring it to a public feed means standing up
a *new* recurring automated job to invoke that pass on a schedule; the skill itself is not
that job.

**The upkeep cost.** A "shipped this week" feed forces a choice between two bad options.
(a) A recurring automated job that publishes to a public site with zero human review — which
must stay correct and non-embarrassing indefinitely. Given that even the cleaner source rides
on an LLM summarization pass, and the raw source leaks internal repo names and error text, the
failure mode is publishing something wrong, stale, or embarrassing with no one in the loop.
(b) Manual curation before each publish — which directly competes for attention with the
actual engineering work the feed would report on, and predictably lapses (the daily-logs
already went dark for ten weeks).

**Conclusion.** The value is a low-stakes vanity feed with no clearly-defined audience; the
cost is a standing correctness-and-embarrassment liability on a public surface, or recurring
manual toil. Recommendation stands: **no-build now.** Revisit only as a separate,
appropriately-scoped issue — and if pursued, gate it on `/weekly-review` output that a human
approves before it goes live, rather than any unattended pipeline over `daily-logs/`.
