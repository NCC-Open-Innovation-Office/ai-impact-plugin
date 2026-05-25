"""
title: AI Impact Dashboard Tool
author: AI Impact Plugin
author_url: https://github.com/NCC-Open-Innovation-Office/ai-impact-plugin
git_url: https://github.com/NCC-Open-Innovation-Office/ai-impact-plugin.git
description: >
  Companion tool for the AI Environmental Impact Filter.  Exposes functions
  that an LLM can call to fetch usage statistics and render an HTML dashboard
  showing CO₂ emissions, water consumption, energy usage, and monetary cost
  across all recorded AI interactions.

required_open_webui_version: 0.3.17
requirements: pydantic>=2.0.0
version: 1.0.0
licence: MIT
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared database path default (must match the filter's default)
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = "~/.local/share/ai_impact/usage.db"


def _open_db(db_path: str) -> Optional[sqlite3.Connection]:
    path = Path(db_path).expanduser()
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


def _query_totals(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT
            COUNT(*)            AS total_queries,
            SUM(total_tokens)   AS total_tokens,
            SUM(energy_wh)      AS total_energy_wh,
            SUM(co2_g)          AS total_co2_g,
            SUM(water_ml)       AS total_water_ml,
            SUM(cost_usd)       AS total_cost_usd
        FROM usage_records
        """
    ).fetchone()
    if row is None:
        return {}
    return dict(row)


