"""Review HTML generation endpoint and related rendering helpers."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import fitz  # PyMuPDF
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from api.auth import _require_demo_access
from api.storage import _job_dir, _load_analyzed_packet
from ocr.clustering import _iter_packet_fields
from ocr.providers import _redact_secrets
from ocr.reporting import _render_packet_report
from ocr.review_model import build_review_model


def _basename(value: Any) -> str:
    return Path(str(value or "")).name


def _translate_status(status: Any, lang: str = "en") -> str:
    value = str(status or "unknown")
    if lang != "el":
        return value
    return {
        "pass": "επιτυχία",
        "warning": "προειδοποίηση",
        "fail": "αποτυχία",
        "unknown": "άγνωστο",
        "possible_match": "πιθανή ταύτιση",
    }.get(value, value)


def _translate_check_title(title: Any, lang: str = "en") -> str:
    value = str(title or "")
    if lang != "el":
        return value
    return {
        "Extraction completeness": "Πληρότητα εξαγωγής",
        "Field confidence": "Εμπιστοσύνη πεδίων",
        "Handwritten content": "Χειρόγραφο περιεχόμενο",
        "Address consistency": "Συνέπεια διευθύνσεων",
        "Person name consistency": "Συνέπεια ονομάτων",
        "Identifier classification": "Ταξινόμηση αναγνωριστικών",
        "KAEK consistency": "Συνέπεια ΚΑΕΚ",
        "AFM evidence": "Ένδειξη ΑΦΜ",
        "ATAK evidence": "Ένδειξη ΑΤΑΚ",
        "Registry identifier evidence": "Ένδειξη αριθμού μητρώου",
        "Unknown identifier review": "Έλεγχος άγνωστων αναγνωριστικών",
        "Date consistency": "Συνέπεια ημερομηνιών",
        "Permit number evidence": "Ένδειξη αριθμού άδειας",
        "Owner evidence": "Ένδειξη ιδιοκτήτη",
        "Engineer evidence": "Ένδειξη μηχανικού",
        "Signature evidence": "Ένδειξη υπογραφής",
        "Stamp evidence": "Ένδειξη σφραγίδας",
    }.get(value, value)


def _review_labels(lang: str) -> dict[str, str]:
    if lang == "el":
        return {
            "title_prefix": "προεπισκόπηση φακέλου",
            "markdown": "Markdown αναφορά",
            "json": "Packet JSON",
            "back": "Πίσω στο job",
            "admin": "Διαχείριση",
            "pages": "Σελίδες",
            "fields": "Πεδία",
            "checks": "Έλεγχοι",
            "cost": "Εκτ. κόστος",
            "coverage": "Κάλυψη σελίδων",
            "extracted_selected": "εξήχθησαν / επιλέχθηκαν",
            "triaged_total": "σελίδες εντοπίστηκαν συνολικά",
            "skipped_cap": "σελίδες δεν αναλύθηκαν λόγω ορίου demo",
            "no_skipped": "Δεν παραλείφθηκαν σελίδες από το όριο demo.",
            "executive": "Σύνοψη",
            "validation": "Έλεγχοι εγκυρότητας",
            "priorities": "Προτεραιότητες ελέγχου",
            "source_files": "Αρχεία πηγής",
            "fuzzy": "Πιθανές ταυτίσεις",
            "clusters": "Ομάδες τιμών",
            "errors": "Σφάλματα",
            "pages_word": "σελίδες",
            "none": "Δεν καταγράφηκε κάτι.",
            "no_checks": "Δεν καταγράφηκαν έλεγχοι.",
            "no_clusters": "Δεν καταγράφηκαν ομάδες τιμών.",
            "no_fuzzy": "Δεν καταγράφηκαν πιθανές ταυτίσεις.",
            "no_errors": "Δεν καταγράφηκαν σφάλματα εξαγωγής σελίδων.",
            "language_note": "Η δομή της αναφοράς εμφανίζεται στα ελληνικά. Κάποια αυτόματα summaries/evidence παραμένουν όπως παράχθηκαν από τον extractor για να μη χαθεί ακρίβεια.",
        }
    return {
        "title_prefix": "packet review",
        "markdown": "Markdown report",
        "json": "Packet JSON",
        "back": "Back to job",
        "admin": "Admin",
        "pages": "Pages",
        "fields": "Fields",
        "checks": "Checks",
        "cost": "Estimated cost",
        "coverage": "Page Coverage",
        "extracted_selected": "extracted / selected",
        "triaged_total": "pages triaged in total",
        "skipped_cap": "pages were not analyzed because of the demo cap",
        "no_skipped": "No pages were skipped by the demo cap.",
        "executive": "Executive Summary",
        "validation": "Validation Checks",
        "priorities": "Review Priorities",
        "source_files": "Source Files",
        "fuzzy": "Fuzzy Review",
        "clusters": "Clusters",
        "errors": "Errors",
        "pages_word": "pages",
        "none": "None recorded.",
        "no_checks": "No validation checks recorded.",
        "no_clusters": "No clusters recorded.",
        "no_fuzzy": "No fuzzy near-match groups recorded.",
        "no_errors": "No page extraction errors recorded.",
        "language_note": "",
    }


def _status_badge(status: Any, lang: str = "en") -> str:
    safe = html.escape(str(status or "unknown"))
    cls = re.sub(r"[^a-z0-9_-]+", "-", safe.lower())
    label = html.escape(_translate_status(status, lang))
    return f'<span class="badge badge-{cls}">{label}</span>'


def _list_items(values: Any, limit: int = 8, empty_text: str = "None recorded.") -> str:
    if not isinstance(values, list) or not values:
        return f'<p class="muted">{html.escape(empty_text)}</p>'
    items = []
    for value in values[:limit]:
        items.append(f"<li>{html.escape(str(value))}</li>")
    if len(values) > limit:
        items.append(f'<li class="muted">... {len(values) - limit} more</li>')
    return f"<ul>{''.join(items)}</ul>"


def _field_index_by_ref(packet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for field in _iter_packet_fields(packet):
        ref = field.get("field_ref")
        if isinstance(ref, str) and ref:
            indexed[ref] = field
    return indexed


def _safe_job_source_path(job_id: str, source_file: Any) -> Path:
    uploads_dir = (_job_dir(job_id) / "uploads").resolve()
    candidate = Path(str(source_file)).resolve()
    if uploads_dir not in candidate.parents and candidate != uploads_dir:
        raise HTTPException(status_code=403, detail="Source file is outside this job.")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Source file not found.")
    return candidate


def _field_ref_thumbnail_html(job_id: str, token: str, ref: str, field: dict[str, Any] | None) -> str:
    safe_ref = html.escape(ref)
    if not field:
        return f"<code>{safe_ref}</code>"
    page_id = html.escape(str(field.get("page_id") or ""))
    value = html.escape(str(field.get("value") or ""))
    thumb_query = urlencode({"token": token, "field_ref": ref})
    thumb_url = f"/jobs/{html.escape(job_id)}/page-thumbnail?{html.escape(thumb_query)}"
    return f"""
    <a class="evidence-card" href="{thumb_url}" target="_blank" title="{safe_ref}">
      <img src="{thumb_url}" loading="lazy" />
      <span><code>{safe_ref}</code><small>{page_id} · {value}</small></span>
    </a>
    """


def _render_check_cards(
    checks: Any,
    *,
    packet: dict[str, Any],
    job_id: str,
    token: str,
    lang: str = "en",
) -> str:
    labels = _review_labels(lang)
    if not isinstance(checks, list) or not checks:
        return f'<p class="muted">{html.escape(labels["no_checks"])}</p>'
    order = {"fail": 0, "warning": 1, "unknown": 2, "pass": 3}
    sorted_checks = sorted(checks, key=lambda c: order.get(str(c.get("status", "unknown")), 99) if isinstance(c, dict) else 99)
    cards = []
    fields_by_ref = _field_index_by_ref(packet)
    for check in sorted_checks:
        if not isinstance(check, dict):
            continue
        title = html.escape(_translate_check_title(check.get("title") or check.get("check_id") or "Check", lang))
        summary = html.escape(str(check.get("summary") or ""))
        evidence_refs = check.get("evidence_refs") if isinstance(check.get("evidence_refs"), list) else []
        evidence = " ".join(
            _field_ref_thumbnail_html(job_id, token, str(ref), fields_by_ref.get(str(ref)))
            for ref in evidence_refs[:8]
        )
        if len(evidence_refs) > 8:
            evidence += f' <span class="muted">+{len(evidence_refs) - 8} more</span>'
        details = _list_items(check.get("details"), limit=6, empty_text=labels["none"])
        cards.append(
            f"""
            <article class="card check-card">
              <div class="card-head">{_status_badge(check.get("status"), lang)}<h3>{title}</h3></div>
              <p>{summary}</p>
              {details}
              <div class="refs">{evidence}</div>
            </article>
            """
        )
    return "".join(cards)


def _render_cluster_cards(clusters: Any, lang: str = "en") -> str:
    labels = _review_labels(lang)
    if not isinstance(clusters, list) or not clusters:
        return f'<p class="muted">{html.escape(labels["no_clusters"])}</p>'
    cards = []
    for cluster in clusters[:24]:
        if not isinstance(cluster, dict):
            continue
        field_type = html.escape(str(cluster.get("field_type") or "field"))
        subtype = cluster.get("subtype")
        canonical = html.escape(str(cluster.get("canonical_value") or ""))
        mentions = cluster.get("mentions") if isinstance(cluster.get("mentions"), list) else []
        subtitle = f"{field_type}"
        if subtype:
            subtitle += f" / {html.escape(str(subtype))}"
        cards.append(
            f"""
            <article class="mini-card">
              <div class="muted">{subtitle} · {len(mentions)} mentions</div>
              <div class="mono strong">{canonical}</div>
            </article>
            """
        )
    if len(clusters) > 24:
        cards.append(f'<p class="muted">Showing 24 of {len(clusters)} clusters.</p>')
    return "".join(cards)


def _render_fuzzy_groups(groups: Any, lang: str = "en") -> str:
    labels = _review_labels(lang)
    if not isinstance(groups, list) or not groups:
        return f'<p class="muted">{html.escape(labels["no_fuzzy"])}</p>'
    cards = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        values = group.get("canonical_values") if isinstance(group.get("canonical_values"), list) else []
        cards.append(
            f"""
            <article class="mini-card">
              <div>{_status_badge(group.get("status"), lang)} <span class="muted">{html.escape(str(group.get("field_type") or ""))}</span></div>
              {_list_items(values, limit=6, empty_text=labels["none"])}
            </article>
            """
        )
    return "".join(cards)


def _render_page_coverage(totals: dict[str, Any], labels: dict[str, str]) -> str:
    pages_triaged = int(totals.get("pages_triaged") or 0)
    pages_selected = int(totals.get("pages_selected") or 0)
    pages_extracted = int(totals.get("pages_extracted") or 0)
    pages_failed = int(totals.get("pages_failed") or 0)
    skipped = max(pages_triaged - pages_selected, 0)
    skipped_text = (
        f"{skipped} {labels['skipped_cap']}"
        if skipped
        else labels["no_skipped"]
    )
    failed_text = f" · {pages_failed} failed" if pages_failed else ""
    return f"""
    <section class="section card coverage-card">
      <h2>{html.escape(labels['coverage'])}</h2>
      <div class="coverage-grid">
        <div><strong>{html.escape(str(pages_extracted))} / {html.escape(str(pages_selected))}</strong><span>{html.escape(labels['extracted_selected'])}{html.escape(failed_text)}</span></div>
        <div><strong>{html.escape(str(pages_triaged))}</strong><span>{html.escape(labels['triaged_total'])}</span></div>
        <div class="{html.escape('coverage-warn' if skipped else 'coverage-ok')}"><strong>{html.escape(str(skipped))}</strong><span>{html.escape(skipped_text)}</span></div>
      </div>
    </section>
    """


def _v2_status_label(status: str) -> tuple[str, str]:
    return {
        "ready": ("Έτοιμο", "ok"),
        "review_recommended": ("Θέλει έλεγχο", "warn"),
        "needs_review": ("Θέλει άμεσο έλεγχο", "fail"),
    }.get(status, ("Θέλει έλεγχο", "warn"))


def _v2_issue_label(status: str) -> str:
    return {
        "fail": "κρίσιμο",
        "warning": "έλεγχος",
        "unknown": "άγνωστο",
    }.get(status, status)


def _v2_thumb(job_id: str, token: str, evidence: dict[str, Any]) -> str:
    ref = str(evidence.get("field_ref") or "")
    if not ref:
        return ""
    query_values = {"field_ref": ref}
    if token:
        query_values["token"] = token
    qs = html.escape(urlencode(query_values))
    thumb_url = f"/jobs/{html.escape(job_id)}/page-thumbnail?{qs}"
    full_url = f"/jobs/{html.escape(job_id)}/page-image?{qs}"
    label = html.escape(str(evidence.get("value") or evidence.get("label") or evidence.get("page_id") or ref))
    page = html.escape(str(evidence.get("page_id") or ""))
    return f"""
    <a class="ev" href="{full_url}" data-thumb="{thumb_url}" data-full="{full_url}" data-label="{label}" target="_blank" rel="noopener">
      <img src="{thumb_url}" loading="lazy" alt="{label}" />
      <span>{label}</span>
      <small>{page}</small>
    </a>
    """


def _v2_evidence(job_id: str, token: str, items: list[dict[str, Any]], limit: int = 4) -> str:
    cards = "".join(_v2_thumb(job_id, token, item) for item in items[:limit] if isinstance(item, dict))
    return f'<div class="evidence">{cards}</div>' if cards else ""


def _v2_fact_card(title: str, value: Any, meta: str = "", tone: str = "") -> str:
    cls = f" fact-{tone}" if tone else ""
    return f"""
    <article class="fact{cls}">
      <div class="fact-label">{html.escape(title)}</div>
      <div class="fact-value">{html.escape(str(value or "Δεν εντοπίστηκε"))}</div>
      {f'<div class="fact-meta">{html.escape(meta)}</div>' if meta else ''}
    </article>
    """


def _v2_cluster_rows(
    items: Any,
    *,
    job_id: str,
    token: str,
    empty: str = "Δεν εντοπίστηκε κάτι.",
    limit: int = 8,
) -> str:
    if not isinstance(items, list) or not items:
        return f'<p class="muted">{html.escape(empty)}</p>'
    rows = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        values = item.get("values") if isinstance(item.get("values"), list) else []
        variants = ", ".join(str(value) for value in values[:4] if str(value).strip())
        meta = f"{item.get('mention_count', 0)} αναφορές · {item.get('source_page_count', 0)} σελίδες"
        if item.get("subtype"):
            meta += f" · {item.get('subtype')}"
        rows.append(
            f"""
            <article class="row-card">
              <div>
                <div class="row-value">{html.escape(str(item.get("value") or ""))}</div>
                <div class="row-meta">{html.escape(meta)}</div>
                {f'<div class="row-variants">Παραλλαγές: {html.escape(variants)}</div>' if variants else ''}
              </div>
              {_v2_evidence(job_id, token, item.get("evidence", []), limit=3)}
            </article>
            """
        )
    if len(items) > limit:
        rows.append(f'<p class="muted">Εμφανίζονται {limit} από {len(items)}.</p>')
    return "".join(rows)


def _render_document_groups_v2(groups: Any) -> str:
    if not isinstance(groups, list) or not groups:
        return '<p class="muted">Δεν καταγράφηκαν αρχεία.</p>'
    cards = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        files = group.get("files") if isinstance(group.get("files"), list) else []
        file_rows = "".join(
            f"<li><span>{html.escape(str(file.get('name') or 'file'))}</span><span>{html.escape(str(file.get('page_count') or 0))} σελ.</span></li>"
            for file in files[:6]
            if isinstance(file, dict)
        )
        if len(files) > 6:
            file_rows += f"<li class='muted'><span>+{len(files) - 6} ακόμα αρχεία</span><span></span></li>"
        cards.append(
            f"""
            <article class="doc-group">
              <div class="doc-head">
                <h3>{html.escape(str(group.get("title") or "Ομάδα εγγράφων"))}</h3>
                <span>{html.escape(str(len(files)))} αρχεία · {html.escape(str(group.get("page_count") or 0))} σελ.</span>
              </div>
              <ul>{file_rows}</ul>
            </article>
            """
        )
    return "".join(cards)


def _render_people_v2(groups: Any, job_id: str, token: str) -> str:
    if not isinstance(groups, list) or not groups:
        return '<p class="muted">Δεν εντοπίστηκαν πρόσωπα.</p>'
    sections = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        sections.append(
            f"""
            <section class="person-group">
              <div class="section-head">
                <h3>{html.escape(str(group.get("role") or "Πρόσωπα"))}</h3>
                <span>{html.escape(str(group.get("total") or 0))} ομάδες</span>
              </div>
              {_v2_cluster_rows(group.get("items"), job_id=job_id, token=token, limit=5)}
            </section>
            """
        )
    return "".join(sections)


def _render_issues_v2(issues: Any, job_id: str, token: str, packet: dict[str, Any]) -> str:
    if not isinstance(issues, list) or not issues:
        return '<p class="muted">Δεν υπάρχουν ανοικτά θέματα ελέγχου.</p>'
    fields_by_ref = _field_index_by_ref(packet)
    cards = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        status = str(issue.get("status") or "unknown")
        refs = [fields_by_ref.get(str(ref), {"field_ref": ref}) for ref in issue.get("evidence_refs", []) if ref]
        details = issue.get("details") if isinstance(issue.get("details"), list) else []
        detail_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in details[:5])
        cards.append(
            f"""
            <article class="issue issue-{html.escape(status)}">
              <div class="issue-head">
                <span>{html.escape(_v2_issue_label(status))}</span>
                <h3>{html.escape(_translate_check_title(issue.get("title"), "el"))}</h3>
              </div>
              <p>{html.escape(str(issue.get("summary") or ""))}</p>
              <p class="action">{html.escape(str(issue.get("action") or ""))}</p>
              {f'<ul>{detail_items}</ul>' if detail_items else ''}
              {_v2_evidence(job_id, token, refs, limit=4)}
            </article>
            """
        )
    return "".join(cards)


def _render_v2_source_files(files: list[dict[str, Any]]) -> str:
    if not files:
        return '<p class="muted">Δεν καταγράφηκαν αρχεία πηγής.</p>'
    rows = "".join(
        f"<li><span>{html.escape(str(f.get('name') or 'file'))}</span><span class='muted'>{html.escape(str(f.get('page_count') or 0))} σελ.</span></li>"
        for f in files[:20]
    )
    extra = f"<li class='muted'><span>+{len(files) - 20} ακόμα</span><span></span></li>" if len(files) > 20 else ""
    return f"<ul class='file-list'>{rows}{extra}</ul>"


def _render_v2_errors(errors: list[dict[str, Any]]) -> str:
    if not errors:
        return '<p class="muted">Δεν υπάρχουν σφάλματα εξαγωγής.</p>'
    rows = "".join(
        f"<li><strong>{html.escape(str(e.get('page_id') or 'page'))}</strong><span class='muted'>{html.escape(str(e.get('message') or ''))}</span></li>"
        for e in errors[:12]
    )
    return f"<ul class='error-list'>{rows}</ul>"


def _render_v2_list(items: list[Any]) -> str:
    if not items:
        return '<p class="muted">Δεν υπάρχουν στοιχεία.</p>'
    return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"


def _render_packet_review_v2(job_id: str, packet: dict[str, Any], token: str = "") -> str:
    # Render parity note: every top-level key from the packet JSON is either
    # surfaced here (hero, issues, identity, people, documents, source files,
    # extraction errors, executive summary, triage) or intentionally omitted
    # because it's already transformed: `clusters` → property/people/permits,
    # `page_extractions` → field-level evidence thumbnails, `fuzzy_groups` is
    # the input to the clustering already shown above.
    model = build_review_model(packet)
    metrics = model["metrics"]
    status_label, status_tone = _v2_status_label(str(model.get("status")))
    token_only_query = f"?{html.escape(urlencode({'token': token}))}" if token else ""
    primary_address = model["property"].get("primary_address") or {}
    kaek_items = model["property"].get("kaek") or []
    kaek_value = kaek_items[0].get("value") if kaek_items else ""
    address_variants = model["property"].get("address_variants") or []
    possible_other = model["property"].get("possible_other_addresses") or []
    permits = model.get("permits") or []
    people = model.get("people") or []
    issues = model.get("issues") or []
    documents = model.get("documents") or []
    source_files = model.get("source_files") or []
    extraction_errors = model.get("extraction_errors") or []
    triage_summary = model.get("triage_summary") or {}
    key_findings = model.get("key_findings") or []
    review_priorities = model.get("review_priorities") or []
    handwritten = metrics.get("handwritten_field_count") or 0
    low_conf = metrics.get("low_confidence_field_count") or 0
    quality_tile = ""
    if handwritten or low_conf:
        quality_tile = _v2_fact_card(
            "Ποιότητα πεδίων",
            f"{handwritten} χειρόγραφα · {low_conf} χαμηλή",
            "πεδία που θέλουν προσοχή",
            "fail",
        )
    return f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>arch-ocr architect review {html.escape(job_id)}</title>
        <style>
          :root {{ --bg:#fbfaf7; --paper:#fff; --paper2:#f4f1ea; --line:#e3ded1; --ink:#151922; --muted:#6f7682; --accent:#1a2540; --ok:#2f7a53; --warn:#9a6b00; --fail:#a43a2d; }}
          * {{ box-sizing:border-box; }}
          body {{ margin:0; background:var(--bg); color:var(--ink); font-family:Inter, ui-sans-serif, system-ui, sans-serif; line-height:1.45; }}
          header {{ padding:28px 36px 22px; border-bottom:1px solid var(--line); background:rgba(251,250,247,.96); position:sticky; top:0; z-index:2; }}
          main {{ padding:28px 36px 56px; }}
          h1 {{ margin:8px 0 8px; font-family:Georgia, serif; font-size:30px; line-height:1.15; max-width:1050px; }}
          h2 {{ margin:0 0 14px; font-size:14px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }}
          h3 {{ margin:0; font-size:16px; }}
          .muted {{ color:var(--muted); }}
          .mono {{ font-family:"JetBrains Mono", ui-monospace, monospace; font-size:12px; }}
          .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:16px; }}
          .btn {{ display:inline-flex; align-items:center; min-height:34px; padding:0 12px; border:1px solid var(--line); border-radius:6px; background:var(--paper); color:var(--accent); text-decoration:none; font-weight:650; font-size:13px; }}
          .btn-primary {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
          .status {{ display:inline-flex; align-items:center; border-radius:999px; padding:5px 10px; font-weight:750; font-size:12px; border:1px solid var(--line); background:var(--paper2); }}
          .status-ok {{ color:var(--ok); background:#edf8f1; }}
          .status-warn {{ color:var(--warn); background:#fff7dc; }}
          .status-fail {{ color:var(--fail); background:#fff0ed; }}
          .hero-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-top:20px; }}
          .fact,.card,.issue,.doc-group,.row-card {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; }}
          .fact {{ padding:14px; min-height:92px; }}
          .fact-label,.row-meta,.row-variants,.doc-head span,.section-head span {{ color:var(--muted); font-size:12px; }}
          .fact-value {{ margin-top:5px; font-size:20px; font-weight:780; overflow-wrap:anywhere; }}
          .fact-meta {{ margin-top:4px; color:var(--muted); font-size:12px; }}
          .fact-fail {{ border-color:#e2aaa2; background:#fff7f5; }}
          .layout {{ display:grid; grid-template-columns:minmax(0,1.2fr) minmax(360px,.8fr); gap:22px; align-items:start; }}
          .card {{ padding:18px; margin-bottom:18px; }}
          .subsection {{ margin-top:18px; }}
          .subsection:first-child {{ margin-top:0; }}
          .section-head,.doc-head,.issue-head {{ display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:10px; }}
          .doc-group {{ padding:14px; margin-bottom:10px; }}
          .doc-group ul {{ list-style:none; padding:0; margin:8px 0 0; }}
          .doc-group li {{ display:flex; justify-content:space-between; gap:16px; border-top:1px solid var(--line); padding:7px 0; font-size:13px; }}
          .row-card {{ padding:13px; margin-bottom:10px; display:flex; flex-direction:column; gap:10px; }}
          .row-value {{ font-weight:750; overflow-wrap:anywhere; }}
          .row-variants {{ margin-top:4px; }}
          .evidence {{ display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-start; }}
          .ev {{ width:128px; border:1px solid var(--line); border-radius:6px; overflow:hidden; background:var(--paper2); text-decoration:none; color:var(--ink); cursor:zoom-in; transition:transform .12s ease, box-shadow .12s ease; }}
          .ev:hover {{ transform:translateY(-1px); box-shadow:0 2px 6px rgba(0,0,0,.07); }}
          .ev img {{ width:100%; height:84px; object-fit:cover; object-position:top center; display:block; border-bottom:1px solid var(--line); background:#fff; }}
          .ev span,.ev small {{ display:block; padding:0 6px; }}
          .ev span {{ padding-top:5px; font-size:11px; font-weight:700; line-height:1.3; max-height:2.6em; overflow:hidden; text-overflow:ellipsis; word-break:break-word; }}
          .ev small {{ padding-bottom:5px; color:var(--muted); font-size:10px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
          .issue {{ padding:15px; margin-bottom:10px; }}
          .issue-head {{ justify-content:flex-start; }}
          .issue-head span {{ border-radius:999px; padding:3px 8px; font-size:11px; font-weight:800; background:var(--paper2); }}
          .issue-fail {{ border-color:#e2aaa2; background:#fff7f5; }}
          .issue-warning {{ border-color:#e4c36f; background:#fffaf0; }}
          .issue .action {{ font-weight:700; }}
          .issue ul {{ margin:8px 0; padding-left:18px; }}
          .person-group {{ margin-bottom:18px; }}
          .file-list,.error-list {{ list-style:none; padding:0; margin:0; }}
          .file-list li {{ display:flex; justify-content:space-between; gap:12px; border-top:1px solid var(--line); padding:8px 0; font-size:13px; }}
          .file-list li:first-child {{ border-top:none; }}
          .error-list li {{ display:flex; flex-direction:column; gap:2px; border-top:1px solid var(--line); padding:8px 0; font-size:13px; }}
          .error-list li:first-child {{ border-top:none; }}
          details {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:12px 16px; margin-bottom:12px; }}
          details summary {{ cursor:pointer; font-weight:700; font-size:13px; color:var(--accent); list-style:none; }}
          details summary::-webkit-details-marker {{ display:none; }}
          details summary::before {{ content:"▸ "; display:inline-block; transition:transform .15s; }}
          details[open] summary::before {{ transform:rotate(90deg); }}
          details ul {{ margin:10px 0 4px; padding-left:22px; }}
          .lightbox {{ position:fixed; inset:0; background:rgba(15,18,28,.88); display:none; align-items:center; justify-content:center; z-index:50; cursor:zoom-out; padding:24px; }}
          .lightbox.open {{ display:flex; }}
          .lightbox img {{ max-width:96vw; max-height:92vh; width:auto; height:auto; box-shadow:0 12px 40px rgba(0,0,0,.4); background:#fff; border-radius:4px; }}
          .lightbox-meta {{ position:absolute; top:14px; left:18px; right:60px; color:#f3efe5; font-size:13px; line-height:1.4; pointer-events:none; }}
          .lightbox-close {{ position:absolute; top:12px; right:14px; width:36px; height:36px; border-radius:50%; border:none; background:rgba(255,255,255,.18); color:#fff; font-size:20px; cursor:pointer; }}
          .lightbox-close:hover {{ background:rgba(255,255,255,.3); }}
          @media (max-width: 1050px) {{ .layout {{ grid-template-columns:1fr; }} header,main {{ padding-left:18px; padding-right:18px; }} }}
        </style>
      </head>
      <body>
        <header>
          <div class="mono muted">arch-ocr · architect review · {html.escape(job_id)}</div>
          <h1>Ανασύνθεση φακέλου και σημεία ελέγχου</h1>
          <div><span class="status status-{html.escape(status_tone)}">{html.escape(status_label)}</span> <span class="muted">{html.escape(str(model.get("created_at") or ""))} · {html.escape(str(model.get("model") or ""))}</span></div>
          <div class="actions">
            <a class="btn btn-primary" href="#issues">Θέματα προς έλεγχο</a>
            <a class="btn" href="/jobs/{html.escape(job_id)}/review{token_only_query}">Technical review</a>
            <a class="btn" href="/jobs/{html.escape(job_id)}/packet{token_only_query}">Packet JSON</a>
            <a class="btn" href="/design/arch-ocr.html?screen=job&job={html.escape(job_id)}">Πίσω στο job</a>
          </div>
          <section class="hero-grid">
            {_v2_fact_card("Κύρια διεύθυνση", primary_address.get("value"), f"{primary_address.get('mention_count', 0)} αναφορές · {primary_address.get('source_page_count', 0)} σελίδες")}
            {_v2_fact_card("ΚΑΕΚ", kaek_value, f"{kaek_items[0].get('mention_count', 0)} αναφορές" if kaek_items else "")}
            {_v2_fact_card("Σελίδες", f"{metrics['pages_extracted']} / {metrics['pages_selected']}", f"{metrics['pages_failed']} αποτυχίες", "fail" if metrics["pages_failed"] else "")}
            {_v2_fact_card("Πεδία / κόστος", f"{metrics['field_count']} πεδία", f"${metrics['estimated_cost_usd']:.4f} εκτίμηση")}
            {quality_tile}
          </section>
        </header>
        <main>
          <div class="layout">
            <div>
              <section class="card">
                <h2>Ταυτότητα φακέλου</h2>
                <div class="subsection">
                  <div class="section-head"><h3>Διευθύνσεις του ακινήτου</h3><span>βάσει επαναλήψεων και fuzzy matching</span></div>
                  {_v2_cluster_rows(address_variants, job_id=job_id, token=token, limit=10)}
                </div>
                {('<div class="subsection"><div class="section-head"><h3>Πιθανές άλλες διευθύνσεις / ιστορικές αναφορές</h3><span>θέλουν ανθρώπινη κρίση</span></div>' + _v2_cluster_rows(possible_other, job_id=job_id, token=token, limit=6) + '</div>') if possible_other else ''}
                <div class="subsection">
                  <div class="section-head"><h3>Άδειες και αναγνωριστικά</h3><span>{html.escape(str(len(permits)))} ομάδες</span></div>
                  {_v2_cluster_rows(permits, job_id=job_id, token=token, limit=10)}
                </div>
                <div class="subsection">
                  <div class="section-head"><h3>Πρόσωπα και ρόλοι</h3><span>{html.escape(str(sum(len(g.get('items') or []) for g in people if isinstance(g, dict))))} ομάδες</span></div>
                  {_render_people_v2(people, job_id, token)}
                </div>
              </section>
            </div>
            <aside>
              <section id="issues" class="card">
                <h2>Θέματα προς έλεγχο</h2>
                {_render_issues_v2(issues, job_id, token, packet)}
              </section>
              <section class="card">
                <h2>Χάρτης εγγράφων</h2>
                {_render_document_groups_v2(documents)}
                <div class="subsection">
                  <div class="section-head"><h3>Αρχεία πηγής</h3><span>{html.escape(str(len(source_files)))} αρχεία</span></div>
                  {_render_v2_source_files(source_files)}
                </div>
                <div class="subsection">
                  <div class="section-head"><h3>Σφάλματα εξαγωγής</h3><span>{html.escape(str(len(extraction_errors)))}</span></div>
                  {_render_v2_errors(extraction_errors)}
                </div>
              </section>
            </aside>
          </div>
          {('<details><summary>Σύνοψη AI: ευρήματα</summary>' + _render_v2_list(key_findings) + '</details>') if key_findings else ''}
          {('<details><summary>Σύνοψη AI: προτεραιότητες ελέγχου</summary>' + _render_v2_list(review_priorities) + '</details>') if review_priorities else ''}
          {('<details><summary>Triage σελίδων</summary><p class="muted">Σύνολο: ' + html.escape(str(triage_summary.get('pages_total', 0))) + ' · Επιλέχθηκαν: ' + html.escape(str(triage_summary.get('pages_selected', 0))) + ' · Παραλείφθηκαν: ' + html.escape(str(triage_summary.get('pages_skipped', 0))) + '</p>' + (_render_v2_list(triage_summary.get('skip_reasons') or []) if triage_summary.get('skip_reasons') else '') + '</details>') if triage_summary else ''}
        </main>
        <div class="lightbox" id="lightbox" role="dialog" aria-modal="true" aria-label="Προεπισκόπηση σελίδας">
          <div class="lightbox-meta" id="lightbox-meta"></div>
          <button class="lightbox-close" type="button" aria-label="Κλείσιμο">×</button>
          <img id="lightbox-img" alt="" />
        </div>
        <script>
          (function() {{
            var box = document.getElementById('lightbox');
            var img = document.getElementById('lightbox-img');
            var meta = document.getElementById('lightbox-meta');
            function open(src, label) {{
              img.src = src;
              meta.textContent = label || '';
              box.classList.add('open');
              document.body.style.overflow = 'hidden';
            }}
            function close() {{
              box.classList.remove('open');
              img.src = '';
              document.body.style.overflow = '';
            }}
            document.addEventListener('click', function(ev) {{
              var a = ev.target.closest && ev.target.closest('a.ev');
              if (!a) return;
              if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.button === 1) return;
              ev.preventDefault();
              open(a.dataset.full || a.href, a.dataset.label || '');
            }});
            box.addEventListener('click', function(ev) {{
              if (ev.target === box || ev.target.classList.contains('lightbox-close')) close();
            }});
            document.addEventListener('keydown', function(ev) {{
              if (ev.key === 'Escape' && box.classList.contains('open')) close();
            }});
          }})();
        </script>
      </body>
    </html>
    """


