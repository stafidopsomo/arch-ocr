"""Rate limiting, throttling, and job queue state."""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

MIN_SECONDS_BETWEEN_CALLS = float(os.getenv("OCR_PROVIDER_MIN_SECONDS_BETWEEN_CALLS", "4"))
MAX_REQUESTS_PER_MINUTE = int(os.getenv("OCR_PROVIDER_MAX_REQUESTS_PER_MINUTE", "10"))
MAX_REQUESTS_PER_DAY = int(os.getenv("OCR_PROVIDER_MAX_REQUESTS_PER_DAY", "100"))

worker_lock = threading.Lock()


class JobAborted(RuntimeError):
    pass


def _seconds_since(timestamp: str) -> float | None:
    try:
        event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - event_time).total_seconds()


def _is_abort_requested(job_id: str) -> bool:
    try:
        from api.storage import _load_job
        return str(_load_job(job_id).get("status")) == "abort_requested"
    except Exception:
        return False


def _raise_if_abort_requested(job_id: str, page_id: str | None = None) -> None:
    from api.events import _append_job_event
    if not _is_abort_requested(job_id):
        return
    _append_job_event(job_id, "job_abort_observed", page_id=page_id)
    raise JobAborted("Job aborted by user.")


def _throttle_before_provider_call(job_id: str, page_id: str, *, update_job_status: bool = True) -> None:
    from api.events import _append_job_event, _usage_events
    from api.storage import _set_job_status

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
            if update_job_status:
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
            if update_job_status:
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
            if update_job_status:
                _set_job_status(
                    job_id,
                    "throttled",
                    message=f"Waiting {round(last_wait, 1)}s before next provider call.",
                    current_page_id=page_id,
                )
            time.sleep(last_wait)
            _raise_if_abort_requested(job_id, page_id)
        return
