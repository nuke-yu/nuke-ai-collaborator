#!/usr/bin/env python3
"""Headless mode CLI entry point.

Usage:
    python -m backend.headless auto --group-id 1 --member-id 2
    python -m backend.headless discuss "Review this code" --group-id 1 --member-id 2 --json
    python -m backend.headless next --group-id 1 --member-id 2 --resume session_123
"""

import sys
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from backend.headless import main

if __name__ == "__main__":
    main()
