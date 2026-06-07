"""Synthesize a scoped args_pattern when a user picks 'always allow'.

A blanket rule (empty args_pattern) means "allow every call to this tool" — for
run_shell that is "allow any shell command", which defeats the gate entirely
(approving `git status` once would also auto-allow `rm -rf /`). Instead we
synthesize a pattern scoped to the *kind* of call the user approved, mirroring
gsd-2's subcommand-depth bash patterns.

The result is an fnmatch glob matched (in engine._matches) against each argument
value, e.g. for run_shell against the `cmd` string.
"""
import re
import shlex

# Commands whose first sub-token is a meaningful subcommand worth keeping in the
# scope, so `git push origin main` → `git push *` (not the over-broad `git *`).
_SUBCOMMAND_DEPTH = {
    "git": 2, "gh": 2, "docker": 2, "podman": 2, "kubectl": 2, "helm": 2,
    "npm": 2, "pnpm": 2, "yarn": 2, "cargo": 2, "go": 2, "pip": 2, "pip3": 2,
    "aws": 2, "az": 2, "gcloud": 2, "systemctl": 2, "brew": 2,
    "apt": 2, "apt-get": 2, "make": 2, "poetry": 2, "uv": 2, "terraform": 2,
}
_DEFAULT_DEPTH = 1

# Leading tokens that wrap the real command — stripped before reading the base.
_WRAPPERS = {"sudo", "doas", "env", "command", "nohup", "time", "nice",
             "ionice", "stdbuf", "setsid", "xargs", "timeout"}

_GLOB_META = set("*?[]")


def _escape_glob(s: str) -> str:
    """fnmatch has no escape syntax; wrap glob metachars in [..] classes so a
    literal token can't act as a wildcard."""
    return "".join(f"[{c}]" if c in _GLOB_META else c for c in s)


def _bash_pattern(cmd: str) -> str | None:
    """Build a subcommand-scoped glob from a shell command, or None if empty."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()
    i = 0
    changed = True
    while changed and i < len(tokens):
        changed = False
        while i < len(tokens) and re.match(r"^[A-Za-z_]\w*=", tokens[i]):
            i += 1
            changed = True
        while i < len(tokens) and tokens[i].split("/")[-1] in _WRAPPERS:
            i += 1
            changed = True
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
    real = tokens[i:]
    if not real:
        return None
    base = real[0].split("/")[-1]
    depth = _SUBCOMMAND_DEPTH.get(base, _DEFAULT_DEPTH)
    prefix = real[:depth]
    escaped = _escape_glob(" ".join(prefix))
    # No args beyond the identity prefix → match it exactly; otherwise allow that
    # (sub)command with any trailing args. The trailing " *" keeps a token
    # boundary so `ls` can't widen into `lsof`.
    if len(real) <= len(prefix):
        return escaped
    return escaped + " *"


def synthesize_args_pattern(tool_name: str, arguments: dict) -> str:
    """Return a scoped fnmatch args_pattern for an 'always' rule.

    Empty string means blanket (allow any args) — kept only as a fallback for
    tools we don't have a scoping strategy for.
    """
    if tool_name == "run_shell":
        return _bash_pattern((arguments.get("cmd") or "").strip()) or ""
    if tool_name in ("write_file", "read_local_file", "write_local_file"):
        path = arguments.get("path") or arguments.get("file_path") or ""
        return _escape_glob(str(path)) if path else ""
    if tool_name == "spawn_agent":
        name = arguments.get("bot_name") or ""
        return _escape_glob(str(name)) if name else ""
    return ""
