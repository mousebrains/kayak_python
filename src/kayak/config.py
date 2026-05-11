"""Application configuration.

All settings are loaded from environment variables with sensible defaults.
Use a .env file for local development.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

_config_env = Path.home() / ".config" / "kayak" / ".env"
load_dotenv(_config_env if _config_env.exists() else None)

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"

# Database
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"sqlite:///{(BASE_DIR / '../DB/kayak.db').resolve()}",
)

# Data pipeline
FETCH_TIMEOUT = int(os.environ.get("FETCH_TIMEOUT", "300"))
# Wall-clock budget for the fetch batch as a whole. URLs still in flight
# when the budget runs out are cancelled and surface as deadline-exceeded
# errors so the pipeline can move on to build/etc. instead of being killed
# by systemd's TimeoutStartSec.
FETCH_BUDGET = int(os.environ.get("FETCH_BUDGET", "240"))
FETCH_USER_AGENT = os.environ.get("FETCH_USER_AGENT", "kayak/1.0")

# Output
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", str(BASE_DIR / "public_html"))

# Maintainer
# Env override mirrors php/includes/auth.php::maintainer_emails(), which
# already reads MAINTAINER_EMAIL first. Without this both layers reach the
# same address only by coincidence — set MAINTAINER_EMAIL in the systemd
# unit's Environment= or /etc/kayak/secrets.env to override.
MAINTAINER_EMAIL = os.environ.get("MAINTAINER_EMAIL", "pat.kayak@gmail.com")
MAINTAINER_NAME = os.environ.get("MAINTAINER_NAME", "Pat Welch")
SITE_URL = os.environ.get("SITE_URL", "https://levels.wkcc.org")
