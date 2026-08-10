# Panel Inspector — Complete Project Documentation

A professional electrical panel inspection tool for Schneider Electric field engineers.
Built as a **web-based Progressive Web App (PWA)** — installable on Android, iOS, and
desktop straight from the browser — backed by a Python AI server.

The engineer takes a photo of an electrical panel on their phone (or uploads one).
Within seconds the AI identifies every breaker, warns about live busbars,
generates safety steps, and produces an inspection checklist and report.

**AI Used: Google Gemini (`gemini-3.1-pro-preview` for analysis, `gemini-2.0-flash` for fast OCR)**

---

## What Problem Does This Solve?

When an electrical engineer goes to inspect or work on a switchboard they need to know:

- What type of panel is this?
- Where exactly are all the breakers?
- Is there a live busbar nearby that could kill me even with the main breaker off?
- What LOTO (Lockout/Tagout) steps do I follow?
- What PPE (arc flash protection) do I need?
- What does the Schneider maintenance checklist say for this exact panel model?

Before this app, engineers had to carry physical catalogues, rely on memory,
or call a specialist. This app automates all of that in seconds from a phone photo.

---

## System Overview

```
Engineer's Browser (installed as a PWA — Android / iOS / desktop)
        │
        │  HTTPS POST (same origin as the app itself)
        │  Sends: photo + work zone + task + SLD image (if uploaded)
        ▼
FastAPI Server (server.py) — cloud-hosted, one container
        │  Serves the web/ folder (the app itself) AND the /api/* routes
        │
        │  HTTPS to Google API
        │  Sends: photo + SLD + layout + instructions
        ▼
Google Gemini AI (gemini-3.1-pro-preview / gemini-2.0-flash)
        │
        │  Returns: panel type, breaker boxes, circuit labels, ratings, warnings
        ▼
FastAPI Server
        │  Safety engine adds LOTO/PPE/arc flash warnings
        │  Catalogue engine adds Schneider maintenance checklist
        │  Saves scan to PostgreSQL (Supabase) / SQLite
        │
        │  HTTPS JSON response
        ▼
Browser (PWA)
        Draws bounding boxes on photo
        Shows safety warnings and checklist
        Engineer saves the scan to History
```

The app is not a native mobile app — it's a set of static pages (`web/*.html`, `web/js/*.js`)
served by the same FastAPI process that serves the API. A `manifest.json` and a service
worker (`sw.js`) make it installable and give it an app icon and offline shell caching,
which is what makes it feel like a native Android app without needing Kotlin, an APK,
or the Play Store.

---

## Part 1 — Web App (Page by Page)

### How the App is Built
- Language: Vanilla JavaScript (no framework — no React/Angular/Vue)
- Markup/styling: Plain HTML + CSS (`web/css/*.css`)
- HTTP requests: native `fetch()` API
- Camera/upload: native `<input type="file" accept="image/*" capture="environment">`
- Session/local state: `localStorage` (login state, cached preferences)
- Installability: `manifest.json` + `sw.js` (service worker) — makes it a PWA
- Icons: generated server-side by `GET /api/pwa-icon`

---

### Page 1 — `login.html` / `login.js`

What it does:
- First page when the app opens
- Username and password fields
- Sends `POST /api/login` with `{ username, password }`
- On success (`200`): stores `pi_auth = '1'` and `pi_user = <username>` in `localStorage`, redirects to `home.html`
- On failure (`401`): shows "Invalid username or password"
- On network failure: shows "Cannot reach server. Check your connection."
- If already logged in (`pi_auth` already set) → skips straight to `home.html`

Where credentials are stored:
- Server side, checked against a `USERS` dictionary in `server.py`

---

### Page 2 — `home.html` / `home.js` (Dashboard)

What it does on load:
- Redirects back to `login.html` if `pi_auth` isn't set in `localStorage`
- Fetches `GET /api/scans` and renders:
  - Recent scan activity
  - Animated stat counters (via `animateNum`)
