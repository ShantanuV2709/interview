#!/bin/bash
set -e

echo "🚀 Starting Ideal IT API Microservice on port 3000..."
uvicorn app:app --host 0.0.0.0 --port 3000
