# Setup Guide - OCR HTK Tool (Windows)

This guide installs and verifies the local OCR pipeline on Windows.

## Requirements

Install these before running:
1. Python 3.10+
2. Tesseract OCR with Greek language data
3. Poppler for Windows (required by pdf2image)

## Step 1 - Install Tesseract OCR

1. Download installer:
   https://github.com/UB-Mannheim/tesseract/wiki

2. During install:
   - Include Greek language data (look for ell and grc)
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

1. Download Windows build:
   https://github.com/oschwartz10612/poppler-windows/releases

2. Extract (example):
   C:\poppler\

3. Add Poppler bin to PATH (example):
   C:\poppler\Library\bin

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

## Input/Output Folders

Recommended local workflow:
- Put test PDFs in test_inputs\
- Generated reports go to output\
- OCR cache is stored in ocr_cache\

## CLI Usage

Single PDF:

```powershell
.\.venv\Scripts\python ocr_script.py test_inputs\example.pdf
```

Custom output path:

```powershell
.\.venv\Scripts\python ocr_script.py test_inputs\example.pdf --output output\result.pdf
```

Process all PDFs in folder:

```powershell
.\.venv\Scripts\python ocr_script.py test_inputs\
```

If executables are not on PATH:

```powershell
.\.venv\Scripts\python ocr_script.py test_inputs\example.pdf --tesseract-path "C:\Program Files\Tesseract-OCR\tesseract.exe" --poppler-path "C:\poppler\Library\bin"
```

## Output Format

The script now produces one PDF report:

| Page | Content |
|------|---------|
| 1 | HTK summary table with extracted values and page references |
| 2+ | Per-page two-column layout: page image (left) and OCR text (right) |

## Local Web App (Optional)

Start:

```powershell
.\.venv\Scripts\python -m uvicorn webapp:app --reload
```

Open:
- http://127.0.0.1:8000

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Tesseract not found | Add Tesseract folder to PATH or use --tesseract-path |
| Poppler/pdfinfo not found | Add Poppler bin to PATH or use --poppler-path |
| No PDFs found | Check input path and file extension |
| OCR quality low on archival pages | Expected on handwriting/stamps; keep manual review in flow |
| Greek text in output appears degraded | Current known limitation in PDF rendering path; see README known limitations |

## Quality Expectation

This tool is currently optimized for local testing and assisted extraction, not zero-touch legal automation.
Human review is still required for low-confidence fields.