def _render_packet_review(job_id: str, packet: dict[str, Any], token: str = "", lang: str = "el") -> str:
    lang = "el" if lang == "el" else "en"
    labels = _review_labels(lang)
    token_query_values = {"lang": lang}
    if token:
        token_query_values["token"] = token
    token_query = f"?{html.escape(urlencode(token_query_values))}"
    token_only_query = f"?{html.escape(urlencode({'token': token}))}" if token else ""
    executive = packet.get("executive_summary") if isinstance(packet.get("executive_summary"), dict) else {}
    totals = packet.get("totals") if isinstance(packet.get("totals"), dict) else {}
    check_summary = packet.get("check_summary") if isinstance(packet.get("check_summary"), dict) else {}
    checks_by_status = check_summary.get("checks_by_status") if isinstance(check_summary.get("checks_by_status"), dict) else {}
    cost = packet.get("cost_summary") if isinstance(packet.get("cost_summary"), dict) else {}
    source_files = packet.get("source_files") if isinstance(packet.get("source_files"), list) else []
    errors = packet.get("errors") if isinstance(packet.get("errors"), list) else []
    headline = html.escape(str(executive.get("headline") or "Packet review"))
    created_at = html.escape(str(packet.get("created_at") or ""))
    provider = html.escape(str(packet.get("provider_config", {}).get("provider") or packet.get("provider") or ""))
    model = html.escape(str(packet.get("provider_config", {}).get("model") or packet.get("model") or ""))
    status_counts = " ".join(
        f"{_status_badge(status, lang)} <strong>{html.escape(str(count))}</strong>"
        for status, count in checks_by_status.items()
    )
    file_rows = "".join(
        f"<li><span>{html.escape(_basename(file.get('source_file') if isinstance(file, dict) else file))}</span><span class='muted'>{html.escape(str(file.get('page_count', '?') if isinstance(file, dict) else '?'))} {html.escape(labels['pages_word'])}</span></li>"
        for file in source_files
    )
    error_cards = "".join(
        f"<article class='mini-card'><strong>{html.escape(str(error.get('page_id') or error.get('source_file') or 'page'))}</strong><p>{html.escape(str(error.get('error') or error))}</p></article>"
        for error in errors
        if isinstance(error, dict)
    ) or f'<p class="muted">{html.escape(labels["no_errors"])}</p>'
    findings = _list_items(executive.get("key_findings"), limit=8, empty_text=labels["none"])
    priorities = _list_items(executive.get("review_priorities"), limit=8, empty_text=labels["none"])
    estimated_cost = float(cost.get("estimated_cost_usd") or 0.0)
    lang_switch = "en" if lang == "el" else "el"
    lang_switch_label = "English" if lang == "el" else "Ελληνικά"
    return f"""
    <html>
      <head>
        <title>arch-ocr review {html.escape(job_id)}</title>
        <style>
          :root {{ --bg:#fafaf7; --paper:#fff; --paper2:#f6f4ee; --line:#e7e3d8; --ink:#14181f; --muted:#68707c; --accent:#1a2540; }}
          * {{ box-sizing:border-box; }}
          body {{ margin:0; background:var(--bg); color:var(--ink); font-family:Inter, ui-sans-serif, system-ui, sans-serif; line-height:1.5; }}
          header {{ position:sticky; top:0; z-index:1; background:rgba(250,250,247,.96); border-bottom:1px solid var(--line); padding:22px 36px; }}
          main {{ padding:28px 36px 48px; }}
          h1 {{ margin:0 0 8px; font-family:Georgia, serif; font-size:30px; line-height:1.15; }}
          h2 {{ margin:0 0 14px; font-size:15px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); }}
          h3 {{ margin:0; font-size:16px; }}
          a {{ color:var(--accent); }}
          code,.mono {{ font-family:"JetBrains Mono", ui-monospace, monospace; font-size:12px; }}
          .muted {{ color:var(--muted); }}
          .strong {{ font-weight:700; color:var(--ink); }}
          .actions {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }}
          .btn {{ display:inline-flex; align-items:center; height:34px; padding:0 12px; border:1px solid var(--line); border-radius:6px; background:var(--paper); text-decoration:none; font-weight:600; font-size:13px; }}
          .btn-primary {{ background:var(--accent); color:white; border-color:var(--accent); }}
          .grid {{ display:grid; grid-template-columns:1.15fr .85fr; gap:22px; align-items:start; }}
          .section {{ margin-bottom:22px; }}
          .card,.mini-card {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:16px; }}
          .mini-card {{ margin-bottom:8px; }}
          .check-card {{ margin-bottom:12px; }}
          .card-head {{ display:flex; gap:10px; align-items:center; margin-bottom:8px; }}
          .stats {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:10px; margin:18px 0; }}
          .stat {{ background:var(--paper); border:1px solid var(--line); border-radius:8px; padding:14px; }}
          .stat .value {{ font-size:22px; font-weight:700; }}
          .coverage-card {{ margin-top:-8px; }}
          .coverage-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; }}
          .coverage-grid div {{ border:1px solid var(--line); border-radius:7px; background:var(--paper2); padding:12px; }}
          .coverage-grid strong {{ display:block; font-size:20px; }}
          .coverage-grid span {{ display:block; color:var(--muted); font-size:13px; }}
          .coverage-warn {{ border-color:#e2bd5b !important; background:#fff7db !important; }}
          .coverage-ok {{ border-color:#b7ddc8 !important; background:#eef8f2 !important; }}
          .badge {{ display:inline-flex; align-items:center; border-radius:999px; padding:2px 8px; font-size:12px; font-weight:700; border:1px solid var(--line); background:var(--paper2); }}
          .badge-pass {{ color:#27724f; background:#eef8f2; }}
          .badge-warning {{ color:#926800; background:#fff7db; }}
          .badge-fail {{ color:#a43a2d; background:#fff0ed; }}
          .badge-unknown {{ color:#626b76; background:#f1f2f3; }}
          .refs {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }}
          .refs code {{ background:var(--paper2); border:1px solid var(--line); border-radius:999px; padding:2px 7px; }}
          .evidence-card {{ width:132px; display:block; border:1px solid var(--line); border-radius:7px; overflow:hidden; background:var(--paper2); text-decoration:none; color:var(--ink); }}
          .evidence-card img {{ width:100%; height:92px; object-fit:cover; object-position:top center; display:block; background:#fff; border-bottom:1px solid var(--line); }}
          .evidence-card span {{ display:block; padding:6px; }}
          .evidence-card code {{ display:block; border:0; background:transparent; padding:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
          .evidence-card small {{ display:block; color:var(--muted); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; margin-top:2px; }}
          ul {{ margin:8px 0 0; padding-left:20px; }}
          .file-list {{ padding:0; list-style:none; }}
          .file-list li {{ display:flex; justify-content:space-between; gap:12px; border-bottom:1px solid var(--line); padding:8px 0; }}
          @media (max-width: 900px) {{ .grid,.stats {{ grid-template-columns:1fr; }} header,main {{ padding-left:18px; padding-right:18px; }} }}
        </style>
      </head>
      <body>
        <header>
          <div class="muted mono">arch-ocr {html.escape(labels['title_prefix'])} · {html.escape(job_id)}</div>
          <h1>{headline}</h1>
          <div class="muted">{created_at} · {provider} · {model}</div>
          {f'<p class="muted">{html.escape(labels["language_note"])}</p>' if labels["language_note"] else ''}
          <div class="actions">
            <a class="btn btn-primary" href="/jobs/{html.escape(job_id)}/report{token_only_query}">{html.escape(labels['markdown'])}</a>
            <a class="btn" href="/jobs/{html.escape(job_id)}/review-v2{token_only_query}">Architect review</a>
            <a class="btn" href="/jobs/{html.escape(job_id)}/packet{token_only_query}">{html.escape(labels['json'])}</a>
            <a class="btn" href="/jobs/{html.escape(job_id)}/review?{html.escape(urlencode({'token': token, 'lang': lang_switch}))}">{lang_switch_label}</a>
            <a class="btn" href="/design/arch-ocr.html?screen=job&job={html.escape(job_id)}">{html.escape(labels['back'])}</a>
            <a class="btn" href="/admin{token_only_query}">{html.escape(labels['admin'])}</a>
          </div>
        </header>
        <main>
          <section class="stats">
            <div class="stat"><div class="muted">{html.escape(labels['pages'])}</div><div class="value">{html.escape(str(totals.get("pages_extracted", 0)))} / {html.escape(str(totals.get("pages_selected", totals.get("pages_triaged", 0))))}</div></div>
            <div class="stat"><div class="muted">{html.escape(labels['fields'])}</div><div class="value">{html.escape(str(totals.get("field_count", 0)))}</div></div>
            <div class="stat"><div class="muted">{html.escape(labels['checks'])}</div><div class="value">{html.escape(str(check_summary.get("check_count", 0)))}</div><div>{status_counts}</div></div>
            <div class="stat"><div class="muted">{html.escape(labels['cost'])}</div><div class="value">${estimated_cost:.6f}</div></div>
          </section>
          {_render_page_coverage(totals, labels)}
          <div class="grid">
            <div>
              <section class="section card">
                <h2>{html.escape(labels['executive'])}</h2>
                {findings}
              </section>
              <section class="section">
                <h2>{html.escape(labels['validation'])}</h2>
                {_render_check_cards(packet.get("checks"), packet=packet, job_id=job_id, token=token, lang=lang)}
              </section>
              <section class="section card">
                <h2>{html.escape(labels['priorities'])}</h2>
                {priorities}
              </section>
            </div>
            <aside>
              <section class="section card">
                <h2>{html.escape(labels['source_files'])}</h2>
                <ul class="file-list">{file_rows}</ul>
              </section>
              <section class="section card">
                <h2>{html.escape(labels['fuzzy'])}</h2>
                {_render_fuzzy_groups(packet.get("fuzzy_groups"), lang)}
              </section>
              <section class="section card">
                <h2>{html.escape(labels['clusters'])}</h2>
                {_render_cluster_cards(packet.get("clusters"), lang)}
              </section>
              <section class="section card">
                <h2>{html.escape(labels['errors'])}</h2>
                {error_cards}
              </section>
            </aside>
          </div>
        </main>
      </body>
    </html>
    """


