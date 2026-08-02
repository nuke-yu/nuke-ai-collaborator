import { spawn } from 'node:child_process';
import path from 'node:path';
import net from 'node:net';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');

let pythonProcess = null;

/**
 * Checks if a port on 127.0.0.1 is currently in use/listening.
 */
export function isPortListening(port = 8000) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(500);
    socket.on('connect', () => {
      socket.destroy();
      resolve(true);
    });
    socket.on('timeout', () => {
      socket.destroy();
      resolve(false);
    });
    socket.on('error', () => {
      resolve(false);
    });
    socket.connect(port, '127.0.0.1');
  });
}

/**
 * Finds an available random TCP port on localhost.
 */
export function findFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
    server.on('error', reject);
  });
}

/**
 * Spawns the backend Python process (FastAPI / Supervisor) as a sidecar if not running.
 */
export async function startPythonSidecar(port = 8000) {
  const alreadyRunning = await isPortListening(port);
  if (alreadyRunning) {
    console.log(`[PythonSidecar] Backend is already running and listening on port ${port}. Using existing server.`);
    return port;
  }

  const isDev = process.env.NODE_ENV === 'development' || !process.env.APP_PACKAGED;

  let pythonExec;
  let args;

  if (isDev) {
    // In development mode, use python inside backend/venv
    pythonExec = path.join(projectRoot, 'backend', 'venv', 'bin', 'python');
    const mainPy = path.join(projectRoot, 'backend', 'main.py');
    args = [mainPy, '--port', String(port)];
  } else {
    // In packaged mode, use pre-compiled standalone binary inside resources
    pythonExec = path.join(process.resourcesPath, 'sidecar', 'collaborator-backend');
    args = ['--port', String(port)];
  }

  console.log(`[PythonSidecar] Spawning backend sidecar: ${pythonExec} on port ${port}`);

  const env = {
    ...process.env,
    PORT: String(port),
    PYTHONUNBUFFERED: '1',
  };

  try {
    pythonProcess = spawn(pythonExec, args, {
      cwd: path.join(projectRoot, 'backend'),
      env,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    pythonProcess.stdout.on('data', (data) => {
      console.log(`[Backend STDOUT] ${data.toString().trim()}`);
    });

    pythonProcess.stderr.on('data', (data) => {
      console.error(`[Backend STDERR] ${data.toString().trim()}`);
    });

    pythonProcess.on('exit', (code, signal) => {
      console.log(`[PythonSidecar] Backend process exited with code ${code}, signal ${signal}`);
      pythonProcess = null;
    });

    return port;
  } catch (err) {
    console.error(`[PythonSidecar] Failed to spawn backend:`, err);
    throw err;
  }
}

/**
 * Cleanly kills the backend Python process when Electron shuts down.
 */
export function stopPythonSidecar() {
  if (pythonProcess) {
    console.log('[PythonSidecar] Terminating Python backend process...');
    pythonProcess.kill('SIGTERM');
    setTimeout(() => {
      if (pythonProcess) {
        pythonProcess.kill('SIGKILL');
      }
    }, 2000);
  }
}
