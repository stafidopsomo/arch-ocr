"""Login, logout, and session management endpoints."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

ADMIN_TOKEN = (os.getenv("OCR_ADMIN_TOKEN") or "").strip()
DEMO_REQUIRE_TOKEN = os.getenv("OCR_DEMO_REQUIRE_TOKEN", "true").lower() != "false"
ADMIN_USERNAME = os.getenv("OCR_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("OCR_ADMIN_PASSWORD") or ADMIN_TOKEN
STARTER_USERNAME = os.getenv("OCR_STARTER_USERNAME", "stavret")
STARTER_PASSWORD = os.getenv("OCR_STARTER_PASSWORD") or ADMIN_TOKEN
SESSION_COOKIE = "arch_ocr_session"


def _session_secret() -> bytes:
    secret = ADMIN_TOKEN or os.getenv("OCR_SESSION_SECRET") or "arch-ocr-demo-session"
    return secret.encode("utf-8")


def _sign_session(payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")).decode("ascii")
    sig = hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def _verify_session(value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    body, sig = value.rsplit(".", 1)
    expected = hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _current_user(request: Request) -> dict[str, Any] | None:
    return _verify_session(request.cookies.get(SESSION_COOKIE))


def _demo_users() -> dict[str, dict[str, str]]:
    users = {
        ADMIN_USERNAME: {"password": ADMIN_PASSWORD, "role": "admin"},
        STARTER_USERNAME: {"password": STARTER_PASSWORD, "role": "user"},
    }
    if ADMIN_TOKEN:
        users.setdefault("admin", {"password": ADMIN_TOKEN, "role": "admin"})
        users.setdefault("stavret", {"password": ADMIN_TOKEN, "role": "user"})
    return users


def _token_from_request(request: Request, form_token: str | None = None) -> str:
    return (
        form_token
        or request.headers.get("x-admin-token")
        or request.query_params.get("token")
        or request.query_params.get("admin_token")
        or ""
    ).strip()


def _require_demo_access(request: Request, form_token: str | None = None) -> None:
    if not DEMO_REQUIRE_TOKEN:
        return
    if _current_user(request):
        return
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="OCR_ADMIN_TOKEN is not configured on the server.",
        )
    if _token_from_request(request, form_token) != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing demo token.")


def _require_admin_access(request: Request) -> None:
    _require_demo_access(request)
    user = _current_user(request)
    if user and user.get("role") == "admin":
        return
    if ADMIN_TOKEN and _token_from_request(request) == ADMIN_TOKEN:
        return
    raise HTTPException(status_code=403, detail="Admin access required.")
