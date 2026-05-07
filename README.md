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

## Current Status

The project now has two usable surfaces:

- a local CLI for packet extraction, retry, clustering, validation, and
  deterministic Markdown reporting.
- a one-service Railway demo in `app.py` with uploads, background jobs,
  usage/cost tracking, review HTML, JSON export, markdown report export, and a
  static prototype UI under `/design/arch-ocr.html`.

The Railway demo is intentionally file-backed on a mounted Volume. It does not
yet use Postgres, real user accounts, subscriptions, or long-term report
history. Those are the next product layer once the friend demo proves the
workflow.

Recent review-page improvements:

- Review pages are available in Greek by default with an English toggle.
- Stored packets are re-analyzed dynamically when opening JSON/report/review,
  so older jobs can benefit from improved deterministic checks when their
  source PDFs still exist on the Volume.
- Embedded PDF text is scanned deterministically for AFM, KAEK, and ATAK so
  obvious text-layer identifiers are not missed by the vision model.
- Validation evidence refs can show source-page thumbnails in the review page.
- Review pages show page coverage explicitly: triaged pages, selected pages,
  extracted pages, and pages skipped by the current demo cap.

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

Folder-level packet extraction:

```bash
python ocr_script.py packet test_inputs/private/packet_001 \
  --provider gemini \
  --max-pages-per-file 2 \
  --output output/packet_001.json
```

Retry only failed pages in an existing packet JSON:

```bash
python ocr_script.py retry-failed output/packet_001.json
```

Add or rebuild clusters on an existing packet JSON, with no API calls:

```bash
python ocr_script.py cluster output/packet_001.json
```

Generate a deterministic evidence report from packet JSON, with no API calls:

```bash
python ocr_script.py report output/packet_001.json \
  --output output/packet_001_report.md
```

Add or rebuild validation checks on an existing packet JSON, with no API calls:

