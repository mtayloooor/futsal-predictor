# CLAUDE.md

Reference for future Claude agents working in this repo.

## What this is

A single-page futsal/football league table predictor for Melbourne Social Futsal (Carlton North Mixed Wednesday Nights, futsalhq.com.au). Users load current standings + remaining fixtures, then enter hypothetical scores to see how the table would shake out. Deployed via GitHub Pages: https://mtayloooor.github.io/futsal-predictor/

## Repo layout

```
README.md                              - User-facing usage notes (data sources)
index.html                             - The entire app (HTML + Tailwind + React via CDN + Babel JSX)
futsal_results_website_crawler.py      - Playwright + BeautifulSoup scraper that produces a season JSON
.github/workflows/crawl.yml            - Weekly GitHub Actions cron that runs the crawler and commits
seasons/manifest.json                  - Lists every season JSON file in this repo
seasons/<season-id>.json               - One file per season (current + historic snapshots)
```

There is no build step, no package.json, no tests. Edits to `index.html` ship by committing — GitHub Pages serves it directly.

## Season files & manifest

`seasons/manifest.json` is the discovery mechanism (GitHub raw doesn't list directories). Shape:

```
{
  "latest": "2025-11",
  "seasons": [
    { "id": "2025-11", "label": "Nov 2025 – May 2026",
      "file": "2025-11.json", "competition_url": "..." }
  ]
}
```

Season **IDs are derived from data, not configured**. The crawler reads every match date from `team_results`, takes the first one's `YYYY-MM`, and uses that as the ID. The label is `"MMM YYYY – MMM YYYY"` spanning first and last dates. This is stable across weekly updates because the first round's date is fixed once a season starts; only the last date drifts as new fixtures get posted.

The crawler auto-upserts the manifest entry and sets `latest = <derived_id>` on every run. When the futsalhq site rolls over to a new season, the crawl produces a new ID (different `YYYY-MM` for the first match), creates a new JSON file, adds a manifest entry, and flips `latest`. The previous season's file stays as the last snapshot before the website wiped its data.

**Important:** futsalhq.com.au serves only the currently-active season at `COMPETITION_TABLE_URL`. There is no way to re-crawl a historic season from the source — old seasons exist only as committed snapshots in this repo. Don't delete them.

## Automated weekly crawl

`.github/workflows/crawl.yml` runs the crawler every Wednesday at 22:30 Melbourne (cron `30 11 * * 3` UTC, accurate during AEDT / the active season). It installs Playwright + Chromium, runs the script, and commits any diff in `seasons/` back to `main` using the built-in `GITHUB_TOKEN`. Manual runs via `workflow_dispatch` from the Actions tab.

## Tech stack (all via CDN, no bundler)

- React 18 + ReactDOM (UMD prod builds from unpkg)
- `@babel/standalone` to transform the inline JSX at runtime
- Tailwind CSS via `cdn.tailwindcss.com`
- All app code lives in a single `<script type="text/babel">` block inside `index.html`

Because Babel transforms on the client, expect a brief blank-flash on load. Don't try to "modernise" this into Vite/Next unless the user explicitly asks — the simplicity (one static file → GitHub Pages) is the point.

## Data shape

### `futsal_season_data.json`
```
{
  "competition_url": string,
  "standings": [
    { "Team", "Played", "Wins", "Draws", "Losses", "BYEs",
      "For", "Against", "Diff.", "Points" }       // all stringified ints
  ],
  "teams": { [teamName]: teamPageUrl },
  "team_results": {
    [teamName]: [
      { "round": "Round N", "date", "time", "opponent", "opponent_url",
        "result": "Win"|"Loss"|"Draw"|null,        // null = unplayed (future fixture)
        "score_raw", "this_team_score": int, "opposing_team_score": int }
    ]
  }
}
```

Played matches have `result` set; future fixtures have `result: null` and are used to seed the predictions list. Each match appears twice (once per team) in `team_results`, so dedup with a `R{n}-{sortedTeamA}-{sortedTeamB}` key when iterating.

### Internal team object (`baseTable` state)
`{ name, p, w, d, l, b, f, a, gd, pts, baseRank }`

### Prediction object
`{ id, round, teamA, teamB, scoreA, scoreB, isBye, time }`

## App structure (in `index.html`)

The single `App` component drives three tabs via `activeTab`:
1. **`import`** (Data Setup): three import paths — fetch latest from GitHub (loads ALL seasons via the manifest), upload a single JSON, or paste raw text from futsalhq.com.au.
2. **`history`** (Progress / History): rank-trajectory SVG chart, highlight cards (longest win streak, highest-scoring game, biggest blowout), per-round scrubber table, upcoming fixtures. The tab label flips from "Progress" to "History" when the user is viewing a non-latest season.
3. **`predict`** (Predictions): sticky predicted league table + per-round score entry. Recomputes via the `predictedTable` `useMemo` whenever predictions change. **Disabled when viewing a historic season** — past seasons have no unplayed fixtures.

A header-level season `<select>` appears whenever multiple seasons have been loaded (i.e. only via "Use Latest Data"). Switching season re-runs `processParsedJSON` against that season's JSON. Predictions are only seeded for the latest season; switching wipes any user-entered hypothetical scores on the latest season — that's a known trade-off, not a bug.

Key functions to know:
- `processParsedJSON(json, { seedPredictions = true, switchToHistory = true } = {})` — the main pipeline. Parses standings → builds full historical table per round → computes metrics → seeds predictions from unplayed fixtures. Options let the season switcher reuse it without touching the active tab or wiping predictions inappropriately.
- `handleFetchLatest()` — fetches `seasons/manifest.json` then every season JSON in parallel; populates `availableSeasons`, `loadedSeasons`, and processes the `latest` one.
- `handleSeasonChange(id)` — re-processes a different season's JSON in place; only seeds predictions if `id === latestSeasonId`.
- `handleParse()` — the manual paste fallback. Parses tab/space-separated league table text plus a free-form schedule text (rounds, BYE lines, time-prefixed fixture rows).
- `predictedTable` (useMemo) — clones baseTable, applies predictions, re-sorts by `pts → gd → f`.
- `getChartCoordinates()` — builds the SVG rank-trajectory paths.

Sort/tiebreak rule used everywhere: **points → goal difference → goals for**.

## Constants worth knowing

- `GITHUB_RAW_BASE` / `GITHUB_MANIFEST_URL` / `seasonFileUrl()` (line ~269): all point at the `main` branch raw URL. Update if the repo moves or the data layout changes.
- `DEFAULT_LEAGUE_TABLE` / `DEFAULT_SCHEDULE` (lines ~31, ~44): seed text for the manual-entry textareas, useful as a parsing fixture.
- `TEAM_COLORS`: 14 hex colors cycled by team index for the chart and legend.

## Design conventions

- Tailwind utility classes only, no custom CSS beyond the `<style>` block (body bg + hide-scrollbar).
- Rounded `rounded-2xl`/`rounded-xl` cards with `shadow-sm border border-gray-200` is the default surface.
- Inline SVG icon components (`IconUp`, `IconDown`, `IconTrophy`, etc.) — no icon library.
- Blue (`blue-600`) is primary action; green = wins/positive; red = losses/negative; sky = "fetch latest" CTA.

## When making changes

- The whole app is one file — search inside `index.html` rather than expecting separate components.
- If you change the JSON schema, update both `processParsedJSON` and the scraper that produces `futsal_season_data.json` (the scraper lives outside this repo — flag schema changes to the user).
- Keep the no-build-step constraint: don't introduce imports, JSX outside the existing `<script type="text/babel">`, or anything that needs npm.
- Test by opening `index.html` directly in a browser, or via `python3 -m http.server` from the repo root.

## Branch convention

The web-based Claude sessions are pinned to feature branches like `claude/<name>` per session prompt. Develop there and push; do not push to `main` without explicit instruction.
