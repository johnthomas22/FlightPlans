#!/usr/bin/env python3
"""
condor_fpl_gen.py
-----------------
Generate a Condor 2/3 .fpl flight plan file from a task briefing PDF or a
task JSON file.

Usage:
    # From a task briefing PDF (requires: pip install pdfplumber)
    python condor_fpl_gen.py --pdf "Spring2026#5.pdf"
    python condor_fpl_gen.py --pdf "Spring2026#5.pdf" --output Race5.fpl

    # From a filled-in task JSON
    python condor_fpl_gen.py --task my_task.json --output MyRace.fpl

    # Print a blank task JSON template
    python condor_fpl_gen.py --template > my_task.json

    # Interactive prompt mode
    python condor_fpl_gen.py --interactive

PDF mode requires pdfplumber and a directory of existing .fpl files for
coordinate lookup (see --fpl-dir).  The default lookup directory is:
    %USERPROFILE%\\Documents\\Condor3\\FlightPlans

FPL structure
-------------
TP0  Airport (physical launch airfield, TPAirport=1)
TP1  Start gate / cylinder  (first TP from task table)
TP2+ Waypoints and finish   (remaining TPs from task table)

The airport TP (TP0) is specified via the JSON "airport_tp" key or, in PDF
mode, is derived from "Airborne over <name>" in the briefing sheet.
If "airport_tp" is absent from the JSON, TP0 is a clone of TP1 (legacy
behaviour compatible with older task JSON files).
"""

import argparse
import json
import math
import os
import random
import datetime
import sys


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

DEFAULT_FPL_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "Condor3", "FlightPlans"
)

DEFAULT_CUP_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "Condor3", "Turnpoints"
)

DEFAULT_XCSOAR_DIR = os.path.join(
    os.path.expanduser("~"), "Documents", "XCSoarData", "tasks"
)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

KTS_TO_MS  = 0.514444
KTS_TO_KMH = 1.852
FT_TO_M    = 0.3048


def kts_to_ms(kts):
    return kts * KTS_TO_MS


def ft_to_m(ft):
    return ft * FT_TO_M


def date_to_excel(date_str):
    """Convert ISO date string (YYYY-MM-DD) to Excel serial date number."""
    d = datetime.date.fromisoformat(date_str)
    origin = datetime.date(1899, 12, 30)
    return (d - origin).days


def start_type_code(name):
    """Map human-readable start type to Condor integer code."""
    return {"gate": 0, "line": 1, "airborne": 2, "tow": 2}.get(
        name.lower(), 2
    )


def cloud_base_to_inversion(cloud_base_ft):
    return round(ft_to_m(cloud_base_ft))


def _fmt_latlon(lat, lon) -> str:
    """Format decimal lat/lon as a readable string, e.g. '42.8117°N  013.2090°E'."""
    if lat is None or lon is None:
        return "—"
    lat_h = "N" if lat >= 0 else "S"
    lon_h = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}\u00b0{lat_h}  {abs(lon):>8.4f}\u00b0{lon_h}"


# ---------------------------------------------------------------------------
# FPL builder
# ---------------------------------------------------------------------------

