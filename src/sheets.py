"""
sheets.py — LOAD layer.
Writes each output DataFrame to its own worksheet tab in a Google Sheet.
"""

import logging
import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from src.config import GOOGLE_SERVICE_ACCOUNT_FILE

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(str(GOOGLE_SERVICE_ACCOUNT_FILE), scopes=SCOPES)
    return gspread.authorize(creds)


def load(context: dict[str, pd.DataFrame], sheets_config: list[dict], google_sheet_id: str) -> list[dict]:
    """
    Write each entry in sheets_config to its own worksheet tab.
    sheets_config: [{"name": "Tab Name", "input": "context_key"}, ...]
    """
    client = _get_client()
    spreadsheet = client.open_by_key(google_sheet_id)
    results = []

    for sheet_cfg in sheets_config:
        df       = context[sheet_cfg["input"]]
        tab_name = sheet_cfg["name"]
        result   = _write_tab(spreadsheet, df, tab_name)
        results.append(result)
        logger.info("  Wrote '%s': %d rows × %d columns", tab_name, result["rows_written"], result["columns"])

    return results


def _normalize_tab_name(tab_name: str) -> str:
    """Normalize worksheet titles so reuse checks survive whitespace/case mismatches."""
    return (tab_name or "").strip()


def _find_existing_worksheet(spreadsheet, tab_name: str):
    """Find an existing worksheet by exact or normalized title."""
    normalized = _normalize_tab_name(tab_name)
    if not normalized:
        return None

    worksheets = spreadsheet.worksheets()

    for ws in worksheets:
        if ws.title == normalized:
            return ws

    lowered = normalized.casefold()
    for ws in worksheets:
        if ws.title.strip().casefold() == lowered:
            return ws

    return None


def _get_or_create_worksheet(spreadsheet, df: pd.DataFrame, tab_name: str):
    normalized = _normalize_tab_name(tab_name)
    if not normalized:
        raise ValueError("Worksheet tab name cannot be empty.")

    existing = _find_existing_worksheet(spreadsheet, normalized)
    if existing is not None:
        return existing, normalized, False

    try:
        ws = spreadsheet.add_worksheet(
            title=normalized,
            rows=max(len(df) + 50, 200),
            cols=max(len(df.columns) + 5, 26),
        )
        logger.info("  Created new tab '%s'.", normalized)
        return ws, normalized, True
    except APIError as exc:
        # Google sometimes rejects addSheet because the tab already exists even
        # though the immediate lookup did not find it yet. Re-fetch and reuse it.
        message = str(exc).lower()
        if "already exists" in message:
            existing = _find_existing_worksheet(spreadsheet, normalized)
            if existing is not None:
                logger.info("  Reusing existing tab '%s'.", existing.title)
                return existing, existing.title, False
        raise


def _write_tab(spreadsheet, df: pd.DataFrame, tab_name: str) -> dict:
    ws, resolved_tab_name, _ = _get_or_create_worksheet(spreadsheet, df, tab_name)

    ws.clear()

    rows = [df.columns.tolist()]
    for _, row in df.iterrows():
        rows.append([_serialize(v) for v in row.tolist()])

    if rows:
        cell_range = f"A1:{gspread.utils.rowcol_to_a1(len(rows), len(rows[0]))}"
        ws.update(cell_range, rows)

    return {
        "tab":          resolved_tab_name,
        "rows_written": len(df),
        "columns":      len(df.columns),
        "sheet_url":    f"https://docs.google.com/spreadsheets/d/{spreadsheet.id}",
    }


def _serialize(value) -> str | int | float | None:
    if pd.isna(value):
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value)
