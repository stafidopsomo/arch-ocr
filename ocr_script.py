"""
LLM feedback CLI for PDF pages.

This script sends one or more pages from a PDF to an OpenRouter vision model
and returns structured feedback.

Usage:
    python ocr_script.py test_inputs/example_page1.pdf
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import requests
from dotenv import load_dotenv

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemma-3-4b-it:free"
DEFAULT_PROMPT = (
    "Review this page from a property document and provide concise feedback.\n"
    "Use these sections exactly:\n"
    "1) Document summary\n"
    "2) Key visible fields and values\n"
    "3) Missing or unclear information\n"
    "4) Scan quality issues\n"
    "5) Recommended next checks\n"
    "If something is uncertain, say uncertain and do not guess."
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send PDF pages to an LLM and get feedback.",
    )
    parser.add_argument(
        "pdf_path",
        nargs="?",
        default="test_inputs/example_page1.pdf",
        help="Input PDF path. Defaults to test_inputs/example_page1.pdf.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL),
        help=f"OpenRouter model name (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=1,
        help="How many PDF pages to send (default: 1).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Rasterization DPI for PDF pages (default: 150).",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt sent to the model.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output markdown file path.",
    )
    parser.add_argument(
        "--raw-output",
        default=None,
        help="Optional path to store raw API JSON response.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OPENROUTER_API_KEY"),
        help="OpenRouter API key. Defaults to OPENROUTER_API_KEY.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds (default: 120).",
    )
    return parser.parse_args()


def _read_pdf_pages_as_data_urls(pdf_path: Path, max_pages: int, dpi: int) -> list[str]:
    if max_pages < 1:
        raise ValueError("--max-pages must be at least 1.")
    if dpi < 72:
        raise ValueError("--dpi must be at least 72.")

    data_urls: list[str] = []
    with fitz.open(pdf_path) as doc:
        if doc.page_count == 0:
            raise RuntimeError("PDF has no pages.")

        page_count = min(max_pages, doc.page_count)
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        for page_index in range(page_count):
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image_bytes = pix.tobytes("png")
            image_b64 = base64.b64encode(image_bytes).decode("ascii")
            data_urls.append(f"data:image/png;base64,{image_b64}")

    return data_urls


def _build_messages(prompt: str, image_data_urls: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    return [{"role": "user", "content": content}]


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


def _call_openrouter(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
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
        "messages": messages,
        "temperature": 0.1,
    }

    response = requests.post(
        OPENROUTER_ENDPOINT,
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if response.status_code >= 400:
        preview = response.text[:1000]
        raise RuntimeError(f"OpenRouter error {response.status_code}: {preview}")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("OpenRouter returned non-JSON response.") from exc


def _render_markdown_feedback(
    *,
    pdf_path: Path,
    model: str,
    page_count: int,
    feedback_text: str,
) -> str:
    lines = [
        "# LLM Feedback",
        "",
        f"- Source PDF: {pdf_path}",
        f"- Model: {model}",
        f"- Pages sent: {page_count}",
        "",
        "## Response",
        "",
        feedback_text.strip() or "No content returned by model.",
        "",
    ]
    return "\n".join(lines)


def process_pdfs(*_args: Any, **_kwargs: Any) -> None:
    """Compatibility stub for old webapp imports."""
    raise RuntimeError(
        "The OCR pipeline has been removed. Use ocr_script.py as an LLM PDF feedback CLI."
    )


def main() -> int:
    load_dotenv()
    args = parse_args()

    pdf_path = Path(args.pdf_path).expanduser().resolve()
    if not pdf_path.exists() or not pdf_path.is_file():
        print(f"Input PDF not found: {pdf_path}", file=sys.stderr)
        return 2

    api_key = (args.api_key or "").strip()
    if not api_key:
        print(
            "Missing OpenRouter API key. Set OPENROUTER_API_KEY in .env or pass --api-key.",
            file=sys.stderr,
        )
        return 2

    try:
        image_data_urls = _read_pdf_pages_as_data_urls(
            pdf_path=pdf_path,
            max_pages=args.max_pages,
            dpi=args.dpi,
        )
    except Exception as exc:
        print(f"Failed to render PDF pages: {exc}", file=sys.stderr)
        return 1

    messages = _build_messages(args.prompt, image_data_urls)

    try:
        raw_response = _call_openrouter(
            api_key=api_key,
            model=args.model,
            messages=messages,
            timeout=args.timeout,
        )
    except Exception as exc:
        print(f"LLM request failed: {exc}", file=sys.stderr)
        return 1

    choices = raw_response.get("choices") or []
    if not choices:
        print("OpenRouter returned no choices.", file=sys.stderr)
        return 1

    content = choices[0].get("message", {}).get("content", "")
    feedback_text = _extract_message_text(content)

    report_md = _render_markdown_feedback(
        pdf_path=pdf_path,
        model=args.model,
        page_count=len(image_data_urls),
        feedback_text=feedback_text,
    )

    output_path = (
        Path(args.output)
        if args.output
        else Path("output") / f"{pdf_path.stem}_feedback.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report_md, encoding="utf-8")

    if args.raw_output:
        raw_output_path = Path(args.raw_output)
        raw_output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_output_path.write_text(
            json.dumps(raw_response, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(report_md)
    print(f"Saved feedback to: {output_path}")
    if args.raw_output:
        print(f"Saved raw API response to: {args.raw_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
