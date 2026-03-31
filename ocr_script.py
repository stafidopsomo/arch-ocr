"""
LLM feedback CLI for PDF pages or image files.

This script renders PDF pages to images, sends them to a vision model provider,
and returns feedback.

Supported providers:
- OpenRouter
- Ollama (local host or ollama.com cloud API)
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
DEFAULT_OPENROUTER_MODEL = "google/gemma-3-4b-it:free"
DEFAULT_OLLAMA_MODEL = "qwen3-vl:8b"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}

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
        description="Send a PDF or image file to an LLM and get feedback.",
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        default="test_inputs/example_page1.pdf",
        help="Input file path (.pdf or image). Defaults to test_inputs/example_page1.pdf.",
    )
    parser.add_argument(
        "--provider",
        choices=["openrouter", "ollama"],
        default=os.getenv("LLM_PROVIDER", "openrouter"),
        help="Provider to use (default: openrouter).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help=(
            "Model name for selected provider. "
            "Defaults: OPENROUTER_MODEL or google/gemma-3-4b-it:free for openrouter; "
            "OLLAMA_MODEL or qwen3-vl:8b for ollama."
        ),
    )
    parser.add_argument(
        "--host",
        default=None,
        help=(
            "Provider host override (ollama only). "
            "Examples: http://localhost:11434, http://<mac-ip>:11434, https://ollama.com"
        ),
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
        default=None,
        help=(
            "API key override. Uses OPENROUTER_API_KEY for openrouter and "
            "OLLAMA_API_KEY for ollama when not provided."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds (default: 120).",
    )
    return parser.parse_args()


def _read_pdf_pages_as_base64_pngs(pdf_path: Path, max_pages: int, dpi: int) -> list[str]:
    if max_pages < 1:
        raise ValueError("--max-pages must be at least 1.")
    if dpi < 72:
        raise ValueError("--dpi must be at least 72.")

    images_b64: list[str] = []
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
            images_b64.append(base64.b64encode(image_bytes).decode("ascii"))

    return images_b64


def _read_image_file_as_base64_png(image_path: Path) -> list[str]:
    try:
        pix = fitz.Pixmap(str(image_path))
    except Exception as exc:
        raise RuntimeError(f"Could not read image: {image_path}") from exc

    # Normalize to PNG for consistent downstream payloads.
    image_bytes = pix.tobytes("png")
    return [base64.b64encode(image_bytes).decode("ascii")]


def _read_input_pages_as_base64_pngs(input_path: Path, max_pages: int, dpi: int) -> list[str]:
    suffix = input_path.suffix.lower()

    if suffix == ".pdf":
        return _read_pdf_pages_as_base64_pngs(pdf_path=input_path, max_pages=max_pages, dpi=dpi)

    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return _read_image_file_as_base64_png(image_path=input_path)

    raise ValueError(
        "Unsupported input extension. Use a PDF or image file "
        f"({', '.join(sorted(SUPPORTED_IMAGE_EXTENSIONS))})."
    )


def _build_openrouter_messages(prompt: str, images_b64: list[str]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_b64 in images_b64:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_b64}"},
            }
        )

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


def _extract_ollama_message_text(raw_response: dict[str, Any]) -> str:
    message = raw_response.get("message") or {}
    content = _extract_message_text(message.get("content", ""))
    if content:
        return content

    # Some thinking-enabled models may leave content empty.
    thinking = message.get("thinking")
    if isinstance(thinking, str) and thinking.strip():
        return thinking.strip()

    return ""


def _call_openrouter(
    *,
    api_key: str,
    model: str,
    prompt: str,
    images_b64: list[str],
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
        "messages": _build_openrouter_messages(prompt, images_b64),
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


def _call_ollama(
    *,
    host: str,
    api_key: str | None,
    model: str,
    prompt: str,
    images_b64: list[str],
    timeout: int,
) -> dict[str, Any]:
    url = host.rstrip("/") + "/api/chat"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": images_b64,
            }
        ],
        "stream": False,
        "think": False,
        "options": {"temperature": 0.1},
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
    )

    if response.status_code >= 400:
        preview = response.text[:1000]
        raise RuntimeError(f"Ollama error {response.status_code}: {preview}")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError("Ollama returned non-JSON response.") from exc


def _render_markdown_feedback(
    *,
    input_path: Path,
    provider: str,
    model: str,
    page_count: int,
    feedback_text: str,
    endpoint: str | None = None,
) -> str:
    lines = [
        "# LLM Feedback",
        "",
        f"- Source file: {input_path}",
        f"- Provider: {provider}",
        f"- Model: {model}",
        f"- Pages sent: {page_count}",
    ]
    if endpoint:
        lines.append(f"- Endpoint: {endpoint}")

    lines.extend(
        [
            "",
            "## Response",
            "",
            feedback_text.strip() or "No content returned by model.",
            "",
        ]
    )
    return "\n".join(lines)


def process_pdfs(*_args: Any, **_kwargs: Any) -> None:
    """Compatibility stub for old webapp imports."""
    raise RuntimeError(
        "The OCR pipeline has been removed. Use ocr_script.py as an LLM PDF feedback CLI."
    )


def _is_ollama_cloud_host(host: str) -> bool:
    norm = host.lower().rstrip("/")
    return norm.startswith("https://ollama.com") or norm.startswith("https://www.ollama.com")


def main() -> int:
    load_dotenv()
    args = parse_args()

    input_path = Path(args.input_path).expanduser().resolve()
    if not input_path.exists() or not input_path.is_file():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 2

    provider = args.provider

    if provider == "openrouter":
        model = args.model or os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
        api_key = (args.api_key or os.getenv("OPENROUTER_API_KEY") or "").strip()
        if not api_key:
            print(
                "Missing OpenRouter API key. Set OPENROUTER_API_KEY in .env or pass --api-key.",
                file=sys.stderr,
            )
            return 2
        endpoint = OPENROUTER_ENDPOINT
    else:
        model = args.model or os.getenv("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
        endpoint = (args.host or os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST).strip()
        if not endpoint:
            endpoint = DEFAULT_OLLAMA_HOST
        api_key = (args.api_key or os.getenv("OLLAMA_API_KEY") or "").strip()

        # Cloud API requires API key. Local daemon does not.
        if _is_ollama_cloud_host(endpoint) and not api_key:
            print(
                "Missing OLLAMA_API_KEY for ollama.com host. Set OLLAMA_API_KEY or pass --api-key.",
                file=sys.stderr,
            )
            return 2

    try:
        images_b64 = _read_input_pages_as_base64_pngs(
            input_path=input_path,
            max_pages=args.max_pages,
            dpi=args.dpi,
        )
    except Exception as exc:
        print(f"Failed to render PDF pages: {exc}", file=sys.stderr)
        return 1

    try:
        if provider == "openrouter":
            raw_response = _call_openrouter(
                api_key=api_key,
                model=model,
                prompt=args.prompt,
                images_b64=images_b64,
                timeout=args.timeout,
            )
            choices = raw_response.get("choices") or []
            if not choices:
                print("OpenRouter returned no choices.", file=sys.stderr)
                return 1
            content = choices[0].get("message", {}).get("content", "")
        else:
            raw_response = _call_ollama(
                host=endpoint,
                api_key=api_key or None,
                model=model,
                prompt=args.prompt,
                images_b64=images_b64,
                timeout=args.timeout,
            )
            content = _extract_ollama_message_text(raw_response)
    except Exception as exc:
        print(f"LLM request failed: {exc}", file=sys.stderr)
        return 1

    feedback_text = _extract_message_text(content)

    report_md = _render_markdown_feedback(
        input_path=input_path,
        provider=provider,
        model=model,
        page_count=len(images_b64),
        feedback_text=feedback_text,
        endpoint=endpoint if provider == "ollama" else OPENROUTER_ENDPOINT,
    )

    output_path = (
        Path(args.output)
        if args.output
        else Path("output") / f"{input_path.stem}_feedback.md"
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
