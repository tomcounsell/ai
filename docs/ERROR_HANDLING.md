# Error Handling Guidelines

This document outlines the standardized approach to error handling and logging across the application.

## Core Principles

1. **Consistency**: All errors follow a standard structure and format
2. **Contextual Information**: Error messages include relevant context for debugging
3. **User-Friendly**: User-facing errors are clear and helpful
4. **Security**: Internal details are hidden from users in production

## Error Handling Components

The project provides a comprehensive error handling system in `apps.common.utilities.logger`:

- **Exception Classes**: A hierarchy of application-specific exceptions
- **Error Logging**: Centralized logging with consistent formatting
- **View Error Handling**: Middleware for handling exceptions in views
- **API Error Handling**: DRF exception handler for consistent API responses
- **Decorators and Mixins**: Tools for handling errors in different contexts

## Exception Classes

Use these standardized exceptions throughout the application:

```python
# Import exception classes
from apps.common.utilities.logger import (
    AppError,           # Base exception class
    ValidationError,    # For data validation errors (400)
    AuthenticationError, # For auth failures (401)
    PermissionError,    # For permission issues (403) 
    NotFoundError,      # For missing resources (404)
    ConflictError       # For resource conflicts (409)
)

# Raise with custom details
raise ValidationError(
    message="Invalid form data",
    code="invalid_form",
    field_errors={"name": ["This field is required"]}
)
```

## Error Handling in Views

### Class-Based Views

Use the `ErrorHandlingMixin` with class-based views:

```python
from django.views import View
from apps.common.utilities.logger import ErrorHandlingMixin

class MyView(ErrorHandlingMixin, View):
    def get(self, request):
        # If this raises an exception, it will be handled automatically
        return render(request, 'template.html')
```

### Function-Based Views

Use the `error_decorator` with function-based views:

```python
from apps.common.utilities.logger import error_decorator

@error_decorator
def my_view(request):
    # If this raises an exception, it will be handled automatically
    return render(request, 'template.html')
```

## Error Handling in API Views

DRF views use the custom exception handler configured in `settings.third_party.py`:

```python
# Raise application errors in API views
from apps.common.utilities.logger import NotFoundError

def get_object(self):
    try:
        return super().get_object()
    except Exception:
        raise NotFoundError(
            message="The requested resource was not found",
            code="resource_not_found",
            details={"resource_type": self.queryset.model.__name__}
        )
```

## Form Validation

Use the `BaseModelForm` for consistent form validation:

```python
from apps.common.utilities.forms import BaseModelForm

class UserForm(BaseModelForm):
    # Define required fields for automatic validation
    required_fields = ["username", "email"]
    
    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name"]
    
    def validate_form(self, cleaned_data):
        # Custom validation logic
        if cleaned_data.get("username") == "admin":
            self.add_error("username", "This username is reserved")
```

## Error Response Formats

### HTML Responses

HTML errors use the `error.html` template with a standardized context:

```html
{% extends "base.html" %}

{% block content %}
  {% include "components/common/error_message.html" %}
{% endblock %}
```

### API Responses

API errors have a consistent JSON structure:

```json
{
  "error": "The requested resource was not found",
  "code": "not_found",
  "status_code": 404,
  "detail": {
    "resource_type": "User",
    "resource_id": 123
  }
}
```

### HTMX Responses

HTMX errors render a partial template with appropriate status codes:

```html
<div id="error-message" hx-swap-oob="true">
  {% include "components/common/error_message.html" %}
</div>
```

## Logging

Use the centralized logger for consistent logging:

```python
from apps.common.utilities.logger import logger, log_error

# Simple logging
logger.info("User logged in successfully")
logger.error("Failed to process payment")

# Exception logging with context
try:
    # Code that might raise an exception
    process_payment(user, amount)
except Exception as e:
    log_error(e, request, level=logging.ERROR)
```

## Error Code Standards

Error codes follow a standardized format:

- `validation_error`: Data validation issues
- `not_found`: Resource not found
- `permission_denied`: Permission issues
- `authentication_error`: Authentication failures
- `conflict`: Resource conflicts
- `server_error`: Server/internal errors

## Best Practices

1. **Always Use Application Exceptions**: Use the provided exception classes
2. **Be Specific**: Use descriptive error messages and appropriate status codes
3. **Include Context**: Add relevant details to help with debugging
4. **Handle Gracefully**: Use try/except to catch and handle specific errors
5. **Log Appropriately**: Use the right log level for different error types
6. **Don't Expose Internals**: Hide implementation details from users
7. **Use Decorators**: Apply error handling decorators to all views

