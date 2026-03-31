# arch-ocr

This repository is now a focused CLI workflow for document understanding with vision LLMs.

- Input: PDF page(s) or image file(s)
- Processing: render PDF pages to images
- Output: model feedback in markdown + optional raw API JSON

Supported providers:

- OpenRouter
- Ollama (local daemon, remote daemon, or ollama.com cloud API)

## Requirements

- Python 3.10+

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
cp .env.example .env
```

## Provider Modes

### 1) OpenRouter (default)

Set in `.env`:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_openrouter_key_here
OPENROUTER_MODEL=google/gemma-3-4b-it:free
```

Run:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf
```

### 2) Ollama Cloud API

Create Ollama API key at `https://ollama.com/settings/keys`.

Set in `.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_HOST=https://ollama.com
OLLAMA_API_KEY=your_ollama_api_key
OLLAMA_MODEL=qwen3-vl:8b
```

Run:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --provider ollama
```

### 3) Ollama Remote Daemon (your Mac)

If your Mac daemon is reachable from WSL:

```env
LLM_PROVIDER=ollama
OLLAMA_HOST=http://<mac-ip>:11434
OLLAMA_MODEL=qwen3-vl:8b
```

Run:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --provider ollama
```

No API key is needed for plain local daemon endpoints unless you add your own proxy/auth.

## Image-First Flow

Convert the existing sample PDF page to a PNG:

```bash
./.venv/bin/python - <<'PY'
import fitz
from pathlib import Path

pdf = Path('test_inputs/example_page1.pdf')
out = Path('test_inputs/example_page1.png')
doc = fitz.open(pdf)
pix = doc[0].get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
pix.save(out)
print(out)
PY
```

Run with image input using Ollama cloud model through your signed-in local daemon:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.png \
  --provider ollama \
  --model qwen3-vl:235b-cloud \
  --output output/example_page1_feedback_ollama_cloud_image.md \
  --raw-output output/example_page1_raw_ollama_cloud_image.json
```

## Prompt While Sending The File

Yes, use `--prompt`.

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
  --provider ollama \
  --model qwen3-vl:8b \
  --prompt "$PROMPT" \
  --output output/example_page1_handwritten_feedback.md \
  --raw-output output/example_page1_handwritten_raw.json
```

## PDF vs Images (Important)

Short answer: keep the current image pipeline for scanned forms.

- Many vision endpoints work best with image inputs, not raw PDF bytes.
- PDF pages are currently rendered to PNG images before sending.
- For token/cost efficiency, avoid very high DPI for full pages.

Recommended defaults:

- `--dpi 130` to `--dpi 170` for normal forms.
- `--max-pages 1` while testing prompts and models.

When to switch strategy:

- born-digital text PDFs: extract text directly first, use vision only for signatures/stamps/handwriting zones.
- scanned/handwritten PDFs: image-first is the right path.

## CLI Reference

```bash
./.venv/bin/python ocr_script.py --help
```

Main flags:

- `--provider openrouter|ollama`
- `--model <model-name>`
- `--host <ollama-host>`
- `--prompt "..."`
- `--max-pages <n>`
- `--dpi <n>`
- `--api-key <key>`
- `--raw-output <path>`

## Output Files

Default output:

- `output/<pdf_stem>_feedback.md`

Optional raw response:

- path from `--raw-output`

## Troubleshooting

- OpenRouter `404 No endpoints found`: model is unavailable, switch model.
- OpenRouter `429`: free tier throttling, retry or use paid model.
- Ollama cloud auth errors: set `OLLAMA_API_KEY` and use `https://ollama.com` host.
- Poor attachment results in chat tools: model may be text-only or client may not send proper image payload format.
