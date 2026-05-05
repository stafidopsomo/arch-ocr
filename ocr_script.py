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
import difflib
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

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
PRICING_SOURCE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
OPENROUTER_USAGE_SOURCE_URL = "https://openrouter.ai/docs/guides/guides/administration/usage-accounting"
DEFAULT_MODEL_PRICING_PER_1M_USD = {
    ("gemini", "gemini-3.1-flash-lite-preview"): {
        "input": 0.25,
        "output": 1.50,
        "cached_input": 0.025,
        "source": PRICING_SOURCE_URL,
        "note": "Default estimate for Gemini 3.1 Flash-Lite Preview. Override with OCR_COST_* env vars if pricing changes.",
    },
    ("openrouter", "google/gemini-3.1-flash-lite-preview"): {
        "input": 0.25,
        "output": 1.50,
        "cached_input": 0.025,
        "source": OPENROUTER_USAGE_SOURCE_URL,
        "note": "OpenRouter responses may include reported cost; this table is only a fallback estimate.",
    },
}
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VALID_CONFIDENCE_VALUES = {"high", "medium", "low"}
API_KEY_ENV_NAMES = {
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_VISION_API_KEY",
    "OPENROUTER_API_KEY",
}
PLACEHOLDER_API_KEYS = {
    "your_key_here",
    "your_gemini_key_here",
    "your_google_vision_key_here",
    "your_openrouter_key_here",
}
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


def _parse_provider_args(parser: argparse.ArgumentParser) -> None:
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

    if len(sys.argv) > 1 and sys.argv[1] == "packet":
        parser = argparse.ArgumentParser(
            description="Extract validated page evidence for a folder packet.",
        )
        parser.set_defaults(command="packet")
        parser.add_argument(
            "input_path",
            help="Input folder, PDF, or image file packet.",
        )
        parser.add_argument(
            "--max-pages-per-file",
            type=int,
            default=2,
            help="Limit processed pages per file. Default: 2.",
        )
        parser.add_argument(
            "--preview-dpi",
            type=int,
            default=36,
            help="Low-resolution render DPI for triage. Default: 36.",
        )
        _parse_provider_args(parser)
        _parse_common_args(parser)
        return parser.parse_args(sys.argv[2:])

    if len(sys.argv) > 1 and sys.argv[1] == "cluster":
        parser = argparse.ArgumentParser(
            description="Add deterministic field clusters to an existing packet JSON.",
        )
        parser.set_defaults(command="cluster")
        parser.add_argument(
            "input_path",
            help="Existing arch_ocr.packet.v1 JSON file.",
        )
        _parse_common_args(parser)
        return parser.parse_args(sys.argv[2:])

    if len(sys.argv) > 1 and sys.argv[1] == "report":
        parser = argparse.ArgumentParser(
            description="Render a deterministic markdown report from packet JSON.",
        )
        parser.set_defaults(command="report")
        parser.add_argument(
            "input_path",
            help="Existing arch_ocr.packet.v1 JSON file.",
        )
        _parse_common_args(parser)
        return parser.parse_args(sys.argv[2:])

    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        parser = argparse.ArgumentParser(
            description="Add deterministic validation checks to an existing packet JSON.",
        )
        parser.set_defaults(command="validate")
        parser.add_argument(
            "input_path",
            help="Existing arch_ocr.packet.v1 JSON file.",
        )
        _parse_common_args(parser)
        return parser.parse_args(sys.argv[2:])

    if len(sys.argv) > 1 and sys.argv[1] == "retry-failed":
        parser = argparse.ArgumentParser(
            description="Retry failed page extractions in an existing packet JSON.",
        )
        parser.set_defaults(command="retry_failed")
        parser.add_argument(
            "input_path",
            help="Existing arch_ocr.packet.v1 JSON file.",
        )
        _parse_provider_args(parser)
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
    _parse_provider_args(parser)
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
        print(f"Triage failed: {_redact_secrets(str(exc))}", file=sys.stderr)
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


def _read_pdf_page_as_base64_png(pdf_path: Path, page_number: int, dpi: int) -> RenderedPage:
    if page_number < 1:
        raise ValueError("Page number must be at least 1.")
    if dpi < 72:
        raise ValueError("--dpi must be at least 72.")

    with fitz.open(pdf_path) as doc:
        if page_number > doc.page_count:
            raise RuntimeError(f"PDF has only {doc.page_count} pages.")

        zoom = dpi / 72.0
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        image_bytes = pix.tobytes("png")
        return RenderedPage(
            page_number=page_number,
            image_b64=base64.b64encode(image_bytes).decode("ascii"),
        )


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


