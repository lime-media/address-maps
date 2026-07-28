# Setup and maintenance

One-time setup, then the repo runs itself. Everything below is for whoever owns
this, not for the person uploading files.

## One-time setup

**1. Create the repo, public.** Public matters: Actions minutes are unmetered and
outside collaborators don't consume a seat. Private costs a Team seat per uploader
and the Pages site would still be public unless you're on Enterprise Cloud, so it
buys nothing here.

**2. Push this folder to it.**

    git init && git branch -M main
    git add -A && git commit -m "address map pipeline"
    git remote add origin https://github.com/ORG/address-maps.git
    git push -u origin main

**3. Settings → Pages → Source: GitHub Actions.** Not "Deploy from a branch" — the
workflow publishes directly.

**4. Settings → Secrets and variables → Actions → New repository secret.**
Name `ANTHROPIC_API_KEY`, value from console.anthropic.com. Optional: without it,
a tab with an unrecognized column layout is reported and skipped instead of being
figured out. Everything else works unchanged.

**5. Add the uploader as an outside collaborator with Write access.** Settings →
Collaborators → Add people. *Outside collaborator, not an organization member* —
member costs a Team seat, outside collaborator on a public repo is free.

**6. Send them the repo URL and the README.** That's their whole interface.

## What runs, in order

    inbox/*.xlsx
      -> clean_addresses.py      detect layout, standardize text, split out
                                 rows needing coordinates
      -> resolve_schema.py       only when no layout matches: learn it
      -> censusgeocode_full.py   only for rows with no coordinates
      -> finalize_addresses.py   merge, ZIP-validate, QA flag
      -> build_address_map.py    one self-contained HTML per market
      -> validate.py             the gate
      -> Pages

## The gate

`validate.py` decides whether anything publishes. A failure deploys nothing and
leaves the live maps alone.

Blocks publishing:

- under 90% of rows mappable
- under 98% of coordinates inside the state the data claims
- no mappable rows
- every coordinate identical, which means that column is a placeholder

Publishes but flags:

- over 20% flagged far from their own ZIP
- a ZIP holding fewer than 5 addresses
- over 20% of addresses sharing a coordinate
- more than one state on a tab

Thresholds are constants at the top of the file. Loosen them only after looking at
what tripped, since the point of the gate is that nobody has to eyeball every run.

## Column layouts

`clean_addresses.py` knows the raw AT&T export, the Alloy RG export, F37, and a
generic fallback. Anything else, and `resolve_schema.py` sends the header row plus
three sample rows to the API, gets back a mapping of which column is which, and
appends it to `pipeline/schemas.json`.

**Commit `schemas.json` when it changes.** The run summary tells you when it has,
with the diff. Uncommitted, the same layout gets relearned every time — it still
works, it's just wasteful.

The model only ever maps column *names* to roles. It never supplies address values
or coordinates, and it never transforms data. A wrong answer produces a wrong
column choice, which the gate catches; it cannot introduce invented data. That
boundary is deliberate — keep it if you extend this.

Adding a layout by hand is also fine, and is better when you know the answer:
append to `layouts` with a `fingerprint` (headers that must all be present) and a
`map` of role to exact column name. Longest matching fingerprint wins. Built-in
schemas always beat learned ones.

## Costs

| | |
|---|---|
| Actions minutes | $0 — unmetered on public repos, every plan |
| Uploader's access | $0 — outside collaborator on a public repo |
| Pages | $0 |
| Census geocoding | $0, no key, roughly 92% match |
| Schema resolution | a few hundred tokens, only on a layout never seen before |

A run is about two minutes of Linux time. Metered it would be around a cent.

## Running it locally

    pip install -r requirements.txt
    python3 pipeline/publish.py            # everything in inbox/
    python3 pipeline/publish.py file.xlsx --tabs 534_ORLANDO
    python3 pipeline/validate.py work/

Maps land in `site/`, audit CSVs and geocoder checkpoints in `work/`. Both are
gitignored; the Action builds them fresh. Add `-v` for full per-stage output.

## Things that will eventually come up

**A market needs to disappear.** Delete the file containing it from `inbox/`. Pages
serves what's in `site/`, which is rebuilt from scratch each run, so it stops being
published. The Google Sites embed will then show a 404 — remove it there too.

**Two files contain the same tab.** Later filename wins. Dated filenames sort
correctly and make this predictable.

**A huge market times out.** Jobs cap at 6 hours. `work/` is cached between runs
and the geocoder skips finished IDs, so re-running from the Actions tab resumes.
Nothing is redone.

**Pins need to sit on buildings rather than at the curb.** That's a geocoder
change, not a settings change: `post_batch()` in `censusgeocode_full.py` is the
single swap point for a paid rooftop provider. Same per-id return shape, nothing
else changes. `finalize_addresses.py --parcel-offset` exists but is cosmetic and
does not improve accuracy — don't reach for it to answer an accuracy complaint.

**Addresses shouldn't be public any more.** Nothing here depends on the repo being
public except cost. Flipping to private needs a paid plan for Pages, a seat per
uploader, and Enterprise Cloud if the *site* also has to stop being public.
