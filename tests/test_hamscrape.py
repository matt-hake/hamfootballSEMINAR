from __future__ import annotations

import sys
from pathlib import Path

# Add the project's src/ directory to Python's import path (CITE: CHATGPT)
TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent
SRC_DIR = PROJECT_DIR / "src"

#if the source directory doesn't exist
if str(SRC_DIR) not in sys.path:
    #add ti
    sys.path.insert(0, str(SRC_DIR))

from hamscrape import (
    build_filename,
    extract_game_id,
    parse_home_away_from_pdf_landing,
    parse_month_year,
    tokenize
)

#checks token creation is working
def test_tokenize():
    assert tokenize("Hamilton") == "HAMILTON"
    assert tokenize("Middlebury College") == "MIDDLEBURYCOLLEGE"

#checks extraction of id
def test_extract_game_id():
    url = "https://athletics.hamilton.edu/sports/football/stats/2025/williams/boxscore/6102"
    assert extract_game_id(url) == "6102"
    assert extract_game_id("https://example.com/nope") is None

#first m/d/y convention
def test_parse_month_year_numeric():
    html = "Kickoff was on 10/19/2025 at noon."
    assert parse_month_year(html) == (10, 2025)

#second m/d/y convention
def test_parse_month_year_named():
    html = "Kickoff was on September 21, 2025 at noon."
    assert parse_month_year(html) == (9, 2025)

#first url naming convention function
def test_parse_home_away_from_pdf_landing_vs():
    url = "https://athletics.hamilton.edu/sports/football/stats/2025/williams/boxscore/vs-williams/pdf"
    assert parse_home_away_from_pdf_landing(url, "Hamilton") == ("HAMILTON", "WILLIAMS")

#second url naming convention function
def test_parse_home_away_from_pdf_landing_at():
    url = "https://athletics.hamilton.edu/sports/football/stats/2025/williams/boxscore/at-williams/pdf"
    assert parse_home_away_from_pdf_landing(url, "Hamilton") == ("WILLIAMS", "HAMILTON")

#correct filename from parameters
def test_build_filename():
    assert build_filename("Hamilton", "Williams", 10, 2025) == "HAMILTON.WILLIAMS.10.2025.pdf"