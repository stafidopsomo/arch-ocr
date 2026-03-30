"""
OCR → PDF Tool for Greek Property Documents (HTK)
===================================================
Converts one or more scanned PDF documents into a single PDF with OCR results.
- Page 1: HTK cross-reference summary table (fields found across all documents)
- Remaining pages: per-page layout with [image | OCR text]

Usage:
    python ocr_script.py <pdf1> [pdf2] [pdf3] ...  [--output output/result.pdf]
    python ocr_script.py /path/to/folder/           [--output output/result.pdf]
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pytesseract
from pdf2image import convert_from_path
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, PageBreak, Spacer, Image as RLImage
from reportlab.lib import colors

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Adjust this if Tesseract is not on PATH (Windows common location)
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Adjust this if poppler is not on PATH (Windows)
POPPLER_PATH = None  # e.g. r"C:\poppler\bin"

OCR_LANG = "ell+grc"   # Modern Greek + ancient/polytonic Greek
OCR_CONFIG = "--psm 6 --oem 1"

# HTK fields: (display_name, list_of_regex_patterns_to_search)
HTK_FIELDS = [
    ("Όνομα",           [r"(?:Ονομα|ΟΝΟΜΑ|ΠΑΥΛΟΣ|Όνομα)[:\s]+([Α-Ωα-ωΆ-Ώ\w]+)"]),
    ("Επώνυμο",         [r"(?:Επώνυμο|ΕΠΩΝΥΜΟ|υπογεγραμμένος)[:\s]+.*?([Α-Ωα-ω]{3,})"]),
    ("Πατρώνυμο",       [r"(?:πατρός|ΠΑΤΡΩΝΥΜΟ|Πατρώνυμο|πατρώνυμ)[:\s]+([Α-Ωα-ωΆ-Ώ\w]+)"]),
    ("Μητρώνυμο",       [r"(?:μητρός|ΜΗΤΡΩΝΥΜΟ|μητρώνυμ)[:\s]+([Α-Ωα-ωΆ-Ώ\w]+)"]),
    ("Έτος γέννησης",   [r"(?:γεννηθείς|γεννηθεί|γεννημένος|κατοικ)[^\d]*(\d{4})"]),
    ("Τόπος γέννησης",  [r"(?:γεννηθείς|γεννηθεί)\s+(?:εν|εις|στ[ηο])\s+([Α-Ωα-ωΆ-Ώ\w]+)"]),
    ("Διεύθυνση",       [r"(?:οδός|οδό|επί της οδού|οδ\.)\s*([Α-Ωα-ωΆ-Ώ\w\s\d]+?)(?:\s*αρι|,|\.|\d)",
                          r"(?:ΟΔΟΣ|ΟΔΟ)[:\s]+([Α-Ωα-ωΆ-ΏA-Za-z\s\d]+?)(?:\n|,)"]),
    ("Αριθμός ακινήτου",[r"(?:αριθ\.|αριθμ\.|αρ\.)\s*(\d+[\w\-]*)"]),
    ("Χρήση ακινήτου",  [r"(?:Χρήσις|Χρήση)\s+(?:ακινήτου)?\s*[:\.]?\s*([Α-Ωα-ωΆ-ΏA-Za-z\s]+?)(?:\n|\()"]),
    ("Αριθμός ορόφων",  [r"(?:Αριθμ[οό]ς\s+ορόφων|ορόφων\s+κα[ιί]\s+στάσεων)[^\d]*([0-9\-ΗΜΙημι\s]+)"]),
    ("ΑΔΤ",             [r"(?:δελτίου\s+ταυτότητος|ταυτότητας|ΑΔΤ|Αρ\.\s*Δελτ)[^\w]*([Α-ΩA-Z]{1,2}\s*\d{5,9})"]),
    ("Τηλέφωνο",        [r"(?:ΤΗΛΕΦ|Τηλέφ|τηλ|ΤΗΛ)[.\s:]*(\d[\d\s\-/]{6,14})"]),
    ("ΑΦΜ",             [r"(?:ΑΦΜ|Α\.Φ\.Μ|αριθμ\.\s*φορ)[.\s:]*(\d{9})"]),
]

FIELD_KEY_MAP = {
    "Όνομα": "first_name",
    "Επώνυμο": "last_name",
    "Πατρώνυμο": "fathers_name",
    "Μητρώνυμο": "mothers_name",
    "Έτος γέννησης": "birth_year",
    "Τόπος γέννησης": "birth_place",
    "Διεύθυνση": "address",
    "Αριθμός ακινήτου": "property_number",
    "Χρήση ακινήτου": "property_use",
    "Αριθμός ορόφων": "floor_count",
    "ΑΔΤ": "id_card_number",
    "Τηλέφωνο": "phone",
    "ΑΦΜ": "tax_id",
}

KEY_FIELD_MAP = {v: k for k, v in FIELD_KEY_MAP.items()}

# Flatten HTK_FIELDS so values are always lists (handle tuple vs list inconsistency)
HTK_FIELDS = [
    (name, patterns if isinstance(patterns, list) else [patterns])
    for name, patterns in HTK_FIELDS
]

# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------

def preprocess_image(pil_img: Image.Image) -> Image.Image:
    """Enhance image quality for OCR: grayscale, denoise, deskew, threshold."""
    img = np.array(pil_img.convert("RGB"))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # Upscale if image is small (improves OCR on low-res scans)
    h, w = gray.shape
    if w < 1500:
        scale = 1500 / w
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # CLAHE for contrast
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Denoise
    gray = cv2.fastNlMeansDenoising(gray, h=10)

    # Deskew
    gray = _deskew(gray)

    # Otsu threshold
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return Image.fromarray(binary)


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Detect and correct rotation using Hough lines."""
    try:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                                 minLineLength=100, maxLineGap=10)
        if lines is None:
            return gray
        angles = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 - x1 != 0:
                angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if -15 < angle < 15:
                    angles.append(angle)
        if not angles:
            return gray
        median_angle = float(np.median(angles))
        if abs(median_angle) < 0.3:
            return gray
        h, w = gray.shape
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, median_angle, 1.0)
        rotated = cv2.warpAffine(gray, M, (w, h),
                                  flags=cv2.INTER_CUBIC,
                                  borderMode=cv2.BORDER_REPLICATE)
        return rotated
    except Exception:
        return gray


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------

