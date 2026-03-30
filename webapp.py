from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ocr_script import process_pdfs

APP_ROOT = Path(__file__).parent.resolve()
UPLOAD_ROOT = APP_ROOT / "uploads"
OUTPUT_ROOT = APP_ROOT / "output"
JOBS_ROOT = APP_ROOT / "jobs"
TEMPLATES_ROOT = APP_ROOT / "templates"

for p in (UPLOAD_ROOT, OUTPUT_ROOT, JOBS_ROOT, TEMPLATES_ROOT):
    p.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="arch-ocr local webapp")
templates = Jinja2Templates(directory=str(TEMPLATES_ROOT))

DEFAULT_ENGINE = os.getenv("ARCH_OCR_ENGINE", "ocr")
DEFAULT_VLM_MODEL = os.getenv("ARCH_OCR_VLM_MODEL", "qwen2.5-vl:7b")
DEFAULT_GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")


class JobStore:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def write(self, payload: dict):
        with self.lock:
            self._path(payload["id"]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def read(self, job_id: str) -> dict | None:
        path = self._path(job_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list(self) -> list[dict]:
        jobs = []
        for path in sorted(self.root.glob("*.json"), reverse=True):
            jobs.append(json.loads(path.read_text(encoding="utf-8")))
        return jobs


store = JobStore(JOBS_ROOT)


def now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def run_job(job_id: str, job_input_dir: Path, output_path: Path):
    job = store.read(job_id)
    if not job:
        return

    logs: list[str] = []

    def log(msg: str):
        logs.append(msg)
        current = store.read(job_id) or job
        current["logs"] = logs[-200:]
        current["updated_at"] = now_iso()
        store.write(current)

    try:
        current = store.read(job_id) or job
        current["status"] = "running"
        current["updated_at"] = now_iso()
        store.write(current)

        result = process_pdfs(
            inputs=[str(job_input_dir)],
            output=str(output_path),
            engine=DEFAULT_ENGINE,
            vlm_model=DEFAULT_VLM_MODEL,
            google_api_key=DEFAULT_GOOGLE_API_KEY,
            progress_cb=log,
        )

        current = store.read(job_id) or job
        current["status"] = "done"
        current["output_path"] = result["output_path"]
        current["metrics"] = {
            "input_count": result["input_count"],
            "converted_count": result["converted_count"],
            "page_count": result["page_count"],
            "engine_used": result.get("engine_used", DEFAULT_ENGINE),
            "vlm_model": result.get("vlm_model"),
            "vision_mode": result.get("vision_mode", False),
        }
        current["updated_at"] = now_iso()
        store.write(current)
    except Exception as exc:
        current = store.read(job_id) or job
        current["status"] = "error"
        current["error"] = str(exc)
        current["updated_at"] = now_iso()
        store.write(current)


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "jobs": store.list(),
        },
    )


@app.post("/jobs")
async def create_job(
    request: Request,
    files: list[UploadFile] = File(...),
    client_name: str = Form(default="tester"),
):
    pdf_files = [f for f in files if f.filename and f.filename.lower().endswith(".pdf")]
    if not pdf_files:
        raise HTTPException(status_code=400, detail="Upload at least one PDF file.")

    job_id = uuid.uuid4().hex[:12]
    job_dir = UPLOAD_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    saved_names = []
    for f in pdf_files:
        target = job_dir / Path(f.filename).name
        target.write_bytes(await f.read())
        saved_names.append(target.name)

    output_path = OUTPUT_ROOT / f"{job_id}_result.pdf"
    payload = {
        "id": job_id,
        "status": "queued",
        "client_name": client_name,
        "files": saved_names,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "output_path": None,
        "metrics": {},
        "engine": DEFAULT_ENGINE,
        "vlm_model": DEFAULT_VLM_MODEL,
        "vision_mode": DEFAULT_ENGINE in {"vision", "hybrid_vision", "hybrid_all"},
        "logs": [],
        "error": None,
    }
    store.write(payload)

    thread = threading.Thread(
        target=run_job,
        args=(job_id, job_dir, output_path),
        daemon=True,
    )
    thread.start()

    return RedirectResponse(url="/", status_code=303)


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = store.read(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/jobs/{job_id}/download")
def download_job(job_id: str):
    job = store.read(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done" or not job.get("output_path"):
        raise HTTPException(status_code=409, detail="Job is not completed yet")

    path = Path(job["output_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Output file missing")

    return FileResponse(path, filename=path.name, media_type="application/pdf")
