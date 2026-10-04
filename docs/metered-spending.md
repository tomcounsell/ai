# Metered spending

This document is the metering section of [architecture.md](architecture.md),
moved out to keep that file short. Terms are defined there.

**Built.** Every model call is metered and its price recorded on its task.
The gateway is a local HTTP proxy with a per-turn token in the path and two
routes: Anthropic's Messages API (`route: gateway`) and OpenAI's Responses
API under `openai/` (`route: openai`). For every call it:

1. prices the model from the table in `core/settings.py`, each price
   carrying the day it was checked (`price_checked` on the charge row); an unpriced model is refused. An Anthropic dated id matches its undated entry; an OpenAI id matches only exactly or with a `-YYYY-MM-DD` suffix. Per-million charges round up to whole micro-dollars in integers;
2. opens the call with a `gateway.opened` row (call id, turn id, model, route, estimate), refusing with a `gateway.refused` row of reason
   `stopped` if the task is stopped; nothing else refuses a call;
3. forwards the call with the kernel's own credential (harnesses.md, Metering
   through the gateway), streaming the response back unchanged and reading the
   provider's reported usage as it passes;
4. charges what the provider reported in a `gateway.charged` row. A call
   cut before its usage arrives is charged the worst-case estimate (every
   input token at the most expensive input rate plus every output token it
   was allowed), so the ledger never records less than the invoice; the
   estimate is only that fallback charge, never a gate.

The OpenAI route sends the kernel's key, or the turn's own (`credential:
turn`) when the kernel holds none, only as `POST v1/responses` and `GET` or
`HEAD` on `v1/models` and one model by id; any other path or method is a 403.
It drops the turn's `proxy-authorization` and answers an upstream 401 with its
own body naming the key refused. It charges at the reported tier (one the
table lacks, at the highest, `tier_unpriced`) with cached, cache-write,
long-context, and per-search rates; an unpriced model, tool, or stored prompt
is a 400 with no row. Content referenced by id or URL, or a hosted tool
without `max_tool_calls`, can leave a cut call short of the bill
(`referenced`, `bounded: false`). Anthropic searches are charged per search; its code execution is unmetered.

Metered spending is always derived from the ledger, by one fold
(`tasks.spending`) that `status` shows as `Metered spending: $X`: the sum of
the task's charges, with the calls still open (`core/spending.py`).

Because the harness's base URL points at the gateway, every call the turn
makes passes through it, Claude Code's own side calls and any subagents it
starts included. In the first demonstration 69 calls were metered at $2.972649
against a harness-reported $2.972625 (rebuild-demonstration.md, Money): the
meter sees what Claude Code spends on its own account.

Serves bounded authority, metered spending. Money never refuses, pauses,
or stops a task, and nothing asks Tom because of it; the metering sees what
passes the gateway and is not a wall around the provider (see Limits).
Ledgers written before 2026-10-03 hold `gateway.reserved` rows, which the
folds read as `gateway.opened`, and rows the folds ignore. Judgement calls
are opened and charged the same way in the kernel process, with no HTTP
route (judgement-layer.md).

A child's spending rolls up into its parent's reported spending:
`tasks.tree_spending` sums every charge in a task's subtree, and `status`
shows it beside the task's own (architecture.md, The objective tree).

**Design.** A task that ends reports what it spent,
what it produced, and what it asks for. A hung tool spends no money, so a
per-task wall-clock deadline catches what metering cannot.
