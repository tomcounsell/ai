# Principal Context — Tom Counsell

This file encodes the operating context of the principal (supervisor) so the
agent system can make directionally correct decisions without constant check-ins.
Structured loosely on the TELOS framework: Mission → Goals → Beliefs → Strategies
→ Challenges → Narratives → Projects → Learned.

---

## Mission

Build autonomous AI coworker systems that ship real software — proving that a
solo technical founder can operate at the output level of a small engineering
team by delegating execution to AI agents. The endgame is not "AI assistance"
but genuine agency: agents that own work end-to-end, self-correct, and improve
over time.

The broader bet: the first generation of people who figure out how to reliably
delegate knowledge work to AI agents will have a compounding advantage. This
system (Valor) is both the product and the proof of concept.

---

## Goals (6-12 Month Horizon)

1. **Valor as a reliable coworker** — Valor should be able to receive a work
   request via Telegram, plan it, build it, test it, and open a PR with minimal
   human steering. The metric is "how many sessions complete without manual
   intervention." Current state: works for small/medium tasks, still needs
   babysitting for larger features.

2. **Multi-project throughput** — Use Valor across the full project portfolio
   (Popoto, Django Template, PsyOPTIMAL, Flutter Template, Cuttlefish, Research)
   not just on the Valor repo itself. The system should context-switch between
   projects cleanly.

3. **Revenue path for yudame** — At least one of the portfolio projects
   (likely PsyOPTIMAL or a productized version of the agentic tooling) needs to
   move toward generating revenue. Valor's job is to multiply Tom's capacity so
   he can pursue multiple opportunities simultaneously.

4. **Reduce Tom's review burden** — Better validation loops, test coverage, and
   self-healing so Tom reviews fewer PRs manually and trusts the automated
   quality gates more.

5. **Open-source credibility** — Popoto and the Django/Flutter templates serve
   as portfolio pieces and community tools. They should be well-maintained,
   well-documented, and actively improved.

<!-- TOM: Are there specific revenue targets or timeline pressures I should know
about? Is PsyOPTIMAL the priority product, or is there another candidate? -->

---

## Beliefs (Working Assumptions)

- **AI agents will replace junior/mid dev capacity within 2 years.** Building
  the orchestration layer now is a strategic investment, not a hobby project.

- **System > prompt.** A well-designed system (SDLC pipeline, validation loops,
  session management) produces better results than clever prompting alone. The
  agent infrastructure matters more than any single interaction.

- **Ship small, ship often.** Favor many small PRs with clear scope over large
  ambitious branches. This is both a development philosophy and a practical
  necessity when the "developer" is an AI that works best with bounded tasks.

- **Premium compute is worth it.** Using Opus over Haiku for complex reasoning
  is a valid tradeoff. The time saved and quality gained justifies the cost.
  (Explicit in SOUL.md wisdom section.)

- **Autonomy requires trust infrastructure.** You can't just tell an agent to
  "be autonomous" — you need test suites, validation hooks, crash recovery,
  session monitoring, and escalation paths. Autonomy is earned through
  engineering, not prompting.

- **Open source is leverage.** Publishing tools and templates builds reputation,
  attracts contributors, and forces higher code quality standards.

<!-- TOM: What's your risk tolerance on AI spending? Is there a monthly budget
ceiling, or is it purely ROI-driven? -->

---

## Strategies

1. **Build the system that builds the system.** Invest disproportionately in
   Valor's own infrastructure (SDLC pipeline, session management, self-healing)
   because every improvement compounds across all projects.

2. **Parallel execution over serial.** Use the P-Thread pattern and multi-project
   routing to run multiple workstreams simultaneously rather than completing one
   project before starting another.

3. **Validation loops over review.** Automate quality checks (tests, linting,
   build verification) so human review is about strategic direction, not catching
   bugs. The Ralph Wiggum Pattern from SOUL.md.

4. **Progressive trust delegation.** Start with small tasks, build confidence
   through demonstrated reliability, then delegate larger work. Track which task
   types succeed autonomously and which still need steering.

5. **Telegram as the control plane.** Keep the human interface lightweight —
   Telegram messages, not dashboards or web UIs. The agent reports outcomes;
   Tom steers with short messages. Minimize context-switching cost for Tom.

---

