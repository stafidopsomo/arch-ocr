"""
Cloud document-understanding CLI for PDFs and image files.

The project no longer runs local OCR engines or local LLMs. It renders scanned
PDF pages to PNG images, then sends those images to cheap cloud providers:

- Gemini Developer API for vision-LLM extraction and reasoning.
- OpenRouter for model experiments through one API.
- Google Vision API for a low-cost OCR baseline.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import requests
from dotenv import load_dotenv

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
GOOGLE_VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

DEFAULT_PROVIDER = "gemini"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_OPENROUTER_MODEL = "google/gemini-flash-1.5-8b"
DEFAULT_GOOGLE_VISION_MODEL = "document-text-detection"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
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

DEFAULT_PROMPT = """You are reviewing Greek property, architecture, or legal-building documents.

Return concise, practical findings in markdown with these sections:

1. Document Summary
2. Key Visible Fields And Values
3. Handwritten Or Stamped Content
4. Missing Or Unclear Information
5. Recommended Next Checks

If a value is uncertain, write "uncertain" and explain why. Do not invent values.
"""

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


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    image_b64: str


def _parse_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional output path. In normal mode this is markdown; "
            "in --json-mode this is normalized extraction JSON."
        ),
    )


def parse_args() -> argparse.Namespace:
    if len(sys.argv) > 1 and sys.argv[1] == "triage":
        parser = argparse.ArgumentParser(
            description="Run local page triage without cloud API calls.",
        )
        parser.set_defaults(command="triage")
        parser.add_argument(
            "input_path",
            help="Input PDF/image file or folder to triage.",
        )
        parser.add_argument(
            "--max-pages-per-file",
            type=int,
            default=None,
            help="Limit pages per PDF during triage.",
        )
        parser.add_argument(
            "--preview-dpi",
            type=int,
            default=36,
            help="Low-resolution render DPI for blank/ink checks. Default: 36.",
        )
        _parse_common_args(parser)
        return parser.parse_args(sys.argv[2:])

    parser = argparse.ArgumentParser(
        description="Send PDF pages or images to cloud document-understanding providers.",
    )
    parser.set_defaults(command="extract")

    parser.add_argument(
        "input_path",
        nargs="?",
        default="test_inputs/example_page1.pdf",
        help="Input file path (.pdf or image). Defaults to test_inputs/example_page1.pdf.",
    )
    parser.add_argument(
        "--provider",
        choices=["gemini", "openrouter", "google-vision"],
        default=os.getenv("CLOUD_PROVIDER", DEFAULT_PROVIDER),
        help="Cloud provider to use. Default: gemini.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model override. Used by gemini and openrouter.",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt sent to vision LLM providers.",
    )
    parser.add_argument(
        "--json-mode",
        action="store_true",
        help="Use the built-in strict JSON extraction prompt.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="How many PDF pages to send. Default: 1.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Rasterization DPI for PDF pages. Default: 150.",
    )
    parser.add_argument(
        "--language-hints",
        default=os.getenv("GOOGLE_VISION_LANGUAGE_HINTS", "el,en"),
        help="Comma-separated language hints for Google Vision. Default: el,en.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Optional output path. In normal mode this is markdown; "
            "in --json-mode this is normalized extraction JSON."
        ),
    )
    parser.add_argument(
        "--raw-output",
        default=None,
        help="Optional path to store raw API JSON response.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key override for the selected provider.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds. Default: 120.",
    )
    return parser.parse_args()


def _iter_input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        if input_path.name.endswith(":Zone.Identifier"):
            return []
        return [input_path]

    if not input_path.is_dir():
        raise ValueError(f"Input path does not exist: {input_path}")

    files: list[Path] = []
    for path in sorted(input_path.rglob("*")):
        if not path.is_file() or path.name.endswith(":Zone.Identifier"):
            continue
        if path.suffix.lower() == ".pdf" or path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            files.append(path)
    return files


def _pixmap_ink_ratio(page: fitz.Page, preview_dpi: int) -> float:
    if preview_dpi < 18:
        raise ValueError("--preview-dpi must be at least 18.")

    zoom = preview_dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False, colorspace=fitz.csGRAY)
    samples = pix.samples
    if not samples:
        return 0.0

    non_white = sum(1 for value in samples if value < 245)
    return round(non_white / len(samples), 4)


def _page_annotation_count(page: fitz.Page) -> int:
    annotations = page.annots()
    if annotations is None:
        return 0
    return sum(1 for _ in annotations)


def _classify_page_kind(
    *,
    embedded_text_chars: int,
    image_count: int,
    annotation_count: int,
    ink_ratio: float,
) -> tuple[str, bool, bool, str]:
    if embedded_text_chars == 0 and image_count == 0 and ink_ratio < 0.01:
        return ("blank", False, False, "No embedded text, no images, and very low ink ratio.")

    if embedded_text_chars == 0 and image_count > 0:
        return ("scanned", True, False, "No embedded text and page contains image content.")

    if embedded_text_chars > 0 and (image_count > 1 or annotation_count > 0):
        return (
            "mixed",
            True,
            True,
            "Embedded text exists, but images or annotations may contain visual evidence.",
        )

    if embedded_text_chars > 0 and image_count == 1:
        return (
            "mixed",
            True,
            True,
            "Embedded text exists with image content; visual marks may exist.",
        )

    if embedded_text_chars > 0:
        return ("born_digital", False, True, "Meaningful embedded text layer exists.")

    return ("unknown", True, False, "Could not confidently classify page locally.")


def _triage_pdf(pdf_path: Path, max_pages: int | None, preview_dpi: int) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    with fitz.open(pdf_path) as doc:
        page_limit = doc.page_count if max_pages is None else min(max_pages, doc.page_count)
        for page_index in range(page_limit):
            page = doc.load_page(page_index)
            embedded_text = page.get_text().strip()
            images = page.get_images(full=True)
            annotation_count = _page_annotation_count(page)
            ink_ratio = _pixmap_ink_ratio(page, preview_dpi)
            page_kind, needs_vision, needs_text_layer, reason = _classify_page_kind(
                embedded_text_chars=len(embedded_text),
                image_count=len(images),
                annotation_count=annotation_count,
                ink_ratio=ink_ratio,
            )

            pages.append(
                {
                    "page_id": f"{pdf_path.stem}:p{page_index + 1}",
                    "source_file": str(pdf_path),
                    "page_number": page_index + 1,
                    "width_points": round(page.rect.width, 2),
                    "height_points": round(page.rect.height, 2),
                    "embedded_text_chars": len(embedded_text),
                    "embedded_text_preview": " ".join(embedded_text.split())[:300],
                    "image_count": len(images),
                    "annotation_count": annotation_count,
                    "ink_ratio": ink_ratio,
                    "page_kind": page_kind,
                    "needs_vision": needs_vision,
                    "needs_text_layer": needs_text_layer,
                    "possible_handwriting_or_stamp": (
                        "unknown" if needs_vision else "no"
                    ),
                    "reason": reason,
                }
            )

    return {
        "source_file": str(pdf_path),
        "file_type": "pdf",
        "page_count": len(pages),
        "pages": pages,
    }


def _triage_image(image_path: Path, preview_dpi: int) -> dict[str, Any]:
    try:
        pix = fitz.Pixmap(str(image_path))
    except Exception as exc:
        raise RuntimeError(f"Could not read image: {image_path}") from exc

    image_area = max(pix.width * pix.height, 1)
    sample_stride = max(image_area // 200000, 1)
    non_white = 0
    sampled = 0
    components = pix.n
    samples = pix.samples
    for offset in range(0, len(samples), components * sample_stride):
        pixel = samples[offset : offset + min(components, 3)]
        if pixel and sum(pixel) / len(pixel) < 245:
            non_white += 1
        sampled += 1

    ink_ratio = round(non_white / sampled, 4) if sampled else 0.0
    page_kind = "blank" if ink_ratio < 0.01 else "image"

    return {
        "source_file": str(image_path),
        "file_type": "image",
        "page_count": 1,
        "pages": [
            {
                "page_id": f"{image_path.stem}:p1",
                "source_file": str(image_path),
                "page_number": 1,
                "width_pixels": pix.width,
                "height_pixels": pix.height,
                "embedded_text_chars": 0,
                "embedded_text_preview": "",
                "image_count": 1,
                "annotation_count": 0,
                "ink_ratio": ink_ratio,
                "page_kind": page_kind,
                "needs_vision": page_kind != "blank",
                "needs_text_layer": False,
                "possible_handwriting_or_stamp": "unknown" if page_kind != "blank" else "no",
                "reason": "Image input requires vision unless visually blank.",
            }
        ],
    }


def _build_triage_artifact(
    *,
    input_path: Path,
    files: list[Path],
    max_pages_per_file: int | None,
    preview_dpi: int,
) -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    totals = {
        "files": 0,
        "pages": 0,
        "blank_pages": 0,
        "born_digital_pages": 0,
        "mixed_pages": 0,
        "scanned_pages": 0,
        "image_pages": 0,
        "unknown_pages": 0,
        "pages_needing_vision": 0,
        "pages_with_text_layer": 0,
    }

    for path in files:
        if path.suffix.lower() == ".pdf":
            document = _triage_pdf(path, max_pages_per_file, preview_dpi)
        elif path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            document = _triage_image(path, preview_dpi)
        else:
            continue

        documents.append(document)
        totals["files"] += 1
        for page in document["pages"]:
            totals["pages"] += 1
            kind = page["page_kind"]
            key = f"{kind}_pages"
            if key in totals:
                totals[key] += 1
            if page["needs_vision"]:
                totals["pages_needing_vision"] += 1
            if page["embedded_text_chars"] > 0:
                totals["pages_with_text_layer"] += 1

    return {
        "artifact_version": "arch_ocr.triage.v1",
        "input_path": str(input_path),
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "preview_dpi": preview_dpi,
        "max_pages_per_file": max_pages_per_file,
        "totals": totals,
        "documents": documents,
    }


def _run_triage(args: argparse.Namespace) -> int:
    input_path = Path(args.input_path).expanduser().resolve()
    try:
        files = _iter_input_files(input_path)
        if not files:
            raise RuntimeError("No supported PDF/image files found.")
        artifact = _build_triage_artifact(
            input_path=input_path,
            files=files,
            max_pages_per_file=args.max_pages_per_file,
            preview_dpi=args.preview_dpi,
        )
    except Exception as exc:
        print(f"Triage failed: {exc}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output)
        if args.output
        else Path("output") / f"{input_path.stem or 'packet'}_triage.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    totals = artifact["totals"]
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    print(
        "Triage summary: "
        f"{totals['files']} files, {totals['pages']} pages, "
        f"{totals['pages_needing_vision']} pages need vision, "
        f"{totals['pages_with_text_layer']} pages have embedded text."
    )
    print(f"Saved triage JSON to: {output_path}")
    return 0


def _triage_selected_input_pages(
    *,
    input_path: Path,
    max_pages: int,
) -> list[dict[str, Any]]:
    if input_path.suffix.lower() == ".pdf":
        document = _triage_pdf(input_path, max_pages=max_pages, preview_dpi=36)
    elif input_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
        document = _triage_image(input_path, preview_dpi=36)
    else:
        raise ValueError("Unsupported input extension for triage.")

    return document["pages"]


def _read_pdf_pages_as_base64_pngs(pdf_path: Path, max_pages: int, dpi: int) -> list[RenderedPage]:
    if max_pages < 1:
        raise ValueError("--max-pages must be at least 1.")
    if dpi < 72:
        raise ValueError("--dpi must be at least 72.")

    pages: list[RenderedPage] = []
    with fitz.open(pdf_path) as doc:
        if doc.page_count == 0:
            raise RuntimeError("PDF has no pages.")

        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page_index in range(min(max_pages, doc.page_count)):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_bytes = pix.tobytes("png")
            pages.append(
                RenderedPage(
                    page_number=page_index + 1,
                    image_b64=base64.b64encode(image_bytes).decode("ascii"),
                )
            )

    return pages


def _read_image_file_as_base64_png(image_path: Path) -> list[RenderedPage]:
    try:
        pix = fitz.Pixmap(str(image_path))
    except Exception as exc:
        raise RuntimeError(f"Could not read image: {image_path}") from exc

    image_bytes = pix.tobytes("png")
    return [
        RenderedPage(
            page_number=1,
            image_b64=base64.b64encode(image_bytes).decode("ascii"),
        )
    ]


def _read_input_pages(input_path: Path, max_pages: int, dpi: int) -> list[RenderedPage]:
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        return _read_pdf_pages_as_base64_pngs(input_path, max_pages, dpi)

    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return _read_image_file_as_base64_png(input_path)

    raise ValueError(
        "Unsupported input extension. Use a PDF or image file "
        f"({', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))})."
    )


def _extract_message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        return "\n\n".join(parts).strip()

    return str(content).strip()


def _require_api_key(provider: str, explicit_key: str | None) -> str:
    env_names = {
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "openrouter": ("OPENROUTER_API_KEY",),
        "google-vision": ("GOOGLE_VISION_API_KEY", "GOOGLE_API_KEY"),
    }[provider]

    api_key = (explicit_key or "").strip()
    if api_key:
        return api_key

    for env_name in env_names:
        api_key = (os.getenv(env_name) or "").strip()
        if api_key:
            return api_key

    names = " or ".join(env_names)
    raise RuntimeError(f"Missing API key. Set {names} in .env or pass --api-key.")


def _call_gemini(
    *,
    api_key: str,
    model: str,
    prompt: str,
    pages: list[RenderedPage],
    timeout: int,
) -> dict[str, Any]:
    url = GEMINI_ENDPOINT_TEMPLATE.format(model=model)
    parts: list[dict[str, Any]] = [{"text": prompt}]
    for page in pages:
        parts.append({"text": f"Page {page.page_number}:"})
        parts.append(
            {
                "inline_data": {
                    "mime_type": "image/png",
                    "data": page.image_b64,
                }
            }
        )

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.1},
    }

    response = requests.post(
        url,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Gemini error {response.status_code}: {response.text[:1000]}")
    return response.json()


def _extract_gemini_text(raw_response: dict[str, Any]) -> str:
    candidates = raw_response.get("candidates") or []
    if not candidates:
        return ""

    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_parts = [part.get("text", "").strip() for part in parts if isinstance(part, dict)]
    return "\n\n".join(part for part in text_parts if part)


def _build_openrouter_messages(prompt: str, pages: list[RenderedPage]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for page in pages:
        content.append({"type": "text", "text": f"Page {page.page_number}:"})
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{page.image_b64}"},
            }
        )
    return [{"role": "user", "content": content}]


def _call_openrouter(
    *,
    api_key: str,
    model: str,
    prompt: str,
    pages: list[RenderedPage],
    timeout: int,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    referer = os.getenv("OPENROUTER_HTTP_REFERER")
    app_title = os.getenv("OPENROUTER_APP_TITLE")
    if referer:
        headers["HTTP-Referer"] = referer
    if app_title:
        headers["X-Title"] = app_title

    payload = {
        "model": model,
        "messages": _build_openrouter_messages(prompt, pages),
        "temperature": 0.1,
    }

    response = requests.post(
        OPENROUTER_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"OpenRouter error {response.status_code}: {response.text[:1000]}")
    return response.json()


def _extract_openrouter_text(raw_response: dict[str, Any]) -> str:
    choices = raw_response.get("choices") or []
    if not choices:
        return ""
    content = choices[0].get("message", {}).get("content", "")
    return _extract_message_text(content)


def _call_google_vision(
    *,
    api_key: str,
    pages: list[RenderedPage],
    language_hints: list[str],
    timeout: int,
) -> dict[str, Any]:
    requests_payload = []
    for page in pages:
        request_payload: dict[str, Any] = {
            "image": {"content": page.image_b64},
            "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        }
        if language_hints:
            request_payload["imageContext"] = {"languageHints": language_hints}
        requests_payload.append(request_payload)

    response = requests.post(
        GOOGLE_VISION_ENDPOINT,
        params={"key": api_key},
        headers={"Content-Type": "application/json"},
        json={"requests": requests_payload},
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Google Vision error {response.status_code}: {response.text[:1000]}")
    return response.json()


def _extract_google_vision_text(raw_response: dict[str, Any]) -> str:
    page_texts: list[str] = []
    for index, response in enumerate(raw_response.get("responses") or [], start=1):
        if response.get("error"):
            page_texts.append(f"## Page {index}\n\nError: {response['error']}")
            continue

        annotation = response.get("fullTextAnnotation") or {}
        text = (annotation.get("text") or "").strip()
        page_texts.append(f"## Page {index}\n\n{text or 'No text detected.'}")

    return "\n\n".join(page_texts).strip()


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
                    valid = ", ".join(sorted(VALID_FIELD_TYPES))
                    errors.append(f"{field_path}.field_type must be one of: {valid}")

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


def _build_extraction_artifact(
    *,
    input_path: Path,
    provider: str,
    model: str,
    pages: list[RenderedPage],
    extraction: dict[str, Any],
    page_triage: list[dict[str, Any]],
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
        "extraction": extraction,
    }


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


def _default_model_for_provider(provider: str) -> str:
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    return DEFAULT_GOOGLE_VISION_MODEL


def main() -> int:
    load_dotenv()
    args = parse_args()

    if args.command == "triage":
        return _run_triage(args)

    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2

    provider = args.provider
    model = args.model or _default_model_for_provider(provider)
    prompt = JSON_EXTRACTION_PROMPT if args.json_mode else args.prompt

    if args.json_mode and provider == "google-vision":
        print(
            "--json-mode requires a vision LLM provider. Use gemini or openrouter.",
            file=sys.stderr,
        )
        return 2

    try:
        api_key = _require_api_key(provider, args.api_key)
        pages = _read_input_pages(input_path=input_path, max_pages=args.max_pages, dpi=args.dpi)
        page_triage = _triage_selected_input_pages(
            input_path=input_path,
            max_pages=args.max_pages,
        )

        if provider == "gemini":
            raw_response = _call_gemini(
                api_key=api_key,
                model=model,
                prompt=prompt,
                pages=pages,
                timeout=args.timeout,
            )
            response_text = _extract_gemini_text(raw_response)
        elif provider == "openrouter":
            raw_response = _call_openrouter(
                api_key=api_key,
                model=model,
                prompt=prompt,
                pages=pages,
                timeout=args.timeout,
            )
            response_text = _extract_openrouter_text(raw_response)
        else:
            language_hints = [
                item.strip() for item in args.language_hints.split(",") if item.strip()
            ]
            raw_response = _call_google_vision(
                api_key=api_key,
                pages=pages,
                language_hints=language_hints,
                timeout=args.timeout,
            )
            response_text = _extract_google_vision_text(raw_response)
    except Exception as exc:
        print(f"Document request failed: {exc}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else None

    if args.json_mode:
        extraction = _parse_json_response(response_text)
        _validate_extraction_schema(extraction)
        artifact = _build_extraction_artifact(
            input_path=input_path,
            provider=provider,
            model=model,
            pages=pages,
            extraction=extraction,
            page_triage=page_triage,
        )
        if output_path is None:
            output_path = Path("output") / f"{input_path.stem}_{provider}_extraction.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(artifact, ensure_ascii=False, indent=2))
        print(f"Saved extraction JSON to: {output_path}")
    else:
        report_md = _render_markdown_report(
            input_path=input_path,
            provider=provider,
            model=model,
            page_count=len(pages),
            response_text=response_text,
        )
        if output_path is None:
            output_path = Path("output") / f"{input_path.stem}_{provider}_report.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report_md, encoding="utf-8")
        print(report_md)
        print(f"Saved report to: {output_path}")

    if args.raw_output:
        raw_output_path = Path(args.raw_output)
        raw_output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_output_path.write_text(
            json.dumps(raw_response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Saved raw API response to: {args.raw_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
