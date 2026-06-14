# Headless Mode User Guide

## Overview

Headless mode allows you to run Nuke AI Collaborator tasks from the command line, making it suitable for:
- **CI/CD integration** - Automated testing and code review
- **Scriptable execution** - Automate AI workflows
- **Long-running sessions** - Background tasks with auto-restart
- **Session resume** - Continue interrupted tasks

## Installation

Headless mode is included in the backend package. No additional installation required.

## Quick Start

### Basic Usage

```bash
# Run auto mode
python -m backend.headless auto --group-id 1 --member-id 2

# Run discussion
python -m backend.headless discuss "Review this code" --group-id 1 --member-id 2

# Run with JSON output
python -m backend.headless next --group-id 1 --member-id 2 --json

# Resume a previous session
python -m backend.headless auto --group-id 1 --member-id 2 --resume session_123
```

### Using the Script

```bash
./backend/scripts/headless auto --group-id 1 --member-id 2 --json
```

## Command Reference

### Available Commands

| Command | Description |
|---------|-------------|
| `auto` | Run auto-mode (continuous AI assistance) |
| `next` | Advance to next milestone |
| `discuss` | Start a discussion on a topic |
| `plan` | Create a development plan |

### Options

```
positional arguments:
  command               Command to execute (auto/next/discuss/plan)
  query                 Query or content to process

optional arguments:
  -h, --help            Show help message
  --group-id GROUP_ID   Target group ID (required)
  --member-id MEMBER_ID Member ID sending the message (required)
  --query-text TEXT     Alternative to positional query
  --output-format {text,json,stream-json}
                        Output format (default: text)
  --json                Output as JSON (shortcut)
  --timeout TIMEOUT     Timeout in milliseconds (default: 300000)
  --response-timeout TIMEOUT
                        Response timeout in milliseconds (default: 30000)
  --resume SESSION_ID   Session ID to resume
  --max-restarts N      Maximum auto-restarts on error (default: 3)
  --verbose             Enable verbose output
  --bare                Suppress CLAUDE.md, AGENTS.md, etc.
```

## Exit Codes

Headless mode uses standardized exit codes (based on gsd-2):

| Code | Constant | Description |
|------|----------|-------------|
| 0 | `ExitCode.SUCCESS` | Task completed successfully |
| 1 | `ExitCode.ERROR` | Error or timeout occurred |
| 10 | `ExitCode.BLOCKED` | Blocked (requires human intervention) |
| 11 | `ExitCode.CANCELLED` | Task was cancelled |

### Usage in Scripts

```bash
#!/bin/bash
python -m backend.headless auto --group-id 1 --member-id 2
exit_code=$?

case $exit_code in
    0)
        echo "✓ Task completed successfully"
        ;;
    1)
        echo "✗ Task failed with error"
        ;;
    10)
        echo "⚠ Task blocked - needs human review"
        ;;
    11)
        echo "⚡ Task was cancelled"
        ;;
esac
```

## Output Formats

### Text Format (default)

```
Task completed successfully.

Here's the analysis:
- Code quality: Good
- Tests: 85% coverage
- Recommendations: Add more unit tests
```

### JSON Format

```bash
python -m backend.headless auto --group-id 1 --member-id 2 --json
```

```json
{
  "exit_code": 0,
  "interrupted": false,
  "status": "complete",
  "text": "Task completed successfully.",
  "data": {
    "metrics": {
      "coverage": 85,
      "issues": 3
    }
  }
}
```

### Stream JSON Format

```bash
python -m backend.headless auto --group-id 1 --member-id 2 --output-format stream-json
```

Outputs one JSON object per line, suitable for streaming processing.

## Session Management

### Listing Sessions

```bash
# List all sessions
python -c "from backend.headless import list_sessions; import json; print(json.dumps([{'id': s.id, 'status': s.status} for s in list_sessions()], indent=2))"

# List sessions for specific group
python -c "from backend.headless import list_sessions; import json; print(json.dumps([{'id': s.id, 'status': s.status} for s in list_sessions(group_id=1)], indent=2))"
```

### Resuming Sessions

```bash
# First, find the session ID
python -c "from backend.headless import list_sessions; sessions = list_sessions(); print('\n'.join([s.id for s in sessions]))"

# Resume the session
python -m backend.headless auto --group-id 1 --member-id 2 --resume session_123
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: AI Code Review
on: [push, pull_request]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Run AI Review
        run: |
          python -m backend.headless discuss "Review these changes" \
            --group-id ${{ secrets.GROUP_ID }} \
            --member-id ${{ secrets.MEMBER_ID }} \
            --json \
            --timeout 600000
      
      - name: Parse Results
        run: |
          result=$(python -m backend.headless discuss "Review these changes" \
            --group-id ${{ secrets.GROUP_ID }} \
            --member-id ${{ secrets.MEMBER_ID }} \
            --json)
          exit_code=$(echo $result | jq -r '.exit_code')
          if [ $exit_code -ne 0 ]; then
            echo "AI review failed"
            exit 1
          fi
```

