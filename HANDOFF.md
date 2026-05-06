# Handoff

This file has been merged into the main project docs.

Use these files as the source of truth:

- [README.md](README.md) - current setup, CLI commands, Railway demo usage,
  endpoints, guardrails, and known demo limitations.
- [PLAN.md](PLAN.md) - architecture, milestone status, deferred work, and next
  implementation steps.

## Current Snapshot

- The backend processing engine is usable through the CLI and the FastAPI demo
  service.
- Railway demo storage is file-backed under `OCR_STORAGE_DIR`, intended for a
  mounted Volume such as `/data/arch-ocr`.
- The demo has upload/job status, JSON export, Markdown report export, usage
  tracking, admin/job listing, and a Greek review page.
- Stored packets are dynamically re-analyzed when opening packet/report/review
  endpoints, so old jobs can benefit from newer deterministic checks while
  source PDFs still exist.
- Postgres, real users, subscriptions, and long-term report history are still a
  later product layer.

## Immediate Next

Redeploy the Railway demo, verify the existing job review page shows correct
page/field totals and AFM evidence, then run one fresh packet for presentation
quality review.
