# Handoff

Use these files as the source of truth:

- [README.md](README.md) - setup, CLI commands, Railway demo usage, endpoints,
  guardrails, and known demo limitations.
- [PLAN.md](PLAN.md) - architecture, milestone status, deferred work, and next
  implementation steps.
- [findings.txt](findings.txt) - current external research links for OpenRouter
  PDF input, OpenRouter limits, and Gemma API docs.

## Current Snapshot

- The backend processing engine is usable through the CLI and the FastAPI demo
  service.
- Railway demo storage is file-backed under `OCR_STORAGE_DIR`, intended for a
  mounted Volume such as `/data/arch-ocr`.
- The demo has upload/job status, JSON export, Markdown report export, usage
  tracking, admin/job listing, a browser usage page, live job logs, delete/abort
  actions, and a Greek review page.
- Stored packets are dynamically re-analyzed when opening packet/report/review
  endpoints, so old jobs can benefit from newer deterministic checks while
  source PDFs still exist.
- The static UI is still a one-service demo layer. Postgres, real users,
  subscriptions, and long-term report history are intentionally deferred.

## Current Railway Shape

Railway currently has many env vars because the demo is carrying runtime limits,
auth, model selection, and storage configuration without a database/admin
settings layer. This is acceptable for the demo but should be simplified before
production.

Important env groups:

- Auth/demo: `OCR_ADMIN_TOKEN`, `OCR_ADMIN_USERNAME`, `OCR_ADMIN_PASSWORD`,
  `OCR_STARTER_USERNAME`, `OCR_STARTER_PASSWORD`, `OCR_DEMO_REQUIRE_TOKEN`.
- Storage: `OCR_STORAGE_DIR=/data/arch-ocr` with a Railway Volume mounted at
  `/data`.
- Normal OCR model path: `CLOUD_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`,
  `OCR_MODEL_FALLBACKS`.
- Demo guardrails: `OCR_MAX_FILES_PER_PACKET`, `OCR_MAX_PAGES_PER_PACKET`,
  `OCR_PROVIDER_MIN_SECONDS_BETWEEN_CALLS`,
  `OCR_PROVIDER_MAX_REQUESTS_PER_MINUTE`, `OCR_PROVIDER_MAX_REQUESTS_PER_DAY`,
  `OCR_PROVIDER_MAX_RETRIES`, retry backoff vars, and timeout.
- Admin model testing: `OCR_BENCHMARK_MODELS`, `OCR_BENCHMARK_MAX_MODELS`,
  `OCR_BENCHMARK_MAX_PAGES`.

## Model Benchmark Findings

Recent admin benchmarks were run against the same Greek elevator/technical page.
The quality signal matters as much as cost/limits because the report is only as
good as the extracted page evidence.

Observed results:

- `gemini-3.1-flash-lite-preview` succeeded, but extracted fewer fields in the
  latest 1-page comparison: 6 fields, 2,694 tokens, about 23 seconds.
- `gemini-3-flash-preview` succeeded and looked stronger on quality: 10 fields,
  5,875 tokens, about 20 seconds.
- `gemma-3-4b-it`, `gemma-3-12b-it`, and `gemma-3-27b-it` failed with 404 under
  the current Gemini API key/project. `ListModels` does not expose those models
  for this key, even though Google docs still show Gemma 3 hosted examples.
- `gemma-4-26b-a4b-it` and `gemma-4-31b-it` are exposed by `ListModels` and
  support `generateContent`, but current prompts failed: one timed out and one
  returned prose instead of strict JSON. They are too slow/unreliable for the
  demo path unless a Gemma-specific JSON strategy is implemented.
- `gemini-3.1-flash-live-preview` exists, but it only exposes
  `bidiGenerateContent`; it is a Live API model and is out of scope for the
  batch OCR flow.

Current practical Gemini recommendation:

```env
GEMINI_MODEL=gemini-3.1-flash-lite-preview
OCR_MODEL_FALLBACKS=gemini-3-flash-preview
OCR_BENCHMARK_MODELS=gemini-3.1-flash-lite-preview,gemini-3-flash-preview,gemma-4-26b-a4b-it,gemma-4-31b-it
```

## Next Research Task

Focus on best model selection before adding production accounts or more UI
surface. The immediate question is quality/cost/reliability for Greek scanned
architecture/property packets.

Research scope for Claude:

1. Broaden model search beyond direct Gemini API, especially OpenRouter.
2. Check models that can accept PDF or image input and return reliable
   structured JSON.
3. Pay attention to scanned Greek documents, handwritten fields, stamps, and
   long structured output, not only benchmark price.
4. Compare OpenRouter PDF strategies:
   - native file input where supported;
   - `file-parser` with `cloudflare-ai` for cheap text extraction;
   - `file-parser` with `mistral-ocr` for scanned/image-heavy PDFs;
   - current app strategy of rendering pages to image and sending image parts.
5. Track rate limits/credits and whether load can be spread by model/provider.
   OpenRouter docs note that extra API keys do not bypass global limits, but
   different models have different limits.
6. Produce a short candidate list for actual benchmark implementation:
   model id, provider, input mode, expected cost, expected limits, structured
   output support, and why it may beat or complement Gemini 3.1 Flash Lite.

Useful links:

- OpenRouter PDF inputs:
  https://openrouter.ai/docs/guides/overview/multimodal/pdfs
- OpenRouter limits:
  https://openrouter.ai/docs/api/reference/limits
- Gemma through Gemini API:
  https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api

## Not Next

- Do not prioritize Postgres, real accounts, subscriptions, or full production
  admin settings until the model path is clearer.
- Do not make Gemma 4 part of normal fallback until it can return valid schema
  JSON consistently and within timeout.
- Do not use Live API models for the current packet OCR workflow.
