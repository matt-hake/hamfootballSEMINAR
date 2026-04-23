#!/usr/bin/env python3
"""
Core PDF scraper for NESCAC football boxscore PDFs.

CITE: CHATGPT 5.2 was used to help structure and implement several functions in this file,
and for reformatting/commenting once functionality was complete.

Responsibilities:
- Discover stats/boxscore links from schedule pages
- Find PDF landing pages
- Find direct PDF URLs
- Download PDFs
- Return structured results

Non-responsibilities:
- UI job orchestration
- Runtime validation summary
- Pytest unit testing
- API response formatting
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

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

# Default year used only when the caller does not supply one explicitly.
DEFAULT_YEAR = 2025

# Path to the client-editable CSV config file.
BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_CSV = BASE_DIR / "config" / "schools.csv"

# Map month names/abbreviations to month numbers so we can parse both
# "September 14, 2025" and similar textual date formats.
MONTH_MAP = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _load_school_config(csv_path: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Load school configuration from a CSV file.

    Returns two mappings:
    1. school name -> base URL
    2. slug -> team token

    Expected CSV columns:
    - school
    - base_url
    - slug
    - team_token

    This function is intentionally strict. Since the scraper depends on these
    mappings for URL construction and filename generation, malformed config
    should fail early and clearly.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"School config CSV not found: {csv_path}")

    schools: Dict[str, str] = {}
    slug_to_team: Dict[str, str] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)

        required = {"school", "base_url", "slug", "team_token"}

        if not reader.fieldnames:
            raise ValueError(f"School config CSV has no header row: {csv_path}")

        missing = required - set(reader.fieldnames)
        if missing:
            missing_str = ", ".join(sorted(missing))
            raise ValueError(
                f"School config CSV is missing required columns: {missing_str}"
            )

        for row_num, row in enumerate(reader, start=2):
            school = (row.get("school") or "").strip()
            base_url = (row.get("base_url") or "").strip().rstrip("/")
            slug = (row.get("slug") or "").strip().lower()
            team_token = (row.get("team_token") or "").strip().upper()

            if not school:
                raise ValueError(f"Missing school value in row {row_num} of {csv_path}")

            if not base_url:
                raise ValueError(
                    f"Missing base_url for school '{school}' in row {row_num} "
                    f"of {csv_path}"
                )

            if not slug:
                raise ValueError(
                    f"Missing slug for school '{school}' in row {row_num} "
                    f"of {csv_path}"
                )

            if not team_token:
                raise ValueError(
                    f"Missing team_token for school '{school}' in row {row_num} "
                    f"of {csv_path}"
                )

            schools[school] = base_url
            slug_to_team[slug] = team_token

    return schools, slug_to_team


# Load the school configuration once at import time so the rest of the module
# can treat these mappings as authoritative lookup tables.
SCHOOLS, SLUG_TO_TEAM = _load_school_config(CONFIG_CSV)


@dataclass(frozen=True)
class StatsBoxscore:
    """Represents one stats/boxscore page discovered from a schedule page."""

    school: str
    year: int
    url: str


@dataclass(frozen=True)
class DownloadResult:
    """
    Represents the result of processing one PDF candidate.

    status is one of:
    - DOWNLOADED
    - EXISTS
    - DUPLICATE
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
    filename: str
    file_path: str
    status: str


