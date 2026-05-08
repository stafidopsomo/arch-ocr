"""Admin benchmark endpoints and HTML page generators."""

from __future__ import annotations

import html
import os
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException, Request

from api.auth import _require_admin_access
from api.events import _append_job_event
from api.storage import (
    _list_benchmarks,
    _load_benchmark,
    _load_job,
    _now,
    _write_benchmark,
)


def _csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def _get_benchmark_config() -> dict[str, Any]:
    from worker.processor import PROVIDER, _get_model, _get_model_sequence
    model = _get_model()
    model_sequence = _get_model_sequence()
    default_benchmark_models = [
        model,
        "gemini-3-flash-preview",
        "gemma-3-4b-it",
        "gemma-3-12b-it",
        "gemma-3-27b-it",
        "gemma-4-26b-a4b-it",
        "gemma-4-31b-it",
    ]
    benchmark_models = list(
        dict.fromkeys(_csv_env("OCR_BENCHMARK_MODELS") or default_benchmark_models)
    )
    max_benchmark_models = int(os.getenv("OCR_BENCHMARK_MAX_MODELS", "7"))
    max_benchmark_pages = int(os.getenv("OCR_BENCHMARK_MAX_PAGES", "2"))
    return {
        "provider": PROVIDER,
        "model": model,
        "model_sequence": model_sequence,
        "benchmark_models": benchmark_models,
        "max_benchmark_models": max_benchmark_models,
        "max_benchmark_pages": max_benchmark_pages,
    }


def _model_catalog() -> dict[str, Any]:
    from api.storage import MIN_SECONDS_BETWEEN_CALLS
    cfg = _get_benchmark_config()
    return {
        "provider": cfg["provider"],
        "primary_model": cfg["model"],
        "fallback_models": cfg["model_sequence"][1:],
        "benchmark_models": cfg["benchmark_models"],
        "limits": {
            "max_benchmark_models": cfg["max_benchmark_models"],
            "max_benchmark_pages": cfg["max_benchmark_pages"],
            "min_seconds_between_calls": MIN_SECONDS_BETWEEN_CALLS,
        },
        "notes": [
            "Normal jobs continue to use GEMINI_MODEL and OCR_MODEL_FALLBACKS.",
            "Benchmarks call each selected model directly once per selected page.",
            "Live API models are not included because this batch OCR flow uses standard generateContent calls.",
        ],
    }


def _safe_benchmark_models(models: Any) -> list[str]:
    cfg = _get_benchmark_config()
    benchmark_models = cfg["benchmark_models"]
    max_benchmark_models = cfg["max_benchmark_models"]
    allowed = set(benchmark_models)
    if isinstance(models, list):
        safe = [str(m) for m in models if str(m) in allowed]
    else:
        safe = benchmark_models[:]
    return safe[:max_benchmark_models]