- Greets the logged-in user by the `pi_user` value from `localStorage`

Navigation:
- Links out to the inspection wizard (`index.html`), scan history (`history.html`), and the SLD compare tool
- **Sign Out** clears `pi_auth` / `pi_user` from `localStorage` and redirects to `login.html`

---

### Page 3 — `index.html` / `index.js` (Inspection Wizard)

The main workflow — a 4-step guided flow shown as a stepper bar at the top:

1. **Intervention Type** — Live or Dead work
2. **Work Access & Task** — which doors are being opened, and the task type:
   Commissioning · Maintenance · Modification · Replacement · Others
3. **Documents** — optional SLD (Single Line Diagram) and mechanical layout upload
4. **Capture & Analyse** — take/upload the panel photo, draw a work zone, run the AI

**Photo capture:**
- Native file input with `capture="environment"` opens the phone's rear camera directly, or falls back to gallery picker
- Image is read as a base64 string client-side, no native camera library needed

**Work zone drawing:**
- Canvas-based rectangle drawing directly on the loaded photo (`startDraw`/mouse & touch handlers)
- A safety buffer is computed as a slightly expanded version of the drawn rectangle
- Both sent to the server as normalised 0–1000 coordinates, same as the coordinate system in Part 4

**What gets sent to the server** (`POST /api/analyze`):
```text
imageBase64    — the panel photo, base64 JPEG
workZone       — {ymin, xmin, ymax, xmax}, normalised 0-1000
safetyBuffer   — slightly expanded work zone
sldBase64      — SLD image, if uploaded in step 3
layoutBase64   — mechanical layout image, if uploaded in step 3
task           — "maintenance" etc., from step 2
username       — from localStorage (pi_user)
projectName / site / inspector — project metadata
```

**Other actions available from this page**, each hitting its own endpoint:
- `POST /api/aging` — aging/condition assessment of the equipment
- `POST /api/read-sld` — read and interpret an uploaded single-line diagram
- `POST /api/compare-sld` — compare the SLD against what the photo shows
- `POST /api/checklist` — fetch the Schneider maintenance checklist for the panel/task

**Rendering the result** (`renderResults`):
- Draws colour-coded bounding boxes over the photo on a `<canvas>` (same colour-by-family scheme as before: red = ACB, orange = MCCB, blue = MCB)
- Shows panel type, notes, safety warnings, and checklist inline on the page
- `renderError` shows a clear error state if the AI call fails

---

### Page 4 — `history.html` / `history.js`

What it does:
- Fetches `GET /api/scans` and renders every past scan as a card grid
- Stat tiles at the top (total scans, total warnings, etc.) via `renderStats`
- Filters via `applyFilters` (by panel type, task, date)
- Tapping a card (`openDetail`) opens a slide-in drawer with the full scan detail — this replaced an earlier click-to-print interaction
- `openPrint` lets the engineer print/export the scan record from the browser's native print dialog
- **Sign Out** available here too, same `localStorage` clear as the dashboard

---

### Page 5 — `results.html`

- Dedicated full-page view for a single scan's results (bounding boxes, warnings, checklist), reused by both the wizard's inline result view and links from history

---

### Other Server-Rendered / Utility Endpoints Used by the Web App

- `GET /api/scan-image/{filename}` — serves the saved photo for a given scan
- `GET /api/scans/{scan_id}` — fetch one scan's full record
- `GET /api/projects` — list saved project names for autocomplete
- `POST /api/verify_panel` — compares a fresh photo against a reference photo to confirm it's the same panel before starting work (same purpose as before — prevents working on the wrong switchboard — just invoked from the web wizard instead of a dedicated Android screen)
- `POST /api/locate_vbb` — dedicated VBB (Vertical Busbar Box) location flow for PrismaSeT P panels; the VBB is always live even with the main breaker off, so this returns a highlighted box and a hazard warning
- `GET /api/pwa-icon?size=192|512` — generates the app icon referenced by `manifest.json`, so the installed PWA has a real icon on the home screen

