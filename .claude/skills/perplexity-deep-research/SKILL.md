---
name: perplexity-deep-research
description: Automate Perplexity Deep Research API calls using sonar-deep-research model. Supports sync (blocking) and async (fire-and-poll) modes. Use for Phase 1 academic research in podcast episodes. Returns research ready to paste into research-results.md.
---

# Perplexity Deep Research API Automation

This skill automates research using Perplexity's Deep Research API with both synchronous and asynchronous modes.

## Overview

The Perplexity Deep Research API provides programmatic access to comprehensive research:
1. Conducts multi-step research process
2. Searches across academic databases, official sources, peer-reviewed journals
3. Synthesizes findings with proper citations
4. Returns structured markdown-formatted reports

**Modes:**
- **Sync (default):** Blocking call, 30-120s typical, up to 10 minutes. Retries on failure.
- **Async:** Fire-and-poll. Submit returns immediately with job ID. Poll for results. No client-side timeout. Results stored 7 days.

**Output:** Comprehensive research report with inline citations and source links. Results are automatically saved to timestamped files. Metadata sidecar JSON includes citations, cost breakdown, and search results.

**Focus Areas:**
- Academic studies and peer-reviewed papers
- Meta-analyses and systematic reviews
- Official government/regulatory sources
- Authoritative industry reports

## Prerequisites

- Perplexity API key in repository `.env` or `/Users/valorengels/.env`
- Python 3.x with `requests` and `python-dotenv` installed
- API key from: https://www.perplexity.ai/settings/api

## API Key Setup

**Check if API key exists:**

```bash
grep PERPLEXITY_API_KEY .env 2>/dev/null || grep PERPLEXITY_API_KEY /Users/valorengels/.env 2>/dev/null || echo "PERPLEXITY_API_KEY not found"
```

If not found, add to repository `.env` file (preferred) or global `.env` file:

```bash
echo 'PERPLEXITY_API_KEY=pplx-your-api-key-here' >> .env
```

## Complete Automation Workflow

### Step 1: Verify API Key

```bash
grep PERPLEXITY_API_KEY .env 2>/dev/null || grep PERPLEXITY_API_KEY /Users/valorengels/.env 2>/dev/null || echo "PERPLEXITY_API_KEY not found"
```

### Step 2: Prepare Research Prompt

**Prompt format (3 lines, single newlines):**
```
Research [TOPIC].
Focus on peer-reviewed studies, meta-analyses, systematic reviews, and official government/regulatory sources.
Provide comprehensive findings with full citations, sample sizes, methodological details, and source URLs.
```

### Step 3: Run Research

**Synchronous (default — blocking, waits for result):**

```bash
cd /Users/valorengels/src/cuttlefish/apps/podcast/tools
python perplexity_deep_research.py \
  --file ../pending-episodes/[episode-dir]/prompts.md \
  --output ../pending-episodes/[episode-dir]/research/p2-perplexity.md
```

**Async (fire-and-poll — no blocking, no client-side timeout):**

```bash
# Submit and wait for result
python perplexity_deep_research.py --async \
  --file ../pending-episodes/[episode-dir]/prompts.md \
  --output ../pending-episodes/[episode-dir]/research/p2-perplexity.md

# Submit and return immediately (fire-and-forget)
python perplexity_deep_research.py --no-wait "Research prompt here"
# Output: Job ID: abc123

# Poll for results later
python perplexity_deep_research.py --job-id abc123 \
  --output ../pending-episodes/[episode-dir]/research/p2-perplexity.md
```

**List all async jobs:**

```bash
python perplexity_deep_research.py --list-jobs
```

### Available CLI Options

| Option | Description |
|--------|-------------|
| `--file FILEPATH` | Read prompt from file |
| `--output FILEPATH` | Write results to file |
| `--reasoning-effort LEVEL` | Effort: low, medium, high (default: high) |
| `--async` | Use async API (submit, poll, return result) |
| `--sync` | Force synchronous API (default) |
| `--no-wait` | Submit async job, return job ID immediately |
| `--job-id ID` | Poll an existing async job by ID |
| `--list-jobs` | List all async jobs for this API key |
| `--poll-interval SECS` | Seconds between poll attempts (default: 10) |
| `--timeout SECONDS` | Timeout in seconds (default: 600) |
| `--max-retries N` | Max retry attempts for sync mode (default: 3) |
| `--show-cost` | Display cost breakdown |
| `--quiet` | Minimal output |
| `--auto-save` | Auto-save output with timestamp (default when no --output) |
| `--no-auto-save` | Disable automatic file saving |
| `--log-dir DIR` | Directory for output/log files |

### Step 4: Monitor Progress

**Sync mode:** Script will wait 30-120s (up to timeout), retry on failure, auto-save results.

**Async mode:** Script submits job and either polls until complete or returns job ID for later retrieval.

### Step 5: Output Files

**Research output:**
- `research/p2-perplexity.md` — Research content with citations
- `research/p2-perplexity.meta.json` — Structured metadata (citations, cost, search results)
- `research/p2-perplexity_log.txt` — Progress log (sync mode)

