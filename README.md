# Panel Inspector — Complete Project Documentation

A professional electrical panel inspection tool for Schneider Electric field engineers.
Built with an Android app and a Python AI server.

The engineer takes a photo of an electrical panel on their phone.
Within seconds the AI identifies every breaker, warns about live busbars,
generates safety steps, and produces a PDF inspection report.

**AI Used: Google Gemini only (gemini-2.5-pro-preview)**

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
Engineer's Phone (Android App)
        │
        │  Same WiFi network
        │  HTTP POST to port 8000
        │  Sends: photo + work zone + task + SLD image (if uploaded)
        ▼
Laptop running Python Server (server.py)
        │
        │  HTTPS to Google API
        │  Sends: photo + SLD + layout + instructions
        ▼
Google Gemini AI (gemini-2.5-pro-preview)
        │
        │  Returns: panel type, breaker boxes, circuit labels, ratings, warnings
        ▼
Python Server
        │  Safety engine adds LOTO/PPE/arc flash warnings
        │  Catalogue engine adds Schneider maintenance checklist
        │  Saves scan to SQLite database on laptop
        │
        │  HTTP JSON response
        ▼
Android App
        Draws bounding boxes on photo
        Shows safety warnings
        Engineer saves/shares PDF report
```

---

## Part 1 — Android App (Screen by Screen)

### How the App is Built
- Language: Kotlin
- Camera: CameraX library
- HTTP requests: OkHttp
- JSON parsing: Gson + org.json
- Images: Glide
- Storage: SharedPreferences (settings) + JSON file (scan history)
- PDF: Custom ReportGenerator

---

### Screen 1 — LoginActivity

What it does:
- First screen when the app opens
- Shows a username and password input
- Sends login request to the server: `POST /api/login`
- Server checks credentials against a hardcoded user list
- On success: saves `is_logged_in = true` to SharedPreferences and goes to MainActivity
- On failure: shows error message

Where credentials are stored:
- Server side in `USERS` dictionary in server.py
- Current users: santosh / admin / techuser

---

### Screen 2 — MainActivity (Home Dashboard)

What it shows:
- **Scan count tile** — total number of scans done (tappable, goes to Reports)
- **Warnings tile** — total safety warnings encountered across all scans (tappable, shows summary dialog)
- **Last panel scanned** — product type and how long ago
- **Project subtitle** — active project name in green if set, grey prompt if not
- **Documents subtitle** — shows "SLD uploaded", "Layout uploaded", "SLD + Layout uploaded", or prompt
- **Pulsing dot** — green if server is reachable, orange if offline (checked every 10 seconds)
- **Sign Out button** — shows confirmation dialog, clears login state

Navigation cards:
- **Scan** — starts the guided flow: Documents → Task Selection → Camera
- **Reports** — opens scan history
- **Project Details** — fill in project name, site, inspector
- **Documents** — upload SLD and mechanical layout

How the pulse check works:
- Every 10 seconds, tries to open a TCP socket to the server IP on port 8000
- If it connects within 2 seconds → "Model ready" (green)
- If it fails → "Server offline" (orange)

---

### Screen 3 — ProjectDetailsActivity

What it does:
- Three text fields: Project Name, Site Location, Inspector Name
- Saved to SharedPreferences under key `"google_api_prefs"`
- These values are:
  - Shown in the home screen subtitle
  - Sent to the server with every scan request
  - Printed on every PDF report

---

### Screen 4 — DocumentsActivity

What it does:
- Upload two reference images from the phone gallery:

**SLD (Single Line Diagram)**
- The electrical diagram of the panel showing all circuits, ratings, and connections
- Saved as `sld_image_path.jpg` in the app's internal files directory
- Path stored in SharedPreferences as `"sld_image_path"`

**Mechanical Layout**
- Physical diagram showing cubicle positions and dimensions
- Saved as `layout_image_path.jpg`
- Path stored in SharedPreferences as `"layout_image_path"`

How they are used:
- When WorkZoneActivity sends a scan request, it reads both file paths from SharedPreferences
- Loads both as Bitmaps, converts them to Base64 JPEG strings
- Sends them as `sldBase64` and `layoutBase64` in the JSON payload
- Server passes them to Gemini as additional context images before the panel photo
- Gemini reads them and uses them to cross-reference what it sees in the panel photo

Important note:
- Both are OPTIONAL. Most panels in the field do not have a mechanical layout.
- If neither is uploaded → app still works normally
- If only SLD uploaded → Gemini uses it for circuit cross-reference
- If both uploaded → best accuracy

---

### Screen 5 — TaskSelectionActivity

What it does:
- Engineer selects what type of work they are doing:
  - Commissioning (new installation check)
  - Maintenance (routine or periodic)
  - Modification (adding or changing components)
  - Replacement (replacing a broken breaker)
  - Others (general inspection)

Why it matters:
- This value is sent to the server as `task`
- The catalogue engine on the server uses it to pick the right checklist
- For example: Maintenance on MasterPact MTZ → returns NII_Z_1 / NIII_Z_1 procedure codes
- The task type is also saved in the scan record and printed on the PDF

---

### Screen 6 — ScanActivity (Camera)

What it does:
- Shows a live camera preview using CameraX
- Engineer frames the panel in the viewfinder
- Taps the green FAB button to capture the photo

**Pinch-to-zoom:**
- Engineer can use two fingers to zoom in before taking the photo
- Implemented using `ScaleGestureDetector`
- On pinch: reads current `zoomRatio` from `camera.cameraInfo.zoomState`
- Multiplies by `detector.scaleFactor` (>1 = zoom in, <1 = zoom out)
- Sets new zoom via `camera.cameraControl.setZoomRatio()`
- CameraX automatically clamps to the device's supported zoom range
- Why this matters: zooming in makes label text bigger in the photo so Gemini can read it

**Photo quality check (runs after capture):**

1. Brightness check:
   - Samples every 10th pixel of the image
   - Converts each pixel to greyscale: `R*0.299 + G*0.587 + B*0.114`
   - Averages all samples
   - Average < 50 → "too dark" warning
   - Average > 220 → "too bright" warning

2. Blur check (Laplacian variance):
   - Runs on the centre 50% of the image
   - Applies a Laplacian filter: `4*centre - top - bottom - left - right` per pixel
   - Calculates variance of all Laplacian values
   - Variance < 80.0 → "blurry" warning
   - Low variance means there are no sharp edges in the image = blurry

If quality check fails:
- Dialog appears: "Image Too Dark / Too Bright / Blurry"
- Options: "Retake" (dismiss) or "Continue Anyway"

If quality check passes:
- Goes directly to WorkZoneActivity

Alternative:
- Gallery FAB button lets engineer pick an existing photo from the phone gallery

---

### Screen 7 — WorkZoneActivity

What it does:
- Shows the captured photo full screen
- Engineer draws a rectangle on the area they want to work in
- This is called the **Work Zone**

How the drawing works:
- Touch down → start corner of rectangle
- Drag → live preview of rectangle
- Touch up → rectangle confirmed
- Uses `WorkZoneOverlay` custom view for the drawing interaction

Safety Buffer:
- After the work zone is confirmed, the app automatically creates a slightly bigger rectangle
- This is the **Safety Buffer** — expands the work zone by ~10% on each side
- The safety buffer is the actual boundary used for breaker detection on the server
- Both are sent to the server in normalised 0-1000 coordinates

Buttons:
- **Analyze Zone** — sends photo + work zone + safety buffer to server for full analysis
- **Identify Panel** — sends photo without work zone, just identifies panel type and all breakers

What gets sent to the server:
```
- imageBase64       (the panel photo compressed to max 1536px, JPEG quality 90)
- workZone          (ymin, xmin, ymax, xmax — normalised 0-1000)
- safetyBuffer      (slightly expanded work zone)
- sldBase64         (SLD image if uploaded — from SharedPreferences)
- layoutBase64      (layout image if uploaded — from SharedPreferences)
- task              (from TaskSelectionActivity — "maintenance" etc.)
- username          (from login_prefs SharedPreferences)
- projectName       (from google_api_prefs SharedPreferences)
- site              (from google_api_prefs SharedPreferences)
- inspector         (from google_api_prefs SharedPreferences)
```

---

### Screen 8 — ResultActivity (Result Screen)

This is the main output screen. It shows the annotated photo and all AI findings.

**Photo display:**
- Full screen ImageView with `scaleType="fitCenter"`
- Transparent `BoundingBoxOverlay` view sits exactly on top
- All boxes and zones are drawn on the overlay, not on the image itself

**What BoundingBoxOverlay draws (in order, back to front):**

1. Safety Buffer — red dashed rectangle
   - Shows the boundary that was used for breaker detection

2. Work Zone — green semi-transparent filled rectangle with solid border
   - Shows exactly where the engineer said they want to work

3. Cubicle segments (if returned by server) — coloured boxes labelled C1, C2, C3...
   - Each cubicle gets a different colour (red, blue, green, orange, purple, cyan)
   - Label centred inside the box
   - Used in PrismaSeT P to show the VBB cubicle position

4. Busbar strip (fallback if no cubicle boxes) — orange shaded area
   - 18% of panel width on left or right side
   - Only shown when server returns `busbar_side` = "left" or "right"
   - Represents the VBB (Vertical Busbar Box) — always live, never open

5. Breaker bounding boxes — one per detected breaker
   - Colour by breaker family:
     - Red (#F44336) = ACB: MasterPact MTZ, MasterPact NT
     - Orange (#FF9800) = MCCB: Compact NSX, Compact NS
     - Blue (#2196F3) = MCB: Acti9, iC60, Multi9
     - Green (#4CAF50) = unknown
   - Above the box: product name label (e.g. "COMPACT NSX")
   - Below the box: circuit label and rating (e.g. "LV MAIN | 400A") — only if Gemini could read it

**How overlay coordinates work:**
- The image is displayed with `fitCenter` — it may have black bars (letterboxing) on sides or top/bottom
- The overlay must match the image position exactly
- Scale factor: `sc = min(viewWidth / imageWidth, viewHeight / imageHeight)`
- Horizontal offset: `offX = (viewWidth - imageWidth * sc) / 2`
- Vertical offset: `offY = (viewHeight - imageHeight * sc) / 2`
- Any pixel coord from server: `screenX = pixelX * sc + offX`

**Bottom sheet:**
- Slides up from the bottom (uses BottomSheetBehavior)
- Peek height: 80dp (buttons always visible)
- Pull up to see full content

Contents of the bottom sheet:
- **Buttons row**: QR (if QR codes found), Retake, Save, Share
- **Inspection Notes card** (blue) — what Gemini observed in one or two sentences
- **Safety Warnings card** (red) — LOTO steps, PPE, arc flash level
- If both catalogue guidance and work zone present → safety warnings shown, not checklist

**VBB overlap check:**
- After overlay is drawn, app checks if the work zone rectangle overlaps with the VBB box
- If overlap: immediate AlertDialog: "Your work zone overlaps with the VBB cubicle — live busbars present"
- Forces engineer to acknowledge before proceeding

**Save button:**
- Calls `ReportGenerator.generate()` which creates a PDF with:
  - Project name, site, inspector, date
  - Annotated photo (photo + overlay drawn onto a Bitmap using Canvas)
  - Panel type and summary
  - Notes
  - Safety warnings
  - Checklist
- PDF saved to device
- Scan record saved to `ScanHistoryStore` (JSON file on device)
- Scan stats updated in SharedPreferences (scan count, warning count, last panel type)
- Opens PDF viewer automatically

---

### Screen 9 — ReportsActivity

What it does:
- Reads all records from `ScanHistoryStore`
- Shows a scrollable list: date, project name, panel type, task
- Tap any record → opens its saved PDF file
- Delete button to remove a record

---

### Screen 10 — LocateVbbActivity / VbbResultActivity

Purpose:
- Dedicated flow specifically for locating the VBB (Vertical Busbar Box) in a PrismaSeT P panel
- The VBB is always live even when the main breaker is OFF — touching it can be fatal
- Engineer photos the closed panel → AI draws a box around the VBB door and marks which side it's on

What the server does in this mode:
- `busbarOnly = true` is sent in the request
- Server runs a separate cubicle segmentation call
- Returns cubicle boxes with one labelled as "vbb"
- Also returns the exact VBB bounding box in pixel coords

VbbResultActivity:
- Shows the photo with the VBB highlighted in orange
- Shows a safety warning: "VBB contains live busbars — never open this cubicle without upstream isolation"

---

### Screen 11 — VerifyPanelActivity

Purpose:
- Before starting work, engineer takes a fresh photo of the panel they are standing in front of
- AI compares it to the reference photo taken during the original risk assessment
- Confirms it is the same panel — prevents working on the wrong switchboard

How it works:
- Shows two photos side by side: "Risk Analysis Photo" and "Your Current Photo"
- Sends both to server: `POST /api/verify_panel`
- Server sends both images to Gemini with instructions to compare them
- Returns: `match: true/false`, `confidence: high/medium/low`, `reason: one sentence`
- App shows result in green (match) or red (mismatch) with the reason

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

### On the Laptop (Server Side)

**SQLite database** — `breaker_data.db`
- Every scan permanently recorded
- Two tables: `projects` and `scans`
- `scans_images/` folder stores the actual JPEG of every scanned panel
- Persists even when server is restarted

**To view the database manually:**
```bash
sqlite3 breaker_data.db
.tables               # shows: projects  scans
SELECT * FROM scans;  # shows all scans
SELECT * FROM projects;
.quit
```

### On the Phone (Android Side)

**SharedPreferences** (`google_api_prefs`) — app settings and stats:
| Key | What it stores |
|-----|----------------|
| `scan_count` | Total scans done |
| `warning_count` | Total safety warnings |
| `last_scan_time_ms` | Timestamp of last scan |
| `last_panel_type` | e.g. "PrismaSeT P" |
| `project_name` | Active project name |
| `site_location` | Site name |
| `inspector_name` | Inspector name |
| `sld_image_path` | File path to uploaded SLD |
| `layout_image_path` | File path to uploaded layout |
| `photo_guide_seen` | Whether onboarding guide was shown |

**SharedPreferences** (`login_prefs`) — session:
| Key | What it stores |
|-----|----------------|
| `is_logged_in` | true/false |
| `username` | logged-in username |

**JSON file** (`scan_history.json` in app's filesDir):
- All scan records saved locally on the phone
- Each record is a `ScanRecord` object:
  - id, dateMs, projectName, siteLocation, inspectorName
  - panelType, panelSummary, notes
  - warnings (list of strings)
  - imagePath (path to the saved JPEG on phone)
  - reportFilePath (path to the saved PDF on phone)
  - busbarOnly, cubicleCount, task

**Image files** (in app's external files directory):
- Every captured photo saved as `yyyyMMdd_HHmmss.jpg`
- Referenced by path in ScanRecord

**PDF files** (in app's external files directory):
- Generated by ReportGenerator for each saved scan
- Referenced by path in ScanRecord

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
| `server.py` | The entire server — FastAPI routes, Gemini integration, safety engine, catalogue engine, database logic |
| `.env` | Contains `GEMINI_KEY=...` — never share or commit this file |
| `breaker_data.db` | SQLite database — auto-created on first run |
| `scans_images/` | Folder where every scanned panel photo is saved |
| `panel_library.json` | Reference data for panel specifications |

### Android App Side
| File | What it does |
|------|-------------|
| `MainActivity.kt` | Home dashboard, server ping, navigation |
| `LoginActivity.kt` | Login screen |
| `ProjectDetailsActivity.kt` | Project/site/inspector form |
| `DocumentsActivity.kt` | SLD and layout upload |
| `TaskSelectionActivity.kt` | Pick commissioning/maintenance/etc. |
| `ScanActivity.kt` | Camera with pinch-to-zoom, quality check |
| `WorkZoneActivity.kt` | Draw work zone, send to server |
| `ResultActivity.kt` | Show annotated photo + warnings + checklist |
| `BoundingBoxOverlay.kt` | Custom view drawing boxes/zones over photo |
| `GoogleStudioDetector.kt` | HTTP client — sends to server, parses JSON response |
| `Detection.kt` | Data model for one breaker (type, box, circuit label, rating) |
| `ScanRecord.kt` | Data model for one saved scan |
| `ScanHistoryStore.kt` | Save/load scan records as JSON on phone |
| `ReportGenerator.kt` | Generates PDF from scan data |
| `ReportsActivity.kt` | Scan history list screen |
| `LocateVbbActivity.kt` | Dedicated VBB hunting flow |
| `VbbResultActivity.kt` | VBB result with highlighted box |
| `VerifyPanelActivity.kt` | AI panel identity verification |
| `PhotoGuideActivity.kt` | First-time onboarding photo guide |
| `ZoneCoords.kt` | Simple data class for zone rectangle |
| `WorkZoneOverlay.kt` | Touch drawing overlay for work zone screen |

---

## Part 6 — Setup

### Requirements
- Python 3.9+ on laptop
- Android phone running Android 7.0+ (API 24)
- Both on the same WiFi network
- Gemini API key — free at ai.google.dev

### Start the Server
```bash
cd /path/to/project_API_google
GEMINI_KEY=your_key_here python3 server.py
```

Output:
```
INFO: Uvicorn running on http://0.0.0.0:8000
Server running at:
  http://localhost:8000
  http://10.x.x.x:8000  ← use this in the Android app