def run_ocr(pil_img: Image.Image) -> str:
    """Run Tesseract OCR on a PIL image, returns extracted text."""
    preprocessed = preprocess_image(pil_img)
    text = pytesseract.image_to_string(preprocessed, lang=OCR_LANG, config=OCR_CONFIG)
    return text


# ---------------------------------------------------------------------------
# HTK field extraction
# ---------------------------------------------------------------------------

def extract_htk_fields(all_page_texts: list[tuple[str, int, str]]) -> dict:
    """
    Scan OCR text from all pages and extract HTK fields.

    all_page_texts: list of (pdf_name, page_number, ocr_text)

    Returns: dict of {field_name: {"value": str, "occurrences": [(pdf, page)]}}
    """
    results = {}
    for field_name, patterns in HTK_FIELDS:
        occurrences = []
        best_value = None
        value_counts = defaultdict(int)

        for pdf_name, page_num, text in all_page_texts:
            for pattern in patterns:
                matches = re.findall(pattern, text, re.IGNORECASE | re.UNICODE)
                for m in matches:
                    val = m.strip() if isinstance(m, str) else " ".join(m).strip()
                    val = re.sub(r"\s+", " ", val).strip()
                    if len(val) > 1:
                        occurrences.append((pdf_name, page_num))
                        value_counts[val] += 1

        if value_counts:
            best_value = max(value_counts, key=value_counts.get)

        # Deduplicate occurrences (keep unique page references)
        seen = set()
        unique_occ = []
        for occ in occurrences:
            if occ not in seen:
                seen.add(occ)
                unique_occ.append(occ)

        results[field_name] = {
            "value": best_value or "—",
            "occurrences": unique_occ,
        }
    return results


def _empty_htk_results() -> dict:
    return {field_name: {"value": "—", "occurrences": []} for field_name, _ in HTK_FIELDS}


def _normalize_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return None
    if value.lower() in {"null", "none", "unknown", "n/a", "-", "—"}:
        return None
    return value


