from __future__ import annotations

import sys
from pathlib import Path

#CITE: CHATGPT for formatting, cleaning

# Add the project's src/ directory to Python's import path so this test file
# can import hamscrape when pytest is run from the project root or other
# working directories.
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent
SRC_DIR = PROJECT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hamscrape import (
    build_filename,
    extract_game_id,
    parse_home_away_from_pdf_landing,
    parse_month_year,
    tokenize,
)


def test_tokenize() -> None:
    """Verify that team/school names are converted into uppercase tokens."""
    assert tokenize("Hamilton") == "HAMILTON"
    assert tokenize("Middlebury College") == "MIDDLEBURYCOLLEGE"


def test_extract_game_id() -> None:
    """Verify that a numeric game ID is extracted when present in the URL."""
    url = "https://athletics.hamilton.edu/sports/football/stats/2025/williams/boxscore/6102"
    assert extract_game_id(url) == "6102"
    assert extract_game_id("https://example.com/nope") is None


def test_parse_month_year_numeric() -> None:
    """Verify parsing of dates written in numeric month/day/year format."""
    html = "Kickoff was on 10/19/2025 at noon."
    assert parse_month_year(html) == (10, 2025)


def test_parse_month_year_named() -> None:
    """Verify parsing of dates written with a spelled-out month name."""
    html = "Kickoff was on September 21, 2025 at noon."
    assert parse_month_year(html) == (9, 2025)


def test_parse_home_away_from_pdf_landing_vs() -> None:
    """
    Verify home/away parsing for URLs using the 'vs-' convention.

    In this convention, the source school is treated as the home team.
    """
    url = (
        "https://athletics.hamilton.edu/sports/football/stats/2025/"
        "williams/boxscore/vs-williams/pdf"
    )
    assert parse_home_away_from_pdf_landing(url, "Hamilton") == (
        "HAMILTON",
        "WILLIAMS",
    )


def test_parse_home_away_from_pdf_landing_at() -> None:
    """
    Verify home/away parsing for URLs using the 'at-' convention.

    In this convention, the source school is treated as the away team.
    """
    url = (
        "https://athletics.hamilton.edu/sports/football/stats/2025/"
        "williams/boxscore/at-williams/pdf"
    )
    assert parse_home_away_from_pdf_landing(url, "Hamilton") == (
        "WILLIAMS",
        "HAMILTON",
    )


def test_build_filename() -> None:
    """Verify that the standardized PDF filename is built correctly."""
    assert (
        build_filename("Hamilton", "Williams", 10, 2025)
        == "HAMILTON.WILLIAMS.10.2025.pdf"
    )