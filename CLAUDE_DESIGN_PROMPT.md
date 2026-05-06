# Claude Design Prompt — arch-ocr Web UI

Build a complete, production-grade web UI for **arch-ocr**, a document
validation platform for Greek property, architecture, and building/legal
document packets. The Python FastAPI backend already exists (see `app.py` and
`ocr_script.py` included in this brief). Your job is the frontend, the
authentication layer, and the database schema for user accounts. Do not change
the OCR/extraction pipeline — wrap it.

---

## 1. Product summary

Greek architects, engineers, and legal professionals upload "packets" of 10–40
related PDF/image pages (building permits, declarations, contracts, technical
memos, cadastral plans, etc.). The backend extracts evidence per page using
Gemini, clusters repeated values across pages (names, addresses, KAEK/AFM/ATAK
identifiers, permit numbers, dates, architects, engineers, owners), runs
deterministic validation checks (pass/warning/unknown/fail with evidence
references), and produces a structured `packet.json` plus a markdown report.

The current UI is a barebones server-rendered HTML form. Replace it entirely
with a polished, serious, professional web app.

---

## 2. Tech stack (required)

- **Next.js 15** (App Router) + **TypeScript**
- **Tailwind CSS** + **shadcn/ui** components
- **lucide-react** icons
- **next-intl** for bilingual EL/EN
- **Tanstack Query** for server state
- **Zod** for form/schema validation
- **NextAuth.js (Auth.js v5)** with Credentials provider, JWT session strategy
- **Prisma** ORM against **PostgreSQL** (Railway Postgres)
- Deployed in the **same repo** as the FastAPI backend. The Next.js app lives
  in `web/`. Add a top-level dev script (`npm run dev` from `web/`) and a
  Railway build setup that serves Next.js as a separate service or as a
  reverse-proxy in front of FastAPI — whichever is simpler. Document the
  choice clearly.

The Next.js app calls the existing FastAPI endpoints. Do **not** duplicate OCR
logic in JavaScript.

---

## 3. Aesthetic direction

Serious, legal/professional, calm, document-grade. Think **Linear**,
**Stripe dashboard**, **Notion** — but more conservative. This product handles
people's permits and property records; it must look trustworthy.

- Neutral palette: white/near-white background, dark slate text, a single
  restrained accent (deep navy or muted teal). No bright gradients, no playful
  illustrations.
- Typography: a clean sans for UI (Inter), a slightly more serious sans
  (e.g., Söhne-style or system-ui fallback) for headings. Greek must render
  beautifully — verify with real Greek strings.
- Generous whitespace, sharp 1px borders, subtle elevation only where needed.
- Status colors must be calm and accessible: green (pass), amber (warning),
  slate-gray (unknown), red (fail). No saturated traffic-light reds.
- Dense data tables done well. This is a power-user tool, not a consumer app.
- Dark mode is a plus but not required for v1.

---

## 4. Internationalization

Full **bilingual EL ↔ EN** with a header language switcher. Persist choice in
a cookie. Default to Greek. Translate every UI string. Document field names
(e.g., "person_name", "address", "permit_number", "engineer", "architect",
"owner", "applicant", "stamp", "signature", "handwritten_note", "KAEK", "AFM",
"ATAK", "registry_id") need both Greek and English labels. Provide a single
`messages/el.json` and `messages/en.json`.

---

## 5. Authentication & user model

**Single-user accounts. No self-signup. No orgs. No billing.**

- Admin provisions accounts manually (creates rows directly in DB or via a
  protected admin page).
- Login: username + password (bcrypt-hashed, min 12 chars).
- Session: JWT cookie, 7-day rolling.
- Each user only sees their own packets. Admin role can see all.
- "Forgot password" is **out of scope** for v1; admin resets manually.

### Database schema (Prisma, Postgres)

