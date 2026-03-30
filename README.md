# arch-ocr

This repository is now a focused CLI workflow:

- Send a PDF page (for example `test_inputs/example_page1.pdf`) to a vision LLM.
- Receive model feedback or structured extraction output.

Legacy OCR and web app paths are no longer the active workflow.

## Current Flow

- Script: `ocr_script.py`
- Pipeline: PDF -> rendered page image(s) -> OpenRouter vision model -> markdown + optional raw JSON
- Default output path: `output/<pdf_stem>_feedback.md`

## Requirements

- Python 3.10+
- OpenRouter API key

Dependencies in `requirements.txt`:

- requests
- python-dotenv
- PyMuPDF

## Quick Start (WSL / Ubuntu)

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Create your env file:

```bash
cp .env.example .env
```

Edit `.env`:

```env
OPENROUTER_API_KEY=your_openrouter_key_here
OPENROUTER_MODEL=google/gemma-3-4b-it:free
```

`OPENROUTER_MODEL` is optional.

## Basic Run

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf
```

Saves feedback to:

- `output/example_page1_feedback.md`

## Prompt While Sending The File

Yes, you can pass a custom prompt with `--prompt`.

Example: handwritten-only extraction with strict JSON output.

```bash
PROMPT=$(cat <<'EOF'
Look only for handwritten content on the page.
Ignore printed/typed text unless it helps locate a handwritten field.
Return ONLY valid JSON with this exact schema:
{
  "handwritten_fields": [
    {
      "label": "short field name",
      "value": "detected handwritten value",
      "confidence": "high|medium|low",
      "evidence": "short quote or location hint"
    }
  ],
  "notes": ["uncertainty notes"]
}
If no handwritten content is found, return {"handwritten_fields":[],"notes":["none"]}.
Do not include markdown.
EOF
)

./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf \
  --model qwen/qwen3-vl-8b-instruct \
  --prompt "$PROMPT" \
  --output output/example_page1_handwritten_feedback.md \
  --raw-output output/example_page1_handwritten_raw.json
```

## Model Options

Free (can be rate-limited):

- `google/gemma-3-4b-it:free` (default)
- `google/gemma-3-12b-it:free`
- `google/gemma-3-27b-it:free`
- `nvidia/nemotron-nano-12b-v2-vl:free`

Low-cost paid options:

- `qwen/qwen3-vl-8b-instruct`
- `meta-llama/llama-3.2-11b-vision-instruct`
- `google/gemini-2.0-flash-lite-001`
- `amazon/nova-lite-v1`

Set a model per run:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --model qwen/qwen3-vl-8b-instruct
```

## OpenRouter Troubleshooting

- `404 No endpoints found`: model is unavailable or retired. Switch to another model.
- `429 rate-limited`: common on free models. Retry or use a paid model.
- weak extraction quality: use stricter prompt + higher quality model (for example `qwen/qwen3-vl-8b-instruct` or higher).

## Local Ollama Note

Current script path is OpenRouter-only.

Why local chat apps often fail with attachments:

- many local models are text-only and cannot read images;
- even vision models require image-aware API payloads, not plain text chat;
- some chat UIs silently degrade image input when model/tooling is not vision-compatible.

If needed, local Ollama support can be added as a second provider path.
