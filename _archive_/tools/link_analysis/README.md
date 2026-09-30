# Link Analysis Tool

URL extraction, validation, and content analysis.

## Overview

This tool provides URL handling capabilities:
- Extract URLs from text
- Validate URL accessibility
- Get page metadata (title, description)
- AI-powered content analysis

## Installation

For content analysis, configure your API key:

```bash
export PERPLEXITY_API_KEY=your_api_key
```

URL extraction and validation work without an API key.

## Quick Start

```python
from tools.link_analysis import extract_urls, analyze_url

# Extract URLs from text
result = extract_urls("Check out https://example.com")
print(result["urls"])  # ['https://example.com']

# Analyze a URL
analysis = analyze_url("https://example.com")
print(analysis["metadata"]["title"])
```

## API Reference

### extract_urls()

```python
def extract_urls(text: str) -> dict
```

Extract all URLs from text.

**Returns:**
```python
{
    "urls": list[str],  # Unique URLs found
    "count": int        # Number of URLs
}
```

### validate_url()

```python
def validate_url(url: str, timeout: int = 10) -> dict
```

Check if a URL is accessible.

**Returns:**
```python
{
    "url": str,
    "valid": bool,
    "status_code": int,      # If valid
    "final_url": str,        # After redirects
    "redirected": bool,
    "error": str             # If invalid
}
```

### get_metadata()

```python
def get_metadata(url: str, timeout: int = 10) -> dict
```

Get page metadata.

**Returns:**
```python
{
    "url": str,
    "title": str | None,
    "description": str | None,
    "content_type": str
}
```

### analyze_url()

```python
def analyze_url(url: str, analyze_content: bool = True) -> dict
```

Full URL analysis with AI-powered content summary.

**Returns:**
```python
{
    "url": str,
    "validation": dict,
    "metadata": dict,
    "analysis": {
        "summary": str
    }
}
```

### analyze_text_links()

```python
def analyze_text_links(
    text: str,
    analyze_content: bool = False,
    validate_links: bool = True,
) -> dict
```

Analyze all links in a block of text.

## Workflows

### Extract and Validate
```python
result = extract_urls(message_text)
for url in result["urls"]:
    validation = validate_url(url)
    if validation["valid"]:
        print(f"{url} is accessible")
```

### Get Page Info
```python
metadata = get_metadata("https://example.com")
print(f"Title: {metadata['title']}")
print(f"Description: {metadata['description']}")
```

### Full Analysis
```python
analysis = analyze_url("https://example.com")
print(analysis["analysis"]["summary"])
```

## Error Handling

```python
result = analyze_url(url)

if "error" in result:
    print(f"Analysis failed: {result['error']}")
else:
    print(result["analysis"]["summary"])
```

## Troubleshooting

### URL Not Accessible
Check that the URL is correct and the server is running.

### API Key Not Set
Content analysis requires PERPLEXITY_API_KEY. Validation and metadata extraction still work.
