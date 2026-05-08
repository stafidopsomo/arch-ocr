"""Presence, consistency, and identifier validation checks."""

from __future__ import annotations

from typing import Any

from ocr.clustering import (
    _attach_packet_clusters,
    _build_field_clusters,
    _iter_packet_fields,
)


def _clusters_by_field_type(packet: dict[str, Any], field_type: str) -> list[dict[str, Any]]:
    return [
        cluster
        for cluster in packet.get("clusters", [])
        if isinstance(cluster, dict) and cluster.get("field_type") == field_type
    ]


def _fuzzy_groups_by_field_type(packet: dict[str, Any], field_type: str) -> list[dict[str, Any]]:
    return [
        group
        for group in packet.get("fuzzy_groups", [])
        if isinstance(group, dict) and group.get("field_type") == field_type
    ]


def _cluster_ids_in_fuzzy_groups(groups: list[dict[str, Any]]) -> set[str]:
    cluster_ids: set[str] = set()
    for group in groups:
        for cluster_id in group.get("cluster_ids", []):
            if isinstance(cluster_id, str):
                cluster_ids.add(cluster_id)
    return cluster_ids


def _identifier_clusters_by_subtype(
    packet: dict[str, Any],
    identifier_subtype: str,
) -> list[dict[str, Any]]:
    return [
        cluster
        for cluster in packet.get("clusters", [])
        if isinstance(cluster, dict)
        and cluster.get("identifier_subtype") == identifier_subtype
    ]


def _all_identifier_clusters(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        cluster
        for cluster in packet.get("clusters", [])
        if isinstance(cluster, dict)
        and isinstance(cluster.get("identifier_subtype"), str)
    ]


def _evidence_refs_from_clusters(
    clusters: list[dict[str, Any]],
    *,
    limit: int = 12,
) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for cluster in clusters:
        for mention in cluster.get("mentions", []):
            if not isinstance(mention, dict):
                continue
            ref = mention.get("field_ref")
            if not isinstance(ref, str) or not ref or ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
            if len(refs) >= limit:
                return refs
    return refs


def _check_record(
    *,
    check_id: str,
    status: str,
    title: str,
    summary: str,
    evidence_refs: list[str] | None = None,
    details: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status,
        "title": title,
        "summary": summary,
        "evidence_refs": evidence_refs or [],
        "details": details or [],
    }


def _build_presence_check(
    packet: dict[str, Any],
    *,
    field_type: str,
    check_id: str,
    title: str,
    missing_summary: str,
    present_summary: str,
) -> dict[str, Any]:
    clusters = _clusters_by_field_type(packet, field_type)
    if not clusters:
        return _check_record(
            check_id=check_id,
            status="unknown",
            title=title,
            summary=missing_summary,
        )

    mention_count = sum(int(cluster.get("mention_count", 0)) for cluster in clusters)
    return _check_record(
        check_id=check_id,
        status="pass",
        title=title,
        summary=present_summary.format(cluster_count=len(clusters), mention_count=mention_count),
        evidence_refs=_evidence_refs_from_clusters(clusters),
    )


def _build_consistency_check(
    packet: dict[str, Any],
    *,
    field_type: str,
    check_id: str,
    title: str,
    no_evidence_summary: str,
    single_summary: str,
    multiple_summary: str,
    fuzzy_review_summary: str | None = None,
) -> dict[str, Any]:
    clusters = _clusters_by_field_type(packet, field_type)
    if not clusters:
        return _check_record(
            check_id=check_id,
            status="unknown",
            title=title,
            summary=no_evidence_summary,
        )

    repeated = [cluster for cluster in clusters if int(cluster.get("mention_count", 0)) > 1]
    values = [str(cluster.get("canonical_value", "uncertain")) for cluster in clusters]
    if len(clusters) == 1:
        cluster = clusters[0]
        return _check_record(
            check_id=check_id,
            status="pass",
            title=title,
            summary=single_summary.format(
                value=cluster.get("canonical_value", "uncertain"),
                mention_count=cluster.get("mention_count", 0),
                page_count=cluster.get("source_page_count", 0),
            ),
            evidence_refs=_evidence_refs_from_clusters(clusters),
            details=values,
        )

    fuzzy_groups = _fuzzy_groups_by_field_type(packet, field_type)
    if fuzzy_groups and fuzzy_review_summary:
        related_cluster_ids = _cluster_ids_in_fuzzy_groups(fuzzy_groups)
        related_count = len(related_cluster_ids)
        details = values + [
            "Possible same-value group: " + " | ".join(group.get("canonical_values", []))
            for group in fuzzy_groups
        ]
        return _check_record(
            check_id=check_id,
            status="warning",
            title=title,
            summary=fuzzy_review_summary.format(
                cluster_count=len(clusters),
                repeated_count=len(repeated),
                fuzzy_group_count=len(fuzzy_groups),
                related_count=related_count,
                unresolved_count=max(len(clusters) - related_count, 0),
            ),
            evidence_refs=_evidence_refs_from_clusters(clusters),
            details=details,
        )

    return _check_record(
        check_id=check_id,
        status="warning",
        title=title,
        summary=multiple_summary.format(
            cluster_count=len(clusters),
            repeated_count=len(repeated),
        ),
        evidence_refs=_evidence_refs_from_clusters(clusters),
        details=values,
    )