---

## Part 2 — Python Server (server.py)

### What Technology Is Used

- **Language**: Python 3.9
- **Web framework**: FastAPI with Uvicorn
- **AI**: Google Gemini only (`gemini-2.5-pro-preview` via `google-genai` SDK)
- **Database**: SQLite (built into Python — no separate database server needed)
- **Image storage**: Local folder `scans_images/` on the laptop

---

### Server Startup

When `server.py` starts:

1. Reads `GEMINI_KEY` from environment variable
2. Creates a Gemini client: `_genai.Client(api_key=GEMINI_KEY)`
3. Calls `_init_db()` — creates the SQLite database and tables if they don't exist yet
4. Creates `scans_images/` folder if it doesn't exist
5. Starts FastAPI on `0.0.0.0:8000` (accessible from any device on the network)
6. Prints the local IP address so you know what to put in the Android app

---

### Database — SQLite (breaker_data.db)

The server uses a SQLite database file called `breaker_data.db` stored in the same folder as `server.py`.

SQLite is a simple file-based database — no separate database server needed, everything is in one file.

**Table 1: projects**

Stores one row per unique project (project name + site + inspector combination).

```sql
CREATE TABLE IF NOT EXISTS projects (
    id           TEXT PRIMARY KEY,   -- UUID like "a1b2c3d4-..."
    project_name TEXT NOT NULL,      -- e.g. "Site A - LV Room 1"
    site         TEXT,               -- e.g. "Jurong Island"
    inspector    TEXT,               -- e.g. "Ahmad bin Ali"
    created_at   TEXT NOT NULL       -- ISO timestamp e.g. "2026-04-20T08:30:00"
);
```

**Table 2: scans**

Stores one row per scan (every time the engineer taps Analyze).

```sql
CREATE TABLE IF NOT EXISTS scans (
    id              TEXT PRIMARY KEY,  -- UUID for this specific scan
    project_id      TEXT,              -- links to projects table (foreign key)
    timestamp       TEXT NOT NULL,     -- when the scan was done (UTC ISO format)
    username        TEXT,              -- logged-in engineer's username
    panel_type      TEXT,              -- "PrismaSeT P", "PrismaSeT G", or "Okken"
    notes           TEXT,              -- Gemini's one-sentence summary
    safety_warnings TEXT,              -- JSON array of warning strings
    task            TEXT,              -- "maintenance", "commissioning" etc.
    image_path      TEXT,              -- filename of saved JPEG in scans_images/
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
```

**How data gets into the database (after every scan):**

Step 1 — Save the photo:
- Decodes the Base64 image from the request
- Saves it as `{scan_uuid}.jpg` inside the `scans_images/` folder

Step 2 — Upsert the project:
- Checks if a project with the same name + site + inspector already exists
- If yes: reuses the existing project_id
- If no: creates a new row in `projects` table with a new UUID

Step 3 — Insert the scan:
- Creates a new row in `scans` table with all the scan details
- `safety_warnings` is stored as a JSON string (e.g. `["⚡ Warning 1", "🔒 Step 2"]`)
- `scan_id` is sent back to the Android app in the response

This means:
- Every scan is permanently recorded on the laptop
- You can query the database later to see all scans across all projects
- The image file is kept on disk linked to each scan record

---

### User Authentication

Simple hardcoded dictionary in server.py:

```python
USERS = {
    "santosh":  "schneider123",
    "admin":    "admin123",
    "techuser": "tech2026",
}
```

`POST /api/login` — checks username + password, returns `{"success": true}` or 401.

To add a new user: edit the `USERS` dictionary and restart the server.

---

### Google Gemini AI Integration

**Model used**: `gemini-2.5-pro-preview`

Gemini is a multimodal AI — it can look at images and understand them, not just text.

