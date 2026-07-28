#!/usr/bin/env python3
"""
resolve_schema.py -- teach the pipeline a column layout it has never seen.

Runs only when clean_addresses.py cannot find an address column on a tab. Sends
the header row and a few sample rows to the Anthropic API, gets back a mapping of
which column holds which field, and appends it to schemas.json. That layout is
then recognized deterministically forever -- one call per new format, ever.

    python3 resolve_schema.py FILE.xlsx --tab "SHEET NAME"

WHAT IS SENT: the header row, plus up to 3 sample rows, from one tab. That is
enough to tell an address column from a billing column and no more. Set
--headers-only to send column names with no sample values at all, at some cost to
accuracy on ambiguous headers.

WHAT COMES BACK: column names only. The model never supplies address values,
never supplies coordinates, and never transforms data. Its entire output is a
dict of role -> existing column name, and anything naming a column that isn't in
the sheet is discarded. A wrong answer here produces a wrong column choice, which
validate.py catches downstream -- it cannot introduce invented data.

Needs ANTHROPIC_API_KEY. Exits 2 if unset, so the caller can report the layout as
unrecognized rather than crashing.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

MODEL = "claude-sonnet-4-6"
ENDPOINT = "https://api.anthropic.com/v1/messages"
ROLES = ["id", "street", "unit", "city", "state", "zip", "lat", "lon"]

PROMPT = """This is the header row and sample rows from one sheet of an address \
spreadsheet. Identify which column holds each field.

Headers:
{headers}

Sample rows:
{samples}

Return ONLY a JSON object, no preamble, no markdown fences, with these keys:
  street  the street address (house number + street name). Required.
  city    city or municipality
  state   state, 2-letter or full name
  zip     ZIP or postal code
  id      a per-row unique identifier, if one exists
  unit    apartment/suite/unit designator, if it has its own column
  lat     latitude
  lon     longitude

Each value must be a column name copied EXACTLY from the Headers list above, or \
null if no column fits. Do not guess at a column that isn't clearly the field. Do \
not invent column names. If the street address is split across several columns \
(number, name, type), set street to the column holding the street NAME and add a \
"street_parts" key listing all of them in reading order."""


def sample_rows(path, tab, n=3, headers_only=False):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[tab]
    it = ws.iter_rows(values_only=True)
    headers = None
    for row in it:
        if row and sum(1 for c in row if c not in (None, "")) >= 2:
            headers = [str(c or "") for c in row]
            break
    rows = []
    if headers and not headers_only:
        for row in it:
            if row is None or all(c in (None, "") for c in row):
                continue
            rows.append(["" if c is None else str(c)[:60] for c in row])
            if len(rows) >= n:
                break
    wb.close()
    return headers or [], rows


def ask(headers, rows, key):
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1000,
        "messages": [{"role": "user", "content": PROMPT.format(
            headers="\n".join(f"- {h}" for h in headers),
            samples="\n".join(" | ".join(r) for r in rows) or "(none provided)")}],
    }).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        "content-type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
    })
    with urllib.request.urlopen(req, timeout=90) as r:
        payload = json.load(r)
    text = "".join(b.get("text", "") for b in payload.get("content", [])
                   if b.get("type") == "text").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.startswith("json") else text
    return json.loads(text.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--tab", required=True)
    ap.add_argument("--schemas", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "schemas.json"))
    ap.add_argument("--headers-only", action="store_true",
                    help="send column names with no sample values")
    a = ap.parse_args()

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("ANTHROPIC_API_KEY not set -- cannot resolve an unknown layout",
              file=sys.stderr)
        return 2

    headers, rows = sample_rows(a.xlsx, a.tab, headers_only=a.headers_only)
    if not headers:
        print(f"no header row found on '{a.tab}'", file=sys.stderr)
        return 1

    try:
        got = ask(headers, rows, key)
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, KeyError) as e:
        print(f"schema resolution failed: {e}", file=sys.stderr)
        return 1

    # keep only roles naming a column that actually exists
    hset = set(headers)
    mapping = {r: (got.get(r) if got.get(r) in hset else None) for r in ROLES}
    parts = [p for p in (got.get("street_parts") or []) if p in hset]

    if not mapping["street"] and not parts:
        print(f"no address column identified on '{a.tab}' -- needs a human",
              file=sys.stderr)
        return 1
    if not mapping["city"] and not mapping["zip"]:
        print(f"'{a.tab}': found an address column but no city and no ZIP -- "
              f"not enough to place an address", file=sys.stderr)
        return 1

    # fingerprint on the columns we actually rely on, so an unrelated sheet that
    # happens to share one header name can't match this layout
    fp = [mapping[r] for r in ("street", "city", "state", "zip") if mapping[r]]

    store = {"version": 1, "layouts": []}
    if os.path.exists(a.schemas):
        with open(a.schemas) as f:
            store = json.load(f)
    store.setdefault("layouts", [])

    entry = {
        "name": f"learned from tab '{a.tab}'",
        "fingerprint": fp,
        "map": mapping,
        "added": __import__("datetime").date.today().isoformat(),
        "source": f"resolve_schema.py, {os.path.basename(a.xlsx)}",
    }
    if parts:
        entry["street_parts"] = parts

    # replace an existing entry with the same fingerprint rather than stacking
    store["layouts"] = [l for l in store["layouts"]
                        if sorted(l.get("fingerprint", [])) != sorted(fp)]
    store["layouts"].append(entry)
    with open(a.schemas, "w") as f:
        json.dump(store, f, indent=2)
        f.write("\n")

    print(f"learned '{a.tab}': " + ", ".join(
        f"{r}={mapping[r]}" for r in ROLES if mapping[r]))
    if parts:
        print("  street parts: " + ", ".join(parts))
    print(f"appended to {a.schemas} -- commit this file so it is not relearned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
