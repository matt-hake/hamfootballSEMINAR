#!/usr/bin/env python3
"""
Core PDF scraper for NESCAC football boxscore PDFs.

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
"""

from __future__ import annotations

import re
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Iterable
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

# path to client-editable csv config file
BASE_DIR = Path(__file__).resolve().parents[1]
CONFIG_CSV = BASE_DIR / "config" / "schools.csv"

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


def _load_school_config(csv_path: Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Load school/base-url and slug/team-token mappings from a CSV file.
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"School config CSV not found: {csv_path}")

    schools: Dict[str, str] = {}
    slug_to_team: Dict[str, str] = {}

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        required = {"school", "base_url", "slug", "team_token"}
        if not reader.fieldnames:
            raise ValueError(f"School config CSV has no header row: {csv_path}")

        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(
                f"School config CSV is missing required columns: {', '.join(sorted(missing))}"
            )

        for i, row in enumerate(reader, start=2):
            school = (row.get("school") or "").strip()
            base_url = (row.get("base_url") or "").strip().rstrip("/")
            slug = (row.get("slug") or "").strip().lower()
            team_token = (row.get("team_token") or "").strip().upper()

            if not school:
                raise ValueError(f"Missing school value in row {i} of {csv_path}")
            if not base_url:
                raise ValueError(f"Missing base_url for school '{school}' in row {i} of {csv_path}")
            if not slug:
                raise ValueError(f"Missing slug for school '{school}' in row {i} of {csv_path}")
            if not team_token:
                raise ValueError(f"Missing team_token for school '{school}' in row {i} of {csv_path}")

            schools[school] = base_url
            slug_to_team[slug] = team_token

    return schools, slug_to_team


SCHOOLS, SLUG_TO_TEAM = _load_school_config(CONFIG_CSV)


@dataclass(frozen=True)
class StatsBoxscore:
    school: str
    year: int
    url: str


@dataclass(frozen=True)
class DownloadResult:
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
    status: str  # DOWNLOADED | EXISTS | DUPLICATE


def make_session() -> requests.Session:
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


def get_html(session: requests.Session, url: str, timeout: int = 30) -> str:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def get_soup(session: requests.Session, url: str, timeout: int = 30) -> BeautifulSoup:
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
    """
    if years is not None:
        out = sorted({int(y) for y in years})
        if not out:
            raise ValueError("At least one year must be provided.")
        return out

    if start_year is not None and end_year is not None:
        s = int(start_year)
        e = int(end_year)
        if s > e:
            raise ValueError("start_year cannot be greater than end_year.")
        return list(range(s, e + 1))

    return [DEFAULT_YEAR]


def tokenize(s: str) -> str:
    t = re.sub(r"[^A-Za-z0-9]+", "", s).upper()
    return t or "TEAM"


def slug_to_team(slug: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", "", slug.lower())
    return SLUG_TO_TEAM.get(norm, tokenize(slug))


def extract_game_id(url: str) -> Optional[str]:
    path = urlparse(url).path
    m = re.search(r"/boxscore/(\d+)", path)
    return m.group(1) if m else None


def parse_month_year(stats_html: str) -> Optional[Tuple[int, int]]:
    m = re.search(r"\b([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})\b", stats_html)
    if m:
        return int(m.group(1)), int(m.group(3))

    m2 = re.search(r"\b([A-Za-z]+)\s+([0-9]{1,2}),\s*([0-9]{4})\b", stats_html)
    if m2:
        month_name = m2.group(1).lower()
        year = int(m2.group(3))
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
    Visit a school's football schedule page for ONE year and collect stats/boxscore links.
    """
    sch = schedule_url(base, year)

    try:
        soup = get_soup(session, sch)
    except Exception as e:
        raise ScheduleFetchError(f"Could not fetch/parse schedule page: {sch}") from e

    out: List[StatsBoxscore] = []

    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if not href:
            continue

        abs_url = urljoin(sch, href)
        path = urlparse(abs_url).path.lower()

        if f"/sports/football/stats/{year}/" in path and "/boxscore/" in path:
            out.append(StatsBoxscore(school=school, year=year, url=abs_url))

    uniq: List[StatsBoxscore] = []
    seen_urls: Set[str] = set()

    for item in out:
        if item.url not in seen_urls:
            uniq.append(item)
            seen_urls.add(item.url)

    return uniq


def find_pdf_landing_link(session: requests.Session, stats_url: str) -> Optional[str]:
    try:
        soup = get_soup(session, stats_url)
    except Exception as e:
        raise StatsFetchError(f"Could not fetch/parse stats page: {stats_url}") from e

    candidates: List[str] = []

    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if not href:
            continue

        abs_url = urljoin(stats_url, href)
        path = urlparse(abs_url).path.lower()
        txt = (a.get_text(" ", strip=True) or "").lower()

        if path.endswith("/pdf") and "/football/" in path and "/boxscore/" in path:
            candidates.append(abs_url)

        if "pdf" in txt and "/boxscore/" in path:
            candidates.append(abs_url)

        if path.endswith(".pdf"):
            candidates.append(abs_url)

    for u in candidates:
        if urlparse(u).path.lower().endswith("/pdf"):
            return u

    return candidates[0] if candidates else None


