"""Atalho para iniciar o dashboard a partir da raiz."""
import sys
from pathlib import Path
import runpy

ROOT_DIR = Path(__file__).resolve().parent
for p in [ROOT_DIR, ROOT_DIR / "core", ROOT_DIR / "strategies", ROOT_DIR / "dashboard"]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

if __name__ == "__main__":
    app_path = ROOT_DIR / "dashboard" / "app.py"
    runpy.run_path(str(app_path), run_name="__main__")
