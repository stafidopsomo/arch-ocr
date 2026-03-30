# Setup Guide (WSL)

This is the final setup path for the LLM-only workflow.

## 1. Install System Packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

## 2. Create Virtual Environment

From repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## 3. Configure OpenRouter

```bash
cp .env.example .env
```

Edit `.env`:

```env
OPENROUTER_API_KEY=your_openrouter_key_here
OPENROUTER_MODEL=google/gemma-3-4b-it:free
```

`OPENROUTER_API_KEY` is required.

## 4. Run Baseline Command

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf
```

Expected outputs:

- terminal response
- `output/example_page1_feedback.md`

## 5. Run Handwritten-Only Extraction

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

## 6. Model Selection Tips

- free default: `google/gemma-3-4b-it:free`
- better extraction quality: `qwen/qwen3-vl-8b-instruct`
- free models may return `429` during peak load

## Common Errors

- missing key: set `OPENROUTER_API_KEY`
- `404 No endpoints found`: model unavailable, switch `--model`
- `429 rate-limited`: retry or use paid model
- timeout: increase `--timeout 180`
