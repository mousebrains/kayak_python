"""Application configuration (replaces Paths.C).

All settings are loaded from environment variables with sensible defaults.
Use a .env file for local development.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# Database
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{BASE_DIR / 'kayak.db'}",
)

# Flask
SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key")
DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"

# Data pipeline
FETCH_TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "300"))
FETCH_USER_AGENT = os.environ.get("FETCH_USER_AGENT", "kayak/1.0")

# Legacy settings (from Parameters table / Paths.C)
TEMPLATE_DIR = os.environ.get("TEMPLATE_DIR", str(BASE_DIR / "web.templates"))
DISPLAY_CGI = os.environ.get("DISPLAY_CGI", "cgi/display")

# Maintainer
MAINTAINER_EMAIL = "pat.kayak@gmail.com"
MAINTAINER_NAME = "Pat Welch"
SITE_URL = os.environ.get("SITE_URL", "http://levels.wkcc.org")
