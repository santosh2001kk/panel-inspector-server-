"""
Local FastAPI server — mirrors /api/analyze endpoint the Android app expects.
Run:  python server.py
Then point the app to http://<your-mac-ip>:8000
"""

import base64
import io
import json
import os
import re
import socket
from pathlib import Path

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())
from PIL import Image
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional
def classify_panel(acb: int, mccb: int, mcb: int, drawers: int = 0) -> str:
    if acb >= 1 and drawers > 4:
        return "Okken"
    if acb >= 1 and drawers <= 4:
        return "PrismaSeT P"
    return "PrismaSeT G"

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")
GEMINI_KEY    = os.environ.get("GEMINI_KEY", "")

# Switch: "claude", "gemini", or "vertexai"
PROVIDER = "gemini"

VERTEX_PROJECT  = "project-dca768bf-132b-488c-8f2"
VERTEX_LOCATION = "us-central1"

if PROVIDER == "claude":
    import anthropic as _anthropic
    MODEL      = "claude-opus-4-7"
    FAST_MODEL = "claude-opus-4-7"
    client = _anthropic.Anthropic(api_key=ANTHROPIC_KEY)
elif PROVIDER == "vertexai":
    from google import genai as _genai
    from google.genai import types as _types
    MODEL      = "gemini-2.5-pro-preview-05-06"
    FAST_MODEL = "gemini-2.0-flash"
    client = _genai.Client(vertexai=True, project=VERTEX_PROJECT, location=VERTEX_LOCATION)
else:
    from google import genai as _genai
    from google.genai import types as _types
    MODEL      = "gemini-2.5-pro-preview-05-06"
    FAST_MODEL = "gemini-2.0-flash"
    client = _genai.Client(api_key=GEMINI_KEY)

