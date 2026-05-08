"""page_evidence.v1 schema, JSON extraction, and field validation."""

from __future__ import annotations

import json
from typing import Any

VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}
VALID_FIELD_TYPES = {
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
    "handwritten_note",
    "stamp",
    "signature",
    "technical_value",
    "other",
}

JSON_EXTRACTION_PROMPT = """Extract page-level evidence from this Greek property, architecture, or building/legal document packet.

Return ONLY valid JSON with this exact top-level schema:
{
  "schema_version": "page_evidence.v1",
  "packet_summary": "short summary of what these page(s) appear to contain",
  "page_results": [
    {
      "page_number": 1,
      "document_type": "building permit | declaration | plan | contract | technical memo | uncertain | other",
      "language": "el | en | mixed | uncertain",
      "printed_text_summary": "short summary of printed text",
      "fields": [
        {
          "field_id": "p1-f1",
          "field_type": "person_name | address | office | property_id | date | permit_number | engineer | architect | owner | applicant | handwritten_note | stamp | signature | technical_value | other",
          "label_text": "printed or nearby words that label the value, or uncertain",
          "value": "detected value, or uncertain",
          "normalized_value": "clean normalized form if safe, else same as value or uncertain",
          "is_handwritten": false,
          "is_stamped": false,
          "is_signature": false,
          "confidence": "high | medium | low",
          "evidence": {
            "nearby_text": "words before/after or around the value",
            "location_hint": "top right | table row | signature area | margin | uncertain"
          },
          "notes": ["uncertainty, variation, or reading notes"]
        }
      ],
      "page_warnings": ["missing, unclear, contradictory, or low-confidence observations"]
    }
  ],
  "uncertainties": ["packet-level uncertainty notes"]
}

Rules:
- Do not include markdown or code fences.
- Do not guess missing values.
- Preserve nearby labels/context, especially for handwritten values.
- Create separate fields for repeated important values.
- Mark uncertain readings as "uncertain" and explain in notes.
- Use page numbers exactly as provided in the prompt.
"""


def _strip_json_wrappers(response_text: str) -> str:
    text = response_text.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_json_response(response_text: str) -> dict[str, Any]:
    cleaned = _strip_json_wrappers(response_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        preview = cleaned[:1000]
        raise RuntimeError(f"Provider did not return valid JSON: {exc}. Preview: {preview}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("Provider JSON must be an object at the top level.")

    return parsed


def _require_key(
    errors: list[str],
    obj: dict[str, Any],
    key: str,
    expected_type: type | tuple[type, ...],
    path: str,
) -> Any:
    if key not in obj:
        errors.append(f"{path}.{key} is missing")
        return None

    value = obj[key]
    if not isinstance(value, expected_type):
        if isinstance(expected_type, tuple):
            expected = " or ".join(t.__name__ for t in expected_type)
        else:
            expected = expected_type.__name__
        errors.append(f"{path}.{key} must be {expected}")
        return None

    return value


def _validate_extraction_schema(extraction: dict[str, Any]) -> None:
    errors: list[str] = []

    schema_version = _require_key(
        errors,
        extraction,
        "schema_version",
        str,
        "extraction",
    )
    if schema_version is not None and schema_version != "page_evidence.v1":
        errors.append("extraction.schema_version must be page_evidence.v1")

    _require_key(errors, extraction, "packet_summary", str, "extraction")
    page_results = _require_key(errors, extraction, "page_results", list, "extraction")
    uncertainties = extraction.get("uncertainties", [])
    if not isinstance(uncertainties, list):
        errors.append("extraction.uncertainties must be a list")

    if isinstance(page_results, list):
        for page_index, page_result in enumerate(page_results):
            page_path = f"extraction.page_results[{page_index}]"
            if not isinstance(page_result, dict):
                errors.append(f"{page_path} must be an object")
                continue

            _require_key(errors, page_result, "page_number", int, page_path)
            _require_key(errors, page_result, "document_type", str, page_path)
            _require_key(errors, page_result, "language", str, page_path)
            _require_key(errors, page_result, "printed_text_summary", str, page_path)
            fields = _require_key(errors, page_result, "fields", list, page_path)
            page_warnings = page_result.get("page_warnings", [])
            if not isinstance(page_warnings, list):
                errors.append(f"{page_path}.page_warnings must be a list")

            if not isinstance(fields, list):
                continue

            for field_index, field in enumerate(fields):
                field_path = f"{page_path}.fields[{field_index}]"
                if not isinstance(field, dict):
                    errors.append(f"{field_path} must be an object")
                    continue

                field_id = _require_key(errors, field, "field_id", str, field_path)
                if isinstance(field_id, str) and not field_id.strip():
                    errors.append(f"{field_path}.field_id cannot be empty")

                field_type = _require_key(errors, field, "field_type", str, field_path)
                if field_type is not None and field_type not in VALID_FIELD_TYPES:
                    field["original_field_type"] = field_type
                    field["field_type"] = "other"
                    notes = field.get("notes", [])
                    if not isinstance(notes, list):
                        notes = []
                    notes.append(f"Provider returned unsupported field_type: {field_type}")
                    field["notes"] = notes

                _require_key(errors, field, "label_text", str, field_path)
                _require_key(errors, field, "value", str, field_path)
                _require_key(errors, field, "normalized_value", str, field_path)
                _require_key(errors, field, "is_handwritten", bool, field_path)
                _require_key(errors, field, "is_stamped", bool, field_path)
                _require_key(errors, field, "is_signature", bool, field_path)

                confidence = _require_key(errors, field, "confidence", str, field_path)
                if confidence is not None and confidence not in VALID_CONFIDENCE_VALUES:
                    errors.append(f"{field_path}.confidence must be high, medium, or low")

                evidence = _require_key(errors, field, "evidence", dict, field_path)
                if isinstance(evidence, dict):
                    _require_key(errors, evidence, "nearby_text", str, f"{field_path}.evidence")
                    _require_key(errors, evidence, "location_hint", str, f"{field_path}.evidence")

                notes = field.get("notes", [])
                if not isinstance(notes, list):
                    errors.append(f"{field_path}.notes must be a list")

    if errors:
        joined = "\n- ".join(errors)
        raise RuntimeError(f"Extraction schema validation failed:\n- {joined}")
