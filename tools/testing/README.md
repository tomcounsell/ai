# Testing Tools

This directory contains tools to help manage, run, and report on tests in the Django Project Template.

## Available Tools

### Test Manager (`test_manager.py`)

Organizes and runs tests by category, and generates reports.

```bash
# List tests by category
python tools/testing/test_manager.py list --category unit

# Run tests by category
python tools/testing/test_manager.py run --category e2e

# Generate HTML report for a category
python tools/testing/test_manager.py report --category all
```

### Browser Test Runner (`browser_test_runner.py`)

Runs browser-based tests with a managed Django server.

```bash
# Run E2E tests with Chrome in headless mode
python tools/testing/browser_test_runner.py apps/public/tests/test_e2e_*.py

# Run with visible browser
python tools/testing/browser_test_runner.py --no-headless apps/public/tests/test_visual_*.py

# Use Firefox instead of Chrome
python tools/testing/browser_test_runner.py --browser firefox apps/public/tests/test_e2e_*.py
```

### Coverage Reporter (`coverage_reporter.py`)

Generates coverage reports in various formats.

```bash
# Generate HTML coverage report
python tools/testing/coverage_reporter.py apps/common/tests/

# Generate XML report for CI integration
python tools/testing/coverage_reporter.py --type xml apps/

# Generate badge from XML report
python tools/testing/coverage_reporter.py --type xml --badge apps/
```

## Test Categories

- **Unit**: Tests of individual functions, classes, or small components
- **Integration**: Tests of interactions between components
- **E2E**: End-to-end tests that simulate user interactions
- **Visual**: Tests that check the visual appearance of components
- **API**: Tests of API endpoints
- **Performance**: Tests that measure performance metrics

## Test Scopes

- **Model**: Tests for Django models
- **View**: Tests for Django views
- **Form**: Tests for Django forms
- **Behavior**: Tests for behavior mixins
- **Component**: Tests for UI components
- **Workflow**: Tests for user workflows

## Adding New Tests

When adding new tests, follow these naming conventions to ensure proper categorization:

- Unit tests for models: `apps/<app>/tests/test_models/test_<model>.py`
- Integration tests: `apps/<app>/tests/test_integration_*.py`
- E2E tests: `apps/<app>/tests/test_e2e_*.py` or `apps/<app>/tests/test_*_browser.py`
- Visual tests: `apps/<app>/tests/test_visual_*.py` or `apps/<app>/tests/test_screenshot_*.py`
- API tests: `apps/<app>/tests/test_api_*.py`

## Continuous Integration

For CI environments, you can run tests and generate reports with:

```bash
python tools/testing/test_manager.py run --category all --xml
```

This will generate XML reports in the `reports/` directory for use with CI systems.
