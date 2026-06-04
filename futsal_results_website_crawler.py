import asyncio
import json
import os
import re
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
# Update these two values when crawling a different season.
# COMPETITION_TABLE_URL: the futsalhq.com.au "View competition table" URL.
# SEASON_ID: short identifier used as the JSON filename inside seasons/.
#            Also add a matching entry to seasons/manifest.json afterwards.
COMPETITION_TABLE_URL = "https://www.futsalhq.com.au/pointscore/7636/16040"
SEASON_ID = "2025-26-summer"
OUTPUT_FILE = os.path.join("seasons", f"{SEASON_ID}.json")

async def extract_standings_and_teams(page):
    """
    Visits the competition table, extracts the full standings data (Played, Wins, Points, etc.)
    and builds the dictionary of team names to their unique URLs.
    """
    print(f"[*] Fetching competition table from: {COMPETITION_TABLE_URL}")
    teams_data = {}
    standings_data = []

    try:
        await page.goto(COMPETITION_TABLE_URL, wait_until="networkidle", timeout=20000)
        html_content = await page.content()
        soup = BeautifulSoup(html_content, 'html.parser')

        # Focus on the specific table using the classes provided
        table = soup.find('table', class_=lambda x: x and all(c in x.split() for c in ['table', 'table-hover']))
        if not table:
            print("[-] Could not find the standings table. Check the page structure.")
            return teams_data, standings_data

        # Extract headers dynamically so we map the data accurately
        headers = []
        thead = table.find('thead')
        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all('th')]
        else:
            # Fallback if there is no thead tag
            headers = ["Team", "Played", "Wins", "Draws", "Losses", "BYEs", "For", "Against", "Diff.", "Points"]

        # Extract rows
        tbody = table.find('tbody')
        rows = tbody.find_all('tr') if tbody else table.find_all('tr')

        for row in rows:
            tds = row.find_all(['td', 'th'])
            # Skip if it's a header row inside tbody or empty
            if not tds or (len(tds) > 0 and tds[0].name == 'th'):
                continue

            # Find the team link to build our URL dictionary
            a_tag = row.find('a', href=re.compile(r'/games/team/'))
            if a_tag:
                team_name = a_tag.get_text(strip=True)
                full_url = urljoin(COMPETITION_TABLE_URL, a_tag['href'])
                teams_data[team_name] = full_url

                # Build the stats object for this row
                row_stats = {}
                for i, td in enumerate(tds):
                    # Use the header name if available, otherwise fallback to column index
                    col_name = headers[i] if i < len(headers) else f"Col_{i}"

                    if col_name == "Team":
                        # Use the clean team_name from the <a> tag to avoid the standing numbers (e.g., "1. ")
                        row_stats[col_name] = team_name
                    else:
                        # Some columns might have nested tags, so get_text safely extracts it
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
        await page.goto(team_url, wait_until="networkidle", timeout=15000)
        html_content = await page.content()
        soup = BeautifulSoup(html_content, 'html.parser')

        # Find all opponent links
        opponent_links = soup.find_all('a', href=re.compile(r'/games/team/'))

        for opp_tag in opponent_links:
            # Traverse up to the parent row container to capture the whole game block
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

            # Extract Round, Date, Time
            # Found in the adjacent column with class 'col-md'
            round_div = row.find('div', class_=lambda x: x and 'col-md' in x.split())
            if round_div:
                texts = list(round_div.stripped_strings)
                if len(texts) >= 1:
                    match_data["round"] = texts[0]
                if len(texts) >= 2:
                    match_data["date"] = texts[1]
                if len(texts) >= 3:
                    match_data["time"] = texts[2]

            # Extract Result (contained in a div with 'badge' class)
            result_tag = row.find('div', class_=re.compile(r'badge'))
            if result_tag:
                match_data["result"] = result_tag.get_text(strip=True)

            # Extract Score
            # Usually in a <b> tag near the opponent link within the col-lg-3 block
            score_container = opp_tag.find_parent('div', class_=lambda x: x and 'col-lg-3' in x)
            if score_container:
                for b in score_container.find_all('b'):
                    if '-' in b.get_text():
                        # Clean up whitespace formatting (fixes the "3 \n - \n 10" issue)
                        raw_score = b.get_text(separator=' ', strip=True)
                        match_data["score_raw"] = raw_score

                        # Parse score string (e.g. "3 - 10" -> 3, 10)
                        score_match = re.search(r'(\d+)\s*-\s*(\d+)', raw_score)
                        if score_match:
                            s1 = int(score_match.group(1))
                            s2 = int(score_match.group(2))

                            res_text = match_data["result"].lower() if match_data["result"] else ""

                            # Assign scores logically based on match result
                            if "win" in res_text:
                                match_data["this_team_score"] = max(s1, s2)
                                match_data["opposing_team_score"] = min(s1, s2)
                            elif "loss" in res_text:
                                match_data["this_team_score"] = min(s1, s2)
                                match_data["opposing_team_score"] = max(s1, s2)
                            elif "draw" in res_text:
                                match_data["this_team_score"] = s1
                                match_data["opposing_team_score"] = s2
                        break # Found the score tag, stop looking

            team_matches.append(match_data)

        return team_matches

    except Exception as e:
        print(f"  [-] Failed to load {team_name} ({team_url}): {e}")
        return team_matches

async def run_crawler():
    final_export = {
        "competition_url": COMPETITION_TABLE_URL,
        "standings": [],
        "teams": {},
        "team_results": {}
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # STEP 1: Extract Teams and Standings from the Competition Table
        teams_dict, standings_list = await extract_standings_and_teams(page)
        final_export["teams"] = teams_dict
        final_export["standings"] = standings_list

        # STEP 2: Follow Team URLs and Extract Results
        print("\n[*] Starting team results extraction...")
        for team_name, team_url in teams_dict.items():
            results = await extract_team_results(page, team_name, team_url)
            final_export["team_results"][team_name] = results
            # Small delay to be polite to the server
            await asyncio.sleep(1)

        await browser.close()

    return final_export

if __name__ == "__main__":
    print("========================================")
    print("   FUTSALHQ SEASON CRAWLER INITIALIZED  ")
    print("========================================\n")

    # Run the crawler
    data = asyncio.run(run_crawler())

    # Save results to JSON file
    try:
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
        print("\n" + "="*40)
        print(f"CRAWL COMPLETE. Data successfully saved to {OUTPUT_FILE}")
        print("="*40)
        print("\nNext: add an entry for this season to seasons/manifest.json")
        print(f"  {{ \"id\": \"{SEASON_ID}\", \"label\": \"...\", \"file\": \"{SEASON_ID}.json\" }}")
    except Exception as e:
        print(f"\n[-] Error saving JSON to file: {e}")
