import asyncio
import json
import os
import re
from datetime import datetime
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
# The competition URL is stable across seasons — futsalhq.com.au serves only
# the currently-active season at this URL, so each crawl produces the
# latest-season snapshot. Season ID and label are derived from the match
# dates returned by the crawl, not configured here.
COMPETITION_TABLE_URL = "https://www.futsalhq.com.au/pointscore/7636/16040"
SEASONS_DIR = "seasons"
MANIFEST_FILE = os.path.join(SEASONS_DIR, "manifest.json")
DEBUG_DIR = "debug"
DATE_FORMAT = "%a %d %b %Y"  # e.g. "Wed 19 Nov 2025"

# Headless Chromium with the default user-agent gets light bot-screening from
# some sites. A normal Chrome UA avoids that.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


async def dump_debug(page, label):
    """Save the rendered HTML and a screenshot for post-mortem debugging."""
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        html_path = os.path.join(DEBUG_DIR, f"{label}.html")
        png_path = os.path.join(DEBUG_DIR, f"{label}.png")
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        await page.screenshot(path=png_path, full_page=True)
        print(f"    [debug] saved {html_path} and {png_path}")
    except Exception as e:
        print(f"    [debug] failed to dump debug artefacts: {e}")

async def extract_standings_and_teams(page):
    """
    Visits the competition table, extracts the full standings data (Played, Wins, Points, etc.)
    and builds the dictionary of team names to their unique URLs.
    """
    print(f"[*] Fetching competition table from: {COMPETITION_TABLE_URL}")
    teams_data = {}
    standings_data = []

    try:
        await page.goto(COMPETITION_TABLE_URL, wait_until="domcontentloaded", timeout=30000)
        # Wait for the standings table itself instead of relying on networkidle
        # (some pages keep long-running connections open and never go idle).
        try:
            await page.wait_for_selector("table.table-hover tbody tr", timeout=20000)
        except Exception as e:
            print(f"[-] Standings table never appeared: {e}")
            await dump_debug(page, "standings-missing")
            return teams_data, standings_data

        html_content = await page.content()
        soup = BeautifulSoup(html_content, 'html.parser')

        table = soup.find('table', class_=lambda x: x and all(c in x.split() for c in ['table', 'table-hover']))
        if not table:
            print("[-] Could not find the standings table. Check the page structure.")
            await dump_debug(page, "standings-missing")
            return teams_data, standings_data

        headers = []
        thead = table.find('thead')
        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all('th')]
        else:
            headers = ["Team", "Played", "Wins", "Draws", "Losses", "BYEs", "For", "Against", "Diff.", "Points"]

        tbody = table.find('tbody')
        rows = tbody.find_all('tr') if tbody else table.find_all('tr')

        for row in rows:
            tds = row.find_all(['td', 'th'])
            if not tds or (len(tds) > 0 and tds[0].name == 'th'):
                continue

            a_tag = row.find('a', href=re.compile(r'/games/team/'))
            if a_tag:
                team_name = a_tag.get_text(strip=True)
                full_url = urljoin(COMPETITION_TABLE_URL, a_tag['href'])
                teams_data[team_name] = full_url

                row_stats = {}
                for i, td in enumerate(tds):
                    col_name = headers[i] if i < len(headers) else f"Col_{i}"
                    if col_name == "Team":
                        row_stats[col_name] = team_name
                    else:
                        row_stats[col_name] = td.get_text(separator=' ', strip=True)
                standings_data.append(row_stats)

        print(f"[+] Found {len(teams_data)} teams and extracted standings.")
        return teams_data, standings_data

    except Exception as e:
        print(f"[-] Error extracting standings: {e}")
        return teams_data, standings_data

