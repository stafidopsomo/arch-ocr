# arch-ocr connected demo UI

This folder contains the Claude Design static React prototype, lightly wired to
the existing FastAPI service for the current Railway demo.

## Current demo architecture

- One Railway service only: the existing Python/FastAPI app.
- No Next.js service yet.
- No PostgreSQL dependency yet.
- The static UI is served by FastAPI at `/design/arch-ocr.html`.
- The UI calls same-origin FastAPI endpoints:
  - `POST /jobs`
  - `GET /jobs/{job_id}`
  - `GET /jobs/{job_id}/packet`
  - `GET /jobs/{job_id}/report`
  - `GET /usage`

The login screen is still a demo-token gate. Paste the Railway
`OCR_ADMIN_TOKEN`; the browser stores it in local storage for convenience.

## Railway values

No extra Railway service is required for this demo. Keep the existing Python
service and Volume setup:

```env
OCR_STORAGE_DIR=/data/arch-ocr
OCR_ADMIN_TOKEN=<demo-token>
OCR_DEMO_REQUIRE_TOKEN=true
OCR_MAX_FILES_PER_PACKET=10
OCR_MAX_PAGES_PER_PACKET=20
OCR_MAX_UPLOAD_MB=100
OCR_PROVIDER_MIN_SECONDS_BETWEEN_CALLS=4
OCR_PROVIDER_MAX_REQUESTS_PER_MINUTE=10
OCR_PROVIDER_MAX_REQUESTS_PER_DAY=100
OCR_PROVIDER_MAX_RETRIES=2
OCR_PROVIDER_RETRY_BASE_SECONDS=20
OCR_PROVIDER_RETRY_MAX_SECONDS=120
OCR_PROVIDER_TIMEOUT=180
```

## Later production layer

The Claude Design prompt describes the future Next.js + Auth.js + Prisma +
PostgreSQL architecture. That remains the right production direction, but it is
not required for this first client demo.
