# Podcast Research Tools

Automated tools for podcast episode creation and research.

## Deep Research Tools

All deep research tools automatically save both output and logs to files.

### 1. Perplexity Deep Research (`perplexity_deep_research.py`)

Fast academic research with peer-reviewed sources.

```bash
python perplexity_deep_research.py "Research prompt"
python perplexity_deep_research.py --file prompt.txt --output results.md
```

- **Speed:** 30-120 seconds
- **Focus:** Academic studies, peer-reviewed papers, meta-analyses
- **API:** Perplexity `sonar-deep-research` model
- **Key:** `PERPLEXITY_API_KEY` in `.env`

**Options:**
- `--file, -f PATH` - Read prompt from file
- `--output, -o PATH` - Write results to file (also creates `[name]_log.txt`)
- `--reasoning-effort {low,medium,high}` - Default: high
- `--quiet, -q` - Minimal output
- `--no-auto-save` - Disable automatic file saving

**Auto-save:** By default, creates timestamped output and log files:
- `perplexity_output_20241215_143022.md`
- `perplexity_log_20241215_143022.txt`

### 2. Gemini Deep Research (`gemini_deep_research.py`)

Policy analysis and regulatory frameworks.

```bash
python gemini_deep_research.py "Research prompt"
python gemini_deep_research.py --file prompt.txt --output results.md
```

- **Speed:** 3-10 minutes
- **Focus:** Policy analysis, regulatory frameworks, strategic context
- **API:** Google Gemini Deep Research
- **Key:** `GOOGLE_AI_API_KEY` in `.env`

**Options:**
- `--file, -f PATH` - Read prompt from file
- `--output, -o PATH` - Write results to file (also creates `[name]_log.txt`)
- `--stream, -s` - Use streaming mode
- `--poll-interval N` - Seconds between checks (default: 120)
- `--max-wait N` - Max wait in minutes (default: 60)
- `--quiet, -q` - Minimal output
- `--no-auto-save` - Disable automatic file saving

**Auto-save:** By default, creates timestamped output and log files:
- `gemini_output_20241215_143022.md`
- `gemini_log_20241215_143022.txt`

### 3. GPT-Researcher (`gpt_researcher_run.py`)

Multi-agent comprehensive research with 100+ sources.

```bash
uv run python gpt_researcher_run.py "Research prompt"
uv run python gpt_researcher_run.py --file prompt.txt --output results.md
```

- **Speed:** 6-20 minutes
- **Focus:** Multi-agent comprehensive research
- **API:** Configurable (OpenAI GPT-5.2, Claude Opus 4, etc.)
- **Keys:** Multiple (see below)

**Options:**
- `--file, -f PATH` - Read prompt from file
- `--output, -o PATH` - Write results to file (also creates `[name]_log.txt`)
- `--model SPEC` - Model (default: openai:gpt-5.2)
- `--report-type TYPE` - research_report, detailed_report, quick_report, deep
- `--detailed` - Use STORM methodology
- `--quiet, -q` - Minimal output
- `--no-auto-save` - Disable automatic file saving

**Auto-save:** By default, creates timestamped output and log files:
- `gpt_researcher_output_20241215_143022.md`
- `gpt_researcher_log_20241215_143022.txt`

**Supported API Keys:**
- `OPENAI_API_KEY` - OpenAI models
- `ANTHROPIC_API_KEY` - Claude models
- `OPENROUTER_API_KEY` - Unified access to 400+ models
- `XAI_API_KEY` - Grok models
- `TAVILY_API_KEY` - Enhanced search (recommended)

## Audio Processing Tools

### Transcription (`transcribe_only.py`)

Local Whisper transcription (no API key needed):

```bash
# Basic usage
python transcribe_only.py podcast.mp3 --model base

# With organized output and logging
python transcribe_only.py podcast.mp3 --model base --log-dir logs/ --quiet

# Custom output path
python transcribe_only.py podcast.mp3 --output custom_transcript.json
```

**Options:**
- `--model {tiny,base,small,medium}` - Whisper model (default: base)
- `--use-api` - Use OpenAI API instead of local model (requires OPENAI_API_KEY)
- `--output, -o PATH` - Output file path (default: auto-generated)
- `--log-dir DIR` - Directory for output and log files
- `--quiet, -q` - Minimal output (suppress progress messages)

**Models:**
- `tiny` - Fastest (~1-2 min), basic accuracy
- `base` - **Recommended** (~5-10 min), good accuracy
- `small` - Slower (~15-20 min), better accuracy
- `medium` - Slowest (~30-40 min), best accuracy

