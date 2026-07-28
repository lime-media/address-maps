#!/usr/bin/env python3
"""
build_address_map.py -- Stage 4 (final) of the address-map pipeline.

Builds a single self-contained interactive HTML map from <NAME>_addresses_final.csv.
Pins are grouped and colored by ZIP, cluster into counted bubbles when zoomed out,
and resolve to individual addresses when zoomed in.

Usage:
    python3 build_address_map.py <NAME>_addresses_final.csv --name NAME \
        --title "Dallas-Fort Worth" [--subtitle "AT&T Fiber serviceable addresses"] \
        [--include-far] [--cluster-radius 60]

Output: <NAME>_address_map.html

Scale: built for 50k-250k pins. Individual markers are drawn on a Leaflet canvas
renderer and clustered client-side with Supercluster, so the browser never holds
250k DOM nodes. The point payload is gzipped and base64-embedded (~5-8x smaller
than raw JSON), keeping a 200k-address file in the 2-4 MB range.

Rows excluded from the map: qa_flag = NO_MATCH or FAR_FROM_ZIP_SEVERE.
FAR_FROM_ZIP rows are included by default (usually legitimate rural ZIP edges);
pass --include-far=no to drop them too. Exact-coordinate stacks collapse to one
pin carrying an "N addresses at this location" count.

Needs a network connection on open: Leaflet, Supercluster and pako load from CDN.
"""
import argparse
import base64
import csv
import gzip
import json
import os
from collections import defaultdict

# ZIP swatches -- brand teal/magenta first, then hues chosen to stay separable
# against the CARTO light basemap. Cycles if a market has more ZIPs than colors.
PALETTE = ["#43B0CF", "#D946D9", "#F2A900", "#5FBF6A", "#E4572E", "#9B6BF2",
           "#2E86AB", "#E8739F", "#7FB800", "#00A6A6", "#C1666B", "#4F6D7A",
           "#F0A26F", "#8D6A9F", "#2FA36B", "#B5651D"]

