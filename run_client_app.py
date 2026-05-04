from __future__ import annotations

import importlib.util
import sys
import threading
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 5000


def get_base_dir() -> Path:
    """
    Find the folder containing bundled project files.

    In normal development:
        hamfootballSEMINAR/

    In PyInstaller:
        usually FootballParserApp/_internal/
    """
    possible_dirs = []

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        possible_dirs.extend(
            [
                exe_dir,
                exe_dir / "_internal",
                Path(getattr(sys, "_MEIPASS", exe_dir)).resolve(),
            ]
        )
    else:
        possible_dirs.append(Path(__file__).resolve().parent)

    for folder in possible_dirs:
        if (folder / "src" / "app" / "api.py").exists():
            return folder

    raise RuntimeError(
        "Could not find src/app/api.py. Checked:\n"
        + "\n".join(str(folder) for folder in possible_dirs)
    )


BASE_DIR = get_base_dir()
SRC_DIR = BASE_DIR / "src"
APP_DIR = SRC_DIR / "app"

for path in [BASE_DIR, SRC_DIR, APP_DIR]:
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

(BASE_DIR / "data").mkdir(exist_ok=True)
(BASE_DIR / "data" / "temp").mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data" / "exports").mkdir(parents=True, exist_ok=True)


def load_flask_app():
    """
    Load src/app/api.py directly from its file path.

    This avoids the PyInstaller issue where `from api import app`
    sometimes fails even when the file exists.
    """
    api_path = APP_DIR / "api.py"

    spec = importlib.util.spec_from_file_location("api", api_path)

    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load api.py from {api_path}")

    api_module = importlib.util.module_from_spec(spec)
    sys.modules["api"] = api_module
    spec.loader.exec_module(api_module)

    return api_module.app


app = load_flask_app()


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