### Chapter Generation (`generate_chapters.py`)

Generate podcast chapters from audio or existing transcript:

```bash
# From audio file (transcribes automatically)
python generate_chapters.py podcast.mp3

# From existing transcript (faster, avoids re-transcription)
python generate_chapters.py podcast.mp3 --transcript podcast_transcript.json

# With organized output and logging
python generate_chapters.py podcast.mp3 --log-dir logs/ --quiet

# Custom Claude model
python generate_chapters.py podcast.mp3 --claude-model claude-opus-4-20250514
```

**Options:**
- `--transcript PATH` - Use existing transcript JSON (avoids re-transcription)
- `--model {tiny,base,small,medium}` - Whisper model for transcription (default: base)
- `--claude-model MODEL` - Claude model for chapter generation (default: claude-sonnet-4-20250514)
- `--chunk-duration N` - Target chunk duration in seconds (default: 120)
- `--log-dir DIR` - Directory for chapter files and logs
- `--output, -o PATH` - Output base path for chapter files
- `--quiet, -q` - Minimal output

Requires `ANTHROPIC_API_KEY` in `.env`.

## Cover Art Tools

### Cover Generation (`generate_cover.py`)

AI-generated cover art with branding:

```bash
# Auto-generate from report.md
python generate_cover.py ../pending-episodes/2024-12-14-topic --auto

# Custom prompt
python generate_cover.py ../pending-episodes/2024-12-14-topic --prompt "Abstract visualization of..."

# With organized output and logging
python generate_cover.py ../pending-episodes/2024-12-14-topic --auto --log-dir logs/ --quiet

# Custom model and aspect ratio
python generate_cover.py episode-dir --auto --model google/gemini-3-pro-image-preview --aspect-ratio 16:9
```

**Options:**
- `--auto` - Auto-generate prompt from report.md
- `--prompt TEXT` - Custom image generation prompt
- `--model MODEL` - Model to use (default: google/gemini-3-pro-image-preview)
- `--aspect-ratio RATIO` - Image aspect ratio (default: 1:1)
- `--output FILENAME` - Output filename (default: cover.png)
- `--log-dir DIR` - Directory for output and log files
- `--quiet, -q` - Minimal output

Requires `OPENROUTER_API_KEY` in `.env`.

### Logo Watermarking (`add_logo_watermark.py`)

Add logo and branding to images:

```bash
# Basic usage
python add_logo_watermark.py cover.png --series "Series Name" --episode "Ep 3 - Topic"

# With organized logging
python add_logo_watermark.py cover.png --series "Series" --episode "Ep 3" --log-dir logs/ --quiet

# Custom logo and positioning
python add_logo_watermark.py cover.png --logo custom_logo.png --position top-left --size 0.15

# With border
python add_logo_watermark.py cover.png --border 20 --border-color "#FFC20E"
```