def make_session() -> requests.Session:
    """
    Create a requests session with a desktop browser User-Agent.

    Some athletics sites behave differently depending on the client, so using
    a realistic User-Agent improves compatibility.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            )
        }
    )
    return session


def get_html(session: requests.Session, url: str, timeout: int = 30) -> str:
    """
    Fetch raw HTML from a URL and raise if the request fails.
    """
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def get_soup(
    session: requests.Session,
    url: str,
    timeout: int = 30,
) -> BeautifulSoup:
    """
    Fetch and parse HTML into a BeautifulSoup object.
    """
    return BeautifulSoup(get_html(session, url, timeout=timeout), "lxml")


def schedule_url(base: str, year: int) -> str:
    """
    Construct the football schedule URL for a specific year.
    """
    return f"{base}/sports/football/schedule/{year}"


def normalize_years(
    years: Optional[Iterable[int]] = None,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
) -> List[int]:
    """
    Normalize caller input into a sorted list of distinct years.

    Supported usage:
    - normalize_years(years=[2025, 2024, 2023])
    - normalize_years(start_year=2016, end_year=2025)

    If neither form is provided, the default year is used.
    """
    if years is not None:
        normalized = sorted({int(year) for year in years})
        if not normalized:
            raise ValueError("At least one year must be provided.")
        return normalized

    if start_year is not None and end_year is not None:
        start = int(start_year)
        end = int(end_year)

        if start > end:
            raise ValueError("start_year cannot be greater than end_year.")

        return list(range(start, end + 1))

    return [DEFAULT_YEAR]


def tokenize(value: str) -> str:
    """
    Convert a team/school string into a filesystem-friendly uppercase token.

    Example:
    "Hamilton College" -> "HAMILTONCOLLEGE"
    """
    token = re.sub(r"[^A-Za-z0-9]+", "", value).upper()
    return token or "TEAM"


def slug_to_team(slug: str) -> str:
    """
    Convert a URL slug into a team token.

    We first normalize the slug and check the configured slug mapping. If the
    slug is unknown, we fall back to generic tokenization so the scraper can
    still proceed in many cases.
    """
    normalized_slug = re.sub(r"[^a-z0-9]+", "", slug.lower())
    return SLUG_TO_TEAM.get(normalized_slug, tokenize(slug))


def extract_game_id(url: str) -> Optional[str]:
    """
    Extract a numeric game ID from a URL path when present.

    Expected pattern includes /boxscore/<digits>.
    """
    path = urlparse(url).path
    match = re.search(r"/boxscore/(\d+)", path)
    return match.group(1) if match else None


def parse_month_year(stats_html: str) -> Optional[Tuple[int, int]]:
    """
    Extract (month, year) from a stats page's HTML.

    Supported formats include:
    - 9/14/2025
    - September 14, 2025

    We return only month and year because the current downstream filename and
    dedupe logic depend on that granularity rather than the exact day.
    """
    numeric_match = re.search(r"\b([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})\b", stats_html)
    if numeric_match:
        return int(numeric_match.group(1)), int(numeric_match.group(3))

    text_match = re.search(
        r"\b([A-Za-z]+)\s+([0-9]{1,2}),\s*([0-9]{4})\b",
        stats_html,
    )
    if text_match:
        month_name = text_match.group(1).lower()
        year = int(text_match.group(3))
        month = MONTH_MAP.get(month_name)

        if month:
            return month, year

    return None


def collect_stats_boxscore_links(
    session: requests.Session,
    base: str,
    school: str,
    year: int,
) -> List[StatsBoxscore]:
    """
    Visit a school's football schedule page for one year and collect unique
    stats/boxscore links.

    We identify candidate links by looking for football stats URLs that include
    both the target year and a /boxscore/ path segment.
    """
    schedule_page_url = schedule_url(base, year)

    try:
        soup = get_soup(session, schedule_page_url)
    except Exception as exc:
        raise ScheduleFetchError(
            f"Could not fetch/parse schedule page: {schedule_page_url}"
        ) from exc

    discovered: List[StatsBoxscore] = []

    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        if not href:
            continue

        absolute_url = urljoin(schedule_page_url, href)
        path = urlparse(absolute_url).path.lower()

        if f"/sports/football/stats/{year}/" in path and "/boxscore/" in path:
            discovered.append(
                StatsBoxscore(
                    school=school,
                    year=year,
                    url=absolute_url,
                )
            )

    # Deduplicate while preserving discovery order.
    unique_links: List[StatsBoxscore] = []
    seen_urls: Set[str] = set()

    for item in discovered:
        if item.url not in seen_urls:
            unique_links.append(item)
            seen_urls.add(item.url)

    return unique_links


def find_pdf_landing_link(
    session: requests.Session,
    stats_url: str,
) -> Optional[str]:
    """
    Find the PDF landing page (or direct PDF link) from a stats page.

    We gather multiple candidates because site markup can vary:
    - explicit /pdf landing pages
    - links whose text mentions PDF
    - direct .pdf URLs

    If a true /pdf landing page exists, we prefer it because later parsing
    often relies on that URL structure.
    """
    try:
        soup = get_soup(session, stats_url)
    except Exception as exc:
        raise StatsFetchError(f"Could not fetch/parse stats page: {stats_url}") from exc

    candidates: List[str] = []

    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        if not href:
            continue

        #standardize link
        #CITE: https://docs.python.org/3/library/urllib.parse.html
        absolute_url = urljoin(stats_url, href)
        path = urlparse(absolute_url).path.lower()
        text = (anchor.get_text(" ", strip=True) or "").lower()

        if path.endswith("/pdf") and "/football/" in path and "/boxscore/" in path:
            candidates.append(absolute_url)

        if "pdf" in text and "/boxscore/" in path:
            candidates.append(absolute_url)

        if path.endswith(".pdf"):
            candidates.append(absolute_url)

    for candidate in candidates:
        if urlparse(candidate).path.lower().endswith("/pdf"):
            return candidate

    return candidates[0] if candidates else None


def parse_home_away_from_pdf_landing(
    pdf_landing_url: str,
    school_name: str,
) -> Optional[Tuple[str, str]]:
    """
    Infer home and away teams from a PDF landing URL.

    Expected URL patterns include:
    - /boxscore/at-opponent/
    - /boxscore/vs-opponent/

    Interpretation:
    - "at" means the source school is the away team
    - "vs" means the source school is the home team
    """
    path = urlparse(pdf_landing_url).path.lower()
    match = re.search(r"/boxscore/(at|vs)-([^/]+)/", path)
    if not match:
        return None

    at_vs, opponent_slug = match.group(1), match.group(2)
    school_token = tokenize(school_name)
    opponent_token = slug_to_team(opponent_slug)

    return (
        (opponent_token, school_token)
        if at_vs == "at"
        else (school_token, opponent_token)
    )


def find_fullscreen_pdf(
    session: requests.Session,
    pdf_landing_url: str,
) -> Optional[str]:
    """
    Find the direct PDF URL from a PDF landing page.

    We first prefer links labeled something like "full screen", since these
    often point to the raw PDF viewer resource. If that fails, we fall back to
    any anchor ending in .pdf.
    """
    try:
        soup = get_soup(session, pdf_landing_url)
    except Exception as exc:
        raise StatsFetchError(
            f"Could not fetch/parse PDF landing page: {pdf_landing_url}"
        ) from exc

    for anchor in soup.find_all("a"):
        text = (anchor.get_text(" ", strip=True) or "").lower()
        href = anchor.get("href") or ""

        if "full" in text and "screen" in text and href:
            candidate = urljoin(pdf_landing_url, href)
            if candidate.lower().endswith(".pdf"):
                return candidate

    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        if href.lower().endswith(".pdf"):
            return urljoin(pdf_landing_url, href)

    return None


def build_filename(home: str, away: str, month: int, year: int) -> str:
    """
    Build the standardized local filename for a PDF.
    """
    return f"{tokenize(home)}.{tokenize(away)}.{month}.{year}.pdf"


def download_file(session: requests.Session, url: str, out_path: Path) -> None:
    """
    Download a file to disk safely using a temporary .part file.

    Writing to a temp file first avoids leaving behind a misleading partial
    PDF if the download is interrupted or fails midway.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = out_path.with_suffix(out_path.suffix + ".part")

    try:
        with session.get(url, stream=True, timeout=(15, 90)) as response:
            response.raise_for_status()

            with open(temp_path, "wb") as file_obj:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        file_obj.write(chunk)

        temp_path.replace(out_path)

    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

        raise DownloadFailedError(f"Failed downloading PDF: {url}") from exc


