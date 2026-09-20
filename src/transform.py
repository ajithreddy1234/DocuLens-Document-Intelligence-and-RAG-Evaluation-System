"""
transform.py — TRANSFORM layer.

Each step reads from context[input], applies a list of operations in order,
and writes the result to context[output].

Supported operations
--------------------
rename          – rename columns via a mapping dict
select_columns  – keep only the listed columns
drop_columns    – remove listed columns
sort            – sort rows by one or more columns
calculate       – add a new column from a pandas expression
filter          – keep rows matching conditions (==, !=, >, >=, <, <=, in, not in)
fillna          – fill nulls with a value (all columns or specific ones)
replace         – replace a value in a column or whole DataFrame
map             – replace column values via a lookup dict
abs             – take absolute value of columns
astype          – cast a column to a different dtype
drop_duplicates – remove duplicate rows (optional subset)
groupby         – group and aggregate
pivot           – create a pivot table
merge           – join with another DataFrame already in context
concat          – stack another DataFrame (from context) below current one
format_date     – convert date columns to a string format
add_total_column – sum all numeric columns row-wise into a new column
rank            – rank rows by a column (optionally within groups)
cumsum          – running cumulative sum of a column (optionally after sorting)
pct_of_total    – express a column as % of its total (optionally within groups)
date_extract    – pull day_name / week / month / year / quarter out of a date column
"""

import logging
from typing import Any
import pandas as pd

logger = logging.getLogger(__name__)


# ── Public entry point ────────────────────────────────────────────────────────

def transform(context: dict[str, pd.DataFrame], steps: list[dict]) -> dict[str, pd.DataFrame]:
    """
    Apply each step in order. Each step reads from context[input] and
    writes the result to context[output]. Returns the updated context.
    """
    for step in steps:
        input_key  = step["input"]
        output_key = step["output"]

        if input_key not in context:
            raise KeyError(f"Step '{step['name']}': input '{input_key}' not found. "
                           f"Available: {list(context.keys())}")

        df = context[input_key].copy()
        logger.info("  Step '%s': %s → %s", step["name"], input_key, output_key)

        for op in step.get("operations", []):
            df = _apply(df, op, context)

        context[output_key] = df
        logger.info("    → %d rows × %d columns", len(df), len(df.columns))

    return context


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _apply(df: pd.DataFrame, op: dict[str, Any], context: dict) -> pd.DataFrame:
    op_type = op["type"]

    # Context-aware operations need the full context dict
    if op_type == "merge":
        return _merge(df, op, context)
    if op_type == "concat":
        return _concat(df, op, context)

    handlers = {
        "rename":            _rename,
        "select_columns":    _select_columns,
        "drop_columns":      _drop_columns,
        "sort":              _sort,
        "calculate":         _calculate,
        "filter":            _filter,
        "fillna":            _fillna,
        "replace":           _replace,
        "map":               _map,
        "abs":               _abs,
        "astype":            _astype,
        "drop_duplicates":   _drop_duplicates,
        "groupby":           _groupby,
        "pivot":             _pivot,
        "format_date":       _format_date,
        "add_total_column":  _add_total_column,
        "add_total_row":     _add_total_row,
        "rank":              _rank,
        "cumsum":            _cumsum,
        "pct_of_total":      _pct_of_total,
        "date_extract":      _date_extract,
    }

    handler = handlers.get(op_type)
    if not handler:
        raise ValueError(f"Unknown operation type: '{op_type}'. "
                         f"Supported: {sorted(handlers)}")
    return handler(df, op)


# ── Operation handlers ────────────────────────────────────────────────────────

