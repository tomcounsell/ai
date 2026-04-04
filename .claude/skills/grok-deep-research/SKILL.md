---
name: grok-deep-research
description: Automate xAI Grok research API calls using grok-3 model. Synchronous blocking mode only. Use for real-time research with current events, practitioner perspectives, and social/regional insights. Returns research ready to paste into research-results.md.
---

# Grok Deep Research API Automation

This skill automates research using xAI's Grok API (grok-3 model) via the OpenAI-compatible chat completions endpoint.

## Overview

The xAI Grok API provides programmatic access to comprehensive research:
1. Processes research queries through the grok-3 model
2. Leverages real-time knowledge including recent news and social media
3. Returns detailed responses with practitioner perspectives
4. Outputs structured markdown-formatted reports

**Mode:** Synchronous (blocking) only. The tool submits a request and waits for the response, with retry logic on failure.

**Output:** Research report in markdown format. Results are automatically saved to timestamped files. Metadata sidecar JSON includes token usage and cost estimate.

**Focus Areas:**
- Real-time developments and recent news
- Practitioner perspectives and expert opinions
- Social and regional insights
- Current events and trending topics

## Prerequisites

- `GROK_API_KEY` set in environment or dotenv file
- Python 3.x with `requests` and `python-dotenv` installed
- API key from: https://console.x.ai/

## API Key Setup

**Check if API key exists:**

```bash
grep GROK_API_KEY .env 2>/dev/null || echo "GROK_API_KEY not found"
```

Get your key at https://console.x.ai/ and add `GROK_API_KEY=xai-your-key` to your environment configuration.

## Complete Automation Workflow

### Step 1: Verify API Key

```bash
grep GROK_API_KEY .env 2>/dev/null || echo "GROK_API_KEY not found"
```

### Step 2: Prepare Research Prompt

**Prompt format (3 lines, single newlines):**
```
Research [TOPIC].
Focus on recent developments, practitioner perspectives, and real-time insights.
Provide comprehensive findings with citations, expert opinions, and source URLs where available.
```

### Step 3: Run Research

```bash
cd /Users/valorengels/src/cuttlefish/apps/podcast/tools
python grok_deep_research.py \
  --file ../pending-episodes/[episode-dir]/prompts.md \
  --output ../pending-episodes/[episode-dir]/research/p2-grok.md
```

### Available CLI Options

| Option | Description |
|--------|-------------|
| `--file FILEPATH` | Read prompt from file |
| `--output FILEPATH` | Write results to file |
| `--timeout SECONDS` | Timeout in seconds (default: 300) |
| `--max-retries N` | Max retry attempts (default: 3) |
| `--show-cost` | Display cost breakdown |
| `--quiet` | Minimal output |
| `--auto-save` | Auto-save output with timestamp (default when no --output) |
| `--no-auto-save` | Disable automatic file saving |
| `--log-dir DIR` | Directory for output/log files |

### Step 4: Monitor Progress

The script waits for the response (typically 15-60s, up to timeout). It retries on failure with exponential backoff.

### Step 5: Output Files

**Research output:**
- `research/p2-grok.md` -- Research content
- `research/p2-grok.meta.json` -- Structured metadata (usage, cost)
- `research/p2-grok_log.txt` -- Progress log

**Metadata JSON example:**
```json
{
  "timestamp": "2026-04-04T10:30:00",
  "model": "grok-3",
  "usage": {
    "prompt_tokens": 50,
    "completion_tokens": 500,
    "total_tokens": 550,
    "input_tokens": 50,
    "output_tokens": 500
  },
  "cost": {
    "input_tokens": {"count": 50, "cost": 0.0002},
    "output_tokens": {"count": 500, "cost": 0.0075},
    "total": 0.0077
  }
}
```

## API Details

**Endpoint:** `POST https://api.x.ai/v1/chat/completions`

**Request:**
```json
{
  "model": "grok-3",
  "messages": [{"role": "user", "content": "Research prompt"}],
  "stream": false
}
```

**Response:** Standard OpenAI chat completions format with `choices[0].message.content`.

**Authentication:** `Authorization: Bearer {GROK_API_KEY}`

## Error Handling

### API Key Errors

**Error:** `ERROR: GROK_API_KEY not found`
- Check dotenv file: `grep GROK_API_KEY .env`
- Get API key: https://console.x.ai/

### API Request Failures

| Error | Solution |
|-------|----------|
| 401 Unauthorized | API key invalid/expired. Regenerate at console.x.ai |
| 429 Rate Limit | Wait 60s. Check usage limits |
| 500 Server Error | Wait 30s, retry. Check xAI status |
| Timeout | Increase `--timeout`, simplify prompt |

## Integration with Podcast Workflow

```bash
cd apps/podcast/tools
python grok_deep_research.py \
  --file "../pending-episodes/YYYY-MM-DD-slug/prompts.md" \
  --output "../pending-episodes/YYYY-MM-DD-slug/research/p2-grok.md"
```

**Note:** Service integration (adding Grok as a step in `research.py`) is planned as a separate future issue. Currently the tool is standalone CLI only.

## Script Location

**Path:** `/Users/valorengels/src/cuttlefish/apps/podcast/tools/grok_deep_research.py`

## Comparison to Other Tools

| Feature | Perplexity | Gemini | Grok | GPT-Researcher |
|---------|-----------|--------|------|----------------|
| Speed | 30-120s | 3-10 min | 15-60s | 6-20 min |
| Cost | $$$ | $$ | $$ | $ (varies) |
| Academic Focus | High | Low | Medium | Medium |
| Real-time News | Medium | Low | High | Medium |
| Async Support | Yes | Yes | No | Yes |
| Citations | Inline + structured | Inline | Inline | Comprehensive |
| API-Based | Yes | Yes | Yes | Yes |

**Recommendation:** Use Grok for real-time research and practitioner perspectives. Use Perplexity for academic/peer-reviewed sources. Use both for comprehensive coverage.

## Best Practices

1. **Always verify API key** before running research
2. **Use for real-time topics** -- Grok excels at recent news and current events
3. **Specify output file** using `--output` for organized file structure
4. **Check `.meta.json`** for token usage and cost data
5. **Combine with Perplexity** -- Grok for real-time, Perplexity for academic depth
6. **Keep prompts focused** -- the tool works best with clear, specific research questions
7. **Use `--show-cost`** to monitor API spending
