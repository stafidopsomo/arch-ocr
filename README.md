# arch-ocr

Cloud-first packet validation for Greek property, architecture, and
building/legal documents.

This project is being rebuilt around evidence-based validation:

```text
document packet
-> page-level evidence extraction
-> entity clustering
-> cross-check validation
-> structured result JSON
-> optional report
```

The Mac does not run local OCR engines or local LLMs. The active workflow uses
cloud APIs, with Gemini as the first cheap/free-tier vision LLM path and Google
Vision as an optional OCR baseline.

## Current CLI

Local page triage, no API calls:

```bash
python ocr_script.py triage test_inputs/private/packet_001 \
  --output output/packet_001_triage.json
```

Markdown review:

```bash
python ocr_script.py test_inputs/example_page1.pdf \
  --provider gemini \
  --max-pages 1 \
  --output output/example_page1_gemini.md \
  --raw-output output/example_page1_gemini_raw.json
```

Structured page evidence extraction:

```bash
python ocr_script.py test_inputs/example_page1.pdf \
  --provider gemini \
  --json-mode \
  --max-pages 1 \
  --output output/example_page1_gemini_extraction.json \
  --raw-output output/example_page1_gemini_extraction_raw.json
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Recommended `.env` start:

```env
CLOUD_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-3.1-flash-lite-preview
```

## Providers

- `gemini` - default cheap/free-tier vision LLM extraction path.
- `google-vision` - OCR baseline for raw Greek text and labels.
- `openrouter` - hosted model playground.

## Output

Normal mode writes markdown:

```text
output/<input_stem>_<provider>_report.md
```

`--json-mode` writes normalized extraction JSON:

```text
output/<input_stem>_<provider>_extraction.json
```

Extraction artifacts include local page triage metadata, extraction summary
counts, and source/page references on fields. This is the backend evidence layer
that packet validation will build on.

`--json-mode` validates the model response before writing output. Invalid or
missing required fields fail with explicit schema paths.

Raw provider responses can be saved with `--raw-output`.

## Full Plan

See [PLAN.md](PLAN.md) for the detailed architecture, implementation roadmap,
batch strategy, validation design, and Railway deployment plan.