## Challenges

- **Solo founder bandwidth.** Tom is the only human reviewer. Every PR that
  needs manual review is a bottleneck. The system must minimize review burden
  while maintaining quality.

- **Agent reliability plateau.** Large features and cross-cutting changes still
  fail or produce low-quality output. The gap between "works for small tasks"
  and "works for real features" is significant.

- **Context window limits.** Complex tasks exceed what a single agent session
  can hold. Session continuity, worktree isolation, and context compression are
  ongoing engineering challenges.

- **Multi-machine deployment.** Valor runs on multiple machines (at least
  valorengels and tomcounsell). Keeping them in sync, handling path differences,
  and managing the update process adds operational overhead.

- **Revenue timeline pressure.** Building infrastructure is valuable but doesn't
  generate revenue directly. There's an implicit tension between improving Valor
  and shipping product features that could generate income.

<!-- TOM: What's the biggest bottleneck right now from your perspective? Is it
Valor's reliability, your own review bandwidth, or something else entirely? -->

---

## Narratives

The story that motivates the work:

**"One person with the right AI system can outperform a team."** This is not
about replacing developers — it's about a new model of software creation where
the human provides direction, taste, and judgment while AI agents handle
execution at scale. Tom is building the proof that this model works, and the
tools themselves are the first product.

The Valor system is simultaneously:
- A **personal productivity multiplier** (getting more done across projects)
- A **proof of concept** (demonstrating agentic development workflows)
- A **potential product** (the tooling and patterns could be packaged)
- A **portfolio piece** (demonstrating technical capability to the market)

---

## Projects (Active Portfolio)

| Project | Strategic Role | Priority Signal |
|---------|---------------|-----------------|
| **Valor AI** (this repo) | Core infrastructure — everything else depends on it | auto_merge: true, highest investment |
| **Popoto** | Redis ORM — used internally, open-source credibility | auto_merge: false (more careful) |
| **PsyOPTIMAL** | Revenue candidate — mental health platform | auto_merge: false |
| **Django Project Template** | Open-source template, community tool | auto_merge: true |
| **Flutter Project Template** | Open-source template, cross-platform | auto_merge: true |
| **Cuttlefish** | AI tooling / MCP servers — supports Valor ecosystem | auto_merge: true |
| **Yudame Research** | Research outputs, podcasts, educational content | auto_merge: true |

**Inferred priority order:** Valor > PsyOPTIMAL > Popoto > Templates > Others

<!-- TOM: Is the priority order above correct? Are any projects on hold or
deprioritized that I should know about? -->

---

## Learned (Accumulated Lessons)

These are patterns observed from how the system has evolved:

1. **Hooks and validators catch more bugs than code review.** The investment in
   pre-commit hooks, plan validators, and build verification gates has been
   consistently high-ROI.

2. **Session isolation was a turning point.** Before worktrees and task list
   scoping, parallel work was fragile. The two-tier isolation model (thread-scoped
   and slug-scoped) unlocked reliable concurrent execution.

3. **The summarizer is critical infrastructure.** It's not just formatting — it's
   the interface between agent work and human attention. Getting summarization
   wrong means Tom either misses important information or gets buried in noise.

4. **Self-healing beats monitoring.** The bridge watchdog and crash recovery
   system means Tom doesn't need to babysit uptime. Invest in automatic recovery
   over dashboards.

5. **Daily reflections surface real issues.** The autonomous maintenance process
   catches problems (stale code, log anomalies, test degradation) that would
   otherwise accumulate silently.

6. **Plan documents are the highest-leverage artifact.** A good plan doc means
   the build phase mostly works. A bad plan doc means the build phase wastes
   compute and produces garbage. Invest in plan quality.

---

## How This File Should Be Used

This file provides the "why" behind the "how" in CLAUDE.md and SOUL.md. When
the agent needs to make a judgment call — prioritize between projects, decide
how much effort to invest in a task, choose between a quick fix and a proper
solution — this file provides the strategic context.

**Key decision points where this context matters:**
- Triaging work requests (which project gets attention first?)
- Scoping features (minimal viable vs. thorough implementation?)
- Escalation decisions (is this worth interrupting Tom?)
- Resource allocation (use Opus or Haiku for this task?)
- Communication tone (how much detail does Tom want?)
