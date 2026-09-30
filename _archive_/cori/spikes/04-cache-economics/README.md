# Spike 04: Cache economics of a deterministic, volatility-ordered render

Closes tech-stack §8 (Context Builder) and §9 (cost hypotheses), spike 6 in §14.

## Question

If the system prompt is rendered deterministically from slices ordered by
volatility (persona, operator digest, objective roll-up, thread summary,
recent turns, inbox) with cache breakpoints after persona and after roll-up,
what fraction of input tokens over 20 consecutive turns is billed as cache
reads? What does the same content cost in a naive importance order? And what
is the smallest prefix Haiku 4.5 will cache?

## Method

- `render.py`: `render(state, order)` returns system text blocks, one per
  slice, with `cache_control` on the persona and roll-up blocks. The render is
  a pure function; the driver asserts the digest is identical on a second call.
- `slices.py`: deterministic synthetic prose, sized with the provider's own
  `count_tokens` (free) to persona 4,427 tokens, digest 804, roll-up 1,502,
  thread 414. Recent turns and inbox are small and change every turn.
- `run.py`, part A: six prefix sizes from 1,057 to 5,160 tokens, two calls
  each, read `cache_creation_input_tokens` and `cache_read_input_tokens`.
- Part B: 20 turns. Each turn the recent-turns window slides and the inbox
  counters change. The roll-up changes at turns 5, 10, 15 (a report landed),
  the digest changes once at turn 10 (a belief was added), the thread summary
  at turns 8 and 16. Run once in volatility order and once in the naive order
  `inbox, recent, rollup, thread, persona, digest` with the same breakpoint
  rule. Haiku 4.5, `max_tokens=5`, one-line user message.

Run: `./run.sh`. Spend for the whole spike: about $0.25.

## Numbers

Part A, minimum cacheable prefix on Haiku 4.5:

| Prefix tokens | Cached? |
|---|---|
| 1,057 / 2,135 / 3,185 / 4,026 | no (creation 0, read 0, silently) |
| 4,247 | yes (4,234 created, 4,234 read on call 2) |
| 5,160 | yes |

Threshold observed between 4,026 and 4,247, consistent with the documented
4,096 for Haiku 4.5. Newer models document 512 to 1,024.

Part B, 20 turns, 148,090 input tokens either way:

| Order | Cache reads | Cache writes | Uncached | Cost | vs no caching |
|---|---|---|---|---|---|
| volatility, two breakpoints | 81.9% | 9.3% | 8.8% | $0.0427 | 29% |
| naive importance, same breakpoints | 0.0% | 88.8% | 11.2% | $0.1814 | **122%** |

Per-turn shape in volatility order: turn 0 writes 6,743; turns 1 to 4 read
6,743 and pay about 550 uncached; turns 5, 10, 15 (roll-up changed) read
4,429 (the persona prefix survived) and write 2,314 (digest plus roll-up);
everything else reads the full prefix. Latency was flat at about 750 ms
either way except the turn-5 write at 2.0 s.

## Surprises

1. **Naive order with caching turned on costs more than no caching at all.**
   Every turn's prefix starts with the inbox, so nothing matches, and every
   turn pays the 1.25x write price on 6,600 tokens for a cache it never reads.
   "Turn on prompt caching for everyone" (tech-stack §4) is only a saving if
   the render is ordered; otherwise the gateway is charging a 22% premium.
2. **The two-tier breakpoint is worth having.** On the three turns where the
   roll-up changed, the persona breakpoint kept 4,429 tokens as reads and only
   the 2,314 tokens after it were rewritten. With a single breakpoint at the
   roll-up those turns would have rewritten all 6,743.
3. **The 4,096-token minimum on Haiku 4.5 makes "tiny slices" uncacheable.**
   A persona block under 4k tokens would silently never cache, with no error
   and no usage field saying why. The first breakpoint must sit at or past
   4,096 tokens on Haiku 4.5. On Sonnet 5 (1,024) and Opus 5 (512) the same
   render would cache at a fraction of the size. Which model the seat resolves
   to changes where the breakpoints are allowed to go.
4. **Cache reads do not count as uncached input**, so the 82% figure is also
   an 82% reduction in what counts against input-token rate limits on the
   Claude API.

## Not covered

- 1-hour TTL. All entries here were 5-minute; Cori's turns are minutes apart
  at most in a live session, but an idle Cori would pay a write on wake.
- Tool definitions in the prefix (they sit before `system` and must also be
  byte-stable).
- Fifty turns from a recorded event log, as spike 6 in the tech stack asks.
  Twenty synthetic turns were enough to see the shape.

## Recommendation

**Assumption holds, and the pass criterion (60% cache reads) is cleared with
room.** Replace the §9 hypothesis with: volatility-ordered render, two
breakpoints, 82% of input billed as cache reads, 29% of uncached cost over
20 turns on Haiku 4.5. Add two rules to §8: the first breakpoint must be at or
beyond the resolved model's minimum cacheable prefix (4,096 on Haiku 4.5,
1,024 on Sonnet 5, 512 on Opus 5), and the gateway should refuse or warn on a
request whose breakpoint prefix differs from the previous call's for the same
Brief, because unordered caching costs 22% more than none.