app = FastAPI(title="Breaker Detection API", version="1.0.0")

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
    sldBase64: Optional[str] = None        # optional SLD diagram upload
    sldMimeType: str = "image/jpeg"
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

    # ── MasterPact MTZ ───────────────────────────────────────────────────────
    if "masterpact mtz" in pt or "mtz" in pt:
        base = (
            "\n\n--- SCHNEIDER ELECTRIC CATALOGUE REFERENCE: MasterPacT MTZ ---\n"
            "PHYSICAL LAYOUT:\n"
            "  - Air Circuit Breaker (ACB) — draw-out withdrawable chassis design\n"
            "  - Three frame sizes: MTZ1 (up to 1600A), MTZ2 (up to 4000A), MTZ3 (up to 6300A)\n"
            "  - Plug-in or draw-out mounting — three positions: CONNECTED, TEST, DISCONNECTED\n"
            "  - IEC 60947-2 compliant. Fit for Okken, PrismaSeT P, and other LV switchboards\n\n"
            "DEVICES AND COMPONENTS:\n"
            "  - Breaking unit: main contacts + arc chutes (replaceable)\n"
            "  - Mechanism: spring-charged operating mechanism (manual + motor-charged MCH)\n"
            "  - Control unit: electronic trip unit (MicroLogic X) — LSI/LSIG protection\n"
            "  - Chassis: draw-out racking mechanism with padlock facility\n"
            "  - Auxiliary contacts, UV release, shunt trip coils as accessories\n\n"
            "CRITICAL SAFETY:\n"
            "  - Busbars in the switchboard HBB/VBB remain LIVE even when MTZ is in DISCONNECTED position\n"
            "  - Always verify dead with approved voltage tester before any work inside switchboard\n"
            "  - LOTO (Lockout/Tagout) mandatory: rack to DISCONNECTED → lock chassis → test dead\n"
            "  - Arc flash PPE required for any work on live or recently de-energised equipment\n"
            "  - Do NOT defeat any safety shutters or interlocks on the chassis\n\n"
        )
        if t == "maintenance":
            return base + (
                "MAINTENANCE CHECKLIST — MasterPacT MTZ (Schneider Procedure Codes):\n\n"
                "ROUTINE END-USER MAINTENANCE (annually or per site plan):\n"
                "  Device:\n"
                "    • NII_Z_1 — Inspect general condition: check for dust, moisture, corrosion, mechanical damage\n"
                "    • NII_Z_1 — Verify nameplate ratings match SLD (rating, Icu, Ics)\n"
                "  Mechanism:\n"
                "    • NII_Z_1 — Operate breaker manually: OPEN and CLOSE at least 3 times — confirm smooth operation\n"
                "    • NII_Z_2 — Electrically operate via MCH gear motor (if fitted) — verify remote OPEN/CLOSE\n"
                "    • NII_Z_3 — Check spring charge indicator — spring must be fully charged after CLOSE\n"
                "  Auxiliaries:\n"
                "    • NII_Z_1 — Inspect auxiliary contacts, wiring harness and insulation — no fraying or burn marks\n"
                "  Chassis:\n"
                "    • NII_Z_1 — Check racking mechanism: rack CONNECTED → TEST → DISCONNECTED and back\n"
                "    •          Verify shutters operate correctly at each position\n"
                "    •          Check padlock facility on chassis\n\n"
                "INTERMEDIATE END-USER MAINTENANCE (every 3–5 years or after fault trip):\n"
                "  Breaking Unit:\n"
                "    • NIII_Z_1 — Inspect main contact condition and erosion (compare to new contact depth gauge)\n"
                "    • NIII_Z_2 — Check arc chutes for carbon deposits or mechanical damage — clean or replace\n"
                "  Control Unit:\n"
                "    • NIII_Z_4 — Verify MicroLogic X trip unit settings (Ir, Im, Isd, Ii thresholds)\n"
                "    •           Run self-test diagnostics on MicroLogic display\n"
                "    •           Check CT (current transformer) connections inside control unit\n"
                "  Chassis:\n"
                "    • NIII_Z_2 — Lubricate racking screw and sliding contacts with Schneider approved grease\n"
                "    • NIII_Z_3 — Inspect disconnecting contacts (cluster) for wear or burning — clean with dry cloth\n"
                "    • NIII_Z_4 — Check earth connection between chassis and switchboard earth bar\n\n"
                "MANUFACTURER-LEVEL MAINTENANCE (every 5–10 years or after major fault):\n"
                "  • Full disassembly and inspection by Schneider-certified engineer\n"
                "  • Breaking unit replacement if contact erosion exceeds limit\n"
                "  • Full mechanism overhaul and re-greasing\n"
                "  • Control unit calibration and protection relay test with injection set\n"
                "  • Issue maintenance report and update service logbook\n\n"
                "GENERAL SAFETY REMINDER:\n"
                "  • LOTO before any maintenance: DISCONNECTED position → lock chassis → verify dead\n"
                "  • Use CAT III or CAT IV approved voltage tester\n"
                "  • Arc flash PPE as per site risk assessment\n"
                "  • Record all maintenance in the MTZ service logbook\n"
            )
        if t == "commissioning":
            return base + (
                "COMMISSIONING CHECKLIST — MasterPacT MTZ:\n"
                "  1. Verify frame size and rating match SLD (MTZ1/MTZ2/MTZ3, current rating)\n"
                "  2. Check MicroLogic X trip unit settings: Ir, Im, Isd, Ii match protection coordination\n"
                "  3. Rack to TEST position — perform electrical OPEN/CLOSE test\n"
                "  4. Verify MCH gear motor operation (if fitted)\n"
                "  5. Check all auxiliary contacts wired per schematic\n"
                "  6. Verify UV release operates on supply loss\n"
                "  7. Check shutter operation at CONNECTED/TEST/DISCONNECTED positions\n"
                "  8. Rack to CONNECTED — perform final trip test via trip coil\n"
                "  9. Confirm earth connection chassis-to-switchboard\n"
                "  10. Record settings in MTZ commissioning logbook\n"
            )
        if t == "replacement":
            return base + (
                "REPLACEMENT GUIDANCE — MasterPacT MTZ:\n"
                "  1. Identify exact frame: MTZ1 (≤1600A), MTZ2 (≤4000A), MTZ3 (≤6300A)\n"
                "  2. Note current rating, trip unit type (MicroLogic X variant) from nameplate\n"
                "  3. Rack existing MTZ to DISCONNECTED position\n"
                "  4. Lock chassis with padlock — apply LOTO tag\n"
                "  5. Verify dead with approved voltage tester\n"
                "  6. Disconnect auxiliary wiring harness before withdrawing chassis\n"
                "  7. Slide out on racking screw — do NOT force or tilt\n"
                "  8. Replacement unit must match: frame size, rating, trip unit type, accessories\n"
                "  9. After installation: rack to TEST → verify operation → rack to CONNECTED\n"
                "  10. Re-enter MicroLogic X protection settings and test trip functions\n"
            )
        if t == "modification":
            return base + (
                "MODIFICATION GUIDANCE — MasterPacT MTZ:\n"
                "  1. Identify which accessories are fitted (MCH, UV, shunt trip, aux contacts)\n"
                "  2. New accessories must be MTZ-compatible — check frame size compatibility\n"
                "  3. Rack to DISCONNECTED and LOTO before fitting any accessory\n"
                "  4. Control unit (MicroLogic X) can be swapped without full replacement\n"
                "  5. After modification: re-verify all settings and perform functional test\n"
                "  6. Update SLD and panel schedule to reflect changes\n"
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


# ── Excel: "List of Use Cases ERMS" — operations per task type ─────────────────
# Source: Copy of List of Uses cases ERMS-2.xlsx (EW activities + USe cases ERMS sheets)
# erms: "ON" = mandatory, "recommended" = should activate, "OFF" = not needed
_EW_ACTIVITIES = {
    "commissioning": [
        {"op": "First racking in of incomer",                   "position": "outside", "hazards": ["Arc Flash"],                       "erms": "none",        "alt": ""},
        {"op": "First energization / re-energization",          "position": "outside", "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": "Remote O/C — operator stays at panel front face"},
        {"op": "Voltage & phase sequence checks",               "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "recommended", "alt": "Use installed panel meter — avoids direct contact with live parts"},
        {"op": "Auxiliary voltage checks",                      "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "recommended", "alt": "Use installed panel meter — insulated probes only"},
        {"op": "First racking in of feeder",                    "position": "outside", "hazards": ["Arc Flash", "Electric Shock"],      "erms": "none",        "alt": ""},
        {"op": "First closing of feeder / functional testing",  "position": "outside", "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": "Remote O/C"},
    ],
    "operation": [
        {"op": "Racking in / out of incomer",                   "position": "outside", "hazards": ["Arc Flash"],                       "erms": "none",        "alt": ""},
        {"op": "Feeder closing",                                 "position": "outside", "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": "Remote O/C"},
        {"op": "Feeder opening",                                 "position": "outside", "hazards": ["Arc Flash", "Electric Shock"],      "erms": "recommended", "alt": "Remote O/C"},
        {"op": "Racking in / out of feeder",                    "position": "outside", "hazards": ["Arc Flash"],                       "erms": "none",        "alt": ""},
        {"op": "Feeder consignation / padlocking",              "position": "outside", "hazards": ["Arc Flash", "Electric Shock"],      "erms": "recommended", "alt": "Disconnect and padlock at load / downstream equipment"},
        {"op": "Feeder deconsignation / unpadlocking",          "position": "outside", "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": "Remote O/C at switchboard level"},
        {"op": "Meter reading behind doors",                    "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "recommended", "alt": "MTZ App / Smartpanel — no physical door opening needed"},
        {"op": "Reading panel meter / display (doors closed)",  "position": "outside", "hazards": [],                                  "erms": "OFF",         "alt": "Remote monitoring system"},
    ],
    "service": [
        {"op": "Thermographic inspection",                      "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": "Install permanent thermal monitoring — avoids future live access"},
        {"op": "Cable inspection",                              "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": ""},
        {"op": "Portable measurements (U, I, power quality)",  "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": "Install Power meter / Digital module in MTZ — avoids future access"},
        {"op": "Troubleshooting (auxiliary issues)",            "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": ""},
    ],
    "modification": [
        {"op": "Addition of feeder in spare slot",              "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": "Forbid work with energized switchboard where possible"},
        {"op": "Cable addition / handling (power or control)",  "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": ""},
        {"op": "Equipment upgrade / addition of auxiliaries",   "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": ""},
    ],
    "replacement": [
        {"op": "Breaker / component replacement",               "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": ""},
        {"op": "Cable replacement / handling",                  "position": "inside",  "hazards": ["Arc Flash", "Electric Shock"],      "erms": "ON",          "alt": ""},
    ],
    "others": [
        {"op": "Non-electrical work <0.3m from switchboard",   "position": "near",    "hazards": [],                                  "erms": "ON",          "alt": "Forbid access with energized switchboard"},
        {"op": "Non-electrical work 0.3–1m from switchboard",  "position": "near",    "hazards": [],                                  "erms": "ON",          "alt": "Forbid access with energized switchboard"},
        {"op": "Non-electrical work 1–3m from switchboard",    "position": "room",    "hazards": [],                                  "erms": "recommended", "alt": ""},
        {"op": "Non-electrical work >3m from switchboard",     "position": "room",    "hazards": [],                                  "erms": "OFF",         "alt": ""},
    ],
}


def _task_recommendations(task: str, has_work_zone: bool) -> tuple:
    """
    Returns (warnings: list[str], recommendations: list[dict]) from the Excel use-case table.
    warnings   → prepended to safety_warnings (shown in Safety tab)
    recommendations → returned as data["task_recommendations"] (shown as a table in the web app)
    Note from Excel: ERMS only protects LOAD side of incomer — supply side is NOT covered.
    """
    t  = task.lower().strip()
    ew = _EW_ACTIVITIES.get(t, _EW_ACTIVITIES["others"])

    # Filter operations by where the worker is
    if has_work_zone:
        ops = [a for a in ew if a["position"] == "inside"] or ew
    else:
        ops = [a for a in ew if a["position"] in ("outside", "near")] or ew

    warnings = []

    # One warning per operation — full picture: hazards + ERMS + alternative
    _ERMS_LABEL = {"ON": "ERMS ON required", "recommended": "ERMS recommended", "OFF": "ERMS OFF", "none": ""}
    _HAZARD_ICON = {"Arc Flash": "🔥", "Electric Shock": "⚡"}

    for a in ops:
        parts = []
        hazard_str = " + ".join(f"{_HAZARD_ICON.get(h, '')} {h}" for h in a["hazards"]) if a["hazards"] else "No direct electrical hazard"
        erms_str   = _ERMS_LABEL.get(a["erms"], "")
        parts.append(f"[{a['op']}]")
        parts.append(f"Position: {a['position'].replace('inside','Inside switchboard (doors open)').replace('outside','Electrical room <0.3m (doors closed)').replace('near','Electrical room 0.3–1m').replace('room','Electrical room >1m')}")
        parts.append(f"Hazard: {hazard_str}")
        if erms_str:
            parts.append(erms_str)
        if a["alt"]:
            parts.append(f"Alternative: {a['alt']}")
        warnings.append("  |  ".join(parts))

    # Add the ERMS supply-side note once if any operation uses ERMS
    if any(a["erms"] in ("ON", "recommended") for a in ops):
        warnings.append(
            "⚠ ERMS Note: ERMS only protects the LOAD side of the main incomer. "
            "Work near incoming supply cables (top of panel) is NOT covered by ERMS — "
            "additional precautions required there."
        )

    # Structured form for web app table rendering
    recommendations = [
        {
            "operation":  a["op"],
            "position":   a["position"],
            "hazards":    a["hazards"],
            "erms":       a["erms"],
            "alternative": a["alt"],
        }
        for a in ops
    ]

    return warnings, recommendations


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
    """Returns True if the breaker box CENTER is inside the zone."""
    ymin, xmin, ymax, xmax = box
    cy = (ymin + ymax) / 2
    cx = (xmin + xmax) / 2
    return zone.xmin <= cx <= zone.xmax and zone.ymin <= cy <= zone.ymax


class LabelRequest(BaseModel):
    imageBase64: str
    mimeType: str = "image/jpeg"

@app.post("/api/read_label")
def read_label(body: LabelRequest):
    """
    Takes a cropped image of a single breaker (sent from Android when the user
    taps a bounding box on the result screen) and asks Gemini to read the
    circuit label and current rating from close up.

    Returns:
        {"circuit_label": "LV MAIN", "rating": "400A"}
    Both fields are empty strings when not readable.
    """
    prompt = (
        "This is a close-up photo of a single electrical circuit breaker or its label strip.\n"
        "Read any text visible on the breaker face or on the adjacent label strip.\n\n"
        "Return ONLY a JSON object with exactly two fields:\n"
        '  "circuit_label": the circuit name/description (e.g. "LV MAIN", "LIGHTING CKT 1", "UPS FEEDER") — empty string if not readable\n'
        '  "rating": the current rating (e.g. "400A", "63A", "16A") — empty string if not readable\n\n'
        'Return ONLY valid JSON, nothing else. Example: {"circuit_label": "LV MAIN", "rating": "400A"}'
    )
    try:
        img_bytes = base64.b64decode(body.imageBase64)
        parsed = _call_llm(prompt, [(body.imageBase64, body.mimeType)])
        return JSONResponse(content={
            "circuit_label": str(parsed.get("circuit_label", "")),
            "rating":        str(parsed.get("rating", "")),
        })
    except Exception as e:
        print(f"[READ_LABEL] error: {e}")
        return JSONResponse(content={"circuit_label": "", "rating": "", "error": str(e)})


@app.get("/")
def root():
    return RedirectResponse(url="/index.html")


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

        "IMPORTANT: If the image does NOT show an electrical panel at all (e.g. it is a person, animal, "
        "food, vehicle, landscape, or any non-electrical object), set panel_type to 'Not a Panel' and "
        "in panel_summary describe what the image actually shows (e.g. 'This is a cat').\n\n"

        "Return ONLY valid JSON:\n"
        '{"panel_type": "PrismaSeT P", "panel_summary": "describe the key feature you used to identify it"}'
    )
    return _call_llm(prompt, [(image_b64, mime_type)])




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


def _call_llm(prompt: str, images: list, max_tokens: int = 4096) -> dict:
    """Call the configured LLM with a prompt and list of (base64, mime_type) image tuples.
    Returns a parsed JSON dict. Prompt must instruct the model to return ONLY valid JSON."""
    if PROVIDER == "claude":
        content = []
        for img_b64, mime in images:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mime, "data": img_b64},
            })
        content.append({"type": "text", "text": prompt})
        response = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    else:
        parts = []
        for img_b64, mime in images:
            parts.append({"inline_data": {"mime_type": mime, "data": img_b64}})
        parts.append({"text": prompt})
        response = _gemini_with_retry(lambda: client.models.generate_content(
            model=MODEL,
            contents=[{"parts": parts}],
            config=_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        ))
        raw = (getattr(response, 'text', None) or "").strip()
        if not raw:
            raise ValueError(f"Empty response from Gemini (finish_reason={getattr(response, 'candidates', [{}])[0].get('finish_reason','?') if getattr(response,'candidates',None) else '?'})")
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)


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

    # --- Identify-only mode: just return panel type + 1 line ---
    if body.identifyOnly:
        result     = identify_panel_only(body.imageBase64, body.mimeType)
        panel_type = result.get("panel_type", "Unknown")
        print(f"[PANEL] {panel_type} — {result.get('panel_summary')}")
        if panel_type.strip().lower() == "not a panel":
            return JSONResponse(
                status_code=422,
                content={"error": "not_a_panel", "detected_as": result.get("panel_summary", "not an electrical panel")}
            )
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

    from concurrent.futures import ThreadPoolExecutor
    _executor = ThreadPoolExecutor(max_workers=1)

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
            type: str = _F(description="Component name. For breakers use Schneider product name (MasterPact MTZ, MasterPact NT, Compact NSX, Compact NS, Acti9, iC60, Multi9). For other components use: Contactor, Relay, PLC, Meter, Terminal Block, Cable Duct, or Column.")
            box: list[int] = _F(description="[ymin, xmin, ymax, xmax] normalized 0-1000.")
            category: str = _F(default="component", description="'component' for breakers, contactors, PLCs, meters, relays. 'structure' for panel columns, drawers, and cubicle sections.")
            brand: str = _F(default="", description="Manufacturer brand if identifiable — e.g. 'Schneider', 'ABB', 'Siemens', 'Legrand'. Empty string if unknown.")
            type_detail: str = _F(default="", description="Specific sub-type — e.g. 'ACB', 'MCCB', 'MCB', 'Contactor', 'PLC', 'Power Meter'. Empty string if already in type field.")
            circuit_label: str = _F(default="", description="Circuit name/description on label strip — e.g. 'LV MAIN', 'LIGHTING DB'. Empty if not visible.")
            rating: str = _F(default="", description="Current rating on breaker face — e.g. '400A', '63A'. Empty if not visible.")
            estimated_dimensions: str = _F(default="", description="Estimated physical size if determinable — e.g. '250x150mm'. Empty if not estimable.")
        class _DetectionResult(_BM):
            breakers: list[_Breaker] = _F(description="One entry per individual component. For 'standard'/'expert' mode include all visible components. For 'fast' mode include only major breakers.")
            panel_type: str = _F(description=(
                "Exactly one of: PrismaSeT G, PrismaSeT P, Okken, Not a Panel. "
                "Use 'Not a Panel' if the image does not show an electrical switchboard, distribution board, "
                "or LV panel (e.g. it shows a person, animal, food, vehicle, landscape, etc.)."
            ))
            busbar_side: str = _F(description=(
                "Only for PrismaSeT P: identify which side has the 150mm busbar compartment. "
                "Look for the side with a BLANK solid metal door/panel with NO visible breakers — that is the busbar compartment. "
                "The breaker side has visible MCCBs and MCBs. "
                "Return 'left', 'right', or 'unknown'. "
                "For PrismaSeT G and Okken return 'unknown'."
            ))
            notes: str = _F(description=(
                "If panel_type is 'Not a Panel': describe what the image actually shows, e.g. 'This is a cat', "
                "'This appears to be a car', 'This is a landscape photo'. "
                "Otherwise: one sentence summarising the breakers found in the work zone."
            ))
            safety_warnings: list[str]
            summary: str = _F(default="", description="One-sentence technical summary of the panel and its main components.")

        # Unified high-detail detection instructions with improved precision
        _detection_instructions = (
            "\nPERFORM HIGH-PRECISION COMPONENT INVENTORY:\n"
            "1. Detect EVERY visible component, especially those INSIDE the marked Work Zone.\n"
            "2. For each component identify: brand (Schneider, ABB, Siemens, Legrand, etc.), type_detail, and estimated physical dimensions.\n"
            "3. Detect PANEL STRUCTURE: identify each vertical column/cubicle as a separate entry with category='structure' and type='Column'.\n"
            "   For draw-out panels (Okken/Blokset): also detect individual drawers as category='structure', type='Drawer'.\n"
            "4. Return ONE entry per individual component — do NOT group.\n"
            "5. Read circuit_label and rating from breaker faces and label strips.\n"
            "6. BOUNDING BOXES: Ensure boxes are extremely tight to the component body. Do not include wires or gaps.\n"
        )

        gemini_prompt = prompt + _detection_instructions
        # Build parts — add SLD and layout if provided
        parts = []
        if body.sldBase64:
            parts.append({"inline_data": {"mime_type": body.sldMimeType, "data": body.sldBase64}})
            parts.append({"text": "Above is the Single Line Diagram (SLD) of this panel. Use it to understand the circuit layout, breaker ratings, and connections."})
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

    # Reject non-panel images
    if data.get("panel_type", "").strip().lower() == "not a panel":
        print(f"[GATE] Rejected: not a panel. Notes: {data.get('notes','')}")
        return JSONResponse(
            status_code=422,
            content={"error": "not_a_panel", "detected_as": data.get("notes", "not an electrical panel")}
        )

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

        # Filter components to safety buffer zone (or work zone as fallback)
        # Structure items always pass; components must have center inside the zone
        is_structure = b.get("category", "component") == "structure"
        if not is_structure:
            filter_zone = body.safetyBuffer or body.workZone
            if filter_zone and not inside_zone([ymin, xmin, ymax, xmax], filter_zone):
                continue

        # Keep coordinates as 0-1000 normalized — canvas scales them to display size
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

    # --- Safety warnings + ERMS ---
    if body.workZone:
        sw = generate_safety_assessment(panel_type, body.workZone, data.get("breakers", []), panel_ymin_raw, panel_ymax_raw)
        erms_ws, erms_recs = _task_recommendations(body.task, bool(body.workZone))
        data["task_recommendations"] = erms_recs
        data["safety_warnings"] = erms_ws + (sw if sw else data.get("safety_warnings", []))

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
    return _call_llm(prompt, [(image_b64, mime_type)])


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

    result = _call_llm(prompt, [(image_b64, mime_type)])
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




