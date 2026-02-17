---
name: gemini-deep-research
description: Automate Gemini Deep Research using the official API. Use for Phase 3 policy analysis, regulatory frameworks, and strategic context research. Handles API submission, polling (3-10 min), and result extraction. Returns policy-focused research ready to paste into research-results.md.
---

# Gemini Deep Research API Automation

This skill automates research using Google's Gemini Deep Research API - no browser automation required.

## Overview

The Gemini Deep Research API provides programmatic access to Google's multi-step research agent:
1. Autonomously plans research strategy
2. Executes web searches across multiple sources
3. Synthesizes findings into a comprehensive report with citations
4. Runs asynchronously with status polling

**Time:** Research typically takes 3-10 minutes depending on complexity (max 60 minutes).

**Output:** Comprehensive research report with inline citations and source links.

**Focus Areas:**
- Regulatory frameworks and telecommunications legislation
- Government policy documents and strategic plans
- Market structure analysis
- Comparative policy analysis across jurisdictions
- Stakeholder position papers

## Prerequisites

- Google AI API key in `/Users/valorengels/.env` (auto-loaded via ~/.zshenv)
- Python 3.x with required dependencies installed
- API key from: https://aistudio.google.com/apikey

## API Key Setup

**Check if API key exists:**

```bash
grep GEMINI_API_KEY /Users/valorengels/.env
```

If not found, add to global `.env` file:

```bash
# API keys are stored in /Users/valorengels/.env (auto-loaded via ~/.zshenv)
echo 'GEMINI_API_KEY=your-api-key-here' >> /Users/valorengels/.env
```

**Getting an API key:**
1. Go to https://aistudio.google.com/apikey
2. Sign in with your Google account
3. Create a new API key
4. Copy the key and add to `.env` file

## Complete Automation Workflow

### Step 1: Verify API Key

Use Bash to check if the API key is configured:

```bash
grep GEMINI_API_KEY .env
```

If not found, inform user to set up API key at https://aistudio.google.com/apikey

### Step 2: Prepare Research Prompt

The research prompt should be saved in the episode's `prompts.md` file under the Gemini Deep Research section.

**Prompt format (3 lines, single newlines):**
```
Research [TOPIC].
Focus on regulatory frameworks, legislation, government policy documents, strategic plans, and comparative policy analysis.
Provide findings with official source citations, effective dates, and policy context.
```

### Step 3: Run Research via Python Script

Execute the Python script using Bash:

```bash
cd /Users/valorengels/src/cuttlefish/apps/podcast/tools
python gemini_deep_research.py --file ../pending-episodes/[episode-dir]/prompts.md --output ../pending-episodes/[episode-dir]/gemini-results.md
```

Or with inline prompt:

```bash
python gemini_deep_research.py "Research prompt here"
```

**Available options:**
- `--file FILEPATH` - Read prompt from file
- `--output FILEPATH` - Write results to file
- `--stream` - Use streaming mode for real-time output
- `--poll-interval SECONDS` - Seconds between status checks (default: 120)
- `--max-wait MINUTES` - Maximum wait time (default: 60)
- `--quiet` - Minimal output (just the result)

### Step 4: Monitor Progress

The script will:
1. Submit research request to Gemini API
2. Display interaction ID and estimated time
3. Poll every 2 minutes for status updates
4. Show progress: "in_progress", "completed", or "failed"

**Expected output:**
```
==============================================================
GEMINI DEEP RESEARCH API
==============================================================

Prompt: Research Solomon Islands telecommunications...

Submitting research request...

Research started successfully!
Interaction ID: abc123xyz
Status: in_progress
Estimated time: 3-10 minutes (max 60 minutes)
Polling every 120 seconds...
--------------------------------------------------------------

[10:30:15] Status check #1 (elapsed: 120s)
Status: in_progress
Research in progress. Waiting 120s...

[10:32:15] Status check #2 (elapsed: 240s)
Status: completed

==============================================================
RESEARCH COMPLETE (took 240s)
==============================================================
```

### Step 5: Extract and Save Results

If `--output` was specified, results are automatically saved to the file.

Otherwise, the script prints results to stdout and you should:
1. Copy the research output
2. Paste into the episode's `research-results.md` under the Gemini section

**Recommended workflow:**
```bash
# Run with output file
python gemini_deep_research.py \
  --file ../pending-episodes/episode-dir/prompts.md \
  --output ../pending-episodes/episode-dir/gemini-results.md

# Append to research-results.md
cat ../pending-episodes/episode-dir/gemini-results.md >> ../pending-episodes/episode-dir/research-results.md
```

## API Details

**Base URL:** `https://generativelanguage.googleapis.com/v1beta/interactions`

**Agent Model:** `deep-research-pro-preview-12-2025`

**Request Format:**
```json
{
  "input": "Research prompt here",
  "agent": "deep-research-pro-preview-12-2025",
  "background": true,
  "store": true
}
```

**Response Format:**
```json
{
  "id": "interaction-id",
  "status": "in_progress" | "completed" | "failed",
  "outputs": [
    {
      "type": "text",
      "text": "Research report content..."
    }
  ]
}
```

**Default Capabilities (enabled automatically):**
- `google_search` - Web search across Google
- `url_context` - Fetches and analyzes webpage content

## Streaming Mode (Optional)

For real-time progress updates, use `--stream` flag:

```bash
python gemini_deep_research.py --stream "Research prompt here"
```

