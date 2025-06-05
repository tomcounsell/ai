"""Test Claude execution directly."""

import subprocess
import time

# Test 1: Direct claude command
print("Test 1: Direct claude execution")
start = time.time()
try:
    result = subprocess.run(
        ["claude", "--print", "Say hello"],
        capture_output=True,
        text=True,
        timeout=10
    )
    elapsed = time.time() - start
    print(f"✅ Completed in {elapsed:.1f}s")
    print(f"Return code: {result.returncode}")
    print(f"Output: {result.stdout[:100]}...")
    if result.stderr:
        print(f"Stderr: {result.stderr}")
except subprocess.TimeoutExpired:
    print(f"❌ Timed out after 10s")
except Exception as e:
    print(f"❌ Error: {e}")

# Test 2: Test delegation function
print("\n\nTest 2: Delegation function")
from tools.valor_delegation_tool import execute_valor_delegation

try:
    result = execute_valor_delegation(
        prompt="Say hello",
        working_directory=".",
        timeout=10
    )
    print(f"✅ Got result: {result[:100]}...")
except Exception as e:
    print(f"❌ Error: {type(e).__name__}: {e}")

# Test 3: Test spawn_valor_session with short task
print("\n\nTest 3: spawn_valor_session")
from tools.valor_delegation_tool import spawn_valor_session

try:
    result = spawn_valor_session(
        task_description="say hello",
        target_directory=".",
        force_sync=True
    )
    print(f"✅ Got result: {result[:100]}...")
except Exception as e:
    print(f"❌ Error: {type(e).__name__}: {e}")