# --- Panel Photo Match Verification ---

class VerifyPanelRequest(BaseModel):
    referenceBase64: str          # original scan image
    workerBase64:    str          # photo taken by worker right now
    mimeType:        str = "image/jpeg"

@app.post("/api/verify_panel")
def verify_panel(body: VerifyPanelRequest):
    """
    Sends both images to Gemini and asks:
    'Are these photos showing the exact same electrical panel?'
    Returns match=True/False + reason + confidence.
    """
    from pydantic import BaseModel as _BM

    class _VerifyResult(_BM):
        match:      bool
        confidence: str   # "high", "medium", "low"
        reason:     str   # one sentence explanation

    prompt = (
        "You are a safety engineer verifying electrical panel identity.\n\n"
        "You are given TWO photos:\n"
        "  IMAGE 1 — the REFERENCE photo taken during the original risk analysis\n"
        "  IMAGE 2 — the CURRENT photo taken by the worker right now\n\n"
        "YOUR TASK:\n"
        "Determine if both photos show the EXACT SAME electrical panel.\n\n"
        "LOOK FOR:\n"
        "  - Same panel type (Okken / PrismaSeT P / PrismaSeT G)\n"
        "  - Same number of cubicles / doors\n"
        "  - Same breaker layout and positions\n"
        "  - Same colour, size, and physical appearance\n"
        "  - Same labels, markings, or visible serial numbers\n"
        "  - Same surroundings (wall, floor, adjacent equipment)\n\n"
        "IMPORTANT:\n"
        "  - Angle and lighting may differ — judge the panel itself, not the photo quality\n"
        "  - If the panel in IMAGE 2 has different breaker layout or different cubicle count → NOT the same panel\n"
        "  - If you cannot determine with confidence → set match=false for safety\n\n"
        "Return ONLY valid JSON."
    )

    try:
        if PROVIDER == "claude":
            response = client.messages.create(
                model=MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": body.mimeType, "data": body.referenceBase64}},
                    {"type": "text", "text": "IMAGE 1 — Reference photo from risk analysis:"},
                    {"type": "image", "source": {"type": "base64", "media_type": body.mimeType, "data": body.workerBase64}},
                    {"type": "text", "text": "IMAGE 2 — Current photo taken by worker:\n\n" + prompt},
                ]}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
        else:
            response = _gemini_with_retry(lambda: client.models.generate_content(
                model=MODEL,
                contents=[{"parts": [
                    {"inline_data": {"mime_type": body.mimeType, "data": body.referenceBase64}},
                    {"text": "IMAGE 1 — Reference photo from risk analysis:"},
                    {"inline_data": {"mime_type": body.mimeType, "data": body.workerBase64}},
                    {"text": "IMAGE 2 — Current photo taken by worker:\n\n" + prompt},
                ]}],
                config=_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_VerifyResult,
                    temperature=0.0,
                ),
            ))
            result = json.loads(response.text)
        print(f"[VERIFY] match={result.get('match')} confidence={result.get('confidence')} reason={result.get('reason')}")
        return JSONResponse(content=result)
    except Exception as e:
        print(f"[VERIFY] Error: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ----------------------------------------

# --- Pre-Work Safety Checklist ---

class ChecklistRequest(BaseModel):
    task_type: str          # commissioning | maintenance | modification | replacement | troubleshooting | others
    is_live: bool           # True = live intervention, False = dead (LOTO)
    panel_type: str         # PrismaSeT G | PrismaSeT P | Okken
    has_sld: bool           # whether SLD is loaded in app
    vbb_side: Optional[str] = None   # left | right | unknown (PrismaSeT P only)
    cubicle_count: int = 0

_CHECKLIST_COMMON_DEAD = [
    {"id": "dead_1", "text": "Confirm you are working on the CORRECT panel — matches the scanned panel in the app.", "critical": True},
    {"id": "dead_2", "text": "LOTO completed — personal lock and tag physically on the isolator (consignation/padlocking done).", "critical": True},
    {"id": "dead_3", "text": "Absence of voltage confirmed using an approved tester — panel is DEAD.", "critical": True},
    {"id": "dead_4", "text": "PPE appropriate for the residual arc flash risk is worn (minimum PPE1 even when de-energized).", "critical": True},
    {"id": "dead_5", "text": "Arc flash boundary marked and all nearby personnel informed.", "critical": False},
]

_CHECKLIST_COMMON_LIVE = [
    {"id": "live_1", "text": "Live work permit obtained and signed by supervisor.", "critical": True},
    {"id": "live_2", "text": "ERMS (Energy Reduction Maintenance Setting) activated on the incomer circuit breaker.", "critical": True},
    {"id": "live_3", "text": "Arc flash PPE worn — face shield, arc flash suit, insulated gloves rated for this voltage level.", "critical": True},
    {"id": "live_4", "text": "Only insulated tools used — no bare metal tools near live parts.", "critical": True},
    {"id": "live_5", "text": "Safety observer present and knows the emergency procedure and first aid location.", "critical": True},
    {"id": "live_6", "text": "All personnel informed — no unexpected re-energization possible during work.", "critical": True},
    {"id": "live_7", "text": "Hazard identified: Arc Flash + Electric Shock risk. Working distance ≥ 300 mm from live parts.", "critical": False},
]

# Task checklists — sourced from Excel 'EW activities' + 'Use cases ERMS'
# Each item carries an 'erms' field: "ON" | "recommended" | "OFF" | None
_CHECKLIST_BY_TASK = {
    "commissioning": [
        {"id": "com_1", "text": "All wiring verified against SLD before first energization.", "critical": True, "erms": None},
        {"id": "com_2", "text": "Insulation resistance test completed — results within acceptable range.", "critical": True, "erms": None},
        {"id": "com_3", "text": "All protective devices set to correct ratings per design.", "critical": True, "erms": None},
        {"id": "com_4", "text": "First racking in of incomer: doors CLOSED/OPEN. Position: Electrical room <0.3m. Hazard: 🔥 Arc Flash only. ERMS: not required for racking alone.", "critical": True, "erms": None},
        {"id": "com_5", "text": "First energization / re-energization: doors CLOSED. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS ON required. Consider Remote O/C — operator stays at panel front face.", "critical": True, "erms": "ON"},
        {"id": "com_6", "text": "Voltage & phase sequence checks: doors OPEN, inside switchboard. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS recommended. Alternative: use installed panel meter — avoids direct contact.", "critical": True, "erms": "recommended"},
        {"id": "com_7", "text": "Auxiliary voltage checks: doors OPEN, inside switchboard. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS recommended. Insulated probes only.", "critical": True, "erms": "recommended"},
        {"id": "com_8", "text": "First closing of feeder / functional testing: doors CLOSED/OPEN. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS ON required. Alternative: Remote O/C.", "critical": True, "erms": "ON"},
        {"id": "com_9", "text": "First energization plan communicated to all team members before starting.", "critical": False, "erms": None},
    ],
    "operation": [
        {"id": "op_1", "text": "Identified the correct feeder/incomer — confirmed by panel label and SLD.", "critical": True, "erms": None},
        {"id": "op_2", "text": "Racking in/out of incomer: doors CLOSED/OPEN. Position: Electrical room <0.3m. Hazard: 🔥 Arc Flash only. ERMS: not required for racking alone.", "critical": True, "erms": None},
        {"id": "op_3", "text": "Feeder closing: doors CLOSED/OPEN. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS ON required. Alternative: Remote O/C.", "critical": True, "erms": "ON"},
        {"id": "op_4", "text": "Feeder opening: doors CLOSED/OPEN. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS recommended. Alternative: Remote O/C.", "critical": False, "erms": "recommended"},
        {"id": "op_5", "text": "Feeder consignation / padlocking: doors CLOSED/OPEN. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS recommended. Alternative: disconnect and padlock at load side.", "critical": True, "erms": "recommended"},
        {"id": "op_6", "text": "Feeder deconsignation: doors CLOSED/OPEN. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS ON required. Alternative: Remote O/C at switchboard level.", "critical": True, "erms": "ON"},
        {"id": "op_7", "text": "Meter reading behind doors (inside SWB): doors OPEN. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS recommended. Alternative: MTZ App / Smartpanel — no door opening needed.", "critical": False, "erms": "recommended"},
        {"id": "op_8", "text": "Reading panel meter / display: doors CLOSED. No direct electrical hazard. ERMS OFF acceptable. Alternative: remote monitoring system.", "critical": False, "erms": "OFF"},
    ],
    "service": [
        {"id": "svc_1", "text": "All service work: doors OPEN, inside switchboard. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS ON required.", "critical": True, "erms": "ON"},
        {"id": "svc_2", "text": "Thermographic inspection: use thermal camera — no direct contact with live parts. ERMS ON. Alternative: install permanent thermal monitoring.", "critical": True, "erms": "ON"},
        {"id": "svc_3", "text": "Portable measurements (U, I, power quality): calibrated insulated probes only. ERMS ON. Alternative: install Power meter / Digital module in MTZ.", "critical": True, "erms": "ON"},
        {"id": "svc_4", "text": "Cable inspection: check for damage, loose connections — no bare hand contact near live cables. ERMS ON.", "critical": True, "erms": "ON"},
        {"id": "svc_5", "text": "Troubleshooting: root cause documented — no re-energization until fault fully cleared. ERMS ON.", "critical": False, "erms": "ON"},
        {"id": "svc_6", "text": "⚠ ERMS Note: ERMS only protects load side of incomer. Work near supply cables is NOT covered.", "critical": False, "erms": None},
    ],
    "modification": [
        {"id": "mod_1", "text": "All modification work: doors OPEN, inside switchboard. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS ON mandatory.", "critical": True, "erms": "ON"},
        {"id": "mod_2", "text": "Adjacent busbars remain LIVE — insulating barriers placed over all live busbars before starting.", "critical": True, "erms": None},
        {"id": "mod_3", "text": "Magnetic parts tray in use — screws, nuts, washers secured to prevent drops onto live busbars.", "critical": True, "erms": None},
        {"id": "mod_4", "text": "New cables pre-cut and pre-terminated BEFORE approaching the busbar area.", "critical": True, "erms": None},
        {"id": "mod_5", "text": "Spare slot confirmed empty and busbar capacity checked before installing new feeder.", "critical": True, "erms": None},
        {"id": "mod_6", "text": "Change permit / work order signed. Supervisor informed of live adjacent sections.", "critical": False, "erms": None},
        {"id": "mod_7", "text": "⚠ ERMS Note: ERMS only protects load side of incomer. Work near supply cables is NOT covered.", "critical": False, "erms": None},
    ],
    "replacement": [
        {"id": "rep_1", "text": "Replacement work: doors OPEN, inside switchboard. Hazard: 🔥 Arc Flash + ⚡ Electric Shock. ERMS ON required.", "critical": True, "erms": "ON"},
        {"id": "rep_2", "text": "Adjacent busbars may still be live — insulating barriers placed before starting.", "critical": True, "erms": None},
        {"id": "rep_3", "text": "Replacement breaker has the CORRECT rating — type, current, voltage matches original exactly.", "critical": True, "erms": None},
        {"id": "rep_4", "text": "Correct polarity and phase sequence verified before installing the new breaker.", "critical": True, "erms": None},
        {"id": "rep_5", "text": "Torque settings for connections confirmed from manufacturer datasheet.", "critical": False, "erms": None},
        {"id": "rep_6", "text": "Old breaker safely removed and disposed — not left inside the panel.", "critical": False, "erms": None},
    ],
    "others": [
        {"id": "oth_1", "text": "Work scope clearly defined and approved by supervisor before entering electrical room.", "critical": True, "erms": None},
        {"id": "oth_2", "text": "Non-electrical work <0.3m from switchboard: doors CLOSED. No direct electrical hazard but Arc Flash risk present. ERMS ON required. Alternative: forbid access with energized switchboard.", "critical": True, "erms": "ON"},
        {"id": "oth_3", "text": "Non-electrical work 0.3–1m from switchboard: doors CLOSED. ERMS ON required. Alternative: forbid access with energized switchboard.", "critical": False, "erms": "ON"},
        {"id": "oth_4", "text": "Non-electrical work 1–3m from switchboard: doors CLOSED. ERMS recommended.", "critical": False, "erms": "recommended"},
        {"id": "oth_5", "text": "Non-electrical work >3m from switchboard: no direct electrical hazard. ERMS OFF acceptable.", "critical": False, "erms": "OFF"},
    ],
}

_CHECKLIST_PANEL_EXTRAS = {
    "PrismaSeT P": [
        {"id": "psp_1", "text": "⚠ PrismaSeT P — VBB (Vertical Busbar Box) compartment is ALWAYS live even when panel is isolated. Do NOT drill or penetrate the VBB door.", "critical": True},
    ],
    "Okken": [
        {"id": "okk_1", "text": "⚠ Okken panel — Horizontal Busbar (HBB) runs at the TOP and BEHIND the panel. Keep clear of the top section during intervention.", "critical": True},
    ],
    "PrismaSeT G": [],
}

_SLD_MISSING = {"id": "sld_1", "text": "⚠ No SLD loaded in the app — verify circuit layout from physical inspection before starting.", "critical": False}


@app.post("/api/checklist")
def get_checklist(body: ChecklistRequest):
    task = body.task_type.lower().strip()
    items = []

    # SLD warning
    if not body.has_sld:
        items.append(_SLD_MISSING)

    # Common base checklist
    if body.is_live:
        items += _CHECKLIST_COMMON_LIVE
    else:
        items += _CHECKLIST_COMMON_DEAD

    # Task-specific items
    items += _CHECKLIST_BY_TASK.get(task, _CHECKLIST_BY_TASK["others"])

    # Panel-specific warnings
    panel_key = next((k for k in _CHECKLIST_PANEL_EXTRAS if k.lower() in body.panel_type.lower()), None)
    if panel_key:
        extras = _CHECKLIST_PANEL_EXTRAS[panel_key]
        # For PrismaSeT P, add VBB side info if known
        if panel_key == "PrismaSeT P" and body.vbb_side and body.vbb_side != "unknown":
            extras = [dict(e) for e in extras]
            extras[0]["text"] = extras[0]["text"].replace("VBB (Vertical Busbar Box) compartment", f"VBB compartment on the {body.vbb_side.upper()} side")
        items += extras

    total    = len(items)
    critical = sum(1 for i in items if i["critical"])

    # Attach Excel-sourced operation-level recommendations (hazards + ERMS + alternatives)
    ew_ops = _EW_ACTIVITIES.get(task, _EW_ACTIVITIES["others"])
    task_recommendations = [
        {
            "operation":   a["op"],
            "position":    a["position"],
            "hazards":     a["hazards"],
            "erms":        a["erms"],
            "alternative": a["alt"],
        }
        for a in ew_ops
    ]

    print(f"[CHECKLIST] task={task} live={body.is_live} panel={body.panel_type} items={total} critical={critical}")
    return JSONResponse(content={
        "task_type":           task,
        "is_live":             body.is_live,
        "panel_type":          body.panel_type,
        "items":               items,
        "total":               total,
        "critical":            critical,
        "task_recommendations": task_recommendations,
    })


# ----------------------------------------


# Serve the web UI — must be last so API routes take priority
import os as _os
_web_dir = _os.path.join(_os.path.dirname(__file__), "web")
if _os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="web")

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
    print(f"   Web UI: http://{local_ip}:8000/index.html\n")
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
