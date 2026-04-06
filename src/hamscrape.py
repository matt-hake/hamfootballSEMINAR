#!/usr/bin/env python3
"""
Core PDF scraper for 2025 NESCAC football boxscore PDFs.

Responsibilities:
- discover stats boxscore links from schedule pages
- find PDF landing pages
- find direct PDF URLs
- download PDFs
- return structured results

Non-responsibilities:
- UI job orchestration
- runtime validation summary
- pytest unit testing
- API response formatting

CITE: CHATGPT 5.2 was used to supplement various sections of code,
and answer questions about BeautifulSoup usage.
"""

from __future__ import annotations

import re
import csv

# dataclass gives us lightweight structured record types 
from dataclasses import dataclass

# Path gives clean cross-platform file path handling
from pathlib import Path

# Type hints for clarity and maintainability
from typing import Dict, List, Optional, Set, Tuple

# urljoin resolves relative links; urlparse helps inspect URL paths
from urllib.parse import urljoin, urlparse

# RobotFileParser is used to check robots.txt permissions before scraping
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

# Custom exception types used to make failures more descriptive and structured
from errors import (
    DateParseError,
    DirectPdfNotFoundError,
    DownloadFailedError,
    HomeAwayParseError,
    InvalidSchoolError,
    PdfLandingNotFoundError,
    RobotsBlockedError,
    ScheduleFetchError,
    StatsFetchError,
)

# The season year this scraper is built for
YEAR = 2025

# path to client-editable csv config file (goes back two steps to find hamfootballSEMINAR)
BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_CSV = BASE_DIR / "config" / "schools.csv"

# Used when parsing dates written like "September 21, 2025"
MONTH_MAP = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

