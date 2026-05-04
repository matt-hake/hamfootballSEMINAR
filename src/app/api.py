# ------------------------------------------------------------
# API LAYER FOR NESCAC PDF PIPELINE
# ------------------------------------------------------------
# CITE: CHATGPT 5.2 (UI STRUCTURE FOR DEMO PURPOSES)
# ------------------------------------------------------------

from __future__ import annotations

# ============================================================
# STANDARD LIBRARY IMPORTS
# ============================================================

import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ============================================================
# THIRD-PARTY IMPORTS
# ============================================================

import pandas as pd
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

# ============================================================
# INTERNAL PROJECT IMPORTS
# ============================================================

# IMPORTANT:
# We do not import a single hard-coded YEAR value for route logic.
# Instead, the API supports:
#   - one year
#   - a list of years
#   - a start/end range of years
from hamscrape import DEFAULT_YEAR, _load_school_config, discover_pdf_records

# parse() is the main pipeline step that takes a local PDF path
# and writes the structured Excel output.
from ham_parser import build_dataframe_from_pdf, parse

# These custom exceptions let the API convert lower-level scraper/parser
# failures into cleaner JSON responses for the frontend.
from errors import (
    DateParseError,
    DirectPdfNotFoundError,
    DownloadFailedError,
    HomeAwayParseError,
    InvalidSchoolError,
    LlmServiceError,
    ParseFailedError,
    PdfLandingNotFoundError,
    RobotsBlockedError,
    ScheduleFetchError,
    StatsFetchError,
)

# ============================================================
# ENVIRONMENT SETUP
# ============================================================

# Load environment variables from a .env file if present.
# This is especially useful for secrets such as API keys used by
# parser components like the LLM extraction layer.
load_dotenv()

# ============================================================
# PATH SETUP
# ============================================================

# File layout assumptions:
#
# hamfootballSEMINAR/
#   config/
#     schools.csv
#     potential_errors.yaml
#   data/
#     temp/
#     exports/
#   src/
#     app/
#       api.py
#       templates/
#       static/
#
# APP_DIR is the directory containing this file:
#   src/app/
APP_DIR = Path(__file__).resolve().parent

# SRC_DIR is:
#   src/
SRC_DIR = APP_DIR.parent

# PROJECT_DIR is:
#   hamfootballSEMINAR/
PROJECT_DIR = SRC_DIR.parent

# Project folders used by the scraper/parser pipeline.
CONFIG_DIR = PROJECT_DIR / "config"
DATA_DIR = PROJECT_DIR / "data"
TEMP_DIR = DATA_DIR / "temp"
EXPORTS_DIR = DATA_DIR / "exports"

# Frontend folders for Flask templates and static assets.
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

# CSV config containing the valid school definitions.
DEFAULT_CSV_PATH = CONFIG_DIR / "schools.csv"

# Ensure runtime folders exist before the app starts.
DATA_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# FLASK APP SETUP
# ============================================================

# Create the Flask app and explicitly point it at the template and
# static folders used by the frontend.
app = Flask(
    __name__,
    template_folder=str(TEMPLATES_DIR),
    static_folder=str(STATIC_DIR),
)

# ============================================================
# ERROR RESPONSE HELPERS
# ============================================================

