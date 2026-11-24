"""Validation of operator-provided test commands."""
import re
import shlex

TEST_RUNNERS = {
    "pytest", "python", "python3", "npm", "npx", "pnpm", "yarn", "bun",
    "cargo", "go", "dotnet", "mvn", "mvnw", "gradle", "gradlew", "make",
    "bundle", "rspec", "php", "composer",
}
_SAFE_TEST_ARG = re.compile(r"^[A-Za-z0-9_./:@%+=,\-]+$")
_SHELL_CONTROL_CHARS = frozenset("|;&`$<>\n\r\0")


def validate_test_command(v: str) -> str:
    command = v.strip()
    if not command:
        return ""
    if len(command) > 1000:
        raise ValueError("test_command must be at most 1000 characters")
    if any(char in _SHELL_CONTROL_CHARS for char in command):
        raise ValueError("test_command must not contain shell control characters")

    try:
        argv = shlex.split(command, posix=True)
    except ValueError as exc:
        raise ValueError(f"test_command is not valid argv syntax: {exc}") from exc
    if not argv or len(argv) > 64:
        raise ValueError("test_command must contain 1 to 64 arguments")

    executable = argv[0].rsplit("/", 1)[-1]
    if executable not in TEST_RUNNERS:
        raise ValueError(f"test_command runner is not allowed: {executable}")
    if executable in {"python", "python3"} and any(
        arg in {"-c", "--command"} for arg in argv[1:]
    ):
        raise ValueError("inline Python commands are not allowed")
    if any(not _SAFE_TEST_ARG.fullmatch(arg) for arg in argv):
        raise ValueError("test_command arguments contain unsupported characters")
    return command


def observed_test_command(records: list[dict]) -> str:
    """Find a foreground test command the coding runner successfully executed."""
    for record in reversed(records):
        args = record.get("args") or {}
        if (record.get("name") != "run_shell" or record.get("is_error")
                or args.get("background") or args.get("cwd") not in (None, "", "workspace")
                or str(record.get("result", "")).split("\n", 1)[0] != "exit_code: 0"):
            continue
        try:
            command = validate_test_command(args.get("cmd") or "")
        except ValueError:
            continue
        argv = shlex.split(command)
        if not argv:
            continue
        executable = argv[0].rsplit("/", 1)[-1]
        if executable in {"pytest", "jest", "vitest", "rspec"} or any(
                arg in {"test", "pytest", "unittest", "check", "verify"} or arg.startswith("test:")
                for arg in argv[1:]):
            return command
    return ""
