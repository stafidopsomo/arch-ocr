from __future__ import annotations

import html
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import ocr_script

load_dotenv()

APP_NAME = "arch-ocr"
STORAGE_DIR = Path(os.getenv("OCR_STORAGE_DIR", "storage")).expanduser().resolve()
JOBS_DIR = STORAGE_DIR / "jobs"
USAGE_LEDGER_PATH = STORAGE_DIR / "usage_ledger.jsonl"
ALLOWED_EXTENSIONS = {".pdf", *ocr_script.SUPPORTED_IMAGE_EXTENSIONS}

MAX_FILES_PER_PACKET = int(os.getenv("OCR_MAX_FILES_PER_PACKET", "10"))
MAX_PAGES_PER_PACKET = int(os.getenv("OCR_MAX_PAGES_PER_PACKET", "20"))
MAX_UPLOAD_MB = int(os.getenv("OCR_MAX_UPLOAD_MB", "100"))
MIN_SECONDS_BETWEEN_CALLS = float(os.getenv("OCR_PROVIDER_MIN_SECONDS_BETWEEN_CALLS", "4"))
MAX_REQUESTS_PER_MINUTE = int(os.getenv("OCR_PROVIDER_MAX_REQUESTS_PER_MINUTE", "10"))
MAX_REQUESTS_PER_DAY = int(os.getenv("OCR_PROVIDER_MAX_REQUESTS_PER_DAY", "100"))
MAX_PROVIDER_RETRIES = int(os.getenv("OCR_PROVIDER_MAX_RETRIES", "2"))
PROVIDER_RETRY_BASE_SECONDS = float(os.getenv("OCR_PROVIDER_RETRY_BASE_SECONDS", "20"))
PROVIDER_RETRY_MAX_SECONDS = float(os.getenv("OCR_PROVIDER_RETRY_MAX_SECONDS", "120"))
ADMIN_TOKEN = (os.getenv("OCR_ADMIN_TOKEN") or "").strip()
DEMO_REQUIRE_TOKEN = os.getenv("OCR_DEMO_REQUIRE_TOKEN", "true").lower() != "false"
ADMIN_USERNAME = os.getenv("OCR_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("OCR_ADMIN_PASSWORD") or ADMIN_TOKEN
STARTER_USERNAME = os.getenv("OCR_STARTER_USERNAME", "stavret")
STARTER_PASSWORD = os.getenv("OCR_STARTER_PASSWORD") or ADMIN_TOKEN
SESSION_COOKIE = "arch_ocr_session"

PROVIDER = os.getenv("CLOUD_PROVIDER", ocr_script.DEFAULT_PROVIDER)
MODEL = os.getenv("GEMINI_MODEL") or ocr_script._default_model_for_provider(PROVIDER)
PREVIEW_DPI = int(os.getenv("OCR_PREVIEW_DPI", "36"))
RENDER_DPI = int(os.getenv("OCR_RENDER_DPI", "150"))
REQUEST_TIMEOUT = int(os.getenv("OCR_PROVIDER_TIMEOUT", "180"))
LANGUAGE_HINTS = os.getenv("GOOGLE_VISION_LANGUAGE_HINTS", "el,en")

app = FastAPI(title=APP_NAME)
worker_lock = threading.Lock()
ledger_lock = threading.Lock()
event_lock = threading.Lock()

_web_dir = Path(__file__).parent / "web"
if _web_dir.is_dir():
    app.mount("/design", StaticFiles(directory=str(_web_dir), html=True), name="design")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JobAborted(RuntimeError):
    pass


def _ensure_storage() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_LEDGER_PATH.touch(exist_ok=True)


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _job_path(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


def _job_events_path(job_id: str) -> Path:
    return _job_dir(job_id) / "events.jsonl"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(name).name).strip()
    return cleaned or "upload"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Job not found.")
    return _read_json(path)


def _packet_path(job_id: str) -> Path:
    return _job_dir(job_id) / "output" / "packet.json"


def _load_analyzed_packet(job_id: str) -> dict[str, Any]:
    packet_path = _packet_path(job_id)
    if not packet_path.exists():
        raise HTTPException(status_code=404, detail="Packet result not ready.")
    packet = _read_json(packet_path)
    ocr_script._attach_packet_analysis(packet)
    return packet


def _save_job(job: dict[str, Any]) -> None:
    _write_json(_job_path(str(job["job_id"])), job)


def _set_job_status(job_id: str, status: str, **updates: Any) -> None:
    job = _load_job(job_id)
    job.update(updates)
    job["status"] = status
    job["updated_at"] = _now()
    _save_job(job)


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
    return {
        ADMIN_USERNAME: {"password": ADMIN_PASSWORD, "role": "admin"},
        STARTER_USERNAME: {"password": STARTER_PASSWORD, "role": "user"},
    }


def _append_job_event(job_id: str, event_type: str, **details: Any) -> None:
    event = {
        "timestamp": _now(),
        "job_id": job_id,
        "event_type": event_type,
        **details,
    }
    path = _job_events_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with event_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _job_events(job_id: str, limit: int = 200) -> list[dict[str, Any]]:
    path = _job_events_path(job_id)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with event_lock:
        lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _is_abort_requested(job_id: str) -> bool:
    try:
        return str(_load_job(job_id).get("status")) == "abort_requested"
    except HTTPException:
        return False


def _raise_if_abort_requested(job_id: str, page_id: str | None = None) -> None:
    if not _is_abort_requested(job_id):
        return
    _append_job_event(job_id, "job_abort_observed", page_id=page_id)
    raise JobAborted("Job aborted by user.")


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


def _usage_events() -> list[dict[str, Any]]:
    if not USAGE_LEDGER_PATH.exists():
        return []
    events: list[dict[str, Any]] = []
    with ledger_lock:
        for line in USAGE_LEDGER_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def _append_usage_event(event: dict[str, Any]) -> None:
    with ledger_lock:
        with USAGE_LEDGER_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _seconds_since(timestamp: str) -> float | None:
    try:
        event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - event_time).total_seconds()


