# CITE: CHATGPT 5.2 (UI STRUCTURE FOR DEMO PURPOSES)

from __future__ import annotations

# ------------------------------------------------------------
# Standard library imports
# ------------------------------------------------------------

# used for generating random suffixes for filenames to avoid collisions
import uuid

# used for filesystem paths in a platform-independent way
from pathlib import Path

# used for extracting file names from URLs
from urllib.parse import urlparse


# ------------------------------------------------------------
# Third-party imports
# ------------------------------------------------------------

# used for downloading PDFs from remote servers
import requests

# Flask components used to build the API server
from flask import Flask, jsonify, render_template, request, send_from_directory

# utility to ensure generated filenames are safe for the filesystem
from werkzeug.utils import secure_filename


# ------------------------------------------------------------
# Internal project imports
# ------------------------------------------------------------

# Core scraper logic lives in hamscrape.py
# The API calls these functions to perform actual work.
from hamscrape import YEAR, _load_school_config, discover_pdf_records

from ham_parser import parse, build_dataframe_from_pdf

from dotenv import load_dotenv
load_dotenv()

# Import scraper-specific error types so the API can translate them
# into clear messages for the frontend/user.
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
    LlmServiceError
)


# ------------------------------------------------------------
# Paths
# ------------------------------------------------------------

# Determine important directories relative to this file

# folder containing api.py
APP_DIR = Path(__file__).resolve().parent

# src directory (one level up)
SRC_DIR = APP_DIR.parent

# project root directory
PROJECT_DIR = SRC_DIR.parent


# ------------------------------------------------------------
# Project directories used by the pipeline
# ------------------------------------------------------------

# config folder (contains schools.csv)
CONFIG_DIR = PROJECT_DIR / "config"

# main data directory
DATA_DIR = PROJECT_DIR / "data"

# temporary files (PDFs downloaded for preview)
TEMP_DIR = DATA_DIR / "temp"

# location for parsed/exported outputs
EXPORTS_DIR = DATA_DIR / "exports"


# ------------------------------------------------------------
# Frontend directories
# ------------------------------------------------------------

# CSV containing editable school list
DEFAULT_CSV_PATH = CONFIG_DIR / "schools.csv"

# folder containing HTML templates
TEMPLATES_DIR = APP_DIR / "templates"

# static assets (CSS, JS, images if needed)
STATIC_DIR = APP_DIR / "static"


# ------------------------------------------------------------
# Ensure required directories exist
# ------------------------------------------------------------

TEMP_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------
# Flask app initialization
# ------------------------------------------------------------

# Create the Flask application instance.
# This object will handle incoming HTTP requests from the browser.
app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)


# ------------------------------------------------------------
# Error handling helpers
# ------------------------------------------------------------

# High level idea:
#
#  scraper raises an exception
#          ↓
#  API catches it
#          ↓
#  API converts it into JSON
#          ↓
#  frontend displays error message to the user

#PACKAGES ERROR INFORMATION INTO JSON FOR FRONT-END
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

    The frontend can display:
    - message: user-facing explanation
    - detail: more technical explanation if desired
    - error_type: machine-readable category
    - context: optional extra debugging or UI context
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

    # jsonify converts the Python dictionary to JSON
    return jsonify(payload), status_code

