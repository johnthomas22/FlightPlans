#!/usr/bin/env python3
"""
pdf_parser.py
-------------
Parse a Condor task briefing PDF and return a task dict for condor_fpl_gen.py.

Requires:
    pip install pdfplumber

Usage:
    from pdf_parser import parse_task_pdf
    task = parse_task_pdf("Spring2026#5.pdf")
    # task["landscape"]       -> "Centro_Italia3"
    # task["airport_name"]    -> "Castellucio"   (physical launch airfield)
    # task["turnpoints"]      -> [start_gate, wp, ..., finish]  (from task table)
    # task["weather"]         -> {wind_dir_deg, wind_speed_kts, cloud_base_ft, ...}
"""

import re

try:
    import pdfplumber
except ImportError:
    raise ImportError(
        "pdfplumber is required for PDF parsing.\n"
        "Install it with:  pip install pdfplumber"
    )


# ---------------------------------------------------------------------------
# Landscape name normalisation
# ---------------------------------------------------------------------------

# Maps the normalised PDF display name -> Condor internal folder name.
# Add entries here when new landscapes appear.
_LANDSCAPE_MAP = {
    "centroitalia3":   "Centro_Italia3",
    "slovenia3":       "Slovenia3",
    "alps1":           "Alps1",
}

def _normalise_landscape(raw: str) -> str:
    key = raw.lower().replace("_", "").replace(" ", "").strip()
    if key in _LANDSCAPE_MAP:
        return _LANDSCAPE_MAP[key]
    # Fallback: title-case words joined by underscores
    return "_".join(w.capitalize() for w in raw.split())


# ---------------------------------------------------------------------------
# Aircraft name normalisation
# ---------------------------------------------------------------------------

# PDF display name (lowercase) -> Condor internal name
_AIRCRAFT_MAP = {
    "duo discus xl":     "DuoDiscusXL",
    "duo discus t":      "DuoDiscusT",
    "duo discus":        "DuoDiscus",
    "std cirrus":        "StdCirrus",
    "standard cirrus":   "StdCirrus",
    "ls4":               "LS4",
    "ls8":               "LS8",
    "discus 2":          "Discus2",
    "discus2":           "Discus2",
    "blanik":            "Blanik",
    "asw 28":            "ASW28",
    "asw28":             "ASW28",
    "nimbus 4":          "Nimbus4",
    "ventus 2":          "Ventus2",
}

def _normalise_aircraft(raw: str) -> str:
    key = raw.lower().strip()
    if key in _AIRCRAFT_MAP:
        return _AIRCRAFT_MAP[key]
    # Fallback: remove spaces
    return raw.replace(" ", "")


# ---------------------------------------------------------------------------
# Coordinate parsing
# ---------------------------------------------------------------------------

def _ddm_to_decimal(ddm: str) -> float:
    """
    Parse 'N42°48.702' or 'E013°12.540' to decimal degrees.
    Returns float (positive N/E, negative S/W).
    """
    m = re.match(r'([NSEWnsew])(\d+)[°](\d+\.?\d*)', ddm.strip())
    if not m:
        return 0.0
    hemi = m.group(1).upper()
    deg  = int(m.group(2))
    mins = float(m.group(3))
    decimal = deg + mins / 60.0
    if hemi in ('S', 'W'):
        decimal = -decimal
    return decimal


# ---------------------------------------------------------------------------
# Date / time parsing
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

def _parse_datetime(raw: str):
    """
    Parse '21 June 2026 13:00' -> ('2026-06-21', 13).
    Returns (date_str, hour) or (None, None).
    """
    m = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})\s+(\d{1,2}):(\d{2})', raw)
    if not m:
        return None, None
    day   = int(m.group(1))
    month = _MONTHS.get(m.group(2).lower())
    year  = int(m.group(3))
    hour  = int(m.group(4))
    if month is None:
        return None, None
    return f"{year}-{month:02d}-{day:02d}", hour


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def _extract_text(pdf_path: str) -> str:
    """Extract all text from the PDF (all pages joined)."""
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3)
            if t:
                parts.append(t)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _search(pattern: str, text: str, default: str = "") -> str:
    """Return first capture group or *default*."""
    m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else default