def build_fpl(task: dict) -> str:
    """
    Build and return FPL file content as a string (CRLF line endings applied
    by the caller when writing).

    Expected task keys
    ------------------
    Mandatory:
        landscape, task_date, start_time, aircraft, weather, turnpoints

    Optional (with defaults):
        airport_tp          dict with name/x/y/z for the launch airfield (TP0).
                            If absent, TP0 clones turnpoints[0] (legacy mode).
        condor_version      int,  default 3100
        skin                str,  default "Default"
        start_type          str,  default "airborne"
        start_height_m      int,  default 1000
        min_finish_height_m int,  default 0
        max_start_speed_kts int,  default 81
        start_time_window   int minutes, default 0
        race_start_delay_mins int minutes, default 5
        penalties           dict
        description         str
        _ignore_airspace    bool  (set by pdf_parser; makes airspace penalty 0)
    """

    lines = []

    # --- [Version] ---
    ver = task.get("condor_version", 3100)
    lines += [
        "[Version]",
        f"Condor version={ver}",
        "",
    ]

    # --- Build full TP list -----------------------------------------------
    # TP0 = airport (physical launch airfield), TPAirport=1, wide sector
    # TP1 = start gate (first entry from task table), sector from task sheet
    # TP2..N = remaining TPs (waypoints + finish), sector from task sheet

    tps_in = task["turnpoints"]   # start_gate, wp2, ..., finish

    if "airport_tp" in task:
        apt_raw = dict(task["airport_tp"])
    else:
        # Legacy: clone the first task TP as the airport
        apt_raw = dict(tps_in[0])

    # Airport TP always has a fixed wide sector (Condor standard)
    airport_tp = {
        "name":        apt_raw["name"],
        "x":           apt_raw["x"],
        "y":           apt_raw["y"],
        "z":           apt_raw["z"],
        "is_airport":  True,
        "radius_m":    3000,
        "angle_deg":   90,
        "sector_type": 0,
        "sector_dir":  0,
    }

    full_tps = [airport_tp] + [dict(tp) for tp in tps_in]
    count    = len(full_tps)

    # --- [Task] ---
    landscape = task.get("landscape", "Centro_Italia3")
    lines += [
        "[Task]",
        f"Landscape={landscape}",
        f"Count={count}",
    ]

    for i, tp in enumerate(full_tps):
        is_airport = 1 if tp.get("is_airport", False) else 0
        lines += [
            f"TPName{i}={tp['name']}",
            f"TPPosX{i}={tp['x']}",
            f"TPPosY{i}={tp['y']}",
            f"TPPosZ{i}={tp['z']}",
            f"TPAirport{i}={is_airport}",
            f"TPSectorType{i}={tp.get('sector_type', 0)}",
            f"TPSectorDirection{i}={tp.get('sector_dir', 0)}",
            f"TPRadius{i}={tp.get('radius_m', 3000)}",
            f"TPAngle{i}={tp.get('angle_deg', 180)}",
            f"TPAltitude{i}=1500",
            f"TPWidth{i}=0",
            f"TPHeight{i}=10000",
            f"TPAzimuth{i}=0",
        ]

    lines += [
        "PZCount=0",
        "DisabledAirspaces=",
        "",
    ]

    # --- [Weather] ---
    wx             = task.get("weather", {})
    wind_dir       = wx.get("wind_dir_deg", 0)
    wind_speed_ms  = kts_to_ms(wx.get("wind_speed_kts", 0))
    inversion_m    = cloud_base_to_inversion(wx.get("cloud_base_ft", 4921))
    overdevelop    = wx.get("overdevelopment", 0.0)
    th_strength    = wx.get("thermal_strength", 2)
    th_activity    = wx.get("thermal_activity", 3)

    lines += [
        "[Weather]",
        "RandomizeWeatherOnEachFlight=0",
        "WZCount=1",
        "",
        "[WeatherZone0]",
        "Name=Base",
        "PointCount=0",
        "MoveDir=0",
        "MoveSpeed=0",
        "BorderWidth=0",
        f"WindDir={wind_dir}",
        f"WindSpeed={wind_speed_ms:.6f}",
        "WindUpperSpeed=0",
        "WindDirVariation=1",
        "WindSpeedVariation=1",
        "WindTurbulence=2",
        "ThermalsTemp=22",
        "ThermalsTempVariation=1",
        "ThermalsDew=10",
        f"ThermalsStrength={th_strength}",
        "ThermalsStrengthVariation=1",
        f"ThermalsInversionheight={inversion_m}",
        f"ThermalsOverdevelopment={overdevelop}",
        "ThermalsWidth=2",
        "ThermalsWidthVariation=1",
        f"ThermalsActivity={th_activity}",
        "ThermalsActivityVariation=1",
        "ThermalsTurbulence=2",
        "ThermalsFlatsActivity=2",
        "ThermalsStreeting=0",
        "ThermalsBugs=2",
        "WavesStability=5",
        "WavesMoisture=8",
        "HighCloudsCoverage=2",
        "",
    ]

    # --- [Plane] ---
    aircraft = task.get("aircraft", "StdCirrus")
    skin     = task.get("skin", "Default")

    lines += [
        "[Plane]",
        "Class=All",
        f"Name={aircraft}",
        f"Skin={skin}",
        "Water=0",
        "FixedMass=0",
        "CGBias=0",
        "Seat=1",
        "Bugwipers=0",
        "",
    ]

    # --- [GameOptions] ---
    # Use "or default" rather than dict.get(key, default) so that an
    # explicitly-stored None (from a failed PDF parse) still falls back.
    task_date_serial = date_to_excel(task.get("task_date") or "2026-06-21")
    start_time       = task.get("start_time") or 12
    start_height_m   = task.get("start_height_m") or 1000

    stw_mins  = task.get("start_time_window") or 0
    stw_hours = stw_mins / 60.0

    delay_mins  = task.get("race_start_delay_mins") or 5
    delay_hours = delay_mins / 60.0

    max_speed_kts = task.get("max_start_speed_kts") or 81
    max_speed_kmh = round(max_speed_kts * KTS_TO_KMH)

    st_code = start_type_code(task.get("start_type", "airborne"))

    pen = task.get("penalties", {})
    airspace_penalty = 0 if task.get("_ignore_airspace") else pen.get("airspace", 100)

    lines += [
        "[GameOptions]",
        f"TaskDate={task_date_serial}",
        f"StartTime={start_time}",
        f"StartTimeWindow={stw_hours:.17f}",
        f"RaceStartDelay={delay_hours:.17f}",
        "AATTime=3",
        "IconsVisibleRange=20",
        "ThermalHelpersRange=0",
        "TurnpointHelpersRange=0",
        "AAT=0",
        "AllowBugwipers=1",
        "AllowPDA=1",
        "AllowRealtimeScoring=1",
        "AllowExternalView=1",
        "AllowPadlockView=1",
        "AllowSmoke=1",
        "AllowPlaneRecovery=0",
        "AllowHeightRecovery=0",
        "AllowMidairCollisionRecovery=0",
        "AllowInstructorActions=0",
        f"PenaltyCloudFlying={pen.get('cloud_flying', 100)}",
        f"PenaltyPlaneRecovery={pen.get('plane_recovery', 100)}",
        f"PenaltyHeightRecovery={pen.get('height_recovery', 100)}",
        "PenaltyWrongWindowEnterance=100",
        "PenaltyWindowCollision=100",
        f"PenaltyAirspaceEnterance={airspace_penalty}",
        "PenaltyPenaltyZoneEnterance=100",
        "PenaltyThermalHelpers=0",
        f"MaxStartGroundSpeed={max_speed_kmh}",
        "PenaltyStartSpeed=1",
        "PenaltyHighStart=1",
        "PenaltyLowFinish=1",
        f"RandSeed={random.randint(0, 2147483647)}",
        f"StartType={st_code}",
        f"StartHeight={start_height_m}",
        "BreakProb=0",
        "RopeLength=50",
        "MaxWingLoading=0",
        "MaxTeams=0",
        "AcroFlight=0",
        "",
    ]

    # --- [Description] ---
    desc = task.get("description", "")
    lines += [
        "[Description]",
        f"Text={desc}",
        "",
    ]

    return "\r\n".join(lines)


