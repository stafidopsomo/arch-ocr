"""Thin FastAPI entry point. All logic lives in api/, worker/, and ocr/ packages."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

load_dotenv()

APP_NAME = "arch-ocr"

app = FastAPI(title=APP_NAME)

_web_dir = Path(__file__).parent / "web"
if _web_dir.is_dir():
    app.mount("/design", StaticFiles(directory=str(_web_dir), html=True), name="design")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@app.on_event("startup")
def startup() -> None:
    from api.storage import _ensure_storage
    _ensure_storage()


# ---------------------------------------------------------------------------
# Misc / session / auth
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    from api.pages import health as _health
    return _health()


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> RedirectResponse:
    from api.pages import index as _index
    return _index(request)


@app.get("/session")
def session(request: Request):
    from api.pages import session as _session
    return _session(request)


@app.get("/models")
def models(request: Request):
    from api.benchmarks import _model_catalog
    from api.auth import _require_demo_access
    _require_demo_access(request)
    return _model_catalog()


@app.post("/login")
async def login(request: Request) -> JSONResponse:
    from api.pages import login as _login
    return await _login(request)


@app.post("/logout")
def logout():
    from api.pages import logout as _logout
    return _logout()


# ---------------------------------------------------------------------------
# Usage / admin
# ---------------------------------------------------------------------------

@app.get("/usage", response_model=None)
def usage(request: Request):
    from api.usage import usage as _usage
    return _usage(request)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request) -> str:
    from api.pages import admin as _admin
    return _admin(request)


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.post("/jobs")
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    token: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> JSONResponse:
    from api.jobs import create_job as _create_job
    return await _create_job(request, background_tasks, files=files, token=token, title=title)


@app.post("/jobs/draft")
async def create_draft_job(
    request: Request,
    files: list[UploadFile] = File(...),
    token: str | None = Form(default=None),
    title: str | None = Form(default=None),
) -> JSONResponse:
    from api.jobs import create_draft_job as _create_draft_job
    return await _create_draft_job(request, files=files, token=token, title=title)


@app.post("/jobs/{job_id}/start")
async def start_uploaded_job(job_id: str, request: Request, background_tasks: BackgroundTasks):
    from api.jobs import start_uploaded_job as _start_uploaded_job
    return await _start_uploaded_job(job_id, request, background_tasks)


@app.get("/jobs")
def list_jobs(request: Request):
    from api.jobs import list_jobs as _list_jobs
    return _list_jobs(request)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    from api.jobs import get_job as _get_job
    return _get_job(job_id, request)


@app.get("/jobs/{job_id}/view", response_class=HTMLResponse)
def view_job(job_id: str, request: Request) -> str:
    from api.pages import view_job as _view_job
    return _view_job(job_id, request)


@app.get("/jobs/{job_id}/events")
def get_job_events(job_id: str, request: Request, limit: int = 200):
    from api.jobs import get_job_events as _get_job_events
    return _get_job_events(job_id, request, limit)


@app.get("/jobs/{job_id}/logs", response_class=HTMLResponse)
def view_job_logs(job_id: str, request: Request) -> str:
    from api.pages import view_job_logs as _view_job_logs
    return _view_job_logs(job_id, request)


@app.post("/jobs/{job_id}/abort")
def abort_job(job_id: str, request: Request):
    from api.jobs import abort_job as _abort_job
    return _abort_job(job_id, request)


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request):
    from api.jobs import delete_job as _delete_job
    return _delete_job(job_id, request)


@app.get("/jobs/{job_id}/packet")
def get_packet(job_id: str, request: Request):
    from api.jobs import get_packet as _get_packet
    return _get_packet(job_id, request)


@app.get("/jobs/{job_id}/report", response_class=PlainTextResponse)
def get_report(job_id: str, request: Request) -> str:
    from api.review import get_report as _get_report
    return _get_report(job_id, request)


@app.get("/jobs/{job_id}/review", response_class=HTMLResponse)
def get_review(job_id: str, request: Request) -> str:
    from api.review import get_review as _get_review
    return _get_review(job_id, request)


@app.get("/jobs/{job_id}/page-thumbnail")
def get_page_thumbnail(job_id: str, request: Request, field_ref: str) -> Response:
    from api.review import get_page_thumbnail as _get_page_thumbnail
    return _get_page_thumbnail(job_id, request, field_ref)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

@app.get("/jobs/{job_id}/benchmarks", response_class=HTMLResponse)
def view_benchmarks(job_id: str, request: Request) -> str:
    from api.benchmarks import view_benchmarks as _view_benchmarks
    return _view_benchmarks(job_id, request)


@app.post("/jobs/{job_id}/benchmarks")
async def create_benchmark(job_id: str, request: Request, background_tasks: BackgroundTasks):
    from api.benchmarks import create_benchmark as _create_benchmark
    return await _create_benchmark(job_id, request, background_tasks)


@app.get("/jobs/{job_id}/benchmarks/{benchmark_id}.json")
def get_benchmark_json(job_id: str, benchmark_id: str, request: Request):
    from api.benchmarks import get_benchmark_json as _get_benchmark_json
    return _get_benchmark_json(job_id, benchmark_id, request)


@app.get("/jobs/{job_id}/benchmarks/{benchmark_id}", response_class=HTMLResponse)
def view_benchmark(job_id: str, benchmark_id: str, request: Request) -> str:
    from api.benchmarks import view_benchmark as _view_benchmark
    return _view_benchmark(job_id, benchmark_id, request)