SKELETON = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>__TITLE__ &mdash; Address Map</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pako/2.1.0/pako.min.js"></script>
<script src="https://unpkg.com/supercluster@8.0.1/dist/supercluster.min.js"></script>
<style>
  :root{
    --navy:#102B46; --panel:#21405F; --teal:#43B0CF;
    --muted:#B8C7D8; --line:rgba(184,199,216,.18);
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;font:14px/1.45 -apple-system,BlinkMacSystemFont,
    "Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#fff;background:var(--navy)}
  #app{display:flex;height:100%;overflow:hidden}
  #side{width:340px;min-width:340px;background:var(--navy);display:flex;
    flex-direction:column;transition:margin-left .26s ease;border-right:1px solid var(--line)}
  #app.nav-collapsed #side{margin-left:-340px}
  #map{flex:1;background:#e8eaed}
  .sec{padding:14px 16px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:19px;font-weight:650;letter-spacing:.2px}
  .tag{margin:4px 0 0;color:var(--teal);font-size:12.5px}
  h2{margin:0 0 9px;font-size:11px;font-weight:700;letter-spacing:.09em;
    text-transform:uppercase;color:var(--muted)}
  #zipwrap{flex:1;overflow-y:auto;padding:14px 16px}
  #zipwrap::-webkit-scrollbar{width:9px}
  #zipwrap::-webkit-scrollbar-thumb{background:var(--panel);border-radius:5px}
  .find{width:100%;padding:7px 9px;margin-bottom:9px;border-radius:6px;
    border:1px solid var(--line);background:var(--panel);color:#fff;font-size:13px}
  .find::placeholder{color:#7f96ae}
  .bulk{display:flex;gap:7px;margin-bottom:11px}
  .bulk button{flex:1;padding:6px;border:1px solid var(--line);border-radius:6px;
    background:var(--panel);color:var(--muted);font-size:11.5px;font-weight:600;cursor:pointer}
  .bulk button:hover{color:#fff;border-color:var(--teal)}
  .row{display:flex;align-items:center;gap:9px;padding:5px 6px;border-radius:6px;cursor:pointer}
  .row:hover{background:var(--panel)}
  .row input{accent-color:var(--teal);cursor:pointer;flex:none}
  .sw{width:11px;height:11px;border-radius:50%;flex:none;box-shadow:0 0 0 2px rgba(255,255,255,.22)}
  .zl{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .zl b{font-weight:600}
  .zl span{color:var(--muted);font-size:12px}
  .ct{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums;flex:none}
  .note{color:var(--muted);font-size:12px;margin:0}
  .note li{margin:3px 0}
  ul.b{list-style:none;padding:0;margin:0}
  ul.b li:before{content:"\25A0";color:var(--teal);margin-right:7px;font-size:10px}
  #foot{padding:12px 16px;color:var(--muted);font-size:11.5px;
    border-top:1px solid var(--line);font-variant-numeric:tabular-nums}
  #navtoggle{position:absolute;top:12px;left:12px;z-index:900;background:var(--navy);
    color:#fff;border:1px solid var(--line);border-radius:7px;padding:8px 12px;
    font-size:12.5px;font-weight:600;cursor:pointer}
  #navtoggle:hover{border-color:var(--teal)}
  #busy{position:absolute;top:12px;left:50%;transform:translateX(-50%);z-index:900;
    background:var(--navy);border:1px solid var(--teal);border-radius:7px;
    padding:7px 15px;font-size:12.5px;display:none}
  .cl{border-radius:50%;display:flex;align-items:center;justify-content:center;
    font-weight:700;color:#fff;font-variant-numeric:tabular-nums;
    text-shadow:0 1px 2px rgba(0,0,0,.35);cursor:pointer}
  .leaflet-popup-content{margin:11px 14px;font-size:13px;line-height:1.5}
  .leaflet-popup-content b{font-size:13.5px}
  .leaflet-popup-content i{color:#5a6b7c;font-size:12px}
  @media (max-width:820px){#app.nav-collapsed #side{margin-left:-340px}}
  @media (prefers-reduced-motion:reduce){#side{transition:none}}
</style>
</head>
<body>
<div id="app">
  <div id="side">
    <div class="sec">
      <h1>__TITLE__</h1>
      <p class="tag">__SUBTITLE__</p>
    </div>
    <div id="zipwrap">
      <h2>ZIP codes</h2>
      <input class="find" id="find" placeholder="Filter ZIP or city&hellip;" autocomplete="off"/>
      <div class="bulk"><button id="bAll">Show all</button><button id="bNone">Hide all</button></div>
      <div id="ziplist"></div>
    </div>
    <div class="sec">
      <h2>Data notes</h2>
      <ul class="b note" id="notes"></ul>
    </div>
    <div id="foot"></div>
  </div>
  <div id="map"></div>
</div>
<button id="navtoggle">&#10005; Close</button>
<div id="busy">Updating&hellip;</div>

<script>
const PAYLOAD = "__PAYLOAD__";

/* ---- inflate the embedded point payload ---- */
const bin = atob(PAYLOAD);
const u8 = new Uint8Array(bin.length);
for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
const D = JSON.parse(pako.ungzip(u8, {to: 'string'}));

/* delta-decode coordinates back to degrees */
const N = D.z.length;
const LAT = new Float64Array(N), LON = new Float64Array(N);
let al = 0, ao = 0;
for (let i = 0; i < N; i++) { al += D.lat[i]; ao += D.lon[i]; LAT[i] = al / 1e5; LON[i] = ao / 1e5; }

/* ---- one GeoJSON feature per pin, grouped by ZIP for fast re-indexing ---- */
const featsByZip = D.zips.map(() => []);
for (let i = 0; i < N; i++) {
  featsByZip[D.z[i]].push({
    type: 'Feature',
    properties: {i: i, z: D.z[i]},
    geometry: {type: 'Point', coordinates: [LON[i], LAT[i]]}
  });
}

const map = L.map('map', {preferCanvas: true, zoomControl: false});
L.control.zoom({position: 'topright'}).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
  {attribution: '&copy; OpenStreetMap &copy; CARTO', maxZoom: 19}).addTo(map);

const cvs = L.canvas({padding: 0.35});
const pinLayer = L.layerGroup().addTo(map);
const clusterLayer = L.layerGroup().addTo(map);

const CLUSTER_RADIUS = __CLUSTER_RADIUS__;
/* Safety valve: a normal street-level viewport holds ~1k pins, but an unusually
   dense footprint could ask for far more. Cap what we draw and say so. */
const MAX_PINS = 15000;
const active = new Set(D.zips.map((_, i) => i));
let index = null;

function buildIndex() {
  const feats = [];
  for (const zi of active) for (const f of featsByZip[zi]) feats.push(f);
  index = new Supercluster({
    radius: CLUSTER_RADIUS, maxZoom: 16, minPoints: 4, extent: 512,
    map: p => ({dz: p.z, dn: 1}),
    reduce: (a, b) => { if (b.dn > a.dn) { a.dz = b.dz; a.dn = b.dn; } }
  }).load(feats);
  return feats.length;
}

function bubbleSize(n) {
  return Math.min(58, 24 + 11 * Math.log10(Math.max(n, 10)));
}

function render() {
  if (!index) return;
  const b = map.getBounds(), z = Math.min(19, Math.round(map.getZoom()));
  const cl = index.getClusters([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()], z);
  pinLayer.clearLayers();
  clusterLayer.clearLayers();

  let drawn = 0, skipped = 0;
  for (const c of cl) {
    const [lon, lat] = c.geometry.coordinates;
    if (c.properties.cluster) {
      const n = c.properties.point_count;
      const col = D.cols[c.properties.dz % D.cols.length];
      const s = bubbleSize(n);
      const m = L.marker([lat, lon], {
        icon: L.divIcon({
          className: '',
          iconSize: [s, s],
          html: '<div class="cl" style="width:' + s + 'px;height:' + s + 'px;background:' + col +
                ';box-shadow:0 0 0 4px ' + col + '4d;font-size:' + (n > 9999 ? 11 : 12.5) + 'px">' +
                c.properties.point_count_abbreviated + '</div>'
        })
      });
      m.on('click', () => {
        map.setView([lat, lon], Math.min(19, index.getClusterExpansionZoom(c.properties.cluster_id)));
      });
      m.bindTooltip(n.toLocaleString() + ' addresses', {direction: 'top', offset: [0, -s / 2]});
      clusterLayer.addLayer(m);
    } else {
      if (drawn >= MAX_PINS) { skipped++; continue; }
      drawn++;
      const i = c.properties.i;
      const col = D.cols[D.z[i] % D.cols.length];
      const u = D.u[i];
      L.circleMarker([lat, lon], {
        renderer: cvs, radius: 4, weight: 1, color: '#fff', opacity: .85,
        fillColor: col, fillOpacity: .92
      }).bindPopup(
        '<b>' + D.s[i] + '</b><br>' + D.city[D.z[i]] + ', ' + D.st + ' ' + D.zips[D.z[i]] +
        (u > 1 ? '<br><i>' + u + ' addresses at this location</i>' : '')
      ).addTo(pinLayer);
    }
  }

  if (skipped > 0) {
    busy.textContent = 'Zoom in to see ' + skipped.toLocaleString() + ' more addresses';
    busy.style.display = 'block';
  } else if (busy.textContent.startsWith('Zoom in')) {
    busy.style.display = 'none';
  }
}

/* ---- ZIP list ---- */
const listEl = document.getElementById('ziplist');
const rows = [];
D.zips.forEach((zip, i) => {
  const col = D.cols[i % D.cols.length];
  const row = document.createElement('label');
  row.className = 'row';
  row.innerHTML =
    '<input type="checkbox" checked/>' +
    '<span class="sw" style="background:' + col + '"></span>' +
    '<span class="zl"><b>' + zip + '</b> <span>' + D.city[i] + '</span></span>' +
    '<span class="ct">' + D.n[i].toLocaleString() + '</span>';
  const cb = row.querySelector('input');
  cb.addEventListener('change', () => {
    cb.checked ? active.add(i) : active.delete(i);
    scheduleRebuild();
  });
  row.querySelector('.zl').addEventListener('click', e => {
    e.preventDefault();
    const fs = featsByZip[i];
    if (!fs.length) return;
    const bb = L.latLngBounds(fs.map(f => [f.geometry.coordinates[1], f.geometry.coordinates[0]]));
    map.fitBounds(bb.pad(0.06));
  });
  listEl.appendChild(row);
  rows.push({el: row, cb: cb, key: (zip + ' ' + D.city[i]).toLowerCase()});
});

document.getElementById('find').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  rows.forEach(r => { r.el.style.display = (!q || r.key.includes(q)) ? '' : 'none'; });
});
document.getElementById('bAll').onclick = () => bulk(true);
document.getElementById('bNone').onclick = () => bulk(false);
function bulk(on) {
  rows.forEach((r, i) => {
    if (r.el.style.display === 'none') return;
    r.cb.checked = on;
    on ? active.add(i) : active.delete(i);
  });
  scheduleRebuild();
}

/* rebuilding the cluster index over 250k points takes a beat -- debounce it */
const busy = document.getElementById('busy');
let timer = null;
function scheduleRebuild() {
  clearTimeout(timer);
  busy.textContent = 'Updating\u2026';
  busy.style.display = 'block';
  timer = setTimeout(() => {
    const n = buildIndex();
    render();
    busy.style.display = 'none';
    updateFoot(n);
  }, 220);
}

function updateFoot(shown) {
  document.getElementById('foot').textContent =
    shown.toLocaleString() + ' of ' + D.total.toLocaleString() + ' locations shown \u00B7 ' +
    active.size + ' of ' + D.zips.length + ' ZIPs';
}

const notesEl = document.getElementById('notes');
D.notes.forEach(t => { const li = document.createElement('li'); li.textContent = t; notesEl.appendChild(li); });

/* ---- panel ---- */
const app = document.getElementById('app'), nav = document.getElementById('navtoggle');
nav.onclick = () => {
  const c = app.classList.toggle('nav-collapsed');
  nav.innerHTML = c ? '\u2630 Layers' : '\u2715 Close';
  setTimeout(() => map.invalidateSize(), 280);
};
if (window.innerWidth < 820) nav.onclick();

/* ---- go ---- */
const total = buildIndex();
map.fitBounds([[D.bb[0], D.bb[1]], [D.bb[2], D.bb[3]]], {padding: [20, 20]});
map.on('moveend', render);   /* Leaflet fires zoomend then moveend -- one hook is enough */
render();
updateFoot(total);
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("final", help="<NAME>_addresses_final.csv")
    ap.add_argument("--name", required=True)
    ap.add_argument("--title", required=True, help='map heading, e.g. "Dallas-Fort Worth"')
    ap.add_argument("--subtitle", default="AT&amp;T Fiber serviceable addresses")
    ap.add_argument("--include-far", default="yes", choices=["yes", "no"],
                    help="keep FAR_FROM_ZIP rows (default yes)")
    ap.add_argument("--cluster-radius", type=int, default=60)
    ap.add_argument("--outdir", default=".")
    a = ap.parse_args()

    drop = {"NO_MATCH", "FAR_FROM_ZIP_SEVERE"}
    if a.include_far == "no":
        drop.add("FAR_FROM_ZIP")

    # collapse exact-coordinate stacks to one pin carrying a count
    pts = {}
    excluded = defaultdict(int)
    state = ""
    rows_in = 0
    for r in csv.DictReader(open(a.final)):
        rows_in += 1
        if r["qa_flag"] in drop or not r["lat"]:
            excluded[r["qa_flag"]] += 1
            continue
        state = state or r["state"]
        key = (round(float(r["lat"]), 5), round(float(r["lon"]), 5))
        if key in pts:
            pts[key][3] += 1
        else:
            pts[key] = [r["street"], r["zip"], r["city"], 1]

    if not pts:
        raise SystemExit("no mappable rows -- check the qa_flag column")

    # ZIP order: biggest first, so the palette's strongest colors land on the big areas
    byzip = defaultdict(list)
    for (lat, lon), (street, zipc, city, units) in pts.items():
        byzip[zipc].append((lat, lon, street, city, units))
    order = sorted(byzip, key=lambda z: -len(byzip[z]))
    zidx = {z: i for i, z in enumerate(order)}

    zips, cities, counts = [], [], []
    for z in order:
        zips.append(z or "(no ZIP)")
        cities.append(max(set(p[3] for p in byzip[z]), key=[p[3] for p in byzip[z]].count) or "")
        counts.append(len(byzip[z]))

    # flatten sorted by (zip, lat) so coordinate deltas stay small and street text
    # from the same area sits adjacent -- both make the gzip much tighter
    flat = []
    for z in order:
        for p in sorted(byzip[z], key=lambda p: (p[0], p[1])):
            flat.append((p[0], p[1], zidx[z], p[2], p[4]))

    dlat, dlon, zs, streets, units = [], [], [], [], []
    pl = po = 0
    for lat, lon, zi, street, u in flat:
        ilat, ilon = int(round(lat * 1e5)), int(round(lon * 1e5))
        dlat.append(ilat - pl)
        dlon.append(ilon - po)
        pl, po = ilat, ilon
        zs.append(zi)
        streets.append(street)
        units.append(u)

    lats = [p[0] for p in flat]
    lons = [p[1] for p in flat]

    notes = [
        f"{len(flat):,} mapped locations from {rows_in:,} input rows.",
        "Pins group into counted bubbles when zoomed out and split apart as you zoom in. "
        "Click a bubble to zoom into it; click a pin for the street address.",
        "Click a ZIP name in the list to zoom to it; use the checkbox to hide it.",
    ]
    stacked = sum(1 for u in units if u > 1)
    if stacked:
        notes.append(f"{stacked:,} locations carry more than one address (multi-family "
                     f"buildings or addresses the geocoder placed on the same point).")
    if excluded:
        parts = []
        if excluded.get("NO_MATCH"):
            parts.append(f"{excluded['NO_MATCH']:,} could not be matched to a coordinate")
        if excluded.get("FAR_FROM_ZIP_SEVERE"):
            parts.append(f"{excluded['FAR_FROM_ZIP_SEVERE']:,} landed far outside their own ZIP")
        if excluded.get("FAR_FROM_ZIP"):
            parts.append(f"{excluded['FAR_FROM_ZIP']:,} were flagged for review")
        if parts:
            notes.append("Held out of the map: " + "; ".join(parts) + ".")

    D = {
        "st": state,
        "zips": zips, "city": cities, "n": counts,
        "lat": dlat, "lon": dlon, "z": zs, "s": streets, "u": units,
        "cols": PALETTE,
        "total": len(flat),
        "bb": [min(lats), min(lons), max(lats), max(lons)],
        "notes": notes,
    }
    raw = json.dumps(D, separators=(",", ":"))
    payload = base64.b64encode(gzip.compress(raw.encode(), 9)).decode()

    html = (SKELETON
            .replace("__TITLE__", a.title.replace("&", "&amp;"))
            .replace("__SUBTITLE__", a.subtitle)
            .replace("__CLUSTER_RADIUS__", str(a.cluster_radius))
            .replace("__PAYLOAD__", payload))

    out = os.path.join(a.outdir, f"{a.name}_address_map.html")
    with open(out, "w") as f:
        f.write(html)

    print(f"{rows_in:,} rows in -> {len(flat):,} mapped pins across {len(order)} ZIPs")
    for k, v in sorted(excluded.items()):
        print(f"  excluded {k:<20} {v:,}")
    print(f"\npayload {len(raw)/1e6:.1f} MB json -> {len(payload)/1e6:.1f} MB embedded "
          f"({len(raw)/max(len(payload),1):.1f}x)")
    print(f"wrote {out}  ({os.path.getsize(out)/1e6:.1f} MB)")
    print("\nQA: open in a browser -- confirm pins sit on streets, cluster counts add up "
          "to the footer total, ZIP toggles and zoom-to work.")


if __name__ == "__main__":
    main()
