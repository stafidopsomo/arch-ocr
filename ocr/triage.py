"""Page classification, ink ratios, and annotation detection."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from ocr.render import SUPPORTED_IMAGE_EXTENSIONS


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
