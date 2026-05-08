"""File-backed job and benchmark storage helpers."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

STORAGE_DIR = Path(os.getenv("OCR_STORAGE_DIR", "storage")).expanduser().resolve()
JOBS_DIR = STORAGE_DIR / "jobs"
USAGE_LEDGER_PATH = STORAGE_DIR / "usage_ledger.jsonl"

MAX_FILES_PER_PACKET = int(os.getenv("OCR_MAX_FILES_PER_PACKET", "10"))
MAX_PAGES_PER_PACKET = int(os.getenv("OCR_MAX_PAGES_PER_PACKET", "20"))
MAX_UPLOAD_MB = int(os.getenv("OCR_MAX_UPLOAD_MB", "100"))
MIN_SECONDS_BETWEEN_CALLS = float(os.getenv("OCR_PROVIDER_MIN_SECONDS_BETWEEN_CALLS", "4"))
MAX_REQUESTS_PER_MINUTE = int(os.getenv("OCR_PROVIDER_MAX_REQUESTS_PER_MINUTE", "10"))
MAX_REQUESTS_PER_DAY = int(os.getenv("OCR_PROVIDER_MAX_REQUESTS_PER_DAY", "100"))
MAX_PROVIDER_RETRIES = int(os.getenv("OCR_PROVIDER_MAX_RETRIES", "2"))
PROVIDER_RETRY_BASE_SECONDS = float(os.getenv("OCR_PROVIDER_RETRY_BASE_SECONDS", "20"))
PROVIDER_RETRY_MAX_SECONDS = float(os.getenv("OCR_PROVIDER_RETRY_MAX_SECONDS", "120"))


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


def _job_events_path(job_id: str) -> Path:
    return _job_dir(job_id) / "events.jsonl"


def _benchmark_dir(job_id: str) -> Path:
    return _job_dir(job_id) / "benchmarks"


def _benchmark_path(job_id: str, benchmark_id: str) -> Path:
    return _benchmark_dir(job_id) / f"{benchmark_id}.json"


def _packet_path(job_id: str) -> Path:
    return _job_dir(job_id) / "output" / "packet.json"


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


def _load_analyzed_packet(job_id: str) -> dict[str, Any]:
    from ocr.packet import _attach_packet_analysis
    packet_path = _packet_path(job_id)
    if not packet_path.exists():
        raise HTTPException(status_code=404, detail="Packet result not ready.")
    packet = _read_json(packet_path)
    _attach_packet_analysis(packet)
    return packet


def _save_job(job: dict[str, Any]) -> None:
    _write_json(_job_path(str(job["job_id"])), job)


def _set_job_status(job_id: str, status: str, **updates: Any) -> None:
    job = _load_job(job_id)
    job.update(updates)
    job["status"] = status
    job["updated_at"] = _now()
    _save_job(job)


def _write_benchmark(job_id: str, benchmark: dict[str, Any]) -> None:
    _write_json(_benchmark_path(job_id, str(benchmark["benchmark_id"])), benchmark)


def _load_benchmark(job_id: str, benchmark_id: str) -> dict[str, Any]:
    path = _benchmark_path(job_id, benchmark_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Benchmark not found.")
    return _read_json(path)


def _list_benchmarks(job_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in sorted(_benchmark_dir(job_id).glob("*.json"), reverse=True):
        try:
            items.append(_read_json(path))
        except Exception:
            continue
    return items