```prisma
model User {
  id            String   @id @default(cuid())
  username      String   @unique
  passwordHash  String
  displayName   String?
  role          Role     @default(USER)
  locale        String   @default("el")
  createdAt     DateTime @default(now())
  updatedAt     DateTime @updatedAt
  packets       Packet[]
}

enum Role {
  USER
  ADMIN
}

model Packet {
  id              String   @id @default(cuid())
  userId          String
  user            User     @relation(fields: [userId], references: [id], onDelete: Cascade)
  jobId           String   @unique          // matches FastAPI job_id (uuid)
  title           String?                   // user-supplied label, optional
  status          String                    // mirrors FastAPI status string
  filesCount      Int      @default(0)
  pagesSelected   Int      @default(0)
  pagesProcessed  Int      @default(0)
  overallStatus   String?                   // pass | warning | fail | needs_review
  estimatedCostUsd Decimal? @db.Decimal(12, 6)
  createdAt       DateTime @default(now())
  completedAt     DateTime?
  errorMessage    String?
  // packet.json + report.md remain on the FastAPI Volume; we only store metadata.
}

model AuditLog {
  id        String   @id @default(cuid())
  userId    String?
  action    String
  meta      Json?
  createdAt DateTime @default(now())
}
```

Provide a CLI seed script (`web/scripts/create-user.ts`) so the operator can
run `npm run create-user -- --username=foo --password=... [--admin]` from the
Railway shell.

---

## 6. Backend contract (already implemented in `app.py`)

The Next.js app calls these FastAPI endpoints. The current demo token gate
must be replaced: the Next.js server passes a shared service token via the
`x-admin-token` header on behalf of the authenticated user. Users never see or
hold this token.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | health check |
| `POST` | `/jobs` (multipart: `files[]`) | start a new packet job, returns `{job_id, status, ...}` |
| `GET` | `/jobs/{job_id}` | job status JSON (live polling source) |
| `GET` | `/jobs/{job_id}/packet` | full `arch_ocr.packet.v1` JSON |
| `GET` | `/jobs/{job_id}/report` | markdown report (text/plain) |
| `GET` | `/usage` | provider usage ledger summary |

### Job status state machine

`queued → triaging → processing → (throttled | retrying | rate_limited)* →
completed | completed_with_errors | failed`

Live progress fields on the job: `pages_selected`, `pages_processed`,
`current_page_id`, `message`.

### Packet JSON shape (`arch_ocr.packet.v1`)

Top-level keys:

- `artifact_version`, `packet_id`, `provider`, `model`, `dpi`,
  `created_at`, `source_files[]`
- `triage` — per-file page kind, embedded text length, image count,
  needs_vision, possible handwriting/stamp/signature
- `page_extractions[]` — each page's extracted fields. Each field has:
  `field_id`, `field_ref` (stable e.g. `page-2:p2-f4`), `field_type`,
  `label_text`, `value`, `normalized_value`, `is_handwritten`, `is_stamped`,
  `is_signature`, `confidence` (high/medium/low), `evidence.nearby_text`,
  `evidence.location_hint`, `notes[]`, `source_file`, `page_number`
- `clusters[]` — `cluster_id`, `field_type`, `subtype` (e.g. KAEK/AFM/ATAK
  for identifiers), `canonical_value`, `mentions[]` (each with `field_ref`,
  `value`, `page_id`, `source_file`, `confidence`, `nearby_label`)
- `fuzzy_groups[]` — near-match clusters across `person_name`/`address` for
  human review
- `checks[]` — `check_id`, `status` (pass/warning/unknown/fail), `title`,
  `summary`, `evidence_refs[]`, `details[]`
- `executive_summary` — short paragraph
- `errors[]` — per-page failures
- `totals` — `pages_total`, `pages_processed`, `pages_failed`, `fields_total`,
  `low_confidence_fields`
- `cost_summary` — `input_tokens`, `output_tokens`, `cached_input_tokens`,
  `estimated_cost_usd`, `reported_cost_usd`, `calls`
- `check_summary` — counts by status

### Backend limits (visualize, do not bypass)

- max 10 files per packet
- max 20 pages per packet
- one active job at a time globally (worker_lock)
- min 4 seconds between provider calls
- file types: `.pdf`, plus image extensions

---

## 7. Pages and screens

### 7.1 Public landing page (`/`)

Unauthenticated marketing page. Bilingual.

- **Hero**: clear value prop in Greek headline + English subhead, e.g.
  "Έλεγχος εγγράφων ακινήτων με αποδείξεις" / "Evidence-based packet
  validation for Greek property documents". CTA: "Sign in" (no signup).
- **How it works** section: 4 steps with simple icons — Upload packet → Page
  triage → Evidence extraction → Validation report.
- **What it validates**: chips listing field types it understands (KAEK,
  AFM, ATAK, permit numbers, addresses, architect/engineer identity,
  signatures, stamps, handwritten amendments, dates).