**How the request to Gemini is built:**

The server builds a `parts` list which is everything Gemini will see:

```
parts = [
    (1) SLD image     ← if uploaded by engineer
        + text: "This is the Single Line Diagram. Use it to cross-reference."

    (2) Layout image  ← if uploaded by engineer
        + text: "This is the Mechanical Layout. Use it for cubicle positions."

    (3) Panel photo   ← the actual photo taken by the engineer

    (4) Instruction prompt
]
```

All images are sent as Base64 JPEG inline data directly in the API request.

**The instruction prompt tells Gemini:**
- How to identify the panel type (PrismaSeT G / P / Okken)
- Which specific visual features to look for (VBB door, draw-out ACB, double doors)
- How to draw bounding boxes (normalised 0-1000)
- To read circuit labels and ratings from breaker faces
- What product names to use exactly
- Not to label cable ducts or enclosures as breakers

**Structured output (Pydantic schema):**

The server uses Gemini's structured output feature. Instead of asking for plain text,
it defines an exact Python class that the response must match:

```python
class _Breaker(BaseModel):
    type:          str       # "Compact NSX", "MasterPact MTZ" etc.
    box:           list[int] # [ymin, xmin, ymax, xmax] normalised 0-1000
    circuit_label: str       # text on label strip e.g. "LV MAIN" (empty if not readable)
    rating:        str       # current rating e.g. "400A" (empty if not readable)

class _DetectionResult(BaseModel):
    breakers:        list[_Breaker]
    panel_type:      str        # "PrismaSeT G", "PrismaSeT P", or "Okken"
    busbar_side:     str        # "left", "right", or "unknown"
    notes:           str        # one sentence summary
    safety_warnings: list[str]  # any immediate hazards Gemini noticed
```

Gemini is forced to return JSON that matches this exact structure.
This means the server never has to parse free-form text — it always gets clean structured data.

**Retry logic:**
- If Gemini fails or returns invalid JSON → server retries up to 3 times with 2 second delay
- This handles temporary API errors gracefully

---

### Safety Assessment Engine

After Gemini returns the breaker list and panel type, the server runs its own
safety assessment that is not done by AI — it uses fixed logic based on
Schneider Electric's training slide library.

**Step 1: Find work zone position**

The work zone's vertical centre is calculated:
`zone_cy = (workZone.ymin + workZone.ymax) / 2`

If breakers span a wide range (panel_ymax - panel_ymin > 200):
- Position is calculated relative to the panel content, not the full image
- This handles zoomed-in photos correctly
- `relative_cy = (zone_cy - panel_ymin) / (panel_ymax - panel_ymin)`
- < 0.40 = TOP, > 0.60 = BOTTOM, else MIDDLE

If only a few breakers detected (small range):
- Falls back to raw image Y axis
- zone_cy < 400 = TOP, > 600 = BOTTOM

**Step 2: Generate warnings based on panel type + position**

For PrismaSeT G:
- TOP zone → LOTO on incomer from top, VAT check, PPE Cat 1
- MIDDLE zone → isolate each feeder individually, electric shock risk everywhere
- BOTTOM zone → LOTO on bottom incomer, risk of dropping tools

For PrismaSeT P (small ≤4 cubicles):
- TOP zone → VBB is live at top, highest arc flash risk
- MIDDLE zone → feeder zone, ACB draw-out risk
- BOTTOM zone → cable entry zone warnings

For PrismaSeT P (large >4 cubicles — multiple transformers):
- Higher arc flash warnings (significant arc flash energy)
- ERMS strongly recommended

For Okken:
- TOP zone → HBB (horizontal busbar) at top, highest risk
- MIDDLE zone → draw-out ACB zone
- BOTTOM zone → cable entry

**MasterPact MTZ specific:**
- If an MTZ is detected anywhere in the scan → recommends ERMS activation
- ERMS = Energy Reduction Maintenance Setting — temporarily reduces arc flash energy

