## Research Links (source of truth)

OpenRouter PDF inputs:
https://openrouter.ai/docs/guides/overview/multimodal/pdfs

OpenRouter limits:
https://openrouter.ai/docs/api/reference/limits

Gemma through Gemini API:
https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api

OpenRouter universal PDF support announcement:
https://openrouter.ai/announcements/universal-pdf-support

Mistral OCR vs Gemini Flash 2.0 accuracy benchmark (Reducto):
https://reducto.ai/blog/lvm-ocr-accuracy-mistral-gemini

Open-source OCR model benchmark (Getomni):
https://getomni.ai/blog/benchmarking-open-source-models-for-ocr

OCR vs VLM-OCR accuracy (DataUnboxed):
https://www.dataunboxed.io/blog/ocr-vs-vlm-ocr-naive-benchmarking-accuracy-for-scanned-documents

Qwen2.5-VL blog (Qwen Team):
https://qwenlm.github.io/blog/qwen2.5-vl/

OpenRouter vision models collection:
https://openrouter.ai/collections/vision-models

Gemini 2.5 Flash on OpenRouter:
https://openrouter.ai/google/gemini-2.5-flash

Gemini 2.0 Flash Lite on OpenRouter:
https://openrouter.ai/google/gemini-2.0-flash-lite-001

Gemini 2.5 Flash Lite on OpenRouter:
https://openrouter.ai/google/gemini-2.5-flash-lite

Mistral AI on OpenRouter:
https://openrouter.ai/provider/mistral

---

## OpenRouter PDF Input Modes (researched 2026-05-07)

Four strategies, controlled via the `plugins` parameter:

### A. Native Model PDF Support
- Pass PDF as a `file` content type — no plugin needed.
- Automatically used for: Gemini, Anthropic, OpenAI providers.
- Charged as standard input tokens; no extra per-page fee.
- Best for: text-layer PDFs with native vision models.

### B. file-parser: cloudflare-ai
  {"plugins": [{"id": "file-parser", "pdf": {"engine": "cloudflare-ai"}}]}
- Converts PDF to markdown via Cloudflare Workers AI. FREE.
- Text-only — no OCR on scanned pages.
- USELESS for scanned Greek property packets (pure image pages).

### C. file-parser: mistral-ocr
  {"plugins": [{"id": "file-parser", "pdf": {"engine": "mistral-ocr"}}]}
- Runs Mistral OCR pipeline to extract text + images. $0.002/page.
- Hard cap: 8 images per PDF forwarded to downstream model.
- After OCR, content is passed to any model on OpenRouter.
- Default non-native fallback when no plugin specified.
- Real-world accuracy on complex forms: ~45% (Reducto benchmark, 43-point gap vs Gemini).
- Hallucination risk on checkboxes, tables, stamps.
- NOT recommended as sole OCR for Greek architecture packets.

### D. Image-per-page (manual, current app strategy)
- Render each PDF page to JPEG/PNG client-side, send as image_url parts.
- No plugin, no per-page fee, no image cap.
- Full pixel fidelity — stamps, handwriting, architectural drawings preserved.
- RECOMMENDED for scanned Greek property packets.

---

## OpenRouter Rate Limits (researched 2026-05-07)

- Global limits apply per ACCOUNT, not per API key.
- Creating additional API keys does NOT bypass global limits.
- Different model IDs have SEPARATE capacity pools.
- Free models (:free suffix): per-minute RPM + daily RPD caps (exact values in dashboard).
- Paid models: no fixed published RPM/TPM wall; Cloudflare blocks extreme abuse.
- Accounts with negative credit balance receive HTTP 402.
- Key-level credit query: GET /api/v1/key → returns limit, usage, remaining.
- Strategy for burst throughput: use different model IDs in parallel (separate pools).

---

## Gemma through Gemini API (findings confirmed 2026-05-07)

- gemma-3-4b-it, gemma-3-12b-it, gemma-3-27b-it: 404 under current key (not exposed by ListModels).
- gemma-4-26b-a4b-it, gemma-4-31b-it: exposed by ListModels, support generateContent,
  but prompts fail: one timed out, one returned prose instead of JSON.
- Gemma 4 excluded from fallback until it can return valid schema JSON within timeout.

---

## Model Benchmark Accuracy Notes (researched 2026-05-07)

