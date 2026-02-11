# Podcast Tools Audit - December 2024

## Tool Inventory

| Tool | Type | Status | Tests | Issues |
|------|------|--------|-------|--------|
| `perplexity_deep_research.py` | Research | ✅ Standardized | ✅ 15/20 pass | None |
| `gemini_deep_research.py` | Research | ✅ Standardized | ✅ 15/20 pass | None |
| `gpt_researcher_run.py` | Research | ✅ Standardized | ✅ 10/20 pass | None |
| `transcribe_only.py` | Audio | ✅ Standardized | ❌ No tests | None |
| `generate_chapters.py` | Audio | ✅ Standardized | ✅ Has tests | None |
| `generate_cover.py` | Visual | ✅ Standardized | ❌ No tests | None |
| `add_logo_watermark.py` | Visual | ✅ Standardized | ✅ Has tests | None |
| `main.py` | Entry | ⚠️ Placeholder | N/A | Empty file |

## Consistency Issues

### 1. Inconsistent CLI Patterns

**Research tools** have consistent interfaces:
```bash
python <tool>.py [prompt] --file FILE --output FILE --log-dir DIR --quiet
```

**Audio/Visual tools** have different patterns:
- `transcribe_only.py`: `audio_file --model --use-api` (no --quiet, no --log-dir)
- `generate_chapters.py`: `audio_file --model --chunk-duration --output-dir` (--output-dir ≠ --log-dir)
- `generate_cover.py`: `episode_dir --prompt --auto --aspect-ratio --output` (no --log-dir)
- `add_logo_watermark.py`: Different positional args pattern

**Recommendation:** Standardize on:
- `--quiet` flag for minimal output
- `--log-dir` for organizing outputs
- `--output` for explicit output files
- Consistent exit codes (0 = success, 1 = error)

### 2. Inconsistent Environment Loading

**Three different approaches:**

1. **python-dotenv** (research tools):
```python
from dotenv import load_dotenv
load_dotenv()
```

2. **Custom loader** (transcribe_only.py):
```python
def load_env():
    env_path = Path(__file__).parent.parent.parent / ".env"
    # Manual parsing
```

3. **Direct os.environ** (generate_cover.py, add_logo_watermark.py):
```python
api_key = os.getenv("OPENROUTER_API_KEY")
```

**Recommendation:** Standardize all tools to use python-dotenv

### 3. Inconsistent Output Patterns

**Research tools:** Auto-save with timestamps OR custom --output path
**Audio tools:** Save to same directory as input file
**Visual tools:** Save to episode directory

**Recommendation:** All tools should support --log-dir for organizing outputs

## Specific Tool Issues

### transcribe_only.py

**Issues:**
1. ❌ No --quiet flag
2. ❌ No --log-dir support
3. ❌ Custom env loading instead of python-dotenv
4. ❌ No progress logging to file
5. ❌ Hardcoded output path pattern
6. ⚠️ Prints to stdout without option to suppress

**Suggested improvements:**
```python
# Add flags
parser.add_argument('--quiet', '-q', action='store_true', help='Minimal output')
parser.add_argument('--log-dir', help='Directory for output files')
parser.add_argument('--output', help='Custom output path')

# Use python-dotenv
from dotenv import load_dotenv
load_dotenv()

# Add progress logging
if log_file:
    with open(log_file, 'a') as f:
        f.write(f"Transcription started: {datetime.now()}\n")
```

**Test coverage:** ❌ No tests

### generate_chapters.py

**Issues:**
1. ❌ Uses `--output-dir` instead of `--log-dir`
2. ❌ No --quiet flag
3. ❌ No progress logging to file
4. ⚠️ Takes audio file instead of transcript JSON (duplicates transcription work)
5. ⚠️ Hardcoded Claude model (`claude-sonnet-4-20250514`)

