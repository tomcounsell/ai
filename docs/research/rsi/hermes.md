# Hermes Agent (Nous Research): RSI notes

Researched 2026-10-20. Inspiration only, no Valor proposal. "S" = source states; "I" = my inference.

**Which Hermes.** Two systems share the name. Hermes Agent is an MIT-licensed self-hosted agent runtime (github.com/NousResearch/hermes-agent, tagline "The agent that grows with you", ~255k stars per the repo page). The Hermes model family (Hermes 3/4) is a separate project. Agent docs say any OpenAI-compatible endpoint works and the models and runtime are separate. This note covers the agent. Release dates were not on the repo page. Third-party posts place the agent in early 2026 (v0.8 to v0.10 by April-May 2026).

## 1. What it improves
S (docs: https://hermes-agent.nousresearch.com/docs/user-guide/features/skills, .../memory):
- **Skills**: Markdown SKILL.md files (agentskills.io-compatible), "procedural memory the agent creates and reuses". Tool `skill_manage` with create/patch/delete/write_file/remove_file. Targeted `patch` is preferred over rewrite.
- **Memory**: two small files, MEMORY.md (2,200 chars) and USER.md (1,375 chars), injected into the system prompt once at session start. Plus a SQLite FTS5 index of all past sessions (`state.db`), raw messages, no LLM calls.
- **User model**: optional Honcho plugin (Plastic Labs). Dual peer (user, AI), a "peer card" of key facts, and a periodic multi-pass "dialectic" reasoning call (https://honcho.dev/docs/v3/guides/integrations/hermes).
- **Prompts, tool descriptions, code (offline)**: separate repo hermes-agent-self-evolution (open-sourced ~2026-06-06 per a DEV post) uses DSPy + GEPA to evolve skills, tool descriptions, system prompts; code via an AGPL external tool. Skills are Tier 1 (https://github.com/NousResearch/hermes-agent-self-evolution, PLAN.md).
- **Models**: not improved by the agent in the loop. The repo offers batch trajectory generation/compression for training tool-calling models (S). Sources disagree whether trajectories flow automatically into Atropos RL (a DEV post says yes; a docs mirror says it only exports ShareGPT traces). Unverified.

## 2. Triggers and evaluation
S:
- **Skill creation**: qualitative prompt triggers, no counters: a non-trivial multi-step workflow, errors or dead ends that led to a working path, and user corrections. `/learn` is the user-initiated version.
- **Background review**: after a turn, a review pass may save memory or patch skills. Runs on the main model by default; `auxiliary.background_review` routes it to a cheaper model on a conversation digest. On the local runtime reviews are deferred until the machine is idle (max age 1800s). The user sees a one-line notice (`display.memory_notifications`).
- **Curator** (.../features/curator): runs on idle plus interval (default every 7 days, after 2h idle). Without any LLM: unused 14 days -> stale, 30 days -> archived (recoverable). Optional LLM pass (off by default) merges overlapping skills into umbrellas. Usage counters (`view_count`, `use_count`, `patch_count`) in `.usage.json`. Only agent-created skills are touched; pinned, hub and hand-written skills are exempt. Tar snapshot before consolidation, audit ledger, `rollback`, `--dry-run`.
- **Evaluation of whether it worked**: in the runtime loop, effectively none beyond usage counts and the human. There is no outcome score for a skill. An advisory linter (incident-log-shape, references-sprawl, oversized-body) warns, never blocks. The only real evaluator is offline: the self-evolution repo scores variants on synthetic or session-history evals, requires a full pytest pass and size caps (skill <=15KB), and ships the winner as a PR for human review. Cost stated: ~$2-10 per optimization run, API only.
- I: the runtime loop is "write on a heuristic, prune on disuse"; quality gating is separated into a slower offline, PR-reviewed path.

## 3. Growing around a user or community
S:
- Per-user: USER.md plus Honcho peer card/representation; FTS5 recall of past chats; SOUL.md identity per "profile" (each profile has its own config, memory, gateway, cron). One gateway serves Telegram, Discord, Slack, WhatsApp, Signal, CLI.
- Memory budget is tiny and hard: writes over the limit error and the agent must consolidate in one batch. No auto-compaction. Mid-session writes show up only next session (snapshot is frozen to keep the prompt stable).
- Community: skills hub with sources (official, skills-sh, github, clawhub, lobehub, url ...), `hermes skills publish`, "taps" (any GitHub repo with `skills/`), trust levels builtin/official/trusted/community. Lock file records source URL, content hash, scanner version. Hub updates compare hashes; locally edited skills are skipped unless `--force`.
- Feedback signals: user corrections are an explicit skill trigger (S). No thumbs/rating signal was found in docs (could not verify absence).

## 4. New capabilities, resources, money
S:
- Capabilities: MCP servers, plugins, hub skills, the agent writing its own skills. Subagents with configurable provider/model/limits. Cron scheduler delivers results to any chat platform.
- Compute: seven terminal backends (local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox); serverless ones cost "nearly nothing" idle.
- Money: no agent-controlled spending or budget feature found. Nous Portal subscription (400+ models) bundles web search, image gen, TTS and browser tools via a "Tool Gateway" (since v0.10.0, per a search summary). Cost is by user choice of provider; docs have no budget section. The agent does not earn or acquire resources on its own (as far as found).

## 5. What failed, drifted, was abused
- **Skill poisoning**: a reviewer calls it "prompt injection with a save button": one injected session writes a skill that later loads as trusted context. No signed provenance, no way to tell agent-written from dropped-in skills (https://kisztof.medium.com/hermes-agent-review-nous-researchs-self-improving-ai-agent-e72bc244435a; fetch returned 403, so from search summary only).
- **Scanner bypasses** (GitHub issues, titles only verified via search): #7072 a community skill passed with 0 findings and exfiltrated env vars via dynamic import; #8884 skill DESCRIPTION.md was unscanned and went verbatim into the system prompt. Docs' answer: hub scanner, `skills.write_approval` (stage agent writes in `~/.hermes/pending/skills/`, `/skills approve|diff`), `memory.write_approval`.
- **Audit/CVEs**: independent audit 2026-04-11 (@Anic888) reported 4 critical / 9 high in default config: regex-guarded shell, no read deny list, approvals skipped in containers (via CSA note, https://labs.cloudsecurityalliance.org/research/csa-research-note-hermes-agent-cves-20260504-csa-styled/). CVE-2026-7396, -7397 (v0.8.0), -6829 (WebUI) are moderate. Not independently checked against CVE records. The CSA note's "9 CVEs in 4 Days" headline is about OpenClaw, not Hermes.
- **Quality drift** (secondary, via Cloudways/eesel summaries): auto-patches sometimes worse than the original; a formatting quirk absorbed as a "user preference"; agent "always thinks it did a good job"; MEMORY.md fills and needs manual consolidation; FTS5 misses synonyms; curator does not undo a bad patch.
- **PAST-Bench** (arXiv 2608.04003, ~2026-08; PDF unreadable by my fetcher, so only a search summary): Hermes sometimes stores procedures as transcript text or near-duplicates, so no ranked skill exists later; under noisy prompts it may act irreversibly before persisting. Treat as unverified.
- **What builders said they got wrong**: I found no postmortem or retrospective by Nous. Their admissions are implicit in features added later (curator, write_approval, lint rules, size caps, memory scanning). Docs reportedly admit skills drift as projects change (via Cloudways, unverified). The "~40% faster on research tasks" claim is third-party and unreplicated.

## 6. Distinctive, borrowable ideas (observations only)
1. Two-speed learning: cheap online writes (patch, small memory) and a slow offline optimizer whose output is a PR gated by tests.
2. Usage counters per skill (view/use/patch) make pruning evidence-based; disuse archives, never deletes, with snapshot, ledger and rollback.
3. Hard tiny memory budget forces consolidation instead of growth; frozen-at-session-start snapshot keeps prompts stable.
4. Provenance split: agent-created vs hand-written vs hub skills get different rights; curator only touches the first.
5. Idle-deferred background review on a cheaper model, with a visible one-line "I saved X" message in chat.
6. Optional write-approval staging with `diff` for agent-authored skills; skill content scanned the same as untrusted input.
7. Honcho-style peer card: a short, no-LLM-call fact sheet per user, plus a slower reasoning pass on a cadence.
8. Cautions (from its failures): no outcome signal means self-praise; persistent files are an injection persistence layer; scanners by regex fail.

## Not verified
Release dates and version history; exact nudge intervals (docs name but do not give them); PAST-Bench and arXiv 2609.16669 contents (PDFs unreadable); the Medium review (403); GitHub issues' bodies; whether Atropos RL is wired to the agent; any Nous retrospective; any thumbs/rating feedback; star and fork counts are from one fetch.
