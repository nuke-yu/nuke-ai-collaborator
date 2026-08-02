#!/usr/bin/env python3
"""
PyInstaller build script to compile the Python backend (FastAPI/Supervisor)
into a single standalone executable for Electron packaging.
"""
import os
import sys
import subprocess

def main():
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    dist_dir = os.path.join(backend_dir, "dist")
    entry_point = os.path.join(backend_dir, "main.py")

    print(f"[BuildBinary] Compiling {entry_point} into standalone binary...")

    cmd = [
        "pyinstaller",
        "--noconfirm",
        "--onedir",
        "--name", "collaborator-backend",
        "--distpath", dist_dir,
        "--workpath", os.path.join(backend_dir, "build"),
        "--clean",
        entry_point
    ]

    try:
        subprocess.run(cmd, check=True)
        print(f"[BuildBinary] Successfully built standalone backend at: {dist_dir}")
    except FileNotFoundError:
        print("[BuildBinary] PyInstaller not installed in current env. Run: pip install pyinstaller")
    except subprocess.CalledProcessError as e:
        print(f"[BuildBinary] Build failed with exit code: {e.returncode}")

if __name__ == "__main__":
    main()
