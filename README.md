# arch-ocr

Local OCR pipeline for Greek property documents (HTK - Ηλεκτρονική Ταυτότητα Κτιρίου).

The current implementation processes one or more scanned PDFs and generates one combined PDF report:
- Page 1: HTK summary table (best extracted values + page references)
- Following pages: side-by-side layout with scanned image and OCR text

## Current Status

- Pipeline runs locally end to end on macOS.
- Multi-engine architecture implemented supporting: `ocr`, `vlm` (local Ollama), `vision` (Google Cloud), and `hybrid` cascades.
- Web UI is available for local testing (FastAPI).
- Output format changed from DOCX to PDF to reduce storage size.

### Hardware Constraints & Local VLM
- Running 7B+ Vision models (e.g., `qwen2.5-vl:7b`) on a 16GB Mac can cause Out Of Memory (OOM) crashes because full-resolution PDF page inference requires massive RAM (~9GB context mapping alone).
- **Recommendation:** Downscale input images before sending to VLM, or use smaller models (e.g., `qwen3.5:0.8b` or `glm-ocr`) to fit inside available memory limits.

Important quality note:
- Greek glyph rendering in the generated PDF text layer currently requires font adjustments.

## Scope

In scope:
- Local OCR of scanned Greek property PDFs
- HTK field extraction for key identity/property fields
- Local web upload workflow for one tester
- Deterministic, inspectable output artifact (PDF)

Out of scope (for now):
- Production authentication and multi-tenant security
- Cloud hosting and autoscaling
- Guaranteed handwritten-text accuracy without review
- Zero-touch legal-grade automation

## What It Extracts

| Field | Greek |
|-------|-------|
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

## Technical Architecture

Core pipeline (ocr_script.py):
1. Resolve input files/folders to PDF list
2. Convert each page to image (pdf2image + poppler)
3. Preprocess page image (grayscale, upscale, CLAHE, denoise, deskew, threshold)
4. OCR with Tesseract (lang: ell+grc)
5. Cache OCR text per source PDF hash (ocr_cache/)
6. Extract HTK fields with regex rules
7. Build combined PDF report (ReportLab)

Local web app (webapp.py):
- FastAPI app with upload form and local job queue
- JSON job store in jobs/
- Uploaded files in uploads/
- Output reports in output/
- Download endpoint serves generated PDF

## Project Layout

- ocr_script.py: CLI OCR pipeline and report builder
- webapp.py: local web app + background job execution
- templates/index.html: local web UI
- test_inputs/: place input PDFs for testing
- output/: generated reports
- uploads/: web-upload staging
- jobs/: job metadata and logs
- ocr_cache/: cached OCR text by file hash

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

See SETUP.md for full Windows setup and PATH configuration.

## CLI Usage

Single PDF:

```bash
./.venv/bin/python ocr_script.py test_inputs/example.pdf
```

Custom output path:

```bash
./.venv/bin/python ocr_script.py test_inputs/example.pdf --output /tmp/test.pdf
```

All PDFs in a folder:

```bash
./.venv/bin/python ocr_script.py test_inputs/
```

Clear OCR cache and re-run:

```bash
rm -rf ocr_cache/*
./.venv/bin/python ocr_script.py test_inputs/example.pdf
```

Check output size:

```bash
ls -lh output/*.pdf
```

Engine modes:

```bash
# Ensure Ollama is running and pull a vision model first
ollama pull glm-ocr
# or: ollama pull qwen3.5:0.8b (if 7B models OOM on your hardware)

# Optional: export Google Vision key (or use service-account ADC instead)
export GOOGLE_API_KEY="YOUR_NEW_GOOGLE_VISION_KEY"

# Extract just a single page for faster benchmarking without OOMing
python -c "from pypdf import PdfReader, PdfWriter; r=PdfReader('test_inputs/example.pdf'); w=PdfWriter(); w.add_page(r.pages[0]); f=open('test_inputs/example_page1.pdf','wb'); w.write(f); f.close()"

# OCR only (default)
./.venv/bin/python ocr_script.py test_inputs/example.pdf --engine ocr

# Hybrid (OCR + local Ollama VLM fallback)
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine hybrid --vlm-model glm-ocr

# VLM only (requires Ollama model available locally)
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine vlm --vlm-model glm-ocr

# Google Vision only
./.venv/bin/python ocr_script.py test_inputs/example.pdf --engine vision

# OCR + Google Vision fallback
./.venv/bin/python ocr_script.py test_inputs/example.pdf --engine hybrid_vision

# OCR + local VLM + Google Vision (max comparison mode)
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine hybrid_all --vlm-model glm-ocr
```

## Local Web App Usage

Run locally:

```bash
export ARCH_OCR_ENGINE=hybrid_all
export ARCH_OCR_VLM_MODEL=glm-ocr
export GOOGLE_API_KEY="YOUR_NEW_GOOGLE_VISION_KEY"  # optional if using vision modes
./.venv/bin/python -m uvicorn webapp:app --reload
```

Open:
- http://127.0.0.1:8000

Current usability:
- Good for one local tester
- Shows job status, basic metrics, and download link
- No production-grade auth yet

## OCR Policy And Decision Gate

Default policy:
- Primary engine: Tesseract (ell+grc), fully local and free
- Optional benchmark engines: EasyOCR or paid APIs
- Human review required for low-confidence handwritten fields

Decision gate before moving to LLM/API:
1. Prepare benchmark set (clean, noisy, handwritten, stamped pages)
2. Measure per-field accuracy on required HTK outputs
3. If critical fields remain below target after OCR tuning, add LLM post-extraction

## Optional Google Vision API Path

For optional cloud benchmarking/fallback setup, see GOOGLE_VISION_SETUP.md.
Use it as a controlled fallback only, not as mandatory default.

Current known blocker on some projects:
- HTTP 403 PERMISSION_DENIED with reason API_KEY_SERVICE_BLOCKED means Vision calls are blocked for that project/key.
- Fix is in Google Cloud project settings (billing/API restrictions/key restrictions), not in this repository code.

Security note:
- Never commit API keys.
- If a key was pasted in chat or terminal history, rotate/revoke it immediately and create a new key.

## Known Limitations

- Handwritten and stamp-overlapped regions are often low quality.
- Current regex extraction can miss values in noisy OCR output.
- Greek text rendering in generated PDF requires improvement (font/encoding path).
- The sample report quality is not yet acceptable as final product output.

## Next Recommended Work

1. Fix Greek font embedding in ReportLab output.
2. Add OCR text normalization before regex extraction.
3. Add extraction confidence and review flags per field.
4. Re-benchmark on expanded test set.
5. If needed, test local Ollama vision models as optional second-stage extractor.
