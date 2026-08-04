#!/usr/bin/env python3
"""
validate.py -- decide whether a built market is fit to publish, and say why.

Reads each <NAME>_addresses_final.csv and applies mechanical checks. Machine
catches the mechanical failures; the report gives a human enough to catch the
semantic ones a script can't see -- a tab labelled with the wrong city, a market
that's half the size it should be.

    python3 validate.py work/ --report report.md

Exit 0 = safe to publish. Exit 1 = at least one market failed; nothing publishes.

FAIL (blocks publishing):
    fewer than 90% of rows mappable      -- geocoder or input is wrong, not noisy
    under 98% of coordinates in the claimed state -- swapped lat/lon, wrong state,
                                            or a centroid fallback
    no mappable rows at all
    every coordinate identical           -- a placeholder column, not real data

WARN (publishes, but says so):
    over 20% flagged FAR_FROM_ZIP        -- often a stretched ZIP, sometimes not
    a ZIP holding fewer than 5 addresses -- usually a typo'd ZIP
    over 20% of rows sharing coordinates -- centerline collapse or apartments
"""
import argparse
import csv
import glob
import os
import sys
from collections import Counter

MIN_MAPPABLE = 0.90
MIN_IN_STATE = 0.98
WARN_FAR = 0.20
WARN_STACK = 0.20
WARN_TINY_ZIP = 5

# Deliberately generous bounding boxes -- this catches a market in the wrong
# state or a lat/lon swap, not a pin a few hundred metres off.
BOX = {
    "AL": (30.1, 35.1, -88.6, -84.8), "AK": (51.0, 71.5, -180.0, -129.0),
    "AZ": (31.2, 37.1, -114.9, -108.9), "AR": (32.9, 36.6, -94.7, -89.6),
    "CA": (32.4, 42.1, -124.5, -114.0), "CO": (36.9, 41.1, -109.1, -102.0),
    "CT": (40.9, 42.1, -73.8, -71.7), "DE": (38.4, 39.9, -75.8, -74.9),
    "DC": (38.7, 39.1, -77.2, -76.8), "FL": (24.3, 31.1, -87.7, -79.9),
    "GA": (30.3, 35.1, -85.7, -80.7), "HI": (18.8, 22.3, -160.3, -154.7),
    "ID": (41.9, 49.1, -117.3, -110.9), "IL": (36.9, 42.6, -91.6, -87.4),
    "IN": (37.7, 41.8, -88.1, -84.7), "IA": (40.3, 43.6, -96.7, -90.1),
    "KS": (36.9, 40.1, -102.1, -94.5), "KY": (36.4, 39.2, -89.6, -81.9),
    "LA": (28.8, 33.1, -94.1, -88.7), "ME": (42.9, 47.6, -71.2, -66.9),
    "MD": (37.8, 39.8, -79.6, -74.9), "MA": (41.2, 43.0, -73.6, -69.8),
    "MI": (41.6, 48.4, -90.5, -82.3), "MN": (43.4, 49.5, -97.3, -89.4),
    "MS": (30.1, 35.1, -91.7, -88.0), "MO": (35.9, 40.7, -95.9, -89.0),
    "MT": (44.3, 49.1, -116.1, -104.0), "NE": (39.9, 43.1, -104.1, -95.2),
    "NV": (35.0, 42.1, -120.1, -114.0), "NH": (42.6, 45.4, -72.6, -70.6),
    "NJ": (38.8, 41.4, -75.6, -73.8), "NM": (31.2, 37.1, -109.1, -102.9),
    "NY": (40.4, 45.1, -79.8, -71.8), "NC": (33.7, 36.7, -84.4, -75.4),
    "ND": (45.8, 49.1, -104.1, -96.5), "OH": (38.3, 42.4, -84.9, -80.4),
    "OK": (33.6, 37.1, -103.1, -94.4), "OR": (41.9, 46.4, -124.6, -116.4),
    "PA": (39.6, 42.4, -80.6, -74.6), "RI": (41.1, 42.1, -71.9, -71.1),
    "SC": (32.0, 35.3, -83.4, -78.4), "SD": (42.4, 46.0, -104.1, -96.4),
    "TN": (34.9, 36.7, -90.4, -81.6), "TX": (25.8, 36.6, -106.7, -93.5),
    "UT": (36.9, 42.1, -114.1, -108.9), "VT": (42.7, 45.1, -73.5, -71.4),
    "VA": (36.5, 39.5, -83.7, -75.1), "WA": (45.5, 49.1, -124.9, -116.9),
    "WV": (37.1, 40.7, -82.7, -77.7), "WI": (42.4, 47.4, -92.9, -86.7),
    "WY": (40.9, 45.1, -111.1, -104.0), "PR": (17.8, 18.6, -67.3, -65.2),
}


