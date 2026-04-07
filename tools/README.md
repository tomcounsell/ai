# Valor Tools

Tools that extend Valor's capabilities. Each tool follows the standard defined in [STANDARD.md](STANDARD.md).

## Available Tools

### Core Tools (Built-in)

| Tool | Description | API Required |
|------|-------------|--------------|
| [browser](browser/) | Browser automation for web testing, screenshots, data extraction | None |
| [image_gen](image_gen/) | AI image generation using OpenRouter | OPENROUTER_API_KEY |
| [transcribe](transcribe/) | Audio transcription using Whisper | OPENAI_API_KEY |

### Search & Research

| Tool | Description | API Required |
|------|-------------|--------------|
| [search](search/) | Web search with Perplexity API | PERPLEXITY_API_KEY |
| [knowledge_search](knowledge_search/) | Local semantic search with embeddings | OPENROUTER_API_KEY |
| [link_analysis](link_analysis/) | URL extraction, validation, content analysis | PERPLEXITY_API_KEY* |

### Vision & Media

| Tool | Description | API Required |
|------|-------------|--------------|
| [image_analysis](image_analysis/) | Multi-modal vision analysis (objects, OCR, scenes) | OPENROUTER_API_KEY |
| [image_tagging](image_tagging/) | AI-powered image categorization and tagging | OPENROUTER_API_KEY |

### Code & Development

| Tool | Description | API Required |
|------|-------------|--------------|
| [code_execution](code_execution/) | Sandboxed Python/JS/Bash execution | None |
| [documentation](documentation/) | Generate docstrings, READMEs, API docs from code | ANTHROPIC_API_KEY |

### Documents & Summarization

| Tool | Description | API Required |
|------|-------------|--------------|
| [doc_summary](doc_summary/) | Document summarization with configurable detail | ANTHROPIC_API_KEY |

### Testing & Quality

| Tool | Description | API Required |
|------|-------------|--------------|
| [test_judge](test_judge/) | AI-powered test result evaluation | ANTHROPIC_API_KEY |
| [test_params](test_params/) | Generate test parameter variations | None |
| [test_scheduler](test_scheduler/) | Background test execution queue | None |

### Communication & History

| Tool | Description | API Required |
|------|-------------|--------------|
| [telegram_history](telegram_history/) | Search chat history with relevance scoring | None |

*Some features work without API key

## Quick Usage

```python
# Search the web
from tools.search import search
result = search("Python best practices")
print(result["summary"])

# Analyze an image
from tools.image_analysis import analyze_image
result = analyze_image("screenshot.png")
print(result["description"])

# Execute code safely
from tools.code_execution import execute_code
result = execute_code("print(2 + 2)")
print(result["stdout"])  # "4"

# Judge test results with AI
from tools.test_judge import judge_test_result
result = judge_test_result(
    test_output="All tests passed",
    expected_criteria=["Indicates success", "No errors"]
)
print(f"Pass: {result['pass_fail']}")
```

## Capabilities by Category

### Information Retrieval
- **Web Search**: Real-time information via Perplexity
- **Knowledge Search**: Semantic search across local documents
- **Link Analysis**: Extract and analyze URLs from text

### Vision & Understanding
- **Image Analysis**: Describe images, detect objects, extract text (OCR)
- **Image Tagging**: Categorize images with confidence scores
- **Image Generation**: Create images from text prompts

### Development Support
- **Code Execution**: Safe sandbox for running Python, JavaScript, Bash
- **Documentation**: Auto-generate docs from code
- **Test Judge**: AI evaluation of test outputs

### Testing Infrastructure
- **Test Params**: Generate edge cases and parameter variations
- **Test Scheduler**: Queue and run tests in background
- **Test Judge**: Subjective test evaluation with criteria matching

### Content Processing
- **Document Summary**: Summarize documents at various detail levels
- **Transcription**: Convert audio to text

## Configuration

API keys are stored in the shared location `/Users/valorengels/src/.env`. Tools automatically load from this file.

Required keys:
- `OPENROUTER_API_KEY` - For vision, tagging, embeddings
- `PERPLEXITY_API_KEY` - For web search
- `ANTHROPIC_API_KEY` - For test judging, documentation, summaries
- `OPENAI_API_KEY` - For transcription

## Running Tests

```bash
# All tool tests
pytest tools/ -v

# Specific tool
pytest tools/search/tests/ -v

# With coverage
pytest tools/ --cov=tools --cov-report=html
```

## Adding New Tools

See [STANDARD.md](STANDARD.md) for the tool structure and requirements.

```bash
# Create from template
mkdir -p tools/<name>/tests
# Add manifest.json, __init__.py, README.md, tests/
```