Reducto RD-FormsBench (real-world complex documents):
  Gemini 2.0 Flash:  80.1%
  Mistral OCR:       45.3%   (43-point gap — Mistral significantly weaker on complex forms)

Mistral internal benchmark (their own distribution, less representative):
  Mistral OCR:  94.89%
  Gemini:       88.49%

Getomni JSON extraction benchmark:
  Gemini/GPT-4o tier: ~75%
  Mistral OCR:        72.2%

Key takeaway: For handwritten fields, stamps, non-standard layouts (Greek property packets),
Gemini-class models outperform Mistral OCR in real-world conditions.

---

## Candidate Model List (for benchmarking, 2026-05-07)

Format: model_id | input_mode | est. cost/page | structured JSON | notes

google/gemini-2.0-flash-lite-001
  - image-parts or native-pdf
  - ~$0.00008–0.00015/page
  - Native JSON mode + tool use
  - CURRENT MODEL. Retiring June 1, 2026. Baseline.

google/gemini-2.5-flash-lite
  - image-parts or native-pdf
  - ~$0.00012–0.00020/page
  - Native JSON mode + tool use
  - PRIORITY UPGRADE. Direct replacement when 2.0-flash-lite retires ($0.10/M).
    Reasoning disabled by default (enable via param). 1M context.

google/gemini-2.5-flash
  - image-parts or native-pdf
  - ~$0.00035–0.00060/page
  - Native JSON mode + tool use + thinking
  - ACCURACY STEP-UP CANDIDATE. 4× better reasoning on complex layouts.
    1M context. Benchmark against 2.5 Flash Lite on representative Greek packet pages.
    Worth the 4× premium if stamp/cadastral number accuracy improves measurably.

google/gemini-3.1-flash-lite-preview  (current GEMINI_MODEL in Railway)
  - image-parts or native-pdf
  - ~$0.00030/page (est.)
  - Native JSON + tool use + full thinking levels
  - Next-gen; explicitly trained for data extraction. Preview SLA uncertain.

anthropic/claude-3.5-haiku
  - image-parts or native-pdf
  - ~$0.001–0.002/page
  - Native tool use (most reliable JSON schema enforcement)
  - 10–20× more expensive. Use as fallback verifier for low-confidence pages
    (illegible cadastral numbers, ambiguous stamp dates).

qwen/qwen2.5-vl-72b-instruct
  - image-parts ONLY (no native PDF)
  - ~$0.00030/page est. ($0.25/M input)
  - JSON mode via prompt or OpenRouter structured output param
  - 96.4% DocVQA, 88.8% OCRBench, 32-language OCR training (Greek included).
    Open weights = provider independence.
    32K context hard limit → max ~20 pages/call at 300 DPI.
    BENCHMARK CANDIDATE for provider redundancy.

qwen/qwen3-vl-32b-instruct
  - image-parts ONLY
  - ~$0.00020–0.00040/page est.
  - JSON mode via prompt
  - Next-gen Qwen VL. 256K context, 32+ language OCR, better spatial understanding.
    Less production-tested as of mid-2025.

---

## Recommended Strategy (2026-05-07)

PDF pre-processing:
  - Scanned packets → render pages to images client-side → send as image-parts.
    (Avoids Mistral OCR 8-image cap and accuracy issues.)
  - Text-layer PDFs (CAD exports) → native-pdf mode with Gemini/Claude. Free, no OCR.
  - Never use cloudflare-ai for scanned content.

Model priority order:
  1. google/gemini-2.5-flash-lite  (primary; replace 2.0-flash-lite before June 2026)
  2. google/gemini-2.5-flash       (fallback / accuracy tier)
  3. qwen/qwen2.5-vl-72b-instruct  (benchmark for provider independence + cost parity)
  4. anthropic/claude-3.5-haiku    (low-confidence page verifier only)

Throughput:
  - Multiple API keys do NOT bypass OpenRouter account-level limits.
  - Use different model IDs in parallel for burst (separate capacity pools).
  - For large batch jobs, go direct to Gemini API (bypass OpenRouter markup + own rate limits).

---

## Gemini Direct API Benchmark Observations (from admin benchmarks)

gemini-3.1-flash-lite-preview: 6 fields, 2,694 tokens, ~23 sec on 1-page Greek elevator doc.
gemini-3-flash-preview:        10 fields, 5,875 tokens, ~20 sec — stronger quality.
