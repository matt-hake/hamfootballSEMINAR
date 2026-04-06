#CITE: CHATGPT 5.2

#File description: This file all errors we may raise in the process of scraping/
#attempting to access websites.

from __future__ import annotations


class HamscrapeError(Exception):
    """Base class for scraper-specific exceptions."""


class InvalidSchoolError(HamscrapeError):
    """Raised when a requested school is not in SCHOOLS."""


class RobotsBlockedError(HamscrapeError):
    """Raised when robots.txt disallows fetching a required URL."""

#hamscrape.py (collect_expected_game_ids_from_schedule)
class ScheduleFetchError(HamscrapeError):
    """Raised when a schedule page cannot be fetched or parsed."""


class StatsFetchError(HamscrapeError):
    """Raised when a stats/boxscore page cannot be fetched or parsed."""


class DateParseError(HamscrapeError):
    """Raised when a game date cannot be parsed from a stats page."""


class PdfLandingNotFoundError(HamscrapeError):
    """Raised when a PDF landing page cannot be found from a stats page."""


class HomeAwayParseError(HamscrapeError):
    """Raised when home/away teams cannot be inferred from a landing URL."""


class DirectPdfNotFoundError(HamscrapeError):
    """Raised when a direct .pdf link cannot be found."""


class DownloadFailedError(HamscrapeError):
    """Raised when the PDF download itself fails."""

#new errors for API/LLM parser integration

class ParseFailedError(Exception):
    """Raised when ham_parser fails to generate the Excel output."""
    pass


class LlmServiceError(Exception):
    """Raised when the external LLM service fails or is unavailable."""
    pass