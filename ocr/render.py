"""PDF to PNG base64 rendering and image caching."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class RenderedPage:
    page_number: int
    image_b64: str


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
