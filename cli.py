"""CLI entry point — equivalent to running python ocr_script.py."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from ocr.costs import _empty_cost_totals, _finalize_cost_totals, _format_money
from ocr.extraction import (
    DEFAULT_PROMPT,
    _build_extraction_artifact,
    _render_markdown_report,
    _run_validated_json_extraction,
)
from ocr.packet import (
    _add_extraction_totals,
    _attach_packet_analysis,
    _build_packet_artifact,
    _empty_packet_totals,
    _iter_triaged_pages,
    _recalculate_packet_costs,
    _recalculate_packet_totals,
    _find_packet_page_triage,
    _remove_packet_page_artifact,
)
from ocr.providers import (
    DEFAULT_PROVIDER,
    _default_model_for_provider,
    _redact_secrets,
    _require_api_key,
    _run_provider_request,
)
from ocr.render import (
    SUPPORTED_IMAGE_EXTENSIONS,
    _read_input_pages,
    _read_single_input_page,
)
from ocr.reporting import _render_packet_report
from ocr.schema import JSON_EXTRACTION_PROMPT, _parse_json_response, _validate_extraction_schema
from ocr.triage import (
    _build_triage_artifact,
    _iter_input_files,
    _triage_selected_input_pages,
)
from ocr.costs import _normalize_provider_usage, _add_cost_totals


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


def _arg_was_provided(flag: str) -> bool:
    return any(arg == flag or arg.startswith(f"{flag}=") for arg in sys.argv[1:])


def _run_retry_failed(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

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
    still_failed: list[dict] = []

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
    from ocr.costs import _add_cost_totals

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
    extractions: list[dict] = []
    errors: list[dict] = []

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
