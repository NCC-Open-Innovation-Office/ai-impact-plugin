"""
Minimal FastAPI server for the AI Impact Dashboard.

Serves dashboard.html at / and provides a /api/data endpoint that reads the
shared SQLite database and returns all usage records as JSON.

The database path is controlled by the AI_IMPACT_DB_PATH environment variable
(default: /data/ai_impact/usage.db).  In Docker Compose the open-webui and
ai-impact-dashboard containers share a named volume so both sides point at the
same SQLite file.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(docs_url=None, redoc_url=None)

DB_PATH = Path(os.environ.get("AI_IMPACT_DB_PATH", "/data/ai_impact/usage.db"))
_HTML_PATH = Path(__file__).parent / "dashboard.html"


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Serve the dashboard HTML."""
    return _HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/data")
async def get_data() -> JSONResponse:
    """Return all usage records from the SQLite database as JSON."""
    if not DB_PATH.exists():
        return JSONResponse({"records": [], "status": "no_data"})

    try:
        # Open read-only so we never accidentally corrupt the live DB
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT * FROM usage_records ORDER BY timestamp DESC"
        )
        records = [dict(row) for row in cur.fetchall()]
        conn.close()
        return JSONResponse({"records": records, "status": "ok"})
    except Exception as exc:
        return JSONResponse(
            {"records": [], "status": "error", "detail": str(exc)},
            status_code=500,
        )
