# Screenshot Service

The Django Project Template includes a powerful screenshot service to help with UI development, debugging, and documentation.

## Features

- Capture screenshots of any page in your application
- Support for both headless and visible browser captures
- Automatic authentication support
- Wait for specific elements before capturing
- Full-page or viewport-only screenshots
- Optional AI-powered captures for complex scenarios
- Command-line and Python API interfaces
- Django management command integration

## Installation

The screenshot service requires the following dependencies:

```bash
# Basic capture functionality
uv add --dev playwright

# For AI-powered captures (optional)
uv add --dev browser-use

# Install Playwright browsers
playwright install
```

## Usage

### Django Management Command

The easiest way to use the screenshot service is through the Django management command:

```bash
# Basic screenshot of a page
python manage.py capture_screenshot /todos/

# Capture with authentication
python manage.py capture_screenshot /account/profile/ --username admin

# Capture full page with custom filename
python manage.py capture_screenshot /todos/ --full-page --filename todos_full.png

# Capture with specific viewport size
python manage.py capture_screenshot /todos/ --width 375 --height 667

# Wait for a specific element
python manage.py capture_screenshot /todos/ --wait-for "#todo-list"

# Use visible browser (helpful for debugging)
python manage.py capture_screenshot /todos/ --visible

# AI-powered capture with instructions (requires browser-use)
python manage.py capture_screenshot /todos/ --use-agent --instructions "Click the 'Create Todo' button and fill in the form"
```

### Python API

You can also use the screenshot service directly in your Python code:

```python
from apps.common.utilities.screenshots import ScreenshotService, capture_screenshot

# Quick single screenshot
screenshot_path = capture_screenshot('/todos/')

# More customized screenshot
service = ScreenshotService(
    output_dir='custom_screenshots',
    server_url='http://localhost:8000',
    viewport={"width": 1280, "height": 800},
    headless=True,
    wait_before_capture=500
)

# Capture with options
screenshot_path = service.capture(
    '/todos/',
    filename='my_screenshot.png',
    wait_for_selector='#todo-list',
    full_page=True
)

# AI-powered capture (requires browser-use)
screenshot_path = service.capture_with_browser_agent(
    '/todos/',
    instructions='Click the "Create Todo" button and fill in the form',
    filename='create_todo.png'
)
```

### Standalone Script

You can also use the service as a standalone script:

```bash
# Run the script directly
python apps/common/utilities/screenshots.py /todos/ --full-page

# With more options
python apps/common/utilities/screenshots.py /accounts/login/ \
    --filename login_screen.png \
    --output-dir screenshots/auth \
    --wait-for "#login-form" \
    --visible
```

## Common Use Cases

### Documentation

The screenshot service is ideal for generating documentation screenshots:

```bash
# Create a docs directory
mkdir -p docs/screenshots

# Capture screenshots for documentation
python manage.py capture_screenshot /todos/ --output-dir docs/screenshots --filename todo_list.png
python manage.py capture_screenshot /todos/create/ --output-dir docs/screenshots --filename todo_create.png
```

### Visual Testing

Combine with testing to create visual test cases:

```python
def test_todo_visual():
    # Create test data
    todo = TodoItem.objects.create(title="Test Todo")
    
    # Capture before state
    service = ScreenshotService(output_dir='test_results')
    service.capture('/todos/', filename='before_create.png')
    
    # Perform action
    # ...
    
    # Capture after state
    service.capture('/todos/', filename='after_create.png')
```

### UI Debugging

The screenshot service is helpful for UI debugging:

```bash
# Capture in visible mode to debug UI issues
python manage.py capture_screenshot /todos/ --visible --wait-ms 5000
```

### Multi-Viewport Testing

Test your responsive design by capturing the same page at different viewport sizes:

```bash
# Desktop viewport
python manage.py capture_screenshot /todos/ --width 1280 --height 800 --filename todos_desktop.png

# Tablet viewport
python manage.py capture_screenshot /todos/ --width 768 --height 1024 --filename todos_tablet.png

# Mobile viewport
python manage.py capture_screenshot /todos/ --width 375 --height 667 --filename todos_mobile.png
```

## Advanced Usage

### Authentication

For authenticated pages, you can either:

1. Use the `--username` parameter with the management command
2. Provide cookies to the `capture()` method:

```python
from django.test import Client
from django.contrib.auth import get_user_model

# Create a client and log in
User = get_user_model()
user = User.objects.get(username='admin')
client = Client()
client.force_login(user)

# Extract cookies
cookies = []
for key, value in client.cookies.items():
    cookies.append({
        "name": key,
        "value": value.value,
        "domain": "localhost",
        "path": "/"
    })

# Use cookies with screenshot service
service = ScreenshotService()
service.capture('/account/profile/', cookies=cookies)
```

### AI-Powered Captures

For more complex scenarios, you can use the AI-powered capture with browser-use:

```python
service = ScreenshotService(use_browser_agent=True)
service.capture_with_browser_agent(
    '/todos/',
    instructions="""
    1. Click on "Create Todo" button
    2. Fill in the title field with "Test Todo"
    3. Select "High" for priority
    4. Click the "Save" button
    5. Wait for the todo to appear in the list
    """
)
```

## Troubleshooting

### Common Issues

1. **Missing browser error**: Run `playwright install` to install the required browsers.

2. **No screenshots produced**: Make sure the server is running at the configured URL.

3. **Authentication issues**: Check that the user exists and has the required permissions.

4. **Elements not found**: Use `--wait-ms` to wait longer for elements to appear, or specify a selector with `--wait-for`.

5. **AI agent errors**: Ensure browser-use is installed and your instructions are clear and specific.

### Tips

- Use `--visible` for debugging capture issues
- Increase `wait_before_capture` or `--wait-ms` for pages with dynamic content
- For complex captures, use `--use-agent` with detailed instructions