def extract_htk_fields_vlm(
    all_pages: dict[str, list[tuple[int, Image.Image, str]]],
    model: str,
    progress_cb: Callable[[str], None] | None = None,
) -> dict:
    """Extract HTK fields from raw page images using a local Ollama vision model."""
    try:
        import ollama
    except ImportError as exc:
        raise RuntimeError("Ollama Python package is not installed. Install with: pip install ollama") from exc

    def _log(message: str):
        if progress_cb:
            progress_cb(message)
        else:
            print(message)

    requested_model = model
    resolved_model = model
    try:
        listed = ollama.list()
        available_models = set()
        if isinstance(listed, dict):
            models = listed.get("models", [])
            for item in models:
                if not isinstance(item, dict):
                    continue
                name = item.get("model") or item.get("name")
                if isinstance(name, str) and name.strip():
                    available_models.add(name.strip())

        if available_models and requested_model not in available_models:
            aliases = []
            if "-vl" in requested_model:
                aliases.append(requested_model.replace("-vl", "vl"))
            if "vl" in requested_model and "-vl" not in requested_model:
                aliases.append(requested_model.replace("vl", "-vl", 1))

            for alias in aliases:
                if alias in available_models:
                    resolved_model = alias
                    _log(
                        f"  [WARN] Requested model '{requested_model}' not found. "
                        f"Using available model '{resolved_model}'."
                    )
                    break

            if resolved_model == requested_model:
                preview = ", ".join(sorted(available_models)[:8])
                raise RuntimeError(
                    f"Ollama model '{requested_model}' not found. "
                    f"Available models: {preview}"
                )
    except RuntimeError:
        raise
    except Exception as exc:
        _log(f"  [WARN] Could not validate Ollama model list: {exc}")

    schema = {
        "type": "object",
        "properties": {k: {"type": ["string", "null"]} for k in KEY_FIELD_MAP.keys()},
        "required": list(KEY_FIELD_MAP.keys()),
        "additionalProperties": False,
    }

    prompt = (
        "You are an expert extractor for archival Greek property documents. "
        "Extract only the requested fields from this single page. "
        "If a field is missing or unclear, return null. "
        "Do not guess. Return only valid JSON matching the schema."
    )

    value_counts = {field_name: defaultdict(int) for field_name, _ in HTK_FIELDS}
    occurrences = {field_name: set() for field_name, _ in HTK_FIELDS}

    with tempfile.TemporaryDirectory() as tmp_dir:
        for pdf_name, pages in all_pages.items():
            for page_num, pil_img, _ in pages:
                img_path = os.path.join(tmp_dir, f"{Path(pdf_name).stem}_page_{page_num}.jpg")
                rgb_img = pil_img.convert("RGB")
                rgb_img.save(img_path, "JPEG", quality=90)

                _log(f"  [VLM] {resolved_model} on {Path(pdf_name).name} page {page_num}...")
                try:
                    response = ollama.chat(
                        model=resolved_model,
                        messages=[
                            {
                                "role": "user",
                                "content": prompt,
                                "images": [img_path],
                            }
                        ],
                        format=schema,
                        options={"temperature": 0},
                    )
                except Exception as exc:
                    _log(f"    [WARN] VLM request failed on page {page_num}: {exc}")
                    continue

                try:
                    content = response["message"]["content"]
                    parsed = json.loads(content) if isinstance(content, str) else content
                except Exception as exc:
                    _log(f"    [WARN] Invalid VLM JSON on page {page_num}: {exc}")
                    continue

                for key, value in parsed.items():
                    field_name = KEY_FIELD_MAP.get(key)
                    if not field_name:
                        continue
                    normalized = _normalize_value(value)
                    if not normalized:
                        continue
                    value_counts[field_name][normalized] += 1
                    occurrences[field_name].add((pdf_name, page_num))

    results = _empty_htk_results()
    for field_name, _ in HTK_FIELDS:
        if value_counts[field_name]:
            best_value = max(value_counts[field_name], key=value_counts[field_name].get)
            results[field_name]["value"] = best_value
        results[field_name]["occurrences"] = sorted(occurrences[field_name], key=lambda x: (x[0], x[1]))
    return results


