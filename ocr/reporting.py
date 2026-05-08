"""Markdown report generation from packet artifacts."""

from __future__ import annotations

from typing import Any

from ocr.costs import _as_float, _format_money
from ocr.clustering import _iter_packet_fields


def _format_count_map(values: dict[str, Any]) -> str:
    if not values:
        return "- None"
    lines = []
    for key, value in sorted(values.items(), key=lambda item: str(item[0])):
        lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _format_mentions(mentions: list[dict[str, Any]], limit: int = 4) -> str:
    lines: list[str] = []
    for mention in mentions[:limit]:
        page_id = mention.get("page_id", "unknown page")
        label = mention.get("label_text") or "unlabeled"
        confidence = mention.get("confidence") or "unknown"
        field_ref = mention.get("field_ref") or "no field_ref"
        lines.append(f"  - {page_id}: {label} ({confidence}) [{field_ref}]")
    if len(mentions) > limit:
        lines.append(f"  - ... {len(mentions) - limit} more mentions")
    return "\n".join(lines) if lines else "  - No mentions"


def _top_clusters(
    packet: dict[str, Any],
    *,
    repeated_only: bool,
    field_types: set[str] | None = None,
    limit: int = 12,
) -> list[dict[str, Any]]:
    clusters = [cluster for cluster in packet.get("clusters", []) if isinstance(cluster, dict)]
    if repeated_only:
        clusters = [cluster for cluster in clusters if int(cluster.get("mention_count", 0)) > 1]
    if field_types is not None:
        clusters = [cluster for cluster in clusters if cluster.get("field_type") in field_types]
    return sorted(
        clusters,
        key=lambda cluster: (
            -int(cluster.get("mention_count", 0)),
            str(cluster.get("field_type", "")),
            str(cluster.get("canonical_value", "")),
        ),
    )[:limit]


def _collect_page_warnings(packet: dict[str, Any], limit: int = 20) -> list[str]:
    warnings: list[str] = []
    for artifact in packet.get("page_extractions", []):
        if not isinstance(artifact, dict):
            continue
        extraction = artifact.get("extraction", {})
        if not isinstance(extraction, dict):
            continue
        for page in extraction.get("page_results", []):
            if not isinstance(page, dict):
                continue
            page_id = page.get("page_id", "unknown page")
            for warning in page.get("page_warnings", []):
                warnings.append(f"- {page_id}: {warning}")
                if len(warnings) >= limit:
                    return warnings
    return warnings


def _format_check(check: dict[str, Any]) -> list[str]:
    lines = [
        f"### {check.get('title', check.get('check_id', 'Check'))}",
        "",
        f"- Status: {check.get('status', 'unknown')}",
        f"- Check ID: {check.get('check_id', 'unknown')}",
        f"- Summary: {check.get('summary', '')}",
    ]

    evidence_refs = check.get("evidence_refs", [])
    if evidence_refs:
        lines.append("- Evidence refs:")
        for ref in evidence_refs[:8]:
            lines.append(f"  - {ref}")
        if len(evidence_refs) > 8:
            lines.append(f"  - ... {len(evidence_refs) - 8} more refs")

    details = check.get("details", [])
    if details:
        lines.append("- Details:")
        for detail in details[:8]:
            lines.append(f"  - {detail}")
        if len(details) > 8:
            lines.append(f"  - ... {len(details) - 8} more details")

    lines.append("")
    return lines


def _format_fuzzy_group(group: dict[str, Any]) -> list[str]:
    values = [
        str(value)
        for value in group.get("canonical_values", [])
        if isinstance(value, str) and value.strip()
    ]
    lines = [
        f"### {group.get('field_type', 'unknown')}: {group.get('fuzzy_group_id', 'unknown')}",
        "",
        f"- Clusters: {', '.join(group.get('cluster_ids', []))}",
        f"- Mentions: {group.get('mention_count', 0)}",
        f"- Source pages: {group.get('source_page_count', 0)}",
        "- Values:",
    ]
    for value in values[:8]:
        lines.append(f"  - {value}")
    if len(values) > 8:
        lines.append(f"  - ... {len(values) - 8} more values")
    lines.append("")
    return lines


