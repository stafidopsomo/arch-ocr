# Setup Guide - OCR HTK Tool (Windows)

This guide installs and verifies the local OCR pipeline on Windows.

## Requirements

Install these first:

1. Python 3.10+
2. Tesseract OCR with Greek language data
3. Poppler for Windows (required by pdf2image)

## Step 1 - Install Tesseract OCR

1. Download installer from [UB Mannheim Tesseract builds](https://github.com/UB-Mannheim/tesseract/wiki).

2. During install:

   - Include Greek language data (ell and grc)
   - Default path is usually C:\Program Files\Tesseract-OCR\

3. Add to PATH:

   - Open Environment Variables
   - Edit system Path
   - Add C:\Program Files\Tesseract-OCR

4. Verify in a new terminal:

```powershell
tesseract --version
tesseract --list-langs
```

Expected language list includes:

- ell
- grc

## Step 2 - Install Poppler

1. Download build from [poppler-windows releases](https://github.com/oschwartz10612/poppler-windows/releases).

2. Extract to an example path:

   - C:\poppler\

3. Add Poppler bin folder to PATH:

   - C:\poppler\Library\bin

4. Verify:

```powershell
pdftoppm -v
```

## Step 3 - Create Virtual Environment And Install Dependencies

From repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Input And Output Folders

Recommended local workflow:

- Put input PDFs in test_inputs\
- Generated reports are written to output\
- OCR cache is stored in ocr_cache\

## CLI Usage

Single PDF:

```powershell
.\.venv\Scripts\python ocr_script.py test_inputs\example.pdf
```

Single-page test input (fast local VLM benchmarking):

```powershell
.\.venv\Scripts\python -c "from pypdf import PdfReader, PdfWriter; r=PdfReader('test_inputs/example.pdf'); w=PdfWriter(); w.add_page(r.pages[0]); f=open('test_inputs/example_page1.pdf','wb'); w.write(f); f.close()"
```

VLM mode example:

```powershell
.\.venv\Scripts\python ocr_script.py test_inputs\example_page1.pdf --engine vlm --vlm-model glm-ocr --output output\glm_ocr_page1_test.pdf
```

Vision mode example:

```powershell
set GOOGLE_API_KEY=YOUR_NEW_GOOGLE_VISION_KEY
.\.venv\Scripts\python ocr_script.py test_inputs\example_page1.pdf --engine vision --output output\vision_page1_test.pdf
```

If executables are not on PATH:

```powershell
.\.venv\Scripts\python ocr_script.py test_inputs\example.pdf --tesseract-path "C:\Program Files\Tesseract-OCR\tesseract.exe" --poppler-path "C:\poppler\Library\bin"
```

## Output Format

The script produces one PDF report:

| Page | Content |
| ---- | ------- |
| 1 | HTK summary table with extracted values and page references |
| 2+ | Per-page two-column layout: page image on left and extracted text on right |

## Local Web App (Optional)

Start:

```powershell
.\.venv\Scripts\python -m uvicorn webapp:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The web form now supports per-job engine and VLM model selection.

## Troubleshooting

| Problem | Solution |
| ------- | -------- |
| Tesseract not found | Add Tesseract folder to PATH or use --tesseract-path |
| Poppler/pdfinfo not found | Add Poppler bin to PATH or use --poppler-path |
| No PDFs found | Check input path and PDF extension |
| VLM model fails to load | Use single-page input and a smaller local model |
| Google Vision returns API_KEY_SERVICE_BLOCKED | Fix billing/API key restrictions in Google Cloud project |
| Greek text looks degraded in output | Known font/rendering limitation in current PDF path |

## Quality Expectation

This tool is optimized for local assisted extraction, not zero-touch legal automation.

Human review is still required for low-confidence fields.
