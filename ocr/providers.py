"""API calls to Gemini, OpenRouter, and Google Vision providers."""

from __future__ import annotations

import os
import re
from typing import Any

import requests

from ocr.render import RenderedPage

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
GOOGLE_VISION_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

DEFAULT_PROVIDER = "gemini"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_OPENROUTER_MODEL = "google/gemini-flash-1.5-8b"
DEFAULT_GOOGLE_VISION_MODEL = "document-text-detection"

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


def _default_model_for_provider(provider: str) -> str:
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", DEFAULT_OPENROUTER_MODEL)
    return DEFAULT_GOOGLE_VISION_MODEL


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