async def extract_team_results(page, team_name, team_url):
    """
    Visits a specific team's page and extracts their scheduled games,
    opponents, scores, match results, round, date, and time.
    """
    print(f"  -> Crawling results for {team_name}...")
    team_matches = []

    try:
        await page.goto(team_url, wait_until="domcontentloaded", timeout=30000)
        # Each team should have at least one fixture link; wait for any to
        # appear. Allow this to time out without aborting — a brand-new team
        # could legitimately have no games listed yet.
        try:
            await page.wait_for_selector("a[href*='/games/team/']", timeout=15000)
        except Exception:
            pass

        html_content = await page.content()
        soup = BeautifulSoup(html_content, 'html.parser')

        opponent_links = soup.find_all('a', href=re.compile(r'/games/team/'))
        if not opponent_links:
            await dump_debug(page, f"team-{re.sub(r'[^A-Za-z0-9]+', '-', team_name).strip('-')}")

        for opp_tag in opponent_links:
            row = opp_tag.find_parent('div', class_=lambda c: c and 'row' in c.split())
            if not row:
                continue

            match_data = {
                "round": None,
                "date": None,
                "time": None,
                "opponent": opp_tag.get_text(strip=True),
                "opponent_url": urljoin(team_url, opp_tag['href']),
                "result": None,
                "score_raw": None,
                "this_team_score": None,
                "opposing_team_score": None
            }

            round_div = row.find('div', class_=lambda x: x and 'col-md' in x.split())
            if round_div:
                texts = list(round_div.stripped_strings)
                if len(texts) >= 1:
                    match_data["round"] = texts[0]
                if len(texts) >= 2:
                    match_data["date"] = texts[1]
                if len(texts) >= 3:
                    match_data["time"] = texts[2]

            result_tag = row.find('div', class_=re.compile(r'badge'))
            if result_tag:
                match_data["result"] = result_tag.get_text(strip=True)

            score_container = opp_tag.find_parent('div', class_=lambda x: x and 'col-lg-3' in x)
            if score_container:
                for b in score_container.find_all('b'):
                    if '-' in b.get_text():
                        raw_score = b.get_text(separator=' ', strip=True)
                        match_data["score_raw"] = raw_score

                        score_match = re.search(r'(\d+)\s*-\s*(\d+)', raw_score)
                        if score_match:
                            s1 = int(score_match.group(1))
                            s2 = int(score_match.group(2))
                            res_text = match_data["result"].lower() if match_data["result"] else ""

                            if "win" in res_text:
                                match_data["this_team_score"] = max(s1, s2)
                                match_data["opposing_team_score"] = min(s1, s2)
                            elif "loss" in res_text:
                                match_data["this_team_score"] = min(s1, s2)
                                match_data["opposing_team_score"] = max(s1, s2)
                            elif "draw" in res_text:
                                match_data["this_team_score"] = s1
                                match_data["opposing_team_score"] = s2
                        break

            team_matches.append(match_data)

        return team_matches

    except Exception as e:
        print(f"  [-] Failed to load {team_name} ({team_url}): {e}")
        return team_matches


def filter_to_current_season(team_results):
    """
    futsalhq team pages list a team's ENTIRE match history across every
    season they've played, while the pointscore page shows only the
    currently-active season's standings. Without filtering, a crawl just
    after season rollover produces incoherent output — current-season
    standings (e.g. 1 game played per team) glued to a multi-season
    fixture list (rounds 1-22 of the previous season + Round 1 of the new).

    The current season's start is the most recent date on which any team
    played "Round 1". Keep only matches whose date is on or after that.
    Matches missing a parseable date are kept as a safety net.
    """
    round_one_dates = []
    for matches in team_results.values():
        for m in matches:
            round_text = (m.get("round") or "").strip().lower()
            if round_text != "round 1":
                continue
            raw = m.get("date")
            if not raw:
                continue
            try:
                round_one_dates.append(datetime.strptime(raw, DATE_FORMAT))
            except ValueError:
                continue

    if not round_one_dates:
        return team_results

    season_start = max(round_one_dates)
    print(f"[*] Current-season Round 1 detected on {season_start.strftime(DATE_FORMAT)} — filtering out earlier matches.")

    filtered = {}
    dropped = 0
    for team, matches in team_results.items():
        kept = []
        for m in matches:
            raw = m.get("date")
            if not raw:
                kept.append(m)
                continue
            try:
                d = datetime.strptime(raw, DATE_FORMAT)
            except ValueError:
                kept.append(m)
                continue
            if d >= season_start:
                kept.append(m)
            else:
                dropped += 1
        filtered[team] = kept

    if dropped:
        print(f"[+] Dropped {dropped} cross-season matches from team_results.")
    return filtered