def _google_vision_text_from_bytes(image_bytes: bytes, api_key: str | None = None) -> str:
    """Extract text from an image using Google Vision (API key REST or ADC SDK path)."""
    if api_key:
        url = f"https://vision.googleapis.com/v1/images:annotate?key={api_key}"
        payload = {
            "requests": [
                {
                    "image": {"content": base64.b64encode(image_bytes).decode("ascii")},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                }
            ]
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Google Vision HTTP {exc.code}: {details}") from exc

        responses = data.get("responses", [])
        if not responses:
            return ""
        first = responses[0]
        if "error" in first:
            raise RuntimeError(f"Google Vision error: {first['error']}")
        return first.get("fullTextAnnotation", {}).get("text", "")

    try:
        from google.cloud import vision
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-vision package is not installed. Install with: pip install google-cloud-vision"
        ) from exc

    client = vision.ImageAnnotatorClient()
    image = vision.Image(content=image_bytes)
    response = client.document_text_detection(image=image)
    if response.error.message:
        raise RuntimeError(f"Google Vision SDK error: {response.error.message}")
    return response.full_text_annotation.text or ""


def extract_htk_fields_google_vision(
    all_pages: dict[str, list[tuple[int, Image.Image, str]]],
    progress_cb: Callable[[str], None] | None = None,
    api_key: str | None = None,
    strict: bool = False,
) -> dict:
    """Extract HTK fields from raw page images using Google Vision OCR text + regex extraction."""
    def _log(message: str):
        if progress_cb:
            progress_cb(message)
        else:
            print(message)

    all_page_texts: list[tuple[str, int, str]] = []

    for pdf_name, pages in all_pages.items():
        for page_num, pil_img, _ in pages:
            _log(f"  [VISION] OCR on {Path(pdf_name).name} page {page_num}...")
            buf = io.BytesIO()
            pil_img.convert("RGB").save(buf, format="JPEG", quality=90)
            img_bytes = buf.getvalue()
            try:
                text = _google_vision_text_from_bytes(img_bytes, api_key=api_key)
            except Exception as exc:
                if strict:
                    raise RuntimeError(
                        f"Google Vision failed on {Path(pdf_name).name} page {page_num}: {exc}"
                    ) from exc
                _log(f"    [WARN] Google Vision failed on page {page_num}: {exc}")
                text = ""
            all_page_texts.append((pdf_name, page_num, text))

    return extract_htk_fields(all_page_texts)


def merge_htk_results(primary: dict, secondary: dict) -> dict:
    """Prefer values from primary; fill missing fields from secondary and merge occurrences."""
    merged = _empty_htk_results()
    for field_name, _ in HTK_FIELDS:
        p = primary.get(field_name, {"value": "—", "occurrences": []})
        s = secondary.get(field_name, {"value": "—", "occurrences": []})

        p_val = p.get("value", "—")
        s_val = s.get("value", "—")
        merged[field_name]["value"] = p_val if p_val != "—" else s_val

        occ = set(tuple(x) for x in p.get("occurrences", []))
        occ.update(tuple(x) for x in s.get("occurrences", []))
        merged[field_name]["occurrences"] = sorted(occ, key=lambda x: (x[0], x[1]))
    return merged


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