def _read_single_input_page(input_path: Path, page_number: int, dpi: int) -> RenderedPage:
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        return _read_pdf_page_as_base64_png(input_path, page_number, dpi)

    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        if page_number != 1:
            raise ValueError("Image inputs only have page 1.")
        return _read_image_file_as_base64_png(input_path)[0]

    raise ValueError(
        "Unsupported input extension. Use a PDF or image file "
        f"({', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))})."
    )


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


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _redact_secrets(text: str) -> str:
    redacted = re.sub(r"([?&]key=)[^&\s)'\"]+", r"\1[redacted]", text)
    for env_name in API_KEY_ENV_NAMES:
        secret = (os.getenv(env_name) or "").strip()
        if secret and secret not in PLACEHOLDER_API_KEYS:
            redacted = redacted.replace(secret, "[redacted]")
    return redacted


def _require_api_key(provider: str, explicit_key: str | None) -> str:
    env_names = {
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "openrouter": ("OPENROUTER_API_KEY",),
        "google-vision": ("GOOGLE_VISION_API_KEY", "GOOGLE_API_KEY"),
    }[provider]

    api_key = (explicit_key or "").strip()
    if api_key and api_key not in PLACEHOLDER_API_KEYS:
        return api_key

    for env_name in env_names:
        api_key = (os.getenv(env_name) or "").strip()
        if api_key and api_key not in PLACEHOLDER_API_KEYS:
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
        "usage": {"include": True},
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


def _pricing_override_from_env() -> dict[str, float] | None:
    input_rate = _as_float(os.getenv("OCR_COST_INPUT_PER_1M_USD"))
    output_rate = _as_float(os.getenv("OCR_COST_OUTPUT_PER_1M_USD"))
    cached_input_rate = _as_float(os.getenv("OCR_COST_CACHED_INPUT_PER_1M_USD"))
    if input_rate is None and output_rate is None and cached_input_rate is None:
        return None
    return {
        "input": input_rate or 0.0,
        "output": output_rate or 0.0,
        "cached_input": cached_input_rate or 0.0,
        "source": "env:OCR_COST_*",
        "note": "User-supplied pricing override.",
    }


def _pricing_for_model(provider: str, model: str) -> dict[str, Any] | None:
    override = _pricing_override_from_env()
    if override is not None:
        return override
    pricing = DEFAULT_MODEL_PRICING_PER_1M_USD.get((provider, model))
    if pricing is not None:
        return dict(pricing)
    return None


def _normalize_provider_usage(
    *,
    provider: str,
    model: str,
    raw_response: dict[str, Any],
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cached_input_tokens": 0,
        "reported_cost_credits": None,
        "reported_cost_usd": None,
        "estimated_cost_usd": None,
        "pricing": None,
        "source": "none",
    }

    if provider == "gemini":
        metadata = raw_response.get("usageMetadata") or {}
        if isinstance(metadata, dict):
            usage.update(
                {
                    "input_tokens": _as_int(metadata.get("promptTokenCount")),
                    "output_tokens": _as_int(metadata.get("candidatesTokenCount")),
                    "total_tokens": _as_int(metadata.get("totalTokenCount")),
                    "cached_input_tokens": _as_int(metadata.get("cachedContentTokenCount")) or 0,
                    "thoughts_tokens": _as_int(metadata.get("thoughtsTokenCount")),
                    "source": "gemini.usageMetadata",
                }
            )
    elif provider == "openrouter":
        metadata = raw_response.get("usage") or {}
        if isinstance(metadata, dict):
            usage.update(
                {
                    "input_tokens": _as_int(metadata.get("prompt_tokens")),
                    "output_tokens": _as_int(metadata.get("completion_tokens")),
                    "total_tokens": _as_int(metadata.get("total_tokens")),
                    "reported_cost_credits": _as_float(metadata.get("cost")),
                    "source": "openrouter.usage",
                }
            )
            details = metadata.get("prompt_tokens_details") or {}
            if isinstance(details, dict):
                usage["cached_input_tokens"] = _as_int(details.get("cached_tokens")) or 0
            if usage["reported_cost_credits"] is not None:
                usage["reported_cost_usd"] = usage["reported_cost_credits"]

    pricing = _pricing_for_model(provider, model)
    if pricing is not None:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        cached_input_tokens = usage.get("cached_input_tokens") or 0
        if isinstance(input_tokens, int) or isinstance(output_tokens, int):
            billable_input_tokens = max((input_tokens or 0) - cached_input_tokens, 0)
            estimated_cost = (
                (billable_input_tokens / 1_000_000) * float(pricing.get("input", 0.0))
                + (cached_input_tokens / 1_000_000) * float(pricing.get("cached_input", 0.0))
                + ((output_tokens or 0) / 1_000_000) * float(pricing.get("output", 0.0))
            )
            usage["estimated_cost_usd"] = round(estimated_cost, 8)
            usage["billable_input_tokens"] = billable_input_tokens
        usage["pricing"] = pricing

    return usage


