# Address maps

Upload a spreadsheet. The maps rebuild themselves.

## To update the maps

1. Click the **inbox** folder above
2. **Add file** → **Upload files**, and drag the spreadsheet in
3. Scroll down, click the green **Commit changes** button
4. Wait about two minutes

That's it. Nothing to install, nothing to run.

## Checking it worked

Click the **Actions** tab. The newest run at the top shows a green tick when the
maps are live, and a red X if something stopped them.

Either way, open the run and you'll see a table: how many addresses each market
has, how many made it onto the map, how many were held out. If a market failed a
check, it says which one and why, in plain English.

**If you see a red X, nothing was published and the maps people are already using
are untouched.** Nothing breaks on a bad upload. GitHub also emails you.

## The map URLs

One folder per market tab, named from the tab:

| Tab in the spreadsheet | URL |
|---|---|
| `534_ORLANDO` | `.../534-orlando/` |
| `686_MOBILE` | `.../686-mobile/` |
| `577_WILKES BARRE SCRANTON` | `.../577-wilkes-barre-scranton/` |

**These URLs never change.** A market keeps its address for good, so the embeds in
Google Sites only get set up once and then quietly show the newest data after
every upload. A tab that wasn't in the file before gets a new URL, which is the
only time you need to touch Google Sites.

Your base URL is on the **Settings → Pages** screen.

## Adding a map to Google Sites

In Sites: **Insert** → **Embed** → **By URL**, paste the market URL. For a
full-screen map use **Pages** → **Add** → **Full page embed** instead.

## Two things people ask

**Zoom all the way in and the pins sit in the road, not on the houses.** That's
expected. The coordinates mark the address along the street frontage, which is
the right answer for driving a route. It is not a mistake and there's nothing to
fix.

**A map is blank or won't load.** It needs an internet connection, because it
pulls its background map from the web. On a plane or a locked-down network it
won't draw.

## If something looks wrong

The audit trail for every run is on the Actions tab. Numbers that don't match what
you expect — a market far smaller than it should be, a city that doesn't belong —
are worth raising rather than working around.

---

Setup and maintenance notes are in [SETUP.md](SETUP.md).