def _build_identifier_subtype_check(
    packet: dict[str, Any],
    *,
    identifier_subtype: str,
    check_id: str,
    title: str,
    missing_summary: str,
    single_summary: str,
    multiple_summary: str,
    fail_on_multiple: bool = False,
) -> dict[str, Any]:
    clusters = _identifier_clusters_by_subtype(packet, identifier_subtype)
    if not clusters:
        return _check_record(
            check_id=check_id,
            status="unknown",
            title=title,
            summary=missing_summary,
        )

    values = [str(cluster.get("canonical_value", "uncertain")) for cluster in clusters]
    repeated = [cluster for cluster in clusters if int(cluster.get("mention_count", 0)) > 1]
    if len(clusters) == 1:
        cluster = clusters[0]
        return _check_record(
            check_id=check_id,
            status="pass",
            title=title,
            summary=single_summary.format(
                value=cluster.get("canonical_value", "uncertain"),
                mention_count=cluster.get("mention_count", 0),
                page_count=cluster.get("source_page_count", 0),
            ),
            evidence_refs=_evidence_refs_from_clusters(clusters),
            details=values,
        )

    status = "fail" if fail_on_multiple else "warning"
    return _check_record(
        check_id=check_id,
        status=status,
        title=title,
        summary=multiple_summary.format(
            cluster_count=len(clusters),
            repeated_count=len(repeated),
        ),
        evidence_refs=_evidence_refs_from_clusters(clusters),
        details=values,
    )


