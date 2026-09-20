"""
main.py — Entry point for running any sales report.

HOW IT WORKS (ETL pipeline)
----------------------------
1. EXTRACT   — Connect to the database and run SQL queries.
               Each query result is saved as a named table in memory.

2. TRANSFORM — Apply operations on those tables (group, rename,
               filter, calculate new columns, pivot, etc.)
               Each step reads one table and writes a new one.

3. LOAD      — Write the final tables to Google Sheets.
               Each table becomes its own tab in the sheet.

HOW TO RUN
----------
    python -m src.main                    # shows a menu to pick a report
    python -m src.main daywise_sales      # run a specific report by name
    python -m src.main --list             # see all available reports
"""

import argparse
import sys
from pathlib import Path

from src.config import load_request, list_reports, PROJECT_ROOT
from src.db import extract
from src.transform import transform
from src.sheets import load


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str = ""):
    """Simple print-based logger so output is easy to read."""
    print(msg)


def section(title: str):
    """Print a visible section header."""
    log()
    log("=" * 55)
    log(f"  {title}")
    log("=" * 55)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(request_path: Path):
    """
    Run the full ETL pipeline for a given report JSON file.

    Step 1 — Read the report config (what to query, how to shape it, where to write)
    Step 2 — EXTRACT:   run SQL → get raw data from the database
    Step 3 — TRANSFORM: reshape the data (group, rename, filter, etc.)
    Step 4 — LOAD:      write the final data to Google Sheets
    """

    # ── Step 1: Read the report config ───────────────────────────────────────
    config         = load_request(request_path)
    report_name    = config.get("report_name", request_path.stem)
    reference_date = config.get("reference_date", "")

    log()
    log(f"  Report : {report_name}")
    if reference_date:
        log(f"  Date   : {reference_date}")


    # ── Step 2: EXTRACT — pull data from the database ────────────────────────
    section("STEP 1 — EXTRACT  (database → memory)")
    log("  Reading data from the database ...")

    queries = config["extract"]["queries"]
    for q in queries:
        log(f"  • Query: {q['name']}")

    raw_data = extract(
        db_profile     = config["extract"]["db_profile"],
        queries        = queries,
        reference_date = reference_date,
    )

    log()
    log("  Done. Tables now in memory:")
    for name, df in raw_data.items():
        log(f"  • {name}  →  {len(df)} rows × {len(df.columns)} columns")


    # ── Step 3: TRANSFORM — reshape the data ─────────────────────────────────
    steps = config.get("transform", [])

    section("STEP 2 — TRANSFORM  (reshape the data)")

    if not steps:
        log("  No transform steps defined — data used as-is.")
        shaped_data = raw_data
    else:
        log(f"  Applying {len(steps)} step(s) ...")
        for step in steps:
            log(f"  • {step['name']}  ({len(step.get('operations', []))} operations)")

        original_keys = set(raw_data.keys())
        shaped_data   = transform(raw_data, steps)

        log()
        log("  Done. Output tables:")
        for name, df in shaped_data.items():
            if name not in original_keys:
                log(f"  • {name}  →  {len(df)} rows × {len(df.columns)} columns")


    # ── Step 4: LOAD — write to Google Sheets ────────────────────────────────
    sheets_to_write = config["load"]["sheets"]

    section("STEP 3 — LOAD  (memory → Google Sheets)")
    log(f"  Writing {len(sheets_to_write)} sheet(s) ...")
    for s in sheets_to_write:
        log(f"  • Tab '{s['name']}'  ←  {s['input']}")

    results = load(
        context         = shaped_data,
        sheets_config   = sheets_to_write,
        google_sheet_id = config["load"]["google_sheet_id"],
    )


    # ── Done ──────────────────────────────────────────────────────────────────
    section("DONE")
    for r in results:
        log(f"  ✅  Tab '{r['tab']}'  —  {r['rows_written']} rows written")
    log()
    log(f"  Open your sheet: {results[0]['sheet_url']}")
    log()


# ── CLI ───────────────────────────────────────────────────────────────────────

def pick_from_menu() -> Path:
    """Show a numbered list of available reports and let the user pick one."""
    reports = list_reports()
    if not reports:
        print("No reports found in the input/ folder.")
        sys.exit(1)

    print()
    print("  Available reports:")
    print()
    for i, report in enumerate(reports, start=1):
        print(f"    {i}.  {report.stem}")
    print()

    while True:
        choice = input("  Pick a number: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(reports):
            return reports[int(choice) - 1]
        print(f"  Please enter a number between 1 and {len(reports)}")


def find_report(name: str) -> Path:
    """Find a report by file path or by name inside the input/ folder."""
    # Maybe it's a direct path
    if Path(name).exists():
        return Path(name)
    # Maybe it's just the filename without .json
    candidate = PROJECT_ROOT / "input" / f"{name}.json"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        f"Report '{name}' not found.\n"
        f"Run with --list to see available reports."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run a sales report: pulls data from DB and writes to Google Sheets."
    )
    parser.add_argument(
        "report",
        nargs="?",
        help="Name of the report to run (e.g. daywise_sales). Leave blank for a menu.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Show all available reports and exit.",
    )
    args = parser.parse_args()

    # Show available reports and exit
    if args.list:
        print("\nAvailable reports:")
        for r in list_reports():
            print(f"  • {r.stem}")
        print()
        return

    # Resolve which report to run
    if args.report:
        path = find_report(args.report)
    else:
        path = pick_from_menu()

    # Run it
    try:
        run_pipeline(path)
    except FileNotFoundError as exc:
        print(f"\n  ERROR: {exc}\n")
        sys.exit(1)
    except Exception as exc:
        print(f"\n  ERROR: Pipeline failed — {exc}\n")
        raise


if __name__ == "__main__":
    main()