def build_pdf_output(htk_results: dict, pdf_names: list[str], all_pages: dict) -> bytes:
    """
    Build a PDF report from OCR results using ReportLab.
    
    Args:
        htk_results: Dictionary with extracted HTK fields and their occurrences
        pdf_names: List of processed PDF file names
        all_pages: Dict of {pdf_name: [(page_num, pil_image, ocr_text), ...]}
    
    Returns:
        PDF file content as bytes
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, cm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image as RLImage
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from io import BytesIO
    import tempfile
    
    # Create PDF in memory
    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=A4, topMargin=0.5*cm, bottomMargin=0.5*cm)
    
    # Build elements list
    elements = []
    styles = getSampleStyleSheet()
    
    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=14,
        textColor=colors.HexColor('#1F497D'),
        alignment=TA_CENTER,
        spaceAfter=12
    )
    
    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=9,
        textColor=colors.HexColor('#606060'),
        alignment=TA_CENTER,
        spaceAfter=12
    )
    
    # Title
    title = Paragraph("ΣΤΟΙΧΕΙΑ ΓΙΑ ΗΛΕΚΤΡΟΝΙΚΗ ΤΑΥΤΟΤΗΤΑ ΚΤΙΡΙΟΥ (ΗΤΚ)", title_style)
    elements.append(title)
    
    # Subtitle with document list
    doc_list = ", ".join([Path(p).stem for p in pdf_names])
    subtitle = Paragraph(f"Επεξεργάστηκαν {len(pdf_names)} έγγραφο/α: {doc_list}", subtitle_style)
    elements.append(subtitle)
    elements.append(Spacer(1, 0.3*cm))
    
    # HTK Summary Table
    table_data = [["Πεδίο ΗΤΚ", "Καλύτερη τιμή", "Βρέθηκε σε", "Σύνολο"]]
    
    for field_name, _ in HTK_FIELDS:
        data = htk_results.get(field_name, {"value": "—", "occurrences": []})
        occs = data["occurrences"]
        
        if occs:
            occ_parts = []
            for pdf_name, page_num in occs:
                occ_parts.append(f"Σ.{page_num}")
            occ_str = ", ".join(occ_parts)
        else:
            occ_str = "—"
        
        table_data.append([field_name, data["value"], occ_str, str(len(occs))])
    
    table = Table(table_data, colWidths=[3*cm, 4*cm, 6*cm, 1.5*cm])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1F497D')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F0F0F0')]),
    ]))
    
    elements.append(table)
    elements.append(PageBreak())
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        for pdf_name in pdf_names:
            if pdf_name not in all_pages:
                continue

            pages = all_pages[pdf_name]
            for page_num, pil_img, ocr_text in pages:
                label_text = f"{Path(pdf_name).name} - Σελίδα {page_num}"
                label = Paragraph(label_text, ParagraphStyle(
                    'SectionLabel',
                    parent=styles['Normal'],
                    fontSize=9,
                    textColor=colors.HexColor('#444444'),
                    spaceAfter=6
                ))
                elements.append(label)

                safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", pdf_name)
                tmp_path = os.path.join(tmp_dir, f"page_{safe_name}_{page_num}.jpg")

                if pil_img.mode == 'RGBA':
                    rgb_img = Image.new('RGB', pil_img.size, (255, 255, 255))
                    rgb_img.paste(pil_img, mask=pil_img.split()[3])
                    rgb_img.save(tmp_path, 'JPEG', quality=75, optimize=True)
                else:
                    pil_img.convert('RGB').save(tmp_path, 'JPEG', quality=75, optimize=True)

                rl_img = RLImage(tmp_path, width=3 * cm, height=3.5 * cm)
                ocr_para = Paragraph(
                    (ocr_text.strip() if ocr_text.strip() else "(Δεν αναγνωρίστηκε κείμενο)"),
                    ParagraphStyle(
                        'OCRText',
                        parent=styles['Normal'],
                        fontSize=7,
                        leftIndent=0.2 * cm
                    )
                )

                page_table = Table([[rl_img, ocr_para]], colWidths=[3.5 * cm, 11 * cm])
                page_table.setStyle(TableStyle([
                    ('ALIGN', (0, 0), (0, 0), 'CENTER'),
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    ('GRID', (0, 0), (-1, -1), 0.5, colors.lightgrey),
                ]))
                elements.append(page_table)
                elements.append(Spacer(1, 0.3 * cm))

        doc.build(elements)
    
    # Return PDF bytes
    pdf_buffer.seek(0)
    return pdf_buffer.getvalue()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OCR cache (saves results to avoid re-running OCR on unchanged PDFs)
# ---------------------------------------------------------------------------

CACHE_DIR = Path("ocr_cache")

def _pdf_hash(pdf_path: str) -> str:
    h = hashlib.md5()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def load_cache(pdf_path: str) -> list | None:
    """Return cached list of (page_num, text) or None if not cached."""
    CACHE_DIR.mkdir(exist_ok=True)
    key = _pdf_hash(pdf_path)
    cache_file = CACHE_DIR / f"{key}.json"
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)
    return None

def save_cache(pdf_path: str, pages: list):
    """Save list of (page_num, text) to cache."""
    CACHE_DIR.mkdir(exist_ok=True)
    key = _pdf_hash(pdf_path)
    cache_file = CACHE_DIR / f"{key}.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False, indent=2)


def collect_pdfs(inputs: list[str]) -> list[str]:
    """Resolve input paths (files or folders) to a list of PDF paths."""
    pdfs = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            pdfs.extend(sorted(p.glob("*.pdf")))
            pdfs.extend(sorted(p.glob("*.PDF")))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            pdfs.append(p)
        else:
            print(f"[WARN] Skipping '{inp}' — not a PDF file or directory.")
    return [str(p) for p in pdfs]


def process_pdfs(
    inputs: list[str],
    output: str = "output/result.pdf",
    poppler_path: str | None = None,
    tesseract_path: str | None = None,
    engine: str = "ocr",
    vlm_model: str = "qwen2.5vl:7b",
    google_api_key: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> dict:
    """Run the OCR pipeline and return output metadata."""
    def _log(message: str):
        if progress_cb:
            progress_cb(message)
        else:
            print(message)

    if tesseract_path:
        pytesseract.pytesseract.tesseract_cmd = tesseract_path

    resolved_google_api_key = google_api_key or os.getenv("GOOGLE_API_KEY")

    resolved_poppler_path = poppler_path or POPPLER_PATH

    pdf_paths = collect_pdfs(inputs)
    if not pdf_paths:
        raise ValueError("No PDF files found in provided inputs.")

    _log(f"[INFO] Processing {len(pdf_paths)} PDF(s):")
    for p in pdf_paths:
        _log(f"       {p}")

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_page_texts = []
    all_pages_by_pdf = {}  # {pdf_name: [(page_num, pil_img, text), ...]}
    converted_docs = 0
    processed_pages = 0
    needs_ocr = engine in {"ocr", "hybrid", "hybrid_vision", "hybrid_all"}

    for pdf_path in pdf_paths:
        pdf_name = Path(pdf_path).name
        cached = load_cache(pdf_path)

        _log(f"\n[INFO] Converting '{pdf_name}' to images...")
        try:
            pil_pages = convert_from_path(
                pdf_path, dpi=300,
                poppler_path=resolved_poppler_path
            )
            converted_docs += 1
            processed_pages += len(pil_pages)
        except Exception as e:
            _log(f"[ERROR] Could not convert '{pdf_name}': {e}")
            continue

        all_pages_by_pdf[pdf_name] = []

        if needs_ocr:
            if cached and len(cached) == len(pil_pages):
                _log(f"  [CACHE] Using cached OCR for '{pdf_name}' ({len(pil_pages)} pages)")
                for page_num, pil_img in enumerate(pil_pages, start=1):
                    text = cached[page_num - 1][1]
                    all_page_texts.append((pdf_path, page_num, text))
                    all_pages_by_pdf[pdf_name].append((page_num, pil_img, text))
            else:
                page_cache = []
                for page_num, pil_img in enumerate(pil_pages, start=1):
                    _log(f"  OCR page {page_num}/{len(pil_pages)}...")
                    try:
                        text = run_ocr(pil_img)
                    except Exception as e:
                        text = f"[OCR error: {e}]"
                        _log(f"    [WARN] OCR error on page {page_num}: {e}")
                    page_cache.append((page_num, text))
                    all_page_texts.append((pdf_path, page_num, text))
                    all_pages_by_pdf[pdf_name].append((page_num, pil_img, text))
                save_cache(pdf_path, page_cache)
                _log(f"  [CACHE] Saved OCR cache for '{pdf_name}'")
        else:
            _log(f"  [INFO] Skipping Tesseract OCR in '{engine}' mode")
            for page_num, pil_img in enumerate(pil_pages, start=1):
                all_pages_by_pdf[pdf_name].append((page_num, pil_img, ""))

    if processed_pages == 0:
        raise RuntimeError("No pages were processed successfully.")

    if needs_ocr:
        _log("\n[INFO] Extracting HTK fields from OCR text...")
        ocr_results = extract_htk_fields(all_page_texts)
    else:
        _log("\n[INFO] Skipping OCR-regex extraction in this mode...")
        ocr_results = _empty_htk_results()

    engine_used = engine
    if engine == "ocr":
        htk_results = ocr_results
    elif engine == "vlm":
        _log(f"[INFO] Running VLM-only extraction with model '{vlm_model}'...")
        htk_results = extract_htk_fields_vlm(all_pages_by_pdf, vlm_model, progress_cb=_log)
        engine_used = "vlm"
    elif engine == "vision":
        _log("[INFO] Running Google Vision-only extraction...")
        htk_results = extract_htk_fields_google_vision(
            all_pages_by_pdf,
            progress_cb=_log,
            api_key=resolved_google_api_key,
            strict=True,
        )
        engine_used = "vision"
    elif engine == "hybrid":
        _log(f"[INFO] Running hybrid extraction (OCR + {vlm_model})...")
        try:
            vlm_results = extract_htk_fields_vlm(all_pages_by_pdf, vlm_model, progress_cb=_log)
            htk_results = merge_htk_results(ocr_results, vlm_results)
            engine_used = "ocr+vlm"
        except Exception as exc:
            _log(f"[WARN] Hybrid fallback to OCR-only due to VLM error: {exc}")
            htk_results = ocr_results
            engine_used = "ocr"
    elif engine == "hybrid_vision":
        _log("[INFO] Running hybrid extraction (OCR + Google Vision)...")
        try:
            vision_results = extract_htk_fields_google_vision(
                all_pages_by_pdf,
                progress_cb=_log,
                api_key=resolved_google_api_key,
            )
            htk_results = merge_htk_results(ocr_results, vision_results)
            engine_used = "ocr+vision"
        except Exception as exc:
            _log(f"[WARN] Hybrid Vision fallback to OCR-only due to error: {exc}")
            htk_results = ocr_results
            engine_used = "ocr"
    elif engine == "hybrid_all":
        _log(f"[INFO] Running hybrid_all extraction (OCR + {vlm_model} + Google Vision)...")
        combined = ocr_results
        engines = ["ocr"]

        try:
            vlm_results = extract_htk_fields_vlm(all_pages_by_pdf, vlm_model, progress_cb=_log)
            combined = merge_htk_results(combined, vlm_results)
            engines.append("vlm")
        except Exception as exc:
            _log(f"[WARN] VLM stage skipped due to error: {exc}")

        try:
            vision_results = extract_htk_fields_google_vision(
                all_pages_by_pdf,
                progress_cb=_log,
                api_key=resolved_google_api_key,
            )
            combined = merge_htk_results(combined, vision_results)
            engines.append("vision")
        except Exception as exc:
            _log(f"[WARN] Vision stage skipped due to error: {exc}")

        htk_results = combined
        engine_used = "+".join(engines)
    else:
        raise ValueError("engine must be one of: ocr, vlm, vision, hybrid, hybrid_vision, hybrid_all")

    pdf_names = [Path(p).name for p in pdf_paths]
    _log("[INFO] Building PDF...")

    # Build PDF and write to disk
    pdf_bytes = build_pdf_output(htk_results, pdf_names, all_pages_by_pdf)
    out_path.write_bytes(pdf_bytes)

    _log(f"\n[DONE] Saved: {out_path.resolve()}")
    return {
        "output_path": str(out_path.resolve()),
        "input_count": len(pdf_paths),
        "converted_count": converted_docs,
        "page_count": processed_pages,
        "engine_used": engine_used,
        "vlm_model": vlm_model if "vlm" in engine_used else None,
        "vision_mode": "vision" in engine_used,
    }


def main():
    parser = argparse.ArgumentParser(
        description="OCR Greek property PDFs -> PDF with HTK summary"
    )
    parser.add_argument(
        "inputs", nargs="+",
        help="One or more PDF file paths, or a folder containing PDFs"
    )
    parser.add_argument(
        "--output", default="output/result.pdf",
        help="Output PDF path (default: output/result.pdf)"
    )
    parser.add_argument(
        "--poppler-path", default=None,
        help="Path to poppler bin directory (Windows, if not on PATH)"
    )
    parser.add_argument(
        "--tesseract-path", default=None,
        help="Path to tesseract.exe (Windows, if not on PATH)"
    )
    parser.add_argument(
        "--engine", choices=["ocr", "vlm", "vision", "hybrid", "hybrid_vision", "hybrid_all"], default="ocr",
        help="Extraction engine mode (default: ocr)"
    )
    parser.add_argument(
        "--vlm-model", default="qwen2.5vl:7b",
        help="Ollama VLM model tag for hybrid/vlm modes"
    )
    parser.add_argument(
        "--google-api-key", default=None,
        help="Google Vision API key (optional). Prefer env GOOGLE_API_KEY or ADC service account credentials."
    )
    args = parser.parse_args()

    try:
        process_pdfs(
            inputs=args.inputs,
            output=args.output,
            poppler_path=args.poppler_path,
            tesseract_path=args.tesseract_path,
            engine=args.engine,
            vlm_model=args.vlm_model,
            google_api_key=args.google_api_key,
        )
    except Exception as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