def _build_packet_checks(packet: dict[str, Any]) -> list[dict[str, Any]]:
    totals = packet.get("totals", {})
    checks: list[dict[str, Any]] = []

    pages_failed = int(totals.get("pages_failed", 0)) if isinstance(totals, dict) else 0
    pages_extracted = int(totals.get("pages_extracted", 0)) if isinstance(totals, dict) else 0
    pages_selected = int(totals.get("pages_selected", pages_extracted)) if isinstance(totals, dict) else pages_extracted
    if pages_failed == 0 and pages_extracted == pages_selected:
        checks.append(
            _check_record(
                check_id="extraction-completeness",
                status="pass",
                title="Extraction completeness",
                summary=f"All {pages_selected} selected pages were extracted without recorded page errors.",
            )
        )
    else:
        checks.append(
            _check_record(
                check_id="extraction-completeness",
                status="fail" if pages_failed else "warning",
                title="Extraction completeness",
                summary=(
                    f"{pages_extracted} of {pages_selected} selected pages were extracted; "
                    f"{pages_failed} page errors were recorded."
                ),
                details=[
                    str(error.get("page_id", "unknown page"))
                    for error in packet.get("errors", [])
                    if isinstance(error, dict)
                ],
            )
        )

    low_confidence = int(totals.get("low_confidence_field_count", 0)) if isinstance(totals, dict) else 0
    checks.append(
        _check_record(
            check_id="field-confidence",
            status="pass" if low_confidence == 0 else "warning",
            title="Field confidence",
            summary=(
                "No low-confidence fields were extracted."
                if low_confidence == 0
                else f"{low_confidence} low-confidence fields need manual review."
            ),
        )
    )

    handwritten = int(totals.get("handwritten_field_count", 0)) if isinstance(totals, dict) else 0
    checks.append(
        _check_record(
            check_id="handwritten-content",
            status="warning" if handwritten else "pass",
            title="Handwritten content",
            summary=(
                f"{handwritten} handwritten fields were detected and should be checked against source images."
                if handwritten
                else "No handwritten fields were detected in the selected pages."
            ),
        )
    )

    checks.append(
        _build_consistency_check(
            packet,
            field_type="address",
            check_id="address-consistency",
            title="Address consistency",
            no_evidence_summary="No address evidence was extracted.",
            single_summary="One address cluster appears {mention_count} times across {page_count} source pages: {value}.",
            multiple_summary="{cluster_count} address clusters were extracted, with {repeated_count} repeated clusters. Review for possible address variations.",
            fuzzy_review_summary=(
                "{cluster_count} address clusters were extracted; {fuzzy_group_count} fuzzy group(s) "
                "may represent near-duplicate address forms. Review {unresolved_count} cluster(s) "
                "that remain outside those groups."
            ),
        )
    )
    checks.append(
        _build_consistency_check(
            packet,
            field_type="person_name",
            check_id="person-name-consistency",
            title="Person name consistency",
            no_evidence_summary="No person-name evidence was extracted.",
            single_summary="One person-name cluster appears {mention_count} times across {page_count} source pages: {value}.",
            multiple_summary="{cluster_count} person-name clusters were extracted, with {repeated_count} repeated clusters. Review roles and possible extra names.",
            fuzzy_review_summary=(
                "{cluster_count} person-name clusters were extracted; {fuzzy_group_count} fuzzy group(s) "
                "may represent spelling, initial, case, or inflection variants. Review role labels "
                "before treating them as separate people."
            ),
        )
    )
    identifier_clusters = _all_identifier_clusters(packet)
    identifier_counts: dict[str, int] = {}
    for cluster in identifier_clusters:
        subtype = str(cluster.get("identifier_subtype", "unknown_identifier"))
        identifier_counts[subtype] = identifier_counts.get(subtype, 0) + 1
    checks.append(
        _check_record(
            check_id="identifier-classification",
            status="pass" if identifier_clusters else "unknown",
            title="Identifier classification",
            summary=(
                "Identifier clusters were classified by subtype: "
                + ", ".join(f"{key}={value}" for key, value in sorted(identifier_counts.items()))
                if identifier_clusters
                else "No identifier clusters were available for subtype classification."
            ),
            evidence_refs=_evidence_refs_from_clusters(identifier_clusters),
            details=[
                f"{cluster.get('identifier_subtype')}: {cluster.get('canonical_value')}"
                for cluster in identifier_clusters
            ],
        )
    )
    checks.append(
        _build_identifier_subtype_check(
            packet,
            identifier_subtype="kaek",
            check_id="kaek-consistency",
            title="KAEK consistency",
            missing_summary="No KAEK evidence was extracted.",
            single_summary="One KAEK cluster appears {mention_count} times across {page_count} source pages: {value}.",
            multiple_summary="{cluster_count} KAEK clusters were extracted, with {repeated_count} repeated clusters. For a single-property packet this is a clear identifier contradiction unless the packet intentionally contains multiple properties.",
            fail_on_multiple=True,
        )
    )
    checks.append(
        _build_identifier_subtype_check(
            packet,
            identifier_subtype="afm",
            check_id="afm-consistency",
            title="AFM evidence",
            missing_summary="No AFM evidence was extracted.",
            single_summary="One AFM cluster appears {mention_count} times across {page_count} source pages: {value}.",
            multiple_summary="{cluster_count} AFM clusters were extracted, with {repeated_count} repeated clusters. Review whether they refer to different people/entities.",
        )
    )
    checks.append(
        _build_identifier_subtype_check(
            packet,
            identifier_subtype="atak",
            check_id="atak-evidence",
            title="ATAK evidence",
            missing_summary="No ATAK evidence was extracted.",
            single_summary="One ATAK cluster appears {mention_count} times across {page_count} source pages: {value}.",
            multiple_summary="{cluster_count} ATAK clusters were extracted, with {repeated_count} repeated clusters.",
        )
    )
    checks.append(
        _build_identifier_subtype_check(
            packet,
            identifier_subtype="registry_id",
            check_id="registry-identifier-review",
            title="Registry identifier evidence",
            missing_summary="No non-KAEK registry identifier evidence was extracted.",
            single_summary="One registry identifier cluster appears {mention_count} times across {page_count} source pages: {value}.",
            multiple_summary="{cluster_count} registry identifier clusters were extracted, with {repeated_count} repeated clusters. Review their document roles.",
        )
    )
    unknown_identifier_clusters = _identifier_clusters_by_subtype(packet, "unknown_identifier")
    checks.append(
        _check_record(
            check_id="unknown-identifier-review",
            status="warning" if unknown_identifier_clusters else "pass",
            title="Unknown identifier review",
            summary=(
                f"{len(unknown_identifier_clusters)} identifier cluster(s) could not be confidently typed."
                if unknown_identifier_clusters
                else "All extracted identifier clusters received a deterministic subtype."
            ),
            evidence_refs=_evidence_refs_from_clusters(unknown_identifier_clusters),
            details=[
                str(cluster.get("canonical_value", "uncertain"))
                for cluster in unknown_identifier_clusters
            ],
        )
    )
    checks.append(
        _build_consistency_check(
            packet,
            field_type="date",
            check_id="date-consistency",
            title="Date consistency",
            no_evidence_summary="No date evidence was extracted.",
            single_summary="One date cluster appears {mention_count} times across {page_count} source pages: {value}.",
            multiple_summary="{cluster_count} date clusters were extracted, with {repeated_count} repeated clusters. Review whether dates serve different document roles.",
        )
    )
    checks.append(
        _build_presence_check(
            packet,
            field_type="permit_number",
            check_id="permit-number-presence",
            title="Permit number evidence",
            missing_summary="No permit-number evidence was extracted.",
            present_summary="{cluster_count} permit-number cluster(s) with {mention_count} mention(s) were extracted.",
        )
    )
    checks.append(
        _build_presence_check(
            packet,
            field_type="owner",
            check_id="owner-presence",
            title="Owner evidence",
            missing_summary="No explicit owner field was extracted from the selected pages.",
            present_summary="{cluster_count} owner cluster(s) with {mention_count} mention(s) were extracted.",
        )
    )
    checks.append(
        _build_presence_check(
            packet,
            field_type="engineer",
            check_id="engineer-presence",
            title="Engineer evidence",
            missing_summary="No explicit engineer field was extracted from the selected pages.",
            present_summary="{cluster_count} engineer cluster(s) with {mention_count} mention(s) were extracted.",
        )
    )

    signature_count = int(totals.get("signature_field_count", 0)) if isinstance(totals, dict) else 0
    checks.append(
        _check_record(
            check_id="signature-evidence",
            status="pass" if signature_count else "warning",
            title="Signature evidence",
            summary=(
                f"{signature_count} signature field(s) were detected."
                if signature_count
                else "No signature fields were detected in the selected pages."
            ),
            evidence_refs=[
                str(field.get("field_ref"))
                for field in _iter_packet_fields(packet)
                if field.get("is_signature") and field.get("field_ref")
            ],
        )
    )

    stamp_count = int(totals.get("stamped_field_count", 0)) if isinstance(totals, dict) else 0
    checks.append(
        _check_record(
            check_id="stamp-evidence",
            status="pass" if stamp_count else "warning",
            title="Stamp evidence",
            summary=(
                f"{stamp_count} stamped field(s) were detected."
                if stamp_count
                else "No stamped fields were detected in the selected pages."
            ),
            evidence_refs=[
                str(field.get("field_ref"))
                for field in _iter_packet_fields(packet)
                if field.get("is_stamped") and field.get("field_ref")
            ][:12],
        )
    )

    return checks


def _summarize_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: dict[str, int] = {}
    for check in checks:
        status = str(check.get("status", "unknown"))
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "check_count": len(checks),
        "checks_by_status": by_status,
    }


def _checks_with_status(packet: dict[str, Any], status: str) -> list[dict[str, Any]]:
    return [
        check
        for check in packet.get("checks", [])
        if isinstance(check, dict) and check.get("status") == status
    ]


def _attach_packet_checks(packet: dict[str, Any]) -> None:
    if not packet.get("clusters"):
        _attach_packet_clusters(packet)
    checks = _build_packet_checks(packet)
    packet["checks"] = checks
    packet["check_summary"] = _summarize_checks(checks)
    totals = packet.setdefault("totals", {})
    if isinstance(totals, dict):
        totals["check_count"] = len(checks)