## Research Tool Error Surfacing

When any research tool API returns an error or empty response, failures are surfaced
directly in the artifact content rather than being silently swallowed into a generic skipped state.
This applies uniformly across all six research sources: Perplexity, Grok, Gemini, GPT-Researcher,
Together, and Claude.

### Artifact Prefix Conventions

Two prefixes are used to distinguish intentional degradation from unexpected failures:

| Prefix | Meaning | Example |
|--------|---------|---------|
| `[SKIPPED: ...]` | Intentional graceful degradation — the API key is not configured or the service is explicitly unavailable | `[SKIPPED: PERPLEXITY_API_KEY not configured]` |
| `[FAILED: ...]` | Unexpected error — the API was called but returned an error or empty content | `[FAILED: Perplexity API 429 - rate_limit_exceeded]` |

#### When `[SKIPPED: ...]` is written

- The tool's API key environment variable is not set (e.g. `PERPLEXITY_API_KEY`, `GROK_API_KEY`, `GEMINI_API_KEY`).
- The service is explicitly configured as unavailable.

`[SKIPPED: ...]` is **never** written for API call failures — those always use `[FAILED: ...]`.

#### When `[FAILED: ...]` is written

HTTP-based tools (Perplexity, Grok, Gemini):
- Non-200 HTTP status: `[FAILED: {ToolName} API {status_code} - {error_type}]`
- 200 with empty content: `[FAILED: {ToolName} API returned empty content]`

Framework/exception-based tools (GPT-Researcher, Claude):
- Exception raised: `[FAILED: {ToolName} {ExceptionType} - {message}]`
- Empty return: `[FAILED: {ToolName} returned empty content]`

Together (hybrid):
- Timeout: `[FAILED: Together TIMEOUT - timed out after {n}s]`
- Exception: `[FAILED: Together {ExceptionType} - {message}]`
- Empty: `[FAILED: Together returned empty content]`

### Error Metadata

When a `[FAILED: ...]` artifact is written, the raw error details are stored in
`artifact.metadata["error"]` for debugging:

```python
# API error case
metadata = {"error": str(error_body or error_message)}

# Empty content case
metadata = {"error": "API returned no content"}
```

### UI Status Resolution

`_resolve_substep_status()` in `apps/podcast/services/workflow_progress.py` parses artifact
content prefixes to drive UI status indicators:

```python
if content.startswith("[FAILED:"):
    # Extracts message between "[FAILED: " and trailing "]"
    error_msg = content[len("[FAILED: "):].rstrip("]").strip()
    return ("failed", error_msg)  # UI shows red failed state with error text
if content.startswith("[SKIPPED:"):
    return ("skipped", "")        # UI shows grey skipped state (no error shown)
```

This means:
- `[FAILED: ...]` artifacts render as **failed** in the workflow UI with the error message visible.
- `[SKIPPED: ...]` artifacts render as **skipped** — no error is shown, because the skip was expected.

### Implementation

The error surfacing pattern is implemented consistently across the tool and service layers:

**Tool layer** — each tool returns `tuple[str | None, dict]`:
- On success: `(content_text, {})`  or `(content_text, metadata_dict)`
- On API error: `(None, {"_error_status": <code>, "_error_message": <reason>, "_error_body": <raw>})`
- On exception (GPT-Researcher, Claude): raises exception (service layer catches it)

**Service layer** (`apps/podcast/services/research.py`) — each `run_*_research()` function:
1. Checks for the API key before calling the tool (writes `[SKIPPED: ...]` if missing)
2. Calls the tool and unpacks `(content_text, response_data)`
3. On `None` or empty content: reads `_error_status`/`_error_message` from `response_data`, writes `[FAILED: ...]`
4. On success: writes the content as the artifact body

**Tool files:**
- `apps/podcast/tools/perplexity_deep_research.py` — reference implementation
- `apps/podcast/tools/grok_deep_research.py` — `_handle_error_response()` returns structured error dict
- `apps/podcast/tools/gemini_deep_research.py` — `submit_research()` returns `tuple` (no longer raises `GeminiQuotaError`)
- `apps/podcast/tools/gpt_researcher_run.py` — `run_research()` wraps library call in try/except, returns `tuple`
- `apps/podcast/tools/together_deep_research/runner.py` — error metadata enriched with `_error_status`/`_error_message`

## Testing Errors

Test error handling with specific test cases:

```python
def test_validation_error_handling(self):
    # Test form with invalid data
    response = self.client.post("/create-user/", {"email": "invalid"})
    self.assertEqual(response.status_code, 400)
    self.assertIn("error", response.json())
```
