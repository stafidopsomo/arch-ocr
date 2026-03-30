# Google Vision API Setup Guide (Local Testing)

This guide helps you connect Google Cloud Vision to this project for optional benchmarking.

## 0. Security First

If you shared or pasted an API key in chat, logs, or screenshots, revoke it now and create a new one before testing.
Treat leaked keys as compromised.

## 1. What You Need

- A Google account (your university-provided Google Pro account is fine if billing/API access is allowed)
- A Google Cloud project
- Billing enabled on that project
- Vision API enabled
- One authentication method: API key (quick local testing) or service account JSON via Application Default Credentials (recommended for controlled environments)

## 2. Create/Select Google Cloud Project

1. Open Google Cloud Console.
2. Create a new project (or select existing).
3. Confirm billing is attached.

## 3. Enable Vision API

1. Go to APIs & Services > Library.
2. Search for Vision API.
3. Click Enable.

## 4. Authentication Options

### Option A - API Key (Quick Local Test)

1. Go to APIs & Services > Credentials.
2. Create API key.
3. In API restrictions, allow Vision API.
4. Export key locally:

```bash
export GOOGLE_API_KEY="YOUR_NEW_GOOGLE_VISION_KEY"
```

### Option B - Service Account JSON (ADC)

1. Go to IAM & Admin > Service Accounts.
2. Create service account (example: arch-ocr-vision).
3. Grant minimal role for testing:

   - Vision AI User (or equivalent least-privilege role)

4. Create key:

   - Type: JSON
   - Download key file

Store it safely, for example:

- /Users/steliosdim/.config/gcp/arch-ocr-vision-key.json

## 5. Configure Local Environment

Use one of these before running Python scripts:

API key path:

```bash
export GOOGLE_API_KEY="YOUR_NEW_GOOGLE_VISION_KEY"
```

ADC service-account path:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/Users/steliosdim/.config/gcp/arch-ocr-vision-key.json"
```

Optional quick check:

```bash
echo "$GOOGLE_APPLICATION_CREDENTIALS"
```

## 6. Install Client Library

Already included in requirements.txt, otherwise:

```bash
pip install google-cloud-vision
```

## 7. Minimal Smoke Test

CLI test from this repository:

```bash
./.venv/bin/python ocr_script.py test_inputs/example_page1.pdf --engine vision --output output/vision_page1_test.pdf
```

The repository code will use GOOGLE_API_KEY first when present, otherwise ADC.

Create a small test script and run it against one page image:

```python
from google.cloud import vision

client = vision.ImageAnnotatorClient()
with open("sample_page.jpg", "rb") as f:
    content = f.read()

image = vision.Image(content=content)
response = client.document_text_detection(image=image)
print(response.full_text_annotation.text[:1000])
```

## 8. Free Tier / Cost Notes

Google Vision pricing can include free monthly usage tiers, but exact quotas and pricing change over time.
Always verify on the official pricing page for your region before scaling usage.

Recommended approach:

- keep Google Vision optional
- run only on low-confidence pages
- track per-page API calls in logs

Cost-control checklist:

1. Create a dedicated test project for OCR experiments.
2. Set a low budget alert (for example 5 EUR or 10 EUR).
3. Configure alert thresholds at 25/50/75/90/100 percent.
4. Set conservative Vision API quotas.
5. Start with very small page batches (for example 10 pages).
6. Check Billing Reports before scaling usage.

## 9. How To Integrate In This Project

Suggested mode architecture:

- ocr: current local Tesseract only
- hybrid: OCR first, then VLM/Google Vision fallback for missing fields
- vision_api: optional provider for benchmark runs

Implemented CLI engine modes in this repo:

- ocr
- vlm
- vision
- hybrid (ocr + vlm)
- hybrid_vision (ocr + vision)
- hybrid_all (ocr + vlm + vision)

Use provider fallback order like:

1. OCR
2. Ollama VLM
3. Google Vision (only if still unresolved)

## 10. About MCP Connection

If you want the assistant to act directly on your Google resources in future sessions, you can add a Google Cloud MCP server to your environment.
That setup depends on your local MCP configuration and OAuth/service-account policy.

Practical path:

1. Configure credentials locally first (steps above).
2. Validate with smoke test.
3. Then wire MCP server credentials in your editor environment if needed.

Even without MCP, this repository can call Google Vision directly through the Python SDK.

## 11. Common Error: API_KEY_SERVICE_BLOCKED (403)

If you see this:

- HTTP 403 PERMISSION_DENIED
- reason: API_KEY_SERVICE_BLOCKED
- message: Requests to vision.googleapis.com ... are blocked

It means your Google project/key is currently blocked from calling Vision API.

Typical causes:

1. Billing not active for the project.
2. Vision API not enabled in that project.
3. API key restrictions block Vision API (wrong API restriction or app restriction).
4. Organization policy blocks the service.

Quick checks in Google Cloud Console:

1. Billing: confirm project is linked to an active billing account.
2. APIs: confirm Vision API is enabled.
3. Credentials: open your API key and allow Vision API in API restrictions.
4. Try again with a newly created unrestricted test key (temporarily) to isolate restriction issues.

Note:

- This is a cloud configuration issue, not a bug in local Python code.