def _render_packet_report(packet: dict[str, Any]) -> str:
    totals = packet.get("totals", {})
    cost_summary = packet.get("cost_summary", {})
    cluster_summary = packet.get("cluster_summary", {})
    fuzzy_group_summary = packet.get("fuzzy_group_summary", {})
    check_summary = packet.get("check_summary", {})
    executive_summary = packet.get("executive_summary", {})
    checks = [check for check in packet.get("checks", []) if isinstance(check, dict)]
    repeated_clusters = _top_clusters(packet, repeated_only=True)
    identity_clusters = _top_clusters(
        packet,
        repeated_only=False,
        field_types={
            "person_name",
            "owner",
            "applicant",
            "engineer",
            "architect",
            "address",
            "property_id",
            "permit_number",
            "office",
        },
        limit=20,
    )
    page_warnings = _collect_page_warnings(packet)

    lines = [
        "# Packet Evidence Report",
        "",
        "This report is generated from extracted page evidence and deterministic clusters only. It is not a legal or engineering opinion.",
        "",
        "## Processing Summary",
        "",
        f"- Packet ID: {packet.get('packet_id', 'unknown')}",
        f"- Source path: {packet.get('input_path', 'unknown')}",
        f"- Files: {totals.get('files', 0)}",
        f"- Pages triaged: {totals.get('pages_triaged', 0)}",
        f"- Pages extracted: {totals.get('pages_extracted', 0)}",
        f"- Failed pages: {totals.get('pages_failed', 0)}",
        f"- Extracted fields: {totals.get('field_count', 0)}",
        f"- Handwritten fields: {totals.get('handwritten_field_count', 0)}",
        f"- Stamped fields: {totals.get('stamped_field_count', 0)}",
        f"- Signature fields: {totals.get('signature_field_count', 0)}",
        f"- Low-confidence fields: {totals.get('low_confidence_field_count', 0)}",
        "",
        "## Cost Summary",
        "",
        f"- Provider: {cost_summary.get('provider', 'unknown') if isinstance(cost_summary, dict) else 'unknown'}",
        f"- Model: {cost_summary.get('model', 'unknown') if isinstance(cost_summary, dict) else 'unknown'}",
        f"- Calls with usage: {cost_summary.get('calls_with_usage', 0) if isinstance(cost_summary, dict) else 0}",
        f"- Calls without usage: {cost_summary.get('calls_without_usage', 0) if isinstance(cost_summary, dict) else 0}",
        f"- Input tokens: {cost_summary.get('input_tokens', 0) if isinstance(cost_summary, dict) else 0}",
        f"- Output tokens: {cost_summary.get('output_tokens', 0) if isinstance(cost_summary, dict) else 0}",
        f"- Total tokens: {cost_summary.get('total_tokens', 0) if isinstance(cost_summary, dict) else 0}",
        f"- Estimated cost: {_format_money(cost_summary.get('estimated_cost_usd') if isinstance(cost_summary, dict) else None)}",
        f"- Reported cost: {_format_money(cost_summary.get('reported_cost_usd') if isinstance(cost_summary, dict) else None)}",
        "",
        "Cost notes:",
        "",
        _format_count_map(
            {
                str(index): note
                for index, note in enumerate(
                    cost_summary.get("notes", []) if isinstance(cost_summary, dict) else [],
                    start=1,
                )
            }
        ),
        "",
        "## Executive Summary",
        "",
        str(executive_summary.get("headline", "No executive summary available."))
        if isinstance(executive_summary, dict)
        else "No executive summary available.",
        "",
        "Key findings:",
        "",
        _format_count_map(
            {
                str(index): finding
                for index, finding in enumerate(
                    executive_summary.get("key_findings", [])
                    if isinstance(executive_summary, dict)
                    else [],
                    start=1,
                )
            }
        ),
        "",
        "Review priorities:",
        "",
        _format_count_map(
            {
                str(index): priority
                for index, priority in enumerate(
                    executive_summary.get("review_priorities", [])
                    if isinstance(executive_summary, dict)
                    else [],
                    start=1,
                )
            }
        ),
        "",
        "## Cluster Summary",
        "",
        f"- Clusters: {cluster_summary.get('cluster_count', 0)}",
        f"- Repeated clusters: {cluster_summary.get('repeated_cluster_count', 0)}",
        f"- Fuzzy near-match groups: {fuzzy_group_summary.get('fuzzy_group_count', 0)}",
        "",
        "Clusters by field type:",
        "",
        _format_count_map(cluster_summary.get("clusters_by_field_type", {})),
        "",
        "Repeated clusters by field type:",
        "",
        _format_count_map(cluster_summary.get("repeated_clusters_by_field_type", {})),
        "",
        "Identifier clusters by subtype:",
        "",
        _format_count_map(cluster_summary.get("identifier_clusters_by_subtype", {})),
        "",
        "Repeated identifier clusters by subtype:",
        "",
        _format_count_map(cluster_summary.get("repeated_identifier_clusters_by_subtype", {})),
        "",
        "Fuzzy near-match groups by field type:",
        "",
        _format_count_map(fuzzy_group_summary.get("fuzzy_groups_by_field_type", {})),
        "",
        "## Validation Checks",
        "",
        f"- Checks: {check_summary.get('check_count', len(checks))}",
        "",
        "Checks by status:",
        "",
        _format_count_map(check_summary.get("checks_by_status", {})),
        "",
    ]

    for check in checks:
        lines.extend(_format_check(check))

    fuzzy_groups = [
        group
        for group in packet.get("fuzzy_groups", [])
        if isinstance(group, dict)
    ]
    lines.extend(["## Fuzzy Near-Match Groups", ""])
    if fuzzy_groups:
        for group in fuzzy_groups[:12]:
            lines.extend(_format_fuzzy_group(group))
        if len(fuzzy_groups) > 12:
            lines.extend([f"... {len(fuzzy_groups) - 12} more fuzzy groups", ""])
    else:
        lines.extend(["No fuzzy near-match groups found.", ""])

    lines.extend(
        [
        "## Key Repeated Evidence",
        "",
        ]
    )

    if repeated_clusters:
        for cluster in repeated_clusters:
            lines.extend(
                [
                    f"### {cluster.get('field_type', 'unknown')}: {cluster.get('canonical_value', 'uncertain')}",
                    "",
                    f"- Cluster ID: {cluster.get('cluster_id')}",
                    f"- Mentions: {cluster.get('mention_count', 0)}",
                    f"- Source pages: {cluster.get('source_page_count', 0)}",
                    f"- Confidence: {cluster.get('confidence_counts', {})}",
                    "",
                    _format_mentions(cluster.get("mentions", [])),
                    "",
                ]
            )
    else:
        lines.extend(["No repeated clusters found.", ""])

    lines.extend(["## Identity And Property Evidence", ""])
    for cluster in identity_clusters:
        lines.extend(
            [
                f"### {cluster.get('field_type', 'unknown')}: {cluster.get('canonical_value', 'uncertain')}",
                "",
                f"- Cluster ID: {cluster.get('cluster_id')}",
                f"- Mentions: {cluster.get('mention_count', 0)}",
                f"- Source pages: {cluster.get('source_page_count', 0)}",
                "",
                _format_mentions(cluster.get("mentions", []), limit=3),
                "",
            ]
        )

    lines.extend(["## Errors And Warnings", ""])
    errors = packet.get("errors", [])
    if errors:
        for error in errors:
            if not isinstance(error, dict):
                continue
            lines.append(
                f"- {error.get('page_id', 'unknown page')}: {error.get('error', 'unknown error')}"
            )
    else:
        lines.append("- No page extraction errors recorded.")

    if page_warnings:
        lines.extend(["", "Page warnings:", ""])
        lines.extend(page_warnings)

    lines.extend(
        [
            "",
            "## Recommended Review",
            "",
            "- Review all handwritten fields against the source PDFs.",
            "- Confirm repeated names, addresses, property identifiers, and permit numbers across pages.",
            "- Inspect singleton identity/property clusters because they may indicate missing or inconsistent packet evidence.",
            "- Treat technical-value clusters as evidence candidates until domain-specific validation checks are added.",
            "",
        ]
    )
    return "\n".join(lines)
