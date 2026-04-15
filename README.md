# Panel Inspector — FastAPI Server

A local Python server that receives electrical panel images from the Android app, analyses them using the Gemini AI model, and returns breaker detections, panel classification, and safety warnings.

---

## How It Works

```
Android App
    │
    │  POST /api/analyze
    │  { imageBase64, workZone, safetyBuffer, task }
    ▼
FastAPI Server (server.py)
    │
    ├── Gemini AI (vision model)
    │       └── Detects breakers (ACB / MCCB / MCB)
    │           Returns bounding boxes, panel type, notes
    │
    ├── Expert System (experta)
    │       └── Classifies panel type:
    │           ACB + >4 drawers  → Okken
    │           ACB + ≤4 drawers  → PrismaSeT P
    │           No ACB            → PrismaSeT G
    │
    └── Safety Engine
            └── Generates safety warnings based on:
                - Panel type (Prisma G / Prisma P / Okken)
                - Work zone position (TOP / MIDDLE / BOTTOM)
                - Busbar location
                - MTZ / ERMS presence
    │
    │  JSON response
    │  { breakers, panel_type, notes, safety_warnings, ... }
    ▼
Android App (draws bounding boxes + shows warnings)
```

---

## API Endpoint

### `POST /api/analyze`

**Request body:**
```json
{
  "imageBase64": "<base64 encoded JPEG>",
  "mimeType": "image/jpeg",
  "identifyOnly": false,
  "task": "others",
  "workZone": { "ymin": 100, "xmin": 100, "ymax": 800, "xmax": 900 },
  "safetyBuffer": { "ymin": 80, "xmin": 80, "ymax": 820, "xmax": 920 }
}
```

**Response:**
```json
{
  "breakers": [
    { "type": "ACB", "box": [ymin, xmin, ymax, xmax] },
    { "type": "MCB", "box": [ymin, xmin, ymax, xmax] }
  ],
  "panel_type": "PrismaSeT G",
  "notes": "3 breakers detected in work zone.",
  "safety_warnings": [
    "🔒 LOTO on incomer supply from the TOP.",
    "🦺 PPE Cat 1 required."
  ],
  "busbar_side": "TOP",
  "cubicle_count": 3
}
```

**Breaker types:**
| Type | Description |
|------|-------------|
| ACB  | Air Circuit Breaker — large, high current (630A+) |
| MCCB | Moulded Case Circuit Breaker — medium current (up to 630A) |
| MCB  | Miniature Circuit Breaker — small, low current (up to 125A) |

**Panel types:**
| Type | Classification Rule |
|------|---------------------|
| PrismaSeT G | No ACB detected |
| PrismaSeT P | ACB present, ≤4 cubicles |
| Okken | ACB present, >4 cubicles |

---

## Setup

### 1. Install dependencies
```bash
pip install fastapi uvicorn google-genai pillow numpy opencv-python experta anthropic
```

### 2. Set environment variables
```bash
cp .env.example .env
# Edit .env and add your keys
export GEMINI_KEY=your_gemini_api_key_here
```
Get a free Gemini API key at [ai.google.dev](https://ai.google.dev)

### 3. Run the server
```bash
python3 server.py
```

The server prints your local IP at startup:
```
Server running at:
  http://localhost:8000
  http://10.x.x.x:8000  ← use this in the Android app
```

### 4. Switch AI provider (optional)
In `server.py`, change the `PROVIDER` variable:
```python
PROVIDER = "gemini"      # default — Google Gemini
PROVIDER = "claude"      # Anthropic Claude
PROVIDER = "vertexai"    # Google Vertex AI
```

---

## Project Structure

```
server.py          — Main FastAPI server + AI logic + safety engine
panel_library.json — Panel catalogue data
requirements.txt   — Python dependencies
.env.example       — Environment variable template
```