- **Privacy & data handling**: brief, serious. Files are processed via
  Google Gemini; results retained per user.
- **Footer**: contact email, GitHub link slot, language switcher.
- No pricing section. No testimonials. No live chat.

### 7.2 `/login`

Centered card. Username + password. Show "Contact your administrator for an
account" — no signup link. Errors are generic ("Invalid credentials").

### 7.3 `/app` — Dashboard (authed)

Sidebar layout. Sidebar items: Dashboard, New Packet, Packets, Usage,
(Admin — admins only), Settings.

Dashboard content:

- Greeting with display name.
- Stat tiles: Total packets, Packets this month, Pages processed,
  Estimated spend (USD).
- "Recent packets" table: title, status badge, pages, created, overall
  status (pass/warning/fail), action (Open).
- "New packet" prominent CTA card.

### 7.4 `/app/new` — New packet upload

- Big drag-and-drop zone. Accepts PDF + images. Show file chips with size +
  remove.
- Live counter: "X / 10 files, est Y / 20 pages" (estimate page count
  client-side from PDF using `pdfjs-dist`).
- Optional "Title" field.
- Submit posts to FastAPI `/jobs`, then redirects to `/app/packets/{jobId}`.
- Hard-block submit if limits exceeded; show calm inline errors in EL/EN.

### 7.5 `/app/packets/{jobId}` — Live job progress (when not yet completed)

- Top: status pill (queued / triaging / processing / throttled / retrying /
  rate_limited / completed / failed) with subtle pulse animation while
  active.
- Progress bar: pages_processed / pages_selected.
- Current step message.
- Files list with each file's pages and a per-page state (pending,
  processing, done, failed).
- "Cancel" button is **not** required for v1.
- Auto-poll `/jobs/{job_id}` every 3s until terminal status, then redirect
  to the review view (same URL but rendered as the review).

### 7.6 `/app/packets/{jobId}` — Packet review (when completed)

This is the heart of the product. Three-column desktop layout.

**Header strip:**
- Title, overall status badge, file count, page count, created date.
- "Download JSON" and "Download Markdown report" buttons.
- Counts of checks by status.

**Left column — Pages (~280px):**
- Vertical list of pages grouped by source file.
- Each row: page thumbnail (lazy-loaded — backend doesn't currently expose
  thumbnails, so render a placeholder card with page number + filename for
  now and leave a `TODO: thumbnail endpoint` comment), triage badges:
  scanned / born_digital / mixed; tiny icons for handwriting / stamp /
  signature when present; field count.
- Click selects a page in the center column.

**Center column — Selected page detail:**
- Tabs: **Fields** (default), **Raw text**, **Triage info**.
- **Fields** tab: grouped list of extracted fields. Each field card shows:
  field_type label (translated), label_text, value (large), normalized
  value if different, confidence chip, flags (handwritten / stamped /
  signature), `evidence.nearby_text` in muted style, location hint. Each
  card has a `field_ref` you can copy and a "Show in clusters" link that
  scrolls/highlights the parent cluster on the right.

**Right column — Validation & clusters (~420px, tabbed):**

- Tab **Checks**: list of all `checks[]`, sorted fail → warning → unknown →
  pass. Each check card: status badge, title (translated), summary,
  expandable details. Evidence refs are clickable chips that, on click,
  navigate the left list to the right page and pulse-highlight the matching
  field in the center column.
- Tab **Clusters**: list of clusters grouped by `field_type`, with subtype
  badges (KAEK/AFM/ATAK/permit_number/registry_id) for identifiers. Each
  cluster shows canonical value, mention count, and an expandable list of
  mentions with jump-to-evidence behavior.
- Tab **Fuzzy review**: pairs of near-matching values side-by-side with
  evidence refs. No edit affordance for v1 — display only.
- Tab **Errors**: per-page failures with the redacted error message.

**Footer strip:**
- Cost summary: input/output/cached tokens and estimated USD.
- Executive summary text in a quoted block.

### 7.7 `/app/packets` — All packets list

Filterable, sortable table: Title, Status, Overall, Pages, Created, Cost.
Search by title. Pagination at 25/page.

### 7.8 `/app/usage`

Charts (recharts):

