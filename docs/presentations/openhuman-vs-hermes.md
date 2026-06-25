---
marp: true
theme: default
paginate: true
backgroundColor: #ffffff
color: #0d1117
style: |
  section {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: #ffffff;
    color: #0d1117;
    padding: 56px 64px;
    font-size: 22px;
    line-height: 1.5;
  }
  h1, h2, h3 {
    color: #0d1117;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  h1 { font-size: 2.0em; margin-bottom: 0.2em; }
  h2 { font-size: 1.5em; margin-bottom: 0.6em; border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }
  h3 { font-size: 1.1em; color: #1f2328; }
  a, strong { color: #0969da; }
  code, pre {
    font-family: "SF Mono", "Cascadia Code", "Fira Code", monospace;
    background: #f6f8fa;
    color: #0d1117;
    border-radius: 6px;
  }
  code { padding: 2px 6px; font-size: 0.88em; }
  pre { padding: 14px 18px; font-size: 0.78em; border: 1px solid #d0d7de; }
  blockquote {
    border-left: 4px solid #0969da;
    background: #ddf4ff;
    color: #0a3069;
    padding: 12px 18px;
    margin: 12px 0;
    font-style: normal;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 0.88em;
    margin: 8px 0;
  }
  th, td {
    border: 1px solid #d0d7de;
    padding: 8px 12px;
    text-align: left;
    vertical-align: top;
  }
  th { background: #f6f8fa; font-weight: 600; }
  section.lead {
    text-align: center;
    justify-content: center;
    background: linear-gradient(135deg, #ffffff 0%, #f6f8fa 100%);
  }
  section.lead h1 { font-size: 2.6em; }
  section.lead p { color: #57606a; font-size: 1.1em; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; margin-top: 10px; }
  .cols-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 16px; margin-top: 10px; }
  .stat {
    background: #ddf4ff; border-left: 4px solid #0969da;
    padding: 12px 20px; margin: 10px 0;
    font-size: 0.96em; font-weight: 600; color: #0a3069; line-height: 1.5;
  }
  .warn {
    background: #fff8c5; border-left: 4px solid #d29922;
    padding: 10px 18px; margin: 10px 0;
    font-size: 0.88em; color: #7d4e00; line-height: 1.5;
  }
  .path-card {
    border: 1px solid #d0d7de; border-radius: 6px;
    padding: 14px 16px; font-size: 0.86em; background: #f6f8fa;
  }
  .path-card strong { display: block; margin-bottom: 6px; color: #0d1117; font-size: 1.05em; }
  section::after { color: #8b949e; font-size: 14px; }
  ul, ol { margin-left: 1.2em; }
  li { margin-bottom: 4px; }
---

<!-- _class: lead -->
# OpenHuman vs. Hermes Agent
A side-by-side for agent builders
<br>
*Two open agent stacks. Same era. Very different bets.*

---

## Why this comparison

Two new self-hosted agent stacks landed in the same window. Both claim "persistent memory," "tool use," and "autonomous skill." Both ship public docs and code.

But they are not competing for the same problem.

<div class="stat">
OpenHuman is a desktop app for one user's digital life. Hermes is a headless framework you deploy on a VPS and pipe through 20 chat platforms.
</div>

If you mix them up at the architecture stage, you waste a quarter.

---

## At a glance

|  | **OpenHuman** | **Hermes Agent** |
|---|---|---|
| **Made by** | tinyhumans | Nous Research |
| **Form factor** | Desktop app (Tauri) | Headless framework |
| **Primary surface** | GUI + Obsidian vault | CLI + messaging gateway + ACP/IDE |
| **Stack** | Rust core + React UI | Python (`run_agent.py`, `AIAgent`) |
| **Storage** | SQLite + Markdown files | SQLite + FTS5 |
| **Distribution** | One-user install, OAuth onboarding | Self-hosted, 6 execution backends |
| **License** | GNU GPL3 | Open (Nous Research) |

---

## How each one thinks about "memory"

<div class="cols">
<div>

### ![w:28](diagrams/logo-obsidian.svg) OpenHuman — Memory Tree
A deterministic ETL pipeline.

1. OAuth pulls from 118+ sources every 20 min
2. Normalize to provenance-tagged Markdown
3. Chunk into ≤3k-token segments
4. Score + entity-extract in background
5. Fold into per-source / per-topic / per-day summary trees
6. Browse in Obsidian. Edit by hand.

> "No vector-soup black box."

</div>
<div>

### Hermes — Curated + Dialectic
Two layers, both agent-driven.

1. **Agent-curated memory** — the agent decides what to persist, nudged periodically
2. **Honcho dialectic modeling** — separate service builds a user profile across sessions
3. **FTS5 recall** over session SQLite, with LLM summarization on retrieval
4. **Skills** — procedural memory, agentskills.io standard, self-improving

> Memory is something the agent earns, not something piped in.

</div>
</div>

---

## OpenHuman architecture

```
+-------------------------------------------------------+
|  Tauri Shell  (windowing, OS integration, lifecycle)  |
+-------------------------------------------------------+
|  React Frontend  <-- JSON-RPC -->  Rust Core          |
+-------------------------------------------------------+
                                |
        +-----------------------+-----------------------+
        |                       |                       |
   Integrations          Memory Tree              Model Router
   (118+ OAuth)          - chunk                  - frontier vs cheap
   - 20-min sync         - score + embed          - TokenJuice
   - Gmail, Slack...     - summary trees          - compresses tool out
                         - SQLite + .md vault
```

One process. One user. One machine (with cloud-backed inference).

---

## Hermes architecture

```
   CLI    Messaging Gateway (20 platforms)    ACP / IDE
     \              |                            /
      +-------------+----------------------------+
                    |
              AIAgent (run_agent.py)
                    |
   +----------+-----+------+-----------+--------------+
   |          |            |           |              |
 Prompt   Provider     Tool       Session         Skill
 System   Resolver     Registry   Storage         System
 - personas (18+ APIs) (70+ tools) (SQLite+FTS5)  (agentskills.io)
                          |
              6 terminal backends:
       local | Docker | SSH | Daytona | Modal | Singularity
```

Many entry points. Many backends. One `AIAgent` class.

---

## Strengths

<div class="cols">
<div class="path-card">
<strong>OpenHuman</strong>

- Transparent memory you can <em>read and edit</em>
- Onboarding is OAuth-easy
- Local-first; Ollama option for privacy
- TokenJuice keeps long-history costs sane
- Native desktop affordances (mascot, meeting agent, voice)
- Audit trail is just Markdown files
</div>
<div class="path-card">
<strong>Hermes Agent</strong>

- Runs <em>headless</em> on $5 VPS or GPU cluster
- Closed learning loop — skill creation + self-improvement
- 20+ chat platforms from one gateway
- MCP-compatible tool ecosystem
- Atropos hook for RL trajectory export
- Container isolation, command approval built in
</div>
</div>

---

## Robustness — where the rubber meets the road

|  | **OpenHuman** | **Hermes Agent** |
|---|---|---|
| **Failure domain** | Single desktop process | Per-session, per-backend isolated |
| **Sandboxing** | Tauri process boundary; tools run in-process | Docker / Singularity / Modal containers |
| **State recovery** | SQLite + flat Markdown — trivially backupable | SQLite + FTS5 with lineage across compressions |
| **Prompt stability** | Implicit (single user, single session) | Explicit guarantee — system prompt frozen mid-conversation |
| **Multi-user** | No — one human, one install | Yes — per-platform isolation, user authorization |
| **Blast radius** | Your laptop | A container, a VPS, or nothing |

<div class="warn">
OpenHuman's robustness story is "your filesystem is the backup." Hermes' is "everything runs in something you can kill."
</div>

---

## Ideal use cases

<div class="cols-3">
<div class="path-card">
<strong>Pick OpenHuman if</strong>
You are one knowledge worker, you want a second brain across Gmail / Slack / GitHub / Notion, and you want to <em>see</em> what the agent remembers.
</div>
<div class="path-card">
<strong>Pick Hermes if</strong>
You are deploying an always-on agent that talks to many users on Telegram / Discord / Slack, runs jobs on remote infra, and you want skills to compound over time.
</div>
<div class="path-card">
<strong>Pick neither if</strong>
You need a tightly-scoped coding agent inside an IDE (use Claude Code / Cursor) or a single API call wrapper (use the SDK directly).
</div>
</div>

---

## The honest trade-off

<div class="cols">
<div>

### OpenHuman bets on…
**Transparency over autonomy.**

The agent is bounded by what you let it see, and you can audit every chunk it reasons over. Skills are not really a concept — the system is the skill.

The cost: it scales to one user, on one machine, with one cloud subscription handling inference.

</div>
<div>

### Hermes bets on…
**Autonomy and reach over transparency.**

Skills are first-class, self-improving, and shareable. Twenty chat platforms. Six execution backends. RL training hooks.

The cost: more moving parts, more services to operate, and "what does the agent know" is a query, not a folder.

</div>
</div>

---

## Takeaways for an agent builder

<div class="stat">
1. <strong>Memory shape follows form factor.</strong> Desktop apps can afford a Markdown vault. Headless multi-tenant agents need indexed session stores with lineage.
</div>

<div class="stat">
2. <strong>Sandboxing is a deployment choice, not a feature.</strong> Hermes ships six backends because operators need that range. OpenHuman ships one because there is one operator.
</div>

<div class="stat">
3. <strong>"Self-improving skills" is the live experiment.</strong> Hermes is betting the agentskills.io ecosystem becomes a thing. Worth watching even if you don't adopt.
</div>

---

<!-- _class: lead -->
# Questions?

OpenHuman: [tinyhumans.gitbook.io/openhuman](https://tinyhumans.gitbook.io/openhuman)
Hermes: [hermes-agent.nousresearch.com/docs](https://hermes-agent.nousresearch.com/docs)
