#!/usr/bin/env python3
"""
tp_database.py
--------------
Builds a turnpoint coordinate lookup from Condor .fpl flight plan files
and SeeYou .cup turnpoint files.

Maps (landscape, TP name) -> (X, Y, Z) in Condor's local grid coordinates.

When a .cup file is loaded alongside .fpl files, an affine geo-transform is
fitted using TPs that appear in both sources.  This allows TPs that are in
the CUP file but have never appeared in an .fpl file to be resolved by
converting their lat/lon to Condor XY.

Usage:
    from tp_database import TurnpointDatabase
    db = TurnpointDatabase()
    db.load_fpl_dir(r"C:\\Users\\jt235\\Documents\\Condor3\\FlightPlans")
    db.load_cup(r"C:\\Users\\jt235\\Documents\\Condor3\\Turnpoints\\Centro_Italia3.cup",
                "Centro_Italia3")
    db.build_transforms()
    xyz = db.resolve("Centro_Italia3", "Salerne", lat=40.6789, lon=14.7673)
"""

import os
import re
from difflib import get_close_matches


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _norm_name(name: str) -> str:
    """Lowercase and collapse whitespace for name comparison."""
    return " ".join(name.lower().split())


def _norm_landscape(landscape: str) -> str:
    """
    Normalise landscape name for comparison.
    'Centro_Italia3', 'Centro Italia 3', 'Centro Italia3' all map to 'centrolitalia3'.
    """
    return landscape.lower().replace("_", "").replace(" ", "")


# ---------------------------------------------------------------------------
# CUP format helpers
# ---------------------------------------------------------------------------

def _parse_cup_latlon(s: str) -> float:
    """
    Parse a SeeYou CUP DDM lat/lon string to decimal degrees.
    Examples: '4040.734N' -> 40.6789, '01446.039E' -> 14.7673
    """
    m = re.match(r'(\d{2,3})(\d{2}\.\d+)([NSEWnsew])', s.strip())
    if not m:
        return 0.0
    deg = int(m.group(1))
    mins = float(m.group(2))
    hemi = m.group(3).upper()
    val = deg + mins / 60.0
    return -val if hemi in ('S', 'W') else val


# ---------------------------------------------------------------------------
# Affine transform fitter (pure Python OLS, no external dependencies)
# ---------------------------------------------------------------------------

def _solve_affine(refs):
    """
    Fit z = a*u + b*v + c using ordinary least squares.
    refs: list of (u, v, z) tuples (at least 3 required).
    Returns (a, b, c) or None if underdetermined / singular.
    """
    n = len(refs)
    if n < 3:
        return None

    S_uu = S_uv = S_u = S_vv = S_v = 0.0
    S_uz = S_vz = S_z = 0.0
    for u, v, z in refs:
        S_uu += u * u
        S_uv += u * v
        S_u  += u
        S_vv += v * v
        S_v  += v
        S_uz += u * z
        S_vz += v * z
        S_z  += z

    # Normal equation matrix (A^T A) and RHS (A^T b)
    M = [
        [S_uu, S_uv, S_u],
        [S_uv, S_vv, S_v],
        [S_u,  S_v,  float(n)],
    ]
    rhs = [S_uz, S_vz, S_z]

    def det3(m):
        return (
            m[0][0] * (m[1][1]*m[2][2] - m[1][2]*m[2][1])
          - m[0][1] * (m[1][0]*m[2][2] - m[1][2]*m[2][0])
          + m[0][2] * (m[1][0]*m[2][1] - m[1][1]*m[2][0])
        )

    d = det3(M)
    if abs(d) < 1e-6:
        return None

    def subcol(mat, col, vals):
        rows = [row[:] for row in mat]
        for i in range(3):
            rows[i][col] = vals[i]
        return rows

    a = det3(subcol(M, 0, rhs)) / d
    b = det3(subcol(M, 1, rhs)) / d
    c = det3(subcol(M, 2, rhs)) / d
    return a, b, c


# ---------------------------------------------------------------------------
# FPL file parser
# ---------------------------------------------------------------------------

def _parse_fpl(path: str):
    """
    Parse a .fpl file and extract landscape name and all turnpoints.
    Returns (landscape: str, tps: list[dict]) or (None, []) on failure.
    Each tp dict has keys: name, x, y, z.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None, []

    m = re.search(r"^Landscape=(.+)$", text, re.MULTILINE)
    if not m:
        return None, []
    landscape = m.group(1).strip()

    m = re.search(r"^Count=(\d+)$", text, re.MULTILINE)
    if not m:
        return landscape, []
    count = int(m.group(1))

    tps = []
    for i in range(count):
        name_m = re.search(rf"^TPName{i}=(.+)$", text, re.MULTILINE)
        x_m    = re.search(rf"^TPPosX{i}=(.+)$", text, re.MULTILINE)
        y_m    = re.search(rf"^TPPosY{i}=(.+)$", text, re.MULTILINE)
        z_m    = re.search(rf"^TPPosZ{i}=(.+)$", text, re.MULTILINE)
        if name_m and x_m and y_m and z_m:
            tps.append({
                "name": name_m.group(1).strip(),
                "x":    float(x_m.group(1)),
                "y":    float(y_m.group(1)),
                "z":    float(z_m.group(1)),
            })

    return landscape, tps


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class TurnpointDatabase:
    """
    Maps (landscape, TP name) -> (X, Y, Z) Condor local grid coordinates.

    Built by:
      1. Scanning existing .fpl race files (provides XY for known TPs).
      2. Loading .cup landscape files (provides lat/lon for all TPs).
      3. Fitting an affine geo-transform using TPs present in both sources.
         This allows new TPs (in CUP but not yet in any FPL) to be resolved
         by converting their lat/lon to Condor XY.
    """

    def __init__(self):
        # { norm_landscape -> { norm_name -> (canonical_name, x, y, z) } }
        self._db: dict = {}
        # { norm_landscape -> { norm_name -> (canonical_name, lat, lon, elev_m) } }
        self._cup: dict = {}
        # { norm_landscape -> ((a_x, b_x, c_x), (a_y, b_y, c_y)) }
        # where X = a_x*lon + b_x*lat + c_x, Y = a_y*lon + b_y*lat + c_y
        self._tfm: dict = {}

    # ------------------------------------------------------------------
    def load_fpl_dir(self, directory: str) -> int:
        """
        Scan *directory* for all *.fpl files and add their turnpoints.
        Files whose names contain 'generated' are skipped — they were produced
        by this tool and their coordinates ultimately came from the geo-transform,
        so using them as reference points would create a circular dependency.
        Returns the number of new unique TPs added.
        """
        added = 0
        if not os.path.isdir(directory):
            return 0
        for fname in sorted(os.listdir(directory)):
            if not fname.lower().endswith(".fpl"):
                continue
            if "generated" in fname.lower():
                continue  # skip our own output files
            landscape, tps = _parse_fpl(os.path.join(directory, fname))
            if not landscape:
                continue
            lk = _norm_landscape(landscape)
            if lk not in self._db:
                self._db[lk] = {}
            for tp in tps:
                nk = _norm_name(tp["name"])
                if nk not in self._db[lk]:
                    self._db[lk][nk] = (tp["name"], tp["x"], tp["y"], tp["z"])
                    added += 1
        return added

    # ------------------------------------------------------------------
    def load_cup(self, path: str, landscape: str) -> int:
        """
        Parse a SeeYou .cup file and store (lat, lon, elev) for every TP.
        Returns the number of new entries added.
        """
        lk = _norm_landscape(landscape)
        if lk not in self._cup:
            self._cup[lk] = {}
        added = 0
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("name,"):
                        continue
                    parts = line.split(",")
                    if len(parts) < 6:
                        continue
                    name     = parts[0].strip('"').strip()
                    lat_str  = parts[3].strip()
                    lon_str  = parts[4].strip()
                    elev_str = parts[5].strip().rstrip("m").strip()
                    if not name or not lat_str or not lon_str:
                        continue
                    try:
                        lat  = _parse_cup_latlon(lat_str)
                        lon  = _parse_cup_latlon(lon_str)
                        elev = float(elev_str) if elev_str else 0.0
                    except (ValueError, AttributeError):
                        continue
                    nk = _norm_name(name)
                    if nk not in self._cup[lk]:
                        self._cup[lk][nk] = (name, lat, lon, elev)
                        added += 1
        except OSError:
            pass
        return added

    # ------------------------------------------------------------------
    def build_transforms(self):
        """
        For each landscape that has both FPL XY and CUP lat/lon data, fit
        an affine transform  (X, Y) = f(lon, lat)  using TPs that appear
        in both sources as reference points.

        Must be called after load_fpl_dir() and load_cup().
        """
        for lk in self._db:
            fpl = self._db.get(lk, {})
            cup = self._cup.get(lk, {})
            refs = []
            for nk, (_, x, y, _z) in fpl.items():
                if nk in cup:
                    _, lat, lon, _ = cup[nk]
                    refs.append((lat, lon, x, y))
            if len(refs) < 3:
                continue
            # Fit X = ax*lon + bx*lat + cx  and  Y = ay*lon + by*lat + cy
            tfm_x = _solve_affine([(lon, lat, x) for lat, lon, x, _ in refs])
            tfm_y = _solve_affine([(lon, lat, y) for lat, lon, _, y in refs])
            if tfm_x and tfm_y:
                self._tfm[lk] = (tfm_x, tfm_y)
                print(f"  [db] Geo-transform fitted for '{lk}' "
                      f"using {len(refs)} reference TPs")

    # ------------------------------------------------------------------
    def latlon_to_xy(self, landscape: str, lat: float, lon: float):
        """
        Convert (lat, lon) to Condor (X, Y) using the fitted affine transform.
        Returns (x, y) tuple or None if no transform is available.
        """
        lk = _norm_landscape(landscape)
        tfm = self._tfm.get(lk)
        if not tfm:
            return None
        (ax, bx, cx), (ay, by, cy) = tfm
        return ax * lon + bx * lat + cx, ay * lon + by * lat + cy

    # ------------------------------------------------------------------
    def get_cup_latlon(self, landscape: str, name: str):
        """
        Return (lat, lon, elev) for *name* from the CUP file, or None.
        Uses exact normalised name match only.
        """
        lk = _norm_landscape(landscape)
        cup = self._cup.get(lk, {})
        nk  = _norm_name(name)
        if nk in cup:
            _, lat, lon, elev = cup[nk]
            return lat, lon, elev
        return None

    # ------------------------------------------------------------------
    def resolve(self, landscape: str, name: str,
                lat: float = None, lon: float = None):
        """
        Look up Condor (X, Y, Z) for *name* in *landscape*.

        The CUP file is the authoritative geographic source.  FPL coordinates
        are only used as a fallback for turnpoints absent from the CUP.

        Resolution order
        ----------------
        1. Exact CUP name match → geo-transform to XY (CUP is ground truth).
        2. If (lat, lon) provided:
             a. Find nearest CUP TP within 0.01° (~1 km) → geo-transform.
             b. If no nearby CUP TP, apply geo-transform directly to (lat, lon).
        3. Exact FPL name match → use FPL XY (only for TPs absent from CUP).
        4. Fuzzy FPL name match (last resort; may give wrong results).

        Returns (x, y, z) tuple, or None if unresolvable.
        """
        lk      = _norm_landscape(landscape)
        tp_dict = self._db.get(lk, {})
        cup     = self._cup.get(lk, {})
        nk      = _norm_name(name)

        # 1. Exact CUP name match → geo-transform (CUP is ground truth)
        if nk in cup:
            _, clat, clon, celev = cup[nk]
            xy = self.latlon_to_xy(landscape, clat, clon)
            if xy:
                x, y = xy
                print(f"  [db] CUP: '{name}' ({x:.0f}, {y:.0f})")
                return x, y, celev

        # 2. CUP geo-proximity via supplied lat/lon
        if lat is not None and lon is not None:
            if cup:
                best_nk, best_dist = None, float("inf")
                for cnk, (_, clat, clon, _) in cup.items():
                    d = (clat - lat) ** 2 + (clon - lon) ** 2
                    if d < best_dist:
                        best_dist, best_nk = d, cnk

                if best_dist < 0.01 ** 2 and best_nk is not None:
                    _, clat, clon, celev = cup[best_nk]
                    xy = self.latlon_to_xy(landscape, clat, clon)
                    if xy:
                        x, y = xy
                        if best_nk != nk:
                            print(f"  [db] Geo-match: '{name}' -> "
                                  f"'{cup[best_nk][0]}' ({x:.0f}, {y:.0f})")
                        else:
                            print(f"  [db] CUP geo: '{name}' ({x:.0f}, {y:.0f})")
                        return x, y, celev

        # 3. FPL exact match — for TPs genuinely absent from the CUP.
        #    These coordinates come from real historical race .fpl files and
        #    are more accurate than the geo-transform estimate.
        if nk in tp_dict:
            _, x, y, z = tp_dict[nk]
            print(f"  [db] FPL fallback: '{name}' ({x:.0f}, {y:.0f})")
            return x, y, z

        # 4. Direct transform — TP not in CUP or FPL, but PDF provides lat/lon.
        if lat is not None and lon is not None:
            xy = self.latlon_to_xy(landscape, lat, lon)
            if xy:
                x, y = xy
                print(f"  [db] Direct transform: '{name}' ({x:.0f}, {y:.0f})")
                return x, y, 0.0

        # 5. Fuzzy FPL match (last resort)
        if tp_dict:
            hits = get_close_matches(nk, list(tp_dict.keys()), n=1, cutoff=0.75)
            if hits:
                matched_name, x, y, z = tp_dict[hits[0]]
                print(f"  [db] Fuzzy match: '{name}' -> '{matched_name}'")
                return x, y, z

        return None

    # ------------------------------------------------------------------
    def known_tps(self, landscape: str) -> list:
        """Return sorted list of canonical TP names for *landscape*."""
        lk = _norm_landscape(landscape)
        tp_dict = self._db.get(lk, {})
        return sorted(v[0] for v in tp_dict.values())

    def count(self, landscape: str) -> int:
        """Return number of known TPs for *landscape*."""
        lk = _norm_landscape(landscape)
        return len(self._db.get(lk, {}))

    def known_landscapes(self) -> list:
        """Return list of normalised landscape keys in the database."""
        return list(self._db.keys())


# ---------------------------------------------------------------------------
# CLI: list known TPs
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="List turnpoints known to the TP database.")
    parser.add_argument("--fpl-dir", required=True, help="Directory of .fpl files to scan")
    parser.add_argument("--cup-dir", help="Directory of .cup landscape files")
    parser.add_argument("--landscape", help="Filter by landscape name")
    args = parser.parse_args()

    db = TurnpointDatabase()
    n = db.load_fpl_dir(args.fpl_dir)
    print(f"Loaded {n} unique turnpoints from {args.fpl_dir}\n")

    if args.cup_dir and os.path.isdir(args.cup_dir):
        for fname in sorted(os.listdir(args.cup_dir)):
            if fname.lower().endswith(".cup"):
                landscape = os.path.splitext(fname)[0]
                n_cup = db.load_cup(os.path.join(args.cup_dir, fname), landscape)
                print(f"Loaded {n_cup} TPs from CUP: {fname}")
        db.build_transforms()
        print()

    for lk in sorted(db.known_landscapes()):
        if args.landscape and _norm_landscape(args.landscape) != lk:
            continue
        print(f"Landscape: {lk}  ({db.count(lk)} TPs)")
        for name in db.known_tps(lk):
            print(f"  {name}")
        print()