def make_error_response(
    message: str,
    *,
    status_code: int = 400,
    error_type: str = "APIError",
    detail: str | None = None,
    context: dict | None = None,
):
    """
    Build a consistent JSON error payload for the frontend.

    Why this helper exists:
    - It keeps all API error responses in the same shape.
    - The frontend can always expect:
        {
          "ok": False,
          "error_type": ...,
          "error": ...,
          "detail": ...
        }
    - This makes UI-side error handling much simpler.

    Parameters
    ----------
    message:
        Short user-facing summary of what went wrong.
    status_code:
        HTTP status code to return.
    error_type:
        A machine-readable category/name for the error.
    detail:
        Optional lower-level detail string, often str(exception).
    context:
        Optional extra structured metadata.
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
    Translate project-specific exceptions into user-friendly API responses.

    The scraper/parser code raises many meaningful custom exceptions.
    Instead of exposing raw stack traces or generic 500 errors, this
    function maps each known exception type to:
    - a clearer message for the frontend
    - a sensible HTTP status code
    - a consistent JSON structure

    This is one of the main places where backend errors become
    understandable UI errors.
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
            "Could not load one of the stats pages needed to find a PDF.",
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

    # Fallback for anything we did not explicitly map above.
    return make_error_response(
        "An unexpected error occurred.",
        status_code=500,
        error_type=type(e).__name__,
        detail=str(e),
    )

# ============================================================
# GENERAL HELPERS
# ============================================================

def list_schools() -> list[str]:
    """
    Load and return the valid school names from the CSV config.

    The scraper uses a CSV-driven configuration instead of hard-coded
    schools. This function gives the frontend a clean way to populate
    its checkbox list or dropdown of valid school options.
    """
    schools_map, _ = _load_school_config(DEFAULT_CSV_PATH)
    return sorted(schools_map.keys())


def normalize_limit_links(value) -> int:
    """
    Validate and normalize the limit_links payload field.

    Meaning of limit_links:
    - 0 means "no limit"
    - positive integer means "only inspect this many links per school/year"

    This helper accepts common frontend representations like:
    - None
    - ""
    - 0
    - "0"

    and converts them into a safe integer for the scraper.

    Raises
    ------
    ValueError
        If the value cannot be interpreted as a nonnegative integer.
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
    Convert frontend year input into a validated list of years.

    Supported request shapes
    ------------------------
    1. Single year:
       { "year": 2025 }

    2. Explicit list of years:
       { "years": [2021, 2022, 2023, 2024, 2025] }

    3. Year range:
       { "start_year": 2016, "end_year": 2025 }

    Returns
    -------
    list[int]
        Sorted, distinct list of years to send to the scraper.

    Why this helper is useful:
    - The route logic stays cleaner.
    - The scraper always receives the same normalized format:
      a list of integer years.
    """

    # --------------------------------------------------------
    # Option 1: explicit list of years
    # --------------------------------------------------------
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

        # Remove duplicates and return in ascending order so downstream
        # code gets a clean predictable list.
        return sorted(set(out))

    # --------------------------------------------------------
    # Option 2: start/end year range
    # --------------------------------------------------------
    has_start = "start_year" in payload and payload.get("start_year") not in (None, "")
    has_end = "end_year" in payload and payload.get("end_year") not in (None, "")

    if has_start or has_end:
        # If the user supplies one bound, they must supply the other.
        if not (has_start and has_end):
            raise ValueError("Both 'start_year' and 'end_year' must be provided together.")

        try:
            start_year = int(payload.get("start_year"))
            end_year = int(payload.get("end_year"))
        except (TypeError, ValueError):
            raise ValueError("'start_year' and 'end_year' must be integers.")

        if start_year > end_year:
            raise ValueError("'start_year' cannot be greater than 'end_year'.")

        # Convert the range into the unified list-of-years format.
        return list(range(start_year, end_year + 1))

    # --------------------------------------------------------
    # Option 3: single year (default / backward-compatible)
    # --------------------------------------------------------
    try:
        single_year = int(payload.get("year", DEFAULT_YEAR))
    except (TypeError, ValueError):
        raise ValueError("'year' must be an integer.")

    return [single_year]


def build_local_pdf_name(record: dict, pdf_url: str) -> str:
    """
    Build a readable, filesystem-safe local filename for a downloaded PDF.

    Preferred naming pieces:
    - school
    - opponent
    - date

    Example:
        Hamilton_Amherst_2025-10-18_ab12cd34.pdf

    The short UUID suffix helps avoid collisions if two downloads would
    otherwise produce the same filename.
    """
    school = secure_filename(str(record.get("school", "school")))
    opponent = secure_filename(str(record.get("opponent", "opponent")))
    date_str = secure_filename(str(record.get("date", "unknown-date")))

    if school and opponent and date_str:
        base_name = f"{school}_{opponent}_{date_str}"
    else:
        # Fallback: derive something sensible from the URL path if the
        # record metadata is incomplete.
        parsed = urlparse(pdf_url)
        base_name = Path(parsed.path).stem or "game_pdf"

    return f"{base_name}_{uuid.uuid4().hex[:8]}.pdf"


def build_local_excel_name(record: dict) -> str:
    """
    Build the output Excel filename corresponding to a parsed PDF.

    Unlike the PDF temp filename, this one does not add a UUID by default,
    because the Excel file is meant to be the stable output artifact for
    that game.

    Example:
        Hamilton_Amherst_2025-10-18.xlsx
    """
    school = secure_filename(str(record.get("school", "school")))
    opponent = secure_filename(str(record.get("opponent", "opponent")))
    date_str = secure_filename(str(record.get("date", "unknown-date")))

    base_name = f"{school}_{opponent}_{date_str}"
    return f"{base_name}.xlsx"


def download_pdf_to_temp(pdf_url: str, record: dict) -> Path:
    """
    Download a PDF from its source URL into data/temp/.

    This function does not parse the PDF.
    It only stores the raw PDF locally so that the API can:
    - preview it in the browser
    - hand it to the parser
    - let the user download it directly if desired

    Steps
    -----
    1. Request the PDF bytes from the remote site.
    2. Raise an exception if the request fails.
    3. Build a safe local filename.
    4. Save the file into TEMP_DIR.
    5. Return the local path.
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
    Enrich a discovered PDF record with fields that simplify frontend logic.

    Right now this mainly mirrors pdf_url into source_pdf_url so the UI
    can always treat that as the original source location.

    Why copy the field?
    - It makes the UI code more explicit:
      source_pdf_url = original remote file
    - The UI can distinguish between:
        * the original remote PDF URL
        * the local preview URL served by Flask
    """
    enriched = dict(record)

    pdf_url = enriched.get("pdf_url")
    if pdf_url:
        enriched["source_pdf_url"] = pdf_url

    return enriched

# ============================================================
# ROUTES
# ============================================================

@app.get("/")
def index():
    """
    Render the main frontend page.
    """
    return render_template("index.html")


@app.get("/api/health")
def api_health():
    """
    Lightweight health-check endpoint.

    Useful for:
    - confirming the Flask app is running
    - quick connectivity tests from the frontend
    - debugging deployment issues
    """
    return jsonify({"ok": True})


@app.get("/api/schools")
def api_schools():
    """
    Return school list plus year defaults for the frontend.

    The UI can call this on page load to:
    - populate the available school checkboxes/dropdown
    - prefill a sensible default year
    - suggest a recent 10-year window for multi-year scraping
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

    Important:
    This endpoint does NOT download any PDFs.
    It only finds and returns metadata about available PDFs.

    Supported payload fields
    ------------------------
    - year
    - years
    - start_year + end_year
    - schools
    - limit_links

    Typical frontend flow
    ---------------------
    1. User selects schools and year(s)
    2. Frontend POSTs to /api/find-pdfs
    3. Backend returns discovered PDF records
    4. UI displays the available games/PDFs
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

    # If the frontend does not specify schools, default to all schools.
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
        # Main discovery step:
        # the scraper searches the requested schools/years and returns
        # structured metadata records for each PDF it finds.
        results = discover_pdf_records(
            schools=schools,
            years=years,
            limit_links=limit_links,
        )

        # Add frontend-friendly fields to each record before returning them.
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
    Download one selected PDF into the temp directory.

    This is useful when the frontend wants to:
    - preview the original PDF in-browser
    - provide a direct download button
    - separate "download" from "parse"

    Expected payload
    ----------------
    {
      "record": { ... discovered PDF record ... }
    }

    Response includes
    -----------------
    - local filename
    - preview URL served by Flask
    - download URL served by Flask
    - source PDF URL
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
    Serve a PDF stored in TEMP_DIR.

    Query parameter
    ---------------
    ?download=1

    Behavior
    --------
    - Without ?download=1: browser may preview inline
    - With ?download=1: browser is prompted to download the file
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

    This route is preserved so that any earlier UI code still pointing to
    /downloads/... continues to work, even if newer code prefers /pdfs/... .
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
    Download a selected PDF, parse it, save the Excel output,
    and return preview data for the frontend.

    This is the core scraper -> parser -> Excel workflow route.

    Expected flow
    -------------
    1. Frontend sends a discovered PDF record
    2. Backend downloads the source PDF to TEMP_DIR
    3. Backend runs parse(local_pdf_path, excel_path)
    4. Parser writes the Excel file into EXPORTS_DIR
    5. Backend reads the Excel back in with pandas
    6. Backend returns:
       - PDF preview URL
       - Excel download URL
       - preview rows/columns for UI rendering

    Why read the Excel back in?
    ---------------------------
    So the frontend can immediately show a table preview of the parsed
    result without requiring the user to manually open the Excel file.
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
        # Step 1:
        # Download the remote source PDF locally so the parser can work
        # with a concrete file path on disk.
        local_pdf_path = download_pdf_to_temp(pdf_url, record)

        # Step 2:
        # Choose the final Excel output path in the exports directory.
        excel_name = build_local_excel_name(record)
        excel_path = EXPORTS_DIR / excel_name

        # Step 3:
        # Run the parser once. It should return the final path to the
        # generated Excel file.
        final_excel_path = parse(local_pdf_path, excel_path)

        # Step 4:
        # Read the generated Excel back into memory so the frontend can
        # display a preview table immediately.
        df = pd.read_excel(final_excel_path)

        # If the parser technically completed but extracted no structured
        # rows, return a meaningful error instead of pretending success.
        if df.empty:
            return make_error_response(
                "The parser ran, but no plays were extracted from the PDF.",
                status_code=422,
                error_type="ParseFailedError",
            )

        # Step 5:
        # Limit preview size so the JSON response does not get too large.
        # The full Excel remains available through the download route.
        preview_df = df.head(200).fillna("")
        rows = preview_df.to_dict(orient="records")
        columns = list(preview_df.columns)

        # Step 6:
        # Return everything the UI needs to support a human-in-the-loop
        # review workflow:
        #   - the source PDF preview
        #   - the Excel download
        #   - table data for immediate display
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

