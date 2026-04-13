# CITE: CHATGPT 5.2 (UI STRUCTURE FOR DEMO PURPOSES)

from __future__ import annotations

# ------------------------------------------------------------
# Standard library imports
# ------------------------------------------------------------

import uuid
from pathlib import Path
from urllib.parse import urlparse
from datetime import datetime

# ------------------------------------------------------------
# Third-party imports
# ------------------------------------------------------------

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

# ------------------------------------------------------------
# Internal project imports
# ------------------------------------------------------------

# IMPORTANT:
# We no longer import YEAR here, because the scraper should support
# many years, not just one hard-coded season.
from hamscrape import DEFAULT_YEAR, _load_school_config, discover_pdf_records

from ham_parser import parse, build_dataframe_from_pdf

from dotenv import load_dotenv
load_dotenv()

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
    ParseFailedError,
    LlmServiceError,
)

# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
SRC_DIR = APP_DIR.parent
PROJECT_DIR = SRC_DIR.parent

# ------------------------------------------------------------
# Project directories used by the pipeline
# ------------------------------------------------------------

CONFIG_DIR = PROJECT_DIR / "config"
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = DATA_DIR / "temp"
EXPORTS_DIR = DATA_DIR / "exports"

# ------------------------------------------------------------
# Frontend directories
# ------------------------------------------------------------

DEFAULT_CSV_PATH = CONFIG_DIR / "schools.csv"
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

# ------------------------------------------------------------
# Ensure required directories exist
# ------------------------------------------------------------

TEMP_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------
# Flask app initialization
# ------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)

# ------------------------------------------------------------
# Error handling helpers
# ------------------------------------------------------------

def make_error_response(
    message: str,
    *,
    status_code: int = 400,
    error_type: str = "APIError",
    detail: str | None = None,
    context: dict | None = None,
):
    """
    Create a consistent JSON error payload for the frontend.
    """
    payload = {
        "ok": False,
        "error_type": error_type,
        "error": message,
    }

    if detail:
        payload["detail"] = detail

    if context:
        payload["context"] = context

    return jsonify(payload), status_code


def handle_known_exception(e: Exception):
    """
    Translate scraper/parser-specific exceptions into user-friendly API responses.
    """

    if isinstance(e, InvalidSchoolError):
        return make_error_response(
            "One or more selected schools are not valid.",
            status_code=400,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, RobotsBlockedError):
        return make_error_response(
            "Scraping was blocked by the site's robots.txt rules.",
            status_code=403,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, ScheduleFetchError):
        return make_error_response(
            "Could not load a school's schedule page.",
            status_code=502,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, StatsFetchError):
        return make_error_response(
            "Could not load one of the stats or boxscore pages needed to find a PDF.",
            status_code=502,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, PdfLandingNotFoundError):
        return make_error_response(
            "A game was found, but its PDF landing page could not be located.",
            status_code=404,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, DirectPdfNotFoundError):
        return make_error_response(
            "A PDF page was found, but the direct PDF file link could not be extracted.",
            status_code=404,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, DateParseError):
        return make_error_response(
            "A game page was found, but the game date could not be interpreted.",
            status_code=422,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, HomeAwayParseError):
        return make_error_response(
            "A PDF was found, but the home/away team information could not be interpreted.",
            status_code=422,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, DownloadFailedError):
        return make_error_response(
            "The PDF link was found, but downloading the file failed.",
            status_code=502,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, requests.Timeout):
        return make_error_response(
            "The request took too long and timed out.",
            status_code=504,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, requests.RequestException):
        return make_error_response(
            "A network error occurred while contacting a remote site.",
            status_code=502,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, ParseFailedError):
        return make_error_response(
            "The PDF was downloaded, but parsing it into structured play data failed.",
            status_code=500,
            error_type=type(e).__name__,
            detail=str(e),
        )

    if isinstance(e, LlmServiceError):
        return make_error_response(
            "The parser reached the LLM extraction step, but the LLM service failed.",
            status_code=502,
            error_type=type(e).__name__,
            detail=str(e),
        )

    return make_error_response(
        "An unexpected error occurred.",
        status_code=500,
        error_type=type(e).__name__,
        detail=str(e),
    )

# ------------------------------------------------------------
# Helper functions used by routes
# ------------------------------------------------------------

def list_schools() -> list[str]:
    """
    Load school names from the CSV config file.
    """
    schools_map, _ = _load_school_config(DEFAULT_CSV_PATH)
    return sorted(schools_map.keys())


def normalize_limit_links(value) -> int:
    """
    Convert limit_links to a safe integer.
    """
    if value in (None, "", 0, "0"):
        return 0

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("'limit_links' must be an integer")

    if parsed < 0:
        raise ValueError("'limit_links' cannot be negative")

    return parsed


