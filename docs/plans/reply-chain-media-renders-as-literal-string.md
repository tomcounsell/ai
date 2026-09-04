---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-04
tracking: https://github.com/tomcounsell/ai/issues/2732
last_comment_id: none
---

# Reply-Chain Media Renders As The Literal String `[media]`

## Problem

<!-- skeleton -->

## Freshness Check

<!-- skeleton -->

## Prior Art

<!-- skeleton -->

## Research

<!-- skeleton -->

## Spike Results

<!-- skeleton -->

## Data Flow

<!-- skeleton -->

## Why Previous Fixes Failed

<!-- skeleton -->

## Architectural Impact

<!-- skeleton -->

## Appetite

<!-- skeleton -->

## Prerequisites

<!-- skeleton -->

## Solution

<!-- skeleton -->

## Failure Path Test Strategy

<!-- skeleton -->

## Test Impact

- [ ] `tests/unit/test_context_helpers.py::TestReplyThreadContextHeader::test_format_reply_chain_uses_the_constant` — UPDATE: chain dicts gain a `media` key; the fixture must keep passing with and without it.
- [ ] `tests/unit/test_context_helpers.py::test_format_reply_chain_drops_variation_selector_and_backtick_echo` — UPDATE: confirm sanitisation still applies to the composed caption-plus-descriptor line.
- [ ] `tests/unit/test_context_helpers.py::test_format_reply_chain_omits_messages_below_length_floor` — UPDATE: the length floor must measure the human-authored text, not the synthetic descriptor.
- [ ] `tests/integration/test_steering.py` (reply-chain timeout guards, ~lines 45-80, 1290-1310) — UPDATE: the two `asyncio.wait_for(fetch_reply_chain(...))` guards keep the same 3.0s constant; assertions must survive the signature change.
- [ ] `tests/integration/test_private_tag_ingestion.py` (lines 130, 171) — UPDATE: `strip_private` must still cover the descriptor text spliced into the chain block.

## Rabbit Holes

<!-- skeleton -->

## Risks

<!-- skeleton -->

## Race Conditions

<!-- skeleton -->

## No-Gos (Out of Scope)

<!-- skeleton -->

## Update System

<!-- skeleton -->

## Agent Integration

<!-- skeleton -->

## Documentation

- [ ] Update `docs/features/reply-thread-context-hydration.md` with a section describing how a chain ancestor carrying media is rendered (resolved / unresolved / caption-plus-attachment).
- [ ] Update `docs/features/media-enrichment.md` to state that chain ancestors get a path reference, never an AI enrichment pass, and why.
- [ ] Add inline docstrings on the new descriptor builder and renderer in `bridge/context.py`.

## Success Criteria

<!-- skeleton -->

## Team Orchestration

<!-- skeleton -->

## Step by Step Tasks

<!-- skeleton -->

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `.venv/bin/python -m pytest tests/unit/test_context_helpers.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

<!-- skeleton -->
