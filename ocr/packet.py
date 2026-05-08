"""Packet assembly, combining page artifacts, and deterministic identifier injection."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import fitz  # PyMuPDF

from ocr.clustering import (
    _attach_packet_clusters,
    _iter_packet_fields,
    _normalize_cluster_value,
)
from ocr.costs import (
    _add_cost_totals,
    _as_int,
    _empty_cost_totals,
    _finalize_cost_totals,
    _recalculate_packet_costs,
)
from ocr.extraction import _summarize_extraction
from ocr.validation import _attach_packet_checks


def _add_extraction_totals(totals: dict[str, int], artifact: dict[str, Any]) -> None:
    summary = artifact.get("extraction_summary", {})
    totals["pages_extracted"] += int(summary.get("page_count", 0))
    totals["field_count"] += int(summary.get("field_count", 0))
    totals["low_confidence_field_count"] += int(summary.get("low_confidence_field_count", 0))
    totals["handwritten_field_count"] += int(summary.get("handwritten_field_count", 0))
    totals["stamped_field_count"] += int(summary.get("stamped_field_count", 0))
    totals["signature_field_count"] += int(summary.get("signature_field_count", 0))


def _empty_packet_totals(triage_artifact: dict[str, Any], selected_page_count: int) -> dict[str, int]:
    triage_totals = triage_artifact.get("totals", {})
    return {
        "files": int(triage_totals.get("files", 0)),
        "pages_triaged": int(triage_totals.get("pages", 0)),
        "pages_selected": selected_page_count,
        "pages_extracted": 0,
        "pages_failed": 0,
        "field_count": 0,
        "low_confidence_field_count": 0,
        "handwritten_field_count": 0,
        "stamped_field_count": 0,
        "signature_field_count": 0,
    }


def _same_source_file(left: Any, right: Any) -> bool:
    try:
        return Path(str(left)).expanduser().resolve() == Path(str(right)).expanduser().resolve()
    except Exception:
        return str(left) == str(right)


def _find_packet_page_triage(
    packet: dict[str, Any],
    source_file: Any,
    page_number: int,
) -> dict[str, Any] | None:
    triage = packet.get("triage", {})
    if not isinstance(triage, dict):
        return None

    for document in triage.get("documents", []):
        if not isinstance(document, dict):
            continue
        if not _same_source_file(document.get("source_file"), source_file):
            continue
        for page in document.get("pages", []):
            if isinstance(page, dict) and page.get("page_number") == page_number:
                return page
    return None


def _packet_artifact_has_page(
    artifact: dict[str, Any],
    source_file: Any,
    page_number: int,
) -> bool:
    if not _same_source_file(artifact.get("source_file"), source_file):
        return False
    return page_number in {
        int(page_number)
        for page_number in artifact.get("pages_sent", [])
        if isinstance(page_number, int)
    }


def _remove_packet_page_artifact(
    packet: dict[str, Any],
    source_file: Any,
    page_number: int,
) -> None:
    extractions = packet.get("page_extractions", [])
    if not isinstance(extractions, list):
        packet["page_extractions"] = []
        return

    packet["page_extractions"] = [
        artifact
        for artifact in extractions
        if not (
            isinstance(artifact, dict)
            and _packet_artifact_has_page(artifact, source_file, page_number)
        )
    ]


def _recalculate_packet_totals(packet: dict[str, Any]) -> None:
    triage = packet.get("triage", {})
    triage_totals = triage.get("totals", {}) if isinstance(triage, dict) else {}
    previous_totals = packet.get("totals", {})
    if not isinstance(previous_totals, dict):
        previous_totals = {}

    errors = [error for error in packet.get("errors", []) if isinstance(error, dict)]
    totals = {
        "files": int(triage_totals.get("files", previous_totals.get("files", 0))),
        "pages_triaged": int(
            triage_totals.get("pages", previous_totals.get("pages_triaged", 0))
        ),
        "pages_selected": int(
            previous_totals.get(
                "pages_selected",
                previous_totals.get("pages_extracted", 0) + len(errors),
            )
        ),
        "pages_extracted": 0,
        "pages_failed": len(errors),
        "field_count": 0,
        "low_confidence_field_count": 0,
        "handwritten_field_count": 0,
        "stamped_field_count": 0,
        "signature_field_count": 0,
    }
    provider_config = packet.get("provider_config", {})
    if not isinstance(provider_config, dict):
        provider_config = {}
    cost_totals = _empty_cost_totals(
        provider=str(provider_config.get("provider") or "") or None,
        model=str(provider_config.get("model") or "") or None,
    )

    extractions = packet.get("page_extractions", [])
    if isinstance(extractions, list):
        for artifact in extractions:
            if isinstance(artifact, dict):
                _add_extraction_totals(totals, artifact)
                _add_cost_totals(cost_totals, artifact)

    packet["totals"] = totals
    packet["cost_summary"] = _finalize_cost_totals(cost_totals)


def _snippet_around_match(text: str, start: int, end: int, radius: int = 80) -> str:
    import re
    left = max(start - radius, 0)
    right = min(end + radius, len(text))
    return re.sub(r"\s+", " ", text[left:right]).strip()


def _deterministic_identifier_candidates(text: str) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    patterns = [
        (
            "afm",
            "ΑΦΜ",
            re.compile(r"(?:Α\.?\s*Φ\.?\s*Μ\.?|AFM)\D{0,24}(\d{9})", re.IGNORECASE),
        ),
        (
            "atak",
            "ΑΤΑΚ",
            re.compile(r"(?:Α\.?\s*Τ\.?\s*Α\.?\s*Κ\.?|ATAK)\D{0,24}(\d{8,15})", re.IGNORECASE),
        ),
        (
            "kaek",
            "ΚΑΕΚ",
            re.compile(
                r"(?:ΚΑΕΚ|KAEK)\D{0,80}((?:\d[\s./-]*){12,}(?:/\s*\d+\s*/\s*\d+)?)",
                re.IGNORECASE,
            ),
        ),
    ]
    seen: set[tuple[str, str]] = set()
    for subtype, label, pattern in patterns:
        for match in pattern.finditer(text):
            raw_value = match.group(1)
            if subtype == "afm":
                value = re.sub(r"\D+", "", raw_value)
            elif subtype == "atak":
                value = re.sub(r"\D+", "", raw_value)
            else:
                value = re.sub(r"\s+", " ", raw_value).strip(" .,-")
            if not value:
                continue
            key = (subtype, re.sub(r"\D+", "", value) if subtype in {"afm", "atak"} else value)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "identifier_subtype": subtype,
                    "label_text": label,
                    "value": value,
                    "nearby_text": _snippet_around_match(text, match.start(), match.end()),
                }
            )
    return candidates


def _read_pdf_page_text(source_file: str, page_number: int) -> str:
    try:
        with fitz.open(source_file) as document:
            if page_number < 1 or page_number > len(document):
                return ""
            return document[page_number - 1].get_text("text") or ""
    except Exception:
        return ""


def _field_has_identifier_subtype(field: dict[str, Any], subtype: str) -> bool:
    from ocr.clustering import _infer_identifier_subtype
    evidence = field.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    return _infer_identifier_subtype(
        field_type=str(field.get("field_type", "")),
        field=field,
        evidence=evidence,
    ) == subtype


def _attach_deterministic_text_identifiers(packet: dict[str, Any]) -> None:
    existing_keys: set[tuple[str, str, int, str]] = set()
    for field in _iter_packet_fields(packet):
        source_file = str(field.get("source_file") or "")
        page_number = int(field.get("page_number") or 0)
        value_digits = re.sub(r"\D+", "", str(field.get("value") or field.get("normalized_value") or ""))
        for subtype in ("afm", "atak", "kaek"):
            if _field_has_identifier_subtype(field, subtype):
                existing_keys.add((source_file, subtype, page_number, value_digits))

    for artifact in packet.get("page_extractions", []):
        if not isinstance(artifact, dict):
            continue
        extraction = artifact.get("extraction")
        if not isinstance(extraction, dict):
            continue
        for page_result in extraction.get("page_results", []):
            if not isinstance(page_result, dict):
                continue
            source_file = str(page_result.get("source_file") or artifact.get("source_file") or "")
            page_number = page_result.get("page_number")
            if not source_file or not isinstance(page_number, int):
                continue
            fields = page_result.setdefault("fields", [])
            if not isinstance(fields, list):
                page_result["fields"] = []
                fields = page_result["fields"]
            text = _read_pdf_page_text(source_file, page_number)
            if not text:
                continue
            for candidate_index, candidate in enumerate(_deterministic_identifier_candidates(text), start=1):
                subtype = candidate["identifier_subtype"]
                value = candidate["value"]
                value_digits = re.sub(r"\D+", "", value)
                key = (source_file, subtype, page_number, value_digits)
                if key in existing_keys:
                    continue
                existing_keys.add(key)
                field_id = f"p{page_number}-det-{subtype}-{candidate_index}"
                page_id = str(page_result.get("page_id") or f"{Path(source_file).stem}:p{page_number}")
                notes = [
                    "Deterministically extracted from embedded PDF text layer.",
                    f"Identifier subtype: {subtype}.",
                ]
                fields.append(
                    {
                        "field_id": field_id,
                        "field_ref": f"{page_id}:{field_id}",
                        "field_type": "property_id",
                        "label_text": candidate["label_text"],
                        "value": value,
                        "normalized_value": value_digits if subtype in {"afm", "atak"} else _normalize_cluster_value("property_id", value),
                        "is_handwritten": False,
                        "is_stamped": False,
                        "is_signature": False,
                        "confidence": "high",
                        "evidence": {
                            "nearby_text": candidate["nearby_text"],
                            "location_hint": "embedded text layer",
                            "source_file": source_file,
                            "page_number": page_number,
                            "page_id": page_id,
                            "page_kind": page_result.get("page_kind", "unknown"),
                        },
                        "notes": notes,
                        "source_file": source_file,
                        "page_number": page_number,
                        "page_id": page_id,
                        "page_kind": page_result.get("page_kind", "unknown"),
                    }
                )
            artifact["extraction_summary"] = _summarize_extraction(extraction)


def _build_executive_summary(packet: dict[str, Any]) -> dict[str, Any]:
    from ocr.costs import _as_float
    totals = packet.get("totals", {})
    if not isinstance(totals, dict):
        totals = {}
    check_summary = packet.get("check_summary", {})
    if not isinstance(check_summary, dict):
        check_summary = {}

    failed_checks = [
        check for check in packet.get("checks", [])
        if isinstance(check, dict) and check.get("status") == "fail"
    ]
    warning_checks = [
        check for check in packet.get("checks", [])
        if isinstance(check, dict) and check.get("status") == "warning"
    ]
    unknown_checks = [
        check for check in packet.get("checks", [])
        if isinstance(check, dict) and check.get("status") == "unknown"
    ]
    fuzzy_group_summary = packet.get("fuzzy_group_summary", {})
    if not isinstance(fuzzy_group_summary, dict):
        fuzzy_group_summary = {}
    cost_summary = packet.get("cost_summary", {})
    if not isinstance(cost_summary, dict):
        cost_summary = {}

    pages_extracted = int(totals.get("pages_extracted", 0))
    pages_failed = int(totals.get("pages_failed", 0))
    field_count = int(totals.get("field_count", 0))

    headline = (
        f"{pages_extracted} pages extracted with {pages_failed} failed pages and "
        f"{field_count} evidence fields."
    )
    if failed_checks:
        headline += f" {len(failed_checks)} blocking validation issue(s) need attention."
    elif warning_checks:
        headline += f" {len(warning_checks)} warning check(s) need review."
    else:
        headline += " No warning or fail checks were produced."

    key_findings: list[str] = []
    for check_id in ("extraction-completeness", "kaek-consistency", "afm-consistency"):
        for check in packet.get("checks", []):
            if isinstance(check, dict) and check.get("check_id") == check_id:
                key_findings.append(str(check.get("summary", "")))
                break

    fuzzy_count = int(fuzzy_group_summary.get("fuzzy_group_count", 0))
    if fuzzy_count:
        key_findings.append(
            f"{fuzzy_count} fuzzy near-match group(s) were found for address/name review."
        )
    if int(cost_summary.get("calls_with_usage", 0)):
        estimated_cost = float(cost_summary.get("estimated_cost_usd", 0.0))
        key_findings.append(
            f"Tracked provider usage for {cost_summary.get('calls_with_usage')} call(s); estimated cost is ${estimated_cost:.6f}."
        )
    elif int(cost_summary.get("calls_without_usage", 0)):
        key_findings.append(
            "Cost summary is partial because existing page artifacts do not include provider usage metadata."
        )

    review_priorities = [
        str(check.get("title", check.get("check_id", "Check")))
        for check in failed_checks + warning_checks + unknown_checks
    ][:8]

    return {
        "headline": headline,
        "status_counts": check_summary.get("checks_by_status", {}),
        "key_findings": [finding for finding in key_findings if finding],
        "review_priorities": review_priorities,
    }


def _attach_packet_analysis(packet: dict[str, Any]) -> None:
    _attach_deterministic_text_identifiers(packet)
    _recalculate_packet_totals(packet)
    _recalculate_packet_costs(packet)
    _attach_packet_clusters(packet)
    _attach_packet_checks(packet)
    packet["executive_summary"] = _build_executive_summary(packet)


def _build_packet_artifact(
    *,
    input_path: Path,
    provider: str,
    model: str,
    dpi: int,
    max_pages_per_file: int,
    triage_artifact: dict[str, Any],
    extractions: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    totals: dict[str, int],
) -> dict[str, Any]:
    packet = {
        "artifact_version": "arch_ocr.packet.v1",
        "packet_id": str(uuid4()),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "input_path": str(input_path),
        "provider_config": {
            "provider": provider,
            "model": model,
            "dpi": dpi,
            "max_pages_per_file": max_pages_per_file,
        },
        "source_files": [
            {
                "source_file": document.get("source_file"),
                "file_type": document.get("file_type"),
                "page_count": document.get("page_count"),
            }
            for document in triage_artifact.get("documents", [])
            if isinstance(document, dict)
        ],
        "triage": triage_artifact,
        "page_extractions": extractions,
        "errors": errors,
        "totals": totals,
    }
    _attach_packet_analysis(packet)
    return packet


def _iter_triaged_pages(
    triage_artifact: dict[str, Any],
    max_pages_per_file: int,
) -> list[tuple[Path, dict[str, Any]]]:
    selected: list[tuple[Path, dict[str, Any]]] = []
    for document in triage_artifact.get("documents", []):
        if not isinstance(document, dict):
            continue
        source_file = Path(str(document.get("source_file", "")))
        pages = [page for page in document.get("pages", []) if isinstance(page, dict)]
        for page in pages[:max_pages_per_file]:
            selected.append((source_file, page))
    return selected