def _benchmarks_page(job_id: str, benchmarks: list[dict[str, Any]], token: str = "") -> str:
    cfg = _get_benchmark_config()
    benchmark_models = cfg["benchmark_models"]
    max_benchmark_pages = cfg["max_benchmark_pages"]
    token_query = f"?{html.escape(urlencode({'token': token}))}" if token else ""
    model_checks = "".join(
        f"""
        <label>
          <input type="checkbox" name="models" value="{html.escape(model)}" checked>
          <code>{html.escape(model)}</code>
        </label>
        """
        for model in benchmark_models
    )
    rows = []
    for benchmark in benchmarks:
        benchmark_id = html.escape(str(benchmark.get("benchmark_id") or ""))
        status = html.escape(str(benchmark.get("status") or "unknown"))
        completed = html.escape(str(benchmark.get("completed_calls") or 0))
        total = html.escape(str(benchmark.get("total_calls") or 0))
        models = html.escape(", ".join(str(model) for model in benchmark.get("models", [])))
        rows.append(
            f"""
            <tr>
              <td class="mono">{benchmark_id}</td>
              <td>{status}</td>
              <td class="mono">{completed} / {total}</td>
              <td>{models}</td>
              <td><a href="/jobs/{html.escape(job_id)}/benchmarks/{benchmark_id}{token_query}">open</a></td>
            </tr>
            """
        )
    return f"""
    <html>
      <head>
        <title>arch-ocr model benchmarks</title>
        <style>
          body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin:24px; background:#fafaf7; color:#14181f; }}
          a {{ color:#1a2540; }}
          .card {{ background:white; border:1px solid #e7e3d8; border-radius:8px; padding:16px; margin-bottom:18px; }}
          label {{ display:block; margin:8px 0; }}
          table {{ border-collapse:collapse; width:100%; background:white; }}
          th,td {{ border:1px solid #e7e3d8; padding:8px; text-align:left; vertical-align:top; }}
          th {{ background:#f6f4ee; }}
          button {{ height:34px; padding:0 12px; border:1px solid #1a2540; border-radius:6px; background:#1a2540; color:white; font-weight:700; }}
          input[type=number] {{ width:70px; }}
          .mono, code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
          .muted {{ color:#68707c; }}
        </style>
      </head>
      <body>
        <h1>Model Benchmarks</h1>
        <p><a href="/design/arch-ocr.html?screen=job&job={html.escape(job_id)}">Back to job</a> · <a href="/models{token_query}">model catalog JSON</a></p>
        <section class="card">
          <h2>Start benchmark</h2>
          <p class="muted">Default is one page. Each checked model costs one provider call per page.</p>
          <form id="benchmark-form">
            <p>
              <label>Max pages
                <input type="number" name="max_pages" value="1" min="1" max="{max_benchmark_pages}">
              </label>
            </p>
            {model_checks}
            <button type="submit">Start benchmark</button>
          </form>
        </section>
        <section class="card">
          <h2>Runs</h2>
          <table>
            <thead><tr><th>Benchmark</th><th>Status</th><th>Progress</th><th>Models</th><th>Result</th></tr></thead>
            <tbody>{''.join(rows) or "<tr><td colspan='5'>No benchmarks yet.</td></tr>"}</tbody>
          </table>
        </section>
        <script>
          const form = document.getElementById("benchmark-form");
          form.addEventListener("submit", async (event) => {{
            event.preventDefault();
            const data = new FormData(form);
            const models = data.getAll("models");
            const maxPages = Number(data.get("max_pages") || 1);
            const res = await fetch("/jobs/{job_id}/benchmarks{token_query}", {{
              method: "POST",
              headers: {{ "content-type": "application/json", "accept": "application/json" }},
              credentials: "same-origin",
              body: JSON.stringify({{ models, max_pages: maxPages }})
            }});
            const body = await res.json().catch(() => ({{}}));
            if (!res.ok) {{
              alert(body.detail || `Benchmark failed (${{res.status}})`);
              return;
            }}
            location.href = `/jobs/{job_id}/benchmarks/${{body.benchmark_id}}{token_query}`;
          }});
        </script>
      </body>
    </html>
    """


