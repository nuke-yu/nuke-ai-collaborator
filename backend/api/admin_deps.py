"""Shared dependencies/helpers for control-plane routes."""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request

from core import auth

log = logging.getLogger("control_plane")


async def require_operator(user=Depends(auth.get_current_user)):
    """Restrict control-plane routes to operator-level callers."""
    if not auth.is_operator(user):
        raise HTTPException(403, "Operator privileges required")
    return user


def audit_control_plane(action: str, user: dict, request: Request | None = None, **details) -> None:
    """Structured-ish audit log for high-privilege operations."""
    payload = {
        "action": action,
        "user_id": user.get("uid"),
        "username": user.get("sub"),
    }
    if request is not None:
        payload["path"] = request.url.path
        payload["method"] = request.method
    for key, value in details.items():
        if value is not None:
            payload[key] = value
    log.info("control-plane action: %s", payload)