def parse_home_away_from_pdf_landing(
    pdf_landing_url: str,
    school_name: str,
) -> Optional[Tuple[str, str]]:
    path = urlparse(pdf_landing_url).path.lower()
    m = re.search(r"/boxscore/(at|vs)-([^/]+)/", path)
    if not m:
        return None

    at_vs, opp_slug = m.group(1), m.group(2)
    school = tokenize(school_name)
    opp = slug_to_team(opp_slug)

    return (opp, school) if at_vs == "at" else (school, opp)


def find_fullscreen_pdf(session: requests.Session, pdf_landing_url: str) -> Optional[str]:
    try:
        soup = get_soup(session, pdf_landing_url)
    except Exception as e:
        raise StatsFetchError(f"Could not fetch/parse PDF landing page: {pdf_landing_url}") from e

    for a in soup.find_all("a"):
        txt = (a.get_text(" ", strip=True) or "").lower()
        href = a.get("href") or ""
        if "full" in txt and "screen" in txt and href:
            u = urljoin(pdf_landing_url, href)
            if u.lower().endswith(".pdf"):
                return u

    for a in soup.find_all("a"):
        href = a.get("href") or ""
        if href.lower().endswith(".pdf"):
            return urljoin(pdf_landing_url, href)

    return None


def build_filename(home: str, away: str, month: int, year: int) -> str:
    return f"{tokenize(home)}.{tokenize(away)}.{month}.{year}.pdf"


def download_file(session: requests.Session, url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".part")

    try:
        with session.get(url, stream=True, timeout=(15, 90)) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)

        tmp.replace(out_path)

    except Exception as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise DownloadFailedError(f"Failed downloading PDF: {url}") from e


def verify_url_reachable(session: requests.Session, url: str) -> None:
    try:
        r = session.head(url, allow_redirects=True, timeout=(10, 30))
        if r.status_code >= 400:
            r = session.get(url, stream=True, timeout=(10, 30))
            r.raise_for_status()

    except requests.exceptions.HTTPError as e:
        raise ScheduleFetchError(f"Schedule page returned HTTP error for {url}: {e}") from e
    except requests.exceptions.ConnectionError as e:
        raise ScheduleFetchError(f"Could not connect to schedule page: {url}") from e
    except requests.exceptions.Timeout as e:
        raise ScheduleFetchError(f"Timeout while connecting to schedule page: {url}") from e
    except requests.exceptions.RequestException as e:
        raise ScheduleFetchError(f"Unexpected network error when accessing {url}: {e}") from e


def robots_parser_for_base(session: requests.Session, base: str) -> tuple[RobotFileParser, str]:
    robots_url = f"{base.rstrip('/')}/robots.txt"
    rp = RobotFileParser()
    rp.set_url(robots_url)

    try:
        r = session.get(robots_url, timeout=30)

        if 400 <= r.status_code < 500:
            rp.parse(["User-agent: *", "Disallow:"])
            return rp, robots_url

        if 500 <= r.status_code < 600:
            rp.parse(["User-agent: *", "Disallow: /"])
            return rp, robots_url

        r.raise_for_status()
        rp.parse(r.text.splitlines())
        return rp, robots_url

    except Exception:
        rp.parse(["User-agent: *", "Disallow: /"])
        return rp, robots_url