#ChatGPT-provided helper to load the information from a config file
def _load_school_config(csv_path: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Load school/base-url and slug/team-token mappings from a CSV file.

    Expected columns:
    - school
    - base_url
    - slug
    - team_token

    Returns:
        (schools_map, slug_to_team_map)
    """

    #if we can't access the csv file, raise an error
    if not csv_path.exists():
        raise FileNotFoundError(
            f"School config CSV not found: {csv_path}"
        )

    #initialize dictionaries to store schools' urls, slugs
    schools: Dict[str, str] = {}
    slug_to_team: Dict[str, str] = {}

    #encoding helps avoid extra text that may be at beginning of utf-8 file
    #CITE: ChatGPT 5.2 for debugging/error prevention/robustness
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        #read the rows of the csv file as dictionaries
        reader = csv.DictReader(f)

        required = {"school", "base_url", "slug", "team_token"}
        #check for header (categories)
        if not reader.fieldnames:
            raise ValueError(f"School config CSV has no header row: {csv_path}")

        #error check for missing fields
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"School config CSV is missing required columns: {', '.join(sorted(missing))}"
            )

        #go through rows, starting at the second bc first is the header
        for i, row in enumerate(reader, start=2):
            #extract values that align with columns
            school = (row.get("school") or "").strip()
            base_url = (row.get("base_url") or "").strip().rstrip("/")
            slug = (row.get("slug") or "").strip().lower()
            team_token = (row.get("team_token") or "").strip().upper()

            #error checks for missing values in csv files
            if not school:
                raise ValueError(f"Missing school value in row {i} of {csv_path}")
            if not base_url:
                raise ValueError(f"Missing base_url for school '{school}' in row {i} of {csv_path}")
            if not slug:
                raise ValueError(f"Missing slug for school '{school}' in row {i} of {csv_path}")
            if not team_token:
                raise ValueError(f"Missing team_token for school '{school}' in row {i} of {csv_path}")

            #add to dictionary
            schools[school] = base_url
            slug_to_team[slug] = team_token

    return schools, slug_to_team


# Mapping from school display name to the base athletics site URL
SCHOOLS, SLUG_TO_TEAM = _load_school_config(CONFIG_CSV)

# Mapping from URL slugs to the uppercase team name you want in filenames
# Example: "williams" -> "WILLIAMS"

#one stats/boxscore page from school's schedule page
#NOTE: data classes are used bc we want to easily access params, leverage built in boilerplate code
#CITE: https://thenewstack.io/python-dataclasses-a-complete-guide-to-boilerplatefree-objects/
#CITE: https://docs.python.org/3/library/dataclasses.html
@dataclass(frozen=True)
class StatsBoxscore:
    """
    Represents one stats/boxscore page discovered from a school's schedule page.
    """
    school: str
    url: str

#final result for one PDF-processing attempt
@dataclass(frozen=True)
class DownloadResult:
    """
    Represents the final result for one PDF-processing attempt.

    status is one of:
    - DOWNLOADED: file was successfully downloaded this run
    - EXISTS: file already existed on disk
    - DUPLICATE: same game/PDF was already encountered this run
    """
    source_school: str
    schedule_url: str
    stats_url: str
    game_id: str
    pdf_landing_url: str
    pdf_url: str
    home_team: str
    away_team: str
    month: int
    year: int
    filename: str # clear enough title?
    file_path: str
    status: str  # DOWNLOADED | EXISTS | DUPLICATE

#create http session so that we can make multiple requests more easily, efficiently
def make_session() -> requests.Session:
    """
    Create and configure a reusable HTTP session.

    A session is more efficient than making completely separate requests
    because it can reuse connections across requests.

    We also set a browser-like User-Agent because some sites behave better
    when requests look like they are coming from a normal browser.
    """
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            )
        }
    )
    return s

#gets html/text content from a provided url in the current session
#HTTP errors
def get_html(session: requests.Session, url: str, timeout: int = 30) -> str:
    """
    Fetch a URL and return its HTML/text content.

    raise_for_status() ensures HTTP errors (404, 500, etc.) become exceptions.
    """
    r = session.get(url, timeout=timeout)
    #CITE: https://www.geeksforgeeks.org/python/response-raise_for_status-python-requests/
    #Checks status code to see if there are issues, raises an error if there is
    r.raise_for_status()
    return r.text

#parses a provided page with specified timeout time using BeautifulSoup
def get_soup(session: requests.Session, url: str, timeout: int = 30) -> BeautifulSoup:
    """
    Fetch a page and parse it with BeautifulSoup using the lxml parser.

    This is the helper used anywhere we need to inspect links or page structure.
    """
    return BeautifulSoup(get_html(session, url, timeout=timeout), "lxml")

#given a school's athletic website and leveraging provided year, creates url to find their schedule url
def schedule_url(base: str) -> str:
    """
    Construct the football schedule URL for a school for the target YEAR.
    """
    return f"{base}/sports/football/schedule/{YEAR}"

#reformats team's name for file creation into token
def tokenize(s: str) -> str:
    """
    Normalize a string into an uppercase alphanumeric token.

    Examples:
    - "Middlebury College" -> "MIDDLEBURYCOLLEGE"
    - "St. John's" -> "STJOHNS"

    Used mainly to make consistent team-name tokens for filenames.
    """
    #removes spaces, other special characters and capitalizes the rest
    #CITE: https://docs.python.org/3/library/re.html#re.sub
    t = re.sub(r"[^A-Za-z0-9]+", "", s).upper()
    #if the regex fails, we just return TEAM
    return t or "TEAM"

#pulls out the last part of a url (slug) containing team info,
#converts into a token using tokenize
def slug_to_team(slug: str) -> str:
    """
    Convert a URL slug into a team token.

    First tries the explicit SLUG_TO_TEAM mapping.
    If the slug is unknown, falls back to generic normalization.
    """
    #remove non letters/numbers with sub
    norm = re.sub(r"[^a-z0-9]+", "", slug.lower())
    return SLUG_TO_TEAM.get(norm, tokenize(slug))

#pulls game_id from a URL to be used in error checking later on (after /boxscore/)
    #urlparse: pulls scheme, netloc, path, params, query, fragment from (url)
    #CITE: https://docs.python.org/3/library/urllib.parse.html#url-parsing
def extract_game_id(url: str) -> Optional[str]:
    """
    Extract the numeric game ID from a boxscore-style URL, if present.

    Example:
    /sports/football/stats/2025/williams/boxscore/6102 -> "6102"
    """
    #extract actual file path
    path = urlparse(url).path
    #check to see if there's boxscore, something after it
    m = re.search(r"/boxscore/(\d+)", path)
    #m.group returns digits if they are found
    return m.group(1) if m else None

#finds month and year on a given page to be used in file naming later on 
#Note: Necessity of function, as well as logic of regex suggested by ChatGPT
def parse_month_year(stats_html: str) -> Optional[Tuple[int, int]]:
    """
    Parse a month/year from a stats page's HTML.

    Supports two common formats:
    1. Numeric dates like 9/21/2025
    2. Text dates like September 21, 2025

    Returns:
        (month, year) if found
        None if no recognized date format is found
    """
    # numeric date format (boundary, 1-2 digits, 1-2 digits, 4 digits, boundary)
    m = re.search(r"\b([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})\b", stats_html)
    # if we find a date in this format, trust it's correct and return it
    if m:
        return int(m.group(1)), int(m.group(3))

    # month-name format (boundary, name, space, 1-2 digits, comma, space, year, boundary)
    m2 = re.search(r"\b([A-Za-z]+)\s+([0-9]{1,2}),\s*([0-9]{4})\b", stats_html)
    # if we find a date in THIS format, trust it's correct and return it
    if m2:
        #put in lowercase for accessing MONTH_MAP (need to change later with csv file)
        month_name = m2.group(1).lower()
        year = int(m2.group(3))
        month = MONTH_MAP.get(month_name)
        if month:
            return month, year

    return None

#gathers boxscore links to be scraped given a session,
def collect_stats_boxscore_links(
    session: requests.Session,
    base: str,
    school: str,
) -> List[StatsBoxscore]:
    """
    Visit a school's football schedule page and collect links to stats/boxscore pages.

    The schedule page contains many links, so we filter for URLs that look like:
    /sports/football/stats/2025/.../boxscore/...

    We then deduplicate them before returning.
    """
    #url with schedule info on it based on athletics website url
    sch = schedule_url(base)

    #exception handling in the case that we can't scrape from the url
    try:
        soup = get_soup(session, sch)
    except Exception as e:
        raise ScheduleFetchError(f"Could not fetch/parse schedule page: {sch}") from e

    #initialize output if we are able to access the page
    out: List[StatsBoxscore] = []

    # Look through every <a> (anchor) tag on the schedule page, where links are stored
    for a in soup.find_all("a"):
        #pull out the actual link (after href if it exists)
        href = a.get("href") or ""
        #if we fail to find a link, next iteration
        if not href:
            continue

        # Convert relative links into absolute URLs (adds https:/, reorganizes)
        # CITE: https://stackoverflow.com/questions/10893374/python-confusions-with-urljoin
        # CITE: https://dojo-yeswehack.com/learn/python-pitfalls/urljoin
        abs_url = urljoin(sch, href)
        #extracts path from the url (stuff after the netloc)
        path = urlparse(abs_url).path.lower()

        # Keep only football stats boxscore links for the target year,
        #which we can check for by examining the contents of path in the 
        #absolute url
        if f"/sports/football/stats/{YEAR}/" in path and "/boxscore/" in path:
            out.append(StatsBoxscore(school=school, url=abs_url))

    #remove duplicates
    uniq: List[StatsBoxscore] = []

    #go through the links we've collected
    for x in out:
        already_seen = False
        #check if this URL already exists in uniq
        for y in uniq:
            if x.url == y.url:
                already_seen = True
                break

        if not already_seen:
            uniq.append(x)

    return uniq

#analyzes stats page using keywords to handle the case where stats/boxscore pages do not link directly to a pdf,
#instead have additional page where pdf is viewable but not the entire page
#Idea/code supplemented by ChatGPT 5.2
def find_pdf_landing_link(session: requests.Session, stats_url: str) -> Optional[str]:
    """
    Given a stats/boxscore page, try to find the intermediate PDF landing page.

    Some sites link directly to a PDF, while others first link to a page ending in /pdf
    that then contains the actual direct PDF file link.

    We gather several candidates, then prefer the most structured '/pdf' landing page.
    """
    #exception handling, make sure we can actually access the file
    try:
        soup = get_soup(session, stats_url)
    except Exception as e:
        raise StatsFetchError(f"Could not fetch/parse stats page: {stats_url}") from e

    #initialize list of candidate links
    candidates: List[str] = []

    #same as previous function, find all links using anchor tags
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if not href:
            continue

        #same as others, create absolute url with imported function
        abs_url = urljoin(stats_url, href)
        #needs to be lowercase to match what is in the dictionary from csv file
        path = urlparse(abs_url).path.lower()

        #get visible text, make it lowercase
        txt = (a.get_text(" ", strip=True) or "").lower()

        # Best case: a landing page whose path literally ends with /pdf
        if path.endswith("/pdf") and "/football/" in path and "/boxscore/" in path:
            candidates.append(abs_url)

        # Sometimes the link text mentions PDF even if the URL structure is less clean
        if "pdf" in txt and "/boxscore/" in path:
            candidates.append(abs_url)

        # Sometimes the stats page links directly to a .pdf file
        if path.endswith(".pdf"):
            candidates.append(abs_url)

    # Prefer the canonical /pdf landing page if present
    for u in candidates:
        if urlparse(u).path.lower().endswith("/pdf"):
            return u

    # Otherwise, just take the first plausible candidate
    return candidates[0] if candidates else None

#Helper supported by ChatGPT to determine home/away team
#Can I do this earlier in the workflow somehow?
def parse_home_away_from_pdf_landing(
    pdf_landing_url: str,
    school_name: str,
) -> Optional[Tuple[str, str]]:
    """
    Infer home and away teams from the PDF landing page URL.

    Expected URL pattern:
    /boxscore/(at|vs)-opponent-slug/.../pdf

    Logic:
    - 'at-opponent' means the current school is the away team
    - 'vs-opponent' means the current school is the home team
    """
    path = urlparse(pdf_landing_url).path.lower()
    #makes sure that our file path is actually from a potential boxscore page
    m = re.search(r"/boxscore/(at|vs)-([^/]+)/", path)
    if not m:
        return None

    #uses re to find section of url that specifies home vs away (based on at, vs)
    at_vs, opp_slug = m.group(1), m.group(2)
    #tokenize name for label
    school = tokenize(school_name)
    #will call tokenize
    opp = slug_to_team(opp_slug)

    #return in correct order (home, away)
    return (opp, school) if at_vs == "at" else (school, opp)

#Helper supported by ChatGPT to make pdf landing page downloadable
def find_fullscreen_pdf(session: requests.Session, pdf_landing_url: str) -> Optional[str]:
    """
    Visit the PDF landing page and try to find the direct downloadable PDF URL.

    First preference:
    - a link whose visible text suggests "full screen" and whose href ends in .pdf

    Fallback:
    - any link ending in .pdf
    """
    #if we can't access the landing url, bail
    try:
        soup = get_soup(session, pdf_landing_url)
    except Exception as e:
        raise StatsFetchError(f"Could not fetch/parse PDF landing page: {pdf_landing_url}") from e

    # First pass: try to find the dedicated "full screen" PDF link

    #go through anchor tags (where urls live)
    for a in soup.find_all("a"):
        #pull out visible text
        txt = (a.get_text(" ", strip=True) or "").lower()
        #links come after href in html, this pulls it out
        href = a.get("href") or ""
        #if we see our beautiful keywords
        if "full" in txt and "screen" in txt and href:
            #imported function to create absolute url
            u = urljoin(pdf_landing_url, href)
            #as long as this full screen thing ends in pdf, we should be good, return
            if u.lower().endswith(".pdf"):
                return u

    # Second pass: accept any direct PDF link
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if href.lower().endswith(".pdf"):
            return urljoin(pdf_landing_url, href)

    return None

#helper to create file names from tokenize for each team
def build_filename(home: str, away: str, month: int, year: int) -> str:
    """
    Build the final PDF filename in the required format:
    HOMETEAM.AWAYTEAM.MONTH.YEAR.pdf
    """
    return f"{tokenize(home)}.{tokenize(away)}.{month}.{year}.pdf"

#ACTUALLY DOWNLOADS THE FILES
#puts the file in the current working directory
def download_file(session: requests.Session, url: str, out_path: Path) -> None:
    """
    Download a file safely to disk.

    Strategy:
    1. Create parent directories if needed
    2. Download into a temporary '.part' file first
    3. Rename the temp file to the final filename only after success

    This prevents partially-downloaded files from masquerading as valid PDFs.
    """
    #fills in missing directories if necessary
    out_path.parent.mkdir(parents=True, exist_ok=True)
    #temporary dummy filename
    tmp = out_path.with_suffix(out_path.suffix + ".part")

    try:
        #start the request
        with session.get(url, stream=True, timeout=(15, 90)) as r:
            #check status using error codes, built in method
            r.raise_for_status()
            #create file object, wb = write binary, which is necessary for pdfs
            #CITE: ChatGPT 5.2 for file reading syntax
            with open(tmp, "wb") as f:
                #download file in chunks (manages memory issues)
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    #as long as we read a chunk, write the binary
                    if chunk:
                        f.write(chunk)

        #revert to original name if we succeed
        tmp.replace(out_path)

    #if we fail, throw an error
    except Exception as e:
        # Clean up partial file if the download failed midstream
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise DownloadFailedError(f"Failed downloading PDF: {url}") from e

def verify_url_reachable(session: requests.Session, url: str) -> None:
    """
    Verify that a URL exists and can be reached before attempting to scrape it.

    Raises:
        ScheduleFetchError if the page does not exist or cannot be reached.
    """
    try:
        # HEAD is faster because it does not download the full page
        r = session.head(url, allow_redirects=True, timeout=(10, 30))

        # Some servers don't support HEAD correctly, fallback to GET
        if r.status_code >= 400:
            r = session.get(url, stream=True, timeout=(10, 30))
            r.raise_for_status()

    except requests.exceptions.HTTPError as e:
        raise ScheduleFetchError(
            f"Schedule page returned HTTP error for {url}: {e}"
        ) from e

    except requests.exceptions.ConnectionError as e:
        raise ScheduleFetchError(
            f"Could not connect to schedule page: {url}"
        ) from e

    except requests.exceptions.Timeout as e:
        raise ScheduleFetchError(
            f"Timeout while connecting to schedule page: {url}"
        ) from e

    except requests.exceptions.RequestException as e:
        raise ScheduleFetchError(
            f"Unexpected network error when accessing {url}: {e}"
        ) from e

#CITE: https://docs.python.org/3/library/urllib.robotparser.html
#constructs object used to check parsing ethics, returns it along with robots url
#CITE: usage of RobotFileParser supported by ChatGPT
def robots_parser_for_base(session: requests.Session, base: str) -> tuple[RobotFileParser, str]:
    """
    Fetch and parse robots.txt for a school's base site.

    Behavior:
    - 4xx robots.txt response -> assume no robots rules exist, allow scraping
    - 5xx robots.txt response -> assume server problem, deny scraping
    - other request/parsing failure -> deny scraping conservatively

    Returns:
        (parsed_robot_file_parser, robots_url)
    """
    #take off backslash, add robots.txt
    robots_url = f"{base.rstrip('/')}/robots.txt"
    #build robot file parser object
    rp = RobotFileParser()
    rp.set_url(robots_url)

    #ready to handle errors
    try:
        #request robots.txt file
        r = session.get(robots_url, timeout=30)

        # If robots.txt does not exist, treat as no restrictions
        if 400 <= r.status_code < 500:
            rp.parse(["User-agent: *", "Disallow:"])
            return rp, robots_url

        # If the server errors when serving robots.txt, fail closed
        if 500 <= r.status_code < 600:
            rp.parse(["User-agent: *", "Disallow: /"])
            return rp, robots_url

        #another error check
        r.raise_for_status()
        #if we get here everything is fine, parse the file with the parser object
        rp.parse(r.text.splitlines())
        return rp, robots_url

    #if we fail, assume that we aren't allowed to scrape for safety
    except Exception:
        # Conservative fallback: block scraping if robots cannot be verified
        rp.parse(["User-agent: *", "Disallow: /"])
        return rp, robots_url

#checks whether robots.txt prohibits scraping using RobotFileParser for our agent
#(required parameter for can_fetch)
def robots_allows(rp: RobotFileParser, user_agent: str, url: str) -> bool:
    """
    Check whether robots.txt allows the given user agent to fetch a URL.
    """
    return rp.can_fetch(user_agent, url)

#scrapes AND downloads pdfs for one school
#LOTS of built-in error handling here, but don't think i need this anymore...
def scrape_school_pdfs(
    session: requests.Session,
    school: str,
    base: str,
    out_dir: Path,
    verbose: bool = False,
    limit_links: int = 0,
) -> List[DownloadResult]:
    """
    Scrape and download PDFs for one school only.

    High-level flow:
    1. Validate the school name
    2. Check robots.txt permissions
    3. Collect all stats/boxscore links from the schedule
    4. For each stats page:
       - fetch stats HTML
       - parse date
       - find PDF landing page
       - infer home/away teams
       - find direct PDF URL
       - deduplicate
       - download if needed
    5. Return structured results for every processed item

    Important:
    This function raises structured exceptions instead of swallowing them.
    That makes it suitable as a core scraper that higher-level code can wrap.
    """
    #if the specified school isn't in our list, raise an error
    if school not in SCHOOLS:
        raise InvalidSchoolError(f"Unknown school: {school}")

    #create link to schedule page from the base athletics website (from csv file)
    sch_url = schedule_url(base)

    verify_url_reachable(session, sch_url)

    # Build and parse robots.txt rules for this site
    rp, _robots_url = robots_parser_for_base(session, base)
    #pull out user's agent from the session
    ua = session.headers.get("User-Agent", "*")

    # Make sure the schedule page itself is allowed
    if not robots_allows(rp, ua, sch_url):
        raise RobotsBlockedError(f"robots.txt disallows schedule page: {sch_url}")

    # Discover all stats boxscore links from the schedule
    links = collect_stats_boxscore_links(session, base, school)

    # Optional limit for testing / debugging
    if limit_links and limit_links > 0:
        links = links[:limit_links]

    # These sets help us avoid duplicate downloads in multiple ways
    seen_pdf_urls: Set[str] = set()                       # same exact PDF URL
    seen_filenames: Set[str] = set()                     # same final filename
    downloaded_game_ids: Set[str] = set()               # same boxscore/game ID
    #IMPORTANT: 2 schools may refer to the same game in reverse order
    downloaded_gamekeys_unordered: Set[Tuple[str, str, int, int]] = set()
    # unordered key = same two teams in same month/year regardless of order

    #initialize output
    results: List[DownloadResult] = []

    # Process each stats/boxscore link (each game) one by one
    for item in links:
        #each item is a StatsBoxscore object so we just pull the url atribute
        stats_url = item.url

        # Respect robots.txt for the stats page too
        if not robots_allows(rp, ua, stats_url):
            raise RobotsBlockedError(f"robots.txt disallows stats page: {stats_url}")

        # Fetch raw HTML so we can parse dates from it
        try:
            stats_html = get_html(session, stats_url)
        except Exception as e:
            raise StatsFetchError(f"Could not fetch stats page: {stats_url}") from e

        # Parse game month/year from the stats page
        month_year = parse_month_year(stats_html)
        if not month_year:
            raise DateParseError(f"Could not parse date from stats page: {stats_url}")

        #store month year pair for file naming
        month, year = month_year

        # Skip anything that does not belong to the target season year
        if year != YEAR:
            continue

        # Find the intermediate PDF landing page (or direct PDF candidate)
        pdf_landing = find_pdf_landing_link(session, stats_url)
        if not pdf_landing:
            raise PdfLandingNotFoundError(f"Could not find PDF landing link: {stats_url}")

        # Respect robots.txt for the PDF landing page
        if not robots_allows(rp, ua, pdf_landing):
            raise RobotsBlockedError(f"robots.txt disallows PDF landing page: {pdf_landing}")

        # Try to get a game ID from the landing page URL first, else the stats URL
        game_id = extract_game_id(pdf_landing) or extract_game_id(stats_url) or ""

        # Infer home/away ordering from the landing URL path
        home_away = parse_home_away_from_pdf_landing(pdf_landing, school)
        if not home_away:
            raise HomeAwayParseError(
                f"Could not parse home/away from landing URL: {pdf_landing}"
            )

        #store home away pair for file naming
        home, away = home_away

        # Find the actual direct PDF file URL
        pdf_url = find_fullscreen_pdf(session, pdf_landing)
        if not pdf_url:
            raise DirectPdfNotFoundError(f"Could not find direct PDF URL: {pdf_landing}")

        # Respect robots.txt for the final PDF file too
        if not robots_allows(rp, ua, pdf_url):
            raise RobotsBlockedError(f"robots.txt disallows direct PDF URL: {pdf_url}")

        # If the PDF URL itself has a better/more explicit game ID, use that
        pdf_game_id = extract_game_id(pdf_url) or ""
        if pdf_game_id:
            game_id = pdf_game_id

        # Build final output filename and path
        filename = build_filename(home, away, month, year)
        out_path = out_dir / filename

        # Create an order-independent game key using alphabetical order:
        # This helps catch the same game even if two schools reference it differently.
        a, b = sorted([home, away])
        unordered_key = (a, b, month, year)

        # If we've already seen this game in some equivalent form, mark duplicate
        if (
            (game_id and game_id in downloaded_game_ids)
            or (unordered_key in downloaded_gamekeys_unordered)
            or (pdf_url in seen_pdf_urls)
            or (filename in seen_filenames)
        ):
            results.append(
                DownloadResult(
                    source_school=tokenize(school),
                    schedule_url=sch_url,
                    stats_url=stats_url,
                    game_id=game_id,
                    pdf_landing_url=pdf_landing,
                    pdf_url=pdf_url,
                    home_team=home,
                    away_team=away,
                    month=month,
                    year=year,
                    filename=filename,
                    file_path=str(out_path),
                    status="DUPLICATE",
                )
            )
            continue

        # If the file already exists on disk and is non-empty, do not redownload it
        # This happens when we create an out_path that we've already seen, causing path to match prev.
        if out_path.exists() and out_path.stat().st_size > 0:
            if game_id:
                downloaded_game_ids.add(game_id)
            downloaded_gamekeys_unordered.add(unordered_key)
            seen_pdf_urls.add(pdf_url)
            seen_filenames.add(filename)

            results.append(
                DownloadResult(
                    source_school=tokenize(school),
                    schedule_url=sch_url,
                    stats_url=stats_url,
                    game_id=game_id,
                    pdf_landing_url=pdf_landing,
                    pdf_url=pdf_url,
                    home_team=home,
                    away_team=away,
                    month=month,
                    year=year,
                    filename=filename,
                    file_path=str(out_path),
                    status="EXISTS",
                )
            )
            continue

        # Optional progress output for CLI/debugging
        if verbose:
            print(f"[{school}] downloading {filename}")

        # Actually download the PDF
        download_file(session, pdf_url, out_path)

        # Record this as now seen/downloaded so future duplicates are skipped
        if game_id:
            downloaded_game_ids.add(game_id)
        downloaded_gamekeys_unordered.add(unordered_key)
        seen_pdf_urls.add(pdf_url)
        seen_filenames.add(filename)

        # Save a structured success result
        results.append(
            DownloadResult(
                source_school=tokenize(school),
                schedule_url=sch_url,
                stats_url=stats_url,
                game_id=game_id,
                pdf_landing_url=pdf_landing,
                pdf_url=pdf_url,
                home_team=home,
                away_team=away,
                month=month,
                year=year,
                filename=filename,
                file_path=str(out_path),
                status="DOWNLOADED",
            )
        )

    return results

#this will be used as an intermediate step when we are looking to display
#discovered pdfs on the UI/integrate the parser after the scraper
#LOT of error handling here as well
def discover_pdf_records(
    year: int,
    schools: List[str],
    limit_links: int = 0,
) -> List[dict]:
    """
    Discover UI-ready PDF records without downloading files.

    Returns records shaped like:
    [
        {
            "school": "Hamilton",
            "opponent": "Williams",
            "date": "2025-09-01",
            "pdf_url": "https://...",
            "boxscore_url": "https://...",
            "pdf_landing_url": "https://...",
            "home_team": "HAMILTON",
            "away_team": "WILLIAMS",
            "filename": "HAMILTON.WILLIAMS.9.2025.pdf",
            "game_id": "6102"
        }
    ]
    """
    #check current year
    if year != YEAR:
        raise ValueError(f"This scraper is configured for YEAR={YEAR}, not {year}")

    #start session
    session = make_session()

    #check if we're searching for schools that we don't have sites for
    allow = set(schools)
    unknown = sorted(list(allow - set(SCHOOLS.keys())))
    if unknown:
        raise InvalidSchoolError(f"Unknown school(s): {', '.join(unknown)}")

    #initialize outputs

    #final
    results: List[dict] = []
    #teams, date (normalized)
    seen_keys: Set[Tuple[str, str, int, int]] = set()
    #urls
    seen_pdf_urls: Set[str] = set()

    #iterate through schools
    for school in schools:
        #get base url
        base = SCHOOLS[school]
        #get scheduling page url
        sch_url = schedule_url(base)

        #create parser object
        rp, _robots_url = robots_parser_for_base(session, base)
        #extract agent that will be crawling in our session
        ua = session.headers.get("User-Agent", "*")

        #make sure we're above board
        if not robots_allows(rp, ua, sch_url):
            raise RobotsBlockedError(f"robots.txt disallows schedule page: {sch_url}")

        #get links
        links = collect_stats_boxscore_links(session, base, school)

        #apply limit if necessary
        if limit_links and limit_links > 0:
            links = links[:limit_links]

        #go through links/games
        for item in links:
            #each link is a StatsBoxscore object, so we can pull url atribute
            stats_url = item.url
            
            #check permissions
            if not robots_allows(rp, ua, stats_url):
                raise RobotsBlockedError(f"robots.txt disallows stats page: {stats_url}")

            #get raw content
            try:
                stats_html = get_html(session, stats_url)
            except Exception as e:
                raise StatsFetchError(f"Could not fetch stats page: {stats_url}") from e

            #pull month and year from html data
            month_year = parse_month_year(stats_html)
            if not month_year:
                raise DateParseError(f"Could not parse date from stats page: {stats_url}")

            #store
            month, parsed_year = month_year

            #verify we're in the right year
            if parsed_year != YEAR:
                continue

            #find landing page
            pdf_landing = find_pdf_landing_link(session, stats_url)
            if not pdf_landing:
                raise PdfLandingNotFoundError(f"Could not find PDF landing link: {stats_url}")

            #check permissions
            if not robots_allows(rp, ua, pdf_landing):
                raise RobotsBlockedError(f"robots.txt disallows PDF landing page: {pdf_landing}")

            #find home and away for file naming
            home_away = parse_home_away_from_pdf_landing(pdf_landing, school)
            if not home_away:
                raise HomeAwayParseError(
                    f"Could not parse home/away from landing URL: {pdf_landing}"
                )

            #store
            home, away = home_away

            #find direct pdf url (for downloading)
            pdf_url = find_fullscreen_pdf(session, pdf_landing)
            if not pdf_url:
                raise DirectPdfNotFoundError(f"Could not find direct PDF URL: {pdf_landing}")

            #check permissions
            if not robots_allows(rp, ua, pdf_url):
                raise RobotsBlockedError(f"robots.txt disallows direct PDF URL: {pdf_url}")

            #create alphabetical ordering of teams
            a, b = sorted([home, away])
            #alphabetized key for duplicate checking
            dedupe_key = (a, b, month, parsed_year)

            #if we've already seen this game from the other team's site, go to next game
            if dedupe_key in seen_keys or pdf_url in seen_pdf_urls:
                continue
            
            #mark that we've seen
            seen_keys.add(dedupe_key)
            seen_pdf_urls.add(pdf_url)

            #create token for file name
            school_token = tokenize(school)
            #determines who the opponent is
            opponent = away if home == school_token else home

            results.append(
                {
                    "school": school,
                    "opponent": opponent,
                    #reformat date
                    "date": f"{parsed_year:04d}-{month:02d}-01",
                    "pdf_url": pdf_url,
                    "boxscore_url": stats_url,
                    "pdf_landing_url": pdf_landing,
                    "home_team": home,
                    "away_team": away,
                    "filename": build_filename(home, away, month, parsed_year),
                    "game_id": extract_game_id(pdf_landing) or extract_game_id(stats_url) or "",
                }
            )

    return results

#bulk download used via the command line for vscode debugging
def run_download(
    out_dir: str,
    verbose: bool = False,
    limit_schools: int = 0,
    limit_links: int = 0,
    schools_allowlist: Optional[List[str]] = None,
) -> List[DownloadResult]:
    """
    Synchronous top-level wrapper for scraping/downloading PDFs.

    What this does:
    - create one shared HTTP session
    - ensure the output directory exists
    - optionally filter schools to an allowlist
    - optionally limit number of schools
    - call scrape_school_pdfs() once per school
    - combine all results into one list

    Important note:
    This function stops on the first school-level failure.
    If you want more fault tolerance for the UI, that should be handled
    in a higher-level wrapper or API layer.
    """
    # Build one shared session so all school requests reuse the same
    # headers / TCP connections
    session = make_session()

    # Normalize output path and ensure it exists
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # By default, include all schools from the configured school map
    schools_map = SCHOOLS

    # If an allowlist is supplied, validate it and then filter down
    # to only the requested schools
    if schools_allowlist:
        allow = set(schools_allowlist)
        unknown = sorted(list(allow - set(SCHOOLS.keys())))
        if unknown:
            raise InvalidSchoolError(f"Unknown school(s): {', '.join(unknown)}")

        schools_map = {k: v for k, v in SCHOOLS.items() if k in allow}

    # Turn the school mapping into an ordered list of (school, base_url) pairs
    items = list(schools_map.items())

    # Optional limit for testing/debugging so we only scrape the first N schools
    if limit_schools and limit_schools > 0:
        items = items[:limit_schools]

    #initialize output
    all_results: List[DownloadResult] = []

    # Run the scraper school-by-school
    for school, base in items:
        school_results = scrape_school_pdfs(
            session=session,
            school=school,
            base=base,
            out_dir=out_path,
            verbose=verbose,
            limit_links=limit_links,
        )
        all_results.extend(school_results)

    return all_results