def _throttle_before_provider_call(job_id: str, page_id: str) -> None:
    while True:
        _raise_if_abort_requested(job_id, page_id)
        events = _usage_events()
        recent_success_or_attempt = [
            event
            for event in events
            if _seconds_since(str(event.get("timestamp", ""))) is not None
        ]
        last_event_seconds = None
        if recent_success_or_attempt:
            last_event_seconds = min(
                seconds
                for seconds in (
                    _seconds_since(str(event.get("timestamp", "")))
                    for event in recent_success_or_attempt
                )
                if seconds is not None
            )
        last_wait = (
            max(MIN_SECONDS_BETWEEN_CALLS - last_event_seconds, 0)
            if last_event_seconds is not None
            else 0
        )
        minute_count = sum(
            1
            for event in recent_success_or_attempt
            if (_seconds_since(str(event.get("timestamp", ""))) or 999999) < 60
        )
        day_count = sum(
            1
            for event in recent_success_or_attempt
            if (_seconds_since(str(event.get("timestamp", ""))) or 999999) < 86400
        )

        if day_count >= MAX_REQUESTS_PER_DAY:
            _append_job_event(
                job_id,
                "rate_limited",
                page_id=page_id,
                reason="daily_provider_request_limit",
                day_count=day_count,
                max_requests_per_day=MAX_REQUESTS_PER_DAY,
            )
            _set_job_status(
                job_id,
                "rate_limited",
                message="Daily provider request limit reached. Try again later.",
                current_page_id=page_id,
            )
            time.sleep(60)
            continue

        if minute_count >= MAX_REQUESTS_PER_MINUTE:
            _append_job_event(
                job_id,
                "rate_limited",
                page_id=page_id,
                reason="provider_request_rate_limit",
                minute_count=minute_count,
                max_requests_per_minute=MAX_REQUESTS_PER_MINUTE,
            )
            _set_job_status(
                job_id,
                "rate_limited",
                message="Provider request rate limit reached. Waiting before continuing.",
                current_page_id=page_id,
            )
            time.sleep(10)
            continue

        if last_wait > 0:
            _append_job_event(
                job_id,
                "throttle_wait",
                page_id=page_id,
                wait_seconds=round(last_wait, 2),
                min_seconds_between_calls=MIN_SECONDS_BETWEEN_CALLS,
            )
            _set_job_status(
                job_id,
                "throttled",
                message=f"Waiting {round(last_wait, 1)}s before next provider call.",
                current_page_id=page_id,
            )
            time.sleep(last_wait)
            _raise_if_abort_requested(job_id, page_id)
        return


def _record_provider_event(
    *,
    job_id: str,
    page_id: str,
    source_file: str,
    page_number: int,
    status: str,
    usage: dict[str, Any] | None = None,
    error: str | None = None,
    attempt: int | None = None,
    max_attempts: int | None = None,
) -> None:
    usage = usage or {}
    safe_error = ocr_script._redact_secrets(error) if error else None
    _append_usage_event(
        {
            "timestamp": _now(),
            "job_id": job_id,
            "page_id": page_id,
            "source_file": source_file,
            "page_number": page_number,
            "provider": PROVIDER,
            "model": MODEL,
            "status": status,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "estimated_cost_usd": usage.get("estimated_cost_usd"),
            "reported_cost_usd": usage.get("reported_cost_usd"),
            "error": safe_error,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
    )
    _append_job_event(
        job_id,
        f"provider_{status}",
        page_id=page_id,
        source_file=_basename(source_file),
        page_number=page_number,
        provider=PROVIDER,
        model=MODEL,
        attempt=attempt,
        max_attempts=max_attempts,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        estimated_cost_usd=usage.get("estimated_cost_usd"),
        error=safe_error,
    )


def _is_transient_provider_error(error_message: str) -> bool:
    normalized = error_message.lower()
    transient_markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "deadline",
        "high demand",
        "overload",
        "overloaded",
        "quota",
        "rate limit",
        "resource exhausted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "try again",
        "unavailable",
    )
    return any(marker in normalized for marker in transient_markers)


def _retry_delay_seconds(attempt: int) -> float:
    return min(PROVIDER_RETRY_BASE_SECONDS * (2 ** max(attempt - 1, 0)), PROVIDER_RETRY_MAX_SECONDS)


def _job_view_url(job_id: str, token: str = "") -> str:
    suffix = f"?token={token}" if token else ""
    return f"/jobs/{job_id}/view{suffix}"


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


def _usage_summary() -> dict[str, Any]:
    events = _usage_events()
    total_estimated = sum(
        float(event.get("estimated_cost_usd") or 0.0)
        for event in events
        if isinstance(event, dict)
    )
    successes = [event for event in events if event.get("status") == "success"]
    retries = [event for event in events if event.get("status") == "retry_wait"]
    failures = [event for event in events if event.get("status") == "failure"]
    total_tokens = sum(int(event.get("total_tokens") or 0) for event in events)
    return {
        "event_count": len(events),
        "success_count": len(successes),
        "retry_count": len(retries),
        "failure_count": len(failures),
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(total_estimated, 8),
        "last_events": events[-50:],
        "limits": {
            "max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
            "max_requests_per_day": MAX_REQUESTS_PER_DAY,
            "min_seconds_between_calls": MIN_SECONDS_BETWEEN_CALLS,
            "max_provider_retries": MAX_PROVIDER_RETRIES,
        },
    }


def _usage_page(summary: dict[str, Any]) -> str:
    rows = []
    for event in reversed(summary.get("last_events", [])):
        if not isinstance(event, dict):
            continue
        error = str(event.get("error") or "")
        if len(error) > 220:
            error = error[:220] + "..."
        rows.append(
            f"""
            <tr>
              <td class="mono">{html.escape(str(event.get("timestamp", "")))}</td>
              <td>{html.escape(str(event.get("job_id", "")))}</td>
              <td>{html.escape(str(event.get("page_id", "")))}</td>
              <td>{html.escape(str(event.get("status", "")))}</td>
              <td class="mono">{html.escape(str(event.get("attempt") or ""))}/{html.escape(str(event.get("max_attempts") or ""))}</td>
              <td class="mono">{html.escape(str(event.get("total_tokens") or ""))}</td>
              <td class="mono">${float(event.get("estimated_cost_usd") or 0.0):.6f}</td>
              <td>{html.escape(error)}</td>
            </tr>
            """
        )
    return f"""
    <html>
      <head>
        <title>arch-ocr usage</title>
        <style>
          body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin:24px; background:#fafaf7; color:#14181f; }}
          .grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; margin:18px 0; }}
          .card {{ background:white; border:1px solid #e7e3d8; border-radius:8px; padding:14px; }}
          .muted {{ color:#68707c; font-size:12px; }}
          .value {{ font-size:24px; font-weight:700; }}
          table {{ border-collapse:collapse; width:100%; background:white; }}
          th,td {{ border:1px solid #e7e3d8; padding:7px; vertical-align:top; text-align:left; font-size:13px; }}
          th {{ background:#f6f4ee; }}
          .mono {{ font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
        </style>
      </head>
      <body>
        <h1>Usage</h1>
        <p><a href="/design/arch-ocr.html?screen=dashboard">Dashboard</a> · <a href="/admin">Admin</a></p>
        <section class="grid">
          <div class="card"><div class="muted">Events</div><div class="value">{summary['event_count']}</div></div>
          <div class="card"><div class="muted">Success</div><div class="value">{summary['success_count']}</div></div>
          <div class="card"><div class="muted">Retries</div><div class="value">{summary['retry_count']}</div></div>
          <div class="card"><div class="muted">Failures</div><div class="value">{summary['failure_count']}</div></div>
          <div class="card"><div class="muted">Estimated cost</div><div class="value">${summary['estimated_cost_usd']:.6f}</div></div>
        </section>
        <section class="card">
          <h2>Limits</h2>
          <p>RPM: {summary['limits']['max_requests_per_minute']} · RPD: {summary['limits']['max_requests_per_day']} · Min seconds between calls: {summary['limits']['min_seconds_between_calls']} · Retries: {summary['limits']['max_provider_retries']}</p>
        </section>
        <h2>Recent provider events</h2>
        <table>
          <thead><tr><th>Time</th><th>Job</th><th>Page</th><th>Status</th><th>Attempt</th><th>Tokens</th><th>Cost</th><th>Error</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </body>
    </html>
    """


