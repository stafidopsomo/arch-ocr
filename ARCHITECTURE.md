# arch-ocr Architecture

This document is the living architecture / roadmap reference. Setup, CLI, and
endpoints live in [README.md](README.md). The future Next.js production design
lives in [CLAUDE_DESIGN_PROMPT.md](CLAUDE_DESIGN_PROMPT.md).

## North Star

The product is not OCR. It is **packet validation**:

```text
document packet
-> page-level evidence extraction
-> entity clustering
-> cross-check validation
-> structured result JSON
-> optional human report
```

Architects upload 30–40 related pages for the same client, office, address,
property, or legal case. The system extracts printed / handwritten / stamped /
signed values with nearby text, groups repeated values, flags variations, and
emits a validation result based only on page evidence. The most important
deliverable is reliable structured JSON; reports are a thin layer on top.

## Product Layers

1. **Backend processing engine** (current focus). Local triage, cloud
   extraction, evidence JSON, clustering, validation, cost tracking. Frontend
   independent.
2. **Web app layer** (future). Accounts, history, report viewer, export. Keep
   past results; uploaded source files can be deleted after a short retention
   window.

## Pipeline (8 stages)

1. **Local triage** — `ocr_script.py triage`. PDF → page metadata, no API.
2. **Page rendering** — PyMuPDF rasterizes selected pages to images.
3. **Cloud extraction** — single LLM call per selected page
   ([worker/processor.py:508](worker/processor.py:508)) using
   `JSON_EXTRACTION_PROMPT` ([ocr/schema.py:27](ocr/schema.py:27)). Validated
   against `arch_ocr.extraction.v1`.
4. **Embedded-text scan** — deterministic regex pass for AFM / KAEK / ATAK in
   the text layer, so obvious identifiers aren't lost when vision misses them.
5. **Clustering** — `ocr/clustering.py`. Normalizes strings, groups exact
   repeats, computes fuzzy near-match groups for review hints.
6. **Validation** — `ocr/validation.py`. Rule-based checks emit `pass` /
   `warning` / `unknown` / `fail` with `evidence_refs`. No LLM.
7. **Reporting** — `ocr/reporting.py` (Markdown), `api/review.py` (HTML v1
   technical, HTML v2 architect). All deterministic.
8. **Review model** — `ocr/review_model.py` (`build_review_model`) shapes the
   packet for the v2 architect-facing report.

## Provider Strategy

Default path: render each PDF page to image client-side and send as image
parts. Avoids OpenRouter's `mistral-ocr` plugin 8-image cap and accuracy gap on
complex Greek forms. Native PDF input is reserved for text-layer / CAD exports.

**Recommended priority order** (research as of 2026-05-07):

1. `google/gemini-2.5-flash-lite` — primary; replaces 2.0-flash-lite before
   June 2026.
2. `google/gemini-2.5-flash` — accuracy-tier fallback; benchmark before
   adopting (4× the cost; worth it only if stamp / cadastral accuracy improves
   measurably).
3. `qwen/qwen2.5-vl-72b-instruct` — provider-independence benchmark candidate
   (image-parts only; 32K context).
4. `anthropic/claude-3.5-haiku` — low-confidence-page verifier only (10–20×
   cost).

Currently in production env: `gemini-3.1-flash-lite-preview` as primary,
`gemini-3-flash-preview` as fallback. Latest 1-page benchmark on Greek
elevator/technical doc:

| Model | Fields | Tokens | Latency |
|---|---|---|---|
| `gemini-3.1-flash-lite-preview` | 6 | 2,694 | ~23 s |
| `gemini-3-flash-preview` | 10 | 5,875 | ~20 s |

Gemma 3 IDs return 404 under the current Gemini API project. Gemma 4 IDs are
exposed but currently fail the JSON contract (timeout or prose response). Keep
Gemma in benchmark-mode until a Gemma-specific JSON strategy proves out.

## Demo Guardrails

The Railway demo is a controlled deployment, not production:

- max 10 uploaded files per packet
- max 20 selected pages per packet
- one active processing job at a time
- sequential provider calls (no parallel Gemini)
- local provider usage ledger for token / cost / rate visibility
- ordered model fallback via `OCR_MODEL_FALLBACKS`
- admin-only model benchmarks for comparing candidates on 1–2 pages

Storage is file-backed on a Railway Volume at `/data/arch-ocr` — no Postgres
yet. That is the deliberate next-phase boundary.

## Validation Layer

Validation is deterministic and evidence-based. Statuses are conservative:
checks emit `fail` only when contradicted by extracted evidence, not on
absence. Identifier validation separates KAEK / AFM / ATAK / permit / registry
/ unknown using extracted labels and nearby text. Subtype inference is added
at cluster + check layers; original field evidence is unchanged.

## Reports

- **Markdown report** (`/jobs/{id}/report`) — deterministic, full evidence
  layout for export.
- **Technical review v1** (`/jobs/{id}/review`) — Greek/English HTML, all
  validation checks + clusters + fuzzy groups + per-page errors.
- **Architect review v2** (`/jobs/{id}/review-v2`) — focused architect-facing
  summary: property identity, permits, people, issues, document map, source
  files, extraction errors. Lightbox-zoomable evidence thumbnails (full-res
  served by `/jobs/{id}/page-image`).

## Roadmap (active)

**Now**

- v2 report polish, JSON↔render parity, evidence verification UX.
- Client feedback loop on which v2 sections actually drive review decisions.

**Next**

- OpenRouter / Qwen / Gemini-2.5 benchmark on a representative Greek packet.
- Decide primary model for paid tier.

**Later (deferred)**

- Postgres + real accounts + subscriptions.
- Long-term report history.
- Next.js production frontend per `CLAUDE_DESIGN_PROMPT.md`.
- Admin settings UI to replace the 20+ env-var Railway config.

## Out of Scope

- Local OCR engines or local LLMs on Mac.
- Live API Gemini models (only `bidiGenerateContent`).
- Gemma 4 in normal fallback until JSON contract holds.
- Replacing human legal / engineering review.

## Useful Research Links

- OpenRouter PDF inputs — https://openrouter.ai/docs/guides/overview/multimodal/pdfs
- OpenRouter limits — https://openrouter.ai/docs/api/reference/limits
- Gemma through Gemini API — https://ai.google.dev/gemma/docs/core/gemma_on_gemini_api
- OpenRouter universal PDF support — https://openrouter.ai/announcements/universal-pdf-support
- Reducto Mistral vs Gemini OCR benchmark — https://reducto.ai/blog/lvm-ocr-accuracy-mistral-gemini
- Getomni open-source OCR benchmark — https://getomni.ai/blog/benchmarking-open-source-models-for-ocr
- Qwen2.5-VL — https://qwenlm.github.io/blog/qwen2.5-vl/
