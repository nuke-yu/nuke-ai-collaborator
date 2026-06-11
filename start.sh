#!/bin/bash
# Nuke AI Collaborator - Quick Start Script for macOS/Linux
# Run this script from the project root directory

set -e

echo "============================================"
echo "  Nuke AI Collaborator - Starting..."
echo "============================================"
echo ""

# Check if Python is installed
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed."
    echo "Please install Python 3.11+ from https://www.python.org/"
    echo ""
    echo "On macOS with Homebrew:"
    echo "  brew install python@3.12"
    exit 1
fi

# Check if Node.js is installed
if ! command -v node &> /dev/null; then
    echo "[ERROR] Node.js is not installed."
    echo "Please install Node.js 18+ from https://nodejs.org/"
    echo ""
    echo "On macOS with Homebrew:"
    echo "  brew install node"
    exit 1
fi

echo "[OK] Python and Node.js are installed."
echo ""

# Setup Backend
echo "=== Backend Setup ==="
if [ ! -f backend/requirements.txt ]; then
    echo "[ERROR] backend/requirements.txt not found."
    echo "Please check that you're running this script from the project root directory."
    exit 1
fi

if [ ! -d backend/venv ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv backend/venv
fi

echo "Installing backend dependencies..."
echo "(This may take a few minutes for the first run...)"
backend/venv/bin/pip install -r backend/requirements.txt

echo ""
echo "=== Starting Application ==="
echo ""
echo "Backend will run at: http://localhost:8000"
echo "Frontend setup (run in separate terminal):"
echo "  cd frontend"
echo "  npm install    (first time only)"
echo "  npm run dev"
echo ""
echo "Press Ctrl+C to stop the backend."
echo "============================================"
echo ""

backend/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