- Daily request count (last 30 days)
- Daily estimated cost (last 30 days)
- Token totals
- Current rate-limit status: requests this minute, this day, ceiling lines
  visible. Pull from `/usage`.

### 7.9 `/app/admin` (ADMIN role only)

- Users table (list, role, created, last login).
- Create user form (username, password, role, displayName).
- Reset password (generates new password, shows once).
- Delete user (confirms with typed username).
- Recent audit log entries.

### 7.10 `/app/settings`

- Change own password.
- Language preference (EL/EN), saved to `User.locale`.

---

## 8. UX details that matter

- **Empty states** for: no packets yet, no checks failed, no fuzzy matches.
  Calm, informative, never cute.
- **Long-running jobs**: when a job is in `throttled` or `rate_limited`,
  show the reason clearly with the current wait window. Don't blame the
  user.
- **Greek-first defaults**: every status badge, field type, document type
  (`building permit`, `declaration`, `plan`, `contract`, `technical memo`)
  must have a polished Greek translation. Cross-check with a domain glossary
  in your output.
- **Number/date formatting**: respect locale. KAEK/AFM are always shown
  monospaced.
- **Copying**: every `field_ref` and `cluster_id` is one-click copyable.
- **Keyboard navigation**: `j`/`k` between pages on the review screen,
  `1`/`2`/`3`/`4` between right-column tabs.
- **Loading**: skeleton screens, never spinners-only. Page review skeleton
  preserves the 3-column layout so it doesn't jump.
- **Errors**: a global error boundary with a clean "Something went wrong"
  card and a copy-error-id button.
- **Auth gating**: every `/app/*` route is server-side protected.
- **Responsive**: desktop-first ≥1280. Tablet (≥768) gets stacked columns.
  Mobile (<768) renders a simplified read-only review (no upload).
- **Accessibility**: keyboard reachable, focus rings visible, color is never
  the only signal for status.

---

## 9. File/folder layout to deliver

```
web/
  app/
    (marketing)/page.tsx
    login/page.tsx
    app/
      layout.tsx
      page.tsx                # dashboard
      new/page.tsx
      packets/page.tsx
      packets/[jobId]/page.tsx
      usage/page.tsx
      admin/page.tsx
      settings/page.tsx
    api/
      auth/[...nextauth]/route.ts
      packets/route.ts        # proxies to FastAPI /jobs
      packets/[jobId]/route.ts
      packets/[jobId]/packet/route.ts
      packets/[jobId]/report/route.ts
      usage/route.ts
      admin/users/route.ts
  components/                  # shadcn + custom
  lib/
    api.ts                     # FastAPI client (server-only, holds service token)
    auth.ts
    db.ts                      # Prisma client
    i18n.ts
    types.ts                   # zod schemas mirroring packet.json
  messages/el.json
  messages/en.json
  prisma/schema.prisma
  scripts/create-user.ts
  README.md                    # how to dev, env vars, Railway deploy
  .env.example
```

Required env vars (document them in `.env.example`):

```
DATABASE_URL=
NEXTAUTH_SECRET=
NEXTAUTH_URL=
ARCH_OCR_API_BASE=http://localhost:8000   # FastAPI base URL
ARCH_OCR_SERVICE_TOKEN=                    # mirrors OCR_ADMIN_TOKEN on FastAPI
```

---

## 10. Out of scope (do NOT build)

- Self-service signup or password reset flows
- Billing, plans, Stripe, invoices
- Editing extracted values, merging/splitting clusters, overriding check status
- Multi-tenant orgs, team invites, RBAC beyond USER/ADMIN
- Bounding-box overlays on page images (backend doesn't return coordinates)
- Email notifications
- Real-time websockets (polling is fine)

---

## 11. Deliverables

1. Full Next.js codebase in `web/` matching the layout above.
2. Prisma schema + migration + seed/create-user script.
3. `web/README.md` with setup, dev, build, and Railway deploy instructions
   (including how to run the FastAPI backend alongside).
4. Both `messages/el.json` and `messages/en.json`, fully translated.
5. A short `web/DESIGN_NOTES.md` explaining color tokens, type scale, and
   how to extend with new field types or check types.
6. Screenshots (or Storybook) of: landing, login, dashboard, upload, live
   job, review screen (light + dark if dark is included).

Build the whole thing. Be opinionated. Make it look like a tool a notary
would trust on a Tuesday morning.
