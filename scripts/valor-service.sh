#!/bin/bash
# Valor Bridge Service Manager
# Self-management script for the Telegram-Clawdbot bridge
# Can be called by Valor himself to restart his own process

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_DIR/.venv"
PLIST_NAME="com.valor.bridge"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
UPDATE_PLIST_NAME="com.valor.update"
UPDATE_PLIST_PATH="$HOME/Library/LaunchAgents/${UPDATE_PLIST_NAME}.plist"
WATCHDOG_PLIST_NAME="com.valor.bridge-watchdog"
WATCHDOG_PLIST_PATH="$HOME/Library/LaunchAgents/${WATCHDOG_PLIST_NAME}.plist"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$PROJECT_DIR/data/bridge.pid"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

usage() {
    echo "Valor Bridge Service Manager"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Commands:"
    echo "  start       Start the bridge service"
    echo "  stop        Stop the bridge service"
    echo "  restart     Restart the bridge service"
    echo "  status      Check service status"
    echo "  install     Install launchd service (auto-start on boot)"
    echo "  uninstall   Remove launchd service"
    echo "  logs        Tail the bridge logs"
    echo "  health      Check if bridge is healthy and responding"
    echo ""
}

get_pid() {
    pgrep -f "telegram_bridge.py" 2>/dev/null || true
}

is_running() {
    local pid=$(get_pid)
    [ -n "$pid" ]
}