---

### Catalogue Guidance Engine

This is completely separate from the safety warnings.
It provides the official Schneider maintenance/commissioning checklist for the panel type.

It only runs when **no work zone is drawn** (general scan mode).
When a work zone is drawn, safety warnings replace the checklist.

Supported combinations:

| Panel Type | Tasks with checklists |
|------------|----------------------|
| PrismaSeT P | commissioning, maintenance, modification, replacement |
| PrismaSeT G | commissioning, maintenance, modification, replacement |
| Okken | commissioning, maintenance, modification, replacement |
| MasterPact MTZ | commissioning, maintenance, modification, replacement |

**MasterPact MTZ maintenance checklist** (most detailed — based on DOCA0099EN-05 guide):

Routine (annual):
- NII_Z_1 — General condition inspection
- NII_Z_1 — Manual OPEN/CLOSE operation test
- NII_Z_2 — MCH gear motor electrical operation test
- NII_Z_3 — Spring charge indicator check
- NII_Z_1 — Auxiliary contacts and wiring check
- NII_Z_1 — Chassis racking test (CONNECTED → TEST → DISCONNECTED)

Intermediate (every 3-5 years):
- NIII_Z_1 — Main contact erosion inspection
- NIII_Z_2 — Arc chute carbon deposits check
- NIII_Z_4 — MicroLogic X trip unit settings verification
- NIII_Z_2 — Chassis lubrication with Schneider approved grease
- NIII_Z_3 — Disconnecting contacts inspection
- NIII_Z_4 — Earth connection check

Manufacturer level (every 5-10 years):
- Full disassembly by Schneider-certified engineer
- Breaking unit replacement if contacts worn past limit
- Full mechanism overhaul
- Control unit calibration with injection test set

---

### API Endpoints

**POST /api/login**
```json
Request:  { "username": "santosh", "password": "schneider123" }
Response: { "success": true, "message": "Login successful" }
          or 401: { "success": false, "message": "Invalid username or password" }
```

**POST /api/analyze** — Main endpoint
```json
Request: {
  "imageBase64":  "<base64 JPEG>",
  "mimeType":     "image/jpeg",
  "workZone":     { "ymin": 200, "xmin": 100, "ymax": 800, "xmax": 900 },
  "safetyBuffer": { "ymin": 180, "xmin": 80,  "ymax": 820, "xmax": 920 },
  "sldBase64":    "<base64 JPEG or omit>",
  "layoutBase64": "<base64 JPEG or omit>",
  "task":         "maintenance",
  "identifyOnly": false,
  "busbarOnly":   false,
  "projectName":  "Site A",
  "site":         "Jurong Island",
  "inspector":    "Ahmad",
  "username":     "santosh"
}

Response: {
  "breakers": [
    {
      "type":          "MasterPact MTZ",
      "box":           [120, 80, 420, 300],
      "circuit_label": "LV MAIN INCOMER",
      "rating":        "1600A"
    }
  ],
  "panel_type":         "PrismaSeT P",
  "panel_summary":      "Identified by draw-out ACB and narrow VBB door on left",
  "busbar_side":        "left",
  "cubicle_count":      3,
  "cubicle_line":       "Work zone is in cubicle 2 (breaker cubicle)",
  "safety_warnings":    ["🔒 LOTO on MTZ incomer.", "🦺 PPE Cat 2 required."],
  "notes":              "1600A MTZ incomer and 4 Compact NSX feeders detected.",
  "catalogue_guidance": "",
  "qr_codes":           [],
  "scan_id":            "a1b2c3d4-uuid"
}
```

All bounding box coordinates in the response are in **original image pixels** (not 0-1000).
The server converts them: `pixel = normalised_0_1000 / 1000 * image_dimension`

**POST /api/verify_panel**
```json
Request:  { "referenceBase64": "<base64>", "workerBase64": "<base64>", "mimeType": "image/jpeg" }
Response: { "match": true, "confidence": "high", "reason": "Same panel confirmed by identical busbar positions and labels." }
```

