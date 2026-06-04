# CLAUDE.md

Reference for future Claude agents working in this repo.

## What this is

A single-page futsal/football league table predictor for Melbourne Social Futsal (Carlton North Mixed Wednesday Nights, futsalhq.com.au). Users load current standings + remaining fixtures, then enter hypothetical scores to see how the table would shake out. Deployed via GitHub Pages: https://mtayloooor.github.io/futsal-predictor/

## Repo layout

```
README.md                              - User-facing usage notes (data sources)
index.html                             - The entire app (HTML + Tailwind + React via CDN + Babel JSX)
futsal_results_website_crawler.py      - Playwright + BeautifulSoup scraper that produces a season JSON
seasons/manifest.json                  - Lists every season JSON file in this repo
seasons/<season-id>.json               - One file per season (current + historic snapshots)
```

There is no build step, no package.json, no tests. Edits to `index.html` ship by committing — GitHub Pages serves it directly.

## Season files & manifest

`seasons/manifest.json` is the discovery mechanism (GitHub raw doesn't list directories). Shape:

```
{
  "latest": "2025-26-summer",
  "seasons": [
    { "id": "2025-26-summer", "label": "Summer 2025/26",
      "file": "2025-26-summer.json", "competition_url": "..." }
  ]
}
```

The "Use Latest Data" flow fetches the manifest, then every referenced season JSON in parallel, then shows a season selector in the header. The two seasons-per-year cycle spans the calendar year boundary, so use IDs like `2025-26-summer` / `2026-winter`.

The crawler script reads `COMPETITION_TABLE_URL` and `SEASON_ID` from its config block and writes `seasons/<SEASON_ID>.json`. After running it for a new season, add a corresponding entry to `manifest.json` and (usually) flip `latest` to the new ID.

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
