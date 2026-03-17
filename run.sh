#!/bin/bash

# Start the WebSocket server in the background
python3 ws_server.py &

# Start the HTTP proxy server
python3 start.py
