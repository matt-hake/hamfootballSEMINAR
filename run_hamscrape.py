#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from hamscrape import run_download

#CITE: CHATGPT 5.2


# ------------------------------------------------------------
# Main program entry point
# ------------------------------------------------------------
# This function collects command line arguments and executes
# the download job using the parameters supplied by the user.
def main() -> int:

    # Create the argument parser that will read command line options
    ap = argparse.ArgumentParser()

    # CITE: CHATGPT 5.2
    # Argument specifying where downloaded PDFs should be saved
    #takes file path of current file, resolve gives abs canon. path (workable)
    #parents[1] gives us two steps back which is what we want
    BASE_DIR = Path(__file__).resolve().parents[1]
    #assembling directory
    DEFAULT_OUT = BASE_DIR / "data" / "nescac_2025_pdfs"

    # tells us where files will end up
    ap.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output directory"
    )

    # Optional flag for verbose output (more detailed logging)
    ap.add_argument(
        "--verbose",
        action="store_true"
    )

    # Optional argument to limit how many schools are scraped.
    # Useful for testing so you don't scrape the entire conference.
    ap.add_argument(
        "--limit_schools",
        type=int,
        default=0
    )

    # Optional argument to limit how many boxscore links are processed.
    # Also useful for testing/debugging.
    ap.add_argument(
        "--limit_links",
        type=int,
        default=0
    )

    # Optional allowlist of specific schools to scrape.
    # Example usage:
    #   --schools hamilton williams amherst
    ap.add_argument(
        "--schools",
        nargs="*",
        default=None
    )

    # Parse the arguments supplied on the command line
    args = ap.parse_args()

    # ------------------------------------------------------------
    # Run the scraping / download job
    # ------------------------------------------------------------
    # Calls the main function from hamscrape which:
    # 1. Scrapes team schedules
    # 2. Finds boxscore pages
    # 3. Downloads play-by-play PDFs
    # 4. Returns a list of result objects describing what happened
    results = run_download(
        out_dir=args.out,
        verbose=bool(args.verbose),
        limit_schools=int(args.limit_schools or 0),
        limit_links=int(args.limit_links or 0),
        schools_allowlist=args.schools,
    )

    # ------------------------------------------------------------
    # Post-processing: summarize results
    # ------------------------------------------------------------

    # Count how many files were newly downloaded
    downloaded = sum(1 for r in results if r.status == "DOWNLOADED")

    # Count files that already existed and were skipped
    existing = sum(1 for r in results if r.status == "EXISTS")

    # Count duplicates that were detected and skipped
    duplicates = sum(1 for r in results if r.status == "DUPLICATE")

    # ------------------------------------------------------------
    # Print a summary report to the terminal
    # ------------------------------------------------------------

    # Print the absolute path of the output directory
    print(f"\nOutput directory: {Path(args.out).resolve()}")

    # Print statistics about the scraping job
    print(f"Downloaded: {downloaded}")
    print(f"Already existed: {existing}")
    print(f"Duplicates skipped: {duplicates}")
    print(f"Total processed: {len(results)}")

    # Return success exit code
    return 0


# ------------------------------------------------------------
# Script execution guard
# ------------------------------------------------------------
# Ensures that main() only runs if this file is executed directly
# (not if it is imported as a module in another Python file).
if __name__ == "__main__":
    raise SystemExit(main())