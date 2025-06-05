"""Simple test to verify promise functionality."""

import subprocess
import sys

print("Testing promise system...")

# Test 1: Claude CLI availability
print("\n1. Testing Claude CLI...")
try:
    result = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=5)
    print(f"✅ Claude CLI found: {result.stdout.strip()}")
except Exception as e:
    print(f"❌ Claude CLI error: {e}")
    sys.exit(1)

# Test 2: Simple prompt execution
print("\n2. Testing simple Claude execution...")
try:
    result = subprocess.run(
        ["claude", "--print", "Say hello"], 
        capture_output=True, 
        text=True, 
        timeout=30
    )
    if result.returncode == 0:
        print(f"✅ Claude responded: {result.stdout[:100]}...")
    else:
        print(f"❌ Claude failed with code {result.returncode}")
        print(f"STDERR: {result.stderr}")
except subprocess.TimeoutExpired:
    print("❌ Claude execution timed out")
except Exception as e:
    print(f"❌ Claude execution error: {e}")

# Test 3: Promise generation
print("\n3. Testing promise generation...")
try:
    from tools.valor_delegation_tool import spawn_valor_session, estimate_task_duration
    
    # Test duration estimation
    duration = estimate_task_duration("create comprehensive documentation")
    print(f"✅ Duration estimated: {duration}s")
    
    # Test promise generation
    result = spawn_valor_session(
        "create comprehensive documentation",
        ".",
        force_sync=False
    )
    
    if "ASYNC_PROMISE|" in result:
        print("✅ Promise generated correctly")
    else:
        print(f"❌ No promise generated. Result: {result[:100]}...")
        
except Exception as e:
    print(f"❌ Promise generation error: {e}")
    import traceback
    traceback.print_exc()

# Test 4: Database operations
print("\n4. Testing database operations...")
try:
    from utilities.database import create_promise, get_promise, update_promise_status
    
    # Create a test promise
    promise_id = create_promise(12345, 67890, "Test task")
    print(f"✅ Created promise ID: {promise_id}")
    
    # Retrieve promise
    promise = get_promise(promise_id)
    if promise and promise['status'] == 'pending':
        print("✅ Promise retrieved successfully")
    else:
        print(f"❌ Promise retrieval failed: {promise}")
        
    # Update promise
    update_promise_status(promise_id, "completed", result_summary="Test complete")
    promise = get_promise(promise_id)
    if promise and promise['status'] == 'completed':
        print("✅ Promise update successful")
    else:
        print(f"❌ Promise update failed: {promise}")
        
except Exception as e:
    print(f"❌ Database error: {e}")
    import traceback
    traceback.print_exc()

print("\n✅ All simple tests completed!")