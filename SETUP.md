# Setup Guide — OCR HTK Tool (Windows)

## Requirements

You need three things installed before running the script:
1. **Python 3.10+**
2. **Tesseract OCR** (with Greek language data)
3. **Poppler for Windows** (used by pdf2image)

---

## Step 1 — Install Tesseract OCR

1. Download the installer from:
   **https://github.com/UB-Mannheim/tesseract/wiki**
   (Choose the latest 64-bit `.exe`)

2. During installation:
   - On the "Additional language data" screen, **check "Greek"** (or search for `ell` / `grc`)
   - Note the install path (default: `C:\Program Files\Tesseract-OCR\`)

3. Add Tesseract to PATH:
   - Open **System Properties → Environment Variables**
   - Under **System variables**, find `Path` → Edit → New
   - Add: `C:\Program Files\Tesseract-OCR`

4. Verify: open a new terminal and run:
   ```
   tesseract --version
   tesseract --list-langs
   ```
   You should see `ell` and `grc` in the language list.

---

## Step 2 — Install Poppler for Windows

1. Download the latest Windows build from:
   **https://github.com/oschwartz10612/poppler-windows/releases**
   (Download the `.zip`, e.g. `Release-xx.xx.x-0.zip`)

2. Extract to a folder, e.g. `C:\poppler\`

3. Add the `bin` folder to PATH:
   - Add: `C:\poppler\Library\bin`
   (the exact path depends on the release — it's the folder containing `pdftoppm.exe`)

4. Verify:
   ```
   pdftoppm -v
   ```

---

## Step 3 — Install Python dependencies

Open a terminal in the `ocr_htk` folder and run:

```bash
pip install -r requirements.txt
```

---

## Usage

### Single PDF
```bash
python ocr_script.py "C:\Users\Stelios\Downloads\CamScanner 30-3-26 11.36.pdf"
```

### Multiple PDFs (same client / property)
```bash
python ocr_script.py doc1.pdf doc2.pdf doc3.pdf
```

### All PDFs in a folder
```bash
python ocr_script.py "C:\Users\Stelios\Downloads\client_folder"
```

### Custom output path
```bash
python ocr_script.py doc1.pdf doc2.pdf --output "C:\Users\Stelios\Desktop\client_result.docx"
```

### If Tesseract or Poppler are NOT on PATH
```bash
python ocr_script.py doc1.pdf \
  --tesseract-path "C:\Program Files\Tesseract-OCR\tesseract.exe" \
  --poppler-path "C:\poppler\Library\bin"
```

---

## Output

The script produces a single `.docx` file:

| Page | Content |
|------|---------|
| 1 | HTK summary table — all key fields found, which pages/documents they appear in, and total occurrences |
| 2+ | Per-page two-column table: original page image (left) + OCR text (right) |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `TesseractNotFoundError` | Add Tesseract to PATH or use `--tesseract-path` |
| `PDFInfoNotInstalledError` | Add Poppler `bin` to PATH or use `--poppler-path` |
| Greek text is garbled | Make sure `ell` and `grc` lang packs are installed in Tesseract |
| Low OCR quality on old docs | The script auto-preprocesses images; very degraded pages may still be hard to read |