**Suggested improvements:**
```python
# Add support for pre-existing transcripts
parser.add_argument('--transcript', help='Use existing transcript JSON instead of transcribing')
parser.add_argument('--quiet', '-q', action='store_true')
parser.add_argument('--log-dir', help='Directory for chapter files')

# Allow model selection
parser.add_argument('--model', default='claude-sonnet-4-20250514', help='Claude model to use')

# Add progress logging
log_file = None
if args.log_dir:
    log_file = Path(args.log_dir) / f"chapters_log_{timestamp}.txt"
```

**Test coverage:** ✅ Has tests in `tests/test_generate_chapters.py`

### generate_cover.py

**Issues:**
1. ❌ No --log-dir support
2. ❌ No --quiet flag
3. ❌ Hardcoded model ID (`google/gemini-3-pro-image-preview`)
4. ❌ Manual logging to prompts.md (should be in log file)
5. ⚠️ Uses different --output pattern than other tools
6. ⚠️ Prints "next steps" instructions (could be --quiet controlled)

**Suggested improvements:**
```python
parser.add_argument('--quiet', '-q', action='store_true', help='Minimal output')
parser.add_argument('--log-dir', help='Directory for output and log files')
parser.add_argument('--model', default='google/gemini-3-pro-image-preview', help='Model to use')

# Save metadata to log file instead of prompts.md
if log_dir:
    metadata_file = Path(log_dir) / f"cover_generation_{timestamp}.json"
    with open(metadata_file, 'w') as f:
        json.dump({
            'prompt': prompt,
            'enhanced_prompt': enhanced_prompt,
            'model': MODEL_ID,
            'timestamp': datetime.now().isoformat()
        }, f, indent=2)
```

**Test coverage:** ❌ No tests

### add_logo_watermark.py

**Issues:**
1. ❌ No --log-dir support
2. ❌ No --quiet flag
3. ❌ Different CLI pattern (positional args)
4. ⚠️ No metadata logging
5. ⚠️ Hardcoded padding/size ratios

**Suggested improvements:**
```python
parser.add_argument('cover_image', help='Path to cover image')
parser.add_argument('--output', required=True, help='Output path for watermarked image')
parser.add_argument('--logo', help='Path to logo file')
parser.add_argument('--quiet', '-q', action='store_true')
parser.add_argument('--log-dir', help='Directory for log files')

# Log watermarking parameters
if log_dir:
    log_file = Path(log_dir) / f"watermark_log_{timestamp}.json"
    with open(log_file, 'w') as f:
        json.dump({
            'input': str(cover_path),
            'output': str(output_path),
            'logo': str(logo_path),
            'position': position,
            'timestamp': datetime.now().isoformat()
        }, f, indent=2)
```

**Test coverage:** ✅ Has tests in `tests/test_add_logo_watermark.py`

### main.py

**Current state:** Empty placeholder

**Options:**
1. **Delete it** - Not needed if tools are standalone
2. **Unified CLI** - Create entry point for all tools:
   ```bash
   python main.py transcribe audio.mp3
   python main.py chapters audio.mp3
   python main.py cover episode-dir
   python main.py research "prompt"
   ```
3. **Workflow orchestrator** - Chain tools together:
   ```bash
   python main.py full-episode audio.m4a episode-dir
   # Runs: convert → transcribe → chapters → embed
   ```

**Recommendation:** Create unified CLI with subcommands

## Priority Improvements

### High Priority (Consistency)

1. **Standardize CLI flags across all tools**
   - Add `--quiet` to: transcribe_only, generate_chapters, generate_cover, add_logo_watermark
   - Add `--log-dir` to: transcribe_only, generate_chapters, generate_cover, add_logo_watermark
   - Rename `--output-dir` to `--log-dir` in generate_chapters

2. **Standardize environment loading**
   - Change transcribe_only.py to use python-dotenv
   - Ensure all tools load .env from parent directories

3. **Add exit codes**
   - All tools should exit(0) on success, exit(1) on error
   - Consistent with research tools

### Medium Priority (Features)

4. **Add progress logging to files**
   - All tools should support logging to files when --log-dir is set
   - Follow pattern from research tools (dual stdout + file logging)

