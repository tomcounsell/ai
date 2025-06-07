#!/usr/bin/env python3
"""Emergency script to rescue the system from runaway Claude processes."""

import os
import signal
import subprocess
import time

print("🚨 AGENT RESCUE MISSION INITIATED 🚨")
print("="*50)

# Find all Claude processes
try:
    result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
    lines = result.stdout.split('\n')
    
    claude_processes = []
    for line in lines:
        if 'claude' in line.lower() and 'grep' not in line:
            parts = line.split()
            if len(parts) > 1:
                pid = parts[1]
                claude_processes.append((pid, line))
    
    if not claude_processes:
        print("✅ No runaway Claude processes found!")
    else:
        print(f"🔍 Found {len(claude_processes)} Claude process(es):")
        for pid, line in claude_processes:
            print(f"   PID {pid}: {line[:100]}...")
        
        print("\n🎯 Attempting to terminate runaway processes...")
        for pid, _ in claude_processes:
            try:
                os.kill(int(pid), signal.SIGTERM)
                print(f"   ✅ Sent SIGTERM to PID {pid}")
            except Exception as e:
                print(f"   ❌ Failed to terminate PID {pid}: {e}")
        
        # Wait a moment
        time.sleep(2)
        
        # Check if any are still running and force kill
        for pid, _ in claude_processes:
            try:
                os.kill(int(pid), 0)  # Check if still running
                os.kill(int(pid), signal.SIGKILL)  # Force kill
                print(f"   ⚡ Force killed PID {pid}")
            except:
                pass  # Process already terminated
                
except Exception as e:
    print(f"❌ Rescue mission error: {e}")

print("\n🛠️  Cleaning up resources...")

# Try to clean up any locks
try:
    subprocess.run(['rm', '-f', 'ai_project_bot.session-journal'], capture_output=True)
    print("✅ Cleaned up session journal")
except:
    pass

print("\n💫 RESCUE MISSION COMPLETE")
print("The agent has been brought back to the light side!")
print("\nNext steps:")
print("1. Run 'scripts/stop.sh' to ensure clean shutdown")
print("2. Restart with 'scripts/start.sh'")
print("3. Add timeouts to promise executions to prevent future escapes!")