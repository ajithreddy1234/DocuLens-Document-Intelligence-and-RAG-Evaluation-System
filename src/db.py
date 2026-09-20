"""
db.py — EXTRACT layer.
Runs all queries defined in extract.queries and returns {name: DataFrame}.
{reference_date} in any query string is substituted with the provided date.
"""

import logging
import pandas as pd
from sqlalchemy import create_engine, text
from src.config import get_connection_uri

logger = logging.getLogger(__name__)


def extract(db_profile: str, queries: list[dict], reference_date: str = "") -> dict[str, pd.DataFrame]:
    uri = get_connection_uri(db_profile)
    engine = create_engine(uri, pool_pre_ping=True, connect_args={"connect_timeout": 15})
    context: dict[str, pd.DataFrame] = {}

    try:
        with engine.connect() as conn:
            for q in queries:
                name = q["name"]
                sql = q["query"].replace("{reference_date}", reference_date)
                logger.info("  Fetching '%s' ...", name)
                df = pd.read_sql(text(sql), conn)
                logger.info("  → %d rows × %d columns", len(df), len(df.columns))
                context[name] = df
    except Exception as exc:
        logger.error("Database error: %s", exc)
        raise RuntimeError(f"Query failed: {exc}") from exc
    finally:
        engine.dispose()

    return context