def _run_provider_request(
    *,
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    pages: list[RenderedPage],
    language_hints: str,
    timeout: int,
) -> tuple[dict[str, Any], str]:
    if provider == "gemini":
        raw_response = _call_gemini(
            api_key=api_key,
            model=model,
            prompt=prompt,
            pages=pages,
            timeout=timeout,
        )
        return raw_response, _extract_gemini_text(raw_response)

    if provider == "openrouter":
        raw_response = _call_openrouter(
            api_key=api_key,
            model=model,
            prompt=prompt,
            pages=pages,
            timeout=timeout,
        )
        return raw_response, _extract_openrouter_text(raw_response)

    hints = [item.strip() for item in language_hints.split(",") if item.strip()]
    raw_response = _call_google_vision(
        api_key=api_key,
        pages=pages,
        language_hints=hints,
        timeout=timeout,
    )
    return raw_response, _extract_google_vision_text(raw_response)


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


def _add_extraction_totals(totals: dict[str, int], artifact: dict[str, Any]) -> None:
    summary = artifact.get("extraction_summary", {})
    totals["pages_extracted"] += int(summary.get("page_count", 0))
    totals["field_count"] += int(summary.get("field_count", 0))
    totals["low_confidence_field_count"] += int(summary.get("low_confidence_field_count", 0))
    totals["handwritten_field_count"] += int(summary.get("handwritten_field_count", 0))
    totals["stamped_field_count"] += int(summary.get("stamped_field_count", 0))
    totals["signature_field_count"] += int(summary.get("signature_field_count", 0))


def _add_cost_totals(cost_totals: dict[str, Any], artifact: dict[str, Any]) -> None:
    usage = artifact.get("usage", {})
    if not isinstance(usage, dict) or not usage:
        cost_totals["calls_without_usage"] += 1
        return

    cost_totals["calls_with_usage"] += 1
    for source_key, total_key in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("total_tokens", "total_tokens"),
        ("cached_input_tokens", "cached_input_tokens"),
        ("billable_input_tokens", "billable_input_tokens"),
        ("thoughts_tokens", "thoughts_tokens"),
    ):
        value = _as_int(usage.get(source_key))
        if value is not None:
            cost_totals[total_key] += value

    for source_key, total_key in (
        ("reported_cost_usd", "reported_cost_usd"),
        ("reported_cost_credits", "reported_cost_credits"),
        ("estimated_cost_usd", "estimated_cost_usd"),
    ):
        value = _as_float(usage.get(source_key))
        if value is not None:
            cost_totals[total_key] += value

    pricing = usage.get("pricing")
    if isinstance(pricing, dict):
        source = str(pricing.get("source") or "unknown")
        if source not in cost_totals["pricing_sources"]:
            cost_totals["pricing_sources"].append(source)