def _basename(value: Any) -> str:
    return Path(str(value or "")).name


def _translate_status(status: Any, lang: str = "en") -> str:
    value = str(status or "unknown")
    if lang != "el":
        return value
    return {
        "pass": "επιτυχία",
        "warning": "προειδοποίηση",
        "fail": "αποτυχία",
        "unknown": "άγνωστο",
        "possible_match": "πιθανή ταύτιση",
    }.get(value, value)


def _translate_check_title(title: Any, lang: str = "en") -> str:
    value = str(title or "")
    if lang != "el":
        return value
    return {
        "Extraction completeness": "Πληρότητα εξαγωγής",
        "Field confidence": "Εμπιστοσύνη πεδίων",
        "Handwritten content": "Χειρόγραφο περιεχόμενο",
        "Address consistency": "Συνέπεια διευθύνσεων",
        "Person name consistency": "Συνέπεια ονομάτων",
        "Identifier classification": "Ταξινόμηση αναγνωριστικών",
        "KAEK consistency": "Συνέπεια ΚΑΕΚ",
        "AFM evidence": "Ένδειξη ΑΦΜ",
        "ATAK evidence": "Ένδειξη ΑΤΑΚ",
        "Registry identifier evidence": "Ένδειξη αριθμού μητρώου",
        "Unknown identifier review": "Έλεγχος άγνωστων αναγνωριστικών",
        "Date consistency": "Συνέπεια ημερομηνιών",
        "Permit number evidence": "Ένδειξη αριθμού άδειας",
        "Owner evidence": "Ένδειξη ιδιοκτήτη",
        "Engineer evidence": "Ένδειξη μηχανικού",
        "Signature evidence": "Ένδειξη υπογραφής",
        "Stamp evidence": "Ένδειξη σφραγίδας",
    }.get(value, value)


def _review_labels(lang: str) -> dict[str, str]:
    if lang == "el":
        return {
            "title_prefix": "προεπισκόπηση φακέλου",
            "markdown": "Markdown αναφορά",
            "json": "Packet JSON",
            "back": "Πίσω στο job",
            "admin": "Διαχείριση",
            "pages": "Σελίδες",
            "fields": "Πεδία",
            "checks": "Έλεγχοι",
            "cost": "Εκτ. κόστος",
            "coverage": "Κάλυψη σελίδων",
            "extracted_selected": "εξήχθησαν / επιλέχθηκαν",
            "triaged_total": "σελίδες εντοπίστηκαν συνολικά",
            "skipped_cap": "σελίδες δεν αναλύθηκαν λόγω ορίου demo",
            "no_skipped": "Δεν παραλείφθηκαν σελίδες από το όριο demo.",
            "executive": "Σύνοψη",
            "validation": "Έλεγχοι εγκυρότητας",
            "priorities": "Προτεραιότητες ελέγχου",
            "source_files": "Αρχεία πηγής",
            "fuzzy": "Πιθανές ταυτίσεις",
            "clusters": "Ομάδες τιμών",
            "errors": "Σφάλματα",
            "pages_word": "σελίδες",
            "none": "Δεν καταγράφηκε κάτι.",
            "no_checks": "Δεν καταγράφηκαν έλεγχοι.",
            "no_clusters": "Δεν καταγράφηκαν ομάδες τιμών.",
            "no_fuzzy": "Δεν καταγράφηκαν πιθανές ταυτίσεις.",
            "no_errors": "Δεν καταγράφηκαν σφάλματα εξαγωγής σελίδων.",
            "language_note": "Η δομή της αναφοράς εμφανίζεται στα ελληνικά. Κάποια αυτόματα summaries/evidence παραμένουν όπως παράχθηκαν από τον extractor για να μη χαθεί ακρίβεια.",
        }
    return {
        "title_prefix": "packet review",
        "markdown": "Markdown report",
        "json": "Packet JSON",
        "back": "Back to job",
        "admin": "Admin",
        "pages": "Pages",
        "fields": "Fields",
        "checks": "Checks",
        "cost": "Estimated cost",
        "coverage": "Page Coverage",
        "extracted_selected": "extracted / selected",
        "triaged_total": "pages triaged in total",
        "skipped_cap": "pages were not analyzed because of the demo cap",
        "no_skipped": "No pages were skipped by the demo cap.",
        "executive": "Executive Summary",
        "validation": "Validation Checks",
        "priorities": "Review Priorities",
        "source_files": "Source Files",
        "fuzzy": "Fuzzy Review",
        "clusters": "Clusters",
        "errors": "Errors",
        "pages_word": "pages",
        "none": "None recorded.",
        "no_checks": "No validation checks recorded.",
        "no_clusters": "No clusters recorded.",
        "no_fuzzy": "No fuzzy near-match groups recorded.",
        "no_errors": "No page extraction errors recorded.",
        "language_note": "",
    }


def _status_badge(status: Any, lang: str = "en") -> str:
    safe = html.escape(str(status or "unknown"))
    cls = re.sub(r"[^a-z0-9_-]+", "-", safe.lower())
    label = html.escape(_translate_status(status, lang))
    return f'<span class="badge badge-{cls}">{label}</span>'


def _list_items(values: Any, limit: int = 8, empty_text: str = "None recorded.") -> str:
    if not isinstance(values, list) or not values:
        return f'<p class="muted">{html.escape(empty_text)}</p>'
    items = []
    for value in values[:limit]:
        items.append(f"<li>{html.escape(str(value))}</li>")
    if len(values) > limit:
        items.append(f'<li class="muted">... {len(values) - limit} more</li>')
    return f"<ul>{''.join(items)}</ul>"


