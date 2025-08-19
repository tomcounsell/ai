# Loops Integration

This directory contains integration with [Loops](https://loops.so/), a transactional email and customer messaging platform.

## Setup

1. Set up a Loops account at https://loops.so/
2. Create the email templates you need in the Loops dashboard
3. Add your Loops API key to your environment variables:

```bash
# In .env.local
LOOPS_API_KEY=your_loops_api_key_here
```

## Configuration

The Loops integration is configured in `settings/third_party.py`. In debug mode, emails are logged but not sent, which is useful for development.

## Usage

### LoopsClient

The `LoopsClient` class in `client.py` provides methods for interacting with the Loops API:

```python
from apps.integration.loops.client import LoopsClient

# Initialize client
loops_client = LoopsClient()

# Send a transactional email
loops_client.transactional_email(
    to_email="user@example.com",
    transactional_id="your_template_id",
    data_variables={
        "username": "JohnDoe",
        "reset_link": "https://example.com/reset"
    }
)

# Track an event
loops_client.event(
    to_email="user@example.com",
    event_name="completed_signup",
    event_properties={
        "signup_method": "google"
    }
)
```

### Shortcuts

The `shortcuts.py` file contains pre-configured functions for common email types:

```python
from apps.integration.loops.shortcuts import send_password_reset_email

# Send a password reset email
send_password_reset_email(user, reset_url)
```

## Adding New Templates

1. Create a new template in the Loops dashboard
2. Note the transactional ID (available in the template settings)
3. Add a new shortcut function in `shortcuts.py` or use the `LoopsClient` directly

## Testing

You can test the Loops integration using the provided test script:

```bash
python -m apps.integration.loops.tests.test_loops_debug
```

To test specific templates:

```bash
python -m apps.integration.loops.tests.test_loops_debug custom
python -m apps.integration.loops.tests.test_loops_debug notification
```

## Debug Mode

When `DEBUG=True` in your settings, the Loops client will log the email details instead of actually sending the email, which is useful for development and testing.
