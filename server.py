"""
Local FastAPI server — mirrors /api/analyze endpoint the Android app expects.
Run:  python server.py
Then point the app to http://<your-mac-ip>:8000
"""

import base64
import io
import json
import re
import socket
import numpy as np
import cv2
# pyzbar removed — using OpenCV's built-in QR detector (no system library needed)
from PIL import Image
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
from experta import *

# --- Expert system for panel classification ---
class PanelFact(Fact):
    acb     = int
    mccb    = int
    mcb     = int
    drawers = int

class PanelClassifier(KnowledgeEngine):
    result = "Unknown"

    @Rule(PanelFact(acb=P(lambda x: x >= 1), drawers=P(lambda x: x > 4)))
    def okken(self):
        self.result = "Okken"

    @Rule(PanelFact(acb=P(lambda x: x >= 1), drawers=P(lambda x: x <= 4)))
    def prisma_p(self):
        self.result = "PrismaSeT P"

    @Rule(PanelFact(acb=0))
    def prisma_g(self):
        self.result = "PrismaSeT G"

def classify_panel(acb: int, mccb: int, mcb: int, drawers: int = 0) -> str:
    engine = PanelClassifier()
    engine.reset()
    engine.declare(PanelFact(acb=acb, mccb=mccb, mcb=mcb, drawers=drawers))
    engine.run()
    return engine.result
# ----------------------------------------------

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
GEMINI_KEY    = os.environ.get("GEMINI_KEY", "")

# Switch: "claude", "gemini", or "vertexai"
PROVIDER = "gemini"

VERTEX_PROJECT  = "project-dca768bf-132b-488c-8f2"
VERTEX_LOCATION = "us-central1"

if PROVIDER == "claude":
    import anthropic as _anthropic
    MODEL  = "claude-opus-4-6"
    client = _anthropic.Anthropic(api_key=ANTHROPIC_KEY)
elif PROVIDER == "vertexai":
    from google import genai as _genai
    from google.genai import types as _types
    MODEL  = "gemini-3.1-pro-preview"
    client = _genai.Client(vertexai=True, project=VERTEX_PROJECT, location=VERTEX_LOCATION)
else:
    from google import genai as _genai
    from google.genai import types as _types
    MODEL  = "gemini-3.1-pro-preview"
    client = _genai.Client(api_key=GEMINI_KEY)

app = FastAPI(title="Breaker Detection API", version="1.0.0")

# --- User credentials store ---
USERS = {
    "santosh":  "schneider123",
    "admin":    "admin123",
    "techuser": "tech2026",
}

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/login")
def login(body: LoginRequest):
    user = USERS.get(body.username.lower().strip())
    if user and user == body.password:
        return JSONResponse(content={"success": True, "message": "Login successful"})
    return JSONResponse(status_code=401, content={"success": False, "message": "Invalid username or password"})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Zone coords: [ymin, xmin, ymax, xmax] normalized 0-1000 ---
class Zone(BaseModel):
    ymin: int
    xmin: int
    ymax: int
    xmax: int

# --- Request body ---
class AnalyzeRequest(BaseModel):
    imageBase64: str
    mimeType: str = "image/jpeg"
    workZone: Optional[Zone] = None
    safetyBuffer: Optional[Zone] = None
    identifyOnly: bool = False
    busbarOnly: bool = False
    sldBase64: Optional[str] = None       # optional SLD diagram upload
    layoutBase64: Optional[str] = None    # optional mechanical layout upload
    task: str = "others"                  # commissioning | maintenance | modification | replacement | others


def _official_panel_summary(panel_type: str) -> str:
    """Returns official Schneider Electric definition for each panel type."""
    pt = panel_type.lower()
    if "prismaset g" in pt or "prisma g" in pt:
        return (
            "Panel building system for switchboards up to 630A, IEC 61439-1&2 compliant. "
            "Modular fixed-mounted distribution board using Linergy busbar system (up to 630A / 50kA). "
            "Integrates MCCBs (Compact NS/NSX) and MCBs (Acti9/iC60). IP30, 1230×600×250mm standard enclosure."
        )
    if "prismaset p" in pt or "prisma p" in pt:
        return (
            "Power switchboard system up to 4000A with Vertical Busbar Box (VBB) compartment. "
            "Supports withdrawable MasterPact ACBs and fixed Compact NS/NSX MCCBs. "
            "IEC 61439-1&2 compliant. VBB remains LIVE at all times — even with main breaker OFF."
        )
    if "okken" in pt:
        return (
            "High-power MV/LV switchboard for currents up to 6300A. "
            "Draw-out withdrawable cubicle design with horizontal busbar system (HBB) at the top. "
            "Supports MasterPact MTZ ACBs. IEC 61439-1&2 compliant. HBB remains LIVE during intervention."
        )
    return ""


