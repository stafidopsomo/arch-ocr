"""Job and usage event logging."""

from __future__ import annotations

import json
import threading
from typing import Any

from api.storage import USAGE_LEDGER_PATH, _job_events_path, _now

event_lock = threading.Lock()
ledger_lock = threading.Lock()


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


def _append_usage_event(event: dict[str, Any]) -> None:
    with ledger_lock:
        with USAGE_LEDGER_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


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
