"""HTML page endpoints and misc routes (session, login, logout, health, admin)."""

from __future__ import annotations

import hmac
import html
import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from api.auth import (
    ADMIN_TOKEN,
    DEMO_REQUIRE_TOKEN,
    SESSION_COOKIE,
    _current_user,
    _demo_users,
    _require_demo_access,
    _sign_session,
)
from api.events import _job_events
from api.storage import (
    JOBS_DIR,
    MAX_FILES_PER_PACKET,
    MAX_PAGES_PER_PACKET,
    MAX_UPLOAD_MB,
    _load_job,
    _now,
    _read_json,
)
from api.usage import _usage_summary


def _job_status_page(job: dict[str, Any], token: str = "") -> str:
    job_id = html.escape(str(job.get("job_id", "")))
    status = html.escape(str(job.get("status", "")))
    message = html.escape(str(job.get("message", "")))
    pages_processed = html.escape(str(job.get("pages_processed", 0)))
    pages_selected = html.escape(str(job.get("pages_selected", "?")))
    token_query = f"?token={html.escape(token)}" if token else ""
    terminal = str(job.get("status", "")) in {"completed", "completed_with_errors", "failed"}
    refresh = "" if terminal else '<meta http-equiv="refresh" content="5">'
    result_links = ""
    log_link = f'<p><a href="/jobs/{job_id}/logs{token_query}">View structured job logs</a></p>'
    if str(job.get("status", "")) in {"completed", "completed_with_errors"}:
        result_links = f"""
          <p>
            <a href="/jobs/{job_id}/review{token_query}">Open review</a>
            |
            <a href="/jobs/{job_id}/report{token_query}">Markdown report</a>
            |
            <a href="/jobs/{job_id}/packet{token_query}">Open packet JSON</a>
            |
            <a href="/admin{token_query}">Admin</a>
          </p>
        """
    elif str(job.get("status", "")) == "failed":
        result_links = f'<p><a href="/admin{token_query}">Back to admin</a></p>'

    return f"""
    <html>
      <head>
        <title>arch-ocr job {job_id}</title>
        {refresh}
      </head>
      <body>
        <h1>OCR job</h1>
        <p><strong>Status:</strong> {status}</p>
        <p><strong>Message:</strong> {message}</p>
        <p><strong>Progress:</strong> {pages_processed} / {pages_selected} pages</p>
        <p><small>Job ID: {job_id}</small></p>
        {log_link}
        {result_links}
      </body>
    </html>
    """


def _job_logs_page(job: dict[str, Any], events: list[dict[str, Any]], token: str = "") -> str:
    job_id = html.escape(str(job.get("job_id", "")))
    token_query = f"?token={html.escape(token)}" if token else ""
    rows = []
    for event in reversed(events):
        event_type = html.escape(str(event.get("event_type", "")))
        timestamp = html.escape(str(event.get("timestamp", "")))
        details = {
            key: value
            for key, value in event.items()
            if key not in {"timestamp", "job_id", "event_type"}
        }
        rows.append(
            f"""
            <tr>
              <td class="mono">{timestamp}</td>
              <td><strong>{event_type}</strong></td>
              <td><pre>{html.escape(json.dumps(details, ensure_ascii=False, indent=2))}</pre></td>
            </tr>
            """
        )
    body = "".join(rows) or "<tr><td colspan='3'>No structured job events recorded yet.</td></tr>"
    return f"""
    <html>
      <head>
        <title>arch-ocr job logs {job_id}</title>
        <meta http-equiv="refresh" content="10">
        <style>
          body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin: 24px; background:#fafaf7; color:#14181f; }}
          a {{ color:#1a2540; }}
          table {{ border-collapse: collapse; width: 100%; background:white; }}
          th,td {{ border:1px solid #e7e3d8; padding:8px; vertical-align:top; text-align:left; }}
          th {{ background:#f6f4ee; }}
          pre {{ margin:0; white-space:pre-wrap; font-size:12px; }}
          .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
        </style>
      </head>
      <body>
        <h1>Structured job logs</h1>
        <p><a href="/jobs/{job_id}/view{token_query}">Back to job</a> · <a href="/jobs/{job_id}/events{token_query}">JSON events</a></p>
        <p><strong>Status:</strong> {html.escape(str(job.get("status", "")))} · <strong>Progress:</strong> {html.escape(str(job.get("pages_processed", 0)))} / {html.escape(str(job.get("pages_selected", "?")))} pages</p>
        <table>
          <thead><tr><th>Time</th><th>Event</th><th>Details</th></tr></thead>
          <tbody>{body}</tbody>
        </table>
      </body>
    </html>
    """