```

### Update IP in Android App
Every time the laptop's WiFi IP changes, update two files then Clean + Rebuild:

`GoogleStudioDetector.kt`:
```kotlin
const val BASE_URL = "http://NEW_IP_HERE:8000/api/analyze"
```

`MainActivity.kt`:
```kotlin
val isOnline = isServerReachable("NEW_IP_HERE", 8000)
```

---

## Part 7 — Features Implemented

### Done
- Login screen with session
- Home dashboard with live server status ping
- Project/site/inspector details
- SLD and mechanical layout upload
- Camera with pinch-to-zoom
- Photo quality check (brightness + blur)
- Work zone drawing on photo
- Safety buffer auto-expansion
- Panel type identification (PrismaSeT G / P / Okken)
- Breaker detection with colour-coded bounding boxes
- Circuit label reading per breaker (e.g. "LV MAIN")
- Current rating reading per breaker (e.g. "400A")
- Labels shown below bounding boxes on result screen
- Busbar side detection (left/right VBB)
- VBB overlap warning when work zone touches live busbar
- Cubicle segmentation (C1, C2, C3 on screen)
- Slide-accurate safety warnings (LOTO / PPE / arc flash)
- ERMS recommendation when MasterPact MTZ detected
- Schneider catalogue checklists (PrismaSeT P/G, Okken, MTZ)
- MasterPact MTZ maintenance checklist with NII/NIII procedure codes
- PDF report generation and saving
- Share PDF report
- Scan history screen
- AI panel identity verification (prevent working on wrong panel)
- VBB locate mode
- Photo onboarding guide
- QR code reading from panel labels
- SQLite database on server (records every scan permanently)
- Scan images saved to `scans_images/` on server

### Planned / Not Yet Done
- Tap a breaker box on result screen to zoom-crop and re-read its label
- SLD cross-check warnings (highlight discrepancies between photo and SLD)
- Pinch-to-zoom on the result screen after scanning
