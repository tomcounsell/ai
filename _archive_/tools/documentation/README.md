# Documentation Tool

Generate documentation from code including docstrings, README files, and API docs.

## Overview

This tool provides AI-powered documentation generation:
- Function and class docstrings
- README files
- API documentation
- Changelog entries

Multiple styles supported (Google, NumPy, Sphinx, Markdown).

## Installation

Configure your API key:

```bash
export ANTHROPIC_API_KEY=your_api_key
# or
export OPENROUTER_API_KEY=your_api_key
```

## Quick Start

```python
from tools.documentation import generate_docstring, generate_readme

# Generate a docstring
code = '''
def calculate_total(items, tax_rate=0.08):
    return sum(i["price"] for i in items) * (1 + tax_rate)
'''
result = generate_docstring(code)
print(result["documentation"])

# Generate a README
result = generate_readme(module_code)
print(result["documentation"])
```

## API Reference

### generate_docs()

```python
def generate_docs(
    source: str,
    doc_type: Literal["docstring", "readme", "api", "changelog"] = "docstring",
    style: Literal["google", "numpy", "sphinx", "markdown"] = "google",
    detail_level: Literal["minimal", "standard", "comprehensive"] = "standard",
    include_examples: bool = True,
) -> dict
```

**Parameters:**
- `source`: Code or file path
- `doc_type`: Type of documentation
  - `docstring`: Function/class docstrings
  - `readme`: README.md file
  - `api`: API reference documentation
  - `changelog`: Changelog entries
- `style`: Documentation style
- `detail_level`: Level of detail
- `include_examples`: Include usage examples

**Returns:**
```python
{
    "documentation": str,  # Generated docs
    "doc_type": str,
    "style": str,
    "format": str
}
```

### generate_docstring()

```python
def generate_docstring(
    code: str,
    style: Literal["google", "numpy", "sphinx", "markdown"] = "google",
    include_examples: bool = True,
) -> dict
```

Convenience function for docstring generation.

### generate_readme()

```python
def generate_readme(
    code: str,
    detail_level: Literal["minimal", "standard", "comprehensive"] = "standard",
) -> dict
```

Convenience function for README generation.

## Workflows

### Docstring Generation
```python
result = generate_docstring(function_code, style="google")
print(result["documentation"])
```

Output example:
```python
"""Calculate the total price with tax.

Args:
    items: List of items with price and quantity.
    tax_rate: Tax rate to apply. Defaults to 0.08.

Returns:
    Total price including tax.

Example:
    >>> calculate_total([{"price": 10, "quantity": 2}])
    21.6
"""
```

### README Generation
```python
result = generate_readme(project_code, detail_level="comprehensive")
```

### API Documentation
```python
result = generate_docs(module_code, doc_type="api")
```

### Changelog Entry
```python
diff = "Added new authentication feature..."
result = generate_docs(diff, doc_type="changelog")
```

## Styles

### Google Style
```python
"""Brief description.

Args:
    param1: Description.

Returns:
    Description.
"""
```

### NumPy Style
```python
"""Brief description.

Parameters
----------
param1 : type
    Description.

Returns
-------
type
    Description.
"""
```

### Sphinx Style
```python
"""Brief description.

:param param1: Description.
:return: Description.
"""
```

## Error Handling

```python
result = generate_docs(code)

if "error" in result:
    print(f"Generation failed: {result['error']}")
else:
    print(result["documentation"])
```

## Troubleshooting

### API Key Not Set
Set ANTHROPIC_API_KEY or OPENROUTER_API_KEY.

### Timeout
Large codebases may timeout. Generate docs for smaller sections.
