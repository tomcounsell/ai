---
status: done
---

# A1 rollout record

The rollout of `a1-autonomous-act.md`.

Merged as d47d8372c (C4 e3865fa6d beneath it). The lead booted out the
kernel, ran `migrate`, and started the kernel again (PID 57185). The
migration refused two held effects: f10a2751760c (task 75c0902b6e25) and
481509881e10 (task f917b77bfdd3, a candidate since replaced). Task
75c0902b6e25 requested its merge afresh and merged 1cfb600076a5 into its own
origin with no tap; its report notice is requested. No bridge-owned
`release.requested` row was left with no outcome, so no stale send waits.
The Telegram and email bridge jobs stay unloaded until the live window.
