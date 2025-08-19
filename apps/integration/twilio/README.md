# Twilio Integration

This module provides integration with Twilio for SMS messaging and phone number verification.

## Features

- Send SMS messages via Twilio
- Verify phone numbers
- Track message delivery status with webhooks
- Seamless integration with the SMS model

## Configuration

Add the following settings to your environment:

```python
# In settings/base.py
TWILIO_ENABLED = env.bool("TWILIO_ENABLED", default=False)
TWILIO_ACCOUNT_SID = env.str("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = env.str("TWILIO_AUTH_TOKEN", default="")
TWILIO_PHONE_NUMBER = env.str("TWILIO_PHONE_NUMBER", default="")
```

And in your .env file:

```
TWILIO_ENABLED=True
TWILIO_ACCOUNT_SID=your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_PHONE_NUMBER=+19876543210
```

### Webhook Configuration

To receive status updates for sent messages, configure a webhook in your Twilio account:

1. Go to your Twilio Console
2. Navigate to Phone Numbers > Manage > Active Numbers
3. Click on your phone number
4. In the Messaging section, set the "A Message Comes In" webhook URL to:
   `https://your-domain.com/api/webhooks/twilio/`

## Usage

### Sending SMS

```python
from apps.common.models.sms import SMS
from apps.integration.twilio.shortcuts import send_sms

# Method 1: Using the SMS model
sms = SMS.objects.create(
    to_number="+12345678901",
    body="Hello from Twilio!"
)
result = sms.send()

# Method 2: Direct send with automatic database save
result = send_sms(
    to_number="+12345678901",
    body="Hello from Twilio!",
    save_to_db=True  # Creates an SMS record
)

# Get the SMS ID from the result
sms_id = result.get("sms_id")

# Method 3: Direct send without database save
result = send_sms(
    to_number="+12345678901",
    body="Hello from Twilio!",
    save_to_db=False  # No SMS record created
)
```

### Verifying Phone Numbers

```python
from apps.integration.twilio.shortcuts import verify_phone_number

result = verify_phone_number("+12345678901")

if result.get("success") and result.get("valid"):
    # Phone number is valid
    carrier = result.get("data", {}).get("carrier")
    print(f"Carrier: {carrier}")
else:
    # Invalid phone number or error
    print(f"Error: {result.get('error')}")
```

### Sending Verification Codes

```python
from apps.integration.twilio.shortcuts import send_verification_code

# Generate a code (you can use your own method)
code = "123456"

# Send the code
result = send_verification_code("+12345678901", code)

if result.get("success"):
    print("Verification code sent!")
else:
    print(f"Error: {result.get('error')}")
```

### Getting Message Status

```python
from apps.integration.twilio.shortcuts import get_sms_status

# Using SMS ID
result = get_sms_status(sms_id)

if result.get("success"):
    status = result.get("status")
    print(f"Message status: {status}")
    
    # Get additional details
    if "data" in result:
        print(f"Date sent: {result['data']['date_sent']}")
        print(f"Error (if any): {result['data']['error_message']}")
```

## Testing

The integration has a DEBUG mode that simulates API calls without actually contacting Twilio. This is automatically enabled when `settings.DEBUG` is `True` or when `TWILIO_ENABLED` is `False`.

To run tests:

```bash
DJANGO_SETTINGS_MODULE=settings pytest apps/integration/twilio/tests
```