def _render_check_cards(
    checks: Any,
    *,
    packet: dict[str, Any],
    job_id: str,
    token: str,
    lang: str = "en",
) -> str:
    labels = _review_labels(lang)
    if not isinstance(checks, list) or not checks:
        return f'<p class="muted">{html.escape(labels["no_checks"])}</p>'
    order = {"fail": 0, "warning": 1, "unknown": 2, "pass": 3}
    sorted_checks = sorted(checks, key=lambda c: order.get(str(c.get("status", "unknown")), 99) if isinstance(c, dict) else 99)
    cards = []
    fields_by_ref = _field_index_by_ref(packet)
    for check in sorted_checks:
        if not isinstance(check, dict):
            continue
        title = html.escape(_translate_check_title(check.get("title") or check.get("check_id") or "Check", lang))
        summary = html.escape(str(check.get("summary") or ""))
        evidence_refs = check.get("evidence_refs") if isinstance(check.get("evidence_refs"), list) else []
        evidence = " ".join(
            _field_ref_thumbnail_html(job_id, token, str(ref), fields_by_ref.get(str(ref)))
            for ref in evidence_refs[:8]
        )
        if len(evidence_refs) > 8:
            evidence += f' <span class="muted">+{len(evidence_refs) - 8} more</span>'
        details = _list_items(check.get("details"), limit=6, empty_text=labels["none"])
        cards.append(
            f"""
            <article class="card check-card">
              <div class="card-head">{_status_badge(check.get("status"), lang)}<h3>{title}</h3></div>
              <p>{summary}</p>
              {details}
              <div class="refs">{evidence}</div>
            </article>
            """
        )
    return "".join(cards)


def _render_cluster_cards(clusters: Any, lang: str = "en") -> str:
    labels = _review_labels(lang)
    if not isinstance(clusters, list) or not clusters:
        return f'<p class="muted">{html.escape(labels["no_clusters"])}</p>'
    cards = []
    for cluster in clusters[:24]:
        if not isinstance(cluster, dict):
            continue
        field_type = html.escape(str(cluster.get("field_type") or "field"))
        subtype = cluster.get("subtype")
        canonical = html.escape(str(cluster.get("canonical_value") or ""))
        mentions = cluster.get("mentions") if isinstance(cluster.get("mentions"), list) else []
        subtitle = f"{field_type}"
        if subtype:
            subtitle += f" / {html.escape(str(subtype))}"
        cards.append(
            f"""
            <article class="mini-card">
              <div class="muted">{subtitle} · {len(mentions)} mentions</div>
              <div class="mono strong">{canonical}</div>
            </article>
            """
        )
    if len(clusters) > 24:
        cards.append(f'<p class="muted">Showing 24 of {len(clusters)} clusters.</p>')
    return "".join(cards)


def _render_fuzzy_groups(groups: Any, lang: str = "en") -> str:
    labels = _review_labels(lang)
    if not isinstance(groups, list) or not groups:
        return f'<p class="muted">{html.escape(labels["no_fuzzy"])}</p>'
    cards = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        values = group.get("canonical_values") if isinstance(group.get("canonical_values"), list) else []
        cards.append(
            f"""
            <article class="mini-card">
              <div>{_status_badge(group.get("status"), lang)} <span class="muted">{html.escape(str(group.get("field_type") or ""))}</span></div>
              {_list_items(values, limit=6, empty_text=labels["none"])}
            </article>
            """
        )
    return "".join(cards)


def _render_page_coverage(totals: dict[str, Any], labels: dict[str, str]) -> str:
    pages_triaged = int(totals.get("pages_triaged") or 0)
    pages_selected = int(totals.get("pages_selected") or 0)
    pages_extracted = int(totals.get("pages_extracted") or 0)
    pages_failed = int(totals.get("pages_failed") or 0)
    skipped = max(pages_triaged - pages_selected, 0)
    skipped_text = (
        f"{skipped} {labels['skipped_cap']}"
        if skipped
        else labels["no_skipped"]
    )
    failed_text = f" · {pages_failed} failed" if pages_failed else ""
    return f"""
    <section class="section card coverage-card">
      <h2>{html.escape(labels['coverage'])}</h2>
      <div class="coverage-grid">
        <div><strong>{html.escape(str(pages_extracted))} / {html.escape(str(pages_selected))}</strong><span>{html.escape(labels['extracted_selected'])}{html.escape(failed_text)}</span></div>
        <div><strong>{html.escape(str(pages_triaged))}</strong><span>{html.escape(labels['triaged_total'])}</span></div>
        <div class="{html.escape('coverage-warn' if skipped else 'coverage-ok')}"><strong>{html.escape(str(skipped))}</strong><span>{html.escape(skipped_text)}</span></div>
      </div>
    </section>
    """