@app.post("/api/update-excel")
def api_update_excel():
    payload = request.get_json(silent=True) or {}
    filename = payload.get("filename")
    rows = payload.get("rows")

    if not filename or not rows:
        return make_error_response("Missing 'filename' or 'rows'.", status_code=400)

    try:
        safe_name = secure_filename(filename)
        excel_path = EXPORTS_DIR / safe_name
        
        if not excel_path.exists():
            return make_error_response("File not found.", status_code=404)

        # 1. Load the original file just to get the column order
        original_df = pd.read_excel(excel_path)
        original_cols = list(original_df.columns)

        # 2. Create the new DF and reorder its columns to match the original
        new_df = pd.DataFrame(rows)
        
        # Ensure we only use columns that actually exist in the new data
        valid_cols = [c for c in original_cols if c in new_df.columns]
        new_df = new_df[valid_cols]

        new_df.to_excel(excel_path, index=False)

        return jsonify({"ok": True, "message": "Updated with original column order preserved."})

    except Exception as e:
        return handle_known_exception(e)

@app.get("/exports/<path:filename>")
def serve_export(filename: str):
    """
    Serve generated Excel files from EXPORTS_DIR.

    These are returned as downloadable attachments so the client can
    save the structured parser output locally.
    """
    return send_from_directory(
        directory=str(EXPORTS_DIR),
        path=filename,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# ============================================================
# APP ENTRY POINT
# ============================================================

if __name__ == "__main__":
    """
    Run the Flask development server locally.

    Notes
    -----
    - debug=True is convenient for development
    - host=127.0.0.1 keeps it local to your machine
    - port=5000 is Flask's common default for local testing
    """
    app.run(debug=True, host="127.0.0.1", port=5000)