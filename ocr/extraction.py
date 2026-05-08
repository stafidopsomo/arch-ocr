"""Core extraction flow: render -> providers -> schema -> artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ocr.costs import _normalize_provider_usage
from ocr.providers import _run_provider_request
from ocr.render import RenderedPage
from ocr.schema import JSON_EXTRACTION_PROMPT, _parse_json_response, _validate_extraction_schema

DEFAULT_PROMPT = """You are reviewing Greek property, architecture, or legal-building documents.

Return concise, practical findings in markdown with these sections:

1. Document Summary
2. Key Visible Fields And Values
3. Handwritten Or Stamped Content
4. Missing Or Unclear Information
5. Recommended Next Checks

If a value is uncertain, write "uncertain" and explain why. Do not invent values.
"""


def _index_page_triage(page_triage: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(page["page_number"]): page
        for page in page_triage
        if isinstance(page.get("page_number"), int)
    }


def _enrich_extraction_with_context(
    *,
    extraction: dict[str, Any],
    input_path: Path,
    page_triage: list[dict[str, Any]],
) -> None:
    triage_by_page = _index_page_triage(page_triage)

    for page_result in extraction.get("page_results", []):
        if not isinstance(page_result, dict):
            continue
        page_number = page_result.get("page_number")
        if not isinstance(page_number, int):
            continue

        triage = triage_by_page.get(page_number, {})
        page_result.setdefault("source_file", str(input_path))
        page_result.setdefault("page_id", triage.get("page_id", f"{input_path.stem}:p{page_number}"))
        page_result.setdefault("page_kind", triage.get("page_kind", "unknown"))
        page_result.setdefault("embedded_text_chars", triage.get("embedded_text_chars", 0))
        page_result.setdefault("image_count", triage.get("image_count", 0))
        page_result.setdefault("annotation_count", triage.get("annotation_count", 0))
        page_result.setdefault("ink_ratio", triage.get("ink_ratio"))
        page_result.setdefault("needs_vision", triage.get("needs_vision", True))
        page_result.setdefault("needs_text_layer", triage.get("needs_text_layer", False))

        fields = page_result.get("fields")
        if not isinstance(fields, list):
            page_result["fields"] = []
            continue

        for index, field in enumerate(fields, start=1):
            if not isinstance(field, dict):
                continue
            field.setdefault("field_id", f"p{page_number}-f{index}")
            field.setdefault("field_ref", f"{page_result['page_id']}:{field['field_id']}")
            field.setdefault("source_file", str(input_path))
            field.setdefault("page_number", page_number)
            field.setdefault("page_id", page_result["page_id"])
            field.setdefault("page_kind", page_result["page_kind"])

            evidence = field.get("evidence")
            if not isinstance(evidence, dict):
                evidence = {}
                field["evidence"] = evidence
            evidence.setdefault("source_file", str(input_path))
            evidence.setdefault("page_number", page_number)
            evidence.setdefault("page_id", page_result["page_id"])
            evidence.setdefault("page_kind", page_result["page_kind"])


def _summarize_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    page_results = [
        page for page in extraction.get("page_results", []) if isinstance(page, dict)
    ]
    fields: list[dict[str, Any]] = []
    for page in page_results:
        fields.extend(
            field for field in page.get("fields", []) if isinstance(field, dict)
        )

    field_types: dict[str, int] = {}
    for field in fields:
        field_type = str(field.get("field_type", "unknown"))
        field_types[field_type] = field_types.get(field_type, 0) + 1

    return {
        "page_count": len(page_results),
        "field_count": len(fields),
        "handwritten_field_count": sum(1 for field in fields if field.get("is_handwritten")),
        "stamped_field_count": sum(1 for field in fields if field.get("is_stamped")),
        "signature_field_count": sum(1 for field in fields if field.get("is_signature")),
        "low_confidence_field_count": sum(
            1 for field in fields if field.get("confidence") == "low"
        ),
        "medium_confidence_field_count": sum(
            1 for field in fields if field.get("confidence") == "medium"
        ),
        "field_types": field_types,
    }


def _build_extraction_artifact(
    *,
    input_path: Path,
    provider: str,
    model: str,
    pages: list[RenderedPage],
    extraction: dict[str, Any],
    page_triage: list[dict[str, Any]],
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    page_numbers = [page.page_number for page in pages]
    extraction.setdefault("schema_version", "page_evidence.v1")
    extraction.setdefault("page_results", [])
    extraction.setdefault("uncertainties", [])
    _enrich_extraction_with_context(
        extraction=extraction,
        input_path=input_path,
        page_triage=page_triage,
    )

    return {
        "artifact_version": "arch_ocr.extraction.v1",
        "source_file": str(input_path),
        "provider": provider,
        "model": model,
        "pages_sent": page_numbers,
        "page_triage": page_triage,
        "extraction_summary": _summarize_extraction(extraction),
        "usage": usage or {},
        "extraction": extraction,
    }


def _run_validated_json_extraction(
    *,
    input_path: Path,
    provider: str,
    api_key: str,
    model: str,
    pages: list[RenderedPage],
    page_triage: list[dict[str, Any]],
    language_hints: str,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if provider == "google-vision":
        raise RuntimeError("Packet JSON extraction requires gemini or openrouter.")

    raw_response, response_text = _run_provider_request(
        provider=provider,
        api_key=api_key,
        model=model,
        prompt=JSON_EXTRACTION_PROMPT,
        pages=pages,
        language_hints=language_hints,
        timeout=timeout,
    )
    extraction = _parse_json_response(response_text)
    _validate_extraction_schema(extraction)
    usage = _normalize_provider_usage(
        provider=provider,
        model=model,
        raw_response=raw_response,
    )
    artifact = _build_extraction_artifact(
        input_path=input_path,
        provider=provider,
        model=model,
        pages=pages,
        extraction=extraction,
        page_triage=page_triage,
        usage=usage,
    )
    return artifact, raw_response


def _render_markdown_report(
    *,
    input_path: Path,
    provider: str,
    model: str,
    page_count: int,
    response_text: str,
) -> str:
    return "\n".join(
        [
            "# Cloud Document Report",
            "",
            f"- Source file: {input_path}",
            f"- Provider: {provider}",
            f"- Model: {model}",
            f"- Pages sent: {page_count}",
            "",
            "## Response",
            "",
            response_text.strip() or "No content returned.",
            "",
        ]
    )
