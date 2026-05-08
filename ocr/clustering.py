"""Fuzzy entity deduplication and field clustering across pages."""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

CLUSTERABLE_FIELD_TYPES = {
    "person_name",
    "address",
    "office",
    "property_id",
    "date",
    "permit_number",
    "engineer",
    "architect",
    "owner",
    "applicant",
    "technical_value",
}
IDENTIFIER_SUBTYPES = {
    "kaek",
    "afm",
    "atak",
    "permit_number",
    "registry_id",
    "unknown_identifier",
}


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFD", value)
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


def _normalize_date_value(value: str) -> str:
    match = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", value)
    if not match:
        return value

    day = int(match.group(1))
    month = int(match.group(2))
    year_text = match.group(3)
    year = int(year_text)
    if len(year_text) == 2:
        year += 2000 if year < 50 else 1900
    return f"{year:04d}-{month:02d}-{day:02d}"


def _normalize_cluster_value(field_type: str, value: str) -> str:
    text = value.strip()
    if not text or text.lower() == "uncertain":
        return ""

    text = _strip_accents(text).upper()
    text = re.sub(r"\s+", " ", text)

    if field_type == "date":
        return _normalize_date_value(text)

    if field_type in {"property_id", "permit_number"}:
        digits = re.sub(r"\D+", "", text)
        return digits or re.sub(r"[^\w]+", "", text)

    if field_type == "address":
        replacements = {
            "ΟΔΟΣ ": "",
            "ΟΔ. ": "",
            "ΟΔ ": "",
            "ΛΕΩΦΟΡΟΣ ": "Λ ",
            "ΛΕΩΦ. ": "Λ ",
            "ΑΡΙΘΜΟΣ ": "",
            "ΑΡ. ": "",
        }
        for source, replacement in replacements.items():
            text = text.replace(source, replacement)

    text = re.sub(r"[^\w\sΑ-ΩΆΈΉΊΌΎΏΪΫ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens_for_fuzzy_value(value: str) -> set[str]:
    text = _strip_accents(value).upper()
    text = re.sub(r"[^\w\sΑ-Ω]+", " ", text)
    stopwords = {
        "ΚΑΙ",
        "ΤΟΥ",
        "ΤΗΣ",
        "ΤΩΝ",
        "ΣΤΗΝ",
        "ΣΤΟ",
        "ΣΤΑ",
        "ΕΠΙ",
        "ΟΔΟΣ",
        "ΟΔΟΥ",
        "ΑΡΙΘΜΟΣ",
        "ΑΡ",
        "ΘΕΣΗ",
        "ΠΡΩΗΝ",
    }
    return {
        token
        for token in re.split(r"\s+", text.strip())
        if len(token) > 1 and token not in stopwords
    }


def _token_set_ratio(left: str, right: str) -> float:
    left_tokens = _tokens_for_fuzzy_value(left)
    right_tokens = _tokens_for_fuzzy_value(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _sequence_ratio(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left, right).ratio()


def _digits_in_value(value: str) -> set[str]:
    return set(re.findall(r"\d+", value))


def _fuzzy_similarity(field_type: str, left: str, right: str) -> float:
    token_ratio = _token_set_ratio(left, right)
    sequence_ratio = _sequence_ratio(left, right)

    if field_type == "address":
        left_digits = _digits_in_value(left)
        right_digits = _digits_in_value(right)
        left_words = {token for token in _tokens_for_fuzzy_value(left) if not token.isdigit()}
        right_words = {token for token in _tokens_for_fuzzy_value(right) if not token.isdigit()}
        shared_words = left_words & right_words
        if not shared_words:
            return min(sequence_ratio, 0.5)
        digit_bonus = 0.12 if left_digits and left_digits == right_digits else 0.0
        digit_penalty = 0.12 if left_digits and right_digits and left_digits != right_digits else 0.0
        if len(shared_words) == 1 and sequence_ratio < 0.7:
            return max(token_ratio, sequence_ratio - digit_penalty)
        return max(token_ratio, sequence_ratio + digit_bonus - digit_penalty)

    if field_type in {"person_name", "owner", "applicant", "engineer", "architect"}:
        return max(token_ratio, sequence_ratio)

    return max(token_ratio, sequence_ratio)


def _fuzzy_threshold(field_type: str) -> float:
    if field_type == "address":
        return 0.64
    if field_type in {"person_name", "owner", "applicant", "engineer", "architect"}:
        return 0.82
    return 0.86


def _iter_packet_fields(packet: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for artifact in packet.get("page_extractions", []):
        if not isinstance(artifact, dict):
            continue
        extraction = artifact.get("extraction", {})
        if not isinstance(extraction, dict):
            continue
        for page in extraction.get("page_results", []):
            if not isinstance(page, dict):
                continue
            for field in page.get("fields", []):
                if isinstance(field, dict):
                    fields.append(field)
    return fields


def _choose_canonical_value(mentions: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for index, mention in enumerate(mentions):
        value = str(mention.get("value", "")).strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
        first_seen.setdefault(value, index)

    if not counts:
        return "uncertain"

    return sorted(counts, key=lambda item: (-counts[item], first_seen[item], item))[0]


def _field_value_for_clustering(field: dict[str, Any], field_type: str) -> str:
    value = str(field.get("value") or "").strip()
    normalized_value = str(field.get("normalized_value") or "").strip()
    if field_type in {"date", "property_id", "permit_number"}:
        return normalized_value or value
    return value or normalized_value


def _text_for_identifier_inference(
    field: dict[str, Any],
    evidence: dict[str, Any],
) -> str:
    parts = [
        str(field.get("label_text") or ""),
        str(field.get("value") or ""),
        str(field.get("normalized_value") or ""),
        str(evidence.get("nearby_text") or ""),
        str(evidence.get("location_hint") or ""),
    ]
    return _strip_accents(" ".join(parts)).upper()


def _infer_identifier_subtype(
    *,
    field_type: str,
    field: dict[str, Any],
    evidence: dict[str, Any],
) -> str | None:
    if field_type not in {"property_id", "permit_number"}:
        return None

    text = _text_for_identifier_inference(field, evidence)
    raw_value = str(field.get("value") or field.get("normalized_value") or "")
    digits = re.sub(r"\D+", "", raw_value)

    if "ΚΑΕΚ" in text or "KAEK" in text:
        return "kaek"

    if "ΑΦΜ" in text or "AFM" in text or "ΦΟΡΟΛΟΓΙΚ" in text:
        return "afm"

    if "ΑΤΑΚ" in text or "ATAK" in text:
        return "atak"

    if field_type == "permit_number":
        return "permit_number"

    if (
        "ΑΡΙΘΜΟΣ ΤΑΥΤΟΤΗΤΑΣ ΑΚΙΝΗΤΟΥ" in text
        or "ΤΑΥΤΟΤΗΤΑΣ ΑΚΙΝΗΤΟΥ" in text
        or "ΚΤΗΜΑΤΟΛΟΓ" in text
        or "ΜΕΤΑΓΡΑΦ" in text
        or "ΥΠΟΘΗΚ" in text
        or "ΒΙΒΛΙΟ" in text
        or "ΤΟΜΟΣ" in text
        or "ΤΙΤΛ" in text
        or "ΠΑΡΑΧΩΡ" in text
    ):
        return "registry_id"

    if len(digits) == 9 and not re.search(r"[./-]", raw_value):
        return "afm"

    return "unknown_identifier"


def _build_field_clusters(packet: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}

    for field in _iter_packet_fields(packet):
        field_type = str(field.get("field_type", "other"))
        if field_type not in CLUSTERABLE_FIELD_TYPES:
            continue

        value = _field_value_for_clustering(field, field_type)
        normalized_value = _normalize_cluster_value(field_type, value)
        if not normalized_value:
            continue

        evidence = field.get("evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        identifier_subtype = _infer_identifier_subtype(
            field_type=field_type,
            field=field,
            evidence=evidence,
        )

        mention = {
            "field_ref": field.get("field_ref"),
            "field_id": field.get("field_id"),
            "field_type": field_type,
            "value": field.get("value"),
            "normalized_value": normalized_value,
            "label_text": field.get("label_text"),
            "confidence": field.get("confidence"),
            "source_file": field.get("source_file"),
            "page_number": field.get("page_number"),
            "page_id": field.get("page_id"),
            "page_kind": field.get("page_kind"),
            "is_handwritten": field.get("is_handwritten", False),
            "is_stamped": field.get("is_stamped", False),
            "is_signature": field.get("is_signature", False),
            "nearby_text": evidence.get("nearby_text"),
            "location_hint": evidence.get("location_hint"),
        }
        if identifier_subtype:
            mention["identifier_subtype"] = identifier_subtype
        grouped.setdefault((field_type, normalized_value), []).append(mention)

    clusters: list[dict[str, Any]] = []
    cluster_indexes_by_type: dict[str, int] = {}
    for field_type, normalized_value in sorted(grouped):
        mentions = grouped[(field_type, normalized_value)]
        cluster_indexes_by_type[field_type] = cluster_indexes_by_type.get(field_type, 0) + 1
        cluster_index = cluster_indexes_by_type[field_type]
        values = sorted({str(mention.get("value", "")).strip() for mention in mentions if mention.get("value")})
        confidence_counts: dict[str, int] = {}
        identifier_subtype_counts: dict[str, int] = {}
        for mention in mentions:
            confidence = str(mention.get("confidence", "unknown"))
            confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
            identifier_subtype = mention.get("identifier_subtype")
            if isinstance(identifier_subtype, str):
                identifier_subtype_counts[identifier_subtype] = (
                    identifier_subtype_counts.get(identifier_subtype, 0) + 1
                )

        identifier_subtype = None
        if identifier_subtype_counts:
            identifier_subtype = sorted(
                identifier_subtype_counts,
                key=lambda item: (-identifier_subtype_counts[item], item),
            )[0]

        cluster = {
            "cluster_id": f"{field_type}-{cluster_index}",
            "field_type": field_type,
            "canonical_value": _choose_canonical_value(mentions),
            "normalized_value": normalized_value,
            "mention_count": len(mentions),
            "source_page_count": len({mention.get("page_id") for mention in mentions}),
            "confidence_counts": confidence_counts,
            "values": values,
            "variation_notes": (
                ["Same normalized value appears with multiple surface forms."]
                if len(values) > 1
                else []
            ),
            "mentions": mentions,
        }
        if identifier_subtype:
            cluster["identifier_subtype"] = identifier_subtype
            cluster["identifier_subtype_counts"] = identifier_subtype_counts
        clusters.append(cluster)

    return clusters


def _summarize_clusters(clusters: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    repeated_by_type: dict[str, int] = {}
    identifiers_by_subtype: dict[str, int] = {}
    repeated_identifiers_by_subtype: dict[str, int] = {}
    repeated_clusters = 0
    for cluster in clusters:
        field_type = str(cluster.get("field_type", "unknown"))
        by_type[field_type] = by_type.get(field_type, 0) + 1
        identifier_subtype = cluster.get("identifier_subtype")
        if isinstance(identifier_subtype, str):
            identifiers_by_subtype[identifier_subtype] = (
                identifiers_by_subtype.get(identifier_subtype, 0) + 1
            )
        if int(cluster.get("mention_count", 0)) > 1:
            repeated_clusters += 1
            repeated_by_type[field_type] = repeated_by_type.get(field_type, 0) + 1
            if isinstance(identifier_subtype, str):
                repeated_identifiers_by_subtype[identifier_subtype] = (
                    repeated_identifiers_by_subtype.get(identifier_subtype, 0) + 1
                )

    return {
        "cluster_count": len(clusters),
        "repeated_cluster_count": repeated_clusters,
        "clusters_by_field_type": by_type,
        "repeated_clusters_by_field_type": repeated_by_type,
        "identifier_clusters_by_subtype": identifiers_by_subtype,
        "repeated_identifier_clusters_by_subtype": repeated_identifiers_by_subtype,
    }


def _build_fuzzy_groups(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fuzzy_field_types = {"address", "person_name", "owner", "applicant", "engineer", "architect"}
    groups: list[dict[str, Any]] = []
    group_index = 0

    for field_type in sorted(fuzzy_field_types):
        typed_clusters = [
            cluster
            for cluster in clusters
            if isinstance(cluster, dict) and cluster.get("field_type") == field_type
        ]
        if len(typed_clusters) < 2:
            continue

        parent = list(range(len(typed_clusters)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        pair_scores: dict[tuple[int, int], float] = {}
        threshold = _fuzzy_threshold(field_type)
        for left_index, left_cluster in enumerate(typed_clusters):
            left_value = str(left_cluster.get("normalized_value") or "")
            for right_index in range(left_index + 1, len(typed_clusters)):
                right_cluster = typed_clusters[right_index]
                right_value = str(right_cluster.get("normalized_value") or "")
                score = _fuzzy_similarity(field_type, left_value, right_value)
                if score >= threshold:
                    pair_scores[(left_index, right_index)] = round(score, 3)
                    union(left_index, right_index)

        components: dict[int, list[int]] = {}
        for index in range(len(typed_clusters)):
            components.setdefault(find(index), []).append(index)

        for component in components.values():
            if len(component) < 2:
                continue
            group_index += 1
            group_clusters = [typed_clusters[index] for index in component]
            pair_details = []
            for left_pos, left_index in enumerate(component):
                for right_index in component[left_pos + 1 :]:
                    pair = (min(left_index, right_index), max(left_index, right_index))
                    if pair not in pair_scores:
                        continue
                    pair_details.append(
                        {
                            "left_cluster_id": typed_clusters[left_index].get("cluster_id"),
                            "right_cluster_id": typed_clusters[right_index].get("cluster_id"),
                            "score": pair_scores[pair],
                        }
                    )

            mentions: list[dict[str, Any]] = []
            for cluster in group_clusters:
                mentions.extend(
                    mention
                    for mention in cluster.get("mentions", [])
                    if isinstance(mention, dict)
                )

            groups.append(
                {
                    "fuzzy_group_id": f"fuzzy-{field_type}-{group_index}",
                    "field_type": field_type,
                    "status": "possible_match",
                    "cluster_ids": [str(cluster.get("cluster_id")) for cluster in group_clusters],
                    "canonical_values": [
                        str(cluster.get("canonical_value", "uncertain"))
                        for cluster in group_clusters
                    ],
                    "mention_count": sum(
                        int(cluster.get("mention_count", 0)) for cluster in group_clusters
                    ),
                    "source_page_count": len({mention.get("page_id") for mention in mentions}),
                    "pair_scores": pair_details,
                }
            )

    return groups


def _summarize_fuzzy_groups(groups: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    for group in groups:
        field_type = str(group.get("field_type", "unknown"))
        by_type[field_type] = by_type.get(field_type, 0) + 1
    return {
        "fuzzy_group_count": len(groups),
        "fuzzy_groups_by_field_type": by_type,
    }


def _attach_packet_clusters(packet: dict[str, Any]) -> None:
    clusters = _build_field_clusters(packet)
    fuzzy_groups = _build_fuzzy_groups(clusters)
    packet["clusters"] = clusters
    packet["cluster_summary"] = _summarize_clusters(clusters)
    packet["fuzzy_groups"] = fuzzy_groups
    packet["fuzzy_group_summary"] = _summarize_fuzzy_groups(fuzzy_groups)
    totals = packet.setdefault("totals", {})
    if isinstance(totals, dict):
        totals["cluster_count"] = len(clusters)
        totals["repeated_cluster_count"] = packet["cluster_summary"]["repeated_cluster_count"]
        totals["fuzzy_group_count"] = len(fuzzy_groups)
