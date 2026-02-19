# condor_fpl_gen

A Python utility to generate Condor 2/3 gliding simulator `.fpl` flight plan files from structured task sheet data.

## Background

[Condor](https://www.condorsoaring.com/) is a gliding simulator used by clubs for online racing. Race organisers publish a **task briefing sheet** before each race, specifying the landscape, turnpoints, aircraft, weather conditions, start rules, and timing. Currently, creating the corresponding `.fpl` file requires manual entry in Condor's task editor. This utility automates that process.

The `.fpl` format is a Windows INI-style text file with CRLF line endings, consisting of four sections: `[Task]`, `[Weather]`, `[Plane]`, and `[GameOptions]`.

## Files

| File | Purpose |
|------|---------|
| `condor_fpl_gen.py` | Main script — FPL builder, CLI, interactive mode |
| `task_template.json` | Generated via `--template` flag; fill in and pass to `--task` |

## Usage

```bash
# Print a task JSON template to fill in
python condor_fpl_gen.py --template > my_task.json

# Generate an FPL from a completed task JSON
python condor_fpl_gen.py --task my_task.json --output Race5.fpl

# Interactive prompt mode (no JSON needed)
python condor_fpl_gen.py --interactive
```

## Task JSON Structure

```json
{
    "landscape": "Centro_Italia3",
    "condor_version": 3100,
    "task_date": "2026-06-21",
    "start_time": 13,
    "start_time_window": 5,
    "race_start_delay_mins": 5,
    "aircraft": "Blanik",
    "skin": "Default",
    "start_type": "airborne",
    "start_height_m": 770,
    "min_finish_height_m": 0,
    "max_start_speed_kts": 81,
    "weather": {
        "wind_dir_deg": 90,
        "wind_speed_kts": 13,
        "cloud_base_ft": 4921,
        "overdevelopment": 0.0,
        "thermal_strength": 2,
        "thermal_activity": 3
    },
    "turnpoints": [
        {
            "name": "Castellucio",
            "x": 153585.34375,
            "y": 271711.1875,
            "z": 1288,
            "radius_m": 5000,
            "angle_deg": 180
        },
        {
            "name": "Fiastra lago",
            "x": 155340.046875,
            "y": 299042.96875,
            "z": 636,
            "radius_m": 3000,
            "angle_deg": 90
        },
        {
            "name": "Foligno",
            "x": 193817.03125,
            "y": 286183.3125,
            "z": 212,
            "radius_m": 1000,
            "angle_deg": 180
        }
    ],
    "description": "SGC Spring 2026 Race 5"
}
```

## Key Implementation Notes

### TP Duplication (Important)
The `.fpl` format always has the start turnpoint appear **twice** at the beginning of the TP list:
- **TP0** — the airfield/airport marker (`TPAirport=1`), with a wide sector (angle=90)
- **TP1** — the actual start cylinder (`TPAirport=0`), with the race sector settings

The script handles this automatically. The `turnpoints` array in the JSON should list each TP **once**, starting with the start/airport TP. Do not duplicate it in the JSON.

### Coordinates
Condor uses a **local landscape XY grid in metres**, not latitude/longitude. Coordinates vary per landscape and cannot be derived from lat/lon without a landscape-specific transformation. The task sheet provides lat/lon for reference, but the `.fpl` requires XY.

Currently, XY coordinates must be provided explicitly in the task JSON. See **Suggested Extensions** below for how to automate this.

### Conversions Applied
| Task Sheet Value | Conversion | FPL Field |
|-----------------|-----------|-----------|
| Wind speed (kts) | × 0.514444 | `WindSpeed` (m/s) |
| Cloud base (ft) | × 0.3048 | `ThermalsInversionheight` (m) |
| Max start speed (kts) | × 1.852 | `MaxStartGroundSpeed` (km/h) |
| Task date | Excel serial date | `TaskDate` |
| Race delay (mins) | ÷ 60 | `RaceStartDelay` (fractional hours) |

### Start Types
| Task Sheet | `start_type` value | Condor code |
|-----------|-------------------|-------------|
| Airborne / self-launch | `"airborne"` | `2` |
| Tow | `"tow"` | `2` |
| Gate / cylinder | `"gate"` | `0` |
| Line | `"line"` | `1` |

---

## Suggested Extensions for Claude Code

These are the natural next steps, roughly in priority order:

### 1. Turnpoint Database (highest priority)
The biggest friction point is manually looking up XY coordinates. Condor stores turnpoints in `.cup` (SeeYou) format files inside each landscape folder, typically at:

```
C:\Condor\Landscapes\<LandscapeName>\<LandscapeName>.cup
```

**Task:** Build a `tp_database.py` module that:
- Parses `.cup` files to extract TP name, lat, lon, elevation
- Loads pre-exported XY coordinate data (see note below) into a SQLite database or JSON lookup
- Provides a `resolve_tp(landscape, name) -> (x, y, z)` function
- Fuzzy-matches TP names to handle minor spelling differences between task sheets and the database

**Note on XY coordinates:** The `.cup` file contains lat/lon, but Condor's internal XY grid requires a landscape-specific projection. The most practical approach is to extract XY from existing `.fpl` files (as we did during analysis), or to export them from Condor's task editor. The database should store XY directly once extracted.

A collection of `.fpl` files from the same landscape can be used to bootstrap the database — parse all of them, collect unique `TPName`/`TPPosX`/`TPPosY`/`TPPosZ` tuples, and store them.

### 2. PDF / Text Task Sheet Parser
Task briefing sheets follow a fairly consistent format (see example in the project). A parser could extract fields automatically:

- Landscape name
- Turnpoint names, positions, sector radius and angle
- Aircraft
- Start height, start type, max start speed
- Wind direction and speed, cloud base
- Task date and start time
- Server details (password, launch time)

The PDF can be read with `pdfplumber` or `pymupdf`. Regex patterns cover most fields. This would allow the full workflow: **drop in a PDF → get an FPL out**.

### 3. Validation
Add a `validate_fpl()` function that checks a generated FPL against known-good files:
- TP count is at least 3 (airport + start + finish)
- Start height is plausible for the landscape
- Wind speed is within Condor's accepted range
- Task distance is non-zero and reasonable
- Aircraft name matches a known Condor aircraft list

### 4. Batch Mode
Accept a folder of task JSON files and generate all FPLs in one pass:

```bash
python condor_fpl_gen.py --batch ./tasks/ --output-dir ./races/
```

### 5. Round-trip / Diff Tool
Parse an existing `.fpl` back into the task JSON format, to allow:
- Comparing two FPLs to spot differences
- Editing an existing race (change aircraft, tweak weather) without starting from scratch

```bash
python condor_fpl_gen.py --parse existing.fpl --output updated_task.json
```

---

## Landscape Reference

Landscapes seen in the SGC race files analysed during development:

| Landscape name (FPL) | Region |
|---------------------|--------|
| `Centro_Italia3` | Central Italy — Rieti / Apennines area |
| `Slovenia3` | Slovenia / Austria / NE Italy |

---

## Example FPL Files

The following real race FPLs were used during development and are a useful reference for validating output:

| File | Landscape | Aircraft | Distance |
|------|-----------|----------|----------|
| `Spring26_race5.fpl` | Centro_Italia3 | DuoDiscus | 68 km |
| `Spring26_race1.fpl` | Centro_Italia3 | DuoDiscus | 95 km |
| `Autumm25_race1.fpl` | Slovenia3 | StdCirrus | 88 km |
| `Autumm25_race5.fpl` | Slovenia3 | DuoDiscus | 113 km |

---

## Known Limitations / Open Questions

- **Disabled airspaces:** The `DisabledAirspaces=` field in generated files is left blank. Real files contain a long list of airspace indices specific to the landscape. Condor appears to accept blank without error, but to fully replicate task-editor behaviour this list should be populated. It may be extractable from a reference FPL for the same landscape.
- **Multiple weather zones:** Some races (e.g. `Autumm25_race5.fpl`) use more than one `[WeatherZone]`. The current script only generates a single base zone. Multi-zone support would require additional JSON fields.
- **RandSeed:** Currently generated randomly. Some organisers may want a fixed seed for reproducible conditions.
- **Condor version field:** Set to `3100` by default. Condor 2 uses lower values. This should be a configurable parameter.
"# FlightPlans" 
