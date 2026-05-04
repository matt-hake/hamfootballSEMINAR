from __future__ import annotations

import sys
import threading
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 5000


def get_base_dir() -> Path:
    """
    Find the folder containing the bundled src/ directory.

    In development:
        hamfootballSEMINAR/

    In PyInstaller:
        usually FootballParserApp/_internal/
    """
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent

        possible_dirs = [
            exe_dir,
            exe_dir / "_internal",
            Path(getattr(sys, "_MEIPASS", exe_dir)).resolve(),
        ]

        for folder in possible_dirs:
            if (folder / "src" / "app" / "api.py").exists():
                return folder

        raise RuntimeError(
            "Could not find src/app/api.py. Checked:\n"
            + "\n".join(str(folder) for folder in possible_dirs)
        )

    return Path(__file__).resolve().parent


BASE_DIR = get_base_dir()
SRC_DIR = BASE_DIR / "src"

# Add src/ to Python's import path so this works:
#   from app.api import app
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

(BASE_DIR / "data").mkdir(exist_ok=True)
(BASE_DIR / "data" / "temp").mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data" / "exports").mkdir(parents=True, exist_ok=True)

from app.api import app  # noqa: E402


def open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


if __name__ == "__main__":
    try:
        threading.Timer(1.5, open_browser).start()

        app.run(
            host=HOST,
            port=PORT,
            debug=False,
            use_reloader=False,
        )

    except Exception as error:
        print("\nThe app crashed with this error:")
        print(error)
        input("\nPress Enter to close...")