**Streaming behavior:**
- Shows research output as it's generated
- Displays thinking summaries: `[Thinking: analyzing sources...]`
- No polling required - continuous connection
- Same total time as background mode

**When to use streaming:**
- Interactive debugging
- Watching progress in real-time
- Long research queries where you want to see incremental progress

## Error Handling

### API Key Errors

**Error:** `ERROR: GEMINI_API_KEY not found`

**Solution:**
1. Check `.env` file exists in repository root
2. Verify API key is set: `grep GEMINI_API_KEY .env`
3. Get API key from https://aistudio.google.com/apikey
4. Add to `.env`: `GEMINI_API_KEY=your-key-here`

### API Request Failures

**Error:** `ERROR: API returned status 401`

**Solution:**
- API key is invalid or expired
- Verify key at https://aistudio.google.com/apikey
- Regenerate key if needed

**Error:** `ERROR: API returned status 429`

**Solution:**
- Rate limit exceeded
- Wait 60 seconds and retry
- Check if multiple requests are running
- Monitor usage at https://aistudio.google.com/

**Error:** `ERROR: Failed to submit request: Connection timeout`

**Solution:**
- Check internet connection
- Verify API endpoint is accessible
- Try with longer timeout (script retries 3x automatically)

### Research Failures

**Error:** `ERROR: Research failed: Unknown error`

**Solution:**
- Check error details in output
- May be due to: prompt issues, source access problems, timeout
- Retry with simplified prompt
- Try different research tool if persistent

**Error:** `ERROR: Research timed out after 60 minutes`

**Solution:**
- Research was too complex
- Simplify the prompt or break into smaller tasks
- Increase max-wait time: `--max-wait 90`
- Use alternative tool (Claude, ChatGPT, Perplexity)

### No Output Found

**Error:** `WARNING: Research completed but no text output found`

**Solution:**
- Check API response structure (may have changed)
- Review full response JSON (script shows it on verbose mode)
- File bug report with API response details

## Integration with Podcast Workflow

When called from the podcast episode workflow:

**Input needed:**
- Research prompt from `prompts.md` (Gemini section)
- Episode directory path

**Expected output:**
- Success: Full research report with citations saved to file
- Failure: Error message with troubleshooting steps

**Workflow integration example:**

```bash
# Phase 3: Research Execution - Gemini Deep Research
EPISODE_DIR="apps/podcast/pending-episodes/2024-12-14-topic-slug"

# Run Gemini research
cd apps/podcast/tools
python gemini_deep_research.py \
  --file "../${EPISODE_DIR}/prompts.md" \
  --output "../${EPISODE_DIR}/research-results-gemini.md" \
  --poll-interval 120 \
  --max-wait 60

# Check if successful
if [ $? -eq 0 ]; then
  echo "Gemini research complete"
  # Append to main research results
  cat "../${EPISODE_DIR}/research-results-gemini.md" >> "../${EPISODE_DIR}/research-results.md"
else
  echo "Gemini research failed - check error messages"
fi
```

## Why API-Based Automation

This skill uses the official Gemini Deep Research API for maximum reliability:

- **Stable:** No UI changes breaking selectors
- **Simple:** Just API key configuration needed
- **Scriptable:** Fully automated, no browser required
- **Portable:** Works in any environment with Python and internet
- **Official:** Direct API access to Google's research agent
- **Maintainable:** API contracts are stable and documented

## Best Practices

1. **Always verify API key** before running research
2. **Use background mode** (default) for research
3. **Set reasonable poll intervals** (2 minutes is good balance)
4. **Save output to file** using `--output` flag
5. **Handle errors gracefully** - check exit code before continuing
6. **Monitor API usage** to control costs
7. **Use specific prompts** - vague prompts waste API calls
8. **Test with simple prompts** before complex research

## Example Commands

**Basic research:**
```bash
python gemini_deep_research.py "Research quantum computing applications"
```

**From file with output:**
```bash
python gemini_deep_research.py \
  --file research-prompt.txt \
  --output results.md
```

**Streaming with custom timing:**
```bash
python gemini_deep_research.py \
  --stream \
  "Research climate change policy in Pacific nations"
```

**Quiet mode (just results):**
```bash
python gemini_deep_research.py \
  --quiet \
  --file prompt.txt \
  --output results.md
```

**Custom polling:**
```bash
python gemini_deep_research.py \
  --file prompt.txt \
  --poll-interval 60 \
  --max-wait 90 \
  --output results.md
```

## Script Location

**Path:** `/Users/valorengels/src/cuttlefish/apps/podcast/tools/gemini_deep_research.py`

**Usage:**
```
python gemini_deep_research.py [OPTIONS] [PROMPT]

Options:
  --file, -f PATH       Read prompt from file
  --output, -o PATH     Write output to file
  --stream, -s          Use streaming mode
  --poll-interval N     Seconds between checks (default: 120)
  --max-wait N          Max wait in minutes (default: 60)
  --quiet, -q           Minimal output

Examples:
  python gemini_deep_research.py "Your prompt here"
  python gemini_deep_research.py --file prompt.txt
  python gemini_deep_research.py --file prompt.txt --output results.md
  python gemini_deep_research.py --stream "Your prompt"
```

## Notes

- This API is in **preview** (as of December 2025) - schema may change
- Model name: `deep-research-pro-preview-12-2025`
- `background: true` and `store: true` are required together
- Web search enabled by default
- Maximum research duration: 60 minutes
- Citations included inline in the output
- Perfect for automated workflows - no browser required
- Check pricing at: https://ai.google.dev/pricing
