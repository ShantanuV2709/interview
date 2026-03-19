#!/bin/bash
set -m

# Ensure ports 3002 and 3000 are free
kill -9 $(lsof -t -i:3002,3000) 2>/dev/null

# Start the Logic Server (WebSocket) in the background
echo "🚀 Starting WebSocket Logic Server on port 3002..."
python3 ws_server.py &

# Start the HTTP Proxy Server in the foreground
echo "🌐 Starting HTTP Proxy on port 3000..."
python3 start.py

# Keep the script alive and bring background jobs to foreground if needed
wait
