#!/usr/bin/env python3
"""
censusgeocode_full.py -- geocode a market's full address file via the free U.S.
Census batch geocoder (no API key). Runs IN-SANDBOX: the host
`geocoding.geo.census.gov` is on the egress allowlist, so this no longer needs a
networked machine. Do NOT fall back to grid-math estimation -- estimates land a
median ~1.5 km off (worse the farther from Salt Lake; every Utah city has its own
grid origin) and are unusable on named non-grid streets.

Usage:
    python3 censusgeocode_full.py <MARKET>_geocode_input.csv [--out FILE] [--workers 6] [--chunk 2500]

Input  (NO header): id, street, city, state, zip
        -- build `street` WITHOUT the unit/apt (units hurt Census match rate).
Output (default <input stem>_final.csv):
        id, street, city, state, zip, lat, lon, precision, matched
        (matched=False rows keep blank lat/lon)

Resumable: writes <out>.ckpt.json after every chunk; re-running skips finished ids.
Expect ~92% match. Hold the ~8% no-match out of the map, or route only that set to
a rooftop provider (Smarty/Google) via post_batch()'s swap point below.
"""
import sys, csv, io, os, json, time, argparse, threading, requests
from concurrent.futures import ThreadPoolExecutor, as_completed

ENDPOINT  = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
BENCHMARK = "Public_AR_Current"
RETRIES   = 6          # Census batch 502s are transient -> retry with backoff
TIMEOUT   = 300

def post_batch(rows):
    """rows: list of [id, street, city, state, zip]. Returns Census CSV text (or None).

    ROOFTOP SWAP POINT: to upgrade from street-interpolated to true rooftop, replace
    the body of this function with a Smarty/Google batch call that returns the same
    per-id (lat, lon, precision) -- nothing else in this file changes.
    """
    buf = io.StringIO(); w = csv.writer(buf)
    for r in rows:
        w.writerow(r[:5])                       # id, street, city, state, zip -- no unit
    for attempt in range(RETRIES):
        try:
            resp = requests.post(ENDPOINT, files={"addressFile": ("chunk.csv", buf.getvalue())},
                                 data={"benchmark": BENCHMARK}, timeout=TIMEOUT)
            if resp.status_code == 200 and resp.text.strip():
                return resp.text
            raise RuntimeError(f"HTTP {resp.status_code}")
        except Exception as e:
            if attempt == RETRIES - 1:
                return None
            time.sleep(min(45, 5 * (attempt + 1)))

def parse(text):
    """Parse Census batch CSV.

    CRITICAL: the coordinate comes back as a SINGLE quoted field "lon,lat", so
    csv.reader yields it as ONE column. Field layout is:
        row[0]=id  row[1]=input  row[2]=Match/No_Match  row[3]=Exact/Non_Exact
        row[4]=matched_addr  row[5]="lon,lat"  row[6]=tigerLineId  row[7]=side
    Reading lon=row[5], lat=row[6] (a naive split) puts the TIGER id in latitude and
    silently produces 0 matches. Split row[5] on the comma instead.
    """
    out = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 3:
            continue
        rid = row[0]; match = row[2].strip().strip('"')
        if match == "Match" and len(row) >= 6:
            try:
                lon_s, lat_s = row[5].split(",")            # <-- the fix
                tiger = row[6].strip().strip('"') if len(row) > 6 else ""
                side = row[7].strip().strip('"') if len(row) > 7 else ""
                out[rid] = (round(float(lat_s), 7), round(float(lon_s), 7),
                            row[3].strip().strip('"'), tiger, side)
            except Exception:
                out[rid] = None
        else:
            out[rid] = None
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--chunk", type=int, default=2500)   # <=10000 (Census hard limit); smaller = more resilient
    a = ap.parse_args()
    out_path = a.out or (os.path.splitext(a.input)[0].replace("_geocode_input", "") + "_addresses_final.csv")
    ckpt = out_path + ".ckpt.json"

    rows = list(csv.reader(open(a.input)))
    results = json.load(open(ckpt)) if os.path.exists(ckpt) else {}   # id -> [lat,lon,precision] | None
    lock = threading.Lock()

    pending = [r for r in rows if r[0] not in results]
    chunks = [pending[i:i+a.chunk] for i in range(0, len(pending), a.chunk)]
    print(f"{len(rows):,} addresses | done {len(results):,} | pending {len(pending):,} in {len(chunks)} chunks", flush=True)

    def work(chunk):
        text = post_batch(chunk)
        p = parse(text) if text else {}
        for c in chunk:
            p.setdefault(c[0], None)                # give-ups this pass -> None (retried next run)
        return p

    completed = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for fut in as_completed({ex.submit(work, c): c for c in chunks}):
            with lock:
                results.update(fut.result())
                json.dump(results, open(ckpt, "w"))
            completed += 1
            matched = sum(1 for v in results.values() if v)
            print(f"  chunk {completed}/{len(chunks)} | matched {matched:,}/{len(results):,}", flush=True)

    matched = 0
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id","street","city","state","zip","lat","lon","precision","matched","tiger","side"])
        for r in rows:
            v = results.get(r[0])
            if v:
                matched += 1
                w.writerow(r[:5] + [v[0], v[1], v[2], True,
                                    v[3] if len(v) > 3 else "", v[4] if len(v) > 4 else ""])
            else:
                w.writerow(r[:5] + ["", "", "No_Match", False, "", ""])
    print(f"matched {matched:,}/{len(rows):,} ({matched/len(rows)*100:.1f}%) -> {out_path}", flush=True)
    # merge step (accept only if inside the address's own ZIP) still applies before mapping.

if __name__ == "__main__":
    main()