def catalogue_knowledge(panel_type: str, task: str) -> str:
    """
    Returns Schneider Electric catalogue knowledge for the detected panel type.
    This is injected into the Gemini prompt so answers are catalogue-accurate.
    """
    pt = panel_type.lower().strip()
    t  = task.lower().strip()

    # ── PrismaSeT P ──────────────────────────────────────────────────────────
    if "prismaset p" in pt or "prisma p" in pt:
        base = (
            "\n\n--- SCHNEIDER ELECTRIC CATALOGUE REFERENCE: PrismaSeT P ---\n"
            "PHYSICAL LAYOUT:\n"
            "  - Total width: 800mm (650mm functional section + 150mm VBB section)\n"
            "  - 650mm FUNCTIONAL SECTION: contains incoming ACB (MasterPact NT or NW) + outgoing MCCBs (Compact NSX/NS)\n"
            "  - 150mm VBB SECTION (Vertical Busbar Box): contains vertical bus bars — plain blank door, no devices\n"
            "  - Height: 2000mm standard. Depth: 400mm or 600mm\n"
            "  - IP30/IP31/IP55. IEC 61439-1&2 compliant\n\n"
            "DEVICES SUPPORTED:\n"
            "  - Incoming ACB: MasterPact NT (up to 1600A) or MasterPact NW (up to 4000A) — withdrawable/draw-out\n"
            "  - Outgoing MCCB: Compact NSX (16A–630A) or Compact NS (100A–630A) — fixed mount\n"
            "  - Final MCB: Acti9, iC60, Multi9 (up to 125A)\n"
            "  - Max system current: 4000A\n\n"
            "CRITICAL SAFETY — VBB:\n"
            "  - VBB (150mm section) bus bars are ALWAYS LIVE even when main ACB breaker is switched OFF\n"
            "  - VBB door must NEVER be opened without full upstream isolation (utility supply disconnected)\n"
            "  - VBB is identified by a narrow plain blank door — no handles, no devices, no vents\n"
            "  - V-shaped hinge brackets visible at top and bottom of VBB door edge\n"
            "  - Form of separation: Form 2b, 3b or 4b depending on configuration\n\n"
            "BUS BAR SPECS:\n"
            "  - Linergy busbar system inside VBB\n"
            "  - Ratings: 1000A, 1600A, 2500A, 4000A\n"
            "  - Short circuit withstand: up to 100kA\n"
            "  - Phase arrangement: L1/L2/L3/N (top to bottom or front to back)\n\n"
        )
        if t == "commissioning":
            return base + (
                "COMMISSIONING CHECKLIST (from catalogue):\n"
                "  1. Verify panel type label matches order — PrismaSeT P\n"
                "  2. Confirm VBB compartment present — left or right side\n"
                "  3. Verify ACB rating matches SLD (800A/1250A/1600A/2500A/4000A)\n"
                "  4. Check all MCCB ratings match SLD\n"
                "  5. Verify phase labelling L1/L2/L3/N on all busbars\n"
                "  6. Check all breaker handles move freely — no mechanical damage\n"
                "  7. Verify all cable connections torqued to spec\n"
                "  8. Confirm all blanking plates fitted — no open slots\n"
                "  9. Check earthing connections\n"
                "  10. Do NOT open VBB door until upstream is fully isolated\n"
            )
        if t == "maintenance":
            return base + (
                "MAINTENANCE CHECKLIST (from catalogue):\n"
                "  1. Inspect ACB cradle and draw-out mechanism for wear\n"
                "  2. Check ACB trip unit settings match protection coordination\n"
                "  3. Inspect all MCCB handles — any tripped or intermediate position?\n"
                "  4. Check VBB door seal and condition — any heat discolouration?\n"
                "  5. Inspect bus bar joints for corrosion, overheating, loose connections\n"
                "  6. Check cable connections for heat marks or insulation damage\n"
                "  7. Clean ventilation grilles\n"
                "  8. Verify all labels legible\n"
                "  9. Recommended interval: every 12 months or per site maintenance plan\n"
                "  10. VBB bus bars remain live during maintenance — full isolation required before opening\n"
            )
        if t == "modification":
            return base + (
                "MODIFICATION GUIDANCE (from catalogue):\n"
                "  1. Check available spare slots in 650mm functional section\n"
                "  2. Verify bus bar capacity for additional load (check current rating vs existing load)\n"
                "  3. VBB compartment CANNOT be modified — it is a fixed system\n"
                "  4. New MCCBs must be Compact NSX/NS compatible with PrismaSeT P chassis\n"
                "  5. Any modification requires full panel isolation — VBB remains live until upstream isolated\n"
                "  6. After modification: re-verify all ratings, labels and connections\n"
                "  7. Update SLD to reflect changes\n"
            )
        if t == "replacement":
            return base + (
                "REPLACEMENT GUIDANCE (from catalogue):\n"
                "  1. Identify exact device: MasterPact NT/NW (ACB) or Compact NSX/NS (MCCB) or Acti9/iC60 (MCB)\n"
                "  2. Note current rating from label on device face\n"
                "  3. ACB replacement: use draw-out mechanism — slide out on cradle — do NOT force\n"
                "  4. MCCB replacement: de-energise and isolate that circuit first\n"
                "  5. NEVER replace any device with VBB energised\n"
                "  6. Replacement device must match: type, rating, breaking capacity, frame size\n"
                "  7. After replacement: test operation before re-energising\n"
                "  8. Isolation steps: Open ACB → Lock out → Verify dead with tester → Then work\n"
            )
        return base

    # ── PrismaSeT G ──────────────────────────────────────────────────────────
    if "prismaset g" in pt or "prisma g" in pt:
        base = (
            "\n\n--- SCHNEIDER ELECTRIC CATALOGUE REFERENCE: PrismaSeT G ---\n"
            "PHYSICAL LAYOUT:\n"
            "  - Modular fixed-mounted distribution board\n"
            "  - No ACB, no VBB compartment — all devices are fixed mount\n"
            "  - Standard enclosure: 1230 x 600 x 250mm\n"
            "  - Available widths: 300mm, 400mm, 650mm, 800mm\n"
            "  - IP30/IP31/IP55. IEC 61439-1&2 compliant\n\n"
            "DEVICES SUPPORTED:\n"
            "  - MCCB: Compact NSX (16A–630A) or Compact NS (100A–630A) — fixed mount, bolted to busbar\n"
            "  - MCB: Acti9, iC60, Multi9 (up to 125A) — modular, DIN rail mounted\n"
            "  - Switch disconnectors: INS/INV series\n"
            "  - Max system current: 630A\n"
            "  - No incoming ACB — fed directly from upstream source\n\n"
            "BUS BAR SPECS:\n"
            "  - Linergy busbar system — internal horizontal busbars\n"
            "  - Ratings: up to 630A / 50kA short circuit withstand\n"
            "  - Phase arrangement: L1/L2/L3/N\n"
            "  - Busbars are internal — not a separate compartment like PrismaSeT P VBB\n\n"
            "SAFETY:\n"
            "  - Internal busbars become de-energised when upstream source is isolated\n"
            "  - No permanently live compartment like VBB — safer for maintenance\n"
            "  - Always verify dead with voltage tester before working inside\n\n"
        )
        if t == "commissioning":
            return base + (
                "COMMISSIONING CHECKLIST (from catalogue):\n"
                "  1. Verify panel type — PrismaSeT G (no ACB, no VBB)\n"
                "  2. Check all MCCB ratings match SLD\n"
                "  3. Verify MCB grouping and circuit labelling\n"
                "  4. Check all busbar connections torqued to spec\n"
                "  5. Verify phase labelling L1/L2/L3/N\n"
                "  6. Confirm all blanking plates fitted — no open slots\n"
                "  7. Check earthing connections\n"
                "  8. Test each MCCB operates freely\n"
                "  9. Verify incoming supply rating matches board capacity (max 630A)\n"
            )
        if t == "maintenance":
            return base + (
                "MAINTENANCE CHECKLIST (from catalogue):\n"
                "  1. Check all MCCB handles — any tripped or intermediate position?\n"
                "  2. Inspect busbar connections for heat marks or corrosion\n"
                "  3. Check MCB condition — any signs of overheating or discolouration?\n"
                "  4. Verify all cable connections tight\n"
                "  5. Check all labels legible\n"
                "  6. Clean dust from ventilation openings\n"
                "  7. Recommended interval: every 12 months\n"
                "  8. Isolate upstream supply before opening panel\n"
            )
        if t == "modification":
            return base + (
                "MODIFICATION GUIDANCE (from catalogue):\n"
                "  1. Check spare DIN rail space for new MCBs\n"
                "  2. Check spare mounting space for new MCCBs\n"
                "  3. Verify busbar capacity — max 630A total\n"
                "  4. New devices must be Compact NSX/NS or Acti9/iC60 compatible\n"
                "  5. Isolate full board before any modification\n"
                "  6. Update circuit labels and SLD after modification\n"
            )
        if t == "replacement":
            return base + (
                "REPLACEMENT GUIDANCE (from catalogue):\n"
                "  1. Identify exact device: Compact NSX/NS (MCCB) or Acti9/iC60 (MCB)\n"
                "  2. Note rating from label on device face\n"
                "  3. Isolate upstream supply — verify dead with tester\n"
                "  4. MCCBs are bolted — remove busbar connections before unbolting\n"
                "  5. MCBs are DIN rail clip-on — slide off rail\n"
                "  6. Replacement must match: type, rating, breaking capacity\n"
                "  7. After replacement: test before re-energising\n"
            )
        return base

    # ── Okken ────────────────────────────────────────────────────────────────
    if "okken" in pt:
        base = (
            "\n\n--- SCHNEIDER ELECTRIC CATALOGUE REFERENCE: Okken ---\n"
            "PHYSICAL LAYOUT:\n"
            "  - Draw-out withdrawable cubicle design\n"
            "  - DOUBLE hinged doors per section — two doors side by side per cubicle\n"
            "  - Horizontal Bus Bar (HBB) runs at the TOP of the switchboard\n"
            "  - Cubicle widths: 350mm, 450mm or 650mm\n"
            "  - Height: 2200–2350mm. Depth: 600–1400mm\n"
            "  - IP30/IP31/IP54. IEC 61439-1&2 compliant. Form 4b separation\n\n"
            "DEVICES SUPPORTED:\n"
            "  - Incoming ACB: MasterPact MTZ (up to 6300A) or MasterPact NW — draw-out withdrawable\n"
            "  - Outgoing MCCB: Compact NS630b/1600 — fixed or draw-out\n"
            "  - Motor control units in dedicated cubicles\n"
            "  - Max system current: 7300A\n\n"
            "CRITICAL SAFETY — HBB:\n"
            "  - HBB (Horizontal Bus Bar) at top is ALWAYS LIVE even when all breakers are OFF\n"
            "  - HBB compartment must NEVER be accessed without full upstream isolation\n"
            "  - Draw-out mechanism: ACB slides out on cradle — connected/test/disconnected positions\n"
            "  - Form 4b: full compartmentalisation — devices fully isolated from busbars when withdrawn\n\n"
            "BUS BAR SPECS:\n"
            "  - Horizontal busbar system at top of switchboard\n"
            "  - Ratings: up to 7300A\n"
            "  - Short circuit withstand: up to 150kA\n"
            "  - Distance between busbar axes: 115mm (cubicle 115) or 70mm (cubicle 70)\n\n"
        )
        if t == "commissioning":
            return base + (
                "COMMISSIONING CHECKLIST (from catalogue):\n"
                "  1. Verify panel type — Okken (double doors, HBB at top)\n"
                "  2. Check all ACB ratings match SLD\n"
                "  3. Verify draw-out mechanism operates correctly (connected/test/disconnected)\n"
                "  4. Check HBB connections and phase order L1/L2/L3\n"
                "  5. Verify all cubicle door seals and locking mechanisms\n"
                "  6. Check earthing of all cubicles\n"
                "  7. Verify all labels and circuit identification\n"
                "  8. Test ACB trip unit settings\n"
                "  9. HBB is LIVE from day one — never access without upstream isolation\n"
            )
        if t == "maintenance":
            return base + (
                "MAINTENANCE CHECKLIST (from catalogue):\n"
                "  1. Check draw-out cradle mechanism for wear or damage\n"
                "  2. Inspect ACB trip unit and operating mechanism\n"
                "  3. Check HBB connections for overheating or corrosion\n"
                "  4. Inspect double door hinges and locking mechanism\n"
                "  5. Check arc chute condition on ACBs\n"
                "  6. Verify thermal monitoring readings (Okken has built-in thermal monitoring)\n"
                "  7. Recommended interval: every 12 months for ACBs\n"
                "  8. HBB remains live during maintenance — full isolation required\n"
            )
        if t == "modification":
            return base + (
                "MODIFICATION GUIDANCE (from catalogue):\n"
                "  1. Check spare cubicles available\n"
                "  2. Verify HBB capacity for additional load\n"
                "  3. New cubicles must be Okken-compatible — cannot mix with other panel types\n"
                "  4. Full panel isolation required before any busbar work\n"
                "  5. Draw-out units can be added without full panel shutdown if Form 4b\n"
                "  6. Update SLD and panel schedule after modification\n"
            )
        if t == "replacement":
            return base + (
                "REPLACEMENT GUIDANCE (from catalogue):\n"
                "  1. Identify exact ACB: MasterPact MTZ or NW — note rating from nameplate\n"
                "  2. Use draw-out mechanism — move to DISCONNECTED position first\n"
                "  3. Verify ACB is fully disconnected before withdrawing from cradle\n"
                "  4. Never withdraw a live ACB — always move to disconnected position first\n"
                "  5. Replacement ACB must match: rating, frame size, trip unit type\n"
                "  6. After replacement: test in TEST position before moving to CONNECTED\n"
                "  7. HBB remains live throughout — only cradle/ACB is isolated by draw-out\n"
            )
        return base

    return ""  # unknown panel type