def normalize_year_payload(payload: dict) -> list[int]:
    """
    NEW:
    Convert frontend year input into a validated list of years.

    Supported request shapes:
    1. { "year": 2025 }
    2. { "years": [2021, 2022, 2023, 2024, 2025] }
    3. { "start_year": 2016, "end_year": 2025 }

    Returns:
        sorted distinct list of years
    """
    # Option 1: explicit list of years
    raw_years = payload.get("years")
    if raw_years is not None:
        if not isinstance(raw_years, list):
            raise ValueError("'years' must be a list of integers.")
        if not raw_years:
            raise ValueError("'years' cannot be empty.")

        out: list[int] = []
        for y in raw_years:
            try:
                out.append(int(y))
            except (TypeError, ValueError):
                raise ValueError("Every value in 'years' must be an integer.")

        years = sorted(set(out))
        return years

    # Option 2: start/end range
    has_start = "start_year" in payload and payload.get("start_year") not in (None, "")
    has_end = "end_year" in payload and payload.get("end_year") not in (None, "")

    if has_start or has_end:
        if not (has_start and has_end):
            raise ValueError("Both 'start_year' and 'end_year' must be provided together.")

        try:
            start_year = int(payload.get("start_year"))
            end_year = int(payload.get("end_year"))
        except (TypeError, ValueError):
            raise ValueError("'start_year' and 'end_year' must be integers.")

        if start_year > end_year:
            raise ValueError("'start_year' cannot be greater than 'end_year'.")

        return list(range(start_year, end_year + 1))

    # Option 3: single year (backward compatible)
    try:
        single_year = int(payload.get("year", DEFAULT_YEAR))
    except (TypeError, ValueError):
        raise ValueError("'year' must be an integer.")

    return [single_year]


def build_local_pdf_name(record: dict, pdf_url: str) -> str:
    """
    Generate a readable local filename for a downloaded PDF.
    """
    school = secure_filename(str(record.get("school", "school")))
    opponent = secure_filename(str(record.get("opponent", "opponent")))
    date_str = secure_filename(str(record.get("date", "unknown-date")))

    if school and opponent and date_str:
        base_name = f"{school}_{opponent}_{date_str}"
    else:
        parsed = urlparse(pdf_url)
        base_name = Path(parsed.path).stem or "game_pdf"

    return f"{base_name}_{uuid.uuid4().hex[:8]}.pdf"


def build_local_excel_name(record: dict) -> str:
    school = secure_filename(str(record.get("school", "school")))
    opponent = secure_filename(str(record.get("opponent", "opponent")))
    date_str = secure_filename(str(record.get("date", "unknown-date")))

    base_name = f"{school}_{opponent}_{date_str}"
    return f"{base_name}.xlsx"


def download_pdf_to_temp(pdf_url: str, record: dict) -> Path:
    """
    Download a PDF from its source URL into data/temp/.
    """
    try:
        response = requests.get(pdf_url, timeout=(15, 90))
        response.raise_for_status()
    except requests.RequestException as e:
        raise DownloadFailedError(f"Failed to download PDF from {pdf_url}: {e}") from e

    local_name = build_local_pdf_name(record, pdf_url)
    local_path = TEMP_DIR / local_name
    local_path.write_bytes(response.content)

    return local_path


def add_ui_fields_to_record(record: dict) -> dict:
    """
    Enrich a discovered record with UI-specific fields.
    """
    enriched = dict(record)

    pdf_url = enriched.get("pdf_url")
    if pdf_url:
        enriched["source_pdf_url"] = pdf_url

    return enriched

# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    return jsonify({"ok": True})


@app.get("/api/schools")
def api_schools():
    """
    Return school list plus default year info for the frontend.

    NEW:
    We now expose:
    - default_year
    - a suggested 10-year window
    """
    try:
        current_year = datetime.now().year

        return jsonify(
            {
                "ok": True,
                "default_year": DEFAULT_YEAR,
                "suggested_start_year": current_year - 9,
                "suggested_end_year": current_year,
                "schools": list_schools(),
            }
        )
    except Exception as e:
        return handle_known_exception(e)


