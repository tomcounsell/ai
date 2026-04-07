#!/bin/bash
# Valor Bridge Service Manager
# Self-management script for the Telegram bridge
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
WORKER_PLIST_NAME="com.valor.worker"
WORKER_PLIST_PATH="$HOME/Library/LaunchAgents/${WORKER_PLIST_NAME}.plist"
LOG_DIR="$PROJECT_DIR/logs"
PID_FILE="$PROJECT_DIR/data/bridge.pid"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

usage() {
    echo "Valor Service Manager"
    echo ""
    echo "Usage: $0 <command>"
    echo ""
    echo "Bridge commands:"
    echo "  start       Start the bridge service"
    echo "  stop        Stop the bridge service"
    echo "  restart     Restart the bridge service"
    echo "  status      Check service status"
    echo "  install     Install launchd service (auto-start on boot)"
    echo "  uninstall   Remove launchd service"
    echo "  logs        Tail the bridge logs"
    echo "  health      Check if bridge is healthy and responding"
    echo ""
    echo "Worker commands:"
    echo "  worker-start    Start the standalone worker"
    echo "  worker-stop     Stop the standalone worker"
    echo "  worker-restart  Restart the standalone worker"
    echo "  worker-status   Check worker status"
    echo "  worker-logs     Tail the worker logs"
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

    PROJECTS_JSON="${PROJECTS_CONFIG_PATH:-$HOME/Desktop/Valor/projects.json}"
    if [ ! -f "$PROJECTS_JSON" ]; then
        echo "ERROR: projects.json not found at $PROJECTS_JSON"
        echo "  mkdir -p ~/Desktop/Valor"
        echo "  cp $PROJECT_DIR/config/projects.example.json ~/Desktop/Valor/projects.json"
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

is_launchd_loaded() {
    launchctl list "$PLIST_NAME" &>/dev/null
}

# Log rotation constants
LOG_MAX_SIZE=$((10 * 1024 * 1024))  # 10MB
LOG_MAX_BACKUPS=3

rotate_log() {
    # Rotate a log file if it exceeds LOG_MAX_SIZE.
    # Keeps LOG_MAX_BACKUPS rotated copies (e.g. bridge.error.log.1, .2, .3).
    local log_file="$1"
    if [ ! -f "$log_file" ]; then
        return 0
    fi

    local file_size
    file_size=$(stat -f%z "$log_file" 2>/dev/null || stat --printf="%s" "$log_file" 2>/dev/null || echo 0)

    if [ "$file_size" -gt "$LOG_MAX_SIZE" ]; then
        echo "Rotating $log_file (${file_size} bytes > ${LOG_MAX_SIZE} limit)"
        # Shift existing backups: .3 -> deleted, .2 -> .3, .1 -> .2
        local i=$LOG_MAX_BACKUPS
        while [ "$i" -gt 1 ]; do
            local prev=$((i - 1))
            if [ -f "${log_file}.${prev}" ]; then
                mv "${log_file}.${prev}" "${log_file}.${i}"
            fi
            i=$prev
        done
        # Current -> .1
        mv "$log_file" "${log_file}.1"
        # Create fresh empty file
        touch "$log_file"
    fi
}

start_bridge() {
    # Warn about pending critical dependency upgrades
    if [ -f "$PROJECT_DIR/data/upgrade-pending" ]; then
        echo "WARNING: Critical dependency upgrade pending. Run /update to apply."
        cat "$PROJECT_DIR/data/upgrade-pending"
    fi

    # Rotate oversized log files before starting.
    # All launchd-managed logs (StandardOutPath/StandardErrorPath) are rotated here
    # because launchd holds file descriptors open — newsyslog alone cannot reliably
    # rotate actively-written files. This runs on every service start/restart.
    rotate_log "$LOG_DIR/bridge.error.log"
    rotate_log "$LOG_DIR/bridge.log"
    rotate_log "$LOG_DIR/watchdog.log"
    rotate_log "$LOG_DIR/reflections.log"
    rotate_log "$LOG_DIR/reflections_error.log"

    echo "Starting Valor bridge..."

    # Ensure environment is ready
    cd "$PROJECT_DIR"
    if ! ensure_setup; then
        echo "Setup checks failed. Fix the issues above and try again."
        return 1
    fi

    # Prefer launchd if plist exists (it handles KeepAlive, logging, etc.)
    if [ -f "$PLIST_PATH" ]; then
        if ! is_launchd_loaded; then
            launchctl load "$PLIST_PATH"
        else
            launchctl kickstart "gui/$(id -u)/$PLIST_NAME"
        fi
    else
        # Fallback: manual start (no launchd service installed)
        nohup "$VENV/bin/python" bridge/telegram_bridge.py \
            >> "$LOG_DIR/bridge.log" \
            2>> "$LOG_DIR/bridge.error.log" &
        echo $! > "$PID_FILE"
    fi

    # Wait a moment and verify
    sleep 2
    if is_running; then
        local pid=$(get_pid)
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

    # If launchd manages the bridge, unload to prevent auto-respawn
    if is_launchd_loaded; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        # launchctl unload sends SIGTERM and waits for exit
        sleep 2
        if ! is_running; then
            echo "Bridge stopped (via launchd)"
            rm -f "$PID_FILE"
            return 0
        fi
    fi

    # Fallback: manual kill
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

    # If launchd manages the bridge, use kickstart -k for atomic kill+restart
    if is_launchd_loaded; then
        launchctl kickstart -k "gui/$(id -u)/$PLIST_NAME"
        sleep 2
        if is_running; then
            local pid=$(get_pid)
            echo "Bridge restarted (PID: $pid)"
        else
            echo "Restart failed. Check logs: $LOG_DIR/bridge.error.log"
            return 1
        fi
    else
        # Fallback: manual stop/start
        stop_bridge
        sleep 1
        start_bridge
    fi

    # Also restart the watchdog so it picks up new code
    if [ -f "$WATCHDOG_PLIST_PATH" ]; then
        echo "Restarting bridge watchdog..."
        launchctl kickstart -k "gui/$(id -u)/$WATCHDOG_PLIST_NAME" 2>/dev/null || true
        echo "Watchdog restarted"
    fi
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
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin</string>
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

    # Install update polling (runs remote-update.sh every 30 minutes)
    echo ""
    echo "Installing update polling..."
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
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin</string>
        <key>HOME</key>
        <string>${HOME}</string>
    </dict>
    <key>StartInterval</key>
    <integer>1800</integer>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/update.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/update.log</string>
</dict>
</plist>
UPDATEEOF
    launchctl load "$UPDATE_PLIST_PATH"
    echo "Update polling installed (runs every 30 minutes)"

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
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/usr/sbin:/bin</string>
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

    # Install newsyslog config for launchd-managed log rotation.
    # newsyslog is built into macOS and runs hourly — it provides a safety net
    # for log files that grow between service restarts. The rotate_log function
    # in start_bridge() handles rotation on each restart; newsyslog catches
    # files that grow large during long-running service uptime.
    echo ""
    echo "Installing newsyslog log rotation config..."
    NEWSYSLOG_SRC="$PROJECT_DIR/config/newsyslog.valor.conf"
    NEWSYSLOG_DST="/etc/newsyslog.d/valor.conf"
    if [ -f "$NEWSYSLOG_SRC" ]; then
        # Update paths in the config to match this machine's project directory
        sed "s|/Users/valorengels/src/ai|${PROJECT_DIR}|g" "$NEWSYSLOG_SRC" | sudo tee "$NEWSYSLOG_DST" > /dev/null
        echo "newsyslog config installed at $NEWSYSLOG_DST"
    else
        echo "WARNING: newsyslog config not found at $NEWSYSLOG_SRC"
    fi

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

    if [ -f "$WORKER_PLIST_PATH" ]; then
        launchctl unload "$WORKER_PLIST_PATH" 2>/dev/null || true
        rm -f "$WORKER_PLIST_PATH"
        echo "Worker service uninstalled"
    else
        echo "Worker service was not installed"
    fi

    # Also stop any running processes
    stop_bridge
    stop_worker
}

tail_logs() {
    echo "Tailing bridge logs (Ctrl+C to stop)..."
    tail -f "$LOG_DIR/bridge.log" "$LOG_DIR/bridge.error.log" 2>/dev/null
}

# === Worker Management ===

get_worker_pid() {
    pgrep -f "python -m worker" 2>/dev/null || pgrep -f "python.*worker/__main__" 2>/dev/null || true
}

is_worker_running() {
    local pid=$(get_worker_pid)
    [ -n "$pid" ]
}

is_worker_launchd_loaded() {
    launchctl list "$WORKER_PLIST_NAME" &>/dev/null
}

start_worker() {
    echo "Starting standalone worker..."

    # Rotate worker logs
    rotate_log "$LOG_DIR/worker.log"
    rotate_log "$LOG_DIR/worker_error.log"

    mkdir -p "$LOG_DIR/worker"

    if [ -f "$WORKER_PLIST_PATH" ]; then
        if ! is_worker_launchd_loaded; then
            launchctl load "$WORKER_PLIST_PATH"
        else
            launchctl kickstart "gui/$(id -u)/$WORKER_PLIST_NAME"
        fi
    else
        # Fallback: manual start
        cd "$PROJECT_DIR"
        nohup "$VENV/bin/python" -m worker \
            >> "$LOG_DIR/worker.log" \
            2>> "$LOG_DIR/worker_error.log" &
    fi

    sleep 2
    if is_worker_running; then
        local pid=$(get_worker_pid)
        echo "Worker started (PID: $pid)"
    else
        echo "Failed to start worker. Check logs: $LOG_DIR/worker_error.log"
        return 1
    fi
}

stop_worker() {
    local pid=$(get_worker_pid)

    if [ -z "$pid" ]; then
        echo "Worker is not running"
        return 0
    fi

    echo "Stopping worker (PID: $pid)..."

    if is_worker_launchd_loaded; then
        launchctl unload "$WORKER_PLIST_PATH" 2>/dev/null || true
        sleep 2
        if ! is_worker_running; then
            echo "Worker stopped (via launchd)"
            return 0
        fi
    fi

    kill "$pid" 2>/dev/null || true

    for i in {1..10}; do
        if ! is_worker_running; then
            echo "Worker stopped"
            return 0
        fi
        sleep 1
    done

    echo "Force killing worker..."
    kill -9 "$pid" 2>/dev/null || true
    echo "Worker stopped (forced)"
}

restart_worker() {
    echo "Restarting worker..."

    if is_worker_launchd_loaded; then
        launchctl kickstart -k "gui/$(id -u)/$WORKER_PLIST_NAME"
        sleep 2
        if is_worker_running; then
            local pid=$(get_worker_pid)
            echo "Worker restarted (PID: $pid)"
        else
            echo "Worker restart failed. Check logs: $LOG_DIR/worker_error.log"
            return 1
        fi
    else
        stop_worker
        sleep 1
        start_worker
    fi
}

status_worker() {
    local pid=$(get_worker_pid)

    if [ -n "$pid" ]; then
        echo "Worker Status: RUNNING"
        echo "PID: $pid"
        echo "Uptime: $(ps -o etime= -p $pid 2>/dev/null | xargs)"
        echo "Memory: $(ps -o rss= -p $pid 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')"

        if launchctl list | grep -q "$WORKER_PLIST_NAME"; then
            echo "Launchd: INSTALLED (auto-start enabled)"
        else
            echo "Launchd: NOT INSTALLED (manual start only)"
        fi
        return 0
    else
        echo "Worker Status: STOPPED"
        if launchctl list | grep -q "$WORKER_PLIST_NAME"; then
            echo "Launchd: INSTALLED (will auto-start)"
        fi
        return 1
    fi
}

tail_worker_logs() {
    echo "Tailing worker logs (Ctrl+C to stop)..."
    tail -f "$LOG_DIR/worker.log" "$LOG_DIR/worker_error.log" 2>/dev/null
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
        restart_worker
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
    worker-start)
        start_worker
        ;;
    worker-stop)
        stop_worker
        ;;
    worker-restart)
        restart_worker
        ;;
    worker-status)
        status_worker
        ;;
    worker-logs)
        tail_worker_logs
        ;;
    *)
        usage
        exit 1
        ;;
esac
