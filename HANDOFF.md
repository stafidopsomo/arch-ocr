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
- Private document folder ignored by git.
- `.env` ignored by git.

## Known Test Outputs

Useful local outputs currently exist under `output/`, which is ignored by git:

- `packet_001_triage.json`
- `example_page1_gemini_extraction_validated.json`
- `example_page1_gemini_extraction_validated_raw.json`
- several `review_*` first-page extraction files from the private packet.

These are debugging artifacts, not source files.

## Next Task

Implement packet mode.

Proposed command:

```bash
python ocr_script.py packet test_inputs/private/packet_001 \
  --provider gemini \
  --max-pages-per-file 2 \
  --output output/packet_001.json
```

First version should:

1. Run local triage for all supported files in the folder.
2. Process only the first N pages per file.
3. Call the existing validated JSON extraction path per page or per selected
   file/page group.
4. Keep going when one page fails.
5. Emit `arch_ocr.packet.v1` with:
   - packet metadata
   - triage artifact
   - page extraction artifacts
   - per-page errors
   - totals

Do not implement clustering yet. First prove folder-level packet extraction.

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