def build_prompt(work_zone: Optional[Zone], safety_buffer: Optional[Zone], task: str = "others") -> str:
    breaker_rules = (
        "PANEL IDENTIFICATION — follow this checklist IN ORDER:\n\n"
        "CHECK 1 — Okken: Is the panel very large, dark grey/charcoal, with DOUBLE hinged doors per section?\n"
        "  Each section has two doors side by side. Very heavy industrial floor cabinet.\n"
        "  → YES = Okken.\n\n"
        "CHECK 2 — MasterPact ACB present? Look for a large DRAW-OUT unit as the main incomer:\n"
        "  MasterPact = slides out on a cradle/chassis, big operating handle or rotary knob on front face,\n"
        "  trip/reset button, current rating (800A/1250A/1600A/2500A), 'MasterPact' / 'MTZ' / 'NT' label.\n"
        "  It is MUCH taller and wider than any MCCB. Takes up the full height of its cubicle.\n"
        "  → YES = PrismaSeT P.\n\n"
        "CHECK 3 — VBB compartment present? Look for a NARROW completely blank door on one side:\n"
        "  Plain grey metal door, no handles, no breakers, no labels, no cutouts — totally blank.\n"
        "  Visibly narrower than the breaker cubicle doors beside it.\n"
        "  → YES = PrismaSeT P.\n\n"
        "CHECK 4 — Only MCCBs and MCBs, all similar-sized doors, no blank side compartment?\n"
        "  All breakers are compact fixed-mount MCCBs (Compact NS/NSX) and small MCBs. No large draw-out unit.\n"
        "  → YES = PrismaSeT G.\n\n"
        "CRITICAL: Only classify as PrismaSeT G if you are certain there is NO ACB and NO VBB.\n"
        "Set panel_type to exactly one of: PrismaSeT G, PrismaSeT P, Okken.\n\n"
        "BUSBAR COMPARTMENT DETECTION (PrismaSeT P only):\n"
        "PrismaSeT P has a dedicated 150mm busbar compartment on one side (left or right).\n"
        "This compartment has a NARROW BLANK SOLID DOOR with NO visible breakers, switches, or devices.\n"
        "The main functional section (650mm) has visible MCCBs, MCBs, and wiring.\n"
        "Look at the panel: which side has a narrow blank/plain door with nothing visible on it?\n"
        "  - If the LEFT side has a plain blank narrow door → busbar_side = 'left'\n"
        "  - If the RIGHT side has a plain blank narrow door → busbar_side = 'right'\n"
        "  - If you cannot determine it clearly → busbar_side = 'unknown'\n"
        "For PrismaSeT G and Okken → busbar_side = 'unknown'\n\n"
        "BREAKER CLASSIFICATION RULES (Schneider Electric) — use the SPECIFIC PRODUCT NAME as the label:\n"
        "- MasterPact MTZ or MasterPact NT: Very large and bulky ACB, typically 630A or above. "
        "Has visible arc chambers, large front face, heavy construction. Label as 'MasterPact MTZ' or 'MasterPact NT'.\n"
        "- Compact NSX or Compact NS: Medium-sized MCCB, rectangular molded plastic body, 16A–630A. "
        "Wider than MCBs, solid rectangular shape. Label as 'Compact NSX' or 'Compact NS'.\n"
        "- Acti9, iC60, or Multi9: Small and slim MCB, modular, up to 125A. "
        "Usually arranged in a row of identical thin units. Label as 'Acti9', 'iC60', or 'Multi9'.\n"
        "If you cannot distinguish between MTZ and NT, use 'MasterPact'. "
        "If you cannot distinguish between NSX and NS, use 'Compact NSX'. "
        "If you cannot distinguish between Acti9/iC60/Multi9, use 'Acti9'.\n"
        "IMPORTANT: Do NOT label cable ducts, busbars, terminals, contactors, meters, or enclosure parts as breakers. "
        "Only label actual circuit breakers.\n"
    )

    _has_task = task.lower().strip() not in ("others", "")

    if work_zone and safety_buffer:
        notes_instruction = (
            "" if _has_task
            else f"5. In notes, write one sentence summarising the breakers found in the work zone.\n"
        )
        return (
            f"You are an electrical panel safety inspector analyzing a Schneider Electric panel.\n\n"
            f"{breaker_rules}\n"
            f"The user has drawn a WORK ZONE on this image. Coordinates are normalized to 0-1000:\n"
            f"  Work Zone     (green box): ymin={work_zone.ymin}, xmin={work_zone.xmin}, ymax={work_zone.ymax}, xmax={work_zone.xmax}\n"
            f"  Safety Buffer (red  box):  ymin={safety_buffer.ymin}, xmin={safety_buffer.xmin}, ymax={safety_buffer.ymax}, xmax={safety_buffer.xmax}\n\n"
            f"STRICT INSTRUCTIONS:\n"
            f"1. ONLY detect circuit breakers INSIDE the Safety Buffer zone. Ignore everything outside.\n"
            f"2. Classify each breaker strictly as ACB, MCCB, or MCB using the rules above.\n"
            f"3. Return bounding boxes [ymin, xmin, ymax, xmax] normalized to 0-1000.\n"
            f"4. Check the Safety Buffer for hazards: Main Disconnects, HV switches, exposed busbars. Add to safety_warnings.\n"
            f"{notes_instruction}"
        )
    else:
        notes_instruction = (
            "" if _has_task
            else "In notes, write one sentence summarising what you found."
        )
        return (
            f"You are an electrical panel safety inspector analyzing a Schneider Electric panel.\n\n"
            f"{breaker_rules}\n"
            f"1. Identify the panel type and set panel_type.\n"
            f"2. Write a one-sentence panel_summary.\n"
            f"3. Detect ALL circuit breakers visible in this image. "
            f"Return bounding boxes [ymin, xmin, ymax, xmax] normalized to 0-1000. "
            f"List any visible safety hazards in safety_warnings. "
            f"{notes_instruction}"
        )




def task_prompt(task: str) -> str:
    """Returns additional task-specific instructions appended to the main prompt."""
    t = task.lower().strip()

    if t == "commissioning":
        return (
            "\n\nTASK: COMMISSIONING (First-Time Setup)\n"
            "This is a brand-new installation. Your job is to verify the panel is correctly set up:\n"
            "1. Confirm the panel type (PrismaSeT G / PrismaSeT P / Okken).\n"
            "2. Count ALL breakers visible. In notes, list each type and quantity (e.g. '2x MasterPact, 4x Compact NSX').\n"
            "3. For PrismaSeT P: confirm the 150mm VBB (bus bar compartment) is present on left or right.\n"
            "4. For Okken: confirm the horizontal bus bar section is visible at the top.\n"
            "5. Check if breaker labels/ratings are visible and readable — flag missing labels in safety_warnings.\n"
            "6. Check phase labelling (L1/L2/L3) is present — flag if missing.\n"
            "7. Flag any open slots, missing covers, or exposed terminals as safety_warnings.\n"
            "CRITICAL SAFETY: The bus bar (VBB/HBB) is ALWAYS LIVE even with the main breaker OFF. "
            "Always add this to safety_warnings: 'VBB/HBB bus bar remains energised even with main breaker OFF — treat as live.'"
        )

    if t == "maintenance":
        return (
            "\n\nTASK: MAINTENANCE (Routine Inspection)\n"
            "This is a routine condition check. Look for signs of wear, damage, or deterioration:\n"
            "1. Identify the panel type and all breakers present.\n"
            "2. Look for visible signs of overheating: discolouration, burn marks, melted plastic — flag in safety_warnings.\n"
            "3. Check bus bar joints and connections — any corrosion, loose connections or heat discolouration? Flag in safety_warnings.\n"
            "4. Check breaker handles — are any in a tripped or intermediate position? Flag those.\n"
            "5. Note any physical damage: cracked covers, missing panels, broken handles.\n"
            "6. In notes, give an overall condition summary (e.g. 'Panel in good condition' or 'Signs of heat at MCCB row 2').\n"
            "CRITICAL SAFETY: The bus bar (VBB/HBB) is ALWAYS LIVE. "
            "Always add: 'Do not open bus bar compartment without full isolation and PTW (Permit To Work).'"
        )

    if t == "modification":
        return (
            "\n\nTASK: MODIFICATION (Adding or Changing Equipment)\n"
            "The worker plans to add or change equipment. Help them understand what is already installed:\n"
            "1. Identify the panel type.\n"
            "2. Count all existing breakers and list them with positions (left to right, top to bottom).\n"
            "3. Identify any EMPTY slots or spare space in the cubicles — note in notes.\n"
            "4. For PrismaSeT P: confirm VBB compartment position (left/right) — this CANNOT be modified.\n"
            "5. For Okken: note how many cubicles are used and if any are spare.\n"
            "6. Flag in safety_warnings: any work near bus bars requires full isolation.\n"
            "7. In notes, summarise what exists and where space is available for new equipment.\n"
            "CRITICAL SAFETY: Always add: "
            "'Bus bar compartment (VBB/HBB) is LIVE — new equipment must only be installed with full panel isolation and PTW.'"
        )

    if t == "replacement":
        return (
            "\n\nTASK: REPLACEMENT (Replacing a Breaker or Component)\n"
            "The worker needs to replace a specific component. Identify it precisely:\n"
            "1. Identify the panel type.\n"
            "2. For each breaker detected: provide the EXACT Schneider product name, current rating (if visible on label), "
            "and position in the panel (e.g. 'top-left', 'row 2 position 3').\n"
            "3. If a rating or part number is visible on the breaker face, include it in notes.\n"
            "4. In notes, list each breaker with: Type | Rating | Position — so the worker can order the exact replacement.\n"
            "5. Flag in safety_warnings: the circuit must be fully de-energised and isolated before replacement.\n"
            "6. For PrismaSeT P: if replacing an MCCB, confirm VBB bus bar side — worker must not open VBB.\n"
            "CRITICAL SAFETY: Always add: "
            "'Isolate and lock-out the circuit before removing any breaker. Verify dead with a voltage tester.'"
        )

    if t == "testing":
        return (
            "\n\nTASK: TESTING (Post-Installation or Post-Repair Testing)\n"
            "The worker is testing the panel after installation or repair. Verify it is ready to energise:\n"
            "1. Identify the panel type (PrismaSeT G / P / Okken).\n"
            "2. Check all breakers are in the OFF position before energising — flag any that are ON.\n"
            "3. For PrismaSeT P: confirm VBB compartment door is fully closed and latched.\n"
            "4. For Okken: confirm all cubicle doors are closed and ACBs are in DISCONNECTED or TEST position.\n"
            "5. Check all blanking plates are fitted — no open slots visible.\n"
            "6. Check all cable connections are made — no loose wires or exposed terminals.\n"
            "7. Verify all breaker labels and circuit identifications are visible and correct.\n"
            "8. In notes, confirm whether the panel appears ready for testing or list what needs to be fixed first.\n"
            "CRITICAL SAFETY: Always add: "
            "'VBB/HBB bus bar is LIVE as soon as upstream supply is connected — never open bus bar compartment during testing. "
            "Test each circuit individually. Verify each breaker trips correctly before declaring the panel safe.'"
        )

    # default: others / general
    return (
        "\n\nTASK: GENERAL SCAN\n"
        "Perform a standard inspection:\n"
        "1. Identify the panel type.\n"
        "2. Detect all breakers with bounding boxes.\n"
        "3. List any visible safety hazards in safety_warnings.\n"
        "4. In notes, write one sentence summarising what you found."
    )


