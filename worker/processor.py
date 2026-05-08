"""Background task logic: _process_job() and _process_model_benchmark()."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from api.events import _append_job_event, _append_usage_event
from api.storage import (
    _job_dir,
    _load_benchmark,
    _load_job,
    _now,
    _set_job_status,
    _write_benchmark,
)
from ocr.costs import _add_cost_totals, _empty_cost_totals, _finalize_cost_totals
from ocr.extraction import _run_validated_json_extraction
from ocr.packet import (
    _add_extraction_totals,
    _attach_packet_analysis,
    _build_packet_artifact,
    _empty_packet_totals,
)
from ocr.providers import _redact_secrets, _require_api_key
from ocr.render import _read_single_input_page
from ocr.reporting import _render_packet_report
from ocr.triage import _build_triage_artifact
from worker.queue import JobAborted, worker_lock, _raise_if_abort_requested, _throttle_before_provider_call

PROVIDER = os.getenv("CLOUD_PROVIDER", "gemini")

# These are resolved at import time from env — same pattern as original app.py


def _csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def _get_model() -> str:
    from ocr.providers import _default_model_for_provider
    return os.getenv("GEMINI_MODEL") or _default_model_for_provider(PROVIDER)


def _get_model_sequence() -> list[str]:
    model = _get_model()
    return [model, *[m for m in _csv_env("OCR_MODEL_FALLBACKS") if m != model]]


PREVIEW_DPI = int(os.getenv("OCR_PREVIEW_DPI", "36"))
RENDER_DPI = int(os.getenv("OCR_RENDER_DPI", "150"))
REQUEST_TIMEOUT = int(os.getenv("OCR_PROVIDER_TIMEOUT", "180"))
LANGUAGE_HINTS = os.getenv("GOOGLE_VISION_LANGUAGE_HINTS", "el,en")
MAX_PAGES_PER_PACKET = int(os.getenv("OCR_MAX_PAGES_PER_PACKET", "20"))
MAX_PROVIDER_RETRIES = int(os.getenv("OCR_PROVIDER_MAX_RETRIES", "2"))
PROVIDER_RETRY_BASE_SECONDS = float(os.getenv("OCR_PROVIDER_RETRY_BASE_SECONDS", "20"))
PROVIDER_RETRY_MAX_SECONDS = float(os.getenv("OCR_PROVIDER_RETRY_MAX_SECONDS", "120"))
BENCHMARK_MODELS_ENV = _csv_env("OCR_BENCHMARK_MODELS")
MAX_BENCHMARK_MODELS = int(os.getenv("OCR_BENCHMARK_MAX_MODELS", "7"))
MAX_BENCHMARK_PAGES = int(os.getenv("OCR_BENCHMARK_MAX_PAGES", "2"))


def _basename(value: Any) -> str:
    return Path(str(value or "")).name


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
    model: str | None = None,
) -> None:
    usage = usage or {}
    safe_error = _redact_secrets(error) if error else None
    event_model = model or _get_model()
    _append_usage_event(
        {
            "timestamp": _now(),
            "job_id": job_id,
            "page_id": page_id,
            "source_file": source_file,
            "page_number": page_number,
            "provider": PROVIDER,
            "model": event_model,
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
        model=event_model,
        attempt=attempt,
        max_attempts=max_attempts,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
        estimated_cost_usd=usage.get("estimated_cost_usd"),
        error=safe_error,
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


def _get_benchmark_models() -> list[str]:
    model = _get_model()
    default_benchmark_models = [
        model,
        "gemini-3-flash-preview",
        "gemma-3-4b-it",
        "gemma-3-12b-it",
        "gemma-3-27b-it",
        "gemma-4-26b-a4b-it",
        "gemma-4-31b-it",
    ]
    return list(dict.fromkeys(BENCHMARK_MODELS_ENV or default_benchmark_models))


def _safe_benchmark_models(models: Any) -> list[str]:
    benchmark_models = _get_benchmark_models()
    requested = models if isinstance(models, list) else []
    safe: list[str] = []
    allowed = set(benchmark_models)
    for model in requested:
        model_name = str(model or "").strip()
        if model_name in allowed and model_name not in safe:
            safe.append(model_name)
    if not safe:
        safe = benchmark_models[:]
    return safe[:MAX_BENCHMARK_MODELS]


def _count_artifact_fields(artifact: dict[str, Any]) -> int:
    total = 0
    extraction = artifact.get("extraction")
    if not isinstance(extraction, dict):
        return 0
    for page_result in extraction.get("page_results", []):
        if isinstance(page_result, dict) and isinstance(page_result.get("fields"), list):
            total += len(page_result["fields"])
    return total


def _first_page_result(artifact: dict[str, Any]) -> dict[str, Any]:
    extraction = artifact.get("extraction")
    if not isinstance(extraction, dict):
        return {}
    page_results = extraction.get("page_results")
    if not isinstance(page_results, list) or not page_results:
        return {}
    first = page_results[0]
    return first if isinstance(first, dict) else {}


def _benchmark_pages_for_job(
    job: dict[str, Any],
    *,
    requested_page_ids: Any = None,
    max_pages: int | None = None,
) -> tuple[dict[str, Any], list[tuple[Path, dict[str, Any]]]]:
    upload_dir = _job_dir(str(job["job_id"])) / "uploads"
    input_files = [
        Path(file_info["path"])
        for file_info in job.get("files", [])
        if isinstance(file_info, dict)
    ]
    triage_artifact = _build_triage_artifact(
        input_path=upload_dir,
        files=input_files,
        max_pages_per_file=None,
        preview_dpi=PREVIEW_DPI,
    )
    selected = _selected_pages_for_packet(triage_artifact)
    requested_ids = {str(value) for value in requested_page_ids} if isinstance(requested_page_ids, list) else set()
    if requested_ids:
        selected = [
            (source_file, page)
            for source_file, page in selected
            if str(page.get("page_id") or "") in requested_ids
        ]
    safe_max = max(1, min(int(max_pages or 1), MAX_BENCHMARK_PAGES))
    return triage_artifact, selected[:safe_max]


def _process_model_benchmark(job_id: str, benchmark_id: str) -> None:
    if not worker_lock.acquire(blocking=False):
        benchmark = _load_benchmark(job_id, benchmark_id)
        benchmark["status"] = "queued"
        benchmark["message"] = "Waiting for the active OCR job or benchmark to finish."
        benchmark["updated_at"] = _now()
        _write_benchmark(job_id, benchmark)
        _append_job_event(job_id, "benchmark_queued", benchmark_id=benchmark_id)
        with worker_lock:
            pass
        worker_lock.acquire()

    try:
        benchmark = _load_benchmark(job_id, benchmark_id)
        job = _load_job(job_id)
        api_key = _require_api_key(PROVIDER, None)
        benchmark["status"] = "running"
        benchmark["message"] = "Running model benchmark."
        benchmark["started_at"] = _now()
        benchmark["updated_at"] = _now()
        _write_benchmark(job_id, benchmark)
        _append_job_event(
            job_id,
            "benchmark_started",
            benchmark_id=benchmark_id,
            models=benchmark.get("models"),
            page_ids=benchmark.get("page_ids"),
        )
        _triage, pages = _benchmark_pages_for_job(
            job,
            requested_page_ids=benchmark.get("page_ids"),
            max_pages=int(benchmark.get("max_pages") or 1),
        )
        results: list[dict[str, Any]] = []
        total_calls = len(benchmark.get("models", [])) * len(pages)
        completed_calls = 0

        for source_file, page_triage in pages:
            page_number = int(page_triage.get("page_number", 1))
            page_id = str(page_triage.get("page_id", f"{source_file.stem}:p{page_number}"))
            rendered_page = _read_single_input_page(source_file, page_number, RENDER_DPI)
            for model in benchmark.get("models", []):
                model_name = str(model)
                started = time.perf_counter()
                usage: dict[str, Any] = {}
                result: dict[str, Any] = {
                    "model": model_name,
                    "page_id": page_id,
                    "source_file": str(source_file),
                    "page_number": page_number,
                    "status": "running",
                    "started_at": _now(),
                }
                results.append(result)
                benchmark["results"] = results
                benchmark["completed_calls"] = completed_calls
                benchmark["total_calls"] = total_calls
                benchmark["updated_at"] = _now()
                _write_benchmark(job_id, benchmark)
                _append_job_event(
                    job_id,
                    "benchmark_model_started",
                    benchmark_id=benchmark_id,
                    model=model_name,
                    page_id=page_id,
                )
                try:
                    _throttle_before_provider_call(
                        job_id,
                        f"benchmark:{benchmark_id}:{page_id}:{model_name}",
                        update_job_status=False,
                    )
                    artifact, _raw_response = _run_validated_json_extraction(
                        input_path=source_file,
                        provider=PROVIDER,
                        api_key=api_key,
                        model=model_name,
                        pages=[rendered_page],
                        page_triage=[page_triage],
                        language_hints=LANGUAGE_HINTS,
                        timeout=REQUEST_TIMEOUT,
                    )
                    usage = artifact.get("usage") if isinstance(artifact.get("usage"), dict) else {}
                    first_page_result = _first_page_result(artifact)
                    result.update(
                        {
                            "status": "success",
                            "duration_seconds": round(time.perf_counter() - started, 2),
                            "field_count": _count_artifact_fields(artifact),
                            "document_type": first_page_result.get("document_type"),
                            "summary": first_page_result.get("printed_text_summary"),
                            "usage": usage,
                        }
                    )
                    _record_provider_event(
                        job_id=job_id,
                        page_id=f"benchmark:{benchmark_id}:{page_id}",
                        source_file=str(source_file),
                        page_number=page_number,
                        status="success",
                        usage=usage,
                        attempt=1,
                        max_attempts=1,
                        model=model_name,
                    )
                except Exception as exc:
                    error_message = _redact_secrets(str(exc))
                    result.update(
                        {
                            "status": "failure",
                            "duration_seconds": round(time.perf_counter() - started, 2),
                            "error": error_message,
                        }
                    )
                    _record_provider_event(
                        job_id=job_id,
                        page_id=f"benchmark:{benchmark_id}:{page_id}",
                        source_file=str(source_file),
                        page_number=page_number,
                        status="failure",
                        error=error_message,
                        attempt=1,
                        max_attempts=1,
                        model=model_name,
                    )
                completed_calls += 1
                benchmark["results"] = results
                benchmark["completed_calls"] = completed_calls
                benchmark["total_calls"] = total_calls
                benchmark["updated_at"] = _now()
                _write_benchmark(job_id, benchmark)

        failures = sum(1 for result in results if result.get("status") == "failure")
        benchmark["status"] = "completed" if failures == 0 else "completed_with_errors"
        benchmark["message"] = "Benchmark completed."
        benchmark["completed_at"] = _now()
        benchmark["updated_at"] = _now()
        benchmark["failure_count"] = failures
        _write_benchmark(job_id, benchmark)
        _append_job_event(
            job_id,
            "benchmark_completed",
            benchmark_id=benchmark_id,
            status=benchmark["status"],
            completed_calls=completed_calls,
            failure_count=failures,
        )
    except Exception as exc:
        benchmark = _load_benchmark(job_id, benchmark_id)
        benchmark["status"] = "failed"
        benchmark["message"] = _redact_secrets(str(exc))
        benchmark["updated_at"] = _now()
        _write_benchmark(job_id, benchmark)
        _append_job_event(job_id, "benchmark_failed", benchmark_id=benchmark_id, error=benchmark["message"])
    finally:
        try:
            worker_lock.release()
        except RuntimeError:
            pass


def _process_job(job_id: str) -> None:
    MODEL = _get_model()
    MODEL_SEQUENCE = _get_model_sequence()

    if not worker_lock.acquire(blocking=False):
        _append_job_event(job_id, "job_queued", reason="another_job_running")
        _set_job_status(job_id, "queued", message="Another job is running. This job will wait.")
        with worker_lock:
            pass
        worker_lock.acquire()

    try:
        job = _load_job(job_id)
        _raise_if_abort_requested(job_id)
        _append_job_event(
            job_id,
            "job_started",
            provider=PROVIDER,
            primary_model=MODEL,
            model_fallbacks=MODEL_SEQUENCE[1:],
            model_sequence=MODEL_SEQUENCE,
        )
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

        api_key = _require_api_key(PROVIDER, None)
        triage_artifact = _build_triage_artifact(
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
        totals = _empty_packet_totals(triage_artifact, len(selected_pages))
        cost_totals = _empty_cost_totals(provider=PROVIDER, model=MODEL)
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

            max_attempts = max(MAX_PROVIDER_RETRIES + 1, len(MODEL_SEQUENCE))
            model_for_attempt = MODEL_SEQUENCE[0]
            try:
                rendered_page = _read_single_input_page(source_file, page_number, RENDER_DPI)
                artifact: dict[str, Any] | None = None
                for attempt in range(1, max_attempts + 1):
                    _raise_if_abort_requested(job_id, page_id)
                    _throttle_before_provider_call(job_id, page_id)
                    model_for_attempt = MODEL_SEQUENCE[min(attempt - 1, len(MODEL_SEQUENCE) - 1)]
                    try:
                        artifact, _raw_response = _run_validated_json_extraction(
                            input_path=source_file,
                            provider=PROVIDER,
                            api_key=api_key,
                            model=model_for_attempt,
                            pages=[rendered_page],
                            page_triage=[page_triage],
                            language_hints=LANGUAGE_HINTS,
                            timeout=REQUEST_TIMEOUT,
                        )
                        break
                    except Exception as exc:
                        error_message = _redact_secrets(str(exc))
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
                            model=model_for_attempt,
                        )
                        next_model = MODEL_SEQUENCE[min(attempt, len(MODEL_SEQUENCE) - 1)]
                        model_note = f" Next model: {next_model}." if next_model != model_for_attempt else ""
                        _set_job_status(
                            job_id,
                            "retrying",
                            message=(
                                f"Provider is busy for page {index} of {len(selected_pages)}. "
                                f"Retry {attempt} of {max_attempts - 1} in {round(delay, 1)}s."
                                f"{model_note}"
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
                _add_extraction_totals(totals, artifact)
                _add_cost_totals(cost_totals, artifact)
                _record_provider_event(
                    job_id=job_id,
                    page_id=page_id,
                    source_file=str(source_file),
                    page_number=page_number,
                    status="success",
                    usage=artifact.get("usage") if isinstance(artifact.get("usage"), dict) else {},
                    attempt=attempt,
                    max_attempts=max_attempts,
                    model=model_for_attempt,
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
                    model=model_for_attempt,
                )
            except JobAborted:
                raise
            except Exception as exc:
                error_message = _redact_secrets(str(exc))
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
                    attempt=max_attempts,
                    max_attempts=max_attempts,
                    model=model_for_attempt,
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
                    model=model_for_attempt,
                )

        _append_job_event(
            job_id,
            "analysis_started",
            successful_page_artifacts=len(extractions),
            page_errors=len(errors),
        )
        packet = _build_packet_artifact(
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
        packet.setdefault("provider_config", {})["model_sequence"] = MODEL_SEQUENCE
        packet.setdefault("provider_config", {})["model_fallbacks"] = MODEL_SEQUENCE[1:]
        packet["cost_summary"] = _finalize_cost_totals(cost_totals)
        _attach_packet_analysis(packet)
        packet_path = output_dir / "packet.json"
        report_path = output_dir / "packet_report.md"
        packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        report_path.write_text(_render_packet_report(packet), encoding="utf-8")

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
        error_message = _redact_secrets(str(exc))
        _append_job_event(job_id, "job_failed", error=error_message)
        _set_job_status(job_id, "failed", message=error_message)
    finally:
        try:
            worker_lock.release()
        except RuntimeError:
            pass