def _field_index_by_ref(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for field in ocr_script._iter_packet_fields(packet):
        ref = field.get("field_ref")
        if isinstance(ref, str) and ref:
            indexed[ref] = field
    return indexed


def _safe_job_source_path(job_id: str, source_file: Any) -> Path:
    uploads_dir = (_job_dir(job_id) / "uploads").resolve()
    candidate = Path(str(source_file)).resolve()
    if uploads_dir not in candidate.parents and candidate != uploads_dir:
        raise HTTPException(status_code=403, detail="Source file is outside this job.")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Source file not found.")
    return candidate


def _field_ref_thumbnail_html(job_id: str, token: str, ref: str, field: dict[str, Any] | None) -> str:
    safe_ref = html.escape(ref)
    if not field:
        return f"<code>{safe_ref}</code>"
    page_id = html.escape(str(field.get("page_id") or ""))
    value = html.escape(str(field.get("value") or ""))
    thumb_query = urlencode({"token": token, "field_ref": ref})
    thumb_url = f"/jobs/{html.escape(job_id)}/page-thumbnail?{html.escape(thumb_query)}"
    return f"""
    <a class="evidence-card" href="{thumb_url}" target="_blank" title="{safe_ref}">
      <img src="{thumb_url}" loading="lazy" />
      <span><code>{safe_ref}</code><small>{page_id} · {value}</small></span>
    </a>
    """


def _render_packet_review(job_id: str, packet: dict[str, Any], token: str = "", lang: str = "el") -> str:
    lang = "el" if lang == "el" else "en"
    labels = _review_labels(lang)
    token_query_values = {"lang": lang}
    if token:
        token_query_values["token"] = token
    token_query = f"?{html.escape(urlencode(token_query_values))}"
    token_only_query = f"?{html.escape(urlencode({'token': token}))}" if token else ""
    executive = packet.get("executive_summary") if isinstance(packet.get("executive_summary"), dict) else {}
    totals = packet.get("totals") if isinstance(packet.get("totals"), dict) else {}
    check_summary = packet.get("check_summary") if isinstance(packet.get("check_summary"), dict) else {}
    checks_by_status = check_summary.get("checks_by_status") if isinstance(check_summary.get("checks_by_status"), dict) else {}
    cost = packet.get("cost_summary") if isinstance(packet.get("cost_summary"), dict) else {}
    source_files = packet.get("source_files") if isinstance(packet.get("source_files"), list) else []
    errors = packet.get("errors") if isinstance(packet.get("errors"), list) else []
    headline = html.escape(str(executive.get("headline") or "Packet review"))
    created_at = html.escape(str(packet.get("created_at") or ""))
    provider = html.escape(str(packet.get("provider_config", {}).get("provider") or packet.get("provider") or ""))
    model = html.escape(str(packet.get("provider_config", {}).get("model") or packet.get("model") or ""))
    status_counts = " ".join(
        f"{_status_badge(status, lang)} <strong>{html.escape(str(count))}</strong>"
        for status, count in checks_by_status.items()
    )
    file_rows = "".join(
        f"<li><span>{html.escape(_basename(file.get('source_file') if isinstance(file, dict) else file))}</span><span class='muted'>{html.escape(str(file.get('page_count', '?') if isinstance(file, dict) else '?'))} {html.escape(labels['pages_word'])}</span></li>"
        for file in source_files
    )
    error_cards = "".join(
        f"<article class='mini-card'><strong>{html.escape(str(error.get('page_id') or error.get('source_file') or 'page'))}</strong><p>{html.escape(str(error.get('error') or error))}</p></article>"
        for error in errors
        if isinstance(error, dict)
    ) or f'<p class="muted">{html.escape(labels["no_errors"])}</p>'
    findings = _list_items(executive.get("key_findings"), limit=8, empty_text=labels["none"])
    priorities = _list_items(executive.get("review_priorities"), limit=8, empty_text=labels["none"])
    estimated_cost = float(cost.get("estimated_cost_usd") or 0.0)
    lang_switch = "en" if lang == "el" else "el"
    lang_switch_label = "English" if lang == "el" else "Ελληνικά"
    return f"""
    <html>
      <head>
        <title>arch-ocr review {html.escape(job_id)}</title>
        <style>
          :root {{ --bg:#fafaf7; --paper:#fff; --paper2:#f6f4ee; --line:#e7e3d8; --ink:#14181f; --muted:#68707c; --accent:#1a2540; }}
          * {{ box-sizing:border-box; }}
          body {{ margin:0; background:var(--bg); color:var(--ink); font-family:Inter, ui-sans-serif, system-ui, sans-serif; line-height:1.5; }}
          header {{ position:sticky; top:0; z-index:1; background:rgba(250,250,247,.96); border-bottom:1px solid var(--line); padding:22px 36px; }}
          main {{ padding:28px 36px 48px; }}
          h1 {{ margin:0 0 8px; font-family:Georgia, serif; font-size:30px; line-height:1.15; }}
          h2 {{ margin:0 0 14px; font-size:15px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }}
          h3 {{ margin:0; font-size:16px; }}
          a {{ color:var(--accent); }}
          code,.mono {{ font-family:"JetBrains Mono", ui-monospace, monospace; font-size:12px; }}
          .muted {{ color:var(--muted); }}
          .strong {{ font-weight:700; color:var(--ink); }}
          .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
          .btn {{ display:inline-flex; align-items:center; height:34px; padding:0 12px; border:1px solid var(--line); border-radius:6px; background:var(--paper); text-decoration:none; font-weight:600; font-size:13px; }}
          .btn-primary {{ background:var(--accent); color:white; border-color:var(--accent); }}
          .grid {{ display:grid; grid-template-columns:1.15fr .85fr; gap:22px; align-items:start; }}
          .section {{ margin-bottom:22px; }}
          .card,.mini-card {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:16px; }}
          .mini-card {{ margin-bottom:8px; }}
          .check-card {{ margin-bottom:12px; }}
          .card-head {{ display:flex; gap:10px; align-items:center; margin-bottom:8px; }}
          .stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:18px 0; }}
          .stat {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:14px; }}
          .stat .value {{ font-size:22px; font-weight:700; }}
          .coverage-card {{ margin-top:-8px; }}
          .coverage-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
          .coverage-grid div {{ border:1px solid var(--line); border-radius:7px; background:var(--paper2); padding:12px; }}
          .coverage-grid strong {{ display:block; font-size:20px; }}
          .coverage-grid span {{ display:block; color:var(--muted); font-size:13px; }}
          .coverage-warn {{ border-color:#e2bd5b !important; background:#fff7db !important; }}
          .coverage-ok {{ border-color:#b7ddc8 !important; background:#eef8f2 !important; }}
          .badge {{ display:inline-flex; align-items:center; border-radius:999px; padding:2px 8px; font-size:12px; font-weight:700; border:1px solid var(--line); background:var(--paper2); }}
          .badge-pass {{ color:#27724f; background:#eef8f2; }}
          .badge-warning {{ color:#926800; background:#fff7db; }}
          .badge-fail {{ color:#a43a2d; background:#fff0ed; }}
          .badge-unknown {{ color:#626b76; background:#f1f2f3; }}
          .refs {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }}
          .refs code {{ background:var(--paper2); border:1px solid var(--line); border-radius:999px; padding:2px 7px; }}
          .evidence-card {{ width:132px; display:block; border:1px solid var(--line); border-radius:7px; overflow:hidden; background:var(--paper2); text-decoration:none; color:var(--ink); }}
          .evidence-card img {{ width:100%; height:92px; object-fit:cover; object-position:top center; display:block; background:#fff; border-bottom:1px solid var(--line); }}
          .evidence-card span {{ display:block; padding:6px; }}
          .evidence-card code {{ display:block; border:0; background:transparent; padding:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
          .evidence-card small {{ display:block; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-top:2px; }}
          ul {{ margin:8px 0 0; padding-left:20px; }}
          .file-list {{ padding:0; list-style:none; }}
          .file-list li {{ display:flex; justify-content:space-between; gap:12px; border-bottom:1px solid var(--line); padding:8px 0; }}
          @media (max-width: 900px) {{ .grid,.stats {{ grid-template-columns:1fr; }} header,main {{ padding-left:18px; padding-right:18px; }} }}
        </style>
      </head>
      <body>
        <header>
          <div class="muted mono">arch-ocr {html.escape(labels['title_prefix'])} · {html.escape(job_id)}</div>
          <h1>{headline}</h1>
          <div class="muted">{created_at} · {provider} · {model}</div>
          {f'<p class="muted">{html.escape(labels["language_note"])}</p>' if labels["language_note"] else ''}
          <div class="actions">
            <a class="btn btn-primary" href="/jobs/{html.escape(job_id)}/report{token_only_query}">{html.escape(labels['markdown'])}</a>
            <a class="btn" href="/jobs/{html.escape(job_id)}/packet{token_only_query}">{html.escape(labels['json'])}</a>
            <a class="btn" href="/jobs/{html.escape(job_id)}/review?{html.escape(urlencode({'token': token, 'lang': lang_switch}))}">{lang_switch_label}</a>
            <a class="btn" href="/design/arch-ocr.html?screen=job&job={html.escape(job_id)}">{html.escape(labels['back'])}</a>
            <a class="btn" href="/admin{token_only_query}">{html.escape(labels['admin'])}</a>
          </div>
        </header>
        <main>
          <section class="stats">
            <div class="stat"><div class="muted">{html.escape(labels['pages'])}</div><div class="value">{html.escape(str(totals.get("pages_extracted", 0)))} / {html.escape(str(totals.get("pages_selected", totals.get("pages_triaged", 0))))}</div></div>
            <div class="stat"><div class="muted">{html.escape(labels['fields'])}</div><div class="value">{html.escape(str(totals.get("field_count", 0)))}</div></div>
            <div class="stat"><div class="muted">{html.escape(labels['checks'])}</div><div class="value">{html.escape(str(check_summary.get("check_count", 0)))}</div><div>{status_counts}</div></div>
            <div class="stat"><div class="muted">{html.escape(labels['cost'])}</div><div class="value">${estimated_cost:.6f}</div></div>
          </section>
          {_render_page_coverage(totals, labels)}
          <div class="grid">
            <div>
              <section class="section card">
                <h2>{html.escape(labels['executive'])}</h2>
                {findings}
              </section>
              <section class="section">
                <h2>{html.escape(labels['validation'])}</h2>
                {_render_check_cards(packet.get("checks"), packet=packet, job_id=job_id, token=token, lang=lang)}
              </section>
              <section class="section card">
                <h2>{html.escape(labels['priorities'])}</h2>
                {priorities}
              </section>
            </div>
            <aside>
              <section class="section card">
                <h2>{html.escape(labels['source_files'])}</h2>
                <ul class="file-list">{file_rows}</ul>
              </section>
              <section class="section card">
                <h2>{html.escape(labels['fuzzy'])}</h2>
                {_render_fuzzy_groups(packet.get("fuzzy_groups"), lang)}
              </section>
              <section class="section card">
                <h2>{html.escape(labels['clusters'])}</h2>
                {_render_cluster_cards(packet.get("clusters"), lang)}
              </section>
              <section class="section card">
                <h2>{html.escape(labels['errors'])}</h2>
                {error_cards}
              </section>
            </aside>
          </div>
        </main>
      </body>
    </html>
    """


def _selected_pages_for_packet(triage_artifact: dict[str, Any]) -> list[tuple[Path, dict[str, Any]]]:
    selected: list[tuple[Path, dict[str, Any]]] = []
    for document in triage_artifact.get("documents", []):
        if not isinstance(document, dict):
            continue
        source_file = Path(str(document.get("source_file", "")))
        for page in document.get("pages", []):
            if not isinstance(page, dict):
                continue
            selected.append((source_file, page))
            if len(selected) >= MAX_PAGES_PER_PACKET:
                return selected
    return selected


def _process_job(job_id: str) -> None:
    if not worker_lock.acquire(blocking=False):
        _append_job_event(job_id, "job_queued", reason="another_job_running")
        _set_job_status(job_id, "queued", message="Another job is running. This job will wait.")
        with worker_lock:
            pass
        worker_lock.acquire()

    try:
        job = _load_job(job_id)
        _raise_if_abort_requested(job_id)
        _append_job_event(job_id, "job_started")
        upload_dir = _job_dir(job_id) / "uploads"
        output_dir = _job_dir(job_id) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_files = [
            Path(file_info["path"])
            for file_info in job.get("files", [])
            if isinstance(file_info, dict)
        ]
        _set_job_status(job_id, "triaging", message="Triaging uploaded files.")
        _append_job_event(
            job_id,
            "triage_started",
            input_file_count=len(input_files),
            max_pages_per_packet=MAX_PAGES_PER_PACKET,
        )

        api_key = ocr_script._require_api_key(PROVIDER, None)
        triage_artifact = ocr_script._build_triage_artifact(
            input_path=upload_dir,
            files=input_files,
            max_pages_per_file=None,
            preview_dpi=PREVIEW_DPI,
        )
        selected_pages = _selected_pages_for_packet(triage_artifact)
        triage_totals = (
            triage_artifact.get("totals", {})
            if isinstance(triage_artifact.get("totals"), dict)
            else {}
        )
        pages_triaged = int(triage_totals.get("pages") or 0)
        _append_job_event(
            job_id,
            "triage_completed",
            files_triaged=int(triage_totals.get("files") or 0),
            pages_triaged=pages_triaged,
            pages_selected=len(selected_pages),
            pages_skipped_by_cap=max(pages_triaged - len(selected_pages), 0),
            max_pages_per_packet=MAX_PAGES_PER_PACKET,
        )
        totals = ocr_script._empty_packet_totals(triage_artifact, len(selected_pages))
        cost_totals = ocr_script._empty_cost_totals(provider=PROVIDER, model=MODEL)
        extractions: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for index, (source_file, page_triage) in enumerate(selected_pages, start=1):
            _raise_if_abort_requested(job_id)
            page_number = int(page_triage.get("page_number", 1))
            page_id = str(page_triage.get("page_id", f"{source_file.stem}:p{page_number}"))
            _set_job_status(
                job_id,
                "processing",
                message=f"Processing page {index} of {len(selected_pages)}.",
                current_page_id=page_id,
                pages_selected=len(selected_pages),
                pages_processed=index - 1,
            )
            _append_job_event(
                job_id,
                "page_started",
                page_index=index,
                pages_selected=len(selected_pages),
                page_id=page_id,
                source_file=_basename(source_file),
                page_number=page_number,
            )

            try:
                rendered_page = ocr_script._read_single_input_page(source_file, page_number, RENDER_DPI)
                artifact: dict[str, Any] | None = None
                max_attempts = MAX_PROVIDER_RETRIES + 1
                for attempt in range(1, max_attempts + 1):
                    _raise_if_abort_requested(job_id, page_id)
                    _throttle_before_provider_call(job_id, page_id)
                    try:
                        artifact, _raw_response = ocr_script._run_validated_json_extraction(
                            input_path=source_file,
                            provider=PROVIDER,
                            api_key=api_key,
                            model=MODEL,
                            pages=[rendered_page],
                            page_triage=[page_triage],
                            language_hints=LANGUAGE_HINTS,
                            timeout=REQUEST_TIMEOUT,
                        )
                        break
                    except Exception as exc:
                        error_message = ocr_script._redact_secrets(str(exc))
                        can_retry = attempt < max_attempts and _is_transient_provider_error(error_message)
                        if not can_retry:
                            raise
                        delay = _retry_delay_seconds(attempt)
                        _record_provider_event(
                            job_id=job_id,
                            page_id=page_id,
                            source_file=str(source_file),
                            page_number=page_number,
                            status="retry_wait",
                            error=error_message,
                            attempt=attempt,
                            max_attempts=max_attempts,
                        )
                        _set_job_status(
                            job_id,
                            "retrying",
                            message=(
                                f"Provider is busy for page {index} of {len(selected_pages)}. "
                                f"Retry {attempt} of {MAX_PROVIDER_RETRIES} in {round(delay, 1)}s."
                            ),
                            current_page_id=page_id,
                            pages_selected=len(selected_pages),
                            pages_processed=index - 1,
                        )
                        time.sleep(delay)
                        _raise_if_abort_requested(job_id, page_id)

                if artifact is None:
                    raise RuntimeError("Provider extraction ended without an artifact.")

                extractions.append(artifact)
                ocr_script._add_extraction_totals(totals, artifact)
                ocr_script._add_cost_totals(cost_totals, artifact)
                _record_provider_event(
                    job_id=job_id,
                    page_id=page_id,
                    source_file=str(source_file),
                    page_number=page_number,
                    status="success",
                    usage=artifact.get("usage") if isinstance(artifact.get("usage"), dict) else {},
                    attempt=attempt,
                    max_attempts=max_attempts,
                )
                _append_job_event(
                    job_id,
                    "page_completed",
                    page_index=index,
                    pages_selected=len(selected_pages),
                    page_id=page_id,
                    source_file=_basename(source_file),
                    page_number=page_number,
                    attempt=attempt,
                )
            except JobAborted:
                raise
            except Exception as exc:
                error_message = ocr_script._redact_secrets(str(exc))
                totals["pages_failed"] += 1
                errors.append(
                    {
                        "source_file": str(source_file),
                        "page_number": page_number,
                        "page_id": page_id,
                        "error": error_message,
                    }
                )
                _record_provider_event(
                    job_id=job_id,
                    page_id=page_id,
                    source_file=str(source_file),
                    page_number=page_number,
                    status="failure",
                    error=error_message,
                    attempt=MAX_PROVIDER_RETRIES + 1,
                    max_attempts=MAX_PROVIDER_RETRIES + 1,
                )
                _append_job_event(
                    job_id,
                    "page_failed",
                    page_index=index,
                    pages_selected=len(selected_pages),
                    page_id=page_id,
                    source_file=_basename(source_file),
                    page_number=page_number,
                    error=error_message,
                )

        _append_job_event(
            job_id,
            "analysis_started",
            successful_page_artifacts=len(extractions),
            page_errors=len(errors),
        )
        packet = ocr_script._build_packet_artifact(
            input_path=upload_dir,
            provider=PROVIDER,
            model=MODEL,
            dpi=RENDER_DPI,
            max_pages_per_file=MAX_PAGES_PER_PACKET,
            triage_artifact=triage_artifact,
            extractions=extractions,
            errors=errors,
            totals=totals,
        )
        packet["cost_summary"] = ocr_script._finalize_cost_totals(cost_totals)
        ocr_script._attach_packet_analysis(packet)
        packet_path = output_dir / "packet.json"
        report_path = output_dir / "packet_report.md"
        packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(ocr_script._render_packet_report(packet), encoding="utf-8")

        _append_job_event(
            job_id,
            "job_completed",
            status="completed" if not errors else "completed_with_errors",
            pages_selected=len(selected_pages),
            pages_extracted=packet.get("totals", {}).get("pages_extracted"),
            pages_failed=packet.get("totals", {}).get("pages_failed"),
            field_count=packet.get("totals", {}).get("field_count"),
            estimated_cost_usd=packet.get("cost_summary", {}).get("estimated_cost_usd"),
            check_summary=packet.get("check_summary", {}),
        )
        _set_job_status(
            job_id,
            "completed" if not errors else "completed_with_errors",
            message="Job completed.",
            pages_selected=len(selected_pages),
            pages_processed=len(selected_pages),
            packet_path=str(packet_path),
            report_path=str(report_path),
            totals=packet.get("totals", {}),
            cost_summary=packet.get("cost_summary", {}),
            check_summary=packet.get("check_summary", {}),
        )
    except JobAborted as exc:
        _append_job_event(job_id, "job_aborted", message=str(exc))
        _set_job_status(job_id, "aborted", message=str(exc))
    except Exception as exc:
        error_message = ocr_script._redact_secrets(str(exc))
        _append_job_event(job_id, "job_failed", error=error_message)
        _set_job_status(job_id, "failed", message=error_message)
    finally:
        try:
            worker_lock.release()
        except RuntimeError:
            pass


@app.on_event("startup")
def startup() -> None:
    _ensure_storage()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "storage_dir": str(STORAGE_DIR)}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> RedirectResponse:
    return RedirectResponse("/design/arch-ocr.html", status_code=307)