def view_job(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    _require_demo_access(request)
    return _job_status_page(_load_job(job_id), token)


def view_job_logs(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    _require_demo_access(request)
    return _job_logs_page(_load_job(job_id), _job_events(job_id), token)


def health() -> dict[str, Any]:
    from api.storage import STORAGE_DIR
    return {"ok": True, "storage_dir": str(STORAGE_DIR)}


def index(request: Request) -> RedirectResponse:
    return RedirectResponse("/design/arch-ocr.html", status_code=307)


def session(request: Request) -> dict[str, Any]:
    from worker.processor import PROVIDER, _get_model, _get_model_sequence
    model = _get_model()
    model_sequence = _get_model_sequence()
    user = _current_user(request)
    return {
        "authenticated": bool(user) or not DEMO_REQUIRE_TOKEN,
        "user": user,
        "limits": {
            "max_files_per_packet": MAX_FILES_PER_PACKET,
            "max_pages_per_packet": MAX_PAGES_PER_PACKET,
            "max_upload_mb": MAX_UPLOAD_MB,
            "provider": PROVIDER,
            "model": model,
            "model_fallbacks": model_sequence[1:],
        },
    }


async def login(request: Request) -> JSONResponse:
    payload = await request.json()
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    users = _demo_users()
    record = users.get(username)
    if not record or not record.get("password") or not hmac.compare_digest(password, record["password"]):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    session_payload = {"username": username, "role": record["role"], "created_at": _now()}
    response = JSONResponse({"authenticated": True, "user": session_payload})
    response.set_cookie(
        SESSION_COOKIE,
        _sign_session(session_payload),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response


def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


def admin(request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    if DEMO_REQUIRE_TOKEN and not _current_user(request) and (not ADMIN_TOKEN or token != ADMIN_TOKEN):
        return """
        <html><body>
          <h1>Admin</h1>
          <form>
            <label>Demo token <input name="token" type="password" /></label>
            <button type="submit">Open</button>
          </form>
        </body></html>
        """

    jobs = []
    for path in sorted(JOBS_DIR.glob("*/job.json"), reverse=True):
        try:
            jobs.append(_read_json(path))
        except Exception:
            continue
    usage_summary = _usage_summary()
    rows = []
    for job in jobs[:50]:
        job_id = html.escape(str(job.get("job_id", "")))
        status = html.escape(str(job.get("status", "")))
        message = html.escape(str(job.get("message", "")))
        rows.append(
            f"<tr><td>{job_id}</td><td>{status}</td><td>{message}</td>"
            f"<td><a href='/jobs/{job_id}?token={html.escape(token)}'>json</a></td>"
            f"<td><a href='/jobs/{job_id}/review?token={html.escape(token)}'>review</a></td>"
            f"<td><a href='/jobs/{job_id}/report?token={html.escape(token)}'>report</a></td>"
            f"<td><a href='/jobs/{job_id}/logs?token={html.escape(token)}'>logs</a></td></tr>"
        )
    return f"""
    <html>
      <body>
        <h1>Admin</h1>
        <p>Usage events: {usage_summary['event_count']}</p>
        <p>Estimated cost: ${usage_summary['estimated_cost_usd']:.6f}</p>
        <table border="1" cellpadding="6">
          <tr><th>Job</th><th>Status</th><th>Message</th><th>JSON</th><th>Review</th><th>Report</th><th>Logs</th></tr>
          {''.join(rows)}
        </table>
      </body>
    </html>
    """
