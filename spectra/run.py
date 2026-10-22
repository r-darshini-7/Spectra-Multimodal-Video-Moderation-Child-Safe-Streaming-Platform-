"""
Spectra Platform — Application Entry Point
"""
import os
import sys
import shutil

# ── Ensure the project root (parent of this package) is on sys.path ──────────
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))

_env_path     = os.path.join(_here, ".env")
_env_example  = os.path.join(_here, ".env.example")
if not os.path.exists(_env_path) and os.path.exists(_env_example):
    shutil.copy(_env_example, _env_path)
    print("[Spectra] Created .env from .env.example — edit it before production use.")

from spectra import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000, threaded=True)
