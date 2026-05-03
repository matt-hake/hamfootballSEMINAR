"""
Client launcher for the football play-by-play parser.

This file is meant to become the Windows .exe entry point.

It assumes this repository structure:

hamfootballSEMINAR/
    config/
        schools.csv
        potential_errors.yaml
    data/
    src/
        app/
            api.py
            templates/
        errors.py
        ham_parser.py
        hamscrape.py
        llm_engine.py
        run_hamscrape.py
    .env
    requirements.txt

CITE: CHATGPT 5.2
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 5000


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
SRC_DIR = BASE_DIR / "src"
APP_DIR = SRC_DIR / "app"

# ============================================================
# FIX IMPORT PATHS
# ============================================================

# Add these directories so Python can find your modules
for path in [SRC_DIR, APP_DIR]:
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# Now this works
from api import app  # noqa: E402


def open_browser() -> None:
    """Open the local browser interface."""
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    threading.Timer(1.5, open_browser).start()

    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False,
    )