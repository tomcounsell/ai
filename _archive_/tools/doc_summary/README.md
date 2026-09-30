# Document Summary Tool

Document summarization with configurable detail levels and key point extraction.

## Overview

This tool provides AI-powered document summarization:
- Multiple summary types (brief, standard, detailed, bullets)
- Key point extraction
- Compression ratio tracking
- File or text content support

## Installation

Configure your API key:

```bash
export ANTHROPIC_API_KEY=your_api_key
# or
export OPENROUTER_API_KEY=your_api_key
```

## Quick Start

```python
from tools.doc_summary import summarize

# Summarize text
result = summarize("Your long document content here...")
print(result["summary"])

# Summarize a file
result = summarize("path/to/document.md")
print(result["summary"])

# Get bullet points
result = summarize(content, summary_type="bullets")
for point in result["key_points"]:
    print(f"- {point}")
```

## API Reference

### summarize()

```python
def summarize(
    content: str,
    summary_type: Literal["brief", "standard", "detailed", "bullets"] = "standard",
    max_length: int | None = None,
    focus_areas: list[str] | None = None,
    preserve_quotes: bool = False,
) -> dict
```

**Parameters:**
- `content`: Document content or file path
- `summary_type`: Type of summary
  - `brief`: 1-2 sentences
  - `standard`: Clear, concise coverage
  - `detailed`: Thorough coverage
  - `bullets`: Bullet-point format
- `max_length`: Maximum words (optional)
- `focus_areas`: Topics to emphasize
- `preserve_quotes`: Keep important quotes

**Returns:**
```python
{
    "summary": str,
    "key_points": list[str],
    "word_count": int,
    "original_word_count": int,
    "compression_ratio": float,
    "summary_type": str
}
```

### summarize_file()

```python
def summarize_file(file_path: str, summary_type: str = "standard", **kwargs) -> dict
```

Convenience function for file summarization.

### extract_key_points()

```python
def extract_key_points(content: str, max_points: int = 5) -> dict
```

Extract only key points without full summary.

## Workflows

### Quick Summary
```python
result = summarize(document, summary_type="brief")
print(result["summary"])  # 1-2 sentences
```

### Detailed Analysis
```python
result = summarize(document, summary_type="detailed")
print(result["summary"])
print(f"Key points: {len(result['key_points'])}")
```

### Focused Summary
```python
result = summarize(
    document,
    focus_areas=["security", "performance"],
)
```

### With Length Limit
```python
result = summarize(document, max_length=100)
print(f"Words: {result['word_count']}")  # ~100
```

## Error Handling

```python
result = summarize(content)

if "error" in result:
    print(f"Summary failed: {result['error']}")
else:
    print(result["summary"])
```

## Troubleshooting

### API Key Not Set
Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY.

### File Not Found
Ensure the file path is correct and readable.

### Request Timeout
Large documents may timeout. Try summarizing smaller sections.