### GitLab CI Example

```yaml
ai-review:
  stage: test
  script:
    - python -m backend.headless auto --group-id 1 --member-id 2 --json > review.json
    - cat review.json | jq '.text'
  artifacts:
    reports:
      codequality: review.json
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

## Advanced Usage

### Custom Timeout

```bash
# 10 minute timeout
python -m backend.headless auto --group-id 1 --member-id 2 --timeout 600000

# No timeout (set to 0)
python -m backend.headless auto --group-id 1 --member-id 2 --timeout 0
```

### No Auto-Restart

```bash
# Single attempt only
python -m backend.headless auto --group-id 1 --member-id 2 --max-restarts 0
```

### Verbose Output

```bash
python -m backend.headless auto --group-id 1 --member-id 2 --verbose
```

## Session Storage

Sessions are stored in `backend/.headless_sessions/` as JSON files:

```
backend/.headless_sessions/
├── headless_1_2_1718035200.json
├── headless_1_2_1718035300.json
└── headless_1_2_1718035400.json
```

Each file contains:
```json
{
  "id": "headless_1_2_1718035200",
  "group_id": 1,
  "member_id": 2,
  "created_at": 1718035200.123,
  "command": "auto",
  "status": "completed"
}
```

## Troubleshooting

### "Cannot connect to supervisor"

**Problem:** Headless mode cannot connect to the running server.

**Solution:**
1. Ensure the server is running: `python -m backend.main`
2. Check the IPC address: `NUPC_ADDR` environment variable
3. Verify socket permissions

### "Group not found"

**Problem:** The specified group ID doesn't exist.

**Solution:**
```bash
# List valid groups
python -c "from backend.db import global_db; import asyncio; asyncio.run(global_db().execute('SELECT id, name FROM groups'))"
```

### "Member not found"

**Problem:** The member ID doesn't exist in the specified group.

**Solution:**
```bash
# List valid members
python -c "from backend.db import global_db; import asyncio; asyncio.run(global_db().execute('SELECT id, name FROM members WHERE group_id = 1'))"
```

## API Reference

### Core Functions

#### `run_headless_once(args)`
Execute a single headless run.

**Args:**
- `args`: Parsed command line arguments

**Returns:**
- `Result` with exit_code, status, text

#### `should_restart_headless_run(result)`
Determine if the run should be restarted.

**Args:**
- `result`: Execution result

**Returns:**
- `bool`: True if should restart

#### `resolve_resume_session(sessions, prefix)`
Resolve a session prefix to a single session.

**Args:**
- `sessions`: List of SessionInfo
- `prefix`: Session ID or prefix

**Returns:**
- Tuple of (SessionInfo, error_message)

#### `list_sessions(group_id=None)`
List all sessions.

**Args:**
- `group_id`: Optional filter by group

**Returns:**
- List of SessionInfo

#### `save_session(session)`
Save session state.

**Args:**
- `session`: SessionInfo to save

#### `load_session(session_id)`
Load a session.

**Args:**
- `session_id`: Session ID

**Returns:**
- SessionInfo or None

#### `delete_session(session_id)`
Delete a session.

**Args:**
- `session_id`: Session ID to delete

## Migration from Other Tools

### From gsd-2

```bash
# gsd-2
gsd headless auto --timeout 300000

# Nuke AI
python -m backend.headless auto --group-id 1 --member-id 2 --timeout 300000
```

### From Custom Scripts

```python
# Old custom script
result = subprocess.run(['ai-tool', 'review', '--group', '1'])

# New headless mode
result = subprocess.run([
    'python', '-m', 'backend.headless', 'discuss', 'Review code',
    '--group-id', '1', '--member-id', '2', '--json'
], capture_output=True, text=True)

exit_code = result.returncode
# Exit codes are standardized now (0, 1, 10, 11)
```

## Best Practices

1. **Always use `--json` for scripting** - Easy to parse programmatically
2. **Set appropriate timeouts** - Avoid hanging indefinitely
3. **Handle exit codes** - Differentiate between error and blocked
4. **Use session resume** - For long-running tasks
5. **Clean up old sessions** -定期删除旧会话文件

## Known Limitations

1. WebSocket streaming not available in headless mode
2. Real-time updates not supported (polling only)
3. Some interactive features require browser UI

## Contributing

To add features to headless mode:

1. Add command to `parse_args()` in `headless.py`
2. Implement handler in `run_headless_once()`
3. Add tests in `test_headless.py`
4. Update this documentation

## See Also

- [ARCHITECTURE-COMPARISON-REVIEW-V3.md](../../docs/ARCHITECTURE-COMPARISON-REVIEW-V3.md) - Architecture review
- [backend/main.py](../main.py) - Main FastAPI application
- [backend/runtime/supervisor.py](../runtime/supervisor.py) - Supervisor implementation
