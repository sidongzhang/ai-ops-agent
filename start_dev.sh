#!/bin/bash
# Start backend
echo "Starting backend..."
cd "$(dirname "$0")/backend"
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --port 8000 &
BPID=$!
sleep 2

# Start frontend
echo "Starting frontend..."
cd "$(dirname "$0")/frontend"
npx vite --port 5173 &
FPID=$!

echo "Backend PID=$BPID, Frontend PID=$FPID"
echo "Backend: http://localhost:8000"
echo "Frontend: http://localhost:5173"
echo "Docs: http://localhost:8000/docs"
wait