```bash
python ocr_script.py validate output/packet_001.json
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

Packet mode writes `arch_ocr.packet.v1` JSON with packet metadata, the local
triage artifact, one validated page extraction artifact per successful page,
per-page errors, deterministic clusters, and totals. It keeps going when an
individual page fails. Retry mode reads that same artifact, reprocesses only
recorded failed pages, and then recalculates totals, clusters, and validation
checks.

New page extractions also store normalized provider usage metadata when the API
returns it. Packet JSON includes a `cost_summary` with token totals, provider
reported cost when available, and estimated USD cost. Existing packet artifacts
created before this feature will show partial cost summaries until pages are
reprocessed. Pricing can be overridden without code changes:

```bash
OCR_COST_INPUT_PER_1M_USD=0.25 \
OCR_COST_OUTPUT_PER_1M_USD=1.50 \
OCR_COST_CACHED_INPUT_PER_1M_USD=0.025 \
python ocr_script.py validate output/packet_001.json
```

Clusters group repeated normalized names, addresses, dates, permit/property
identifiers, offices, engineers/architects, owners/applicants, and technical
values while preserving every original `field_ref`. Fuzzy near-match groups add
review hints for likely address/name variants without merging or rewriting the
underlying exact clusters.

Report mode renders a Markdown evidence report from packet JSON and clusters.
It summarizes processing totals, an executive summary, validation checks, fuzzy
near-matches, repeated evidence, identity/property evidence, errors, warnings,
and recommended manual review points.

Validation checks are deterministic and evidence-based. They emit statuses such
as `pass`, `warning`, `unknown`, or conservative `fail` with `evidence_refs`;
they do not replace human legal or engineering review.

Identifier validation separates KAEK, AFM, ATAK, permit numbers, registry IDs,
and unknown identifiers using extracted labels and nearby text. The original
field evidence remains unchanged; subtype inference is added at the cluster and
check layer.

`--json-mode` validates the model response before writing output. Invalid or
missing required fields fail with explicit schema paths.

Raw provider responses can be saved with `--raw-output`.

## Demo Deployment Guardrails

The first Railway deployment is a controlled demo, not the final production
shape. Keep provider usage conservative while using free or low-tier Gemini:

- maximum 10 uploaded files per packet
- maximum 20 selected pages per packet
- one active processing job at a time
- sequential provider calls, no parallel Gemini calls
- local provider usage ledger for token/cost/rate visibility

Gemini rate limits are measured in requests per minute, tokens per minute, and
requests per day, and they apply per Google project. Active limits should be
checked in AI Studio and mirrored into app env vars instead of hard-coded.

Planned env controls:

```env
OCR_MAX_FILES_PER_PACKET=10
OCR_MAX_PAGES_PER_PACKET=20
OCR_PROVIDER_MIN_SECONDS_BETWEEN_CALLS=4
OCR_PROVIDER_MAX_REQUESTS_PER_MINUTE=10
OCR_PROVIDER_MAX_REQUESTS_PER_DAY=100
```

## Railway Demo Service

The repo includes a FastAPI demo service in `app.py`. It stores uploaded files,
job metadata, packet JSON, markdown reports, and usage ledger events under
`OCR_STORAGE_DIR`, so on Railway this should point at a mounted Volume.

Local run:

```bash
uvicorn app:app --reload
```

Railway uses the included `Procfile`:

```text
web: uvicorn app:app --host 0.0.0.0 --port $PORT --no-access-log
```

Access logs are disabled for the demo because tokenized backward-compatible
links may otherwise appear in Railway logs. Use per-job structured logs instead.

Useful endpoints:

- `GET /` - redirects to the connected login/dashboard UI.
- `POST /login` / `POST /logout` / `GET /session` - demo cookie login.
- `GET /design/arch-ocr.html` - static demo UI prototype connected to the demo
  API.
- `GET /jobs` - list stored jobs for the demo UI/admin workflow.
- `POST /jobs` - upload packet files and start a background job.
- `POST /jobs/draft` - upload files into a draft job without starting
  validation yet.
- `POST /jobs/{job_id}/start` - start validation for a drafted upload.
- `POST /jobs/{job_id}/abort` - request cooperative job abort before the next
  provider call.
- `DELETE /jobs/{job_id}` - delete a job folder, including stored uploads,
  packet JSON, report, and logs.
- `GET /jobs/{job_id}` - job status JSON.
- `GET /jobs/{job_id}/packet` - result packet JSON, rebuilt with current
  deterministic analysis when source PDFs are still present.
- `GET /jobs/{job_id}/report` - result markdown report, rebuilt from the
  current analysis.
- `GET /jobs/{job_id}/review?token=...&lang=el` - browser review page with
  Greek labels, validation checks, clusters, and evidence thumbnails.
- `GET /jobs/{job_id}/page-thumbnail?token=...&field_ref=...` - thumbnail for
  a specific evidence field source page.
- `GET /jobs/{job_id}/logs?token=...` - auto-refreshing structured job log
  page for triage, page processing, retries, throttling, and completion.
- `GET /jobs/{job_id}/events?token=...` - structured job event JSON.
- `GET /usage` - usage page in a browser, or JSON when requested as
  `application/json`.
- `GET /admin` - simple demo admin page.

Railway setup checklist:

- Create a Railway service from this repo.
- Add a Railway Volume and mount it at `/data`.
- Set `OCR_STORAGE_DIR=/data/arch-ocr`.
- Set `GEMINI_API_KEY` manually in Railway variables.
- Set `OCR_ADMIN_TOKEN` to a strong random value.
- Set `OCR_ADMIN_USERNAME`, `OCR_ADMIN_PASSWORD`, `OCR_STARTER_USERNAME`, and
  `OCR_STARTER_PASSWORD` for the demo login. If passwords are omitted, the demo
  falls back to `OCR_ADMIN_TOKEN`.
- Keep `OCR_DEMO_REQUIRE_TOKEN=true`.
- Set the demo limit env vars from the guardrails above.

For the first demo, the app uses file-backed storage on the Volume rather than
Postgres. Postgres/user accounts/subscriptions can be added once the friend demo
proves the workflow.

## Current Demo Limitations

- The demo has cookie login for `admin` and `stavret`, but it is still
  file-backed demo authentication, not final account management.
- Backward-compatible token access still exists for older direct links. Rotate
  `OCR_ADMIN_TOKEN` if it appears in screenshots, browser history, or logs.
- The current page cap is a demo/free-tier guardrail. If a packet has more
  selected pages than `OCR_MAX_PAGES_PER_PACKET`, the remaining pages are not
  extracted until the cap is raised or a smaller packet is uploaded.
- Source PDFs must remain on the Volume for dynamic re-analysis and evidence
  thumbnails to work on old jobs.
- The static UI is good enough for presentation/testing, but it is not the final
  Next.js/accounts/admin product.

## Full Plan

See [PLAN.md](PLAN.md) for the detailed architecture, implementation roadmap,
batch strategy, validation design, and Railway deployment plan.