# ---------------------------------------------------------------------------
# XCSoar task builder
# ---------------------------------------------------------------------------

def build_xcsoar_tsk(task: dict) -> str:
    """
    Build an XCSoar .tsk XML racing task file from the task dict.

    Requires lat/lon on each turnpoint (populated by pdf_to_task from the PDF
    or CUP file).  Returns the file content as a UTF-8 string (LF endings).

    Task structure
    --------------
    turnpoints[0]    → <Point type="Start">
    turnpoints[1:-1] → <Point type="Turn">   (one per intermediate waypoint)
    turnpoints[-1]   → <Point type="Finish">

    All observation zones are mapped to XCSoar Cylinder type using the
    radius_m from each turnpoint.
    """
    tps = task.get("turnpoints", [])
    if not tps:
        return ""

    def _escape(s: str) -> str:
        return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")

    lines = [
        '<?xml version="1.0"?>',
        '<Task type="RT" start_requires_arm="1">',
    ]

    for i, tp in enumerate(tps):
        if i == 0:
            pt_type = "Start"
        elif i == len(tps) - 1:
            pt_type = "Finish"
        else:
            pt_type = "Turn"

        lat    = tp.get("lat", 0.0)
        lon    = tp.get("lon", 0.0)
        name   = _escape(tp["name"])
        radius = tp.get("radius_m", 3000)

        lines += [
            f'  <Point type="{pt_type}">',
            f'    <Waypoint name="{name}" id="{i}" comment="">',
            f'      <Location longitude="{lon:.6f}" latitude="{lat:.6f}"/>',
            f'    </Waypoint>',
            f'    <ObservationZone type="Cylinder" radius="{radius}"/>',
            f'  </Point>',
        ]

    lines.append('</Task>')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Task distance calculator
# ---------------------------------------------------------------------------

def calc_task_distance(tps: list) -> float:
    """Return task distance in km from the user TP list (not including TP0)."""
    dist = 0.0
    for i in range(len(tps) - 1):
        dx = tps[i + 1]["x"] - tps[i]["x"]
        dy = tps[i + 1]["y"] - tps[i]["y"]
        dist += math.sqrt(dx ** 2 + dy ** 2)
    return dist / 1000.0


# ---------------------------------------------------------------------------
# Flight strategy generator
# ---------------------------------------------------------------------------

def _compass(bearing_deg: float) -> str:
    """Return a compass point string for *bearing_deg* (0 = N, 90 = E)."""
    dirs = ["N","NNE","NE","ENE","E","ESE","SE","SSE",
            "S","SSW","SW","WSW","W","WNW","NW","NNW"]
    return dirs[round(bearing_deg / 22.5) % 16]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(min(1.0, math.sqrt(a)))