def _query_by_model(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            model,
            COUNT(*)            AS queries,
            SUM(total_tokens)   AS tokens,
            SUM(energy_wh)      AS energy_wh,
            SUM(co2_g)          AS co2_g,
            SUM(water_ml)       AS water_ml,
            SUM(cost_usd)       AS cost_usd
        FROM usage_records
        GROUP BY model
        ORDER BY co2_g DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _query_recent(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """
        SELECT timestamp, model, total_tokens, energy_wh, co2_g, water_ml, cost_usd
        FROM usage_records
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _query_daily_totals(conn: sqlite3.Connection, days: int = 30) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            DATE(timestamp) AS day,
            COUNT(*)        AS queries,
            SUM(co2_g)      AS co2_g,
            SUM(water_ml)   AS water_ml,
            SUM(cost_usd)   AS cost_usd
        FROM usage_records
        WHERE timestamp >= DATE('now', ?)
        GROUP BY DATE(timestamp)
        ORDER BY day ASC
        """,
        (f"-{days} days",),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Dashboard HTML generator
# ---------------------------------------------------------------------------


def _render_dashboard_html(
    totals: dict,
    by_model: list[dict],
    recent: list[dict],
    daily: list[dict],
) -> str:
    """Return a complete, self-contained HTML page for the dashboard."""

    # Prepare chart data
    daily_labels = json.dumps([r["day"] for r in daily])
    daily_co2 = json.dumps([round(r["co2_g"] * 1000, 4) for r in daily])   # mg
    daily_water = json.dumps([round(r["water_ml"], 4) for r in daily])
    daily_cost = json.dumps([round(r["cost_usd"], 6) for r in daily])

    model_labels = json.dumps([r["model"] for r in by_model])
    model_co2 = json.dumps([round(r["co2_g"] * 1000, 4) for r in by_model])
    model_cost = json.dumps([round(r["cost_usd"], 6) for r in by_model])

    # Summary numbers
    tq = int(totals.get("total_queries") or 0)
    tt = int(totals.get("total_tokens") or 0)
    te = round(float(totals.get("total_energy_wh") or 0) * 1000, 4)   # mWh
    tc = round(float(totals.get("total_co2_g") or 0) * 1000, 4)       # mg
    tw = round(float(totals.get("total_water_ml") or 0), 4)
    tx = round(float(totals.get("total_cost_usd") or 0), 6)

    model_rows_html = "".join(
        f"<tr>"
        f"<td>{r['model']}</td>"
        f"<td>{int(r['queries'])}</td>"
        f"<td>{int(r['tokens'])}</td>"
        f"<td>{round(r['energy_wh']*1000, 4)} mWh</td>"
        f"<td>{round(r['co2_g']*1000, 4)} mg</td>"
        f"<td>{round(r['water_ml'], 4)} mL</td>"
        f"<td>${round(r['cost_usd'], 6)}</td>"
        f"</tr>"
        for r in by_model
    )

    recent_rows_html = "".join(
        f"<tr>"
        f"<td>{r['timestamp'][:19]}</td>"
        f"<td>{r['model']}</td>"
        f"<td>{int(r['total_tokens'])}</td>"
        f"<td>{round(r['co2_g']*1000, 4)} mg</td>"
        f"<td>{round(r['water_ml'], 4)} mL</td>"
        f"<td>${round(r['cost_usd'], 6)}</td>"
        f"</tr>"
        for r in recent
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Environmental Impact Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --bg: #0f1117; --card: #1a1d27; --border: #2d3148;
    --text: #e2e8f0; --muted: #94a3b8;
    --green: #22c55e; --blue: #3b82f6; --amber: #f59e0b; --red: #ef4444;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: system-ui,sans-serif; padding: 1.5rem; }}
  h1 {{ font-size: 1.6rem; font-weight: 700; margin-bottom: .25rem; }}
  .subtitle {{ color: var(--muted); font-size: .85rem; margin-bottom: 1.5rem; }}
  .subtitle a {{ color: var(--blue); text-decoration: none; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }}
  .card {{ background: var(--card); border: 1px solid var(--border); border-radius: .75rem; padding: 1rem; }}
  .card-label {{ font-size: .75rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }}
  .card-value {{ font-size: 1.5rem; font-weight: 700; margin: .25rem 0; }}
  .card-unit {{ font-size: .75rem; color: var(--muted); }}
  .green {{ color: var(--green); }} .blue {{ color: var(--blue); }}
  .amber {{ color: var(--amber); }} .red {{ color: var(--red); }}
  .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; margin-bottom: 1.5rem; }}
  .chart-card {{ background: var(--card); border: 1px solid var(--border); border-radius: .75rem; padding: 1rem; }}
  .chart-card h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: .75rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
  th {{ text-align: left; padding: .5rem .75rem; color: var(--muted); font-weight: 600;
        border-bottom: 1px solid var(--border); }}
  td {{ padding: .5rem .75rem; border-bottom: 1px solid var(--border); }}
  tr:hover td {{ background: #ffffff08; }}
  .section {{ background: var(--card); border: 1px solid var(--border); border-radius: .75rem;
               padding: 1rem; margin-bottom: 1rem; }}
  .section h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: .75rem; }}
  .refs {{ font-size: .75rem; color: var(--muted); line-height: 1.6; margin-top: 1rem; }}
  .refs a {{ color: var(--blue); text-decoration: none; }}
</style>
</head>
<body>
<h1>🌱 AI Environmental Impact Dashboard</h1>
<p class="subtitle">
  All-time usage across every AI model tracked by the
  <a href="https://github.com/NCC-Open-Innovation-Office/ai-impact-plugin">AI Impact Filter</a>.
  Methodology: energy from
  <a href="https://arxiv.org/abs/2311.16863">Luccioni et al. (2023)</a>;
  water from <a href="https://arxiv.org/abs/2304.03271">Li et al. (2023)</a>;
  carbon from <a href="https://www.epa.gov/egrid">US EPA eGRID 2022</a>.
</p>

<!-- Summary cards -->
<div class="cards">
  <div class="card">
    <div class="card-label">Total Queries</div>
    <div class="card-value blue">{tq:,}</div>
    <div class="card-unit">AI requests</div>
  </div>
  <div class="card">
    <div class="card-label">Total Tokens</div>
    <div class="card-value blue">{tt:,}</div>
    <div class="card-unit">input + output</div>
  </div>
  <div class="card">
    <div class="card-label">Energy Used</div>
    <div class="card-value amber">{te:,}</div>
    <div class="card-unit">milli-Watt-hours</div>
  </div>
  <div class="card">
    <div class="card-label">CO₂ Emitted</div>
    <div class="card-value red">{tc:,}</div>
    <div class="card-unit">milligrams CO₂</div>
  </div>
  <div class="card">
    <div class="card-label">Water Used</div>
    <div class="card-value green">{tw:,}</div>
    <div class="card-unit">millilitres</div>
  </div>
  <div class="card">
    <div class="card-label">Estimated Cost</div>
    <div class="card-value amber">${tx:,.6f}</div>
    <div class="card-unit">US dollars</div>
  </div>
</div>

<!-- Charts -->
<div class="charts">
  <div class="chart-card">
    <h2>Daily CO₂ Emissions (mg) — last 30 days</h2>
    <canvas id="co2Chart" height="200"></canvas>
  </div>
  <div class="chart-card">
    <h2>Daily Water Usage (mL) — last 30 days</h2>
    <canvas id="waterChart" height="200"></canvas>
  </div>
  <div class="chart-card">
    <h2>CO₂ by Model (mg)</h2>
    <canvas id="modelCo2Chart" height="200"></canvas>
  </div>
  <div class="chart-card">
    <h2>Cost by Model (USD)</h2>
    <canvas id="modelCostChart" height="200"></canvas>
  </div>
</div>

<!-- Per-model breakdown -->
<div class="section">
  <h2>Per-Model Breakdown</h2>
  <table>
    <thead>
      <tr>
        <th>Model</th><th>Queries</th><th>Tokens</th>
        <th>Energy</th><th>CO₂</th><th>Water</th><th>Cost</th>
      </tr>
    </thead>
    <tbody>{model_rows_html}</tbody>
  </table>
</div>

<!-- Recent queries -->
<div class="section">
  <h2>Recent Queries (last 10)</h2>
  <table>
    <thead>
      <tr><th>Time (UTC)</th><th>Model</th><th>Tokens</th><th>CO₂</th><th>Water</th><th>Cost</th></tr>
    </thead>
    <tbody>{recent_rows_html}</tbody>
  </table>
</div>

<!-- Scientific references -->
<div class="refs">
  <strong>References:</strong><br>
  [1] Luccioni, A.S., Viguier, S. &amp; Ligozat, A-L. (2023).
      <em>Power Hungry Processing: Watts Driving the Cost of AI Deployment?</em>
      <a href="https://arxiv.org/abs/2311.16863">arXiv:2311.16863</a>.<br>
  [2] Li, P., Yang, J., Islam, M.A. &amp; Ren, S. (2023).
      <em>Making AI Less "Thirsty": Uncovering and Addressing the Secret Water Footprint of AI Models.</em>
      <a href="https://arxiv.org/abs/2304.03271">arXiv:2304.03271</a>.<br>
  [3] US EPA. (2022). <em>eGRID 2022 — Emissions &amp; Generation Resource Integrated Database.</em>
      <a href="https://www.epa.gov/egrid/download-data">epa.gov/egrid</a>.
</div>

<script>
const CHART_DEFAULTS = {{
  responsive: true,
  plugins: {{ legend: {{ display: false }}, tooltip: {{ mode: 'index' }} }},
  scales: {{
    x: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#2d3148' }} }},
    y: {{ ticks: {{ color: '#94a3b8' }}, grid: {{ color: '#2d3148' }} }}
  }}
}};

// Daily CO2 chart
new Chart(document.getElementById('co2Chart'), {{
  type: 'bar',
  data: {{
    labels: {daily_labels},
    datasets: [{{ label: 'CO₂ (mg)', data: {daily_co2},
      backgroundColor: '#ef444488', borderColor: '#ef4444', borderWidth: 1 }}]
  }},
  options: CHART_DEFAULTS
}});

// Daily water chart
new Chart(document.getElementById('waterChart'), {{
  type: 'bar',
  data: {{
    labels: {daily_labels},
    datasets: [{{ label: 'Water (mL)', data: {daily_water},
      backgroundColor: '#22c55e88', borderColor: '#22c55e', borderWidth: 1 }}]
  }},
  options: CHART_DEFAULTS
}});

// Model CO2 chart
new Chart(document.getElementById('modelCo2Chart'), {{
  type: 'doughnut',
  data: {{
    labels: {model_labels},
    datasets: [{{ data: {model_co2},
      backgroundColor: ['#3b82f6','#ef4444','#22c55e','#f59e0b','#8b5cf6','#ec4899','#14b8a6','#f97316'] }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'right', labels: {{ color: '#e2e8f0' }} }} }} }}
}});

