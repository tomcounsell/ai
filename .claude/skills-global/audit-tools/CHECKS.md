# Audit Checks

10 checks across 4 categories. Each check has a verification method so results are deterministic and reproducible.

## Category 1: Structure (3 checks)

### 1. `manifest-exists` — FAIL if missing

The tool must have a `manifest.json` with required fields: `name`, `version`, `description`, `type`, `status`, `capabilities`.

**Verify:**
```bash
# File exists
test -f tools/{name}/manifest.json

# Required fields present (use jq or python)
python -c "
import json, sys
m = json.load(open('tools/{name}/manifest.json'))
required = ['name', 'version', 'description', 'type', 'status', 'capabilities']
missing = [f for f in required if f not in m]
if missing:
    print(f'FAIL: missing fields: {missing}')
    sys.exit(1)
if not m['capabilities']:
    print('WARN: capabilities list is empty')
print('PASS')
"
```

### 2. `readme-exists` — FAIL if missing

The tool must have a `README.md` with these sections: Overview, Installation/Requirements, Quick Start, API Reference or Command Reference.

**Verify:**
```bash
test -f tools/{name}/README.md
grep -c "^##" tools/{name}/README.md  # Should have at least 3 sections
```

Check for required section headers (case-insensitive):
- Overview or description paragraph at top
- Installation, Requirements, or Setup
- Quick Start or Usage
- API Reference, Command Reference, or Functions

### 3. `tests-exist` — FAIL if missing

The tool must have a `tests/` directory with at least one `test_*.py` file.

**Verify:**
```bash
ls tools/{name}/tests/test_*.py 2>/dev/null
```

---

## Category 2: Interface Documentation (4 checks)

### 4. `inputs-documented` — WARN if incomplete

Every public function in `__init__.py` (and `cli.py` if present) should have type-annotated parameters. The README should document each public function's parameters.

**Verify:**
```bash
# Find public functions without type hints
grep -n "^def [a-z]" tools/{name}/__init__.py | grep -v "-> "
grep -n "^def [a-z]" tools/{name}/__init__.py | grep -v ": "

# Count public functions in code vs documented in README
grep -c "^def [a-z]" tools/{name}/__init__.py
grep -c "^### \`" tools/{name}/README.md
```

A function is considered documented if it appears as a `### \`function_name\`` heading in the README with parameter descriptions.

### 5. `output-types` — WARN if incomplete

Public functions should have return type annotations. The README should describe the shape of returned data (dict keys, list contents, etc.).

**Verify:**
```bash
# Functions missing return type
grep -n "^def [a-z]" tools/{name}/__init__.py | grep -v " -> "
```

For functions returning dicts, the README or docstring should document the keys. For functions returning custom objects, the class should be documented.

### 6. `examples` — WARN if missing

README must have at least 2 working code examples showing realistic usage — not pseudocode, not just function signatures.

**Verify:**
```bash
# Count python code blocks in README
grep -c '```python' tools/{name}/README.md  # Should be >= 2
```

Examples should show imports, function calls with real-looking arguments, and what to do with the return value.

### 7. `error-docs` — INFO if missing

README or docstrings should document error handling: what exceptions can be raised, what error return values look like, how failures are communicated.

**Verify:**
```bash
# Check for error/exception documentation
grep -i -c "error\|exception\|raise\|fail" tools/{name}/README.md
```

At minimum, document whether the tool returns `{"error": ...}` dicts, raises exceptions, or returns None on failure.

---

## Category 3: Test Coverage (2 checks)

### 8. `test-coverage` — FAIL if capabilities untested

Every capability listed in `manifest.json` should have at least one corresponding test. A capability is "tested" if there's a test method whose name contains the capability name or a closely related term.

**Verify:**
```python
import json
m = json.load(open('tools/{name}/manifest.json'))
capabilities = m.get('capabilities', [])

# Get test method names
import subprocess
result = subprocess.run(
    ['grep', '-r', 'def test_', 'tools/{name}/tests/'],
    capture_output=True, text=True
)
test_names = result.stdout.lower()

untested = []
for cap in capabilities:
    # Normalize: read_messages -> check for "read" or "message" in test names
    terms = cap.replace('_', ' ').split()
    if not any(term in test_names for term in terms):
        untested.append(cap)

if untested:
    print(f'FAIL: untested capabilities: {untested}')
```

### 9. `tests-passing` — FAIL if tests fail

Run the tool's tests and verify they pass.

**Verify:**
```bash
python -m pytest tools/{name}/tests/ -v --tb=short -q 2>&1 | tail -5
```

If tests require external services (APIs, databases) that aren't available, note as WARN rather than FAIL. Check for `@pytest.mark.integration` or `@pytest.mark.slow` markers.

---

## Category 4: CLI (1 check)

### 10. `cli-quality` — WARN if incomplete

If the tool has a CLI entry point (registered in `pyproject.toml` or has `cli.py`), its `--help` output must be comprehensive.

**Verify:**
```bash
# Check if registered in pyproject.toml
grep "{name}" pyproject.toml

# If registered, run --help and check quality
valor-{cli-name} --help 2>&1
```

**Comprehensive --help means:**
- Description of what the tool does (not just the tool name)
- All arguments listed with descriptions
- All options/flags listed with descriptions
- At least one usage example
- Help text is more than 5 lines

If the tool has no CLI entry point but should (type is `cli` or `library` with common use cases), that's a WARN suggesting it should be registered.

---

## Severity Summary

| Severity | Checks | Meaning |
|----------|--------|---------|
| **FAIL** | manifest-exists, readme-exists, tests-exist, test-coverage, tests-passing | Broken or missing fundamentals |
| **WARN** | inputs-documented, output-types, examples, cli-quality | Incomplete but functional |
| **INFO** | error-docs | Nice to have |

## Skip Rules

- Skip `_template/` — it's a scaffold, not a real tool
- Skip `__pycache__/` — build artifact
- Skip standalone `.py` files in `tools/` root (e.g., `validate.py`, `classifier.py`) — these are utilities, not tools. Only audit directories.
- If a tool directory contains only an empty `__init__.py` and nothing else, report it as `FAIL: placeholder — no implementation` and skip remaining checks