---

## Part 3 — Data Storage (Two Places)

### Server Side — PostgreSQL (Supabase), SQLite fallback

**Database** — one abstraction (`_get_db()` in `server.py`) picks the driver at start-up:
- If `DATABASE_URL` is set → connects to PostgreSQL (hosted on Supabase in production)
- If not set → falls back to a local SQLite file (`breaker_data.db`) for zero-setup local dev
- Same SQL and the same code path either way — only the connection differs
- Two tables: `projects` and `scans`
- `scans_images/` folder (or equivalent cloud storage) holds the actual JPEG of every scanned panel
- Persists across deploys/restarts since it's a managed database, not a file on a laptop

**To inspect the local SQLite fallback manually:**

```bash
sqlite3 breaker_data.db
.tables               # shows: projects  scans
SELECT * FROM scans;  # shows all scans
SELECT * FROM projects;
.quit
```

### Browser Side — `localStorage`

The web app keeps almost no state client-side — the database is the source of truth, and pages just re-fetch from `GET /api/scans` whenever they need data. `localStorage` only holds the lightweight session flags:

| Key | What it stores |
|-----|----------------|
| `pi_auth` | `'1'` if logged in, otherwise absent — checked by every page on load |
| `pi_user` | logged-in username — shown in greetings, sent with scan requests |

There is no separate on-device scan history file, PDF store, or SharedPreferences equivalent — history, images, and reports are all fetched live from the server (`GET /api/scans`, `GET /api/scan-image/{filename}`), which is what makes the same account usable from any device without syncing.

---

## Part 4 — Coordinate System

Everything uses coordinates normalised to 0–1000.

```
(xmin=0, ymin=0) ──────────── (xmax=1000, ymin=0)
       │                              │
       │          Panel Image         │
       │                              │
(xmin=0, ymax=1000) ──────── (xmax=1000, ymax=1000)
```

- Top-left of image = (0, 0)
- Bottom-right = (1000, 1000)
- A breaker in the top-left area: ymin=50, xmin=30, ymax=200, xmax=180

The Android overlay converts these to screen pixels:

```kotlin
val sc   = min(viewWidth / imageWidth, viewHeight / imageHeight)
val offX = (viewWidth  - imageWidth  * sc) / 2f   // letterbox offset
val offY = (viewHeight - imageHeight * sc) / 2f

val screenX = pixelX * sc + offX
val screenY = pixelY * sc + offY
```

The server returns pixel coords (already converted from 0-1000).
The conversion formula is: `pixel = normalised / 1000 * image_dimension`

---

## Part 5 — All Files Explained

### Server Side

| File | What it does |
|------|-------------|
| `server.py` | The entire backend — FastAPI routes, Gemini integration, safety engine, catalogue engine, database logic — also mounts and serves `web/` |
| `.env` | Contains `GEMINI_KEY=...`, `DATABASE_URL=...`, Supabase keys — never share or commit this file |
| `requirements.txt` | Python dependencies (FastAPI, Gemini SDK, OpenCV, psycopg2, supabase, etc.) |
| `Dockerfile` | Multi-stage build — packages the server + `web/` into one deployable container |
| `render.yaml` | Infra-as-code deployment config (env vars, start command) for Render |
| `breaker_data.db` | Local SQLite fallback database — auto-created on first run when `DATABASE_URL` isn't set |
| `scans_images/` | Folder where every scanned panel photo is saved (local dev) |
| `panel_library.json` | Reference data for panel specifications |

### Web App Side (`web/`)

