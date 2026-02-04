---
description: Prepare application for review by starting services and ensuring accessibility for testing
---

# Prepare App

Prepare the application for review by starting necessary services and ensuring the app is accessible.

## When to Use

- Before running `/review` command with UI validation
- Before manual testing of web applications
- When setting up local development environment for testing
- Before browser automation with `agent-browser`

## Instructions

Follow these steps to prepare the application for review:

### 1. Detect Project Type

**Check for common project indicators:**

```bash
# Django project
test -f manage.py && echo "Django project detected"

# Node/React/Next.js project
test -f package.json && echo "Node project detected"

# Go project
test -f go.mod && echo "Go project detected"

# Determine which setup to run
```

### 2. Django Projects

**For Django applications:**

```bash
# Activate virtual environment if exists
if [ -d venv ]; then
    source venv/bin/activate
elif [ -d .venv ]; then
    source .venv/bin/activate
fi

# Check if server is already running
if lsof -i:8000 >/dev/null 2>&1; then
    echo "✅ Django server already running on port 8000"
else
    # Start server in background
    python manage.py runserver --noreload &
    SERVER_PID=$!

    # Wait for server to be ready
    echo "Starting Django server..."
    sleep 3

    # Verify server is responding
    if curl -s http://localhost:8000/ >/dev/null; then
        echo "✅ Django server ready at http://localhost:8000"
        echo "PID: $SERVER_PID"
    else
        echo "❌ Server failed to start"
        kill $SERVER_PID 2>/dev/null
        exit 1
    fi
fi
```

### 3. Node/React Projects

**For Node-based applications:**

```bash
# Check if server is already running on common ports
if lsof -i:3000 >/dev/null 2>&1; then
    echo "✅ Server already running on port 3000"
elif lsof -i:3001 >/dev/null 2>&1; then
    echo "✅ Server already running on port 3001"
else
    # Determine start command
    if grep -q "\"dev\":" package.json; then
        START_CMD="npm run dev"
    elif grep -q "\"start\":" package.json; then
        START_CMD="npm start"
    else
        echo "❌ No start command found in package.json"
        exit 1
    fi

    # Start server in background
    echo "Starting server with: $START_CMD"
    $START_CMD &
    SERVER_PID=$!

    # Wait for server to be ready
    sleep 5

    # Check common ports
    if curl -s http://localhost:3000/ >/dev/null; then
        echo "✅ Server ready at http://localhost:3000"
        echo "PID: $SERVER_PID"
    elif curl -s http://localhost:3001/ >/dev/null; then
        echo "✅ Server ready at http://localhost:3001"
        echo "PID: $SERVER_PID"
    else
        echo "❌ Server failed to start"
        kill $SERVER_PID 2>/dev/null
        exit 1
    fi
fi
```

### 4. Go Projects

**For Go applications:**

```bash
# Check if server is running
if lsof -i:8080 >/dev/null 2>&1; then
    echo "✅ Server already running on port 8080"
else
    # Start server
    echo "Starting Go server..."
    go run . &
    SERVER_PID=$!

    # Wait for server
    sleep 3

    # Verify
    if curl -s http://localhost:8080/ >/dev/null; then
        echo "✅ Go server ready at http://localhost:8080"
        echo "PID: $SERVER_PID"
    else
        echo "❌ Server failed to start"
        kill $SERVER_PID 2>/dev/null
        exit 1
    fi
fi
```

### 5. Generic Web Server Check

**If project type unclear, check for running web servers:**

```bash
# Check common development ports
for port in 3000 3001 8000 8080 5000 4200; do
    if lsof -i:$port >/dev/null 2>&1; then
        echo "✅ Server found running on port $port"
        echo "URL: http://localhost:$port"
        exit 0
    fi
done

echo "❌ No running server detected on common ports"
echo "Please start your application manually or specify custom port"
exit 1
```

### 6. Database Services (if needed)

**Check and start database services:**

```bash
# PostgreSQL
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -q; then
        echo "✅ PostgreSQL is running"
    else
        echo "⚠️  PostgreSQL not running. Start with: brew services start postgresql"
    fi
fi

# Redis
if command -v redis-cli >/dev/null 2>&1; then
    if redis-cli ping >/dev/null 2>&1; then
        echo "✅ Redis is running"
    else
        echo "⚠️  Redis not running. Start with: brew services start redis"
    fi
fi

# MySQL
if command -v mysql >/dev/null 2>&1; then
    if mysqladmin ping >/dev/null 2>&1; then
        echo "✅ MySQL is running"
    else
        echo "⚠️  MySQL not running. Start with: brew services start mysql"
    fi
fi
```

### 7. Environment Variables Check

**Verify critical environment variables:**

```bash
# Check for .env file
if [ -f .env ]; then
    echo "✅ .env file found"

    # Check for common required variables
    if grep -q "DATABASE_URL" .env; then
        echo "  ✅ DATABASE_URL configured"
    fi

    if grep -q "SECRET_KEY" .env; then
        echo "  ✅ SECRET_KEY configured"
    fi
else
    echo "⚠️  No .env file found. May need environment configuration."
fi
```

### 8. Output Summary

**Report application status:**

```
🚀 Application Preparation Summary

Project Type: {detected_type}
Server Status: {running/started/failed}
Server URL: {url}
Server PID: {pid} (if newly started)

Database Services:
  PostgreSQL: {status}
  Redis: {status}
  MySQL: {status}

Environment: {configured/needs_setup}

✅ Ready for review
```

## Cleanup Instructions

**To stop servers started by this command:**

```bash
# Kill server by PID
kill {SERVER_PID}

# Or kill by port
lsof -ti:8000 | xargs kill
lsof -ti:3000 | xargs kill
```

## Error Handling

**If server fails to start:**
1. Check for port conflicts: `lsof -i:{port}`
2. Check for missing dependencies: `npm install` or `pip install -r requirements.txt`
3. Verify environment variables are set
4. Check application logs for startup errors

**If database connection fails:**
1. Verify database service is running
2. Check connection string in .env
3. Test database connectivity manually
4. Check database logs

## Integration Notes

**Works with:**
- `/review` - Ensures app is running before screenshots
- `agent-browser` - Prepares environment for browser automation
- Local development workflow

**Server lifecycle:**
- Detects already-running servers (no duplicate starts)
- Starts servers in background for review
- Provides PID for manual cleanup
- Uses --noreload for Django to prevent auto-restart interference

## Example Usage

```bash
# Auto-detect and prepare
/prepare_app

# Manual verification after
curl http://localhost:8000/
```

## Best Practices

1. **Check before starting**: Don't start duplicate servers
2. **Wait for ready**: Give servers time to fully initialize
3. **Verify with curl**: Confirm server is actually responding
4. **Track PIDs**: Make cleanup easy
5. **Handle errors gracefully**: Report why server didn't start

## Notes

- Servers started in background may need manual cleanup after review
- Django uses `--noreload` flag to prevent auto-restart during review
- Node servers may take longer to compile and start (5-10 seconds typical)
- Database services are checked but not auto-started (require user permission)