#CRUCIAL FOR ERROR HANDLING
#CITE: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status?utm
def handle_known_exception(e: Exception):
    """
    Translate scraper-specific exceptions into user-friendly API responses.

    The scraper raises domain-specific errors (InvalidSchoolError,
    ScheduleFetchError, etc.). The API catches them here and converts
    them into structured JSON responses the frontend understands.
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

    # network timeout handling
    if isinstance(e, requests.Timeout):
        return make_error_response(
            "The request took too long and timed out.",
            status_code=504,
            error_type=type(e).__name__,
            detail=str(e),
        )

    # generic network error
    if isinstance(e, requests.RequestException):
        return make_error_response(
            "A network error occurred while contacting a remote site.",
            status_code=502,
            error_type=type(e).__name__,
            detail=str(e),
        )
    
    #new error cases

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

    # fallback for unexpected errors
    return make_error_response(
        "An unexpected error occurred.",
        status_code=500,
        error_type=type(e).__name__,
        detail=str(e),
    )


# ------------------------------------------------------------
# Helper functions used by routes
# ------------------------------------------------------------

#gets the schools from hamscrape, alphabetizes
def list_schools() -> list[str]:
    """
    Load school names from the CSV config file.

    This allows the client to modify the list of schools simply by
    editing config/schools.csv without touching the code.
    """

    schools_map, _ = _load_school_config(DEFAULT_CSV_PATH)

    return sorted(schools_map.keys())

#extra error checking may not be necessary due to input format
def normalize_limit_links(value) -> int:
    """
    Convert limit_links to a safe integer.

    Used for demo safety so the UI cannot accidentally scrape
    thousands of pages if misconfigured.
    """

    if value in (None, "", 0, "0"):
        return 0

    #check if value is an int, accounting for exceptions
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("'limit_links' must be an integer")

    if parsed < 0:
        raise ValueError("'limit_links' cannot be negative")

    return parsed

#CITE: https://tedboy.github.io/flask/generated/werkzeug.secure_filename.html
def build_local_pdf_name(record: dict, pdf_url: str) -> str:
    """
    Generate a readable local filename for a downloaded PDF.

    Prefer using:
        school_opponent_date

    Example:
        Hamilton_Williams_2025-10-19_xxxxxxxx.pdf
    """

    school = secure_filename(str(record.get("school", "school")))
    opponent = secure_filename(str(record.get("opponent", "opponent")))
    date_str = secure_filename(str(record.get("date", "unknown-date")))

    if school and opponent and date_str:
        base_name = f"{school}_{opponent}_{date_str}"
    else:
        parsed = urlparse(pdf_url)
        base_name = Path(parsed.path).stem or "game_pdf"

    # random suffix prevents overwriting files with same name
    return f"{base_name}_{uuid.uuid4().hex[:8]}.pdf"

# NEW: builds a clean, deterministic Excel filename so each parsed game
# gets a readable output like "Hamilton_Williams_2025-10-19.xlsx"

def build_local_excel_name(record: dict) -> str:
    school = secure_filename(str(record.get("school", "school")))
    opponent = secure_filename(str(record.get("opponent", "opponent")))
    date_str = secure_filename(str(record.get("date", "unknown-date")))

    base_name = f"{school}_{opponent}_{date_str}"
    return f"{base_name}.xlsx"


#new:

#record will be DownloadResult object converted to dictionary
def download_pdf_to_temp(pdf_url: str, record: dict) -> Path:
    """
    Download a PDF from its source URL into data/temp/.

    This does NOT parse the PDF. It simply stores it locally
    so the frontend can preview or download it.
    """

    #try to download the pdf
    try:
        response = requests.get(pdf_url, timeout=(15, 90))
        response.raise_for_status()
    except requests.RequestException as e:
        raise DownloadFailedError(f"Failed to download PDF from {pdf_url}: {e}") from e

    #use helper to generate file name
    local_name = build_local_pdf_name(record, pdf_url)
    local_path = TEMP_DIR / local_name

    #write out the content to the temporary directory so we can use it later
    local_path.write_bytes(response.content)

    return local_path

#create a duplicate value of pdf_url so we can point to it in memory and via source
def add_ui_fields_to_record(record: dict) -> dict:
    """
    Enrich a discovered record with UI-specific fields.

    This makes the frontend logic simpler.
    """

    enriched = dict(record)

    pdf_url = enriched.get("pdf_url")

    if pdf_url:
        enriched["source_pdf_url"] = pdf_url

    return enriched


# ------------------------------------------------------------
# Routes
# ------------------------------------------------------------

# LINK 1. Loads our template when the user visits the root
@app.get("/")
def index():
    """
    Serve the main frontend page.

    When the user visits the site root, Flask renders index.html.
    """
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    """
    Simple health check endpoint.

    Used to confirm that the API server is running.
    """
    return jsonify({"ok": True})

#when schools is requested, get schools from hamparser (using imported
# function) and convert to json
@app.get("/api/schools")
def api_schools():
    """
    Return the list of available schools to the frontend.

    Called when the UI loads so it can populate the checkbox list.
    """
    try:
        return jsonify(
            {
                "ok": True,
                "year": YEAR,
                "schools": list_schools(),
            }
        )
    except Exception as e:
        return handle_known_exception(e)

#POST request is triggered (client to server), prompting us to find all pdfs
#pdfs are stored as 
@app.post("/api/find-pdfs")
def api_find_pdfs():
    """
    Discover PDF records based on user input.

    This endpoint does NOT download PDFs.
    It only discovers metadata about available PDFs.
    """

    #payload is a python dictionary
    payload = request.get_json(silent=True) or {}

    #make sure year looks how it should
    try:
        year = int(payload.get("year", YEAR))
    except (TypeError, ValueError):
        return make_error_response(
            "'year' must be an integer.",
            status_code=400,
            error_type="ValidationError",
        )

    #pull out schools
    schools = payload.get("schools")

    #if no selection of schools was made, use all of them
    if schools is None:
        schools = list_schools()

    #make sure we're working with a list
    if not isinstance(schools, list):
        return make_error_response(
            "'schools' must be a list or null.",
            status_code=400,
            error_type="ValidationError",
        )

    #get limit_links value (error checking built into function)
    try:
        limit_links = normalize_limit_links(payload.get("limit_links", 0))
    except ValueError as e:
        return make_error_response(
            str(e),
            status_code=400,
            error_type="ValidationError",
        )

    try:
        # Call scraper logic from hamscrape.py
        #CRUCIAL LINK 3
        
        """ User clicks "Find PDFs"
            ↓
        index.html JavaScript
            ↓
        POST /api/find-pdfs
            ↓
        api_find_pdfs()
            ↓
        discover_pdf_records()  ← hamscrape.py
            ↓
        scraper finds PDFs
            ↓
        results returned to API
            ↓
        API sends JSON to frontend """

        results = discover_pdf_records(
            year=year,
            schools=schools,
            limit_links=limit_links,
        )

        #add extra fields for front end
        enriched_results = [add_ui_fields_to_record(r) for r in results]

        #turn into json, return
        return jsonify(
            {
                "ok": True,
                "count": len(enriched_results),
                "results": enriched_results,
            }
        )

    except Exception as e:
        return handle_known_exception(e)

#"function" to download pdf that index html fetches
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

    This route allows:
    - browser preview
    - direct download
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

    Currently serves files from the same temp folder.
    """

    return send_from_directory(
        directory=str(TEMP_DIR),
        path=filename,
        as_attachment=True,
        mimetype="application/pdf",
    )


#new 4/7

# NEW: This is the core pipeline endpoint:
# scraper → download PDF → parse → generate Excel → return preview + links
#
# This replaces the old "download-only" workflow with full processing.

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
        # Step 1: download source PDF locally
        local_pdf_path = download_pdf_to_temp(pdf_url, record)

        # Step 2: build in-memory dataframe for UI preview
        df = build_dataframe_from_pdf(local_pdf_path)

        if df.empty:
            return make_error_response(
                "The parser ran, but no plays were extracted from the PDF.",
                status_code=422,
                error_type="ParseFailedError",
            )

        # Step 3: write final Excel file
        excel_name = build_local_excel_name(record)
        excel_path = EXPORTS_DIR / excel_name
        final_excel_path = parse(local_pdf_path, excel_path)

        # Step 4: prepare preview rows for frontend table
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
    # Start Flask development server
    app.run(debug=True, host="127.0.0.1", port=5000)