@app.get("/session")
def session(request: Request) -> dict[str, Any]:
    user = _current_user(request)
    return {
        "authenticated": bool(user) or not DEMO_REQUIRE_TOKEN,
        "user": user,
        "limits": {
            "max_files_per_packet": MAX_FILES_PER_PACKET,
            "max_pages_per_packet": MAX_PAGES_PER_PACKET,
            "max_upload_mb": MAX_UPLOAD_MB,
        },
    }


@app.post("/login")
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


@app.post("/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.post("/jobs")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    token: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> JSONResponse:
    _ensure_storage()
    _require_demo_access(request, token)
    if len(files) > MAX_FILES_PER_PACKET:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum is {MAX_FILES_PER_PACKET}.",
        )

    job_id = str(uuid4())
    job_dir = _job_dir(job_id)
    upload_dir = job_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[dict[str, Any]] = []

    for upload in files:
        filename = _safe_filename(upload.filename or "upload")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")
        destination = upload_dir / filename
        with destination.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        size = destination.stat().st_size
        if size > MAX_UPLOAD_MB * 1024 * 1024:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"File too large: {filename}")
        saved_files.append({"filename": filename, "path": str(destination), "size_bytes": size})

    job = {
        "job_id": job_id,
        "status": "queued",
        "message": "Job queued.",
        "title": (title or "").strip() or None,
        "created_at": _now(),
        "updated_at": _now(),
        "files": saved_files,
        "limits": {
            "max_files_per_packet": MAX_FILES_PER_PACKET,
            "max_pages_per_packet": MAX_PAGES_PER_PACKET,
            "max_upload_mb": MAX_UPLOAD_MB,
            "provider_min_seconds_between_calls": MIN_SECONDS_BETWEEN_CALLS,
            "provider_max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
            "provider_max_requests_per_day": MAX_REQUESTS_PER_DAY,
            "provider_max_retries": MAX_PROVIDER_RETRIES,
            "provider_retry_base_seconds": PROVIDER_RETRY_BASE_SECONDS,
            "provider_retry_max_seconds": PROVIDER_RETRY_MAX_SECONDS,
        },
    }
    _save_job(job)
    _append_job_event(
        job_id,
        "job_created",
        title=job.get("title"),
        file_count=len(saved_files),
        files=[
            {
                "filename": file_info.get("filename"),
                "size_bytes": file_info.get("size_bytes"),
            }
            for file_info in saved_files
        ],
        limits=job["limits"],
    )
    background_tasks.add_task(_process_job, job_id)
    accept_header = request.headers.get("accept", "")
    if "text/html" in accept_header:
        return RedirectResponse(_job_view_url(job_id, token or ""), status_code=303)
    return JSONResponse(job, status_code=202)