def location_safety_prompt(work_zone: Optional[Zone]) -> str:
    """
    Returns location-aware LOTO/PPE/ERMS/arc-flash guidance injected into the Gemini prompt.
    Based on manager's PPT slides 5 (PrismaSeT G), 7+9 (PrismaSeT P), 11 (Okken).
    Guidance is specific to work zone vertical position (top / middle / bottom).
    """
    if not work_zone:
        return ""

    zone_cy = (work_zone.ymin + work_zone.ymax) / 2

    if zone_cy < 350:
        position      = "TOP"
        position_desc = "near the incomer / top cable connections"
    elif zone_cy > 650:
        position      = "BOTTOM"
        position_desc = "near the bottom cable entry / outgoing cables"
    else:
        position      = "MIDDLE"
        position_desc = "in the feeder / breaker zone"

    # ── PrismaSeT G zone guidance (Slide 5) ──────────────────────────────────
    if position == "TOP":
        g_zone = (
            "  ZONE = TOP (incomer cable connections):\n"
            "    - Dead work: no risk FAR from cable lugs — moderate electric shock + arc flash risk CLOSE to cable lugs.\n"
            "    - If live work is necessary: NSX100–250 = very low arc flash; NSX400–630 = moderate arc flash → PPE Cat 1 (arc flash face shield + Class 0 insulating gloves).\n"
            "    - Always stay away from bare cable lugs — maintain minimum 30mm clearance.\n"
        )
    elif position == "MIDDLE":
        g_zone = (
            "  ZONE = MIDDLE (feeder MCCBs):\n"
            "    - Isolate each outgoing MCCB individually before touching downstream side.\n"
            "    - Arc flash risk from NSX feeders: low to moderate — PPE gloves + safety glasses minimum.\n"
            "    - Risk of dropping tools onto energised cables below — use insulated tools.\n"
        )
    else:  # BOTTOM
        g_zone = (
            "  ZONE = BOTTOM (outgoing cable entry):\n"
            "    - Risk of dropping parts onto energised cable connections — ensure lower cable ends are insulated or dead.\n"
            "    - Moderate electric shock risk near cable lugs — PPE Class 0 gloves + safety glasses.\n"
            "    - Confirm upstream MCCB is OFF and locked before touching any conductor at bottom.\n"
        )

    # ── PrismaSeT P zone guidance (Slides 7 + 9) ─────────────────────────────
    if position == "TOP":
        p_zone = (
            "  ZONE = TOP (main ACB cable connections + top of VBB):\n"
            "    - HIGHEST electric shock risk — main incomer cable lugs at top are live until full upstream isolation.\n"
            "    - Arc flash risk is HIGH near ACB top connections and VBB busbar top.\n"
            "    - If MasterPact MTZ is detected as incomer: propose ERMS (Energy Reduction Maintenance Setting) activation before ANY live work near the incomer.\n"
            "    - ERMS reduces arcing energy significantly — remind worker to activate from the display or EcoStruxure tool.\n"
            "    - PPE: Arc flash face shield rated for panel incident energy + Class 1 or 2 insulating gloves.\n"
        )
    elif position == "MIDDLE":
        p_zone = (
            "  ZONE = MIDDLE (feeder MCCBs — busbar-fed devices):\n"
            "    - Electric shock risk EVERYWHERE inside the panel.\n"
            "    - Arc flash risk near the VBB side — keep tools away from the VBB compartment.\n"
            "    - Arc flash risk on busbar-fed MCCBs — these are directly connected to live VBB busbars.\n"
            "    - If MasterPact MTZ detected: propose ERMS activation before live work.\n"
            "    - PPE: Arc flash face shield + Class 1 insulating gloves minimum.\n"
        )
    else:  # BOTTOM
        p_zone = (
            "  ZONE = BOTTOM (outgoing cable entry / bottom cable connections):\n"
            "    - Electric shock risk near bottom cable lugs.\n"
            "    - Risk of dropping conductive parts onto energised cable connections below.\n"
            "    - Ensure all outgoing cable ends at bottom are isolated or insulated.\n"
            "    - PPE: Class 0 insulating gloves + arc flash safety glasses minimum.\n"
        )

    # ── Okken zone guidance (Slide 11) ───────────────────────────────────────
    if position == "TOP":
        o_zone = (
            "  ZONE = TOP (Horizontal Busbar — HBB):\n"
            "    - CRITICAL: HBB at the top is ALWAYS LIVE — even with all ACBs open.\n"
            "    - Do NOT access the HBB compartment under any circumstances without full upstream isolation.\n"
            "    - If MasterPact MTZ detected: ERMS activation is MANDATORY before any live intervention near the HBB.\n"
            "    - Arc flash risk = VERY HIGH near HBB (high current, low impedance).\n"
            "    - PPE: minimum PPE Cat 2 (arc flash suit + face shield + Class 2 gloves) — may require Cat 3–4 depending on system rating.\n"
        )
    elif position == "MIDDLE":
        o_zone = (
            "  ZONE = MIDDLE (ACB / feeder cubicles):\n"
            "    - Move ACB to DISCONNECTED position before opening cubicle door.\n"
            "    - Electric shock risk depends on Form type (Form 4b = compartmentalised, safer).\n"
            "    - Arc flash risk on busbar-fed devices — HBB above is still live.\n"
            "    - If MasterPact MTZ detected: propose ERMS activation before live work.\n"
            "    - PPE: Arc flash face shield + Class 1 insulating gloves minimum.\n"
        )
    else:  # BOTTOM
        o_zone = (
            "  ZONE = BOTTOM (cable entry / outgoing connections):\n"
            "    - Ensure outgoing cables are de-energised and insulated before work.\n"
            "    - Risk of dropping conductive parts onto live cable below.\n"
            "    - Electric shock risk near cable lugs — PPE Class 0 gloves + safety glasses.\n"
        )

    return (
        f"\n\n=== LOCATION-AWARE SAFETY ASSESSMENT ==="
        f"\nThe worker's WORK ZONE is in the {position} of the panel ({position_desc}).\n"
        f"Based on the panel type you identified above, apply the relevant guidance below:\n\n"

        f"--- IF PrismaSeT G (Slide 5 guidance) ---\n"
        f"  LOTO: Lock Out Tag Out on upstream supply side (incomer switch/MCCB). Check for any local energy sources (generator, UPS, capacitor bank). Use Voltage Absence Tester (VAT) to verify dead.\n"
        f"{g_zone}\n"

        f"--- IF PrismaSeT P (Slides 7+9 guidance) ---\n"
        f"  LOTO: Lock Out Tag Out on ALL supply sides (including PV inverters, generators, UPS). VBB busbars remain LIVE until full upstream supply is isolated — apply padlock on incomer.\n"
        f"{p_zone}\n"

        f"--- IF Okken or large PrismaSeT P (Slide 11 guidance) ---\n"
        f"  LOTO: Lock Out Tag Out ALL supply sides — Okken may have multiple incomers. Check ALL energy sources before declaring dead.\n"
        f"{o_zone}\n"

        f"REQUIRED OUTPUT — add to safety_warnings (specific, not generic):\n"
        f"  1. LOTO instruction specific to this panel type and zone\n"
        f"  2. PPE recommendation specific to this zone and detected incomer type\n"
        f"  3. ERMS prompt if MasterPact MTZ ACB is detected in the image (PrismaSeT P or Okken only)\n"
        f"  4. Arc flash warning specific to this zone (HIGH near busbar top, MODERATE near feeders, LOW near outgoing cables)\n"
        f"  5. Any additional hazard specific to this exact zone + panel combination\n"
        f"Each warning must be concrete and specific — NOT generic. Reference the actual panel type and zone.\n"
    )