def slug_from_workdir(name):
    """work dir "<product>__<tab>" -> URL path "<product>/<tab>"."""
    return name.replace("__", "/")


def check(final_csv):
    rows = list(csv.DictReader(open(final_csv)))
    if not rows:
        return {"market": os.path.basename(final_csv), "fail": ["file is empty"],
                "warn": [], "n": 0, "mapped": 0}

    workdir = os.path.basename(os.path.dirname(final_csv))
    market = workdir
    flags = Counter(r["qa_flag"] for r in rows)
    held = flags["NO_MATCH"] + flags["FAR_FROM_ZIP_SEVERE"]
    mapped = [r for r in rows if r["lat"] and r["qa_flag"] not in
              ("NO_MATCH", "FAR_FROM_ZIP_SEVERE")]
    states = Counter(r["state"] for r in rows if r["state"])
    cities = Counter(r["city"] for r in mapped if r["city"])
    zips = Counter(r["zip"] for r in mapped if r["zip"])
    fail, warn = [], []

    rate = len(mapped) / len(rows)
    if not mapped:
        fail.append("no mappable rows")
    elif rate < MIN_MAPPABLE:
        fail.append(f"only {rate:.1%} of rows mappable "
                    f"(expected at least {MIN_MAPPABLE:.0%})")

    state = states.most_common(1)[0][0] if states else ""
    box = BOX.get(state.upper())
    if mapped and box:
        lo, hi, wlo, whi = box
        inside = sum(1 for r in mapped
                     if lo <= float(r["lat"]) <= hi and wlo <= float(r["lon"]) <= whi)
        frac = inside / len(mapped)
        if frac < MIN_IN_STATE:
            fail.append(f"{1 - frac:.1%} of coordinates fall outside {state} "
                        f"-- check for swapped lat/lon or a wrong state column")
    elif mapped and state and not box:
        warn.append(f"state '{state}' not recognized -- skipped the location check")

    if mapped and len({(r["lat"], r["lon"]) for r in mapped}) == 1:
        fail.append("every coordinate is identical -- that column is a placeholder, "
                    "not real data")

    if len(rows) and flags["FAR_FROM_ZIP"] / len(rows) > WARN_FAR:
        warn.append(f"{flags['FAR_FROM_ZIP'] / len(rows):.0%} flagged far from their "
                    f"own ZIP -- normal for a stretched ZIP, worth a look otherwise")
    stacked = sum(1 for r in mapped if int(r.get("stack_size") or 1) > 1)
    if mapped and stacked / len(mapped) > WARN_STACK:
        warn.append(f"{stacked / len(mapped):.0%} of addresses share a coordinate "
                    f"with another address")
    tiny = [z for z, n in zips.items() if n < WARN_TINY_ZIP]
    if tiny:
        warn.append(f"{len(tiny)} ZIP(s) hold fewer than {WARN_TINY_ZIP} addresses: "
                    + ", ".join(sorted(tiny)[:6]))
    if len(states) > 1:
        warn.append("more than one state on this tab: " + ", ".join(
            f"{s} ({n:,})" for s, n in states.most_common(4)))

    return {"market": market, "slug": slug_from_workdir(market),
            "n": len(rows), "mapped": len(mapped), "held": held,
            "zips": len(zips), "state": state, "fail": fail, "warn": warn,
            "cities": cities.most_common(3), "rate": rate}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir")
    ap.add_argument("--report", default=None, help="write a markdown report here")
    ap.add_argument("--base-url", default=None,
                    help="Pages base URL, so the report can list the map links")
    ap.add_argument("--status-json", default=None,
                    help="also write machine-readable results here (deploy with the maps)")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.workdir, "*", "*_addresses_final.csv")))

    # Restrict to the markets this run actually built. Without this, a cached
    # work/ directory makes long-retired markets reappear in the report and in
    # status.json, with links that 404.
    bpath = os.path.join(a.workdir, "_built.json")
    if os.path.exists(bpath):
        try:
            with open(bpath) as f:
                built = set(__import__("json").load(f))
            kept = [p for p in files
                    if os.path.basename(os.path.dirname(p)) in built]
            dropped = len(files) - len(kept)
            if dropped:
                print(f"ignoring {dropped} market(s) left over in {a.workdir} "
                      f"from earlier runs", file=sys.stderr)
            files = kept
        except (ValueError, OSError):
            pass

    if not files:
        print(f"nothing to validate under {a.workdir}", file=sys.stderr)
        return 1

    results = [check(f) for f in files]
    bad = [r for r in results if r["fail"]]

    # files publish.py could not open at all -- they produced no market, so they
    # would otherwise be invisible to anyone reading only the summary
    # slug -> source filename, so a report can be narrowed to one upload
    sources = {}
    spath = os.path.join(a.workdir, "_sources.json")
    if os.path.exists(spath):
        try:
            with open(spath) as f:
                sources = __import__("json").load(f)
        except (ValueError, OSError):
            sources = {}

    unreadable = []
    upath = os.path.join(a.workdir, "_unreadable.json")
    if os.path.exists(upath):
        try:
            with open(upath) as f:
                unreadable = __import__("json").load(f)
        except (ValueError, OSError):
            unreadable = []

    out = []
    out.append("## Address maps" + ("" if not bad else " — PUBLISHING BLOCKED"))
    out.append("")
    if bad:
        out.append(f"{len(bad)} of {len(results)} markets failed a check, so nothing "
                   f"was published. The maps already live stay as they were.")
        out.append("")
    out.append("| Market | Addresses | Mapped | Held out | ZIPs | State | |")
    out.append("|---|---:|---:|---:|---:|---|---|")
    for r in results:
        mark = "FAILED" if r["fail"] else ("check" if r["warn"] else "ok")
        out.append(f"| {r['market']} | {r['n']:,} | {r['mapped']:,} | {r['held']:,} "
                   f"| {r['zips']} | {r['state']} | {mark} |")
    out.append("")

    for r in results:
        if not (r["fail"] or r["warn"]):
            continue
        out.append(f"### {r['market']}")
        cities = ", ".join(f"{c} ({n:,})" for c, n in r["cities"])
        if cities:
            out.append(f"Mostly {cities}.")
        for f in r["fail"]:
            out.append(f"- **Blocked:** {f}")
        for w in r["warn"]:
            out.append(f"- Worth checking: {w}")
        out.append("")

    if unreadable:
        out.append("### Files that could not be read")
        out.append("")
        for u in unreadable:
            out.append(f"- **{u['file']}** — {u['reason']}")
        out.append("")
        out.append("The markets below published normally. Nothing from the file(s) "
                   "above is on the map.")
        out.append("")

    if not bad:
        out.append("Every market passed."
                   + (" One or more files could not be read -- see above."
                      if unreadable else ""))

    if a.base_url:
        base = a.base_url if a.base_url.endswith("/") else a.base_url + "/"
        out.append("")
        out.append("## Map links")
        out.append("")
        if bad:
            out.append("Nothing was republished this run, so these are the maps as "
                       "they already were:")
        else:
            out.append("Live in a minute or two. These addresses never change, so an "
                       "embed in Google Sites only needs setting up once:")
        out.append("")
        for r in results:
            slug = r["slug"]
            note = " — failed this run, showing older data" if r["fail"] else ""
            out.append(f"- [{r['slug']}]({base}{slug}/) — {base}{slug}/{note}")

    text = "\n".join(out)
    print(text)
    if a.report:
        with open(a.report, "w") as f:
            f.write(text + "\n")

    # Machine-readable twin of the report, deployed with the maps so anything
    # downstream can read results without a token. Carries the commit SHA so a
    # reader can tell this run's results from a previous run's.
    if a.status_json:
        base = (a.base_url or "").rstrip("/") + "/" if a.base_url else ""
        status = {
            "generated": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
            "commit": os.environ.get("GITHUB_SHA", ""),
            "run_url": (f"{os.environ['GITHUB_SERVER_URL']}/"
                        f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                        f"{os.environ['GITHUB_RUN_ID']}")
            if os.environ.get("GITHUB_RUN_ID") else "",
            "ok": not bad,
            "unreadable": unreadable,
            "base": base,
            "markets": [{
                "market": r["market"],
                "slug": r["slug"],
                "url": base + r["slug"] + "/",
                "addresses": r["n"],
                "mapped": r["mapped"],
                "held_out": r["held"],
                "zips": r["zips"],
                "state": r["state"],
                "source": sources.get(r["market"], ""),
                "status": "failed" if r["fail"] else ("check" if r["warn"] else "ok"),
                "notes": r["fail"] + r["warn"],
            } for r in results],
        }
        os.makedirs(os.path.dirname(a.status_json) or ".", exist_ok=True)
        with open(a.status_json, "w") as f:
            __import__("json").dump(status, f, indent=2)
            f.write("\n")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