// Model cost chart
new Chart(document.getElementById('modelCostChart'), {{
  type: 'doughnut',
  data: {{
    labels: {model_labels},
    datasets: [{{ data: {model_cost},
      backgroundColor: ['#f59e0b','#3b82f6','#22c55e','#ef4444','#8b5cf6','#ec4899','#14b8a6','#f97316'] }}]
  }},
  options: {{ responsive: true, plugins: {{ legend: {{ position: 'right', labels: {{ color: '#e2e8f0' }} }} }} }}
}});
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Open WebUI Tool class
# ---------------------------------------------------------------------------


class Tools:
    """
    Open WebUI Tool – surfaces AI impact statistics inside the chat.

    The LLM can call these functions when a user asks questions such as:
    • "Show me my AI environmental impact dashboard."
    • "How much CO₂ have I produced with AI today?"
    • "Give me a summary of my AI usage."
    """

    class Valves(BaseModel):
        db_path: str = Field(
            default=_DEFAULT_DB_PATH,
            description="Path to the SQLite database written by the AI Impact Filter.",
        )

    def __init__(self):
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # Public tool functions (callable by the LLM)
    # ------------------------------------------------------------------

    def get_impact_summary(self, __user__: Optional[dict] = None) -> str:
        """
        Return a plain-text summary of all-time AI environmental impact metrics
        including total CO₂ emitted, water consumed, energy used, and USD cost.
        Call this when the user asks for a quick overview of their AI footprint.
        """
        conn = _open_db(self.valves.db_path)
        if conn is None:
            return (
                "No usage data found. "
                "Make sure the AI Environmental Impact Filter is installed and active."
            )

        totals = _query_totals(conn)
        by_model = _query_by_model(conn)
        conn.close()

        tq = int(totals.get("total_queries") or 0)
        tt = int(totals.get("total_tokens") or 0)
        te = float(totals.get("total_energy_wh") or 0)
        tc = float(totals.get("total_co2_g") or 0)
        tw = float(totals.get("total_water_ml") or 0)
        tx = float(totals.get("total_cost_usd") or 0)

        lines = [
            "## 🌱 AI Environmental Impact Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total queries | {tq:,} |",
            f"| Total tokens | {tt:,} |",
            f"| Energy used | {te*1000:.4f} mWh |",
            f"| CO₂ emitted | {tc*1000:.4f} mg |",
            f"| Water consumed | {tw:.4f} mL |",
            f"| Estimated cost | ${tx:.6f} |",
            "",
            "### By Model",
            "| Model | Queries | CO₂ (mg) | Water (mL) | Cost (USD) |",
            "|-------|---------|----------|------------|------------|",
        ]
        for r in by_model:
            lines.append(
                f"| {r['model']} | {int(r['queries'])} | "
                f"{round(r['co2_g']*1000, 4)} | "
                f"{round(r['water_ml'], 4)} | "
                f"${round(r['cost_usd'], 6)} |"
            )

        lines += [
            "",
            "*Methodology: energy – [Luccioni et al. (2023)](https://arxiv.org/abs/2311.16863); "
            "water – [Li et al. (2023)](https://arxiv.org/abs/2304.03271); "
            "carbon – [US EPA eGRID 2022](https://www.epa.gov/egrid).*",
        ]

        return "\n".join(lines)

    def get_dashboard_html(self, __user__: Optional[dict] = None) -> str:
        """
        Generate and return a complete HTML dashboard page showing AI impact
        charts (CO₂, water, energy, cost) and per-model breakdowns.
        Call this when the user asks to see their AI impact dashboard.
        """
        conn = _open_db(self.valves.db_path)
        if conn is None:
            return (
                "<p>No usage data found. "
                "Make sure the AI Environmental Impact Filter is installed and active.</p>"
            )

        totals = _query_totals(conn)
        by_model = _query_by_model(conn)
        recent = _query_recent(conn, limit=10)
        daily = _query_daily_totals(conn, days=30)
        conn.close()

        return _render_dashboard_html(totals, by_model, recent, daily)

    def export_data_json(self, __user__: Optional[dict] = None) -> str:
        """
        Export all usage records as a JSON string.
        Useful for external analysis or to feed the standalone dashboard.html file.
        """
        conn = _open_db(self.valves.db_path)
        if conn is None:
            return json.dumps({"error": "No usage data found."})

        rows = conn.execute(
            "SELECT * FROM usage_records ORDER BY timestamp DESC"
        ).fetchall()
        conn.close()

        data = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "records": [dict(r) for r in rows],
        }
        return json.dumps(data, indent=2)