def generate_safety_assessment(panel_type: str, work_zone: Optional[Zone], breakers: list,
                               panel_ymin: Optional[float] = None, panel_ymax: Optional[float] = None,
                               vbb_cubicle: Optional[dict] = None, cubicle_count: int = 0,
                               safety_buffer: Optional[Zone] = None) -> list:
    """
    Concise, slide-accurate safety warnings — only the key point for the detected zone.
    Slides 5 (PrismaSeT G), 7+9 (PrismaSeT P), 11 (Okken).
    Zone position is relative to detected panel content (not photo edges) — handles zoomed-in photos.
    """
    if not work_zone:
        return []

    pt      = panel_type.lower()
    zone_cy = (work_zone.ymin + work_zone.ymax) / 2

    # Compute position relative to panel content
    # Only use relative calculation if breakers span a meaningful range (>200 units out of 1000)
    # If only 1-2 breakers detected in a small cluster, fall back to raw Y axis
    if panel_ymin is not None and panel_ymax is not None and (panel_ymax - panel_ymin) > 200:
        panel_range = panel_ymax - panel_ymin
        relative_cy = (zone_cy - panel_ymin) / panel_range
        print(f"[ZONE] relative mode: panel_ymin={panel_ymin:.0f} panel_ymax={panel_ymax:.0f} zone_cy={zone_cy:.0f} relative={relative_cy:.2f}")
        if relative_cy < 0.40:
            position = "TOP"
        elif relative_cy > 0.60:
            position = "BOTTOM"
        else:
            position = "MIDDLE"
    else:
        # Fallback: raw image Y axis (0-1000)
        print(f"[ZONE] raw Y mode: zone_cy={zone_cy:.0f} (panel range too small or no breakers)")
        if zone_cy < 400:
            position = "TOP"
        elif zone_cy > 600:
            position = "BOTTOM"
        else:
            position = "MIDDLE"
    print(f"[ZONE] → position={position}")

    has_mtz = any(
        any(k in b.get("type", "").lower() for k in ["mtz", "masterpact", "masterPact", "acb"])
        for b in breakers
    )

    # ── PrismaSeT G — Slide 5 ────────────────────────────────────────────────
    if "prismaset g" in pt or "prisma g" in pt:
        if position == "TOP":
            # Slide 5: LOTO on incomer supply from the top
            # "No risk far from cable / Electric shock + Arc Flash CLOSE to incoming cable"
            return [
                "🔒 LOTO on incomer supply from the TOP. VAT check + confirm no local sources (PV).",
                "⚡ No risk if far from incoming cable. Electric shock + Arc Flash risk CLOSE to incoming cable.",
                "🦺 NSX100–250 → very low arc flash. NSX400–630 → moderate arc flash. PPE Cat 1.",
            ]
        elif position == "MIDDLE":
            # Slide 5: feeder zone — live work, electric shock everywhere, arc flash from feeders
            return [
                "🔒 LOTO on supply side. VAT check + confirm no local sources (PV).",
                "⚡ Electric shock risk everywhere (Form 0, no terminal shield). Isolate each feeder individually.",
                "🦺 NSX feeder arc flash: NSX100–250 → very low. NSX400–630 → moderate. PPE Cat 1.",
            ]
        else:  # BOTTOM
            # Slide 5: LOTO on incomer from bottom — risk of dropping parts
            return [
                "🔒 LOTO on incomer supply from the BOTTOM (if it exists). VAT check + confirm no local sources.",
                "⚠️ Risk of dropping parts wherever the working zone is — secure all tools before starting.",
                "🦺 Electric shock risk everywhere (Form 0) → PPE Cat 1.",
            ]

    # ── PrismaSeT P — Slides 6+7 (small ≤1000kVA) or Slides 10+11 (large >1000kVA) ──
    elif "prismaset p" in pt or "prisma p" in pt:
        # >4 cubicles = large PrismaSeT P (multiple transformers) → slide 10 warnings
        # ≤4 cubicles = small PrismaSeT P ≤1000kVA → slide 7 warnings
        is_large = cubicle_count > 4
        print(f"[PRISMA P] cubicle_count={cubicle_count} → {'LARGE (slide 10)' if is_large else 'SMALL (slide 7)'}")
        if is_large:
            # Slide 10+11: Large PrismaSeT P — multiple supplies, arc flash HIGH
            if position == "TOP":
                w = [
                    "Arc flash risk is Significant",
                    "🔒 Dead work: LOTO on ALL supply side(s). VAT check. Check LOTO on ALL sources (multiple supplies very likely).",
                    "⚡ No risk far from incoming circuit — electric shock + Arc Flash risk CLOSE to incoming circuit(s).",
                    "🦺 PPE: Arc flash risk HIGH — select PPE according to Arc Flash risk.",
                ]
            elif position == "MIDDLE":
                w = [
                    "Arc flash risk is Significant",
                    "🔒 Dead work: LOTO on ALL supply side(s). VAT check. Check LOTO on ALL sources (multiple supplies very likely).",
                    "⚡ Live work: Electric shock risk depends on panel Form. Arc flash close to BB. Arc flash on devices directly supplied by busbar (NS>630 or MasterPact).",
                    "🦺 PPE shall be selected according to Arc Flash risk.",
                ]
            else:
                w = [
                    "Arc flash risk is Significant",
                    "🔒 Dead work: LOTO on ALL supply side(s). VAT check. Check LOTO on ALL sources (multiple supplies very likely).",
                    "⚠️ Risk of dropping parts wherever the working zone is — secure all tools and components.",
                    "🦺 PPE: Arc flash risk HIGH — select PPE according to Arc Flash risk.",
                ]
            if vbb_cubicle:
                vbb_box = vbb_cubicle.get("box", [])
                check_zone = safety_buffer or work_zone
                if len(vbb_box) >= 4 and check_zone:
                    if (check_zone.xmin < vbb_box[3] and check_zone.xmax > vbb_box[1] and
                            check_zone.ymin < vbb_box[2] and check_zone.ymax > vbb_box[0]):
                        w.append("⚡ Work zone overlaps VBB — Arc flash risk CLOSE to BB. VBB busbars ALWAYS LIVE.")
            if has_mtz:
                w.append("🔧 ERMS: MTZ detected as incomer — activate ERMS before any live work to reduce arc flash energy.")
            return w
        if position == "TOP":
            # Slide 7: LOTO on incomer from top
            # "No risk far from incoming cable / Electric shock + Arc Flash risk close to incoming cable"
            w = [
                "Arc flash risk is Significant",
                "🔒 Dead work: LOTO on incomer supply from the TOP. VAT check. Check presence of local sources (PV).",
                "⚡ No risk far from incoming cable — electric shock + Arc Flash risk CLOSE to incoming cable.",
                "🦺 PPE: Select PPE according to Arc Flash risk level near incomer.",
            ]
        elif position == "MIDDLE":
            # Slide 7: feeder/busbar zone
            # "Electric shock everywhere / Arc flash close to BB / Arc flash on busbar-fed devices"
            w = [
                "Arc flash risk is Significant",
                "🔒 Dead work: LOTO on supply side. VAT check. Check presence of local sources (PV).",
                "⚡ Live work: Electric shock risk everywhere (Form 0). Arc flash close to BB. Arc flash on devices directly supplied by busbar (NS>630 or MasterPact).",
                "🦺 PPE shall be selected according to Arc Flash risk.",
            ]
        else:  # BOTTOM
            # Slide 7: LOTO on incomer from bottom
            # "Risk of dropping parts wherever is the working zone"
            w = [
                "Arc flash risk is Significant",
                "🔒 Dead work: LOTO on incomer supply from the BOTTOM. VAT check. Check presence of local sources (PV).",
                "⚠️ Risk of dropping parts wherever the working zone is — secure all tools and components.",
                "🦺 PPE: Select PPE according to Arc Flash risk.",
            ]
        # VBB overlap check — use safety buffer (wider zone) to catch proximity to VBB (Slide 7)
        if vbb_cubicle:
            vbb_box = vbb_cubicle.get("box", [])  # [ymin, xmin, ymax, xmax] in 0-1000
            check_zone = safety_buffer or work_zone  # prefer safety buffer — it's wider
            if len(vbb_box) >= 4 and check_zone:
                vbb_overlaps = (check_zone.xmin < vbb_box[3] and check_zone.xmax > vbb_box[1] and
                                check_zone.ymin < vbb_box[2] and check_zone.ymax > vbb_box[0])
                if vbb_overlaps:
                    w.append("⚡ Work zone overlaps VBB — Arc flash risk CLOSE to BB. VBB busbars are ALWAYS LIVE even with main ACB OFF.")
        if has_mtz:
            w.append("🔧 ERMS: MTZ detected as incomer — activate ERMS before any live work to reduce arc flash energy.")
        return w

    # ── Okken / Large PrismaSeT P — Slides 10 + 11 ──────────────────────────
    # Applies to: Okken AND large PrismaSeT P (>1000kVA or multiple transformers)
    elif "okken" in pt:
        if position == "TOP":
            # Slide 11: LOTO on incomer(s) from top
            # "No risk far from incoming circuit / Electric shock + Arc Flash risk close to incoming circuit"
            w = [
                "Arc flash risk is HIGH",
                "🔒 Dead work: LOTO on ALL supply side(s). VAT check. Check LOTO on ALL sources (multiple supplies very likely)",
                "⚡ No risk far from incoming circuit — electric shock + Arc Flash risk CLOSE to incoming circuit(s).",
                "🦺 PPE: Arc flash risk HIGH — select PPE according to Arc Flash risk.",
            ]
        elif position == "MIDDLE":
            # Slide 11: feeder/busbar zone
            # "Electric shock depends on form / Arc flash close to BB / Arc flash on busbar-fed devices"
            w = [
                "Arc flash risk is HIGH",
                "🔒 Dead work: LOTO on ALL supply side(s). VAT check. Check LOTO on ALL sources (multiple supplies very likely).",
                "⚡ Live work: Electric shock risk depends on panel Form. Arc flash close to BB. Arc flash on devices directly supplied by busbar (NS>630 or MasterPact).",
                "🦺 PPE shall be selected according to Arc Flash risk.",
            ]
        else:  # BOTTOM
            # Slide 11: LOTO on incomer from bottom
            # "Risk of dropping parts wherever is the working zone"
            w = [
                "Arc flash risk is HIGH",
                "🔒 Dead work: LOTO on ALL supply side(s). VAT check. Check LOTO on ALL sources (multiple supplies very likely).",
                "⚠️ Risk of dropping parts wherever the working zone is — secure all tools and components.",
                "🦺 PPE: Arc flash risk HIGH — select PPE according to Arc Flash risk.",
            ]
        if has_mtz:
            w.append("🔧 ERMS: MTZ detected as incomer — activate ERMS before any live work to reduce arc flash energy.")
        return w

    return []


def inside_zone(box: list[int], zone: Zone) -> bool:
    """Returns True if the breaker box overlaps with the zone (not just center)."""
    ymin, xmin, ymax, xmax = box
    return not (xmax < zone.xmin or xmin > zone.xmax or ymax < zone.ymin or ymin > zone.ymax)


@app.get("/")
def root():
    return {"status": "running", "model": MODEL}


@app.get("/health")
def health():
    return {"status": "healthy"}


def identify_panel_only(image_b64: str, mime_type: str) -> dict:
    """Just identify the panel type — no bounding boxes."""
    prompt = (
        "You are a Schneider Electric panel expert. Study this panel image carefully before classifying.\n\n"

        "VISUAL CHECKLIST — answer each question mentally before deciding:\n\n"

        "Q1. Is this panel very large, dark grey/charcoal, with DOUBLE hinged doors per section?\n"
        "    Each section has TWO doors side by side that open outward. Floor-standing industrial cabinet.\n"
        "    → YES = Okken. STOP.\n\n"

        "Q2. Do you see a MasterPact ACB (Air Circuit Breaker) as the main incomer?\n"
        "    MasterPact looks like: a large DRAW-OUT unit (it slides out on a cradle/chassis),\n"
        "    front face has a big operating handle or rotary knob, trip/reset button, current rating label\n"
        "    (e.g. 800A, 1250A, 1600A, 2500A). It occupies a FULL cubicle height and is much wider/taller\n"
        "    than any MCCB. You may also see 'MasterPact' or 'MTZ' or 'NT' printed on it.\n"
        "    → YES = PrismaSeT P. STOP.\n\n"

        "Q3. Do you see a NARROW blank door on the LEFT or RIGHT side of the panel?\n"
        "    This VBB door is completely plain grey metal — no handles, no breakers, no labels, no cutouts.\n"
        "    It is visibly NARROWER (roughly half the width) compared to the breaker cubicle doors next to it.\n"
        "    → YES = PrismaSeT P. STOP.\n\n"

        "Q4. Are ALL breakers compact MCCBs / MCBs (Compact NS, NSX, INS) with NO large draw-out unit?\n"
        "    MCCBs are fixed-mount, smaller (typically 100-630A), bolted directly to busbar.\n"
        "    All cubicle doors are similar width. No blank side compartment.\n"
        "    → YES = PrismaSeT G.\n\n"

        "IMPORTANT: If you see a large main breaker that could be an ACB, classify as PrismaSeT P — "
        "do NOT call it PrismaSeT G unless you are certain there is absolutely no ACB and no VBB.\n\n"

        "Return ONLY valid JSON:\n"
        '{"panel_type": "PrismaSeT P", "panel_summary": "describe the key feature you used to identify it"}'
    )
    from pydantic import BaseModel as _BM
    class _PanelResult(_BM):
        panel_type: str
        panel_summary: str
    response = client.models.generate_content(
        model=MODEL,
        contents=[{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            {"text": prompt}
        ]}],
        config=_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_PanelResult,
            temperature=0.0,
        ),
    )
    return json.loads(response.text)


