from __future__ import annotations

import html
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

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
ADMIN_TOKEN = (os.getenv("OCR_ADMIN_TOKEN") or "").strip()
DEMO_REQUIRE_TOKEN = os.getenv("OCR_DEMO_REQUIRE_TOKEN", "true").lower() != "false"

PROVIDER = os.getenv("CLOUD_PROVIDER", ocr_script.DEFAULT_PROVIDER)
MODEL = os.getenv("GEMINI_MODEL") or ocr_script._default_model_for_provider(PROVIDER)
PREVIEW_DPI = int(os.getenv("OCR_PREVIEW_DPI", "36"))
RENDER_DPI = int(os.getenv("OCR_RENDER_DPI", "150"))
REQUEST_TIMEOUT = int(os.getenv("OCR_PROVIDER_TIMEOUT", "180"))
LANGUAGE_HINTS = os.getenv("GOOGLE_VISION_LANGUAGE_HINTS", "el,en")

app = FastAPI(title=APP_NAME)
worker_lock = threading.Lock()
ledger_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _ensure_storage() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    USAGE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    USAGE_LEDGER_PATH.touch(exist_ok=True)


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _job_path(job_id: str) -> Path:
    return _job_dir(job_id) / "job.json"


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


def _save_job(job: dict[str, Any]) -> None:
    _write_json(_job_path(str(job["job_id"])), job)


def _set_job_status(job_id: str, status: str, **updates: Any) -> None:
    job = _load_job(job_id)
    job.update(updates)
    job["status"] = status
    job["updated_at"] = _now()
    _save_job(job)


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
            _set_job_status(
                job_id,
                "rate_limited",
                message="Daily provider request limit reached. Try again later.",
                current_page_id=page_id,
            )
            time.sleep(60)
            continue

        if minute_count >= MAX_REQUESTS_PER_MINUTE:
            _set_job_status(
                job_id,
                "rate_limited",
                message="Provider request rate limit reached. Waiting before continuing.",
                current_page_id=page_id,
            )
            time.sleep(10)
            continue

        if last_wait > 0:
            _set_job_status(
                job_id,
                "throttled",
                message=f"Waiting {round(last_wait, 1)}s before next provider call.",
                current_page_id=page_id,
            )
            time.sleep(last_wait)
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
) -> None:
    usage = usage or {}
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
            "error": error,
        }
    )


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
        _set_job_status(job_id, "queued", message="Another job is running. This job will wait.")
        with worker_lock:
            pass
        worker_lock.acquire()

    try:
        job = _load_job(job_id)
        upload_dir = _job_dir(job_id) / "uploads"
        output_dir = _job_dir(job_id) / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_files = [
            Path(file_info["path"])
            for file_info in job.get("files", [])
            if isinstance(file_info, dict)
        ]
        _set_job_status(job_id, "triaging", message="Triaging uploaded files.")

        api_key = ocr_script._require_api_key(PROVIDER, None)
        triage_artifact = ocr_script._build_triage_artifact(
            input_path=upload_dir,
            files=input_files,
            max_pages_per_file=None,
            preview_dpi=PREVIEW_DPI,
        )
        selected_pages = _selected_pages_for_packet(triage_artifact)
        totals = ocr_script._empty_packet_totals(triage_artifact, len(selected_pages))
        cost_totals = ocr_script._empty_cost_totals(provider=PROVIDER, model=MODEL)
        extractions: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for index, (source_file, page_triage) in enumerate(selected_pages, start=1):
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
            _throttle_before_provider_call(job_id, page_id)

            try:
                rendered_page = ocr_script._read_single_input_page(source_file, page_number, RENDER_DPI)
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
                )
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
    except Exception as exc:
        _set_job_status(job_id, "failed", message=ocr_script._redact_secrets(str(exc)))
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
def index(request: Request) -> str:
    token_hint = "" if DEMO_REQUIRE_TOKEN else "Demo token not required."
    return f"""
    <html>
      <head><title>arch-ocr demo</title></head>
      <body>
        <h1>arch-ocr demo</h1>
        <p>Upload up to {MAX_FILES_PER_PACKET} PDF/image files. The demo processes up to {MAX_PAGES_PER_PACKET} pages total.</p>
        <form action="/jobs" method="post" enctype="multipart/form-data">
          <label>Demo token <input name="token" type="password" /></label>
          <p>{html.escape(token_hint)}</p>
          <input name="files" type="file" multiple />
          <button type="submit">Start OCR job</button>
        </form>
        <p><a href="/admin">Admin</a></p>
      </body>
    </html>
    """


@app.post("/jobs")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    token: str | None = Form(default=None),
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
        },
    }
    _save_job(job)
    background_tasks.add_task(_process_job, job_id)
    return JSONResponse(job, status_code=202)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    return _load_job(job_id)


@app.get("/jobs/{job_id}/packet")
def get_packet(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    packet_path = _job_dir(job_id) / "output" / "packet.json"
    if not packet_path.exists():
        raise HTTPException(status_code=404, detail="Packet result not ready.")
    return _read_json(packet_path)


@app.get("/jobs/{job_id}/report", response_class=PlainTextResponse)
def get_report(job_id: str, request: Request) -> str:
    _require_demo_access(request)
    report_path = _job_dir(job_id) / "output" / "packet_report.md"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="Report not ready.")
    return report_path.read_text(encoding="utf-8")


@app.get("/usage")
def usage(request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    events = _usage_events()
    total_estimated = sum(
        float(event.get("estimated_cost_usd") or 0.0)
        for event in events
        if isinstance(event, dict)
    )
    return {
        "event_count": len(events),
        "estimated_cost_usd": round(total_estimated, 8),
        "last_events": events[-50:],
        "limits": {
            "max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
            "max_requests_per_day": MAX_REQUESTS_PER_DAY,
            "min_seconds_between_calls": MIN_SECONDS_BETWEEN_CALLS,
        },
    }


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    if DEMO_REQUIRE_TOKEN and (not ADMIN_TOKEN or token != ADMIN_TOKEN):
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
    usage_summary = usage(request)
    rows = []
    for job in jobs[:50]:
        job_id = html.escape(str(job.get("job_id", "")))
        status = html.escape(str(job.get("status", "")))
        message = html.escape(str(job.get("message", "")))
        rows.append(
            f"<tr><td>{job_id}</td><td>{status}</td><td>{message}</td>"
            f"<td><a href='/jobs/{job_id}?token={html.escape(token)}'>json</a></td>"
            f"<td><a href='/jobs/{job_id}/report?token={html.escape(token)}'>report</a></td></tr>"
        )
    return f"""
    <html>
      <body>
        <h1>Admin</h1>
        <p>Usage events: {usage_summary['event_count']}</p>
        <p>Estimated cost: ${usage_summary['estimated_cost_usd']:.6f}</p>
        <table border="1" cellpadding="6">
          <tr><th>Job</th><th>Status</th><th>Message</th><th>JSON</th><th>Report</th></tr>
          {''.join(rows)}
        </table>
      </body>
    </html>
    """
