#!/usr/bin/env python3
"""
finalize_addresses.py -- Stage 3 of the address-map pipeline.

Merges geocoder output back into the clean table, sanity-checks every coordinate
against its own ZIP, applies QA reason codes, and emits the map-ready file:

    <NAME>_addresses_final.csv
        id, street, unit, city, state, zip, lat, lon,
        coord_source, precision, matched, qa_flag, stack_size

Usage:
    python3 finalize_addresses.py <NAME>_addresses_clean.csv --name NAME \
        [--geocoded <NAME>_addresses_final_geo.csv]

If the input file already carried coordinates for every row, --geocoded is omitted.

QA reason codes (the map builder honors these):
    OK                    usable
    FAR_FROM_ZIP          6-15 km from the ZIP's own center -> kept, flagged for review
                          (often legitimate: rural ZIP edges)
    FAR_FROM_ZIP_SEVERE   > 15 km -> QUARANTINED from the map (centroid fallback /
                          broken geocode match)
    DUP_STACK             8+ addresses share one exact coordinate (street-centerline
                          collapse or a multi-family building) -> kept, collapsed to a
                          single pin with a unit count
    NO_MATCH              geocoder returned nothing -> held out of the map

ZIP validation note: this checks each coordinate against the MEDIAN of all other
matched coordinates in the same ZIP, computed from this file. It needs no external
ZIP boundary file and it reliably catches the failure that actually matters -- a
coordinate that fell back to a city or ZIP centroid miles from the real address.
It is a distance test, not a polygon test.
"""
import argparse
import csv
import math
import os
from collections import defaultdict
from statistics import median

SEVERE_KM = 15.0
REVIEW_KM = 6.0
STACK_MIN = 8


def km(lat1, lon1, lat2, lon2):
    """Equirectangular approximation -- plenty accurate at metro distances."""
    mlat = math.radians((lat1 + lat2) / 2)
    dx = (lon2 - lon1) * 111.320 * math.cos(mlat)
    dy = (lat2 - lat1) * 110.574
    return math.hypot(dx, dy)