def _true_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial true bearing in degrees (0 = N, clockwise) from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def generate_strategy(task: dict) -> str:
    """
    Return a plain-text flight strategy for *task*.

    Uses TP lat/lon (preferred) or Condor XY for bearing/distance, weather,
    and a built-in glider polar table to produce:
      - Per-leg bearing, distance and wind component analysis
      - McCready-adjusted cruise speed recommendations
      - Routing notes (cloud streets, ridge lift, drift offset)
      - Thermal exit altitude targets for each leg
    """
    W = 66  # output width

    def rule(ch="─"):
        return ch * W

    # ------------------------------------------------------------------ data
    wx          = task.get("weather", {})
    wind_dir    = float(wx.get("wind_dir_deg",   0))
    wind_kts    = float(wx.get("wind_speed_kts", 0))
    cloud_ft    = float(wx.get("cloud_base_ft",  3000))
    cloud_m     = round(cloud_ft * FT_TO_M)
    th_strength = int(wx.get("thermal_strength", 2))   # 1–5
    th_activity = int(wx.get("thermal_activity", 3))   # 1–5
    aircraft    = task.get("aircraft", "?")
    tps         = task.get("turnpoints", [])

    # Estimated thermal climb rate (m/s) per strength level
    CLIMB_MS = {1: 0.8, 2: 1.5, 3: 2.5, 4: 3.5, 5: 5.0}
    climb_ms = CLIMB_MS.get(th_strength, 2.0)

    # Glider polar table: (best_glide_ratio, best_ld_kts, nominal_cruise_kts)
    POLARS = {
        "StdCirrus":   (38, 59,  80),
        "LS4":         (40, 60,  85),
        "LS8":         (44, 65,  90),
        "Discus2":     (43, 62,  88),
        "ASW28":       (46, 65,  92),
        "Nimbus4":     (56, 70, 100),
        "Ventus2":     (50, 68,  95),
        "DuoDiscus":   (40, 60,  85),
        "DuoDiscusXL": (42, 62,  88),
        "DuoDiscusT":  (40, 60,  85),
        "Blanik":      (28, 55,  70),
    }
    best_glide, best_ld_kts, base_cruise_kts = POLARS.get(aircraft, (38, 59, 80))

    # McCready scaling: stronger thermals → higher cruise speed
    MC_FACTOR = {1: 0.87, 2: 0.93, 3: 1.00, 4: 1.07, 5: 1.15}
    cruise_kts = round(base_cruise_kts * MC_FACTOR.get(th_strength, 1.0))
    cruise_ms  = cruise_kts * KTS_TO_MS

    # wind_to_dir: direction wind blows TOWARDS (opposite of the FROM bearing)
    wind_to_dir  = (wind_dir + 180) % 360
    wind_from_cmp = _compass(wind_dir)       # compass label for windward side
    streets_likely = wind_kts > 12 and th_strength >= 2

    out = []

    # ---------------------------------------------------------------- header
    desc  = task.get("description", "")
    title = f"FLIGHT STRATEGY  —  {desc}" if desc else "FLIGHT STRATEGY"
    out.append(rule("═"))
    out.append(title)
    out.append(rule("═"))

    # ---------------------------------------------------------------- conditions
    out.append("\nCONDITIONS")
    out.append(rule())
    out.append(f"  Wind:       {wind_dir:.0f}° @ {wind_kts:.0f} kts  ({_compass(wind_dir)})")
    out.append(f"  Cloud base: {cloud_ft:.0f} ft  ({cloud_m} m)")
    out.append(f"  Thermals:   Strength {th_strength}/5,  Activity {th_activity}/5"
               f"  (~{climb_ms:.1f} m/s climbs)")
    out.append(f"  Aircraft:   {aircraft}  (best glide ~{best_glide}:1)")

    # ---------------------------------------------------------------- turnpoints
    out.append("\nTURNPOINTS")
    out.append(rule())
    out.append(f"  {'#':<4} {'Name':<24} {'Coordinates':<30} {'Radius':>7}")
    out.append("  " + rule())
    for i, tp in enumerate(tps):
        label = "S" if i == 0 else ("F" if i == len(tps) - 1 else str(i + 1))
        latlon = _fmt_latlon(tp.get("lat"), tp.get("lon"))
        radius_str = f"{tp.get('radius_m', 3000)} m"
        out.append(f"  {label:<4} {tp['name']:<24} {latlon:<30} {radius_str:>7}")

    # ---------------------------------------------------------------- cruise speed
    out.append("\nSUGGESTED CRUISE SPEED")
    out.append(rule())
    out.append(f"  Inter-thermal:  {cruise_kts} kts  "
               f"(McCready {th_strength} — thermal strength {th_strength}/5)")
    out.append(f"  Headwind legs:  {cruise_kts + 5}–{cruise_kts + 10} kts  "
               f"(fly faster to minimise time fighting headwind)")
    out.append(f"  Tailwind legs:  {max(cruise_kts - 5, best_ld_kts)}–{cruise_kts} kts  "
               f"(slower — ground speed is already high)")

    # ---------------------------------------------------------------- legs
    if len(tps) < 2:
        out.append("\n(Not enough turnpoints for leg analysis.)")
        return "\n".join(out)

    out.append("\nLEG-BY-LEG ANALYSIS")
    out.append(rule())
    out.append(
        f"  {'#':<3} {'From':<22} {'To':<22} {'Dist':>6}  "
        f"{'Bearing':>8}  {'Wind':>8}  Assessment"
    )
    out.append("  " + rule())

    leg_data = []
    for i in range(len(tps) - 1):
        a, b = tps[i], tps[i + 1]

        # Prefer lat/lon for accurate true bearing; fall back to Condor XY
        if a.get("lat") is not None and b.get("lat") is not None:
            dist_km = _haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
            dist_m  = dist_km * 1000.0
            bearing = _true_bearing(a["lat"], a["lon"], b["lat"], b["lon"])
        else:
            dx      = b["x"] - a["x"]
            dy      = b["y"] - a["y"]
            dist_m  = math.sqrt(dx ** 2 + dy ** 2)
            dist_km = dist_m / 1000.0
            bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360

        if dist_m == 0:
            out.append(
                f"  {i+1:<3} {a['name']:<22} {b['name']:<22} "
                f"  !! identical coordinates — likely a database lookup error"
            )
            continue

        # Wind components using true bearing — no Condor axis assumptions needed.
        # angle: how far the leg direction is ahead of the wind-to direction.
        angle         = math.radians((bearing - wind_to_dir + 360) % 360)
        tailwind_kts  =  wind_kts * math.cos(angle)   # +ve = tailwind
        crosswind_kts =  wind_kts * math.sin(angle)   # +ve = wind from left

        if tailwind_kts > 5:
            assess = "Tailwind  — favourable"
        elif tailwind_kts < -5:
            assess = "Headwind  — difficult"
        else:
            assess = "Crosswind — neutral"

        out.append(
            f"  {i+1:<3} {a['name']:<22} {b['name']:<22} "
            f"{dist_km:>5.1f}km  {_compass(bearing)} {bearing:>3.0f}°  "
            f"{tailwind_kts:>+6.0f} kts  {assess}"
        )
        leg_data.append({
            "idx":           i + 1,
            "from":          a["name"],
            "to":            b["name"],
            "dist_m":        dist_m,
            "dist_km":       dist_km,
            "bearing":       bearing,
            "tailwind_kts":  tailwind_kts,
            "crosswind_kts": crosswind_kts,
        })

    # ---------------------------------------------------------------- routing notes
    out.append("\nROUTING NOTES")
    out.append(rule())

    total_tw   = sum(d["tailwind_kts"] * d["dist_km"] for d in leg_data)
    total_dist = sum(d["dist_km"] for d in leg_data)
    avg_tw     = total_tw / total_dist if total_dist else 0

    if avg_tw > 3:
        out.append("  Overall: Downwind-dominant task. Expect faster-than-nominal task times.")
        out.append("           Build height early — a strong final glide is achievable.")
    elif avg_tw < -3:
        out.append("  Overall: Headwind-dominant task. Expect slower-than-nominal task times.")
        out.append("           Stay high, fly fast, and minimise detours from the optimal track.")
    else:
        out.append("  Overall: Wind effects roughly balanced across the task.")
    out.append("")

    for d in leg_data:
        tw  = d["tailwind_kts"]
        xw  = d["crosswind_kts"]   # signed: +ve = wind from left
        brg = d["bearing"]
        notes = []

        if tw > 8:
            notes.append(
                "Strong tailwind — use dolphin technique through weaker thermals; "
                "accept lower exit heights to maintain ground speed."
            )
        elif tw < -8:
            notes.append(
                "Strong headwind — fly faster, stay on the optimal track, and "
                "only circle in strong climbs (>McCready setting)."
            )
        elif abs(xw) > 10:
            # Upwind side: where the wind comes FROM relative to the track.
            # +ve xw = wind from left → upwind offset is LEFT (brg-90)
            # -ve xw = wind from right → upwind offset is RIGHT (brg+90)
            upwind_side = _compass((brg - 90 if xw > 0 else brg + 90) % 360)
            notes.append(
                f"Crosswind ~{abs(xw):.0f} kts — offset slightly to the {upwind_side} "
                f"(upwind) side of the direct track to compensate for drift "
                f"and find better lift along the windward slope."
            )

        if streets_likely:
            alignment = abs(((wind_to_dir - brg) + 180) % 360 - 180)
            if alignment < 35:
                notes.append(
                    "Wind broadly aligned with this leg — cloud streets are likely. "
                    "Look for a street and dolphin straight through rather than circling."
                )

        if tw < -3:
            notes.append(
                f"Headwind leg: look for orographic lift on the "
                f"{wind_from_cmp} (windward) side of ridges and high ground."
            )

        if not notes:
            notes.append(
                "Standard thermal task. Follow cloud shadows and "
                "look for blue thermals near sun-facing slopes."
            )

        out.append(f"  Leg {d['idx']} ({d['from']} → {d['to']}):")
        for note in notes:
            out.append(f"    • {note}")

    # ---------------------------------------------------------------- thermal exit altitudes
    out.append("\nTHERMAL EXIT ALTITUDES  (height above destination TP)")
    out.append(rule())
    out.append(
        f"  Cloud base {cloud_m} m  |  best glide {best_glide}:1  |  cruise {cruise_kts} kts"
    )
    out.append("")
    out.append(
        f"  {'#':<3} {'Destination':<22} {'Dist':>6}  "
        f"{'Minimum':>9}  {'Target':>9}  Note"
    )
    out.append("  " + rule())

    ARRIVAL_M = 300   # minimum arrival margin above TP (m)
    BUFFER    = 1.30  # target = minimum × buffer

    for d in leg_data:
        tw_ms     = d["tailwind_kts"] * KTS_TO_MS
        # Wind-adjusted effective glide ratio over the ground
        eff_glide = best_glide * (1.0 + tw_ms / cruise_ms) if cruise_ms else best_glide
        eff_glide = max(eff_glide, best_glide * 0.25)   # sanity floor

        min_m = d["dist_m"] / eff_glide + ARRIVAL_M
        tgt_m = min(min_m * BUFFER, cloud_m - 200)
        min_m = round(min_m / 10) * 10
        tgt_m = round(tgt_m / 10) * 10

        if min_m > cloud_m:
            note = "⚠ may need intermediate thermal"
        elif tgt_m >= cloud_m - 250:
            note = "near cloud base"
        else:
            note = ""

        out.append(
            f"  {d['idx']:<3} {d['to']:<22} {d['dist_km']:>5.1f}km  "
            f"{min_m:>7} m    {tgt_m:>7} m  {note}"
        )

    out.append("")
    out.append(rule("═"))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# PDF pipeline
