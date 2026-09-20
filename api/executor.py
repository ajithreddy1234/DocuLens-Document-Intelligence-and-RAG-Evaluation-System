"""
executor.py — Converts a node-based pipeline graph into execution steps
and runs them using the existing src/ ETL code.
"""

import re
from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Callable, Optional

import pandas as pd
from sqlalchemy import create_engine, text

from src.config import build_connection_uri, get_connection_uri
from src.transform import transform as apply_transform


# ── Helpers ───────────────────────────────────────────────────────────────────

def resolve_date(mode: str, custom: str = "") -> str:
    today = date.today()
    if mode == "today":
        return str(today)
    if mode == "yesterday":
        return str(today - timedelta(days=1))
    return custom or str(today)


def extract_sheet_id(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else url


def topo_sort(nodes: list, edges: list) -> list:
    """Return nodes in dependency order (Kahn's algorithm)."""
    node_ids = {n.id for n in nodes}
    by_id    = {n.id: n for n in nodes}
    in_deg   = {n.id: 0 for n in nodes}
    adj      = defaultdict(list)

    for e in edges:
        if e.source in node_ids and e.target in node_ids:
            adj[e.source].append(e.target)
            in_deg[e.target] += 1

    queue  = deque([n for n in nodes if in_deg[n.id] == 0])
    result = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for nid in adj[node.id]:
            in_deg[nid] -= 1
            if in_deg[nid] == 0:
                queue.append(by_id[nid])
    return result


def incoming_edges(node_id: str, edges: list) -> list:
    return [e for e in edges if e.target == node_id]


def build_node_connection_uri(node_data: dict) -> str:
    """Build a connection URI directly from a Credential node."""
    db_type = (node_data.get("db_type") or "postgres").strip().lower()
    default_port = "5439" if db_type == "redshift" else "5432"
    default_ssl  = "require" if db_type == "redshift" else ""

    settings = {
        "driver":   "postgresql+psycopg2",
        "host":     node_data.get("host", ""),
        "port":     node_data.get("port") or default_port,
        "database": node_data.get("database", ""),
        "user":     node_data.get("username", ""),
        "password": node_data.get("password", ""),
        "query": {
            "sslmode": node_data.get("sslmode") or default_ssl,
        },
    }
    return build_connection_uri(settings)


# ── Main executor ─────────────────────────────────────────────────────────────

def execute_up_to(
    graph,
    target_node_id: str,
    on_progress: Optional[Callable] = None,
    dry_run: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Execute all nodes up to and including target_node_id (in topological order).
    Returns context dict {node_id: DataFrame}.
    on_progress(node_id, status, rows, error) is called for each node.
    When dry_run=True, Load nodes are executed but skip the actual sheet write.
    """
    nodes_by_id = {n.id: n for n in graph.nodes}
    ordered     = topo_sort(graph.nodes, graph.edges)

    # Only go up to the target node
    try:
        cut = next(i for i, n in enumerate(ordered) if n.id == target_node_id)
        ordered = ordered[: cut + 1]
    except StopIteration:
        pass

    # Credential node — build a connection URI from the node's fields
    cred = next((n for n in graph.nodes if n.type == "credential"), None)
    if cred and cred.data.get("host") and cred.data.get("database") and cred.data.get("username"):
        db_uri = build_node_connection_uri(cred.data)
    else:
        profile = (cred.data.get("db_profile") or "local_postgres") if cred else "local_postgres"
        db_uri  = get_connection_uri(profile)

    context: dict[str, pd.DataFrame] = {}

    for node in ordered:
        if on_progress:
            on_progress(node.id, "running", 0, None)

        try:
            _execute_node(node, graph.edges, context, db_uri, dry_run=dry_run)
        except Exception as exc:
            if on_progress:
                on_progress(node.id, "error", 0, str(exc))
            raise

        rows = len(context.get(node.id, pd.DataFrame()))
        if on_progress:
            on_progress(node.id, "done", rows, None)

    return context


def _execute_node(node, edges: list, context: dict, db_uri: str, dry_run: bool = False):
    """Execute a single node and store result in context."""

    if node.type == "credential":
        return  # no-op

    elif node.type == "extract":
        d       = node.data
        ref     = resolve_date(d.get("reference_date_mode", "custom"), d.get("reference_date", ""))
        sql     = d.get("sql", "").replace("{reference_date}", ref)
        uri     = db_uri
        engine  = create_engine(uri, pool_pre_ping=True, connect_args={"connect_timeout": 15})
        try:
            with engine.connect() as conn:
                df = pd.read_sql(text(sql), conn)
        finally:
            engine.dispose()
        context[node.id] = df

    elif node.type == "transform":
        in_edges  = incoming_edges(node.id, edges)
        if not in_edges:
            return

        d         = node.data
        operation = dict(d.get("operation") or {})
        op_type   = operation.get("type", "")

        if len(in_edges) >= 2 and op_type in ("merge", "concat"):
            # Split into left / right by targetHandle
            left_key  = next((e.source for e in in_edges if e.targetHandle != "right"), in_edges[0].source)
            right_key = next((e.source for e in in_edges if e.targetHandle == "right"), in_edges[-1].source)
            df = context.get(left_key, pd.DataFrame()).copy()
            operation["right_input"] = right_key
        else:
            left_key = in_edges[0].source
            df = context.get(left_key, pd.DataFrame()).copy()

        if not operation:
            context[node.id] = df
            return

        # Apply through existing transform layer
        mini  = dict(context)
        mini["__in__"] = df
        step  = {
            "name":       d.get("label", "transform"),
            "input":      "__in__",
            "output":     node.id,
            "operations": [operation],
        }
        result = apply_transform(mini, [step])
        context[node.id] = result[node.id]

    elif node.type == "load":
        in_edges = incoming_edges(node.id, edges)
        if not in_edges:
            return
        input_key = in_edges[0].source
        df        = context.get(input_key, pd.DataFrame())
        d         = node.data
        sheet_id  = extract_sheet_id(d.get("google_sheet_url", ""))
        tab       = d.get("tab_name", "Sheet1")

        if not dry_run:
            from src.sheets import load as write_sheets
            write_sheets(
                context         = {input_key: df},
                sheets_config   = [{"name": tab, "input": input_key}],
                google_sheet_id = sheet_id,
            )
        context[node.id] = df  # always keep for preview