**Options:**
- `--logo PATH` - Path to logo (default: ../cover.png)
- `--position POS` - Logo position: bottom-right, bottom-left, top-right, top-left, center (default: bottom-right)
- `--opacity N` - Logo opacity 0.0-1.0 (default: 1.0)
- `--size N` - Logo size ratio 0.1-0.3 (default: 0.12)
- `--brand TEXT` - Podcast brand name (default: "Yudame Research")
- `--series TEXT` - Series name text
- `--episode TEXT` - Episode text
- `--border N` - Border width in pixels (default: 0)
- `--border-color HEX` - Border color (default: #FFC20E)
- `--log-dir DIR` - Directory for log files
- `--quiet, -q` - Minimal output

## Tool Comparison

| Tool | Speed | Cost | Academic | Policy | Technical |
|------|-------|------|----------|--------|-----------|
| Perplexity | 30-120s | $$$ | ✓✓✓ | ✓ | ✓✓ |
| Gemini | 3-10m | $$ | ✓ | ✓✓✓ | ✓✓ |
| GPT-Researcher | 6-20m | $ | ✓✓ | ✓✓ | ✓✓✓ |

## Common Workflows

### Quick Academic Research
```bash
cd podcast/tools
python perplexity_deep_research.py \
  --file ../pending-episodes/episode-dir/prompts.md \
  --log-dir ../pending-episodes/episode-dir/logs
# Creates files in ../pending-episodes/episode-dir/logs/:
#   - perplexity_output_[timestamp].md
#   - perplexity_log_[timestamp].txt
```

### Comprehensive Multi-Source Research
```bash
cd podcast/tools
uv run python gpt_researcher_run.py \
  --file ../pending-episodes/episode-dir/prompts.md \
  --log-dir ../pending-episodes/episode-dir/logs
# Creates files in ../pending-episodes/episode-dir/logs/:
#   - gpt_researcher_output_[timestamp].md
#   - gpt_researcher_log_[timestamp].txt
```

### Audio Processing Pipeline

**Basic workflow:**
```bash
cd apps/podcast/pending-episodes/episode-dir

# 1. Convert audio
ffmpeg -i original.m4a -codec:a libmp3lame -b:a 128k episode.mp3

# 2. Transcribe
cd ../../tools
python transcribe_only.py ../pending-episodes/episode-dir/episode.mp3 --model base

# 3. Generate chapters (using existing transcript)
python generate_chapters.py ../pending-episodes/episode-dir/episode.mp3 \
  --transcript ../pending-episodes/episode-dir/episode_transcript.json

# 4. Embed chapters
cd ../pending-episodes/episode-dir
ffmpeg -i episode.mp3 -i episode_chapters.txt -map_metadata 1 -codec copy temp.mp3
mv temp.mp3 episode.mp3
```

**With organized logging:**
```bash
cd podcast/tools

# Create logs directory
mkdir -p ../pending-episodes/episode-dir/logs

# Transcribe with logging
python transcribe_only.py ../pending-episodes/episode-dir/episode.mp3 \
  --model base \
  --log-dir ../pending-episodes/episode-dir/logs \
  --quiet

# Generate chapters from existing transcript with logging
python generate_chapters.py ../pending-episodes/episode-dir/episode.mp3 \
  --transcript ../pending-episodes/episode-dir/episode_transcript.json \
  --log-dir ../pending-episodes/episode-dir/logs \
  --quiet

# All logs and output files are now in ../pending-episodes/episode-dir/logs/
```

## Environment Setup

Create `.env` file in the project root (or add to `.env.local`):

```bash
# Research APIs
PERPLEXITY_API_KEY=pplx-...
GOOGLE_AI_API_KEY=...
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
OPENROUTER_API_KEY=sk-or-...
TAVILY_API_KEY=tvly-...

# Optional
XAI_API_KEY=...
```

Get API keys:
- Perplexity: https://www.perplexity.ai/settings/api
- Google AI: https://aistudio.google.com/apikey
- OpenAI: https://platform.openai.com/api-keys
- Anthropic: https://console.anthropic.com/settings/keys
- OpenRouter: https://openrouter.ai/keys
- Tavily: https://tavily.com/

## Python Dependencies

Install all dependencies:

```bash
cd podcast/tools
pip install -r requirements.txt
```

Or use uv (recommended for GPT-Researcher):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv pip install -r requirements.txt
```

## File Output Behavior

### Default (No --output)
All research tools auto-save with timestamps:
```bash
python perplexity_deep_research.py "Research X"
```
Creates:
- `perplexity_output_20241215_143022.md` - Research results
- `perplexity_log_20241215_143022.txt` - Full execution log

### With --output
Specify output filename, log file created automatically:
```bash
python perplexity_deep_research.py --file prompt.txt --output results.md
```
Creates:
- `results.md` - Research results
- `results_log.txt` - Full execution log

### Disable Auto-Save
Print to stdout only:
```bash
python perplexity_deep_research.py "Research X" --no-auto-save
```

## Log Files

Log files contain complete execution details:
- Configuration settings
- Progress updates
- API usage statistics
- Error messages
- Timing information

Useful for:
- Debugging issues
- Cost tracking
- Performance analysis
- Audit trails

## Troubleshooting

### API Key Errors

**Error:** `ERROR: [SERVICE]_API_KEY not found`

**Solution:**
1. Check `.env` file exists: `ls -la .env`
2. Verify key is set: `grep [SERVICE]_API_KEY .env`
3. Get API key from service provider
4. Add to `.env` file

### Import Errors

**Error:** `ModuleNotFoundError: No module named 'X'`

**Solution:**
```bash
pip install requests python-dotenv
# or
pip install -r requirements.txt
```

### Rate Limits

**Error:** `429 Too Many Requests`

**Solution:**
- Wait 60 seconds
- Check API usage dashboard
- Upgrade API plan if needed

## Best Practices

1. **Review Logs:** Check log files after each run for errors/warnings
2. **Cost Monitoring:** Track API usage in log files
3. **Backup Results:** Save important research outputs
4. **API Keys:** Keep `.env` file secure (gitignored)
5. **Test First:** Use simple prompts before complex research
