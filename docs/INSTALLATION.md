# Installation Guide

This guide covers the complete installation and startup process for Nuke AI Collaborator on different platforms.

## Quick Start

### Prerequisites

- **Python 3.11+** - Check with `python --version`
- **Node.js 18+** - Check with `node --version`
- **8GB+ RAM** recommended (4GB minimum)
- **Visual C++ Build Tools** (Windows only) - Required for `chromadb` and other packages with native extensions

---

## One-Command Setup (Recommended)

### macOS / Linux

```bash
./start.sh
```

This script will:
1. Verify Python and Node.js are installed
2. Create a Python virtual environment
3. Install backend dependencies
4. Start the backend server

### Windows

**Double-click** `start.bat` or run in PowerShell:

```powershell
.\start.bat
```

> ⚠️ **Note**: The `start.bat` script starts the **backend only**. You need to start the frontend separately:
> ```powershell
> cd frontend
> npm install    # First time only
> npm run dev
> ```

This script will:
1. Verify Python and Node.js are installed
2. Create a Python virtual environment
3. Install backend dependencies
4. Start the backend server

---

## First-Time Setup

When you first start the application:

1. **Database is created automatically**:
   - Central DB: `backend/chat.db` (users, groups, members, templates)
   - Group DBs: `backend/workspaces/group_X/chat.db` (messages, sessions) - created lazily

