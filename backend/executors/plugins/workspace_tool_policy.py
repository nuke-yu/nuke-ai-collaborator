"""Path safety policies shared by workspace tool handlers."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path

SENSITIVE_PATH_PREFIXES = [
    "~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gcloud", "~/.kube",
    "~/.docker", "~/.config/gh", "~/.config/git", "~/.password-store",
]
SENSITIVE_FILENAME_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_rsa.*",
    "id_ed25519", "id_ed25519.*", "credentials", ".netrc", "*.pfx",
    "*.p12", ".git-credentials", ".npmrc", ".pypirc", ".dockercfg",
    "*.keystore", "*.jks", ".htpasswd", "cookies.sqlite",
]
SENSITIVE_FILENAME_ALLOWLIST = {".env.example", ".env.sample", ".env.template"}


def is_sensitive_path(path: str) -> bool:
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        resolved = Path(path).expanduser()
    if resolved.name.lower() in SENSITIVE_FILENAME_ALLOWLIST:
        return False
    value = str(resolved)
    for prefix in SENSITIVE_PATH_PREFIXES:
        expanded = str(Path(prefix).expanduser())
        if value == expanded or value.startswith(expanded + os.sep):
            return True
    if any(part in resolved.parts for part in {".ssh", ".aws", ".docker", ".gnupg", ".kube", ".password-store"}):
        return True
    return any(fnmatch.fnmatch(resolved.name.lower(), pattern) for pattern in SENSITIVE_FILENAME_PATTERNS)
