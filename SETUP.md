# Setup Guide (WSL)

This guide configures the current LLM-only workflow with either OpenRouter or Ollama.

## 1. Install Base Packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

## 2. Create Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

## 3. Choose Provider

### Option A: OpenRouter

`.env`:

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_openrouter_key_here
OPENROUTER_MODEL=google/gemma-3-4b-it:free
```

Run:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf
```

### Option B: Ollama Cloud API

1. Create API key at `https://ollama.com/settings/keys`.
2. Set `.env`:

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

### Option C: Ollama on your Mac (from WSL)

Set `.env`:

```env
LLM_PROVIDER=ollama
OLLAMA_HOST=http://<mac-ip>:11434
OLLAMA_MODEL=qwen3-vl:8b
```

Run:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --provider ollama
```

## 4. Handwritten-Only Prompt Example

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

## 5. Convert PDF to Image and Run

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

./.venv/bin/python ocr_script.py test_inputs/example_page1.png \
  --provider ollama \
  --model qwen3-vl:235b-cloud \
  --output output/example_page1_feedback_ollama_cloud_image.md \
  --raw-output output/example_page1_raw_ollama_cloud_image.json
```

## 6. PDF Handling Recommendation

- Keep the current PDF -> image conversion for scanned and handwritten documents.
- Use `--dpi 130` to `--dpi 170` for better token efficiency.
- Use low `--max-pages` during testing.

## Common Errors

- Missing provider key: set `OPENROUTER_API_KEY` or `OLLAMA_API_KEY`.
- OpenRouter 429: free tier limit, retry or switch model.
- Model not found: pass valid model with `--model`.
- Timeout: increase `--timeout`.
