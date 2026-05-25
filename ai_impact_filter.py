"""
title: AI Environmental Impact Filter
author: AI Impact Plugin
author_url: https://github.com/NCC-Open-Innovation-Office/ai-impact-plugin
git_url: https://github.com/NCC-Open-Innovation-Office/ai-impact-plugin.git
description: >
  Tracks every AI model invocation and calculates the environmental cost of
  each prompt: energy (Wh), CO₂ emissions (g), water usage (mL), and USD cost.
  Results are persisted to a local SQLite database and, optionally, appended to
  every assistant message so users see the footprint in real time.

  Scientific basis
  ----------------
  • Energy  – Luccioni, A.S., Viguier, S. & Ligozat, A-L. (2023).
              "Power Hungry Processing: Watts Driving the Cost of AI
              Deployment?" arXiv:2311.16863 / DOI:10.1145/3627673.3679071.
              Baseline: BLOOM-176B measured at 0.025 Wh per 1 000 tokens on
              8 × A100 GPUs.  Other models are scaled by (params/176B)^0.8
              with a 0.5× efficiency discount for cloud-optimised deployments.

  • Water   – Li, P., Yang, J., Islam, M.A. & Ren, S. (2023).
              "Making AI Less 'Thirsty': Uncovering and Addressing the Secret
              Water Footprint of AI Models." arXiv:2304.03271.
              Water Usage Effectiveness (WUE) = 1.8 L / kWh (average across
              major cloud data-centre campuses).

  • Carbon  – US EPA eGRID 2022 national average: 386 g CO₂ / kWh.
              https://www.epa.gov/egrid/download-data

required_open_webui_version: 0.3.17
requirements: pydantic>=2.0.0
version: 1.0.0
licence: MIT
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Scientific constants (see module docstring for references)
# ---------------------------------------------------------------------------

CARBON_INTENSITY_G_CO2_PER_KWH: float = 386.0   # US EPA eGRID 2022
WATER_L_PER_KWH: float = 1.8                     # Li et al. (2023) WUE
BLOOM_176B_WH_PER_1K_TOKENS: float = 0.025       # Luccioni et al. (2023)
SCALING_EXPONENT: float = 0.8                     # Luccioni et al. (2023)
CLOUD_EFFICIENCY_FACTOR: float = 0.5             # cloud vs. local deployment

# ---------------------------------------------------------------------------
# Model energy + cost lookup table  (loaded from model_data.json at start-up)
# ---------------------------------------------------------------------------

_MODEL_DATA: dict[str, dict] = {}
_MODEL_DATA_PATH = Path(__file__).parent / "model_data.json"


def _load_model_data() -> dict[str, dict]:
    """Return model data from model_data.json, falling back to a built-in dict."""
    try:
        with open(_MODEL_DATA_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        return raw.get("models", {})
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # Minimal built-in fallback so the filter still works without the file
        return {
            "default": {
                "energy_wh_per_1k_tokens": 0.005,
                "input_cost_usd_per_1k_tokens": 0.001,
                "output_cost_usd_per_1k_tokens": 0.002,
            }
        }


# ---------------------------------------------------------------------------
# Core calculation helpers (pure functions – easy to unit-test)
# ---------------------------------------------------------------------------


def get_model_record(model_name: str, model_data: dict[str, dict]) -> dict:
    """
    Return the energy/cost record for *model_name*.

    Matching is case-insensitive and uses substring search so that variants
    like ``gpt-4-0125-preview`` still match the ``gpt-4`` entry.
    """
    if not model_name:
        return model_data.get("default", {})

    name_lower = model_name.lower().strip()

    # 1. Exact match
    if name_lower in model_data:
        return model_data[name_lower]

    # 2. Substring / prefix match (longest key wins to avoid gpt-4 matching
    #    gpt-4o-mini before gpt-4o)
    best_key = ""
    for key in model_data:
        if key == "default":
            continue
        if key in name_lower or name_lower.startswith(key):
            if len(key) > len(best_key):
                best_key = key

    if best_key:
        return model_data[best_key]

    return model_data.get("default", {
        "energy_wh_per_1k_tokens": 0.005,
        "input_cost_usd_per_1k_tokens": 0.001,
        "output_cost_usd_per_1k_tokens": 0.002,
    })


def calculate_impact(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    model_data: dict[str, dict],
    *,
    carbon_intensity: float = CARBON_INTENSITY_G_CO2_PER_KWH,
    water_wue: float = WATER_L_PER_KWH,
) -> dict[str, float]:
    """
    Calculate the environmental impact of one AI query.

    Parameters
    ----------
    model_name:       Model identifier string (e.g. ``"gpt-4"``).
    input_tokens:     Number of prompt / input tokens.
    output_tokens:    Number of completion / output tokens.
    model_data:       Full model lookup table (from ``_load_model_data()``).
    carbon_intensity: g CO₂ per kWh (default: US EPA eGRID 2022 average).
    water_wue:        Litres of water per kWh (default: Li et al. 2023 average).

    Returns
    -------
    dict with keys:
        ``energy_wh``  – energy consumed in watt-hours
        ``co2_g``      – CO₂ emitted in grams
        ``water_ml``   – water consumed in millilitres
        ``cost_usd``   – estimated USD cost
    """
    record = get_model_record(model_name, model_data)

    total_tokens = max(0, input_tokens) + max(0, output_tokens)
    energy_wh = (total_tokens / 1000.0) * record.get("energy_wh_per_1k_tokens", 0.005)

    # CO₂ in grams: convert Wh → kWh then multiply by gCO₂/kWh
    co2_g = (energy_wh / 1000.0) * carbon_intensity

    # Water in millilitres: convert Wh → kWh × L/kWh × 1000 mL/L
    water_ml = (energy_wh / 1000.0) * water_wue * 1000.0

    # Monetary cost
    cost_usd = (
        (max(0, input_tokens) / 1000.0) * record.get("input_cost_usd_per_1k_tokens", 0.001)
        + (max(0, output_tokens) / 1000.0) * record.get("output_cost_usd_per_1k_tokens", 0.002)
    )

    return {
        "energy_wh": round(energy_wh, 8),
        "co2_g": round(co2_g, 8),
        "water_ml": round(water_ml, 8),
        "cost_usd": round(cost_usd, 8),
    }


def estimate_tokens(text: str) -> int:
    """
    Estimate the number of tokens in *text*.

    Uses the common approximation: 1 token ≈ 4 characters in English.
    This is used only when the API does not return usage statistics.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _extract_usage(body: dict) -> tuple[int, int]:
    """
    Extract (input_tokens, output_tokens) from an Open WebUI response body.

    Falls back to character-based estimation when ``usage`` is absent.
    """
    usage = body.get("usage") or {}
    if usage:
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        if prompt_tokens or completion_tokens:
            return prompt_tokens, completion_tokens

    # Fallback: count characters in the messages list
    messages = body.get("messages", [])
    input_text = " ".join(
        str(m.get("content", ""))
        for m in messages
        if m.get("role") in ("user", "system")
    )
    output_text = " ".join(
        str(m.get("content", ""))
        for m in messages
        if m.get("role") == "assistant"
    )
    return estimate_tokens(input_text), estimate_tokens(output_text)


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        TEXT    NOT NULL,
    user_id          TEXT,
    user_name        TEXT,
    model            TEXT    NOT NULL,
    input_tokens     INTEGER NOT NULL,
    output_tokens    INTEGER NOT NULL,
    total_tokens     INTEGER NOT NULL,
    energy_wh        REAL    NOT NULL,
    co2_g            REAL    NOT NULL,
    water_ml         REAL    NOT NULL,
    cost_usd         REAL    NOT NULL,
    chat_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_records (timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_model     ON usage_records (model);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database and apply the schema."""
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def insert_record(
    conn: sqlite3.Connection,
    *,
    timestamp: str,
    user_id: str,
    user_name: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    energy_wh: float,
    co2_g: float,
    water_ml: float,
    cost_usd: float,
    chat_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO usage_records
            (timestamp, user_id, user_name, model,
             input_tokens, output_tokens, total_tokens,
             energy_wh, co2_g, water_ml, cost_usd, chat_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            timestamp,
            user_id,
            user_name,
            model,
            input_tokens,
            output_tokens,
            input_tokens + output_tokens,
            energy_wh,
            co2_g,
            water_ml,
            cost_usd,
            chat_id,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Impact summary text helper
# ---------------------------------------------------------------------------


def _format_impact_summary(impact: dict, model: str) -> str:
    """Return a compact markdown-formatted impact summary string."""
    return (
        "\n\n---\n"
        "**🌱 AI Environmental Impact**  \n"
        f"⚡ Energy: `{impact['energy_wh'] * 1000:.4f} mWh`  \n"
        f"☁️ CO₂: `{impact['co2_g'] * 1000:.4f} mg`  \n"
        f"💧 Water: `{impact['water_ml']:.4f} mL`  \n"
        f"💰 Cost: `${impact['cost_usd']:.6f}`  \n"
        f"*Model: {model} — [methodology](https://arxiv.org/abs/2311.16863)*"
    )


# ---------------------------------------------------------------------------
# Open WebUI Filter class
# ---------------------------------------------------------------------------


class Filter:
    """
    Open WebUI Filter that logs the environmental footprint of every AI call.

    Valves
    ------
    db_path
        Path to the SQLite database file (``~`` is expanded).
    show_impact_in_response
        When ``True``, appends a short footprint summary to every assistant
        message so users see it directly in the chat.
    carbon_intensity_g_co2_per_kwh
        Override the default US EPA grid intensity (386 g CO₂/kWh).
    water_usage_effectiveness_l_per_kwh
        Override the default WUE coefficient from Li et al. 2023 (1.8 L/kWh).
    """

    class Valves(BaseModel):
        db_path: str = Field(
            default="~/.local/share/ai_impact/usage.db",
            description="Path to the SQLite database for usage records.",
        )
        show_impact_in_response: bool = Field(
            default=True,
            description="Append an environmental-impact summary to each AI response.",
        )
        carbon_intensity_g_co2_per_kwh: float = Field(
            default=CARBON_INTENSITY_G_CO2_PER_KWH,
            description=(
                "Carbon intensity of the electricity grid in g CO₂/kWh. "
                "Default: US national average (EPA eGRID 2022)."
            ),
        )
        water_usage_effectiveness_l_per_kwh: float = Field(
            default=WATER_L_PER_KWH,
            description=(
                "Data-centre Water Usage Effectiveness (WUE) in L/kWh. "
                "Default: 1.8 L/kWh (Li et al. 2023 average)."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()
        self._model_data: dict[str, dict] = _load_model_data()
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = init_db(self.valves.db_path)
        return self._conn

    # ------------------------------------------------------------------
    # Open WebUI filter hooks
    # ------------------------------------------------------------------

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> dict:
        """Pass-through – no pre-processing needed."""
        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[Any], Awaitable[None]]] = None,
    ) -> dict:
        """
        Intercept the completed response, calculate environmental impact,
        persist to SQLite, and (optionally) annotate the assistant message.
        """
        try:
            model: str = body.get("model", "unknown")
            input_tokens, output_tokens = _extract_usage(body)

            impact = calculate_impact(
                model_name=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model_data=self._model_data,
                carbon_intensity=self.valves.carbon_intensity_g_co2_per_kwh,
                water_wue=self.valves.water_usage_effectiveness_l_per_kwh,
            )

            # ---- Persist ------------------------------------------------
            user = __user__ or {}
            insert_record(
                self._get_conn(),
                timestamp=datetime.now(timezone.utc).isoformat(),
                user_id=str(user.get("id", "")),
                user_name=str(user.get("name", "")),
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                energy_wh=impact["energy_wh"],
                co2_g=impact["co2_g"],
                water_ml=impact["water_ml"],
                cost_usd=impact["cost_usd"],
                chat_id=str(body.get("chat_id", "")),
            )

            # ---- Annotate response --------------------------------------
            if self.valves.show_impact_in_response:
                messages: list = body.get("messages", [])
                summary = _format_impact_summary(impact, model)
                for i in range(len(messages) - 1, -1, -1):
                    if messages[i].get("role") == "assistant":
                        original = messages[i].get("content", "")
                        if isinstance(original, str):
                            messages[i]["content"] = original + summary
                        break

        except Exception:
            # Never crash the user's conversation due to tracking errors
            pass

        return body
