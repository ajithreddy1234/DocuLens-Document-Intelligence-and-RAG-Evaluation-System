"""
app.py — FastAPI backend for the ETL Pipeline Builder.

Routes:
  GET  /api/reports          — list saved reports in input/
  POST /api/preview          — run up to a node, return 10 rows + columns
  WS   /ws/run               — execute full pipeline, stream per-node progress
"""

import asyncio
import json
import threading

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api.executor import execute_up_to, topo_sort
from api.models import PipelineGraph, PreviewRequest
from src.config import list_reports, load_request

app = FastAPI(title="ETL Pipeline Builder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Reports ───────────────────────────────────────────────────────────────────

@app.get("/api/reports")
def get_reports():
    """Return all .json report files from input/."""
    out = []
    for path in list_reports():
        try:
            req = load_request(path)
            out.append({
                "name":        path.stem,
                "report_name": req.get("report_name", path.stem),
                "description": req.get("_what_it_does", [""])[0] if isinstance(req.get("_what_it_does"), list) else "",
            })
        except Exception:
            pass
    return out


# ── Preview ───────────────────────────────────────────────────────────────────

@app.post("/api/preview")
def preview_node(req: PreviewRequest):
    """
    Execute all nodes up to (and including) req.node_id.
    Returns first 10 rows, column names, and total row count.
    """
    try:
        ctx = execute_up_to(req.graph, req.node_id, dry_run=True)
        import pandas as pd
        df  = ctx.get(req.node_id)

        if df is None or df.empty:
            return {"columns": [], "rows": [], "total_rows": 0, "error": None}

        cols = df.columns.tolist()
        rows = _safe_rows(df.head(10))
        return {"columns": cols, "rows": rows, "total_rows": len(df), "error": None}

    except Exception as exc:
        return {"columns": [], "rows": [], "total_rows": 0, "error": str(exc)}


def _safe_rows(df) -> list[dict]:
    """Convert DataFrame rows to JSON-safe dicts."""
    import pandas as pd
    rows = []
    for _, row in df.iterrows():
        clean = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                clean[k] = v.isoformat()
            elif hasattr(v, "item"):          # numpy scalar
                clean[k] = v.item()
            elif v is None or (isinstance(v, float) and str(v) == "nan"):
                clean[k] = ""
            else:
                clean[k] = v
        rows.append(clean)
    return rows


# ── WebSocket run ─────────────────────────────────────────────────────────────

@app.websocket("/ws/run")
async def run_websocket(ws: WebSocket):
    """
    Stream per-node execution progress.
    Client sends:  JSON string of PipelineGraph
    Server sends:  { type: "progress", node_id, status, rows, error? }
                   { type: "complete" }
                   { type: "error", message }
    """
    await ws.accept()

    try:
        raw   = await ws.receive_text()
        graph = PipelineGraph(**json.loads(raw))

        # Find last node in topo order (skip credential)
        ordered     = topo_sort(graph.nodes, graph.edges)
        exec_nodes  = [n for n in ordered if n.type != "credential"]
        if not exec_nodes:
            await ws.send_json({"type": "complete"})
            return

        last_id = exec_nodes[-1].id

        # Thread-safe progress queue
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def on_progress(node_id, status, rows=0, error=None):
            msg = {"type": "progress", "node_id": node_id,
                   "status": status, "rows": rows}
            if error:
                msg["error"] = error
            asyncio.run_coroutine_threadsafe(queue.put(msg), loop)

        error_holder = {}

        def run():
            try:
                execute_up_to(graph, last_id, on_progress=on_progress)
            except Exception as exc:
                error_holder["msg"] = str(exc)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)  # sentinel

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

        # Stream messages until sentinel
        while True:
            msg = await queue.get()
            if msg is None:
                break
            await ws.send_json(msg)

        if "msg" in error_holder:
            await ws.send_json({"type": "error", "message": error_holder["msg"]})
        else:
            await ws.send_json({"type": "complete"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