ensure_setup() {
    # Ensure virtual environment exists
    if [ ! -d "$VENV" ]; then
        echo "Creating virtual environment..."
        python3 -m venv "$VENV"
    fi

    # Ensure dependencies are installed (use explicit venv paths, no user-site)
    if ! "$VENV/bin/python" -c "import telethon; import httpx; import dotenv" 2>/dev/null; then
        echo "Installing dependencies..."
        "$VENV/bin/pip" install -e "$PROJECT_DIR" 2>&1
    fi

    # Check for required config files
    if [ ! -f "$PROJECT_DIR/.env" ]; then
        echo "ERROR: .env file not found."
        echo "  cp $PROJECT_DIR/.env.example $PROJECT_DIR/.env"
        echo "  # Then edit .env with your credentials"
        return 1
    fi

    if [ ! -f "$PROJECT_DIR/config/projects.json" ]; then
        echo "ERROR: config/projects.json not found."
        echo "  cp $PROJECT_DIR/config/projects.json.example $PROJECT_DIR/config/projects.json"
        echo "  # Then edit with your project settings"
        return 1
    fi

    # Warn about missing Telegram session
    if ! ls "$PROJECT_DIR"/data/*.session 2>/dev/null | grep -q .; then
        echo "WARNING: No Telegram session found. Run first:"
        echo "  $VENV/bin/python $PROJECT_DIR/scripts/telegram_login.py"
    fi

    return 0
}

start_bridge() {
    # Atomic process lock (prevents concurrent starts)
    local lock_dir="$PROJECT_DIR/data/bridge-start.lock"
    if ! mkdir "$lock_dir" 2>/dev/null; then
        echo "ERROR: Another bridge start/stop operation is in progress."
        echo "If this persists, remove: $lock_dir"
        return 1
    fi

    # Warn about pending critical dependency upgrades
    if [ -f "$PROJECT_DIR/data/upgrade-pending" ]; then
        echo "WARNING: Critical dependency upgrade pending. Run /update to apply."
        cat "$PROJECT_DIR/data/upgrade-pending"
    fi

    # Always stop any existing processes first to ensure clean state
    if is_running; then
        echo "Stopping existing bridge process..."
        stop_bridge
        sleep 2
    fi

    echo "Starting Valor bridge..."

    # Ensure environment is ready
    cd "$PROJECT_DIR"
    if ! ensure_setup; then
        echo "Setup checks failed. Fix the issues above and try again."
        rmdir "$lock_dir" 2>/dev/null || true
        return 1
    fi

    # Start with nohup so it survives terminal close (explicit venv python)
    nohup "$VENV/bin/python" bridge/telegram_bridge.py \
        >> "$LOG_DIR/bridge.log" \
        2>> "$LOG_DIR/bridge.error.log" &

    local pid=$!
    echo $pid > "$PID_FILE"

    # Wait a moment and verify
    sleep 2
    rmdir "$lock_dir" 2>/dev/null || true
    if is_running; then
        echo "Bridge started successfully (PID: $pid)"
        return 0
    else
        echo "Failed to start bridge. Check logs: $LOG_DIR/bridge.error.log"
        return 1
    fi
}

stop_bridge() {
    local pid=$(get_pid)

    if [ -z "$pid" ]; then
        echo "Bridge is not running"
        rm -f "$PID_FILE"
        return 0
    fi

    echo "Stopping bridge (PID: $pid)..."
    kill "$pid" 2>/dev/null || true

    # Wait for graceful shutdown (15s to let Telethon close SQLite session)
    for i in {1..15}; do
        if ! is_running; then
            echo "Bridge stopped"
            rm -f "$PID_FILE"
            return 0
        fi
        sleep 1
    done

    # Force kill if still running
    echo "Force killing bridge..."
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "Bridge stopped (forced)"
}

restart_bridge() {
    echo "Restarting Valor bridge..."
    stop_bridge
    sleep 1
    start_bridge
}

status_bridge() {
    local pid=$(get_pid)

    if [ -n "$pid" ]; then
        echo "Bridge Status: RUNNING"
        echo "PID: $pid"
        echo "Uptime: $(ps -o etime= -p $pid 2>/dev/null | xargs)"
        echo "Memory: $(ps -o rss= -p $pid 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')"

        # Check launchd status
        if launchctl list | grep -q "$PLIST_NAME"; then
            echo "Launchd: INSTALLED (auto-start enabled)"
        else
            echo "Launchd: NOT INSTALLED (manual start only)"
        fi
        return 0
    else
        echo "Bridge Status: STOPPED"
        if launchctl list | grep -q "$PLIST_NAME"; then
            echo "Launchd: INSTALLED (will auto-start)"
        fi
        return 1
    fi
}

health_check() {
    if ! is_running; then
        echo "UNHEALTHY: Bridge is not running"
        return 1
    fi

    # Check if Telegram is connected by looking at recent logs
    local last_log=$(tail -1 "$LOG_DIR/bridge.log" 2>/dev/null)
    local last_error=$(tail -1 "$LOG_DIR/bridge.error.log" 2>/dev/null)

    if echo "$last_error" | grep -qi "error\|exception\|disconnect"; then
        echo "UNHEALTHY: Recent errors detected"
        echo "Last error: $last_error"
        return 1
    fi

    echo "HEALTHY: Bridge is running and connected"
    return 0
}

install_service() {
    echo "Installing launchd service..."

    # Create plist
    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PROJECT_DIR}/.venv/bin/python</string>
        <string>${PROJECT_DIR}/bridge/telegram_bridge.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <!-- KeepAlive unconditional: restart on ANY exit (SIGTERM, crash, etc).
         ThrottleInterval below prevents rapid restart loops. -->
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/bridge.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/bridge.error.log</string>
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

    # Stop any running instance first
    stop_bridge

    # Load the bridge service
    launchctl load "$PLIST_PATH"

    echo "Bridge service installed and started"
    echo "Bridge will auto-start on boot"

    # Install update cron (runs remote-update.sh every 12 hours)
    echo ""
    echo "Installing update cron..."
    cat > "$UPDATE_PLIST_PATH" << UPDATEEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${UPDATE_PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${PROJECT_DIR}/scripts/remote-update.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
        <dict>
            <key>Hour</key>
            <integer>6</integer>
        </dict>
        <dict>
            <key>Hour</key>
            <integer>18</integer>
        </dict>
    </array>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/update.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/update.log</string>
</dict>
</plist>
UPDATEEOF
    launchctl load "$UPDATE_PLIST_PATH"
    echo "Update cron installed (runs at 06:00 and 18:00)"

    # Install bridge watchdog (runs every 60 seconds)
    echo ""
    echo "Installing bridge watchdog..."
    cat > "$WATCHDOG_PLIST_PATH" << WATCHDOGEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${WATCHDOG_PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PROJECT_DIR}/.venv/bin/python</string>
        <string>${PROJECT_DIR}/monitoring/bridge_watchdog.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/watchdog.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/watchdog.log</string>
</dict>
</plist>
WATCHDOGEOF
    launchctl load "$WATCHDOG_PLIST_PATH"
    echo "Bridge watchdog installed (runs every 60 seconds)"

    sleep 2
    status_bridge
}

uninstall_service() {
    echo "Uninstalling launchd services..."

    if [ -f "$PLIST_PATH" ]; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        rm -f "$PLIST_PATH"
        echo "Bridge service uninstalled"
    else
        echo "Bridge service was not installed"
    fi

    if [ -f "$UPDATE_PLIST_PATH" ]; then
        launchctl unload "$UPDATE_PLIST_PATH" 2>/dev/null || true
        rm -f "$UPDATE_PLIST_PATH"
        echo "Update cron uninstalled"
    else
        echo "Update cron was not installed"
    fi

    if [ -f "$WATCHDOG_PLIST_PATH" ]; then
        launchctl unload "$WATCHDOG_PLIST_PATH" 2>/dev/null || true
        rm -f "$WATCHDOG_PLIST_PATH"
        echo "Bridge watchdog uninstalled"
    else
        echo "Bridge watchdog was not installed"
    fi

    # Also stop any running process
    stop_bridge
}

tail_logs() {
    echo "Tailing bridge logs (Ctrl+C to stop)..."
    tail -f "$LOG_DIR/bridge.log" "$LOG_DIR/bridge.error.log" 2>/dev/null
}

# Main
case "${1:-}" in
    start)
        start_bridge
        ;;
    stop)
        stop_bridge
        ;;
    restart)
        restart_bridge
        ;;
    status)
        status_bridge
        ;;
    health)
        health_check
        ;;
    install)
        install_service
        ;;
    uninstall)
        uninstall_service
        ;;
    logs)
        tail_logs
        ;;
    *)
        usage
        exit 1
        ;;
esac