| File | What it does |
|------|-------------|
| `login.html` / `js/login.js` | Login page and its logic |
| `home.html` / `js/home.js` | Dashboard — recent scans, stats, navigation |
| `index.html` / `js/index.js` | The inspection wizard — capture, work zone, analyze, aging, SLD read/compare, checklist |
| `history.html` / `js/history.js` | Scan history grid, filters, slide-in detail drawer |
| `results.html` | Full-page single-scan result view |
| `dashboard.html` | Extended dashboard/reporting view |
| `portfolio.html` | Project/portfolio overview page |
| `manifest.json` | PWA manifest — app name, icons, start URL, theme color — makes the app installable |
| `sw.js` | Service worker — caches the app shell for offline loading and fast repeat visits |
| `css/*.css` | One stylesheet per page, split out from the HTML |

---

## Part 6 — Setup

### Requirements
- Python 3.11+ (matches `runtime.txt` / the Docker base image)
- A Gemini API key — free at ai.google.dev
- Optional: a Supabase/PostgreSQL project if you want to use `DATABASE_URL` instead of the SQLite fallback
- Any modern browser — no Android SDK, emulator, or native build tooling needed

### Run Locally

```bash
cd /path/to/project_API_google
pip install -r requirements.txt
GEMINI_KEY=your_key_here uvicorn server:app --reload --port 8000
```

Then open `http://localhost:8000/home.html` in a browser. Because the same FastAPI
process serves both the API and the `web/` folder, there's no separate frontend
server, no IP address to configure, and no CORS setup needed for local use.

### Install as an App (PWA)

On a phone or desktop, open the deployed URL (or `http://localhost:8000/home.html`
locally) in the browser and use **"Add to Home Screen"** (Android/Chrome) or
**"Install"** (desktop Chrome/Edge). The manifest and service worker handle the rest —
no APK, no Play Store listing, no separate build per platform.

### Deploy to the Cloud

The app ships as a single Docker container (`Dockerfile`) and can be deployed to
Cloud Run, Render, or any container host. Required environment variables:

| Variable | Purpose |
|----------|---------|
| `GEMINI_KEY` | Google Gemini API key |
| `DATABASE_URL` | PostgreSQL connection string (omit to fall back to SQLite) |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | If using Supabase for storage/auth |

`render.yaml` already declares these as deploy-time secrets for a one-click Render setup.

---

## Part 7 — Features Implemented

### Done
- Installable Progressive Web App (Android / iOS / desktop) — no APK, no app store
- Login page with session held in `localStorage`
- Home dashboard with recent scan activity and stats
- Project/site/inspector details captured in the wizard
- SLD and mechanical layout upload — accepts image or PDF directly (PDFs are read natively by Gemini; images convert to JPG client-side via pdf.js when needed for preview)
- Photo capture via native `<input capture>` (opens rear camera) or gallery/file upload
- Work zone drawing on the photo (canvas-based rectangle)
- Safety buffer auto-expansion around the drawn work zone
- Panel type identification (PrismaSeT G / P / Okken)
- Breaker detection with colour-coded bounding boxes, drawn on canvas
- Circuit label reading per breaker (e.g. "LV MAIN")
- Current rating reading per breaker (e.g. "400A")
- Busbar side detection (left/right VBB)
- Slide-accurate safety warnings (LOTO / PPE / arc flash)
- ERMS recommendation when MasterPact MTZ detected
- Schneider catalogue checklists (PrismaSeT P/G, Okken, MTZ)
- Aging/condition assessment (`/api/aging`)
- SLD reading and SLD-vs-photo comparison (`/api/read-sld`, `/api/compare-sld`)
- Report export via the browser's native Print / Save-as-PDF dialog
- Scan history page with filters and a slide-in detail drawer
- AI panel identity verification (prevent working on wrong panel)
- VBB locate mode
- PostgreSQL (Supabase) database with SQLite fallback — records every scan permanently
- Scan images saved and served back via `/api/scan-image/{filename}`

### Planned / Not Yet Done
- Tap a breaker box on the result view to zoom-crop and re-read its label
- QR code reading from panel labels
- Offline scan queueing (capture offline, sync when back online)