def _rename(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    return df.rename(columns=op["mapping"])


def _select_columns(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    cols = [c for c in op["columns"] if c in df.columns]
    return df[cols]


def _drop_columns(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    return df.drop(columns=op["columns"], errors="ignore")


def _sort(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    by = op["by"] if isinstance(op["by"], list) else [op["by"]]
    return df.sort_values(by=by, ascending=op.get("ascending", True)).reset_index(drop=True)


def _calculate(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    # Decimal/object columns from Postgres break eval — coerce to numeric first
    for col in df.select_dtypes(include="object").columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            pass
    df[op["target"]] = df.eval(op["expression"])
    if "fillna" in op:
        df[op["target"]] = df[op["target"]].fillna(op["fillna"])
    return df


def _filter(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    # Accept both the UI single-condition format {column, operator, value}
    # and the JSON multi-condition format {conditions: [...]}
    if "conditions" in op:
        conditions = op["conditions"]
    else:
        raw_value = op.get("value", "")
        conditions = [{"column": op["column"], "operator": op["operator"], "value": raw_value}]

    for cond in conditions:
        col      = cond["column"]
        operator = cond["operator"]
        value    = cond["value"]

        # For 'in' / 'not in': accept comma-separated string or list
        if operator in ("in", "not in") and isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]

        # Coerce value type to match the column
        if operator not in ("in", "not in") and not isinstance(value, (list,)):
            try:
                value = type(df[col].dropna().iloc[0])(value) if not df[col].dropna().empty else value
            except (ValueError, TypeError, KeyError):
                pass

        if   operator == "==":     df = df[df[col] == value]
        elif operator == "!=":     df = df[df[col] != value]
        elif operator == ">":      df = df[df[col] > value]
        elif operator == ">=":     df = df[df[col] >= value]
        elif operator == "<":      df = df[df[col] < value]
        elif operator == "<=":     df = df[df[col] <= value]
        elif operator == "in":     df = df[df[col].isin(value)]
        elif operator == "not in": df = df[~df[col].isin(value)]
        else:
            raise ValueError(f"Unknown filter operator: '{operator}'")
    return df.reset_index(drop=True)


def _fillna(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    cols = op.get("columns")
    val  = op.get("value", 0)
    if cols:
        df[cols] = df[cols].fillna(val)
    else:
        df = df.fillna(val)
    return df


def _replace(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    col = op.get("column")
    if col:
        df[col] = df[col].replace(op["to_replace"], op["value"])
    else:
        df = df.replace(op["to_replace"], op["value"])
    return df


def _map(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    col = op["column"]
    df[col] = df[col].map(op["mapping"]).fillna(df[col])
    return df


def _abs(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    for col in op["columns"]:
        if col in df.columns:
            df[col] = df[col].abs()
    return df


def _astype(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    col = op["column"]
    if col in df.columns:
        df[col] = df[col].astype(op["dtype"])
    return df


def _drop_duplicates(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    return df.drop_duplicates(subset=op.get("subset")).reset_index(drop=True)


def _groupby(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    return df.groupby(op["group_by"], as_index=False).agg(op["aggregations"])


def _pivot(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    pivot_df = df.pivot_table(
        index=op["index"],
        columns=op.get("columns"),
        values=op["values"],
        aggfunc=op.get("aggfunc", "sum"),
        fill_value=op.get("fill_value", 0),
    ).reset_index()

    pivot_df.columns = [str(c) for c in pivot_df.columns]

    if op.get("add_total_row"):
        totals = pivot_df.select_dtypes(include="number").sum()
        total_row = {col: "" for col in pivot_df.columns}
        total_row.update(totals.to_dict())
        total_row[op["index"][0]] = op.get("total_row_label", "Total")
        pivot_df = pd.concat([pivot_df, pd.DataFrame([total_row])], ignore_index=True)

    return pivot_df


def _merge(df: pd.DataFrame, op: dict, context: dict) -> pd.DataFrame:
    right_key = op["right_input"]
    if right_key not in context:
        raise KeyError(f"Merge: right_input '{right_key}' not found in context.")
    return df.merge(
        context[right_key],
        left_on=op["left_on"],
        right_on=op["right_on"],
        how=op.get("how", "left"),
    )


def _concat(df: pd.DataFrame, op: dict, context: dict) -> pd.DataFrame:
    """Stack another DataFrame (from context) below the current one."""
    right_key = op["right_input"]
    if right_key not in context:
        raise KeyError(f"Concat: right_input '{right_key}' not found in context.")
    return pd.concat([df, context[right_key]], ignore_index=True)


def _format_date(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    fmt = op.get("format", "%Y-%m-%d")
    for col in op["columns"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col]).dt.strftime(fmt)
    return df


def _add_total_column(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    label   = op.get("label", "Total")
    exclude = set(op.get("exclude_columns", []))
    numeric_cols = [c for c in df.select_dtypes(include="number").columns if c not in exclude]
    df[label] = df[numeric_cols].sum(axis=1)
    return df


def _add_total_row(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    """
    Append a summary row at the bottom with column sums.

    op keys:
      label_column – which column gets the label text (e.g. "franchise")
      label        – text to put in the label column (default "Total")
      columns      – list of columns to sum; empty = all numeric columns
    """
    label_col    = op.get("label_column")
    label        = op.get("label", "Total")
    cols_to_sum  = op.get("columns", [])

    all_numeric  = df.select_dtypes(include="number").columns.tolist()
    numeric_cols = [c for c in cols_to_sum if c in all_numeric] if cols_to_sum else all_numeric

    total_row = {}
    for col in df.columns:
        if col in numeric_cols:
            total_row[col] = df[col].sum()
        elif col == label_col:
            total_row[col] = label
        else:
            total_row[col] = ""

    return pd.concat([df, pd.DataFrame([total_row])], ignore_index=True)


def _rank(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    """
    Rank rows by a column. Optionally rank within groups (group_by).

    op keys:
      rank_by       – column to rank on
      ascending     – False = rank 1 = highest (default False)
      group_by      – list of columns; if given, rank resets per group
      output_column – name for the new rank column (default "Rank")
      method        – ties method: 'min' | 'max' | 'average' | 'first' (default 'min')
    """
    col    = op["rank_by"]
    asc    = op.get("ascending", False)
    out    = op.get("output_column", "Rank")
    method = op.get("method", "min")
    groups = op.get("group_by")

    if groups:
        df[out] = df.groupby(groups)[col].rank(ascending=asc, method=method).astype(int)
    else:
        df[out] = df[col].rank(ascending=asc, method=method).astype(int)

    return df


def _cumsum(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    """
    Running cumulative sum of a column, optionally after sorting first.

    op keys:
      column        – column to accumulate
      output_column – name for the result (default "<column> Cumulative")
      sort_by       – column to sort by before accumulating (optional)
      ascending     – sort direction (default True)
    """
    col    = op["column"]
    out    = op.get("output_column", f"{col} Cumulative")
    sort   = op.get("sort_by")
    asc    = op.get("ascending", True)

    if sort:
        df = df.sort_values(sort, ascending=asc).reset_index(drop=True)

    df[out] = df[col].cumsum()
    return df


def _pct_of_total(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    """
    Express a numeric column as a percentage of its total.
    If group_by is given, percentages are computed within each group.

    op keys:
      column        – column whose share to compute
      output_column – name for the % column
      group_by      – list of columns; if given, % is within-group
      decimals      – decimal places to round to (default 1)
    """
    col      = op["column"]
    out      = op.get("output_column", f"{col} %")
    groups   = op.get("group_by")
    decimals = op.get("decimals", 1)

    if groups:
        df[out] = df.groupby(groups)[col].transform(lambda s: s / s.sum() * 100)
    else:
        df[out] = df[col] / df[col].sum() * 100

    df[out] = df[out].round(decimals)
    return df


def _date_extract(df: pd.DataFrame, op: dict) -> pd.DataFrame:
    """
    Extract calendar parts from a date/datetime column into new columns.

    op keys:
      column         – source date column
      parts          – list of parts to extract; each must be one of:
                         year | month | day | day_name | week | quarter | hour | minute
      output_columns – list of names for the new columns (same length as parts)

    Example:
      parts: ["day_name", "week", "month"]
      output_columns: ["Day", "Week No", "Month"]
    """
    col     = op["column"]
    parts   = op["parts"]
    out_cols = op["output_columns"]

    if len(parts) != len(out_cols):
        raise ValueError("date_extract: 'parts' and 'output_columns' must be the same length.")

    dt = pd.to_datetime(df[col])

    extractors = {
        "year":     dt.dt.year,
        "month":    dt.dt.month,
        "day":      dt.dt.day,
        "day_name": dt.dt.day_name(),
        "week":     dt.dt.isocalendar().week.astype(int),
        "quarter":  dt.dt.quarter,
        "hour":     dt.dt.hour,
        "minute":   dt.dt.minute,
    }

    for part, out in zip(parts, out_cols):
        if part not in extractors:
            raise ValueError(f"date_extract: unknown part '{part}'. Use: {list(extractors)}")
        df[out] = extractors[part]

    return df
