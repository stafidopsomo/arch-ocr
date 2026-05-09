"""Client-facing review model built from packet extraction artifacts."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from ocr.clustering import _strip_accents


PROPERTY_HINTS = ("ΚΑΤΣΩΝΗ", "ΚΑΤΖΩΝΗ", "ΝΙΚΑΙΑ", "18451", "18452")


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _norm(value: Any) -> str:
    return _strip_accents(str(value or "")).upper()


def _mention_pages(cluster: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    for mention in _as_list(cluster.get("mentions")):
        if not isinstance(mention, dict):
            continue
        ref = _clean_text(mention.get("field_ref"))
        page_id = _clean_text(mention.get("page_id"), "unknown page")
        key = ref or page_id
        if key in seen:
            continue
        seen.add(key)
        pages.append(
            {
                "field_ref": ref,
                "page_id": page_id,
                "source_file": _clean_text(mention.get("source_file")),
                "page_number": mention.get("page_number"),
                "confidence": _clean_text(mention.get("confidence"), "unknown"),
                "label": _clean_text(mention.get("label_text")),
                "value": _clean_text(mention.get("value")),
            }
        )
        if len(pages) >= limit:
            break
    return pages


def _cluster_card(cluster: dict[str, Any]) -> dict[str, Any]:
    mentions = _as_list(cluster.get("mentions"))
    return {
        "cluster_id": _clean_text(cluster.get("cluster_id")),
        "field_type": _clean_text(cluster.get("field_type")),
        "subtype": _clean_text(cluster.get("identifier_subtype")),
        "value": _clean_text(cluster.get("canonical_value"), "uncertain"),
        "normalized": _clean_text(cluster.get("normalized_value")),
        "mention_count": int(cluster.get("mention_count") or len(mentions) or 0),
        "source_page_count": int(cluster.get("source_page_count") or 0),
        "confidence_counts": _as_dict(cluster.get("confidence_counts")),
        "values": [str(value) for value in _as_list(cluster.get("values")) if str(value).strip()],
        "evidence": _mention_pages(cluster),
    }


def _clusters(packet: dict[str, Any], *field_types: str) -> list[dict[str, Any]]:
    wanted = set(field_types)
    values = [
        cluster
        for cluster in _as_list(packet.get("clusters"))
        if isinstance(cluster, dict) and (not wanted or cluster.get("field_type") in wanted)
    ]
    return sorted(
        values,
        key=lambda cluster: (
            -int(cluster.get("mention_count") or 0),
            str(cluster.get("field_type") or ""),
            str(cluster.get("canonical_value") or ""),
        ),
    )


def _identifier_clusters(packet: dict[str, Any], subtype: str) -> list[dict[str, Any]]:
    return [
        cluster
        for cluster in _clusters(packet, "property_id", "permit_number")
        if cluster.get("identifier_subtype") == subtype
    ]


def _looks_like_main_property(value: Any) -> bool:
    normalized = _norm(value)
    return any(hint in normalized for hint in PROPERTY_HINTS)


def _build_property_identity(packet: dict[str, Any]) -> dict[str, Any]:
    addresses = [_cluster_card(cluster) for cluster in _clusters(packet, "address")]
    likely_main = [item for item in addresses if _looks_like_main_property(item["value"])]
    other_addresses = [item for item in addresses if not _looks_like_main_property(item["value"])]
    primary_address = (likely_main or addresses or [{}])[0]
    kaek = [_cluster_card(cluster) for cluster in _identifier_clusters(packet, "kaek")]
    atak = [_cluster_card(cluster) for cluster in _identifier_clusters(packet, "atak")]
    property_ids = [_cluster_card(cluster) for cluster in _clusters(packet, "property_id")]
    return {
        "primary_address": primary_address,
        "address_variants": likely_main[:12],
        "possible_other_addresses": other_addresses[:8],
        "kaek": kaek[:5],
        "atak": atak[:5],
        "property_identifiers": property_ids[:12],
    }


def _role_label(field_type: str) -> str:
    return {
        "owner": "Ιδιοκτήτες",
        "applicant": "Αιτούντες",
        "engineer": "Μηχανικοί",
        "architect": "Αρχιτέκτονες",
        "person_name": "Λοιπά πρόσωπα",
    }.get(field_type, field_type)


def _build_people(packet: dict[str, Any]) -> list[dict[str, Any]]:
    people: list[dict[str, Any]] = []
    for field_type in ("owner", "applicant", "engineer", "architect", "person_name"):
        cards = [_cluster_card(cluster) for cluster in _clusters(packet, field_type)]
        if cards:
            people.append(
                {
                    "role": _role_label(field_type),
                    "field_type": field_type,
                    "items": cards[:10],
                    "total": len(cards),
                }
            )
    return people


def _classify_document(path: str) -> str:
    text = _norm(path)
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}:
        return "Φωτογραφίες / εικόνες"
    if "E9" in text or "IDIOKTHT" in text:
        return "Φορολογικά / Ε9"
    if any(token in text for token in ("ADEIA", "ΑΔΕΙΑ", "299-2002", "3028-2015", "TOPOGRAFIKO", "DIAGRAMMA")):
        return "Άδειες / σχέδια / τοπογραφικά"
    if any(token in text for token in ("4495", "EEDMK", "ΕΕΔΜΚ", "DILOSI", "ΔΗΛΩΣ")):
        return "Δηλώσεις / ρυθμίσεις"
    if any(token in text for token in ("SCAN", "CAMSCANNER")):
        return "Σαρωμένα έγγραφα"
    return "Λοιπά έγγραφα"


def _display_source_name(source_file: Any) -> str:
    path = str(source_file or "")
    return Path(path).name or path or "upload"


def _build_document_groups(packet: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for source in _as_list(packet.get("source_files")):
        if not isinstance(source, dict):
            continue
        path = str(source.get("source_file") or "")
        category = _classify_document(path)
        group = groups.setdefault(category, {"title": category, "files": [], "page_count": 0})
        group["files"].append(
            {
                "name": _display_source_name(path),
                "path": path,
                "file_type": _clean_text(source.get("file_type"), "file"),
                "page_count": int(source.get("page_count") or 0),
            }
        )
        group["page_count"] += int(source.get("page_count") or 0)
    return sorted(groups.values(), key=lambda item: (-int(item["page_count"]), item["title"]))


def _issue_action(check_id: str, status: str) -> str:
    if check_id == "extraction-completeness":
        return "Άνοιξε τη σελίδα που απέτυχε και έλεγξε αν χρειάζεται επανάληψη ή χειροκίνητη καταχώρηση."
    if check_id == "field-confidence":
        return "Έλεγξε τα πεδία χαμηλής εμπιστοσύνης πριν τα χρησιμοποιήσεις σε συμπέρασμα."
    if check_id == "handwritten-content":
        return "Σύγκρινε τα χειρόγραφα πεδία με την εικόνα της σελίδας."
    if check_id in {"address-consistency", "person-name-consistency"}:
        return "Επιβεβαίωσε ποιες παραλλαγές είναι το ίδιο στοιχείο και ποιες ανήκουν αλλού."
    if status == "fail":
        return "Χρειάζεται ανθρώπινος έλεγχος πριν θεωρηθεί ολοκληρωμένος ο φάκελος."
    return "Χρειάζεται γρήγορη επιβεβαίωση από τον χρήστη."


def _build_issues(packet: dict[str, Any]) -> list[dict[str, Any]]:
    severity_order = {"fail": 0, "warning": 1, "unknown": 2, "pass": 3}
    issues: list[dict[str, Any]] = []
    for check in _as_list(packet.get("checks")):
        if not isinstance(check, dict):
            continue
        status = _clean_text(check.get("status"), "unknown")
        if status == "pass":
            continue
        check_id = _clean_text(check.get("check_id"))
        issues.append(
            {
                "check_id": check_id,
                "status": status,
                "title": _clean_text(check.get("title"), check_id or "Έλεγχος"),
                "summary": _clean_text(check.get("summary")),
                "action": _issue_action(check_id, status),
                "details": [str(item) for item in _as_list(check.get("details"))[:8]],
                "evidence_refs": [str(item) for item in _as_list(check.get("evidence_refs"))[:8]],
            }
        )
    return sorted(issues, key=lambda item: (severity_order.get(item["status"], 9), item["title"]))


def _build_permits(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [_cluster_card(cluster) for cluster in _identifier_clusters(packet, "permit_number")][:16]


def build_review_model(packet: dict[str, Any]) -> dict[str, Any]:
    """Create a higher-level model designed for architect-facing review."""
    totals = _as_dict(packet.get("totals"))
    cost = _as_dict(packet.get("cost_summary"))
    checks = _as_dict(packet.get("check_summary"))
    executive = _as_dict(packet.get("executive_summary"))
    issues = _build_issues(packet)
    status = "needs_review" if any(issue["status"] == "fail" for issue in issues) else "review_recommended" if issues else "ready"
    return {
        "status": status,
        "headline": _clean_text(executive.get("headline"), "Ο φάκελος ελέγχθηκε."),
        "created_at": _clean_text(packet.get("created_at")),
        "provider": _clean_text(_as_dict(packet.get("provider_config")).get("provider")),
        "model": _clean_text(_as_dict(packet.get("provider_config")).get("model")),
        "metrics": {
            "pages_extracted": int(totals.get("pages_extracted") or 0),
            "pages_selected": int(totals.get("pages_selected") or totals.get("pages_triaged") or 0),
            "pages_failed": int(totals.get("pages_failed") or 0),
            "field_count": int(totals.get("field_count") or 0),
            "handwritten_field_count": int(totals.get("handwritten_field_count") or 0),
            "low_confidence_field_count": int(totals.get("low_confidence_field_count") or 0),
            "check_count": int(checks.get("check_count") or 0),
            "estimated_cost_usd": float(cost.get("estimated_cost_usd") or 0.0),
        },
        "property": _build_property_identity(packet),
        "documents": _build_document_groups(packet),
        "people": _build_people(packet),
        "permits": _build_permits(packet),
        "issues": issues,
        "technical_summary": {
            "key_findings": _as_list(executive.get("key_findings")),
            "review_priorities": _as_list(executive.get("review_priorities")),
        },
    }
