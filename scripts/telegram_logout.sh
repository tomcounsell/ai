#!/bin/bash

# Telegram logout/cleanup script
# Removes session files and clears authentication

echo "🔐 Telegram Logout & Cleanup"
echo "============================"
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT" || exit 1

# Function to cleanup session files
cleanup_sessions() {
    echo "🧹 Cleaning up Telegram session files..."
    
    # Kill any processes that might be using session files FIRST
    LOCKING_PIDS=$(lsof ai_project_bot.session* 2>/dev/null | awk 'NR>1 {print $2}' | sort -u)
    if [ -n "$LOCKING_PIDS" ]; then
        echo "  ⚠️  Found processes holding session files, terminating..."
        echo "$LOCKING_PIDS" | xargs kill -9 2>/dev/null
        sleep 2
        echo "  ✅ Terminated processes holding session files"
    fi
    
    # Remove main session file
    if [ -f "ai_project_bot.session" ]; then
        rm -f "ai_project_bot.session"
        echo "  ✅ Removed ai_project_bot.session"
    fi
    
    # Remove any session journal files
    if ls ai_project_bot.session-journal* 1> /dev/null 2>&1; then
        rm -f ai_project_bot.session-journal*
        echo "  ✅ Removed session journal files"
    fi
    
    # Remove any backup session files
    if ls *.session.bak 1> /dev/null 2>&1; then
        rm -f *.session.bak
        echo "  ✅ Removed backup session files"
    fi
    
    # Remove ALL session-related files (more aggressive)
    rm -f ai_project_bot.session* 2>/dev/null
    rm -f *.session 2>/dev/null
    rm -f *.session.* 2>/dev/null
    
    # Clean up Python cache that might hold credentials
    echo "  🧹 Cleaning Python cache..."
    find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
    find . -name "*.pyc" -delete 2>/dev/null
    find . -name "*.pyo" -delete 2>/dev/null
    echo "  ✅ Python cache cleaned"
}

# Function to try graceful logout first
attempt_logout() {
    echo "🔍 Attempting graceful logout..."
    
    python -c "
import asyncio
import os
import sys

# Add project root to path
sys.path.insert(0, '.')

async def logout():
    try:
        # Check if session exists
        if not os.path.exists('ai_project_bot.session'):
            print('  ℹ️  No active session found')
            return True
            
        from pyrogram import Client
        
        # Try to load and logout
        app = Client(
            'ai_project_bot',
            workdir='.'
        )
        
        await app.start()
        me = await app.get_me()
        print(f'  📱 Logging out user: {me.first_name} (@{me.username})')
        
        # Send logout request
        await app.log_out()
        await app.stop()
        
        print('  ✅ Successfully logged out from Telegram')
        return True
        
    except Exception as e:
        print(f'  ⚠️  Graceful logout failed: {e}')
        return False

# Run logout
result = asyncio.run(logout())
sys.exit(0 if result else 1)
" 2>/dev/null
    
    return $?
}

# Main execution
echo "⚠️  This will remove all Telegram authentication data!"
echo ""

# Try graceful logout first
if attempt_logout; then
    echo ""
fi

# Always do cleanup (even if graceful logout failed)
cleanup_sessions

# Kill any remaining Python processes that might be using the session
echo ""
echo "🔍 Checking for orphaned processes..."

# More comprehensive process cleanup
TELEGRAM_PIDS=$(pgrep -f "telegram|pyrogram|auth\.py|ai_project_bot" 2>/dev/null)
PYTHON_TELEGRAM_PIDS=$(pgrep -f "python.*telegram" 2>/dev/null)
AUTH_PIDS=$(pgrep -f "python.*auth\.py" 2>/dev/null)

ALL_PIDS=$(echo "$TELEGRAM_PIDS $PYTHON_TELEGRAM_PIDS $AUTH_PIDS" | tr ' ' '\n' | sort -u | grep -v '^$')

if [ -n "$ALL_PIDS" ]; then
    echo "  ⚠️  Found Telegram-related processes, terminating..."
    echo "$ALL_PIDS" | xargs kill -9 2>/dev/null
    sleep 1
    echo "  ✅ Cleaned up orphaned processes"
else
    echo "  ✅ No orphaned processes found"
fi

# Final verification that session is gone
if [ -f "ai_project_bot.session" ] || ls ai_project_bot.session* 1> /dev/null 2>&1; then
    echo ""
    echo "  ⚠️  Session files still exist, force removing..."
    rm -rf ai_project_bot.session* 2>/dev/null
    rm -rf *.session 2>/dev/null
    echo "  ✅ Force removed remaining session files"
fi

echo ""
echo "✅ TELEGRAM LOGOUT COMPLETE"
echo ""
echo "You have been logged out and all session data removed."
echo ""
echo "To authenticate again:"
echo "  1. Run: scripts/telegram_login.sh"
echo "  2. Enter your phone number"
echo "  3. Enter the verification code"
echo ""
echo "Then start the system with: scripts/start.sh"