def _empty_cost_totals(provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    pricing = _pricing_for_model(provider or "", model or "") if provider and model else None
    return {
        "provider": provider,
        "model": model,
        "calls_with_usage": 0,
        "calls_without_usage": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_input_tokens": 0,
        "billable_input_tokens": 0,
        "thoughts_tokens": 0,
        "reported_cost_usd": 0.0,
        "reported_cost_credits": 0.0,
        "estimated_cost_usd": 0.0,
        "currency": "USD",
        "pricing": pricing,
        "pricing_sources": [],
        "notes": [],
    }


def _finalize_cost_totals(cost_totals: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(cost_totals)
    for key in ("reported_cost_usd", "reported_cost_credits", "estimated_cost_usd"):
        finalized[key] = round(float(finalized.get(key, 0.0)), 8)
    if finalized.get("calls_without_usage"):
        finalized.setdefault("notes", []).append(
            "Some page artifacts do not contain provider usage metadata; totals are partial."
        )
    if not finalized.get("pricing") and finalized.get("estimated_cost_usd") == 0.0:
        finalized.setdefault("notes", []).append(
            "No pricing table matched this provider/model. Set OCR_COST_INPUT_PER_1M_USD and OCR_COST_OUTPUT_PER_1M_USD to estimate cost."
        )
    return finalized


def _recalculate_packet_costs(packet: dict[str, Any]) -> dict[str, Any]:
    provider_config = packet.get("provider_config", {})
    if not isinstance(provider_config, dict):
        provider_config = {}
    provider = str(provider_config.get("provider") or "")
    model = str(provider_config.get("model") or "")
    cost_totals = _empty_cost_totals(provider=provider or None, model=model or None)
    for artifact in packet.get("page_extractions", []):
        if isinstance(artifact, dict):
            _add_cost_totals(cost_totals, artifact)
    cost_summary = _finalize_cost_totals(cost_totals)
    packet["cost_summary"] = cost_summary
    return cost_summary


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


def _build_executive_summary(packet: dict[str, Any]) -> dict[str, Any]:
    totals = packet.get("totals", {})
    if not isinstance(totals, dict):
        totals = {}
    check_summary = packet.get("check_summary", {})
    if not isinstance(check_summary, dict):
        check_summary = {}

    failed_checks = _checks_with_status(packet, "fail")
    warning_checks = _checks_with_status(packet, "warning")
    unknown_checks = _checks_with_status(packet, "unknown")
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


def _attach_packet_checks(packet: dict[str, Any]) -> None:
    if not packet.get("clusters"):
        _attach_packet_clusters(packet)
    checks = _build_packet_checks(packet)
    packet["checks"] = checks
    packet["check_summary"] = _summarize_checks(checks)
    totals = packet.setdefault("totals", {})
    if isinstance(totals, dict):
        totals["check_count"] = len(checks)


def _attach_packet_analysis(packet: dict[str, Any]) -> None:
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


def _run_cluster(args: argparse.Namespace) -> int:
    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        print(f"Packet JSON not found: {input_path}", file=sys.stderr)
        return 2

    try:
        packet = json.loads(input_path.read_text(encoding="utf-8"))
        if packet.get("artifact_version") != "arch_ocr.packet.v1":
            raise RuntimeError("Input is not an arch_ocr.packet.v1 artifact.")
        _attach_packet_analysis(packet)
    except Exception as exc:
        print(f"Cluster build failed: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else input_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = packet["cluster_summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        "Cluster summary: "
        f"{summary['cluster_count']} clusters, "
        f"{summary['repeated_cluster_count']} repeated."
    )
    print(f"Saved clustered packet JSON to: {output_path}")
    return 0


def _run_validate(args: argparse.Namespace) -> int:
    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        print(f"Packet JSON not found: {input_path}", file=sys.stderr)
        return 2

    try:
        packet = json.loads(input_path.read_text(encoding="utf-8"))
        if packet.get("artifact_version") != "arch_ocr.packet.v1":
            raise RuntimeError("Input is not an arch_ocr.packet.v1 artifact.")
        _attach_packet_analysis(packet)
    except Exception as exc:
        print(f"Validation build failed: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else input_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = packet["check_summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(
        "Validation summary: "
        f"{summary['check_count']} checks, "
        f"{summary['checks_by_status']}."
    )
    print(f"Saved validated packet JSON to: {output_path}")
    return 0


def _arg_was_provided(flag: str) -> bool:
    return any(arg == flag or arg.startswith(f"{flag}=") for arg in sys.argv[1:])


def _run_retry_failed(args: argparse.Namespace) -> int:
    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        print(f"Packet JSON not found: {input_path}", file=sys.stderr)
        return 2

    try:
        packet = json.loads(input_path.read_text(encoding="utf-8"))
        if packet.get("artifact_version") != "arch_ocr.packet.v1":
            raise RuntimeError("Input is not an arch_ocr.packet.v1 artifact.")
    except Exception as exc:
        print(f"Retry setup failed: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1

    errors = [error for error in packet.get("errors", []) if isinstance(error, dict)]
    if not errors:
        _recalculate_packet_totals(packet)
        _attach_packet_analysis(packet)
        output_path = Path(args.output) if args.output else input_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(packet, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("Retry summary: no failed pages recorded.")
        print(f"Saved packet JSON to: {output_path}")
        return 0

    provider_config = packet.get("provider_config", {})
    if not isinstance(provider_config, dict):
        provider_config = {}

    provider = (
        args.provider
        if _arg_was_provided("--provider")
        else str(provider_config.get("provider") or args.provider)
    )
    if provider == "google-vision":
        print("Packet JSON extraction requires gemini or openrouter.", file=sys.stderr)
        return 2

    model = args.model or str(provider_config.get("model") or _default_model_for_provider(provider))
    dpi = (
        args.dpi
        if _arg_was_provided("--dpi")
        else int(provider_config.get("dpi") or args.dpi)
    )

    try:
        api_key = _require_api_key(provider, args.api_key)
    except Exception as exc:
        print(f"Retry setup failed: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1

    successful = 0
    still_failed: list[dict[str, Any]] = []

    for error in errors:
        source_file = Path(str(error.get("source_file", ""))).expanduser()
        page_number = int(error.get("page_number", 1))
        page_id = str(error.get("page_id") or f"{source_file.stem}:p{page_number}")
        page_triage = _find_packet_page_triage(packet, source_file, page_number)
        if page_triage is None:
            page_triage = {
                "page_id": page_id,
                "source_file": str(source_file),
                "page_number": page_number,
                "page_kind": "unknown",
                "needs_vision": True,
                "needs_text_layer": False,
                "embedded_text_chars": 0,
                "image_count": 0,
                "annotation_count": 0,
                "ink_ratio": None,
            }

        print(f"Retrying {source_file.name} page {page_number}...")
        try:
            rendered_page = _read_single_input_page(source_file, page_number, dpi)
            artifact, _raw_response = _run_validated_json_extraction(
                input_path=source_file,
                provider=provider,
                api_key=api_key,
                model=model,
                pages=[rendered_page],
                page_triage=[page_triage],
                language_hints=args.language_hints,
                timeout=args.timeout,
            )
            _remove_packet_page_artifact(packet, source_file, page_number)
            packet.setdefault("page_extractions", []).append(artifact)
            successful += 1
        except Exception as exc:
            error_message = _redact_secrets(str(exc))
            updated_error = dict(error)
            updated_error.update(
                {
                    "source_file": str(source_file),
                    "page_number": page_number,
                    "page_id": page_id,
                    "error": error_message,
                    "last_retry_at": datetime.now(timezone.utc)
                    .replace(microsecond=0)
                    .isoformat(),
                }
            )
            still_failed.append(updated_error)
            print(
                f"Retry failed: {source_file.name} page {page_number}: {error_message}",
                file=sys.stderr,
            )

    packet["errors"] = still_failed
    _recalculate_packet_totals(packet)
    _attach_packet_analysis(packet)

    output_path = Path(args.output) if args.output else input_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    totals = packet["totals"]
    print(
        "Retry summary: "
        f"{len(errors)} attempted, {successful} recovered, "
        f"{len(still_failed)} still failed."
    )
    print(
        "Packet summary: "
        f"{totals['pages_extracted']} extracted, {totals['pages_failed']} failed, "
        f"{totals['field_count']} fields."
    )
    cost_summary = packet.get("cost_summary", {})
    if isinstance(cost_summary, dict):
        print(
            "Cost summary: "
            f"{cost_summary.get('calls_with_usage', 0)} calls with usage, "
            f"{cost_summary.get('calls_without_usage', 0)} without usage, "
            f"estimated {_format_money(cost_summary.get('estimated_cost_usd'))}."
        )
    print(f"Saved packet JSON to: {output_path}")
    return 0 if not still_failed else 1


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


def _format_money(value: Any) -> str:
    amount = _as_float(value)
    if amount is None:
        return "unknown"
    return f"${amount:.6f}"


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


def _run_report(args: argparse.Namespace) -> int:
    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        print(f"Packet JSON not found: {input_path}", file=sys.stderr)
        return 2

    try:
        packet = json.loads(input_path.read_text(encoding="utf-8"))
        if packet.get("artifact_version") != "arch_ocr.packet.v1":
            raise RuntimeError("Input is not an arch_ocr.packet.v1 artifact.")
        _attach_packet_analysis(packet)
        report = _render_packet_report(packet)
    except Exception as exc:
        print(f"Report build failed: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1

    output_path = (
        Path(args.output)
        if args.output
        else input_path.with_name(f"{input_path.stem}_report.md")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"Saved packet report to: {output_path}")
    return 0


def _run_packet(args: argparse.Namespace) -> int:
    input_path = Path(args.input_path).expanduser().resolve()
    if args.max_pages_per_file < 1:
        print("--max-pages-per-file must be at least 1.", file=sys.stderr)
        return 2
    if args.provider == "google-vision":
        print("Packet JSON extraction requires gemini or openrouter.", file=sys.stderr)
        return 2

    provider = args.provider
    model = args.model or _default_model_for_provider(provider)

    try:
        files = _iter_input_files(input_path)
        if not files:
            raise RuntimeError("No supported PDF/image files found.")
        api_key = _require_api_key(provider, args.api_key)
        triage_artifact = _build_triage_artifact(
            input_path=input_path,
            files=files,
            max_pages_per_file=None,
            preview_dpi=args.preview_dpi,
        )
    except Exception as exc:
        print(f"Packet setup failed: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1

    selected_pages = _iter_triaged_pages(triage_artifact, args.max_pages_per_file)
    totals = _empty_packet_totals(triage_artifact, len(selected_pages))
    cost_totals = _empty_cost_totals(provider=provider, model=model)
    extractions: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for source_file, page_triage in selected_pages:
        page_number = int(page_triage.get("page_number", 1))
        page_id = str(page_triage.get("page_id", f"{source_file.stem}:p{page_number}"))
        print(f"Extracting {source_file.name} page {page_number}...")
        try:
            rendered_page = _read_single_input_page(source_file, page_number, args.dpi)
            artifact, _raw_response = _run_validated_json_extraction(
                input_path=source_file,
                provider=provider,
                api_key=api_key,
                model=model,
                pages=[rendered_page],
                page_triage=[page_triage],
                language_hints=args.language_hints,
                timeout=args.timeout,
            )
            extractions.append(artifact)
            _add_extraction_totals(totals, artifact)
            _add_cost_totals(cost_totals, artifact)
        except Exception as exc:
            error_message = _redact_secrets(str(exc))
            totals["pages_failed"] += 1
            errors.append(
                {
                    "source_file": str(source_file),
                    "page_number": page_number,
                    "page_id": page_id,
                    "error": error_message,
                }
            )
            print(
                f"Page failed but packet processing will continue: "
                f"{source_file.name} page {page_number}: {error_message}",
                file=sys.stderr,
            )

    packet = _build_packet_artifact(
        input_path=input_path,
        provider=provider,
        model=model,
        dpi=args.dpi,
        max_pages_per_file=args.max_pages_per_file,
        triage_artifact=triage_artifact,
        extractions=extractions,
        errors=errors,
        totals=totals,
    )
    packet["cost_summary"] = _finalize_cost_totals(cost_totals)
    _attach_packet_analysis(packet)

    output_path = (
        Path(args.output)
        if args.output
        else Path("output") / f"{input_path.stem or 'packet'}_{provider}_packet.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(packet, ensure_ascii=False, indent=2))
    print(
        "Packet summary: "
        f"{totals['pages_extracted']} extracted, {totals['pages_failed']} failed, "
        f"{totals['field_count']} fields."
    )
    print(f"Saved packet JSON to: {output_path}")
    return 0 if totals["pages_failed"] == 0 else 1


def main() -> int:
    load_dotenv()
    args = parse_args()

    if args.command == "triage":
        return _run_triage(args)

    if args.command == "packet":
        return _run_packet(args)

    if args.command == "cluster":
        return _run_cluster(args)

    if args.command == "report":
        return _run_report(args)

    if args.command == "validate":
        return _run_validate(args)

    if args.command == "retry_failed":
        return _run_retry_failed(args)

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
        raw_response, response_text = _run_provider_request(
            provider=provider,
            api_key=api_key,
            model=model,
            prompt=prompt,
            pages=pages,
            language_hints=args.language_hints,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"Document request failed: {_redact_secrets(str(exc))}", file=sys.stderr)
        return 1

    output_path = Path(args.output) if args.output else None

    if args.json_mode:
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