def verify_url_reachable(session: requests.Session, url: str) -> None:
    """
    Verify that a URL is reachable before deeper scraping begins.

    We try HEAD first because it is lightweight, then fall back to GET if the
    server rejects or mishandles HEAD requests.
    """
    try:
        response = session.head(url, allow_redirects=True, timeout=(10, 30))

        if response.status_code >= 400:
            response = session.get(url, stream=True, timeout=(10, 30))
            response.raise_for_status()

    except requests.exceptions.HTTPError as exc:
        raise ScheduleFetchError(
            f"Schedule page returned HTTP error for {url}: {exc}"
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise ScheduleFetchError(
            f"Could not connect to schedule page: {url}"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise ScheduleFetchError(
            f"Timeout while connecting to schedule page: {url}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise ScheduleFetchError(
            f"Unexpected network error when accessing {url}: {exc}"
        ) from exc


def robots_parser_for_base(
    session: requests.Session,
    base: str,
) -> Tuple[RobotFileParser, str]:
    """
    Fetch and build a robots.txt parser for a school's site.

    Policy used here:
    - 4xx on robots.txt => treat as allow-all
    - 5xx on robots.txt => treat as disallow-all
    - request/parsing failure => conservatively disallow-all

    This is intentionally cautious when the server looks unhealthy.
    """
    robots_url = f"{base.rstrip('/')}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)

    try:
        response = session.get(robots_url, timeout=30)

        if 400 <= response.status_code < 500:
            parser.parse(["User-agent: *", "Disallow:"])
            return parser, robots_url

        if 500 <= response.status_code < 600:
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser, robots_url

        response.raise_for_status()
        parser.parse(response.text.splitlines())
        return parser, robots_url

    except Exception:
        parser.parse(["User-agent: *", "Disallow: /"])
        return parser, robots_url


def robots_allows(rp: RobotFileParser, user_agent: str, url: str) -> bool:
    """
    Check whether robots.txt allows the given user agent to fetch a URL.
    """
    return rp.can_fetch(user_agent, url)


def scrape_school_pdfs(
    session: requests.Session,
    school: str,
    base: str,
    out_dir: Path,
    years: Optional[Iterable[int]] = None,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    verbose: bool = False,
    limit_links: int = 0,
) -> List[DownloadResult]:
    """
    Scrape and download PDFs for one school across one or many years.

    Supported examples:
    - scrape_school_pdfs(..., years=[2025, 2024, 2023])
    - scrape_school_pdfs(..., start_year=2016, end_year=2025)

    Deduplication is intentionally layered:
    - game_id when present
    - unordered (team_a, team_b, month, year) key
    - raw PDF URL
    - output filename

    This protects against duplicate discovery across multiple site paths,
    multiple years, or mirrored links.
    """
    if school not in SCHOOLS:
        raise InvalidSchoolError(f"Unknown school: {school}")

    years_to_scrape = normalize_years(
        years=years,
        start_year=start_year,
        end_year=end_year,
    )

    rp, _robots_url = robots_parser_for_base(session, base)
    user_agent = session.headers.get("User-Agent", "*")

    seen_pdf_urls: Set[str] = set()
    seen_filenames: Set[str] = set()
    downloaded_game_ids: Set[str] = set()
    downloaded_gamekeys_unordered: Set[Tuple[str, str, int, int]] = set()

    results: List[DownloadResult] = []

    for target_year in years_to_scrape:
        schedule_page_url = schedule_url(base, target_year)

        verify_url_reachable(session, schedule_page_url)

        if not robots_allows(rp, user_agent, schedule_page_url):
            raise RobotsBlockedError(
                f"robots.txt disallows schedule page: {schedule_page_url}"
            )

        links = collect_stats_boxscore_links(session, base, school, target_year)

        if limit_links and limit_links > 0:
            links = links[:limit_links]

        for item in links:
            stats_url = item.url

            if not robots_allows(rp, user_agent, stats_url):
                raise RobotsBlockedError(
                    f"robots.txt disallows stats page: {stats_url}"
                )

            try:
                stats_html = get_html(session, stats_url)
            except Exception as exc:
                raise StatsFetchError(
                    f"Could not fetch stats page: {stats_url}"
                ) from exc

            month_year = parse_month_year(stats_html)
            if not month_year:
                raise DateParseError(
                    f"Could not parse date from stats page: {stats_url}"
                )

            month, parsed_year = month_year

            # Some pages can be linked from a given season page even when the
            # actual stats page belongs to a different year. Filter those out.
            if parsed_year != target_year:
                continue

            pdf_landing_url = find_pdf_landing_link(session, stats_url)
            if not pdf_landing_url:
                raise PdfLandingNotFoundError(
                    f"Could not find PDF landing link: {stats_url}"
                )

            if not robots_allows(rp, user_agent, pdf_landing_url):
                raise RobotsBlockedError(
                    f"robots.txt disallows PDF landing page: {pdf_landing_url}"
                )

            game_id = extract_game_id(pdf_landing_url) or extract_game_id(stats_url) or ""

            home_away = parse_home_away_from_pdf_landing(pdf_landing_url, school)
            if not home_away:
                raise HomeAwayParseError(
                    f"Could not parse home/away from landing URL: {pdf_landing_url}"
                )

            home, away = home_away

            pdf_url = find_fullscreen_pdf(session, pdf_landing_url)
            if not pdf_url:
                raise DirectPdfNotFoundError(
                    f"Could not find direct PDF URL: {pdf_landing_url}"
                )

            if not robots_allows(rp, user_agent, pdf_url):
                raise RobotsBlockedError(
                    f"robots.txt disallows direct PDF URL: {pdf_url}"
                )

            # Prefer the game ID from the direct PDF URL if present, since it
            # may be more specific/reliable than the intermediate page.
            pdf_game_id = extract_game_id(pdf_url) or ""
            if pdf_game_id:
                game_id = pdf_game_id

            filename = build_filename(home, away, month, parsed_year)
            out_path = out_dir / filename

            # Unordered key prevents counting the same matchup twice when
            # discovered from multiple perspectives/paths.
            team_a, team_b = sorted([home, away])
            unordered_key = (team_a, team_b, month, parsed_year)

            if (
                (game_id and game_id in downloaded_game_ids)
                or (unordered_key in downloaded_gamekeys_unordered)
                or (pdf_url in seen_pdf_urls)
                or (filename in seen_filenames)
            ):
                results.append(
                    DownloadResult(
                        source_school=tokenize(school),
                        schedule_url=schedule_page_url,
                        stats_url=stats_url,
                        game_id=game_id,
                        pdf_landing_url=pdf_landing_url,
                        pdf_url=pdf_url,
                        home_team=home,
                        away_team=away,
                        month=month,
                        year=parsed_year,
                        filename=filename,
                        file_path=str(out_path),
                        status="DUPLICATE",
                    )
                )
                continue

            # If the file is already on disk and non-empty, we treat it as a
            # successful prior download and add its identity to the seen sets.
            if out_path.exists() and out_path.stat().st_size > 0:
                if game_id:
                    downloaded_game_ids.add(game_id)

                downloaded_gamekeys_unordered.add(unordered_key)
                seen_pdf_urls.add(pdf_url)
                seen_filenames.add(filename)

                results.append(
                    DownloadResult(
                        source_school=tokenize(school),
                        schedule_url=schedule_page_url,
                        stats_url=stats_url,
                        game_id=game_id,
                        pdf_landing_url=pdf_landing_url,
                        pdf_url=pdf_url,
                        home_team=home,
                        away_team=away,
                        month=month,
                        year=parsed_year,
                        filename=filename,
                        file_path=str(out_path),
                        status="EXISTS",
                    )
                )
                continue

            if verbose:
                print(f"[{school}][{parsed_year}] downloading {filename}")

            download_file(session, pdf_url, out_path)

            if game_id:
                downloaded_game_ids.add(game_id)

            downloaded_gamekeys_unordered.add(unordered_key)
            seen_pdf_urls.add(pdf_url)
            seen_filenames.add(filename)

            results.append(
                DownloadResult(
                    source_school=tokenize(school),
                    schedule_url=schedule_page_url,
                    stats_url=stats_url,
                    game_id=game_id,
                    pdf_landing_url=pdf_landing_url,
                    pdf_url=pdf_url,
                    home_team=home,
                    away_team=away,
                    month=month,
                    year=parsed_year,
                    filename=filename,
                    file_path=str(out_path),
                    status="DOWNLOADED",
                )
            )

    return results


def discover_pdf_records(
    schools: List[str],
    years: Optional[Iterable[int]] = None,
    start_year: Optional[int] = None,
    end_year: Optional[int] = None,
    limit_links: int = 0,
) -> List[dict]:
    """
    Discover UI-ready PDF records without downloading files.

    Supported examples:
    - discover_pdf_records(["Hamilton"], years=[2025, 2024, 2023])
    - discover_pdf_records(["Hamilton"], start_year=2016, end_year=2025)

    This function shares much of the scraper logic, but stops before download
    and returns plain dictionaries that are easy for the frontend to consume.
    """
    session = make_session()
    years_to_scrape = normalize_years(
        years=years,
        start_year=start_year,
        end_year=end_year,
    )

    allowed_schools = set(schools)
    unknown = sorted(list(allowed_schools - set(SCHOOLS.keys())))
    if unknown:
        raise InvalidSchoolError(f"Unknown school(s): {', '.join(unknown)}")

    results: List[dict] = []
    seen_keys: Set[Tuple[str, str, int, int]] = set()
    seen_pdf_urls: Set[str] = set()

    for school in schools:
        base = SCHOOLS[school]
        rp, _robots_url = robots_parser_for_base(session, base)
        user_agent = session.headers.get("User-Agent", "*")

        for target_year in years_to_scrape:
            schedule_page_url = schedule_url(base, target_year)

            verify_url_reachable(session, schedule_page_url)

            if not robots_allows(rp, user_agent, schedule_page_url):
                raise RobotsBlockedError(
                    f"robots.txt disallows schedule page: {schedule_page_url}"
                )

            links = collect_stats_boxscore_links(session, base, school, target_year)

            if limit_links and limit_links > 0:
                links = links[:limit_links]

            for item in links:
                stats_url = item.url

                if not robots_allows(rp, user_agent, stats_url):
                    raise RobotsBlockedError(
                        f"robots.txt disallows stats page: {stats_url}"
                    )

                try:
                    stats_html = get_html(session, stats_url)
                except Exception as exc:
                    raise StatsFetchError(
                        f"Could not fetch stats page: {stats_url}"
                    ) from exc

                month_year = parse_month_year(stats_html)
                if not month_year:
                    raise DateParseError(
                        f"Could not parse date from stats page: {stats_url}"
                    )

                month, parsed_year = month_year

                if parsed_year != target_year:
                    continue

                pdf_landing_url = find_pdf_landing_link(session, stats_url)
                if not pdf_landing_url:
                    raise PdfLandingNotFoundError(
                        f"Could not find PDF landing link: {stats_url}"
                    )

                if not robots_allows(rp, user_agent, pdf_landing_url):
                    raise RobotsBlockedError(
                        f"robots.txt disallows PDF landing page: {pdf_landing_url}"
                    )

                home_away = parse_home_away_from_pdf_landing(pdf_landing_url, school)
                if not home_away:
                    raise HomeAwayParseError(
                        f"Could not parse home/away from landing URL: {pdf_landing_url}"
                    )

                home, away = home_away

                pdf_url = find_fullscreen_pdf(session, pdf_landing_url)
                if not pdf_url:
                    raise DirectPdfNotFoundError(
                        f"Could not find direct PDF URL: {pdf_landing_url}"
                    )

                if not robots_allows(rp, user_agent, pdf_url):
                    raise RobotsBlockedError(
                        f"robots.txt disallows direct PDF URL: {pdf_url}"
                    )

                game_id = (
                    extract_game_id(pdf_url)
                    or extract_game_id(pdf_landing_url)
                    or extract_game_id(stats_url)
                    or ""
                )

                # Frontend discovery also deduplicates across repeated links.
                team_a, team_b = sorted([home, away])
                dedupe_key = (team_a, team_b, month, parsed_year)

                if dedupe_key in seen_keys or pdf_url in seen_pdf_urls:
                    continue

                seen_keys.add(dedupe_key)
                seen_pdf_urls.add(pdf_url)

                school_token = tokenize(school)
                opponent = away if home == school_token else home

                results.append(
                    {
                        "school": school,
                        "opponent": opponent,
                        "date": f"{parsed_year:04d}-{month:02d}-01",
                        "year": parsed_year,
                        "month": month,
                        "pdf_url": pdf_url,
                        "boxscore_url": stats_url,
                        "pdf_landing_url": pdf_landing_url,
                        "home_team": home,
                        "away_team": away,
                        "filename": build_filename(home, away, month, parsed_year),
                        "game_id": game_id,
                        "schedule_url": schedule_page_url,
                    }
                )

    results.sort(
        key=lambda record: (
            record["year"],
            record["month"],
            record["school"],
            record["filename"],
        )
    )
    return results