def derive_season_id_and_label(team_results):
    """
    Derive a stable season identifier and human-readable label from the
    actual match dates returned by the crawl.

    ID  = first-match `YYYY-MM` (anchors on the season start, immune to
          last-round date shifting by a week as more fixtures are posted).
    Label = "MMM YYYY – MMM YYYY" spanning first and last match.
    """
    dates = []
    for matches in team_results.values():
        for m in matches:
            raw = m.get("date")
            if not raw:
                continue
            try:
                dates.append(datetime.strptime(raw, DATE_FORMAT))
            except ValueError:
                continue

    if not dates:
        raise RuntimeError(
            "Could not derive a season identifier: no parseable match dates "
            "found in the crawl output. Check the page structure or DATE_FORMAT."
        )

    first, last = min(dates), max(dates)
    season_id = first.strftime("%Y-%m")
    label = f"{first.strftime('%b %Y')} – {last.strftime('%b %Y')}"
    return season_id, label


def update_manifest(season_id, label, competition_url):
    """
    Upsert the season entry in seasons/manifest.json and mark it as latest.
    Writes atomically (temp file + rename) so a crash mid-write can't
    corrupt the manifest.
    """
    if os.path.exists(MANIFEST_FILE):
        with open(MANIFEST_FILE, encoding="utf-8") as f:
            manifest = json.load(f)
    else:
        manifest = {"latest": None, "seasons": []}

    entry = {
        "id": season_id,
        "label": label,
        "file": f"{season_id}.json",
        "competition_url": competition_url,
    }

    seasons = [s for s in manifest.get("seasons", []) if s.get("id") != season_id]
    seasons.append(entry)
    # YYYY-MM IDs sort lexicographically = chronologically.
    seasons.sort(key=lambda s: s.get("id", ""))

    manifest["seasons"] = seasons
    manifest["latest"] = season_id

    tmp = MANIFEST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, MANIFEST_FILE)


async def run_crawler():
    final_export = {
        "competition_url": COMPETITION_TABLE_URL,
        "standings": [],
        "teams": {},
        "team_results": {}
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=USER_AGENT)
        page = await context.new_page()

        teams_dict, standings_list = await extract_standings_and_teams(page)
        final_export["teams"] = teams_dict
        final_export["standings"] = standings_list

        print("\n[*] Starting team results extraction...")
        for team_name, team_url in teams_dict.items():
            results = await extract_team_results(page, team_name, team_url)
            final_export["team_results"][team_name] = results
            await asyncio.sleep(1)

        await browser.close()

    return final_export


if __name__ == "__main__":
    print("========================================")
    print("   FUTSALHQ SEASON CRAWLER INITIALIZED  ")
    print("========================================\n")

    data = asyncio.run(run_crawler())

    data["team_results"] = filter_to_current_season(data["team_results"])

    season_id, label = derive_season_id_and_label(data["team_results"])
    output_file = os.path.join(SEASONS_DIR, f"{season_id}.json")
    os.makedirs(SEASONS_DIR, exist_ok=True)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    update_manifest(season_id, label, COMPETITION_TABLE_URL)

    print("\n" + "=" * 40)
    print(f"CRAWL COMPLETE.")
    print(f"  Season ID : {season_id}")
    print(f"  Label     : {label}")
    print(f"  Data file : {output_file}")
    print(f"  Manifest  : {MANIFEST_FILE} (latest = {season_id})")
    print("=" * 40)
