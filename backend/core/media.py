"""Signed URLs for per-group private media (user uploads & MCP screenshots).

Files live at ``workspaces/group_<gid>/media/<kind>/<file>`` — OUTSIDE the bot
workspace, so they are never swept into git worktrees, promotions, or the bot's
file context.

They are served only via ``GET /media/<gid>/<kind>/<file>`` guarded by a
short-lived HMAC signature (``?exp=…&sig=…``). This is the same model as S3
pre-signed URLs: the DB stores the **canonical unsigned ref**
(``/media/<gid>/<kind>/<file>``) and we **presign on read** — minting a fresh,
short-TTL signature each time a message is serialized to a client. That way a
permanently-stored message keeps working forever while any leaked URL expires.

The signing key is reused from ``core.auth.SECRET_KEY`` (no new dependency / no
new secret to manage).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time

from core.auth import SECRET_KEY

MEDIA_KINDS = ("uploads", "screenshots")
DEFAULT_TTL = 3600  # 1h — long enough for a chat session, short enough that leaks rot

# Canonical ref: /media/<gid>/<kind>/<filename>
_REF_RE = re.compile(r"^/media/(\d+)/([a-z]+)/([^/?]+)$")
# Filenames we mint are uuid + ext; reject anything that could traverse.
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def is_safe_filename(filename: str) -> bool:
    return bool(filename) and ".." not in filename and bool(_NAME_RE.match(filename))


def canonical_ref(gid: int, kind: str, filename: str) -> str:
    return f"/media/{gid}/{kind}/{filename}"


def _compute_sig(gid: int, kind: str, filename: str, exp: int) -> str:
    msg = f"{gid}/{kind}/{filename}/{exp}".encode()
    raw = hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign(gid: int, kind: str, filename: str, ttl: int = DEFAULT_TTL) -> str:
    """Return a signed, time-limited URL for a media object."""
    exp = int(time.time()) + ttl
    sig = _compute_sig(gid, kind, filename, exp)
    return f"{canonical_ref(gid, kind, filename)}?exp={exp}&sig={sig}"


def presign(file_url: str | None, ttl: int = DEFAULT_TTL) -> str | None:
    """Presign a stored ref on read.

    Only canonical ``/media/...`` refs are signed; anything else (None, legacy
    ``/uploads/...``, external URLs) is returned untouched so this is safe to
    apply blanket-style at every client-serialization point.
    """
    if not file_url:
        return file_url
    m = _REF_RE.match(file_url)
    if not m:
        return file_url
    gid, kind, filename = int(m.group(1)), m.group(2), m.group(3)
    if kind not in MEDIA_KINDS or not is_safe_filename(filename):
        return file_url
    return sign(gid, kind, filename, ttl)


def canonicalize(file_url: str | None) -> str | None:
    """Strip any signature query from a /media ref so the DB stores the stable form.

    Defensive: even if a client echoes back a signed URL, we persist the canonical
    ref (otherwise the stored URL would expire).
    """
    if file_url and file_url.startswith("/media/") and "?" in file_url:
        return file_url.split("?", 1)[0]
    return file_url


def presign_message(msg: dict, ttl: int = DEFAULT_TTL) -> dict:
    """Presign the ``file_url`` of a message dict in place (and return it)."""
    if isinstance(msg, dict) and msg.get("file_url"):
        msg["file_url"] = presign(msg["file_url"], ttl)
    return msg


def reap_screenshots(max_age_days: float = 7, max_per_group: int = 50) -> int:
    """Delete old/excess MCP screenshots + orphaned staging files. Returns count removed.

    Screenshots are machine-generated and regenerable, so they are safe to purge.
    User uploads (media/uploads/) are NEVER touched here — that's the whole point
    of keeping them in a separate directory.
    """
    import time
    from workspace import layout

    removed = 0
    now = time.time()
    cutoff = now - max_age_days * 86400
    root = layout._root()
    try:
        group_dirs = list(root.glob("group_*"))
    except Exception:
        group_dirs = []
    for gdir in group_dirs:
        sdir = gdir / "media" / "screenshots"
        if not sdir.is_dir():
            continue
        try:
            files = sorted(
                (f for f in sdir.iterdir() if f.is_file()),
                key=lambda f: f.stat().st_mtime, reverse=True,
            )
        except Exception:
            continue
        for i, f in enumerate(files):
            try:
                if i >= max_per_group or f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
    # Orphaned staging files (worker crashed before moving them) — purge after 1h.
    staging = layout.media_staging_dir()
    if staging.is_dir():
        for f in staging.iterdir():
            try:
                if f.is_file() and f.stat().st_mtime < now - 3600:
                    f.unlink()
                    removed += 1
            except Exception:
                pass
    return removed


def verify(gid: int, kind: str, filename: str, exp: str | int, sig: str) -> bool:
    """Validate a signed request. False on any tamper / expiry / bad input."""
    if kind not in MEDIA_KINDS or not is_safe_filename(filename):
        return False
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if time.time() > exp_i:
        return False
    expected = _compute_sig(gid, kind, filename, exp_i)
    return hmac.compare_digest(sig or "", expected)
