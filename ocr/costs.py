"""Pricing table, usage tracking, and cost normalization."""

from __future__ import annotations

import os
from typing import Any

PRICING_SOURCE_URL = "https://ai.google.dev/gemini-api/docs/pricing"
OPENROUTER_USAGE_SOURCE_URL = "https://openrouter.ai/docs/guides/guides/administration/usage-accounting"

DEFAULT_MODEL_PRICING_PER_1M_USD: dict[tuple[str, str], dict[str, Any]] = {
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


def _format_money(value: Any) -> str:
    amount = _as_float(value)
    if amount is None:
        return "unknown"
    return f"${amount:.6f}"