def _enhance_for_busbar(image_b64: str) -> str:
    """
    Pre-process image before sending to Gemini:
    - Downscale to 768px max — cubicles are large features, don't need full res
    - CLAHE contrast enhancement → makes frame boundaries visible
    - Sharpen → crisp vertical edges
    Returns new base64 string of enhanced image.
    """
    img_bytes = base64.b64decode(image_b64)
    img_pil   = Image.open(io.BytesIO(img_bytes)).convert("RGB")

    # Downscale to 1024px max side — better detail for cubicle boundary detection
    max_side = 1024
    w, h = img_pil.size
    scale = min(max_side / w, max_side / h, 1.0)
    if scale < 1.0:
        img_pil = img_pil.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    img_np    = np.array(img_pil)

    # Convert to LAB — apply CLAHE only on L (lightness) channel
    lab   = cv2.cvtColor(img_np, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    l_eq  = clahe.apply(l)
    lab_eq = cv2.merge([l_eq, a, b])
    enhanced = cv2.cvtColor(lab_eq, cv2.COLOR_LAB2RGB)

    # Sharpen to make vertical frame edges crisper
    kernel   = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(enhanced, -1, kernel)

    # Encode back to base64
    _, buf = cv2.imencode(".jpg", cv2.cvtColor(sharpened, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return base64.b64encode(buf).decode("utf-8")


def identify_cubicles_generic(image_b64: str, mime_type: str) -> dict:
    """
    Generic cubicle detection — no VBB bias.
    Works for ALL panel types (Okken, PrismaSeT G, PrismaSeT P).
    Just detects physical cubicle boundaries and labels each section.
    """
    image_b64 = _enhance_for_busbar(image_b64)
    prompt = (
        "You are a Schneider Electric panel expert.\n\n"
        "YOUR TASK — scan this electrical panel from LEFT to RIGHT.\n"
        "Identify every individual CUBICLE (vertical section with its own door or frame boundary).\n\n"
        "CUBICLE TYPES:\n"
        "  - 'breaker': door with visible ACB / MCCBs / MCBs / breaker handles\n"
        "  - 'cable':   large closed door, cable entry glands, may have emergency stop or display\n"
        "  - 'vbb':     NARROW plain blank door, no devices at all (PrismaSeT P only)\n\n"
        "BOUNDING BOX RULES — CRITICAL:\n"
        "  1. ALL cubicles share the SAME ymin and ymax — the top and bottom of the panel frame.\n"
        "  2. xmin and xmax define each cubicle's LEFT and RIGHT door edges — vary per cubicle.\n"
        "  3. The panel does NOT always fill the full image — there is often background/wall visible.\n"
        "     STOP all boxes at the actual metal panel frame edge, NOT at the image edge (0 or 1000).\n"
        "  4. Adjacent cubicles share a boundary — xmax of cubicle N = xmin of cubicle N+1.\n"
        "  5. Do NOT extend any box to x=0 or x=1000 unless the panel truly starts/ends at the image edge.\n\n"
        "COUNTING RULES:\n"
        "  - Do NOT invent cubicles that do not exist\n"
        "  - Do NOT split one cubicle into two\n"
        "  - Do NOT merge two cubicles into one\n"
        "  - Count only what you actually see\n\n"
        "Draw tight bounding boxes [ymin, xmin, ymax, xmax] normalized 0-1000.\n"
        "Return ONLY valid JSON."
    )
    from pydantic import BaseModel as _BM
    class _Cubicle(_BM):
        position: int
        label: str
        box: list[int]
    class _CubicleResult(_BM):
        cubicle_count: int
        cubicles: list[_Cubicle]
        cubicle_summary: str
    response = client.models.generate_content(
        model=MODEL,
        contents=[{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            {"text": prompt}
        ]}],
        config=_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_CubicleResult,
            temperature=0.0,
        ),
    )
    return json.loads(response.text)


def identify_busbar_only(image_b64: str, mime_type: str) -> dict:
    """Detect every cubicle segment in the panel and return a bounding box for each one."""
    # Enhance image contrast/sharpness before sending to Gemini
    image_b64 = _enhance_for_busbar(image_b64)

    prompt = (
        "You are a Schneider Electric PrismaSeT P panel expert.\n\n"
        "CRITICAL RULE — PrismaSeT P ALWAYS has a VBB (Vertical Busbar Box) compartment:\n"
        "  - ALWAYS a SEPARATE cubicle on the FAR LEFT or FAR RIGHT of the panel\n"
        "  - ALWAYS significantly NARROWER than other cubicles (about 150mm–300mm)\n"
        "  - Door is ALWAYS plain/blank — NO handles, NO vents, NO devices visible\n"
        "  - NEVER merge VBB with adjacent section — they are TWO separate cubicles\n\n"
        "CUBICLE TYPES — you MUST set the correct label for each:\n"
        "  - 'vbb':    NARROW blank plain door, no devices, always on far left or far right\n"
        "  - 'breaker': door with visible ACB / MCCBs / MCBs / breaker handles inside\n"
        "  - 'cable':  large closed door, cable entry glands at bottom, may have emergency stop button or display panel\n\n"
        "PANEL BOUNDARY RULE:\n"
        "  - The panel may NOT fill the full image width\n"
        "  - Stop ALL cubicle boxes at the actual metal panel frame edge\n"
        "  - Do NOT extend boxes to the image edge if the panel ends before it\n\n"
        "CRITICAL — ONE DOOR = ONE CUBICLE:\n"
        "  - Each cubicle is a FULL-HEIGHT vertical section with its own door\n"
        "  - ONE door = ONE cubicle, regardless of how many devices are inside it\n"
        "  - Horizontal rows of breakers INSIDE one door are NOT separate cubicles\n"
        "  - DO NOT split a single door into multiple cubicles\n"
        "  - DO NOT merge the VBB door with the adjacent breaker door — they are always separate\n\n"
        "VBB DETECTION — CHECK THE FAR EDGES CAREFULLY:\n"
        "  - Look at the FAR LEFT edge of the panel — is there a narrow blank door? → VBB\n"
        "  - Look at the FAR RIGHT edge of the panel — is there a narrow blank door? → VBB\n"
        "  - The VBB door has NO handles, NO breakers, NO vents — completely plain grey metal\n"
        "  - It is NARROWER than all other doors — do NOT let the adjacent breaker box swallow it\n"
        "  - V-SHAPED HINGES: PrismaSeT P VBB doors have distinctive V-shaped or triangular hinges\n"
        "    visible at the TOP and BOTTOM of the door edge. If you see V-shaped hinge brackets at the\n"
        "    far left or far right edge of the panel — that is the VBB door boundary, even if the door\n"
        "    itself is mostly out of frame. Draw the VBB cubicle box from that hinge position to the panel edge.\n"
        "  - If the last breaker box extends to the panel edge, STOP and check if there is a narrow VBB door at that edge\n"
        "  - If VBB is partially out of frame, still create a cubicle box for it at the visible edge\n\n"
        "YOUR TASK — scan LEFT to RIGHT:\n"
        "  1. Find the actual LEFT and RIGHT edges of the panel metal frame\n"
        "  2. Check BOTH far edges for a narrow blank VBB door\n"
        "  3. Count full-height DOORS — each door is one cubicle\n"
        "  4. Label: VBB = 'vbb', doors with breakers = 'breaker', cable/display doors = 'cable'\n"
        "  5. Bounding boxes [ymin, xmin, ymax, xmax] normalized 0-1000, no overlaps\n\n"
        "EXAMPLE — 4-cubicle PrismaSeT P where position 1 is VBB on left:\n"
        '{"cubicle_count": 4, "cubicles": ['
        '{"position": 1, "label": "vbb",     "box": [0, 20,  1000, 170]}, '
        '{"position": 2, "label": "cable",   "box": [0, 170, 1000, 450]}, '
        '{"position": 3, "label": "breaker", "box": [0, 450, 1000, 750]}, '
        '{"position": 4, "label": "breaker", "box": [0, 750, 1000, 950]}'
        '], "cubicle_summary": "one sentence"}\n\n'
        "Return ONLY valid JSON."
    )
    from pydantic import BaseModel as _BM
    class _Cubicle(_BM):
        position: int
        label: str   # "vbb", "breaker", or "cable"
        box: list[int]
    class _CubicleResult(_BM):
        cubicle_count: int
        cubicles: list[_Cubicle]
        cubicle_summary: str
    response = client.models.generate_content(
        model=MODEL,
        contents=[{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            {"text": prompt}
        ]}],
        config=_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_CubicleResult,
            temperature=0.0,   # deterministic — same image always gives same count
        ),
    )
    return json.loads(response.text)


import time as _time

def _gemini_with_retry(call_fn, retries=3, delays=(3, 6, 10)):
    """Call a Gemini function with automatic retry on 503 / overload errors."""
    for attempt in range(retries):
        try:
            return call_fn()
        except Exception as e:
            msg = str(e)
            is_retryable = "503" in msg or "UNAVAILABLE" in msg or "overload" in msg.lower()
            if is_retryable and attempt < retries - 1:
                wait = delays[attempt]
                print(f"[RETRY] Gemini 503 — waiting {wait}s (attempt {attempt+1}/{retries})")
                _time.sleep(wait)
            else:
                raise