2. **Visit the app**: Open [http://localhost:5173](http://localhost:5173)

3. **Register an account** - Create your admin account

4. **Create your first group** - Click the "+" button in the sidebar

5. **Add AI members** - Use the "Add Bot" feature to add assistants

---

## Complete Setup: Two Terminals

The application requires **two running processes**: backend API + frontend dev server.

### Terminal 1: Backend

**macOS / Linux:**
```bash
./start.sh
# or manually:
cd backend
source venv/bin/activate
python3 -m uvicorn main:app --reload --port 8000
```

**Windows (PowerShell):**
```powershell
.\start.bat
# or manually:
cd backend
venv\Scripts\activate
python -m uvicorn main:app --reload --port 8000
```

### Terminal 2: Frontend

**macOS / Linux:**
```bash
cd frontend
npm install     # First time only
npm run dev
```

**Windows (PowerShell):**
```powershell
cd frontend
npm install     # First time only
npm run dev
```

Once both are running:
- Backend: http://localhost:8000
- Frontend: http://localhost:5173

---

## Manual Setup

### Backend

#### 1. Install Dependencies

```bash
cd backend

# Create virtual environment (recommended)
python3 -m venv venv  # or use: py -m venv venv on Windows

# Activate virtual environment
source venv/bin/activate       # macOS/Linux
venv\Scripts\activate.bat      # Windows

# Install dependencies
pip install -r requirements.txt
```

#### 2. Configure API Keys (Optional)

Create `backend/.env` or configure via UI after first login:

```env
# OpenAI (required for AI features)
OPENAI_API_KEY=sk-...

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-...

# DeepSeek
DEEPSEEK_API_KEY=sk-...

# Local model (Ollama) - no key needed
# OLLAMA_BASE_URL=http://localhost:11434
```

#### 3. Start Backend

```bash
# Development mode (auto-reload on code changes)
python3 -m uvicorn main:app --reload --port 8000

# Production mode
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

---

### Frontend (New Terminal)

**Important**: Start the frontend in a **separate terminal** from the backend.

```bash
cd frontend

# Install dependencies (only needed once or when dependencies change)
npm install

# Start development server
npm run dev
```

The frontend will be available at: **http://localhost:5173**

The frontend dev server automatically proxies `/api`, `/uploads`, and `/ws` requests to the backend at `http://localhost:8000`.

---

## Platform-Specific Notes

### Windows

#### If `pip` or `npm` is not recognized

Ensure the tools are in your PATH:

1. **Python**: Install from [python.org](https://www.python.org/) and check **"Add Python to PATH"** during installation
2. **Node.js**: Install from [nodejs.org](https://nodejs.org/) (LTS version recommended)

#### If `python` command not found

Use the Python Launcher:

```powershell
py -m venv venv
py -m pip install -r requirements.txt
py -m uvicorn main:app --reload --port 8000
```

#### If installation fails with build errors

If you see errors like `Microsoft Visual C++ 14.0 or greater is required`:

**Install Visual C++ Build Tools:**
1. Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. Run the installer
3. Select **"Desktop development with C++"** workload
4. This is required for packages like `chromadb`, `numpy`, `psutil` that have native extensions

Alternatively, try installing pre-built wheels:
```powershell
pip install --only-binary :all: -r requirements.txt
```

#### Check if port 8000 is available

```powershell
netstat -ano | findstr :8000
```

If something is using it, stop it or use a different port (also update frontend proxy config):
```powershell
# Find the PID, then kill it
tasklist | findstr <PID>
taskkill /PID <PID> /F
```

```powershell
netstat -ano | findstr :8000
```

---

### macOS

#### If `pip3` not found

```bash
# Install Python via Homebrew
brew install python@3.12

# Verify installation
python3 --version
pip3 --version
```

---

### Linux (Ubuntu/Debian)

```bash
# Install Python 3.11+
sudo apt update
sudo apt install python3.11 python3.11-venv python3-pip

# Install Node.js 18+
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
```

---

## Troubleshooting

### Backend won't start

| Error | Solution |
|-------|----------|
| `ModuleNotFoundError: No module named 'fastapi'` | Dependencies not installed - run `pip install -r requirements.txt` |
| `Address already in use` | Port 8000 is already in use - stop the other process or use a different port |
| `websocket: command not found` | Run with `python3 -m uvicorn` instead of `python -m uvicorn` |

### Frontend won't start

| Error | Solution |
|-------|----------|
| `command not found: node` | Node.js not installed or not in PATH |
| `Cannot find module` | Run `npm install` again |
| `EACCES: permission denied` | On Linux/macOS, remove `node_modules` and reinstall: `rm -rf node_modules && npm install` |

### WebSocket connection failed

1. Verify backend is running: `curl http://localhost:8000/api/health`
2. Check Windows Firewall / macOS Firewall isn't blocking port 8000
3. Ensure frontend `vite.config.ts` has correct proxy: `http://localhost:8000`

### Virtual environment issues

On Windows, if activating the venv fails:

```powershell
# Use full path
backend\venv\Scripts\activate.bat

# Or use Python Launcher
py -m venv backend\venv
py backend\venv\Scripts\activate
```

---

## Project Structure After Installation

```
nuke-ai-collaborator/
├── backend/
│   ├── venv/              # Python virtual environment (created on first run)
│   ├── uploads/           # Uploaded files storage
│   ├── chroma_db/         # Local vector database
│   ├── main.py            # Application entry point
│   └── requirements.txt   # Python dependencies
├── frontend/
│   ├── node_modules/      # Node dependencies (created after npm install)
│   ├── dist/              # Built production files (after npm run build)
│   └── package.json       # Node dependencies
├── start.sh               # Quick start script (macOS/Linux)
└── start.bat              # Quick start script (Windows)
```

---

## Updating

### Update Backend Dependencies

```bash
cd backend
source venv/bin/activate  # or activate on Windows
pip install -r requirements.txt --upgrade
```

### Update Frontend Dependencies

```bash
cd frontend
npm update
```

---

## Uninstallation

### 1. Stop the application

Press `Ctrl+C` in the terminal running the backend and frontend.

### 2. Remove generated files

```bash
# Backend
rm -rf backend/venv backend/*.db backend/uploads/* backend/chroma_db

# Frontend
rm -rf frontend/node_modules frontend/dist

# Or use the start scripts (they don't clean up)
```

### 3. Remove project files

```bash
cd ..
rm -rf nuke-ai-collaborator
```

---

## Next Steps

1. **First Login**: Create an account or log in with existing credentials
2. **Configure AI Models**: Go to Settings → AI to add your API keys
3. **Create Your First Group**: Click "+" in the sidebar
4. **Add Bot Members**: Use the "Add Bot" feature to add AI assistants

For detailed feature documentation, see:
- [Architecture](ARCHITECTURE.md)
- [Features](README.md#features)
- [Troubleshooting Guide](TROUBLESHOOTING.md) - if you create one