5. **Support existing transcripts in generate_chapters**
   - Add --transcript flag to use pre-existing transcript JSON
   - Avoid re-transcribing if transcript already exists

6. **Parameterize hardcoded values**
   - Model IDs (Claude, Gemini)
   - Padding ratios
   - Chunk durations
   - Image sizes

### Low Priority (Nice to have)

7. **Create unified CLI (main.py)**
   - Single entry point with subcommands
   - Consistent interface across all tools

8. **Add tests for untested tools**
   - transcribe_only.py
   - generate_cover.py

9. **Add metadata logging**
   - Log all parameters used for each operation
   - Useful for reproducibility

## Proposed File Structure

```
apps/podcast/tools/
├── README.md                       # Current comprehensive guide
├── AUDIT.md                        # This file
├── pyproject.toml                  # Dependencies
├── requirements.txt                # Pip fallback
│
├── Research Tools (✅ Consistent)
│   ├── perplexity_deep_research.py
│   ├── gemini_deep_research.py
│   └── gpt_researcher_run.py
│
├── Audio Tools (⚠️ Need standardization)
│   ├── transcribe_only.py         # → Add --quiet, --log-dir
│   └── generate_chapters.py       # → Add --quiet, --transcript, rename --output-dir
│
├── Visual Tools (⚠️ Need standardization)
│   ├── generate_cover.py          # → Add --quiet, --log-dir
│   └── add_logo_watermark.py      # → Add --quiet, --log-dir
│
├── Entry Point (⚠️ Needs implementation)
│   └── main.py                     # → Create unified CLI or remove
│
└── tests/
    ├── test_research_tools.py      # ✅ Exists
    ├── test_generate_chapters.py   # ✅ Exists
    ├── test_add_logo_watermark.py  # ✅ Exists
    ├── test_transcribe_only.py     # ❌ Create
    └── test_generate_cover.py      # ❌ Create
```

## Example: Standardized Tool Pattern

All tools should follow this pattern:

```python
#!/usr/bin/env python3
"""Tool description."""

import argparse
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

# Load environment
load_dotenv()

def main():
    parser = argparse.ArgumentParser(description="...")

    # Standard flags (all tools)
    parser.add_argument('--quiet', '-q', action='store_true', help='Minimal output')
    parser.add_argument('--log-dir', help='Directory for output and log files')
    parser.add_argument('--output', '-o', help='Output file path')

    # Tool-specific args
    parser.add_argument(...)

    args = parser.parse_args()

    # Setup logging
    log_file = None
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        log_file = Path(args.log_dir) / f"{tool_name}_log_{timestamp}.txt"

    def log(msg):
        if not args.quiet:
            print(msg)
        if log_file:
            with open(log_file, 'a') as f:
                f.write(msg + '\n')

    # Tool logic
    try:
        result = do_work(...)
        log("✓ Success")
        return 0
    except Exception as e:
        log(f"✗ Error: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

## Next Steps

1. **Immediate:** Standardize CLI flags for consistency
2. **Short-term:** Add tests for untested tools
3. **Medium-term:** Implement unified main.py or remove it
4. **Long-term:** Add progress tracking and metadata logging

## Testing Strategy

All tools should have:
- ✅ Unit tests for core functions
- ✅ CLI argument parsing tests
- ✅ File output tests
- ✅ Error handling tests
- ⚠️ Integration tests (optional)

Use mocking to avoid:
- Actual API calls
- Heavy computation (Whisper transcription)
- External dependencies

## Metrics

**Current state:**
- Total tools: 8
- Fully standardized: 3 (37.5%) - Research tools
- Partially standardized: 4 (50%) - Audio/Visual tools
- Placeholder: 1 (12.5%) - main.py
- Test coverage: 5/8 tools (62.5%)

**Target state:**
- Fully standardized: 100%
- Test coverage: 100%
- Unified CLI: Yes
- Consistent logging: Yes
