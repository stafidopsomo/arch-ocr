# arch-ocr

OCR tool for Greek property documents (HTK — Ηλεκτρονική Ταυτότητα Κτιρίου).

Converts scanned PDF documents into a structured Word document with:
- **Page 1**: HTK summary table — key owner/property fields extracted across all documents
- **Remaining pages**: Side-by-side view of each scanned page (image) next to its OCR text

---

## What it extracts

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

---

## Output format

```
[ Page image ]  |  [ OCR text ]
[ Page image ]  |  [ OCR text ]
[ ...        ]  |  [ ...      ]
```

---

## Setup

### macOS

```bash
brew install tesseract tesseract-lang poppler
```

Then install Python deps (use a venv):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Linux / WSL (Ubuntu)

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-ell tesseract-ocr-grc poppler-utils
pip3 install -r requirements.txt --break-system-packages
```

### Windows (native)

See [SETUP.md](SETUP.md) for Tesseract + Poppler installation on Windows.

---

## Usage

### Single PDF
```bash
python3 ocr_script.py "/path/to/document.pdf"
```

### Multiple PDFs (same client / property)
```bash
python3 ocr_script.py doc1.pdf doc2.pdf doc3.pdf
```

### All PDFs in a folder
```bash
python3 ocr_script.py /path/to/folder/
```

### Custom output path
```bash
python3 ocr_script.py doc1.pdf --output /path/to/result.docx
```

---

## OCR caching

After the first run, OCR results are cached in `ocr_cache/`. Re-running the script on the same PDF skips the OCR step and uses the cache instead. This makes subsequent runs much faster.

To force a fresh OCR run, delete the relevant file in `ocr_cache/`.

---

## OCR quality notes

These documents are mid-20th century polytonic Greek, often with handwriting mixed into printed forms. Tesseract (`ell` + `grc`) handles the printed portions reasonably. Handwritten fields (name, address, dates) will require manual review — no local OCR engine handles this reliably.

For better results on degraded or heavily handwritten documents, consider using the EasyOCR engine (installed via `pip install easyocr`).

---

## Requirements

- Python 3.10+
- Tesseract OCR with `ell` (modern Greek) and `grc` (polytonic/ancient Greek)
- Poppler (for PDF → image conversion)
- See `requirements.txt` for Python packages