def _first_line(s: str) -> str:
    """Return only the first non-empty line of *s*."""
    for line in s.splitlines():
        line = line.strip()
        if line:
            return line
    return s.strip()


# ---------------------------------------------------------------------------
# Turnpoint table parser
# ---------------------------------------------------------------------------

def _parse_turnpoints(text: str) -> list:
    """
    Parse task turnpoint rows from the extracted text.

    Expected row format (may be all on one line or wrapped):
        Castellucio  4216ft  N42°48.702  E013°12.540  R1=5000m, θ=180°

    Returns list of dicts with keys:
        name, lat, lon, radius_m, angle_deg, sector_type, sector_dir
    """
    tps = []

    # Regex breakdown:
    #   (.+?)          TP name (non-greedy, stops before elevation)
    #   \s+\d+ft       elevation in feet (anchor — distinguishes data rows from headers)
    #   \s+([NS]...)   latitude in DDM
    #   \s+([EW]...)   longitude in DDM
    #   \s+R1=(\d+)m   sector radius
    #   ,?\s*\S+=(\d+)° sector angle (θ or "theta"; any char between comma and =)
    pattern = re.compile(
        r'(.+?)'                        # TP name
        r'\s+\d+ft'                     # elevation (consume but don't capture)
        r'\s+([NS]\d+[°\u00b0][\d.]+)' # latitude DDM
        r'\s+([EW]\d+[°\u00b0][\d.]+)' # longitude DDM
        r'\s+R1=(\d+)m'                # sector radius
        r',?\s*\S+=(\d+)[°\u00b0]',    # sector angle
        re.MULTILINE,
    )

    for m in pattern.finditer(text):
        name   = m.group(1).strip()
        lat    = _ddm_to_decimal(m.group(2))
        lon    = _ddm_to_decimal(m.group(3))
        radius = int(m.group(4))
        angle  = int(m.group(5))

        # Reject header row artefacts (name would be "Name" or "Task")
        if name.lower() in ("name", "task", "task name"):
            continue

        tps.append({
            "name":        name,
            "lat":         lat,
            "lon":         lon,
            "radius_m":    radius,
            "angle_deg":   angle,
            "sector_type": 0,
            "sector_dir":  0,
        })

    return tps


# ---------------------------------------------------------------------------
# Weather parser
# ---------------------------------------------------------------------------

