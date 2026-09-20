"""
config.py — Database profiles, Google Sheets path, report loader and lister.
"""

import json
import os
from pathlib import Path
from urllib.parse import quote_plus, urlencode
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Add new database profiles here (e.g. redshift, bigquery proxy).
DB_PROFILES: dict[str, dict] = {
    "local_postgres": {
        "driver":   "postgresql+psycopg2",
        "host":     os.getenv("LOCAL_PG_HOST",     "localhost"),
        "port":     os.getenv("LOCAL_PG_PORT",     "5432"),
        "database": os.getenv("LOCAL_PG_DATABASE", "sales_db"),
        "user":     os.getenv("LOCAL_PG_USER",     "sales_user"),
        "password": os.getenv("LOCAL_PG_PASSWORD", "sales_password"),
        "query": {
            "sslmode": os.getenv("LOCAL_PG_SSLMODE", ""),
        },
    },
    "redshift": {
        "driver":   "postgresql+psycopg2",
        "host":     os.getenv("REDSHIFT_HOST",     ""),
        "port":     os.getenv("REDSHIFT_PORT",     "5439"),
        "database": os.getenv("REDSHIFT_DATABASE", ""),
        "user":     os.getenv("REDSHIFT_USER",     ""),
        "password": os.getenv("REDSHIFT_PASSWORD", ""),
        "query": {
            "sslmode": os.getenv("REDSHIFT_SSLMODE", "require"),
        },
    },
}

GOOGLE_SERVICE_ACCOUNT_FILE = PROJECT_ROOT / os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/Sheet_Service.json"
)


def get_connection_uri(profile_name: str) -> str:
    if profile_name not in DB_PROFILES:
        raise ValueError(f"Unknown DB profile '{profile_name}'. Available: {list(DB_PROFILES.keys())}")
    return build_connection_uri(DB_PROFILES[profile_name])


def build_connection_uri(settings: dict) -> str:
    """Build a SQLAlchemy connection URI from a DB settings dict."""
    driver   = settings["driver"]
    user     = quote_plus(str(settings.get("user", "")))
    password = quote_plus(str(settings.get("password", "")))
    host     = settings.get("host", "")
    port     = settings.get("port", "")
    database = settings.get("database", "")
    query    = {k: v for k, v in (settings.get("query") or {}).items() if v not in (None, "")}

    uri = f"{driver}://{user}:{password}@{host}:{port}/{database}"
    if query:
        uri = f"{uri}?{urlencode(query)}"
    return uri


def list_reports() -> list[Path]:
    """Return all .json files in the input/ folder, sorted by name."""
    return sorted((PROJECT_ROOT / "input").glob("*.json"))


def load_request(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Report not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)