@app.post("/jobs/draft")
async def create_draft_job(
    request: Request,
    files: list[UploadFile] = File(...),
    token: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> JSONResponse:
    _ensure_storage()
    _require_demo_access(request, token)
    if len(files) > MAX_FILES_PER_PACKET:
        raise HTTPException(status_code=400, detail=f"Too many files. Maximum is {MAX_FILES_PER_PACKET}.")

    job_id = str(uuid4())
    job_dir = _job_dir(job_id)
    upload_dir = job_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[dict[str, Any]] = []

    for upload in files:
        filename = _safe_filename(upload.filename or "upload")
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")
        destination = upload_dir / filename
        counter = 1
        while destination.exists():
            destination = upload_dir / f"{Path(filename).stem}_{counter}{suffix}"
            counter += 1
        with destination.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        size = destination.stat().st_size
        if size > MAX_UPLOAD_MB * 1024 * 1024:
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"File too large: {filename}")
        saved_files.append({"filename": filename, "path": str(destination), "size_bytes": size})

    job = {
        "job_id": job_id,
        "status": "uploaded",
        "message": "Files uploaded. Ready to start validation.",
        "title": (title or "").strip() or None,
        "created_at": _now(),
        "updated_at": _now(),
        "files": saved_files,
        "limits": {
            "max_files_per_packet": MAX_FILES_PER_PACKET,
            "max_pages_per_packet": MAX_PAGES_PER_PACKET,
            "max_upload_mb": MAX_UPLOAD_MB,
            "provider_min_seconds_between_calls": MIN_SECONDS_BETWEEN_CALLS,
            "provider_max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
            "provider_max_requests_per_day": MAX_REQUESTS_PER_DAY,
            "provider_max_retries": MAX_PROVIDER_RETRIES,
            "provider_retry_base_seconds": PROVIDER_RETRY_BASE_SECONDS,
            "provider_retry_max_seconds": PROVIDER_RETRY_MAX_SECONDS,
        },
    }
    _save_job(job)
    _append_job_event(
        job_id,
        "files_uploaded",
        title=job.get("title"),
        file_count=len(saved_files),
        files=[{"filename": f.get("filename"), "size_bytes": f.get("size_bytes")} for f in saved_files],
        limits=job["limits"],
    )
    return JSONResponse(job, status_code=201)


