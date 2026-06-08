"""Secret redaction for tool output.

Tool results (run_shell stdout/stderr, file reads, MCP results) flow into the
shared model context AND get re-broadcast to other bots. A printed credential
therefore leaks far wider than in a single-agent CLI — multi-agent context is a
leak amplifier. This module masks common high-confidence secret formats before
results leave the executor.

Applied at two choke points (kept here, neutral, so both can import it):
  - tool_executor after-hook  → builtin / run_shell / run_skill
    (see workspace_tools._default_secret_redactor)
  - McpClientToolProvider.execute → MCP results route through the router, not
    tool_executor's after-hooks.

Design bias: high precision over recall. Patterns target *recognizable* secret
formats (prefixed tokens, PEM blocks, JWTs, credentialed URLs, secret-named
assignments) to keep false positives low. Generic high-entropy detection is
deliberately omitted — it would mask hashes, IDs, and base64 data.
"""
import re

_PLACEHOLDER = "[REDACTED]"

# Each entry: (label, compiled pattern, group_to_mask).
# group 0 → replace the whole match; group N → mask only that capture group and
# keep the rest (so "API_KEY=" prefixes / URL userinfo stay readable).
_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    # PEM private key blocks
    ("private-key", re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL), 0),
    # JWT (header.payload.signature)
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), 0),
    # AWS access key id
    ("aws-akid", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    # GitHub tokens
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"), 0),
    ("github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), 0),
    # Anthropic (before OpenAI: sk-ant-… is a superset prefix)
    ("anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), 0),
    ("openai", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), 0),
    # Slack
    ("slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 0),
    # Google API key
    ("google-api", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 0),
    # URL with inline credentials → mask only the password
    ("url-cred", re.compile(r"(://[^/\s:@]+:)([^/\s:@]+)(@)"), 2),
    # Authorization: Bearer <token>  /  bare bearer token
    ("bearer", re.compile(
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|bearer\s+)([A-Za-z0-9._\-]{20,})"), 2),
    # KEY="value" where the key name looks secret. QUOTED literal only, and the
    # value must contain no code punctuation ()[]{}<> — so this never mangles code
    # like `API_KEY = os.environ["X"]` or `PUBLIC_KEY = load_pem(...)` (the false
    # positive that derailed the dev bot). PWD/PASSWD excluded (PWD = cwd var).
    # Unquoted bare secrets in recognizable formats (ghp_/sk-/AKIA/JWT/…) are
    # still caught by the format-specific patterns above, quoted or not.
    ("secret-assignment", re.compile(
        r"(?im)("
        r"[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE)[A-Z0-9_]*"
        r"\s*[:=]\s*)(['\"])([^\s'\"(){}\[\]<>]{6,})(['\"])"), 3),
]


def redact_secrets(text: str) -> tuple[str, int]:
    """Mask high-confidence secret formats. Returns (redacted_text, num_redactions)."""
    if not text or len(text) < 8:
        return text, 0
    total = 0
    for _label, pat, grp in _PATTERNS:
        def _sub(m, _grp=grp):
            nonlocal total
            total += 1
            if _grp == 0:
                return _PLACEHOLDER
            parts = list(m.groups())
            parts[_grp - 1] = _PLACEHOLDER
            return "".join(p for p in parts if p is not None)
        text = pat.sub(_sub, text)
    return text, total