def _benchmark_result_page(job_id: str, benchmark: dict[str, Any], token: str = "") -> str:
    token_query = f"?{html.escape(urlencode({'token': token}))}" if token else ""
    rows = []
    for result in benchmark.get("results", []):
        if not isinstance(result, dict):
            continue
        error = str(result.get("error") or "")
        if len(error) > 260:
            error = error[:260] + "..."
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        rows.append(
            f"""
            <tr>
              <td class="mono">{html.escape(str(result.get("model") or ""))}</td>
              <td>{html.escape(str(result.get("page_id") or ""))}</td>
              <td>{html.escape(str(result.get("status") or ""))}</td>
              <td class="mono">{html.escape(str(result.get("duration_seconds") or ""))}</td>
              <td class="mono">{html.escape(str(result.get("field_count") or ""))}</td>
              <td class="mono">{html.escape(str(usage.get("total_tokens") or ""))}</td>
              <td>{html.escape(error)}</td>
            </tr>
            """
        )
    return f"""
    <html>
      <head>
        <title>arch-ocr benchmark {html.escape(str(benchmark.get("benchmark_id") or ""))}</title>
        <meta http-equiv="refresh" content="8">
        <style>
          body {{ font-family: ui-sans-serif, system-ui, sans-serif; margin:24px; background:#fafaf7; color:#14181f; }}
          a {{ color:#1a2540; }}
          table {{ border-collapse:collapse; width:100%; background:white; }}
          th,td {{ border:1px solid #e7e3d8; padding:8px; text-align:left; vertical-align:top; }}
          th {{ background:#f6f4ee; }}
          .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:12px; }}
        </style>
      </head>
      <body>
        <h1>Benchmark {html.escape(str(benchmark.get("benchmark_id") or ""))}</h1>
        <p><a href="/jobs/{html.escape(job_id)}/benchmarks{token_query}">Back to benchmarks</a> · <a href="/jobs/{html.escape(job_id)}/benchmarks/{html.escape(str(benchmark.get("benchmark_id") or ""))}.json{token_query}">JSON</a></p>
        <p><strong>Status:</strong> {html.escape(str(benchmark.get("status") or ""))} · <strong>Progress:</strong> {html.escape(str(benchmark.get("completed_calls") or 0))} / {html.escape(str(benchmark.get("total_calls") or 0))}</p>
        <table>
          <thead><tr><th>Model</th><th>Page</th><th>Status</th><th>Seconds</th><th>Fields</th><th>Tokens</th><th>Error</th></tr></thead>
          <tbody>{''.join(rows) or "<tr><td colspan='7'>No results yet.</td></tr>"}</tbody>
        </table>
      </body>
    </html>
    """


def view_benchmarks(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    _require_admin_access(request)
    _load_job(job_id)
    return _benchmarks_page(job_id, _list_benchmarks(job_id), token)


async def create_benchmark(job_id: str, request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    from worker.processor import _benchmark_pages_for_job, _process_model_benchmark
    _require_admin_access(request)
    job = _load_job(job_id)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    models = _safe_benchmark_models(payload.get("models"))
    cfg = _get_benchmark_config()
    max_benchmark_pages = cfg["max_benchmark_pages"]
    max_pages = max(1, min(int(payload.get("max_pages") or 1), max_benchmark_pages))
    _triage, pages = _benchmark_pages_for_job(
        job,
        requested_page_ids=payload.get("page_ids"),
        max_pages=max_pages,
    )
    if not pages:
        raise HTTPException(status_code=400, detail="No benchmark pages selected.")
    from worker.processor import PROVIDER
    benchmark_id = str(uuid4())
    benchmark = {
        "benchmark_id": benchmark_id,
        "job_id": job_id,
        "status": "queued",
        "message": "Benchmark queued.",
        "created_at": _now(),
        "updated_at": _now(),
        "provider": PROVIDER,
        "models": models,
        "page_ids": [str(page.get("page_id") or "") for _source, page in pages],
        "max_pages": max_pages,
        "total_calls": len(models) * len(pages),
        "completed_calls": 0,
        "results": [],
    }
    _write_benchmark(job_id, benchmark)
    _append_job_event(
        job_id,
        "benchmark_created",
        benchmark_id=benchmark_id,
        models=models,
        page_ids=benchmark["page_ids"],
        total_calls=benchmark["total_calls"],
    )
    background_tasks.add_task(_process_model_benchmark, job_id, benchmark_id)
    return benchmark


def get_benchmark_json(job_id: str, benchmark_id: str, request: Request) -> dict[str, Any]:
    _require_admin_access(request)
    _load_job(job_id)
    return _load_benchmark(job_id, benchmark_id)


def view_benchmark(job_id: str, benchmark_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    _require_admin_access(request)
    _load_job(job_id)
    return _benchmark_result_page(job_id, _load_benchmark(job_id, benchmark_id), token)
