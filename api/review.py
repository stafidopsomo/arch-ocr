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


def get_page_thumbnail(job_id: str, request: Request, field_ref: str) -> Response:
    _require_demo_access(request)
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
            pixmap = page.get_pixmap(matrix=fitz.Matrix(0.35, 0.35), alpha=False)
            return Response(content=pixmap.tobytes("jpeg"), media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_redact_secrets(str(exc))) from exc
