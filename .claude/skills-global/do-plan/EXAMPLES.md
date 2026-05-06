# Planning Examples

Examples of how to handle different types of planning requests.

## Example: Vague Request

**User:** "Make the auth better"

**Response:**
```
I need to narrow this down. What specific problem are you trying to solve?

- Is login failing for certain users?
- Is the flow too complicated?
- Is there a security concern?
- Is it too slow?

Or is there a specific story: "Users with [X situation] can't [Y action] because [Z breakdown]"?
```

## Example: Grab-Bag Request

**User:** "Redesign the settings page - add dark mode, notification preferences, profile editing, and export functionality"

**Response:**
```
This feels like multiple separate features bundled together. Each of these could be its own plan:

1. Dark mode toggle
2. Notification preferences
3. Profile editing
4. Data export

Which one is most urgent? Or is there a single pain point driving all of these?
```

## Example: Good Request

**User:** "Users can't reschedule meetings when the original attendee is offline. They have to wait for them to come back online to approve the change."

**Response:**
```
Great - this is specific. I'll create a plan for async meeting rescheduling.

Starting with Medium appetite — solo dev + PM. One check-in to align on notification behavior, one review round before shipping.
```