@app.post("/api/analyze")
def analyze(body: AnalyzeRequest):
    # Decode image
    img_bytes = base64.b64decode(body.imageBase64)
    img       = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h      = img.size

    print(f"[DEBUG] Image size: {w}x{h}")
    print(f"[DEBUG] task:        {body.task}")
    print(f"[DEBUG] identifyOnly: {body.identifyOnly}")
    print(f"[DEBUG] workZone:    {body.workZone}")
    print(f"[DEBUG] safetyBuffer: {body.safetyBuffer}")

    # --- Busbar-only mode: detect all cubicle segments, draw a box around each one ---
    if body.busbarOnly:
        result = identify_busbar_only(body.imageBase64, body.mimeType)
        img_bytes = base64.b64decode(body.imageBase64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        w, h = img.size

        # Convert all cubicle boxes from 0-1000 → pixel coords
        cubicles_px = []
        for c in result.get("cubicles", []):
            box = c.get("box", [])
            if len(box) < 4:
                continue
            cubicles_px.append({
                "position": c.get("position"),
                "box": [
                    int(box[0] / 1000 * h),
                    int(box[1] / 1000 * w),
                    int(box[2] / 1000 * h),
                    int(box[3] / 1000 * w),
                ]
            })

        print(f"[CUBICLES] count={result.get('cubicle_count')} | {result.get('cubicle_summary')}")
        return JSONResponse(content={
            "breakers":        [],
            "notes":           result.get("cubicle_summary", ""),
            "safety_warnings": [],
            "panel_type":      "",
            "panel_summary":   result.get("cubicle_summary", ""),
            "busbar_side":     "unknown",
            "cubicle_count":   result.get("cubicle_count", 0),
            "cubicles":        cubicles_px,
        })

    # --- Identify-only mode: just return panel type + 1 line ---
    if body.identifyOnly:
        result     = identify_panel_only(body.imageBase64, body.mimeType)
        panel_type = result.get("panel_type", "Unknown")
        print(f"[PANEL] {panel_type} — {result.get('panel_summary')}")
        return JSONResponse(content={
            "breakers": [],
            "notes": "",
            "safety_warnings": [],
            "panel_type":    panel_type,
            "panel_summary": "",
        })

    from concurrent.futures import ThreadPoolExecutor

    # ONE call — inject all 3 catalogues, Gemini picks the right one after identifying panel
    all_catalogue = (
        catalogue_knowledge("PrismaSeT P", body.task)
        + catalogue_knowledge("PrismaSeT G", body.task)
        + catalogue_knowledge("Okken", body.task)
    )

    # Build prompt with task + full catalogue + location safety — single Gemini call does everything
    prompt = (
        build_prompt(body.workZone, body.safetyBuffer, body.task)
        + task_prompt(body.task)
        + all_catalogue
        + location_safety_prompt(body.workZone)
    )

    # Start cubicle detection in parallel with main detection
    _executor       = ThreadPoolExecutor(max_workers=2)
    _cubicle_future = _executor.submit(identify_cubicles_generic, body.imageBase64, body.mimeType) if body.workZone else None

    json_schema = (
        "\n\nRespond with ONLY valid JSON, no markdown, no explanation. Use this exact structure:\n"
        '{"breakers": [{"type": "ACB|MCCB|MCB", "quantity": 1, '
        '"box": [ymin, xmin, ymax, xmax]}], '
        '"notes": "one sentence", "safety_warnings": ["hazard1"]}\n'
        "IMPORTANT for box coordinates (all normalized 0-1000):\n"
        "  box[0] = ymin  (top edge,    0=top    of image)\n"
        "  box[1] = xmin  (left edge,   0=left   of image)\n"
        "  box[2] = ymax  (bottom edge, 1000=bottom of image)\n"
        "  box[3] = xmax  (right edge,  1000=right  of image)\n"
        "Draw a TIGHT bounding box around each breaker body only. Do not include surrounding wires or labels."
    )

    if PROVIDER == "claude":
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": body.mimeType,
                            "data": body.imageBase64,
                        },
                    },
                    {"type": "text", "text": prompt + json_schema},
                ],
            }],
        )
        raw = response.content[0].text
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
    else:
        from pydantic import BaseModel as _BM, Field as _F
        class _Breaker(_BM):
            type: str = _F(description="Exact Schneider product name: MasterPact MTZ, MasterPact NT, MasterPact, Compact NSX, Compact NS, Acti9, iC60, or Multi9")
            box: list[int] = _F(description="[ymin, xmin, ymax, xmax] normalized 0-1000. ONE entry per individual breaker unit.")
        class _DetectionResult(_BM):
            breakers: list[_Breaker] = _F(description="One entry per individual breaker. Do NOT group multiple breakers into one entry.")
            panel_type: str = _F(description="Exactly one of: PrismaSeT G, PrismaSeT P, Okken")
            busbar_side: str = _F(description=(
                "Only for PrismaSeT P: identify which side has the 150mm busbar compartment. "
                "Look for the side with a BLANK solid metal door/panel with NO visible breakers — that is the busbar compartment. "
                "The breaker side has visible MCCBs and MCBs. "
                "Return 'left', 'right', or 'unknown'. "
                "For PrismaSeT G and Okken return 'unknown'."
            ))
            notes: str
            safety_warnings: list[str]
        gemini_prompt = (
            prompt +
            "\nCRITICAL: Return ONE separate entry in 'breakers' for EACH individual breaker unit you see. "
            "If you see 8 MCBs, return 8 separate entries each with their own tight bounding box. "
            "Do NOT group them. Each box must tightly fit around one single breaker body."
        )
        # Build parts — add SLD and layout if provided
        parts = []
        if body.sldBase64:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": body.sldBase64}})
            parts.append({"text": "Above is the Single Line Diagram (SLD) of this panel. Use it to understand the circuit layout, breaker ratings, and connections."})
        if body.layoutBase64:
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": body.layoutBase64}})
            parts.append({"text": "Above is the Mechanical Layout / Geometry Alignment diagram. Use it to understand the physical cubicle arrangement and dimensions."})
        parts.append({"inline_data": {"mime_type": body.mimeType, "data": body.imageBase64}})
        parts.append({"text": gemini_prompt})

        response = _gemini_with_retry(lambda: client.models.generate_content(
            model=MODEL,
            contents=[{"parts": parts}],
            config=_types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_DetectionResult,
            ),
        ))
        data = json.loads(response.text)

    print(f"[GEMINI RAW] {json.dumps(data)[:500]}")

    # Capture raw 0-1000 breaker Y range BEFORE pixel conversion
    # Used to compute work zone position relative to panel content (not photo edges)
    raw_breaker_boxes = [b.get("box", []) for b in data.get("breakers", []) if len(b.get("box", [])) >= 4]
    panel_ymin_raw = min((b[0] for b in raw_breaker_boxes), default=None)
    panel_ymax_raw = max((b[2] for b in raw_breaker_boxes), default=None)

    # Filter breakers by zone + convert 0-1000 → pixel coords
    filtered_breakers = []
    for b in data.get("breakers", []):
        box = b.get("box", [])
        if len(box) < 4:
            continue
        ymin, xmin, ymax, xmax = box[0], box[1], box[2], box[3]

        # Drop anything whose center is outside the safety buffer
        if body.safetyBuffer and not inside_zone([ymin, xmin, ymax, xmax], body.safetyBuffer):
            continue

        b["box"] = [
            int(ymin / 1000 * h),
            int(xmin / 1000 * w),
            int(ymax / 1000 * h),
            int(xmax / 1000 * w),
        ]
        filtered_breakers.append(b)

    data["breakers"] = filtered_breakers

    # Use panel type returned by the single Gemini call
    panel_type    = data.get("panel_type", "Unknown")
    panel_summary = data.get("panel_summary", "")
    data["panel_type"]    = panel_type
    data["panel_summary"] = _official_panel_summary(panel_type) or panel_summary or f"Schneider Electric {panel_type} panel"
    print(f"[PANEL] {panel_type}")
    print(f"[BUSBAR] side={data.get('busbar_side', 'unknown')}")

    # Catalogue guidance — only shown when NO work zone (general scan)
    # When work zone is drawn, slide-based safety assessment replaces catalogue text
    if not body.workZone:
        cat = catalogue_knowledge(panel_type, body.task)
        if cat:
            data["catalogue_guidance"] = cat.strip()
            print(f"[CATALOGUE] injected for {panel_type} / {body.task}")
    else:
        data["catalogue_guidance"] = ""

    data["qr_codes"] = []

    # --- Integrate Busbar ID into Analyze Zone (parallel) ---
    cubicles_px  = []
    cubicle_line = ""
    if body.workZone:
        try:
            detected_panel = data.get("panel_type", "").strip()

            # Helper functions — defined here so all panel branches can use them
            def _label_desc(c):
                lbl = c.get("label", "breaker")
                if lbl == "vbb":   return "VBB compartment ⚡"
                if lbl == "cable": return "cable compartment 🔌"
                return "breaker section"

            def _build_cubicles_px(raw):
                # All cubicles share the same panel top/bottom — use median ymin/ymax
                # to avoid outliers (Gemini sometimes extends one cubicle to image edge)
                valid_boxes = [c.get("box", []) for c in raw if len(c.get("box", [])) >= 4]
                if len(valid_boxes) >= 2:
                    ymins = sorted(b[0] for b in valid_boxes)
                    ymaxs = sorted(b[2] for b in valid_boxes)
                    panel_ymin = ymins[len(ymins) // 2]   # median top
                    panel_ymax = ymaxs[len(ymaxs) // 2]   # median bottom
                    if panel_ymax > panel_ymin:
                        for c in raw:
                            b = c.get("box", [])
                            if len(b) >= 4:
                                b[0] = panel_ymin
                                b[2] = panel_ymax
                result = []
                for c in raw:
                    box = c.get("box", [])
                    if len(box) < 4:
                        continue
                    result.append({
                        "position": c.get("position"),
                        "label":    c.get("label", "breaker"),
                        "box": [
                            int(box[0] / 1000 * h),
                            int(box[1] / 1000 * w),
                            int(box[2] / 1000 * h),
                            int(box[3] / 1000 * w),
                        ]
                    })
                return result

            def _build_cubicle_line(raw, include_vbb=True):
                wz_cx     = (body.workZone.xmin + body.workZone.xmax) / 2
                working_c = next((c for c in raw if len(c.get("box",[])) >= 4 and c["box"][1] <= wz_cx <= c["box"][3]), None)
                vbb_c     = next((c for c in raw if c.get("is_vbb") or c.get("label") == "vbb"), None) if include_vbb else None
                parts     = []
                if working_c:
                    wz_pos = working_c.get("position", "?")
                    parts.append(f"Working in Cubicle {wz_pos} ({_label_desc(working_c)}).")
                    left_c  = next((c for c in raw if c.get("position") == wz_pos - 1), None)
                    right_c = next((c for c in raw if c.get("position") == wz_pos + 1), None)
                    if left_c:
                        parts.append(f"Cubicle {left_c.get('position')} immediately to your LEFT is a {_label_desc(left_c)}.")
                    if right_c:
                        parts.append(f"Cubicle {right_c.get('position')} immediately to your RIGHT is a {_label_desc(right_c)}.")
                    if vbb_c:
                        vbb_pos   = vbb_c.get("position", 0)
                        proximity = "immediately " if abs(vbb_pos - wz_pos) == 1 else ""
                        direction = "to your LEFT" if vbb_pos < wz_pos else "to your RIGHT"
                        parts.append(f"⚠ VBB (Cubicle {vbb_pos}) is {proximity}{direction} — live busbars present, do NOT drill or penetrate.")
                return " ".join(parts)

            # Okken — detect cubicles, no VBB, add HBB message
            if "okken" in detected_panel.lower():
                cubicle_result        = _cubicle_future.result(timeout=120)
                raw_cubicles          = cubicle_result.get("cubicles", [])
                cubicles_px           = _build_cubicles_px(raw_cubicles)
                base_line             = _build_cubicle_line(raw_cubicles, include_vbb=False)
                data["cubicle_count"] = cubicle_result.get("cubicle_count", 0)
                data["cubicles"]      = cubicles_px
                data["cubicle_line"]  = (
                    f"{base_line} "
                    f"⚠ Okken panel — Horizontal Busbar (HBB) runs at the TOP "
                    f"and BEHIND the panel. Keep clear of the top section during intervention."
                ).strip()
                sw = generate_safety_assessment(panel_type, body.workZone, data.get("breakers", []), panel_ymin_raw, panel_ymax_raw)
                if sw: data["safety_warnings"] = sw
                _executor.shutdown(wait=False)
                return JSONResponse(content=data)

            # PrismaSeT G — detect cubicles
            if "prismaset g" in detected_panel.lower() or "prisma g" in detected_panel.lower():
                cubicle_result        = _cubicle_future.result(timeout=120)
                raw_cubicles          = cubicle_result.get("cubicles", [])
                cubicles_px           = _build_cubicles_px(raw_cubicles)
                data["cubicle_count"] = cubicle_result.get("cubicle_count", 0)
                data["cubicles"]      = cubicles_px
                data["cubicle_line"]  = _build_cubicle_line(raw_cubicles, include_vbb=False)
                sw = generate_safety_assessment(panel_type, body.workZone, data.get("breakers", []), panel_ymin_raw, panel_ymax_raw)
                if sw: data["safety_warnings"] = sw
                _executor.shutdown(wait=False)
                return JSONResponse(content=data)

            # PrismaSeT P — re-run with VBB-specific prompt for accurate VBB detection
            cubicle_result = identify_busbar_only(body.imageBase64, body.mimeType)
            raw_cubicles   = cubicle_result.get("cubicles", [])

            # If no VBB detected, split the edge cubicle on the busbar_side
            # PrismaSeT P: VBB = 150mm, main section = 650mm → VBB ≈ 19% of total panel width
            has_vbb = any(c.get("label") == "vbb" or c.get("is_vbb") for c in raw_cubicles)
            if not has_vbb and raw_cubicles:
                bs = data.get("busbar_side", "unknown")
                edge_c = raw_cubicles[-1] if bs == "right" else raw_cubicles[0] if bs == "left" else None
                if edge_c is None:
                    # fallback: pick narrowest edge cubicle
                    widths = [(c, c.get("box",[0,0,0,1000])[3] - c.get("box",[0,0,0,1000])[1]) for c in [raw_cubicles[0], raw_cubicles[-1]] if len(c.get("box",[])) >= 4]
                    edge_c = min(widths, key=lambda x: x[1])[0] if widths else None
                if edge_c and len(edge_c.get("box", [])) >= 4:
                    box     = edge_c["box"]
                    vbb_w   = int((box[3] - box[1]) * 150 / 650)
                    new_box = box.copy()
                    if bs == "right" or edge_c is raw_cubicles[-1]:
                        vbb_box      = [box[0], box[3] - vbb_w, box[2], box[3]]
                        edge_c["box"][3] = box[3] - vbb_w
                        raw_cubicles.append({"position": len(raw_cubicles) + 1, "label": "vbb", "is_vbb": True, "box": vbb_box})
                    else:
                        vbb_box      = [box[0], box[1], box[2], box[1] + vbb_w]
                        edge_c["box"][1] = box[1] + vbb_w
                        raw_cubicles.insert(0, {"position": 0, "label": "vbb", "is_vbb": True, "box": vbb_box})
                        for i, c in enumerate(raw_cubicles):
                            c["position"] = i + 1

            # Flag narrowest edge cubicle as VBB if still not flagged
            if raw_cubicles:
                widths   = [(c, c.get("box",[0,0,0,0])[3] - c.get("box",[0,0,0,0])[1]) for c in raw_cubicles if len(c.get("box",[])) >= 4]
                median_w = sorted([w for _, w in widths])[len(widths) // 2] if widths else 0
                for edge_c in [raw_cubicles[0], raw_cubicles[-1]]:
                    box = edge_c.get("box", [])
                    if len(box) >= 4 and (box[3] - box[1]) < 0.45 * median_w:
                        edge_c["is_vbb"] = True
                        edge_c["label"]  = "vbb"

            cubicles_px  = _build_cubicles_px(raw_cubicles)
            cubicle_line = _build_cubicle_line(raw_cubicles, include_vbb=True)

            data["cubicle_count"] = cubicle_result.get("cubicle_count", 0)
            data["cubicles"]      = cubicles_px
            data["cubicle_line"]  = cubicle_line
            print(f"[CUBICLE_LINE] {cubicle_line}")

            # Override busbar_side using actual VBB cubicle position (more reliable than Gemini's panel ID)
            vbb_cubicle = next((c for c in raw_cubicles if c.get("label") == "vbb" or c.get("is_vbb")), None)
            if vbb_cubicle and raw_cubicles:
                vbb_idx = raw_cubicles.index(vbb_cubicle)
                busbar_side = "left" if vbb_idx == 0 else "right"
                data["busbar_side"] = busbar_side
            else:
                busbar_side = data.get("busbar_side", "unknown")

            # Apply slide warnings last for PrismaSeT P — pass VBB cubicle + cubicle count for large/small detection
            sw = generate_safety_assessment(panel_type, body.workZone, data.get("breakers", []), panel_ymin_raw, panel_ymax_raw, vbb_cubicle, len(raw_cubicles), body.safetyBuffer)
            if sw: data["safety_warnings"] = sw

        except Exception as _e:
            print(f"[CUBICLE] Auto-detect failed (non-fatal): {_e}")
            data["cubicle_count"] = 0
            data["cubicles"]      = []
            data["cubicle_line"]  = ""
            # Still apply slide warnings even if cubicle detection failed
            sw = generate_safety_assessment(panel_type, body.workZone, data.get("breakers", []), panel_ymin_raw, panel_ymax_raw)
            if sw: data["safety_warnings"] = sw

    # Ensure catalogue_guidance always present in response
    if "catalogue_guidance" not in data:
        data["catalogue_guidance"] = ""

    _executor.shutdown(wait=False)
    return JSONResponse(content=data)


# ── Panel Library ─────────────────────────────────────────────────────────────
import os as _os
_LIBRARY_PATH = _os.path.join(_os.path.dirname(__file__), "panel_library.json")
with open(_LIBRARY_PATH, "r") as _f:
    PANEL_LIBRARY = json.load(_f)

class LocateVbbRequest(BaseModel):
    panelImageBase64:     str
    nameplateImageBase64: str
    mimeType: str = "image/jpeg"

def _read_mtz_nameplate(image_b64: str, mime_type: str) -> dict:
    """Read MTZ model and rating from nameplate close-up photo."""
    prompt = (
        "You are a Schneider Electric MasterPact expert.\n"
        "Look at this close-up photo of a circuit breaker nameplate or label.\n"
        "Extract:\n"
        "  1. The MasterPact model — return exactly one of: MTZ1, MTZ2, MTZ3, NT, or Unknown\n"
        "  2. The rated current in Amperes (e.g. 1600, 2500) — return 0 if not visible\n"
        "  3. The number of poles (3 or 4) — return 0 if not visible\n\n"
        "Rules:\n"
        "  - MTZ1: up to 1600A, smaller frame\n"
        "  - MTZ2: 1600A–4000A, medium frame\n"
        "  - MTZ3: 4000A–6300A, large frame\n"
        "  - NT:   older MasterPact NT series\n"
        "Respond with ONLY valid JSON: "
        '{"mtz_model": "MTZ1", "rated_current_A": 1600, "poles": 3}'
    )
    from pydantic import BaseModel as _BM
    class _NP(_BM):
        mtz_model: str
        rated_current_A: int
        poles: int
    response = client.models.generate_content(
        model=MODEL,
        contents=[{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            {"text": prompt}
        ]}],
        config=_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_NP,
        ),
    )
    return json.loads(response.text)


def _predict_vbb_location(image_b64: str, mime_type: str, mtz_info: dict) -> dict:
    """Predict VBB compartment location using panel image + library context."""
    mtz_model       = mtz_info.get("mtz_model", "Unknown")
    
    rated_current   = mtz_info.get("rated_current_A", 0)

    # Look up specs from library
    mtz_spec   = PANEL_LIBRARY["mtz_specs"].get(mtz_model, {})
    func_width = mtz_spec.get("min_cubicle_width_mm", "unknown")

    # Determine VBB width from rated current
    vbb_width_mm  = "unknown"
    busbar_rating = "unknown"
    for vbb in PANEL_LIBRARY["vbb_specs"]:
        if rated_current > 0 and vbb["min_rating_A"] <= rated_current <= vbb["max_rating_A"]:
            vbb_width_mm  = vbb["width_mm"]
            busbar_rating = vbb["label"]
            break
    if vbb_width_mm == "unknown" and mtz_spec:
        vbb_width_mm  = mtz_spec.get("inferred_vbb_widths_mm", ["unknown"])[0]
        busbar_rating = mtz_spec.get("inferred_busbar_ratings", ["unknown"])[0]

    vbb_clues     = "\n".join(f"  - {c}" for c in PANEL_LIBRARY["panel_specs"]["PrismaSeT P"]["vbb_door_clues"])
    safety_rules  = "\n".join(f"  - {r}" for r in PANEL_LIBRARY["panel_specs"]["PrismaSeT P"]["safety_rules"])
    inf_rules     = "\n".join(f"  - {r}" for r in PANEL_LIBRARY["inference_rules"])

    prompt = (
        f"You are a Schneider Electric PrismaSeT P panel expert.\n\n"
        f"PANEL INFORMATION FROM NAMEPLATE:\n"
        f"  MTZ Model: {mtz_model}\n"
        f"  Rated Current: {rated_current}A\n"
        f"  Functional section minimum width: {func_width}mm\n"
        f"  Expected VBB compartment width: {vbb_width_mm}mm\n"
        f"  Inferred busbar rating: {busbar_rating}\n\n"
        f"VBB COMPARTMENT VISUAL CLUES (what to look for):\n{vbb_clues}\n\n"
        f"INFERENCE RULES:\n{inf_rules}\n\n"
        f"YOUR TASK:\n"
        f"Look at this CLOSED PrismaSeT P panel image.\n"
        f"1. Identify which side (LEFT or RIGHT) has the narrow blank VBB door\n"
        f"2. Draw a tight bounding box [ymin, xmin, ymax, xmax] normalized 0-1000 around the VBB door\n"
        f"3. Rate your confidence: high, medium, or low\n"
        f"4. Write one sentence of notes explaining what you saw\n\n"
        f"SAFETY RULES TO INCLUDE:\n{safety_rules}\n\n"
        f"Respond with ONLY valid JSON."
    )

    from pydantic import BaseModel as _BM
    class _VbbResult(_BM):
        vbb_side:       str   # "left", "right", or "unknown"
        vbb_box:        list  # [ymin, xmin, ymax, xmax] 0-1000
        confidence:     str   # "high", "medium", "low"
        notes:          str
        safety_warning: str

    response = client.models.generate_content(
        model=MODEL,
        contents=[{"parts": [
            {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            {"text": prompt}
        ]}],
        config=_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_VbbResult,
        ),
    )
    result = json.loads(response.text)
    result["mtz_model"]       = mtz_model
    result["rated_current_A"] = rated_current
    result["vbb_width_mm"]    = vbb_width_mm
    result["busbar_rating"]   = busbar_rating
    return result


@app.post("/api/locate_vbb")
def locate_vbb(body: LocateVbbRequest):
    print(f"[LOCATE_VBB] Reading nameplate...")
    mtz_info = _read_mtz_nameplate(body.nameplateImageBase64, body.mimeType)
    print(f"[LOCATE_VBB] MTZ={mtz_info.get('mtz_model')} rating={mtz_info.get('rated_current_A')}A")

    print(f"[LOCATE_VBB] Predicting VBB location...")
    result = _predict_vbb_location(body.panelImageBase64, body.mimeType, mtz_info)

    # Convert VBB box from 0-1000 → pixel coords using panel image dimensions
    img_bytes = base64.b64decode(body.panelImageBase64)
    img       = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h      = img.size
    box_norm  = result.get("vbb_box", [0, 0, 1000, 1000])
    if len(box_norm) >= 4:
        result["vbb_box_px"] = [
            int(box_norm[0] / 1000 * h),
            int(box_norm[1] / 1000 * w),
            int(box_norm[2] / 1000 * h),
            int(box_norm[3] / 1000 * w),
        ]

    print(f"[LOCATE_VBB] side={result.get('vbb_side')} width={result.get('vbb_width_mm')}mm confidence={result.get('confidence')}")
    return JSONResponse(content=result)


if __name__ == "__main__":
    import uvicorn
    # Get actual LAN IP
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    finally:
        s.close()
    print(f"\n Server running at:")
    print(f"   http://localhost:8000")
    print(f"   http://{local_ip}:8000  ← use this in the Android app\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
