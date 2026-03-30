# arch-ocr

Local OCR pipeline for Greek property documents (Ηλεκτρονική Ταυτότητα Κτιρίου).

The pipeline processes one or more scanned PDFs and generates one combined PDF report.

- Page 1 contains an HTK summary table (best extracted values and page references).
- Remaining pages contain side-by-side page image and extracted text.

## Current Status

- End-to-end pipeline runs locally on macOS.
- Multi-engine extraction is implemented: ocr, vlm, vision, hybrid, hybrid_vision, hybrid_all.
- VLM and Vision only modes now skip Tesseract OCR pre-processing.
- Vision-only mode now fails fast on API errors instead of silently continuing with empty text.
- Web UI supports per-job engine and VLM model selection.
- Output format is PDF.

### Hardware Constraints For Local VLM

- Running larger vision models on 16GB systems can trigger out-of-memory failures on full-resolution pages.
- Use single-page tests, downscale input pages, or smaller models such as glm-ocr and qwen3.5:0.8b.

Important quality note:

- Greek glyph rendering in the generated PDF text layer still needs font-path improvement.

## Scope

In scope:

- Local OCR of scanned Greek property PDFs
- HTK field extraction for key identity and property fields
- Local web upload workflow for a single tester
- Deterministic PDF output artifact for review

Out of scope (for now):

- Production authentication and multi-tenant security
- Cloud hosting and autoscaling
- Guaranteed handwritten-text accuracy without review
- Zero-touch legal-grade automation

## Extracted Fields

| Field | Greek |
| ----- | ----- |
| First name | Όνομα |
| Last name | Επώνυμο |
| Father's name | Πατρώνυμο |
| Mother's name | Μητρώνυμο |
| Year of birth | Έτος γέννησης |
| Place of birth | Τόπος γέννησης |
| Address | Διεύθυνση |
| Property number | Αριθμός ακινήτου |
| Property use | Χρήση ακινήτου |
| Number of floors | Αριθμός ορόφων |
| ID card number | ΑΔΤ |
| Phone | Τηλέφωνο |
| Tax number | ΑΦΜ |

## Architecture

Core pipeline in ocr_script.py:

1. Resolve file and folder inputs into PDF files.
2. Convert PDF pages to images.
3. For OCR-enabled modes: preprocess images and run Tesseract.
4. Run engine-specific extraction path (regex, local VLM, cloud Vision, or hybrid).
5. Build combined PDF report with summary table and page evidence.

Local web app in webapp.py:

- FastAPI app with upload form and local background thread processing
- JSON job store in jobs
- Upload staging in uploads
- Output reports in output
- Download endpoint for generated PDF artifacts

## Project Layout

- ocr_script.py: CLI pipeline and PDF report generation
- webapp.py: local web app and job execution
- templates/index.html: local web UI
- test_inputs: sample input PDFs
- output: generated reports
- uploads: web-upload staging
- jobs: job metadata and logs
- ocr_cache: OCR cache by file hash

## Setup

### macOS

```bash
brew install tesseract tesseract-lang poppler
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Linux / WSL (Ubuntu)

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-ell tesseract-ocr-grc poppler-utils
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows

See SETUP.md for Windows setup and PATH configuration.

## CLI Usage

Single PDF:

```bash
./.venv/bin/python ocr_script.py test_inputs/example.pdf
```

Custom output path:

```bash
./.venv/bin/python ocr_script.py test_inputs/example.pdf --output output/result.pdf
```

All PDFs in folder:

```bash
./.venv/bin/python ocr_script.py test_inputs/
```

Clear OCR cache and rerun:

```bash
rm -rf ocr_cache/*
./.venv/bin/python ocr_script.py test_inputs/example.pdf
```

Prepare a single-page input for fast model checks:

```bash
python -c "from pypdf import PdfReader, PdfWriter; r=PdfReader('test_inputs/example.pdf'); w=PdfWriter(); w.add_page(r.pages[0]); f=open('test_inputs/example_page1.pdf','wb'); w.write(f); f.close()"
```

Engine mode examples:

```bash
ollama pull glm-ocr

# OCR only
./.venv/bin/python ocr_script.py test_inputs/example.pdf --engine ocr

# VLM only
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine vlm --vlm-model glm-ocr

# Vision only (fails fast on API configuration errors)
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine vision

# OCR + VLM fallback
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine hybrid --vlm-model glm-ocr

# OCR + Vision fallback
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine hybrid_vision

# OCR + VLM + Vision
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine hybrid_all --vlm-model glm-ocr
```

## Local Web App

Run locally:

```bash
export ARCH_OCR_ENGINE=ocr
export ARCH_OCR_VLM_MODEL=qwen2.5vl:7b
export GOOGLE_API_KEY="YOUR_NEW_GOOGLE_VISION_KEY"
./.venv/bin/python -m uvicorn webapp:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

Current web app behavior:

- Engine and VLM model can be selected per job in the form.
- Status and metrics are visible in the jobs table.
- Download link is available when processing completes.

## Google Vision Notes

For optional cloud setup details, see GOOGLE_VISION_SETUP.md.

Known blocker:

- HTTP 403 with reason API_KEY_SERVICE_BLOCKED indicates a Google Cloud project or key configuration issue.
- This is not a repository code bug.

Security note:

- Never commit API keys.
- If a key is exposed in logs or chat history, rotate it immediately.

## Known Limitations

- Handwritten and stamp-overlapped regions remain difficult.
- Regex extraction can miss fields in noisy OCR outputs.
- Greek text rendering in PDF text blocks still needs improvement.

## Next Recommended Work

1. Embed a Greek-capable font in ReportLab output.
2. Add extraction confidence and manual-review flags.
3. Add an optional page-range CLI flag for faster benchmarking.
4. Add automated tests for each engine mode and fallback behavior.