def robots_allows(rp: RobotFileParser, user_agent: str, url: str) -> bool:
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

    Examples:
    - scrape_school_pdfs(..., years=[2025, 2024, 2023])
    - scrape_school_pdfs(..., start_year=2016, end_year=2025)
    """
    if school not in SCHOOLS:
        raise InvalidSchoolError(f"Unknown school: {school}")

    years_to_scrape = normalize_years(years=years, start_year=start_year, end_year=end_year)

    rp, _robots_url = robots_parser_for_base(session, base)
    ua = session.headers.get("User-Agent", "*")

    seen_pdf_urls: Set[str] = set()
    seen_filenames: Set[str] = set()
    downloaded_game_ids: Set[str] = set()
    downloaded_gamekeys_unordered: Set[Tuple[str, str, int, int]] = set()

    results: List[DownloadResult] = []

    for target_year in years_to_scrape:
        sch_url = schedule_url(base, target_year)

        verify_url_reachable(session, sch_url)

        if not robots_allows(rp, ua, sch_url):
            raise RobotsBlockedError(f"robots.txt disallows schedule page: {sch_url}")

        links = collect_stats_boxscore_links(session, base, school, target_year)

        if limit_links and limit_links > 0:
            links = links[:limit_links]

        for item in links:
            stats_url = item.url

            if not robots_allows(rp, ua, stats_url):
                raise RobotsBlockedError(f"robots.txt disallows stats page: {stats_url}")

            try:
                stats_html = get_html(session, stats_url)
            except Exception as e:
                raise StatsFetchError(f"Could not fetch stats page: {stats_url}") from e

            month_year = parse_month_year(stats_html)
            if not month_year:
                raise DateParseError(f"Could not parse date from stats page: {stats_url}")

            month, parsed_year = month_year

            # Only keep records that actually belong to the requested year.
            if parsed_year != target_year:
                continue

            pdf_landing = find_pdf_landing_link(session, stats_url)
            if not pdf_landing:
                raise PdfLandingNotFoundError(f"Could not find PDF landing link: {stats_url}")

            if not robots_allows(rp, ua, pdf_landing):
                raise RobotsBlockedError(f"robots.txt disallows PDF landing page: {pdf_landing}")

            game_id = extract_game_id(pdf_landing) or extract_game_id(stats_url) or ""

            home_away = parse_home_away_from_pdf_landing(pdf_landing, school)
            if not home_away:
                raise HomeAwayParseError(
                    f"Could not parse home/away from landing URL: {pdf_landing}"
                )

            home, away = home_away

            pdf_url = find_fullscreen_pdf(session, pdf_landing)
            if not pdf_url:
                raise DirectPdfNotFoundError(f"Could not find direct PDF URL: {pdf_landing}")

            if not robots_allows(rp, ua, pdf_url):
                raise RobotsBlockedError(f"robots.txt disallows direct PDF URL: {pdf_url}")

            pdf_game_id = extract_game_id(pdf_url) or ""
            if pdf_game_id:
                game_id = pdf_game_id

            filename = build_filename(home, away, month, parsed_year)
            out_path = out_dir / filename

            a, b = sorted([home, away])
            unordered_key = (a, b, month, parsed_year)

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
                        year=parsed_year,
                        filename=filename,
                        file_path=str(out_path),
                        status="DUPLICATE",
                    )
                )
                continue

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
                    schedule_url=sch_url,
                    stats_url=stats_url,
                    game_id=game_id,
                    pdf_landing_url=pdf_landing,
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

    Examples:
    - discover_pdf_records(["Hamilton"], years=[2025, 2024, 2023])
    - discover_pdf_records(["Hamilton"], start_year=2016, end_year=2025)
    """
    session = make_session()
    years_to_scrape = normalize_years(years=years, start_year=start_year, end_year=end_year)

    allow = set(schools)
    unknown = sorted(list(allow - set(SCHOOLS.keys())))
    if unknown:
        raise InvalidSchoolError(f"Unknown school(s): {', '.join(unknown)}")

    results: List[dict] = []
    seen_keys: Set[Tuple[str, str, int, int]] = set()
    seen_pdf_urls: Set[str] = set()

    for school in schools:
        base = SCHOOLS[school]
        rp, _robots_url = robots_parser_for_base(session, base)
        ua = session.headers.get("User-Agent", "*")

        for target_year in years_to_scrape:
            sch_url = schedule_url(base, target_year)

            verify_url_reachable(session, sch_url)

            if not robots_allows(rp, ua, sch_url):
                raise RobotsBlockedError(f"robots.txt disallows schedule page: {sch_url}")

            links = collect_stats_boxscore_links(session, base, school, target_year)

            if limit_links and limit_links > 0:
                links = links[:limit_links]

            for item in links:
                stats_url = item.url

                if not robots_allows(rp, ua, stats_url):
                    raise RobotsBlockedError(f"robots.txt disallows stats page: {stats_url}")

                try:
                    stats_html = get_html(session, stats_url)
                except Exception as e:
                    raise StatsFetchError(f"Could not fetch stats page: {stats_url}") from e

                month_year = parse_month_year(stats_html)
                if not month_year:
                    raise DateParseError(f"Could not parse date from stats page: {stats_url}")

                month, parsed_year = month_year

                if parsed_year != target_year:
                    continue

                pdf_landing = find_pdf_landing_link(session, stats_url)
                if not pdf_landing:
                    raise PdfLandingNotFoundError(f"Could not find PDF landing link: {stats_url}")

                if not robots_allows(rp, ua, pdf_landing):
                    raise RobotsBlockedError(f"robots.txt disallows PDF landing page: {pdf_landing}")

                home_away = parse_home_away_from_pdf_landing(pdf_landing, school)
                if not home_away:
                    raise HomeAwayParseError(
                        f"Could not parse home/away from landing URL: {pdf_landing}"
                    )

                home, away = home_away

                pdf_url = find_fullscreen_pdf(session, pdf_landing)
                if not pdf_url:
                    raise DirectPdfNotFoundError(f"Could not find direct PDF URL: {pdf_landing}")

                if not robots_allows(rp, ua, pdf_url):
                    raise RobotsBlockedError(f"robots.txt disallows direct PDF URL: {pdf_url}")

                game_id = (
                    extract_game_id(pdf_url)
                    or extract_game_id(pdf_landing)
                    or extract_game_id(stats_url)
                    or ""
                )

                a, b = sorted([home, away])
                dedupe_key = (a, b, month, parsed_year)

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
                        "pdf_landing_url": pdf_landing,
                        "home_team": home,
                        "away_team": away,
                        "filename": build_filename(home, away, month, parsed_year),
                        "game_id": game_id,
                        "schedule_url": sch_url,
                    }
                )

    results.sort(key=lambda r: (r["year"], r["month"], r["school"], r["filename"]))
    return results