def _parse_weather(text: str) -> dict:
    wx = {
        "wind_dir_deg":    0.0,
        "wind_speed_kts":  0.0,
        "cloud_base_ft":   3000.0,
        "overdevelopment": 0.0,
        "thermal_strength": 2,
        "thermal_activity": 3,
    }

    # Grab the weather report value (everything after the label, up to next label or end)
    wr = _search(r'Weather report\s+(.+?)(?=\n[A-Z]|\Z)', text)
    if not wr:
        wr = text  # scan full text as fallback

    # Wind: "Wind 235° at 16kts" or just "235° at 16kts"
    m = re.search(r'(?:Wind\s+)?(\d+(?:\.\d+)?)[°\u00b0]\s+at\s+(\d+(?:\.\d+)?)kts', wr, re.IGNORECASE)
    if m:
        wx["wind_dir_deg"]   = float(m.group(1))
        wx["wind_speed_kts"] = float(m.group(2))

    # Cloud base: "Cloud base 4921ft"
    m = re.search(r'Cloud\s+base\s+(\d+)ft', wr, re.IGNORECASE)
    if m:
        wx["cloud_base_ft"] = float(m.group(1))

    return wx


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_task_pdf(pdf_path: str) -> dict:
    """
    Parse a Condor task briefing PDF.

    Returns a task dict with these keys:
        landscape           Condor internal name, e.g. "Centro_Italia3"
        condor_version      int, default 3100
        task_date           "YYYY-MM-DD"
        start_time          int hour (24h)
        start_time_window   int minutes
        race_start_delay_mins int minutes
        aircraft            Condor internal name, e.g. "DuoDiscusXL"
        skin                "Default"
        start_type          "airborne" | "gate" | "line"
        airport_name        Physical launch airfield name (from "Airborne over X")
        start_height_m      int, metres AGL
        min_finish_height_m int, metres
        max_start_speed_kts int, knots
        weather             dict
        turnpoints          list of TP dicts (start gate through finish)
        description         str
    """
    text = _extract_text(pdf_path)

    task = {}

    # ---- Description (race title) ----------------------------------------
    m = re.search(r'(SGC\s+\S+\s+\d{4}\s+Race\s+\d+)', text)
    task["description"] = m.group(1).strip() if m else ""

    # ---- Landscape --------------------------------------------------------
    raw_ls = _first_line(_search(r'Landscape\s+(.+)', text))
    task["landscape"] = _normalise_landscape(raw_ls)

    # ---- Date / time ------------------------------------------------------
    # "Virtual date and time" (label may wrap: "Virtual date and\ntime")
    raw_dt = _search(r'Virtual date and\s+(\d+\s+\w+\s+\d{4}\s+\d+:\d+)', text)
    task["task_date"], task["start_time"] = _parse_datetime(raw_dt)
    task["condor_version"] = 3100

    # ---- Timing -----------------------------------------------------------
    # Start time window (minutes)
    raw_stw = _search(r'Start time window\s+(\d+)', text)
    task["start_time_window"] = int(raw_stw) if raw_stw else 0

    # Delay before race start (minutes) — label may wrap across line
    raw_del = _search(r'Delay before race\s+(\d+)', text)
    task["race_start_delay_mins"] = int(raw_del) if raw_del else 5

    # ---- Aircraft ---------------------------------------------------------
    raw_ac = _first_line(_search(r'Aircraft\s+(.+)', text))
    task["aircraft"] = _normalise_aircraft(raw_ac)
    task["skin"]     = "Default"

    # ---- Start type and airport -------------------------------------------
    # "Start type   Airborne, 2526ft over Castellucio"
    raw_st = _first_line(_search(r'Start type\s+(.+)', text))
    task["start_type"] = "airborne"  # all known races are airborne
    m = re.search(r'over\s+(.+)', raw_st, re.IGNORECASE)
    task["airport_name"] = m.group(1).strip() if m else ""

    # ---- Heights ----------------------------------------------------------
    # "Max start height  3282ft (1000m) over Rieti, 4594ft QNH"
    raw_msh = _first_line(_search(r'Max start height\s+(.+)', text))
    m = re.search(r'\((\d+)m\)', raw_msh)
    task["start_height_m"] = int(m.group(1)) if m else 1000

    # "Min finish height  0ft (0m)"
    raw_mfh = _first_line(_search(r'Min finish height\s+(.+)', text))
    m = re.search(r'\((\d+)m\)', raw_mfh)
    task["min_finish_height_m"] = int(m.group(1)) if m else 0

    # ---- Max start speed --------------------------------------------------
    # Notes: "Max start speed 81kts ground speed."
    raw_notes = _search(r'Notes\s+(.+?)(?=\n[A-Z]|\Z)', text)
    m = re.search(r'(\d+)kts\s+ground\s+speed', raw_notes, re.IGNORECASE)
    task["max_start_speed_kts"] = int(m.group(1)) if m else 81

    # ---- Airspace penalty -------------------------------------------------
    # "Ignore airspace" in notes -> penalty 0, else 100
    ignore_airspace = bool(re.search(r'ignore airspace', raw_notes, re.IGNORECASE))
    task["_ignore_airspace"] = ignore_airspace  # consumed by build_fpl

    # ---- Weather ----------------------------------------------------------
    task["weather"] = _parse_weather(text)

    # ---- Turnpoints -------------------------------------------------------
    task["turnpoints"] = _parse_turnpoints(text)

    return task


# ---------------------------------------------------------------------------
# CLI: dump parsed fields for debugging
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print(f"Usage: python pdf_parser.py <task.pdf>")
        sys.exit(1)

    result = parse_task_pdf(sys.argv[1])
    # Remove internal keys before printing
    out = {k: v for k, v in result.items() if not k.startswith("_")}
    print(json.dumps(out, indent=2))