def get_report(job_id: str, request: Request) -> str:
    _require_demo_access(request)
    return _render_packet_report(_load_analyzed_packet(job_id))


def get_review(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    lang = request.query_params.get("lang") or "el"
    _require_demo_access(request)
    return _render_packet_review(job_id, _load_analyzed_packet(job_id), token, lang)


def get_review_v2(job_id: str, request: Request) -> str:
    token = request.query_params.get("token") or request.query_params.get("admin_token") or ""
    _require_demo_access(request)
    return _render_packet_review_v2(job_id, _load_analyzed_packet(job_id), token)


def _render_page_pixmap(job_id: str, field_ref: str, scale: float) -> bytes:
    packet = _load_analyzed_packet(job_id)
    field = _field_index_by_ref(packet).get(field_ref)
    if not field:
        raise HTTPException(status_code=404, detail="Field reference not found.")
    source_file = _safe_job_source_path(job_id, field.get("source_file"))
    page_number = int(field.get("page_number") or 0)
    if page_number < 1:
        raise HTTPException(status_code=404, detail="Field page not found.")
    try:
        with fitz.open(source_file) as document:
            if page_number > len(document):
                raise HTTPException(status_code=404, detail="Page not found.")
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return pixmap.tobytes("jpeg" if scale < 1.0 else "png")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_redact_secrets(str(exc))) from exc


def get_page_thumbnail(job_id: str, request: Request, field_ref: str) -> Response:
    _require_demo_access(request)
    return Response(content=_render_page_pixmap(job_id, field_ref, 0.35), media_type="image/jpeg")


def get_page_image(job_id: str, request: Request, field_ref: str) -> Response:
    _require_demo_access(request)
    return Response(
        content=_render_page_pixmap(job_id, field_ref, 2.0),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )
