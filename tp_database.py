#!/usr/bin/env python3
"""
tp_database.py
--------------
Builds a turnpoint coordinate lookup from Condor .fpl flight plan files.
Maps (landscape, TP name) -> (X, Y, Z) in Condor's local grid coordinates.

Usage:
    from tp_database import TurnpointDatabase
    db = TurnpointDatabase()
    db.load_fpl_dir(r"C:\\Users\\jt235\\Documents\\Condor3\\FlightPlans")
    xyz = db.resolve("Centro_Italia3", "Castellucio")
    # -> (153585.34375, 271711.1875, 1288.0)
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
    Built by scanning existing .fpl race files.
    """

    def __init__(self):
        # { norm_landscape -> { norm_name -> (canonical_name, x, y, z) } }
        self._db: dict = {}

    # ------------------------------------------------------------------
    def load_fpl_dir(self, directory: str) -> int:
        """
        Scan *directory* for all *.fpl files and add their turnpoints.
        Returns the number of new unique TPs added.
        """
        added = 0
        if not os.path.isdir(directory):
            return 0
        for fname in sorted(os.listdir(directory)):
            if not fname.lower().endswith(".fpl"):
                continue
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
    def resolve(self, landscape: str, name: str):
        """
        Look up Condor (X, Y, Z) for *name* in *landscape*.
        Tries:
          1. Exact normalised-name match.
          2. Fuzzy name match (difflib, cutoff 0.75).
        Returns (x, y, z) tuple, or None if not found.
        """
        lk = _norm_landscape(landscape)
        tp_dict = self._db.get(lk)
        if tp_dict is None:
            return None

        nk = _norm_name(name)

        # 1. Exact match
        if nk in tp_dict:
            _, x, y, z = tp_dict[nk]
            return (x, y, z)

        # 2. Fuzzy match
        candidates = list(tp_dict.keys())
        hits = get_close_matches(nk, candidates, n=1, cutoff=0.75)
        if hits:
            matched_name, x, y, z = tp_dict[hits[0]]
            print(f"  [db] Fuzzy match: '{name}' -> '{matched_name}'")
            return (x, y, z)

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
    parser.add_argument("--landscape", help="Filter by landscape name")
    args = parser.parse_args()

    db = TurnpointDatabase()
    n = db.load_fpl_dir(args.fpl_dir)
    print(f"Loaded {n} unique turnpoints from {args.fpl_dir}\n")

    for lk in sorted(db.known_landscapes()):
        if args.landscape and _norm_landscape(args.landscape) != lk:
            continue
        print(f"Landscape: {lk}  ({db.count(lk)} TPs)")
        for name in db.known_tps(lk):
            print(f"  {name}")
        print()