# ---------------------------------------------------------------------------

def _load_database(fpl_dir: str, cup_dir: str = None):
    from tp_database import TurnpointDatabase
    db = TurnpointDatabase()
    n = db.load_fpl_dir(fpl_dir)
    if n == 0:
        print(f"Warning: no TPs loaded from '{fpl_dir}'. "
              f"Check --fpl-dir points to a folder of .fpl files.", file=sys.stderr)
    else:
        print(f"[db] Loaded {n} unique turnpoints from {fpl_dir}")

    # Derive CUP dir from FPL dir if not specified (sibling 'Turnpoints' folder)
    if cup_dir is None:
        cup_dir = os.path.join(os.path.dirname(fpl_dir), "Turnpoints")
    if os.path.isdir(cup_dir):
        for fname in sorted(os.listdir(cup_dir)):
            if fname.lower().endswith(".cup"):
                landscape = os.path.splitext(fname)[0]
                n_cup = db.load_cup(os.path.join(cup_dir, fname), landscape)
                print(f"[db] Loaded {n_cup} TPs from CUP: {fname}")
        db.build_transforms()
    else:
        print(f"[db] No CUP directory found at '{cup_dir}' — name-only lookup active",
              file=sys.stderr)

    return db


def _resolve_tp(db, landscape: str, name: str, kind: str,
                lat: float = None, lon: float = None):
    """Resolve a TP name (and optional lat/lon) to (x, y, z) or abort."""
    coords = db.resolve(landscape, name, lat=lat, lon=lon)
    if coords is None:
        print(
            f"\nERROR: Cannot find Condor XY coordinates for {kind} '{name}' "
            f"in landscape '{landscape}'.\n"
            f"  Add an .fpl file that uses this TP to the --fpl-dir directory,\n"
            f"  or ensure a .cup file for '{landscape}' is in the --cup-dir.\n",
            file=sys.stderr,
        )
        sys.exit(1)
    return coords


