"""Usage/cost dashboard endpoint."""

from __future__ import annotations

import html
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from api.auth import _require_demo_access
from api.events import _usage_events
from api.storage import (
    MAX_PROVIDER_RETRIES,
    MAX_REQUESTS_PER_DAY,
    MAX_REQUESTS_PER_MINUTE,
    MIN_SECONDS_BETWEEN_CALLS,
)


def _usage_summary() -> dict[str, Any]:
    from worker.processor import _get_model, _get_model_sequence
    model = _get_model()
    model_sequence = _get_model_sequence()
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
            "primary_model": model,
            "model_fallbacks": model_sequence[1:],
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
              <td class="mono">{html.escape(str(event.get("model") or ""))}</td>
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
          <p>Primary model: <code>{html.escape(str(summary['limits']['primary_model']))}</code> · Fallbacks: <code>{html.escape(', '.join(summary['limits']['model_fallbacks']) or 'none')}</code></p>
        </section>
        <h2>Recent provider events</h2>
        <table>
          <thead><tr><th>Time</th><th>Job</th><th>Page</th><th>Status</th><th>Model</th><th>Attempt</th><th>Tokens</th><th>Cost</th><th>Error</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </body>
    </html>
    """


def usage(request: Request):
    _require_demo_access(request)
    summary = _usage_summary()
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_usage_page(summary))
    return summary