@app.post("/api/find-pdfs")
def api_find_pdfs():
    """
    Discover PDF records based on user input.

    This endpoint does NOT download PDFs.
    It only discovers metadata about available PDFs.

    NEW:
    Supports:
    - year
    - years
    - start_year + end_year
    """
    payload = request.get_json(silent=True) or {}

    try:
        years = normalize_year_payload(payload)
    except ValueError as e:
        return make_error_response(
            str(e),
            status_code=400,
            error_type="ValidationError",
        )

    schools = payload.get("schools")

    if schools is None:
        schools = list_schools()

    if not isinstance(schools, list):
        return make_error_response(
            "'schools' must be a list or null.",
            status_code=400,
            error_type="ValidationError",
        )

    try:
        limit_links = normalize_limit_links(payload.get("limit_links", 0))
    except ValueError as e:
        return make_error_response(
            str(e),
            status_code=400,
            error_type="ValidationError",
        )

    try:
        # IMPORTANT CHANGE:
        # We now pass 'years=years' instead of 'year=year'
        results = discover_pdf_records(
            schools=schools,
            years=years,
            limit_links=limit_links,
        )

        enriched_results = [add_ui_fields_to_record(r) for r in results]

        return jsonify(
            {
                "ok": True,
                "count": len(enriched_results),
                "years": years,
                "results": enriched_results,
            }
        )

    except Exception as e:
        return handle_known_exception(e)


@app.post("/api/download-pdf")
def api_download_pdf():
    """
    Download a selected PDF into the temp directory
    and return URLs the frontend can use.
    """
    payload = request.get_json(silent=True) or {}
    record = payload.get("record")

    if not isinstance(record, dict):
        return make_error_response(
            "'record' must be an object.",
            status_code=400,
            error_type="ValidationError",
        )

    pdf_url = record.get("pdf_url")

    if not pdf_url or not isinstance(pdf_url, str):
        return make_error_response(
            "Selected record is missing a valid 'pdf_url'.",
            status_code=400,
            error_type="ValidationError",
        )

    try:
        local_pdf_path = download_pdf_to_temp(pdf_url, record)

        return jsonify(
            {
                "ok": True,
                "filename": local_pdf_path.name,
                "preview_url": f"/pdfs/{local_pdf_path.name}",
                "download_url": f"/pdfs/{local_pdf_path.name}?download=1",
                "source_pdf_url": pdf_url,
                "record": record,
            }
        )

    except Exception as e:
        return handle_known_exception(e)


@app.get("/pdfs/<path:filename>")
def serve_pdf(filename: str):
    """
    Serve PDFs stored in the temp directory.
    """
    as_attachment = request.args.get("download") == "1"

    return send_from_directory(
        directory=str(TEMP_DIR),
        path=filename,
        as_attachment=as_attachment,
        mimetype="application/pdf",
    )


@app.get("/downloads/<path:filename>")
def download_generated_file(filename: str):
    """
    Compatibility route for older frontend code.
    """
    return send_from_directory(
        directory=str(TEMP_DIR),
        path=filename,
        as_attachment=True,
        mimetype="application/pdf",
    )


@app.post("/api/parse-pdf")
def api_parse_pdf():
    """
    Download a selected PDF, parse it into play data,
    save an Excel output, and return preview data for the UI.
    """
    payload = request.get_json(silent=True) or {}
    record = payload.get("record")

    if not isinstance(record, dict):
        return make_error_response(
            "'record' must be an object.",
            status_code=400,
            error_type="ValidationError",
        )

    pdf_url = record.get("pdf_url")
    if not pdf_url or not isinstance(pdf_url, str):
        return make_error_response(
            "Selected record is missing a valid 'pdf_url'.",
            status_code=400,
            error_type="ValidationError",
        )

    try:
        local_pdf_path = download_pdf_to_temp(pdf_url, record)

        excel_name = build_local_excel_name(record)
        excel_path = EXPORTS_DIR / excel_name
        
        # RUN THE PARSER ONCE
        final_excel_path = parse(local_pdf_path, excel_path)

        # Read the newly created Excel file back in for the frontend preview
        import pandas as pd
        df = pd.read_excel(final_excel_path)
        
        if df.empty:
            return make_error_response(
                "The parser ran, but no plays were extracted from the PDF.",
                status_code=422,
                error_type="ParseFailedError",
            )

        preview_df = df.head(200).fillna("")
        rows = preview_df.to_dict(orient="records")
        columns = list(preview_df.columns)

        return jsonify(
            {
                "ok": True,
                "filename": final_excel_path.name,
                "pdf_preview_url": f"/pdfs/{local_pdf_path.name}",
                "excel_download_url": f"/exports/{final_excel_path.name}",
                "rows": rows,
                "columns": columns,
                "record": record,
            }
        )

    except Exception as e:
        return handle_known_exception(e)


@app.get("/exports/<path:filename>")
def serve_export(filename: str):
    """
    Serve generated Excel files from the exports directory.
    """
    return send_from_directory(
        directory=str(EXPORTS_DIR),
        path=filename,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ------------------------------------------------------------
# App entry point
# ------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)