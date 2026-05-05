# Handoff

This file is for the next Codex session.

## Current State

The project has been reset into a cloud-first backend processing engine for
Greek property, architecture, and building/legal document packets.

Current backend flow:

```text
local page triage
-> cloud extraction
-> schema validation
-> enriched evidence JSON
-> folder-level packet JSON
```

Frontend, accounts, database, report history, and Railway API are intentionally
not implemented yet. They are planned as a later layer.

## Important Files

- `ocr_script.py` - active backend CLI.
- `README.md` - short project entry point.
- `PLAN.md` - detailed architecture and roadmap.
- `.env.example` - provider environment variables.
- `test_inputs/private/packet_001/` - private local packet fixture, ignored by git.

Old OCR/webapp files were removed from the active code path.

## Working Commands

Local triage, no API calls:

```bash
python ocr_script.py triage test_inputs/private/packet_001 \
  --output output/packet_001_triage.json
```

One-page Gemini extraction with validated evidence JSON:

```bash
python ocr_script.py test_inputs/example_page1.pdf \
  --provider gemini \
  --json-mode \
  --max-pages 1 \
  --output output/example_page1_gemini_extraction_validated.json \
  --raw-output output/example_page1_gemini_extraction_validated_raw.json
```

Packet extraction:

```bash
python ocr_script.py packet test_inputs/private/packet_001 \
  --provider gemini \
  --max-pages-per-file 2 \
  --output output/packet_001.json
```

## Implemented

- PDF/image page rendering with PyMuPDF.
- Gemini, OpenRouter, and Google Vision provider paths.
- Local per-page triage:
  - embedded text chars
  - image count
  - annotation count
  - ink ratio
  - page kind
  - needs vision/text layer flags
- `arch_ocr.triage.v1` output.
- `arch_ocr.extraction.v1` output.
- `page_evidence.v1` model prompt.
- Schema validation for model JSON.
- Enrichment of extraction output with:
  - page triage
  - extraction summary
  - source file/page refs
  - stable `field_ref`
- `packet` command that:
  - runs local triage for all files in a folder
  - processes the first N pages per file
  - extracts validated JSON one page at a time
  - keeps going when a page fails
  - emits `arch_ocr.packet.v1`
- deterministic clustering that:
  - normalizes repeated field values
  - groups names, addresses, dates, identifiers, people/roles, offices, and
    technical values
  - keeps all original `field_ref` evidence mentions
- local `cluster` command to rebuild clusters on an existing packet JSON
  without API calls
- local `report` command to render a Markdown evidence report from packet JSON
  without API calls
- local `validate` command to rebuild deterministic validation checks from
  clusters without API calls
- identifier subtype inference for:
  - KAEK
  - AFM
  - ATAK
  - permit numbers
  - registry IDs
  - unknown identifiers
- Private document folder ignored by git.
- `.env` ignored by git.
- `.env` can be copied from `.env.example`; placeholder API keys are rejected.

## Known Test Outputs

Useful local outputs currently exist under `output/`, which is ignored by git:

- `packet_001_triage.json`
- `example_page1_gemini_extraction_validated.json`
- `example_page1_gemini_extraction_validated_raw.json`
- several `review_*` first-page extraction files from the private packet.

These are debugging artifacts, not source files.

## Next Task

Run packet mode against the private packet after adding `GEMINI_API_KEY` to
`.env`:

```bash
python ocr_script.py packet test_inputs/private/packet_001 \
  --provider gemini \
  --max-pages-per-file 2 \
  --output output/packet_001.json
```

Then inspect `errors`, `totals`, and the first few `page_extractions` before
increasing `--max-pages-per-file`.

Clusters can be rebuilt locally after prompt/schema tweaks without spending API
calls:

```bash
python ocr_script.py cluster output/packet_001.json
```

Reports can be generated locally:

```bash
python ocr_script.py report output/packet_001.json \
  --output output/packet_001_report.md
```

Validation checks can be rebuilt locally:

```bash
python ocr_script.py validate output/packet_001.json
```

Current `output/packet_001.json` has 12 checks:

- 7 pass
- 5 warning

Current `output/packet_001_full.json` has 17 checks after full extraction and
identifier subtype inference:

- 9 pass
- 7 warning
- 1 unknown

The full packet has now been retried successfully:

- 31 pages extracted
- 0 failed pages
- 191 extracted fields
- 2 fuzzy near-match groups:
  - engineer spelling variant: Ηλία Καϊμακτζόγλου / Ηλία Καϊμακτσόγλου
  - person-name initial variant: Β. Βουοδαμή / Κ. Βουοδαμή
- executive summary is included in packet JSON and Markdown reports
- cost summary is included in packet JSON and Markdown reports; current saved
  packet outputs were produced before usage metadata was stored, so they show
  calls without usage until pages are reprocessed

Identifier subtype summary:

- AFM: 1
- KAEK: 1
- permit_number: 9
- registry_id: 9
- unknown_identifier: 1

Roadmap decision:

- Deep domain-specific validation is deliberately parked until we have many
  more real packets. The current fixture set is too small; overfitting now would
  be a 200hp engine on a scooter.
- Milestone 7 provider comparison / Google Vision hybrid mode is deferred.
  Gemini is enough for the first demo path.
- Next implementation step: Railway API demo with strict free-tier guardrails.

Railway demo guardrails to implement first:

- Done: FastAPI demo service in `app.py`
- Done: `Procfile` for Railway start command
- Done: maximum 10 uploaded files per packet
- Done: maximum 20 selected pages per packet
- Done: one active processing job at a time
- Done: sequential provider calls, no parallel Gemini calls
- Done: local usage ledger and throttle env vars before exposing the app to the friend
- Done: file-backed job/result storage under `OCR_STORAGE_DIR`, intended for
  a Railway Volume mounted at `/data`
- Later: Postgres users/admins/accounts/subscriptions

## Notes

- The current CLI still supports the original single-file extraction command.
- The `triage` command is local only and does not call cloud APIs.
- The private packet contains mixed real-world document types:
  - scanned contract/notarial pages
  - scanned declaration/table pages
  - scanned maintenance form
  - born-digital/mixed cadastral PDF
- For real clients, paid cloud tier is preferred over free tier because of data
  privacy and product-improvement terms.
- Gemini rate limits are RPM, TPM, and RPD, applied per project and visible in
  AI Studio. Do not hard-code official free-tier numbers; configure app limits
  with env vars and keep them stricter than the provider limits for demos.
