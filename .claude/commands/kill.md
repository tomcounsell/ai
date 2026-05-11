# Kill server on port

Kill the server running on the specified port.

Usage: /kill [port]
Default: port 8000

<bash>
PORT="${1:-8000}"
lsof -ti:$PORT | xargs kill -9
echo "Killed process on port $PORT"
</bash>