def pdf_to_task(pdf_path: str, fpl_dir: str, cup_dir: str = None) -> dict:
    """Parse *pdf_path* and resolve all TP coordinates. Returns a complete task dict."""
    from pdf_parser import parse_task_pdf

    task = parse_task_pdf(pdf_path)

    if not task.get("turnpoints"):
        print("ERROR: No turnpoints found in PDF. Check the file is a task briefing sheet.",
              file=sys.stderr)
        sys.exit(1)

    landscape = task["landscape"]
    db = _load_database(fpl_dir, cup_dir)

    # Resolve airport TP (launch airfield)
    # Try to get lat/lon from the CUP file to aid resolution.
    airport_name = task.get("airport_name", "")
    if not airport_name:
        print("ERROR: Could not determine launch airfield from PDF (expected 'Airborne over <name>').",
              file=sys.stderr)
        sys.exit(1)
    apt_lat = apt_lon = None
    apt_latlon = db.get_cup_latlon(landscape, airport_name)
    if apt_latlon:
        apt_lat, apt_lon, _ = apt_latlon
        ax, ay, az = _resolve_tp(db, landscape, airport_name, "airport",
                                 lat=apt_lat, lon=apt_lon)
    else:
        ax, ay, az = _resolve_tp(db, landscape, airport_name, "airport")
    task["airport_tp"] = {"name": airport_name, "x": ax, "y": ay, "z": az,
                          "lat": apt_lat, "lon": apt_lon}

    # Resolve each task TP — pass lat/lon from the PDF for geo-transform support
    resolved = []
    for tp in task["turnpoints"]:
        x, y, z = _resolve_tp(db, landscape, tp["name"], "turnpoint",
                               lat=tp.get("lat"), lon=tp.get("lon"))
        resolved.append({**tp, "x": x, "y": y, "z": z})
    task["turnpoints"] = resolved

    return task


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def interactive_mode():
    print("\nCondor FPL Generator — Interactive Mode")
    print("=" * 45)
    print("(Press Enter to accept defaults shown in brackets)\n")

    task = {}

    task["landscape"]              = input("Landscape name [Centro_Italia3]: ").strip() or "Centro_Italia3"
    task["task_date"]              = input("Task date YYYY-MM-DD [2026-06-21]: ").strip() or "2026-06-21"
    task["start_time"]             = int(input("Start time (hour, 24h) [13]: ").strip() or 13)
    task["start_time_window"]      = int(input("Start time window (mins) [5]: ").strip() or 5)
    task["race_start_delay_mins"]  = int(input("Delay before race start (mins) [5]: ").strip() or 5)
    task["aircraft"]               = input("Aircraft name [Blanik]: ").strip() or "Blanik"
    task["start_type"]             = input("Start type (airborne/gate/line) [airborne]: ").strip() or "airborne"
    task["start_height_m"]         = int(input("Start height m AGL [1000]: ").strip() or 1000)
    task["max_start_speed_kts"]    = int(input("Max start speed (kts) [81]: ").strip() or 81)

    print("\n--- Weather ---")
    wx = {}
    wx["wind_dir_deg"]    = float(input("Wind direction (°) [90]: ").strip() or 90)
    wx["wind_speed_kts"]  = float(input("Wind speed (kts) [13]: ").strip() or 13)
    wx["cloud_base_ft"]   = float(input("Cloud base (ft) [4921]: ").strip() or 4921)
    wx["overdevelopment"] = float(input("Overdevelopment 0.0–1.0 [0.0]: ").strip() or 0.0)
    wx["thermal_strength"]= int(input("Thermal strength 1–5 [2]: ").strip() or 2)
    wx["thermal_activity"]= int(input("Thermal activity 1–5 [3]: ").strip() or 3)
    task["weather"] = wx

    print("\n--- Airport (launch airfield) ---")
    apt_name = input("Airport/airfield name (TP0): ").strip()
    apt_x    = float(input("  X (m): ").strip())
    apt_y    = float(input("  Y (m): ").strip())
    apt_z    = float(input("  Z elevation (m): ").strip())
    task["airport_tp"] = {"name": apt_name, "x": apt_x, "y": apt_y, "z": apt_z}

    print("\n--- Task Turnpoints (start gate, waypoints, finish) ---")
    print("Enter each TP in order.  Press Enter (blank name) when done (need at least 2).\n")

    tps = []
    while True:
        idx   = len(tps)
        label = "Start gate (TP1)" if idx == 0 else f"TP{idx + 1} (blank to finish)"
        print(f"  {label}")
        name = input("    Name: ").strip()
        if not name and idx >= 2:
            break
        if not name:
            print("    (Need at least start gate + finish)")
            continue
        x      = float(input("    X (m): ").strip())
        y      = float(input("    Y (m): ").strip())
        z      = float(input("    Z elevation (m): ").strip())
        radius = int(input("    Sector radius (m) [3000]: ").strip() or 3000)
        angle  = int(input("    Sector angle (°) [180]: ").strip() or 180)
        tps.append({"name": name, "x": x, "y": y, "z": z,
                    "radius_m": radius, "angle_deg": angle,
                    "sector_type": 0, "sector_dir": 0})

    task["turnpoints"] = tps
    task["description"] = input("\nDescription []: ").strip()

    dist = calc_task_distance(tps)
    print(f"\nCalculated task distance: {dist:.1f} km")

    out_path = input("Output filename [output.fpl]: ").strip() or "output.fpl"
    content  = build_fpl(task)
    with open(out_path, "w", newline="\r\n") as f:
        f.write(content)
    print(f"Written: {out_path}")

    json_path = out_path.replace(".fpl", ".json")
    if input(f"Save task as JSON [{json_path}]? (y/n) [y]: ").strip().lower() != "n":
        with open(json_path, "w") as f:
            json.dump({k: v for k, v in task.items() if not k.startswith("_")}, f, indent=2)
        print(f"Task JSON saved: {json_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _write_xcsoar_tsk(task: dict, base_name: str, xcsoar_dir: str) -> None:
    """
    Write an XCSoar .tsk file to *xcsoar_dir* if the directory exists and all
    turnpoints have lat/lon.  Silently skips if preconditions are not met.
    Applies the same overwrite-protection rule as the CLI: stops with an error
    if the file already exists.
    """
    if not xcsoar_dir or not os.path.isdir(xcsoar_dir):
        return
    tps = task.get("turnpoints", [])
    if not all(tp.get("lat") is not None for tp in tps):
        print("[xcsoar] Skipping .tsk — lat/lon missing from one or more turnpoints.",
              file=sys.stderr)
        return
    tsk_path = os.path.join(xcsoar_dir, base_name + ".tsk")
    if os.path.exists(tsk_path):
        print(f"ERROR: XCSoar task file already exists: {tsk_path}\n"
              f"       Rename or delete it first.",
              file=sys.stderr)
        sys.exit(1)
    with open(tsk_path, "w", encoding="utf-8") as f:
        f.write(build_xcsoar_tsk(task))
    print(f"  XCSoar:   {tsk_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate a Condor 2/3 .fpl file from a task briefing PDF or task JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    src = parser.add_mutually_exclusive_group()
    src.add_argument("--pdf",         "-p", metavar="PDF",
                     help="Task briefing PDF to parse (requires pdfplumber)")
    src.add_argument("--task",        "-t", metavar="JSON",
                     help="Task JSON file")
    src.add_argument("--interactive", "-i", action="store_true",
                     help="Interactive prompt mode")
    src.add_argument("--template",          action="store_true",
                     help="Print a blank task JSON template to stdout and exit")

    parser.add_argument("--output",  "-o", metavar="FPL",
                        help="Output .fpl filename")
    parser.add_argument("--fpl-dir",        metavar="DIR",
                        default=DEFAULT_FPL_DIR,
                        help=f"Directory of .fpl files for TP coordinate lookup "
                             f"(default: {DEFAULT_FPL_DIR})")
    parser.add_argument("--cup-dir",        metavar="DIR",
                        default=None,
                        help=f"Directory of .cup landscape files for geo-transform "
                             f"(default: sibling 'Turnpoints' folder of --fpl-dir, "
                             f"or {DEFAULT_CUP_DIR})")
    parser.add_argument("--xcsoar-dir",     metavar="DIR",
                        default=DEFAULT_XCSOAR_DIR,
                        help=f"Directory to write XCSoar .tsk file alongside the .fpl "
                             f"(default: {DEFAULT_XCSOAR_DIR}; skip if directory absent)")

    args = parser.parse_args()

    # ---- Template ----------------------------------------------------------
    if args.template:
        template = {
            "landscape":    "Centro_Italia3",
            "condor_version": 3100,
            "task_date":    "2026-06-21",
            "start_time":   13,
            "start_time_window": 5,
            "race_start_delay_mins": 5,
            "aircraft":     "Blanik",
            "skin":         "Default",
            "start_type":   "airborne",
            "airport_tp": {
                "name": "Rieti",
                "x":    183917.75,
                "y":    229719.265625,
                "z":    389,
            },
            "start_height_m":     1000,
            "min_finish_height_m": 0,
            "max_start_speed_kts": 81,
            "weather": {
                "wind_dir_deg":    90,
                "wind_speed_kts":  13,
                "cloud_base_ft":   4921,
                "overdevelopment": 0.0,
                "thermal_strength": 2,
                "thermal_activity": 3,
            },
            "turnpoints": [
                {
                    "name": "Cittaducalepiazz",
                    "x": 175684.546875, "y": 224619.90625, "z": 478,
                    "radius_m": 5000, "angle_deg": 180,
                    "sector_type": 0, "sector_dir": 0,
                },
                {
                    "name": "Galleria S Rocco",
                    "x": 146981.578125, "y": 205843.515625, "z": 1314,
                    "radius_m": 3000, "angle_deg": 90,
                    "sector_type": 0, "sector_dir": 0,
                },
                {
                    "name": "Rieti",
                    "x": 183917.75, "y": 229719.265625, "z": 389,
                    "radius_m": 1000, "angle_deg": 180,
                    "sector_type": 0, "sector_dir": 0,
                },
            ],
            "penalties": {
                "cloud_flying": 100, "plane_recovery": 100,
                "height_recovery": 100, "airspace": 100,
            },
            "description": "SGC Spring 2026 Race 1",
        }
        print(json.dumps(template, indent=2))
        return

    # ---- Interactive -------------------------------------------------------
    if args.interactive:
        interactive_mode()
        return

    # ---- PDF mode ----------------------------------------------------------
    if args.pdf:
        task     = pdf_to_task(args.pdf, args.fpl_dir, args.cup_dir)
        content  = build_fpl(task)
        base     = os.path.splitext(os.path.basename(args.pdf))[0]
        out_path = args.output or (base + ".fpl")
        if os.path.exists(out_path):
            print(f"ERROR: Output file already exists: {out_path}\n"
                  f"       Rename or delete it first, or use --output to specify a different path.",
                  file=sys.stderr)
            sys.exit(1)
        with open(out_path, "w", newline="\r\n") as f:
            f.write(content)
        _write_xcsoar_tsk(task, base, args.xcsoar_dir)

        tps      = task["turnpoints"]
        dist     = calc_task_distance(tps)
        airport  = task.get("airport_tp", {})
        print(f"Generated: {out_path}")
        print(f"  Airport:  {airport.get('name', '?')}  "
              f"{_fmt_latlon(airport.get('lat'), airport.get('lon'))}")
        print(f"  Route:")
        for i, tp in enumerate(tps):
            label = "S" if i == 0 else ("F" if i == len(tps) - 1 else str(i + 1))
            print(f"    {label}  {tp['name']:<26}  {_fmt_latlon(tp.get('lat'), tp.get('lon'))}")
        print(f"  Distance: {dist:.1f} km")
        print(f"  Aircraft: {task.get('aircraft', '?')}")
        wx = task["weather"]
        print(f"  Wind:     {wx['wind_dir_deg']}° @ {wx['wind_speed_kts']}kts")
        print(f"  Landscape:{task['landscape']}")
        print()
        print(generate_strategy(task))
        return

    # ---- JSON mode ---------------------------------------------------------
    if args.task:
        with open(args.task) as f:
            task = json.load(f)

        content  = build_fpl(task)
        base     = os.path.splitext(os.path.basename(args.task))[0]
        out_path = args.output or (os.path.splitext(args.task)[0] + ".fpl")
        if os.path.exists(out_path):
            print(f"ERROR: Output file already exists: {out_path}\n"
                  f"       Rename or delete it first, or use --output to specify a different path.",
                  file=sys.stderr)
            sys.exit(1)
        with open(out_path, "w", newline="\r\n") as f:
            f.write(content)
        _write_xcsoar_tsk(task, base, args.xcsoar_dir)

        tps      = task["turnpoints"]
        dist     = calc_task_distance(tps)
        tp_names = " -> ".join(tp["name"] for tp in tps)
        print(f"Generated: {out_path}")
        print(f"  Route:    {tp_names}")
        print(f"  Distance: {dist:.1f} km")
        print(f"  Aircraft: {task.get('aircraft', '?')}")
        wx = task.get("weather", {})
        print(f"  Wind:     {wx.get('wind_dir_deg', '?')}° @ {wx.get('wind_speed_kts', '?')}kts")
        print()
        print(generate_strategy(task))
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