def parcel_offset(rows, meters):
    """Push each pin further off the street centerline, on the side Census reported.

    Census already offsets about 6 m to the correct side of the centerline, which
    lands a pin at the curb. Real houses sit further back. For each TIGER line
    segment we fit the local street bearing from its own points, work out which
    perpendicular direction 'L' is (empirically -- the L and R points already sit
    on opposite sides, so no assumption about TIGER digitization direction is
    needed), and push each point `meters` further that way.

    THIS IS COSMETIC. It makes pins sit on parcels instead of on pavement; it does
    NOT make them more accurate, and it can be wrong where setbacks are unusual
    (corner lots, apartment complexes, rural frontage). Curb placement is arguably
    the more correct answer for routing a truck. For real rooftop accuracy, swap
    the geocoder in post_batch() -- do not lean on this.

    Segments where only one side is present are left untouched, since the
    direction cannot be determined from the data.
    """
    groups = defaultdict(list)
    for r in rows:
        if r["_lat"] is not None and r.get("tiger") and r.get("side") in ("L", "R"):
            groups[r["tiger"]].append(r)

    moved = skipped = 0
    for tid, pts in groups.items():
        if len(pts) < 2:
            skipped += len(pts)
            continue
        lat0 = sum(p["_lat"] for p in pts) / len(pts)
        klon = 111.320 * math.cos(math.radians(lat0)) * 1000.0   # m per degree lon
        klat = 110.574 * 1000.0
        xs = [(p["_lon"]) * klon for p in pts]
        ys = [(p["_lat"]) * klat for p in pts]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        syy = sum((y - my) ** 2 for y in ys)
        # principal direction of the point cloud = the street bearing
        if sxx + syy == 0:
            skipped += len(pts)
            continue
        theta = 0.5 * math.atan2(2 * sxy, sxx - syy)
        ux, uy = math.cos(theta), math.sin(theta)
        px, py = -uy, ux                                  # perpendicular
        signed = [((x - mx) * px + (y - my) * py) for x, y in zip(xs, ys)]
        lsign = [s for s, p in zip(signed, pts) if p["side"] == "L"]
        rsign = [s for s, p in zip(signed, pts) if p["side"] == "R"]
        if not lsign or not rsign:
            skipped += len(pts)
            continue
        # whichever side sits on the positive perpendicular gets pushed positive
        ldir = 1.0 if (sum(lsign) / len(lsign)) > (sum(rsign) / len(rsign)) else -1.0
        for p in pts:
            d = ldir * meters if p["side"] == "L" else -ldir * meters
            p["_lon"] += (px * d) / klon
            p["_lat"] += (py * d) / klat
            moved += 1
    return moved, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clean", help="<NAME>_addresses_clean.csv from clean_addresses.py")
    ap.add_argument("--name", required=True)
    ap.add_argument("--geocoded", default=None,
                    help="output of censusgeocode_full.py (omit if nothing needed geocoding)")
    ap.add_argument("--parcel-offset", type=float, default=0.0, metavar="METERS",
                    help="COSMETIC: push pins this far off the curb onto the parcel "
                         "(try 10). Default 0 = leave Census placement alone.")
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()

    # ---- geocoder results, keyed by id
    geo = {}
    if a.geocoded:
        if not os.path.exists(a.geocoded):
            raise SystemExit(f"missing {a.geocoded} -- run censusgeocode_full.py first")
        for r in csv.DictReader(open(a.geocoded)):
            if str(r.get("matched", "")).strip().lower() == "true" and r.get("lat"):
                geo[r["id"]] = (float(r["lat"]), float(r["lon"]),
                                r.get("precision", "").strip(),
                                r.get("tiger", ""), r.get("side", ""))
            else:
                geo[r["id"]] = None

    rows = list(csv.DictReader(open(a.clean)))
    print(f"{len(rows):,} rows in {os.path.basename(a.clean)}")

    # ---- attach coordinates
    for r in rows:
        r["tiger"] = ""
        r["side"] = ""
        if r["lat"] and r["lon"]:
            r["_lat"], r["_lon"] = float(r["lat"]), float(r["lon"])
            r["precision"] = "from_file"
            r["matched"] = "True"
        else:
            g = geo.get(r["id"])
            if g:
                r["_lat"], r["_lon"], r["precision"] = g[0], g[1], g[2] or "Census"
                r["tiger"], r["side"] = g[3], g[4]
                r["coord_source"] = "geocode"
                r["matched"] = "True"
            else:
                r["_lat"] = r["_lon"] = None
                r["precision"] = "No_Match"
                r["matched"] = "False"
                r["coord_source"] = "none"

    if a.parcel_offset > 0:
        moved, skipped = parcel_offset(rows, a.parcel_offset)
        print(f"\nparcel offset {a.parcel_offset:.0f} m: moved {moved:,} pins, "
              f"left {skipped:,} in place (street segment had only one side)")
        print("  NOTE: cosmetic only -- improves how pins sit on parcels, not accuracy")

    # ---- per-ZIP median center from this file's own matched points
    byzip = defaultdict(list)
    for r in rows:
        if r["_lat"] is not None and r["zip"]:
            byzip[r["zip"]].append((r["_lat"], r["_lon"]))
    center = {z: (median(p[0] for p in pts), median(p[1] for p in pts))
              for z, pts in byzip.items() if len(pts) >= 5}

    # ---- exact-coordinate stacks
    stack = defaultdict(int)
    for r in rows:
        if r["_lat"] is not None:
            stack[(round(r["_lat"], 5), round(r["_lon"], 5))] += 1

    # ---- flags
    counts = defaultdict(int)
    for r in rows:
        if r["_lat"] is None:
            r["qa_flag"] = "NO_MATCH"
            r["stack_size"] = 0
        else:
            d = None
            c = center.get(r["zip"])
            if c:
                d = km(r["_lat"], r["_lon"], c[0], c[1])
            n = stack[(round(r["_lat"], 5), round(r["_lon"], 5))]
            r["stack_size"] = n
            if d is not None and d > SEVERE_KM:
                r["qa_flag"] = "FAR_FROM_ZIP_SEVERE"
            elif d is not None and d > REVIEW_KM:
                r["qa_flag"] = "FAR_FROM_ZIP"
            elif n >= STACK_MIN:
                r["qa_flag"] = "DUP_STACK"
            else:
                r["qa_flag"] = "OK"
        counts[r["qa_flag"]] += 1

    out = os.path.join(a.outdir, f"{a.name}_addresses_final.csv")
    cols = ["id", "street", "unit", "city", "state", "zip", "lat", "lon",
            "coord_source", "precision", "matched", "qa_flag", "stack_size"]
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            w.writerow([
                r["id"], r["street"], r["unit"], r["city"], r["state"], r["zip"],
                f"{r['_lat']:.6f}" if r["_lat"] is not None else "",
                f"{r['_lon']:.6f}" if r["_lon"] is not None else "",
                r["coord_source"], r["precision"], r["matched"],
                r["qa_flag"], r["stack_size"]])

    mapped = sum(v for k, v in counts.items()
                 if k not in ("NO_MATCH", "FAR_FROM_ZIP_SEVERE"))
    print("\nQA:")
    for k in ("OK", "DUP_STACK", "FAR_FROM_ZIP", "FAR_FROM_ZIP_SEVERE", "NO_MATCH"):
        if counts[k]:
            print(f"  {k:<20} {counts[k]:>9,}")
    print(f"\n  mappable            {mapped:>9,} ({mapped/len(rows)*100:.1f}%)")
    print(f"  ZIPs with a center   {len(center):>9,}")
    print(f"\nwrote {out}")
    print(f"\nNEXT: python3 build_address_map.py {out} --name {a.name} "
          f"--title \"Market Name\"")


if __name__ == "__main__":
    main()