@app.post("/jobs/{job_id}/start")
async def start_uploaded_job(job_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    _require_demo_access(request)
    job = _load_job(job_id)
    if str(job.get("status")) not in {"uploaded", "aborted", "failed"}:
        return job
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    title = str(payload.get("title") or "").strip()
    if title:
        job["title"] = title
    job["status"] = "queued"
    job["message"] = "Job queued."
    job["updated_at"] = _now()
    _save_job(job)
    _append_job_event(job_id, "job_start_requested", title=job.get("title"))
    background_tasks.add_task(_process_job, job_id)
    return _load_job(job_id)


@app.get("/jobs")
def list_jobs(request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    jobs = []
    for path in sorted(JOBS_DIR.glob("*/job.json"), reverse=True):
        try:
            jobs.append(_read_json(path))
        except Exception:
            continue
    return {"jobs": jobs[:100]}


@app.get("/jobs/{job_id}/view", response_class=HTMLResponse)
def view_job(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    _require_demo_access(request)
    return _job_status_page(_load_job(job_id), token)


@app.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, request: Request, limit: int = 200) -> dict[str, Any]:
    _require_demo_access(request)
    job = _load_job(job_id)
    safe_limit = max(1, min(int(limit), 1000))
    events = _job_events(job_id, limit=safe_limit)
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "event_count": len(events),
        "events": events,
    }


@app.get("/jobs/{job_id}/logs", response_class=HTMLResponse)
def view_job_logs(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    _require_demo_access(request)
    return _job_logs_page(_load_job(job_id), _job_events(job_id), token)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    return _load_job(job_id)


@app.post("/jobs/{job_id}/abort")
def abort_job(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    job = _load_job(job_id)
    if str(job.get("status")) in {"completed", "completed_with_errors", "failed", "aborted"}:
        return job
    _append_job_event(job_id, "abort_requested", requested_by=(_current_user(request) or {}).get("username"))
    _set_job_status(job_id, "abort_requested", message="Abort requested. The job will stop before the next provider call.")
    return _load_job(job_id)


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    job = _load_job(job_id)
    if str(job.get("status")) not in {"uploaded", "completed", "completed_with_errors", "failed", "aborted"}:
        raise HTTPException(status_code=409, detail="Abort the running job before deleting it.")
    job_dir = _job_dir(job_id)
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found.")
    shutil.rmtree(job_dir)
    return {"ok": True, "job_id": job_id, "deleted": True}


@app.get("/jobs/{job_id}/packet")
def get_packet(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    return _load_analyzed_packet(job_id)


@app.get("/jobs/{job_id}/report", response_class=PlainTextResponse)
def get_report(job_id: str, request: Request) -> str:
    _require_demo_access(request)
    return ocr_script._render_packet_report(_load_analyzed_packet(job_id))


@app.get("/jobs/{job_id}/review", response_class=HTMLResponse)
def get_review(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    lang = request.query_params.get("lang") or "el"
    _require_demo_access(request)
    return _render_packet_review(job_id, _load_analyzed_packet(job_id), token, lang)


@app.get("/jobs/{job_id}/page-thumbnail")
def get_page_thumbnail(job_id: str, request: Request, field_ref: str) -> Response:
    _require_demo_access(request)
    packet = _load_analyzed_packet(job_id)
    field = _field_index_by_ref(packet).get(field_ref)
    if not field:
        raise HTTPException(status_code=404, detail="Field reference not found.")
    source_file = _safe_job_source_path(job_id, field.get("source_file"))
    page_number = int(field.get("page_number") or 0)
    if page_number < 1:
        raise HTTPException(status_code=404, detail="Field page not found.")
    try:
        with ocr_script.fitz.open(source_file) as document:
            if page_number > len(document):
                raise HTTPException(status_code=404, detail="Page not found.")
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=ocr_script.fitz.Matrix(0.35, 0.35), alpha=False)
            return Response(content=pixmap.tobytes("jpeg"), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=ocr_script._redact_secrets(str(exc))) from exc


@app.get("/usage", response_model=None)
def usage(request: Request):
    _require_demo_access(request)
    summary = _usage_summary()
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_usage_page(summary))
    return summary


@app.get("/admin", response_class=HTMLResponse)
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
