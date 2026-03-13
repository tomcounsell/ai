# Adding Reflection Tasks

A developer guide for adding new steps to the reflections system (`scripts/reflections.py`). Use this as a reference alongside the template step (Step 16: Disk Space Check) which demonstrates every convention.

## Step Method Template

Copy this template and replace the placeholders:

```python
async def step_your_step_name(self) -> None:
    """Step N: One-line description of what this step does.

    Longer description of the check, including what it looks for
    and what findings it produces.
    """
    findings: list[str] = []

    try:
        # --- Your logic here ---
        # Collect issues into the local `findings` list.
        # For each finding, also persist it to state:

        if some_condition:
            finding = "Description of what was found"
            findings.append(finding)
            self.state.add_finding("your_step_name", finding)
            logger.warning(finding)
        else:
            logger.info("Everything looks good")

    except Exception:
        logger.exception("Failed to run your step name")

    self.state.step_progress["your_step_name"] = {
        "findings": len(findings),
    }
```

## Registration

After creating the method, register it in `__init__` inside the `self.steps` list. Steps are numbered sequentially:

```python
self.steps = [
    # ... existing steps ...
    (N, "Your Step Name", self.step_your_step_name),
]
```

Update the module-level docstring at the top of `scripts/reflections.py` to include the new step number and description.

## Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Method name | `step_<snake_case_key>` | `step_disk_space_check` |
| Finding key | Same snake_case key | `"disk_space_check"` |
| Progress key | Same snake_case key | `self.state.step_progress["disk_space_check"]` |
| Step tuple name | Title Case | `"Disk Space Check"` |

All three keys (finding, progress, method suffix) must match exactly so that findings, progress metrics, and the step name stay correlated.

## Required Patterns

Every step must follow these conventions:

1. **Signature**: `async def step_<key>(self) -> None` -- async, no return value.
2. **Local findings list**: `findings: list[str] = []` at the top of the method body.
3. **Persist findings**: Call `self.state.add_finding("<key>", text)` for each finding.
4. **Record progress**: Set `self.state.step_progress["<key>"]` with at minimum `{"findings": len(findings)}` at the end, even on zero findings.
5. **Try/except wrapper**: The entire step body must be wrapped in `try/except Exception` with `logger.exception()` so a single step failure never halts the run.
6. **Logging**: Use `logger.warning()` for findings and `logger.info()` for normal status.

## Test Pattern

Add a test class to `tests/test_reflections.py`. The standard test structure covers three cases:

```python
class TestYourStepName:
    """Tests for the your-step-name step (step N)."""

    @pytest.mark.asyncio
    async def test_normal_case_no_findings(self):
        """No findings when everything is healthy."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()

        # Mock external calls to simulate healthy state
        with patch("scripts.reflections.some_function", return_value=good_value):
            await runner.step_your_step_name()

        progress = runner.state.step_progress.get("your_step_name", {})
        assert progress["findings"] == 0

    @pytest.mark.asyncio
    async def test_problem_detected_creates_finding(self):
        """Creates a finding when the problem is detected."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()

        # Mock external calls to simulate the problem
        with patch("scripts.reflections.some_function", return_value=bad_value):
            await runner.step_your_step_name()

        progress = runner.state.step_progress.get("your_step_name", {})
        assert progress["findings"] == 1
        findings = runner.state.findings.get("your_step_name", [])
        assert len(findings) == 1

    @pytest.mark.asyncio
    async def test_exception_handled_gracefully(self):
        """Exceptions are caught and logged, not propagated."""
        from scripts.reflections import ReflectionRunner

        runner = ReflectionRunner()

        with patch(
            "scripts.reflections.some_function",
            side_effect=Exception("boom"),
        ):
            await runner.step_your_step_name()  # Should NOT raise

        progress = runner.state.step_progress.get("your_step_name", {})
        assert progress["findings"] == 0
```

## Checklist

When adding a new step:

- [ ] Create `step_<key>` method following the template above
- [ ] Register as `(N, "Step Name", self.step_<key>)` in `self.steps`
- [ ] Update the module docstring step list at the top of `scripts/reflections.py`
- [ ] Add test class to `tests/test_reflections.py` with the three standard test cases
- [ ] Update `docs/features/reflections.md` step count and table
- [ ] Run `pytest tests/test_reflections.py -x -q` to verify

## Reference Implementation

See `step_disk_space_check` (Step 16) in `scripts/reflections.py` for the canonical example. It uses `shutil.disk_usage()` to check free space and demonstrates all conventions in a minimal, self-contained step.