**Metadata JSON example:**
```json
{
  "timestamp": "2026-02-11T10:30:00",
  "model": "sonar-deep-research",
  "usage": {
    "prompt_tokens": 234,
    "completion_tokens": 5678,
    "total_tokens": 5912,
    "citation_tokens": 1200,
    "reasoning_tokens": 3400,
    "search_queries": 15
  },
  "cost": {
    "input_tokens": {"count": 234, "cost": 0.0005},
    "output_tokens": {"count": 5678, "cost": 0.0454},
    "total": 0.0894
  },
  "citations": ["https://...", "https://..."],
  "search_results": [{"title": "...", "snippet": "...", "date": "..."}]
}
```

## API Details

### Synchronous API

**Endpoint:** `POST https://api.perplexity.ai/chat/completions`

**Request:**
```json
{
  "model": "sonar-deep-research",
  "messages": [{"role": "user", "content": "Research prompt"}],
  "reasoning_effort": "high"
}
```

### Async API

**Submit:** `POST https://api.perplexity.ai/async/chat/completions`

```json
{
  "request": {
    "model": "sonar-deep-research",
    "messages": [{"role": "user", "content": "Research prompt"}],
    "reasoning_effort": "high"
  }
}
```

**Response:** `{"id": "abc123", "status": "CREATED", "response": null}`

**Poll:** `GET https://api.perplexity.ai/async/chat/completions/{id}`

**Status values:** `CREATED` → `IN_PROGRESS` → `COMPLETED` | `FAILED`

**List:** `GET https://api.perplexity.ai/async/chat/completions`

Results are stored for 7 days.

## Cost Tracking

**Pricing (as of 2025):**

| Component | Cost |
|-----------|------|
| Input tokens | $2/M |
| Output tokens | $8/M |
| Citation tokens | $2/M |
| Reasoning tokens | $3/M |
| Search queries | $5/1K |

Typical deep research: $0.50-$1.00 per query.

Use `--show-cost` to display cost breakdown after research completes.

## Error Handling

### API Key Errors

**Error:** `ERROR: PERPLEXITY_API_KEY not found`
- Check `.env` file: `grep PERPLEXITY_API_KEY .env`
- Get API key: https://www.perplexity.ai/settings/api

### API Request Failures

| Error | Solution |
|-------|----------|
| 401 Unauthorized | API key invalid/expired. Regenerate at perplexity.ai |
| 429 Rate Limit | Wait 60s. Check usage limits |
| 500 Server Error | Wait 30s, retry. Check Perplexity status |
| Timeout (sync) | Use `--async` mode, increase `--timeout`, or reduce `--reasoning-effort` |

### Async-Specific Errors

| Error | Solution |
|-------|----------|
| Job FAILED | Check `error_message` in response. Simplify prompt or retry |
| Job not complete after timeout | Use `--job-id` to poll again later (results stored 7 days) |

## Integration with Podcast Workflow

**Sync workflow (current default):**
```bash
cd apps/podcast/tools
python perplexity_deep_research.py \
  --file "../pending-episodes/YYYY-MM-DD-slug/prompts.md" \
  --output "../pending-episodes/YYYY-MM-DD-slug/research/p2-perplexity.md" \
  --reasoning-effort high
```

**Async workflow (fire-and-forget for parallel research):**
```bash
cd apps/podcast/tools

# Fire off Perplexity research (returns immediately)
python perplexity_deep_research.py --no-wait \
  --file "../pending-episodes/YYYY-MM-DD-slug/prompts.md"
# Output: Job ID: abc123

# ... run other research tools in parallel ...

# Retrieve results when ready
python perplexity_deep_research.py --job-id abc123 \
  --output "../pending-episodes/YYYY-MM-DD-slug/research/p2-perplexity.md"
```

## Script Location

**Path:** `/Users/valorengels/src/cuttlefish/apps/podcast/tools/perplexity_deep_research.py`

## Comparison to Other Tools

| Feature | Perplexity | Gemini | GPT-Researcher | Grok |
|---------|-----------|--------|----------------|------|
| Speed | 30-120s | 3-10 min | 6-20 min | 30-90s |
| Cost | $$$ | $$ | $ (varies) | $$ |
| Academic Focus | High | Low | Medium | Low |
| Async Support | Yes | Yes | Yes | No |
| Citations | Inline + structured | Inline | Comprehensive | Inline |
| API-Based | Yes | Yes | Yes | Yes (OpenAI-compat) |

**Recommendation:** Use Perplexity for Phase 1 academic research. Use `--async` for parallel research workflows.

## Best Practices

1. **Always verify API key** before running research
2. **Use high reasoning effort** for podcast research (default)
3. **Use `--async` for parallel research** — fire off Perplexity while running other tools
4. **Use `--no-wait` + `--job-id`** for true fire-and-forget workflows
5. **Specify output file** using `--output` for organized file structure
6. **Check `.meta.json`** for structured citations and cost data
7. **Use `--show-cost`** to monitor API spending
8. **Trust the async API** — no client-side timeouts or retries needed
9. **Request citations explicitly** in prompts
10. **Use `--sync` as fallback** if async has issues
