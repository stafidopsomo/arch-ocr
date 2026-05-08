"""Job CRUD endpoints."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import BackgroundTasks, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse

from api.auth import _require_demo_access, _current_user
from api.events import _append_job_event, _job_events
from api.storage import (
    JOBS_DIR,
    MAX_FILES_PER_PACKET,
    MAX_PAGES_PER_PACKET,
    MAX_UPLOAD_MB,
    MIN_SECONDS_BETWEEN_CALLS,
    MAX_REQUESTS_PER_MINUTE,
    MAX_REQUESTS_PER_DAY,
    MAX_PROVIDER_RETRIES,
    PROVIDER_RETRY_BASE_SECONDS,
    PROVIDER_RETRY_MAX_SECONDS,
    _job_dir,
    _load_job,
    _now,
    _read_json,
    _safe_filename,
    _save_job,
    _set_job_status,
)
from ocr.render import SUPPORTED_IMAGE_EXTENSIONS

ALLOWED_EXTENSIONS = {".pdf", *SUPPORTED_IMAGE_EXTENSIONS}


def _job_view_url(job_id: str, token: str = "") -> str:
    suffix = f"?token={token}" if token else ""
    return f"/jobs/{job_id}/view{suffix}"


def _get_provider_config() -> dict[str, Any]:
    from worker.processor import PROVIDER, _get_model, _get_model_sequence
    model = _get_model()
    model_sequence = _get_model_sequence()
    return {
        "provider": PROVIDER,
        "model": model,
        "model_fallbacks": model_sequence[1:],
    }


def _job_limits() -> dict[str, Any]:
    cfg = _get_provider_config()
    return {
        "max_files_per_packet": MAX_FILES_PER_PACKET,
        "max_pages_per_packet": MAX_PAGES_PER_PACKET,
        "max_upload_mb": MAX_UPLOAD_MB,
        "provider_min_seconds_between_calls": MIN_SECONDS_BETWEEN_CALLS,
        "provider_max_requests_per_minute": MAX_REQUESTS_PER_MINUTE,
        "provider_max_requests_per_day": MAX_REQUESTS_PER_DAY,
        "provider_max_retries": MAX_PROVIDER_RETRIES,
        "provider_retry_base_seconds": PROVIDER_RETRY_BASE_SECONDS,
        "provider_retry_max_seconds": PROVIDER_RETRY_MAX_SECONDS,
        **cfg,
    }


async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    token: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> JSONResponse:
    from api.storage import _ensure_storage
    from worker.processor import _process_job
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
        "limits": _job_limits(),
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


async def create_draft_job(
    request: Request,
    files: list[UploadFile] = File(...),
    token: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> JSONResponse:
    from api.storage import _ensure_storage
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
        "limits": _job_limits(),
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


async def start_uploaded_job(job_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    from worker.processor import _process_job
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


def list_jobs(request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    jobs = []
    for path in sorted(JOBS_DIR.glob("*/job.json"), reverse=True):
        try:
            jobs.append(_read_json(path))
        except Exception:
            continue
    return {"jobs": jobs[:100]}


def get_job(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    return _load_job(job_id)


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


def abort_job(job_id: str, request: Request) -> dict[str, Any]:
    _require_demo_access(request)
    job = _load_job(job_id)
    if str(job.get("status")) in {"completed", "completed_with_errors", "failed", "aborted"}:
        return job
    _append_job_event(job_id, "abort_requested", requested_by=(_current_user(request) or {}).get("username"))
    _set_job_status(job_id, "abort_requested", message="Abort requested. The job will stop before the next provider call.")
    return _load_job(job_id)


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


def get_packet(job_id: str, request: Request) -> dict[str, Any]:
    from api.storage import _load_analyzed_packet
    _require_demo_access(request)
    return _load_analyzed_packet(job_id)
