"""
Tests for the AI Environmental Impact Plugin.

Covers:
  - calculate_impact()      – core metric calculations
  - get_model_record()      – model lookup / fuzzy matching
  - estimate_tokens()       – fallback token estimation
  - _extract_usage()        – token extraction from response body
  - init_db() / insert_record() / query helpers – SQLite persistence
  - _format_impact_summary()  – annotation formatting
  - Filter class             – plugin integration (outlet hook)
  - Tools class              – dashboard tool functions
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# We import from the plugin files directly
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_impact_filter import (
    CARBON_INTENSITY_G_CO2_PER_KWH,
    WATER_L_PER_KWH,
    Filter,
    _extract_usage,
    _format_impact_summary,
    calculate_impact,
    estimate_tokens,
    get_model_record,
    init_db,
    insert_record,
)
from ai_impact_tool import (
    Tools,
    _open_db,
    _query_by_model,
    _query_daily_totals,
    _query_recent,
    _query_totals,
    _render_dashboard_html,
)


# ---------------------------------------------------------------------------
# Sample model data fixture
# ---------------------------------------------------------------------------

SAMPLE_MODEL_DATA: dict = {
    "gpt-4": {
        "energy_wh_per_1k_tokens": 0.0109,
        "input_cost_usd_per_1k_tokens": 0.030,
        "output_cost_usd_per_1k_tokens": 0.060,
    },
    "gpt-3.5-turbo": {
        "energy_wh_per_1k_tokens": 0.00205,
        "input_cost_usd_per_1k_tokens": 0.00050,
        "output_cost_usd_per_1k_tokens": 0.00150,
    },
    "llama3:8b": {
        "energy_wh_per_1k_tokens": 0.00196,
        "input_cost_usd_per_1k_tokens": 0.0,
        "output_cost_usd_per_1k_tokens": 0.0,
    },
    "default": {
        "energy_wh_per_1k_tokens": 0.005,
        "input_cost_usd_per_1k_tokens": 0.001,
        "output_cost_usd_per_1k_tokens": 0.002,
    },
}


# ===========================================================================
# calculate_impact
# ===========================================================================


class TestCalculateImpact(unittest.TestCase):
    def test_gpt4_basic(self):
        result = calculate_impact("gpt-4", 500, 200, SAMPLE_MODEL_DATA)
        total_tokens = 700
        expected_energy = (total_tokens / 1000) * 0.0109
        self.assertAlmostEqual(result["energy_wh"], expected_energy, places=8)

    def test_co2_formula(self):
        """co2_g = (energy_wh / 1000) * carbon_intensity"""
        result = calculate_impact("gpt-4", 1000, 0, SAMPLE_MODEL_DATA)
        expected_co2 = (result["energy_wh"] / 1000) * CARBON_INTENSITY_G_CO2_PER_KWH
        self.assertAlmostEqual(result["co2_g"], expected_co2, places=10)

    def test_water_formula(self):
        """water_ml = (energy_wh / 1000) * WUE * 1000"""
        result = calculate_impact("gpt-4", 1000, 0, SAMPLE_MODEL_DATA)
        expected_water = (result["energy_wh"] / 1000) * WATER_L_PER_KWH * 1000
        self.assertAlmostEqual(result["water_ml"], expected_water, places=10)

    def test_cost_formula(self):
        """cost = (input/1k * input_rate) + (output/1k * output_rate)"""
        result = calculate_impact("gpt-4", 1000, 500, SAMPLE_MODEL_DATA)
        expected_cost = (1000 / 1000) * 0.030 + (500 / 1000) * 0.060
        self.assertAlmostEqual(result["cost_usd"], expected_cost, places=8)

    def test_zero_tokens(self):
        result = calculate_impact("gpt-4", 0, 0, SAMPLE_MODEL_DATA)
        self.assertEqual(result["energy_wh"], 0.0)
        self.assertEqual(result["co2_g"], 0.0)
        self.assertEqual(result["water_ml"], 0.0)
        self.assertEqual(result["cost_usd"], 0.0)

    def test_negative_tokens_treated_as_zero(self):
        result = calculate_impact("gpt-4", -10, -5, SAMPLE_MODEL_DATA)
        self.assertEqual(result["energy_wh"], 0.0)

    def test_returns_all_keys(self):
        result = calculate_impact("gpt-4", 100, 50, SAMPLE_MODEL_DATA)
        self.assertIn("energy_wh", result)
        self.assertIn("co2_g", result)
        self.assertIn("water_ml", result)
        self.assertIn("cost_usd", result)

    def test_custom_carbon_intensity(self):
        """Passing a custom carbon intensity should change CO₂ result."""
        r1 = calculate_impact("gpt-4", 1000, 500, SAMPLE_MODEL_DATA, carbon_intensity=386.0)
        r2 = calculate_impact("gpt-4", 1000, 500, SAMPLE_MODEL_DATA, carbon_intensity=100.0)
        self.assertGreater(r1["co2_g"], r2["co2_g"])

    def test_custom_water_wue(self):
        r1 = calculate_impact("gpt-4", 1000, 500, SAMPLE_MODEL_DATA, water_wue=1.8)
        r2 = calculate_impact("gpt-4", 1000, 500, SAMPLE_MODEL_DATA, water_wue=0.5)
        self.assertGreater(r1["water_ml"], r2["water_ml"])

    def test_free_local_model_zero_cost(self):
        result = calculate_impact("llama3:8b", 500, 200, SAMPLE_MODEL_DATA)
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertGreater(result["energy_wh"], 0.0)

    def test_default_model_fallback(self):
        result = calculate_impact("totally-unknown-model-xyz", 500, 200, SAMPLE_MODEL_DATA)
        expected_energy = (700 / 1000) * 0.005
        self.assertAlmostEqual(result["energy_wh"], expected_energy, places=8)


# ===========================================================================
# get_model_record
# ===========================================================================


class TestGetModelRecord(unittest.TestCase):
    def test_exact_match(self):
        record = get_model_record("gpt-4", SAMPLE_MODEL_DATA)
        self.assertEqual(record["energy_wh_per_1k_tokens"], 0.0109)

    def test_case_insensitive(self):
        record = get_model_record("GPT-4", SAMPLE_MODEL_DATA)
        self.assertEqual(record["energy_wh_per_1k_tokens"], 0.0109)

    def test_prefix_variant(self):
        """gpt-4-turbo should resolve to the gpt-4 record."""
        record = get_model_record("gpt-4-turbo", SAMPLE_MODEL_DATA)
        self.assertEqual(record["energy_wh_per_1k_tokens"], 0.0109)

    def test_substring_match(self):
        record = get_model_record("gpt-3.5-turbo-0125", SAMPLE_MODEL_DATA)
        self.assertEqual(record["energy_wh_per_1k_tokens"], 0.00205)

    def test_unknown_model_returns_default(self):
        record = get_model_record("totally-unknown", SAMPLE_MODEL_DATA)
        self.assertEqual(record["energy_wh_per_1k_tokens"], 0.005)

    def test_empty_model_name_returns_default(self):
        record = get_model_record("", SAMPLE_MODEL_DATA)
        self.assertEqual(record["energy_wh_per_1k_tokens"], 0.005)

    def test_none_model_name_returns_default(self):
        record = get_model_record(None, SAMPLE_MODEL_DATA)  # type: ignore[arg-type]
        self.assertEqual(record["energy_wh_per_1k_tokens"], 0.005)


# ===========================================================================
# estimate_tokens
# ===========================================================================


class TestEstimateTokens(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(estimate_tokens(""), 0)

    def test_none_treated_as_zero(self):
        self.assertEqual(estimate_tokens(None), 0)  # type: ignore[arg-type]

    def test_short_text(self):
        # "Hello" = 5 chars → max(1, 5//4) = 1
        self.assertEqual(estimate_tokens("Hello"), 1)

    def test_longer_text(self):
        text = "a" * 400
        self.assertEqual(estimate_tokens(text), 100)

    def test_returns_at_least_one_for_nonempty(self):
        self.assertGreaterEqual(estimate_tokens("x"), 1)


# ===========================================================================
# _extract_usage
# ===========================================================================


class TestExtractUsage(unittest.TestCase):
    def test_with_usage_field(self):
        body = {"usage": {"prompt_tokens": 150, "completion_tokens": 75}}
        self.assertEqual(_extract_usage(body), (150, 75))

    def test_partial_usage(self):
        body = {"usage": {"prompt_tokens": 50}}
        inp, out = _extract_usage(body)
        self.assertEqual(inp, 50)
        self.assertEqual(out, 0)

    def test_fallback_to_messages(self):
        body = {
            "messages": [
                {"role": "user", "content": "a" * 200},
                {"role": "assistant", "content": "b" * 100},
            ]
        }
        inp, out = _extract_usage(body)
        self.assertEqual(inp, 50)   # 200 chars // 4
        self.assertEqual(out, 25)   # 100 chars // 4

    def test_empty_body(self):
        inp, out = _extract_usage({})
        self.assertEqual(inp, 0)
        self.assertEqual(out, 0)


# ===========================================================================
# SQLite persistence
# ===========================================================================


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.conn = init_db(self.tmp.name)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.tmp.name)

    def _insert_one(self, model="gpt-4", input_tokens=100, output_tokens=50):
        insert_record(
            self.conn,
            timestamp=datetime.now(timezone.utc).isoformat(),
            user_id="user1",
            user_name="Alice",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            energy_wh=0.001,
            co2_g=0.0004,
            water_ml=0.0018,
            cost_usd=0.005,
            chat_id="chat-abc",
        )

    def test_insert_and_count(self):
        self._insert_one()
        row = self.conn.execute("SELECT COUNT(*) FROM usage_records").fetchone()
        self.assertEqual(row[0], 1)

    def test_total_tokens_stored(self):
        self._insert_one(input_tokens=100, output_tokens=50)
        row = self.conn.execute("SELECT total_tokens FROM usage_records").fetchone()
        self.assertEqual(row[0], 150)

    def test_multiple_records(self):
        for _ in range(5):
            self._insert_one()
        row = self.conn.execute("SELECT COUNT(*) FROM usage_records").fetchone()
        self.assertEqual(row[0], 5)

    def test_query_totals(self):
        self._insert_one(model="gpt-4")
        self._insert_one(model="llama3:8b")
        self.conn.row_factory = sqlite3.Row
        totals = _query_totals(self.conn)
        self.assertEqual(int(totals["total_queries"]), 2)

    def test_query_by_model(self):
        self._insert_one(model="gpt-4")
        self._insert_one(model="gpt-4")
        self._insert_one(model="llama3:8b")
        self.conn.row_factory = sqlite3.Row
        rows = _query_by_model(self.conn)
        models = {r["model"] for r in rows}
        self.assertIn("gpt-4", models)
        self.assertIn("llama3:8b", models)
        for r in rows:
            if r["model"] == "gpt-4":
                self.assertEqual(int(r["queries"]), 2)

    def test_query_recent(self):
        for _ in range(15):
            self._insert_one()
        self.conn.row_factory = sqlite3.Row
        rows = _query_recent(self.conn, limit=10)
        self.assertEqual(len(rows), 10)

    def test_query_daily_totals(self):
        self._insert_one()
        self.conn.row_factory = sqlite3.Row
        rows = _query_daily_totals(self.conn, days=30)
        # At least one row for today
        self.assertGreaterEqual(len(rows), 1)


# ===========================================================================
# _format_impact_summary
# ===========================================================================


class TestFormatImpactSummary(unittest.TestCase):
    def setUp(self):
        self.impact = {
            "energy_wh": 0.001,
            "co2_g": 0.000386,
            "water_ml": 0.0018,
            "cost_usd": 0.005,
        }

    def test_contains_headers(self):
        summary = _format_impact_summary(self.impact, "gpt-4")
        self.assertIn("AI Environmental Impact", summary)

    def test_contains_model_name(self):
        summary = _format_impact_summary(self.impact, "gpt-4")
        self.assertIn("gpt-4", summary)

    def test_contains_metrics(self):
        summary = _format_impact_summary(self.impact, "gpt-4")
        self.assertIn("Energy", summary)
        self.assertIn("CO₂", summary)
        self.assertIn("Water", summary)
        self.assertIn("Cost", summary)

    def test_contains_arxiv_link(self):
        summary = _format_impact_summary(self.impact, "gpt-4")
        self.assertIn("2311.16863", summary)


# ===========================================================================
# Filter class (integration)
# ===========================================================================


class TestFilter(unittest.IsolatedAsyncioTestCase):
    def _make_filter(self, db_path):
        f = Filter()
        f.valves.db_path = db_path
        f.valves.show_impact_in_response = True
        # Patch model data
        f._model_data = SAMPLE_MODEL_DATA
        return f

    async def test_outlet_does_not_crash_on_minimal_body(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            f = self._make_filter(db)
            body = {"model": "gpt-4", "messages": [{"role": "assistant", "content": "Hi"}]}
            result = await f.outlet(body)
            self.assertIsNotNone(result)

    async def test_outlet_annotates_assistant_message(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            f = self._make_filter(db)
            body = {
                "model": "gpt-4",
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "World"},
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            }
            result = await f.outlet(body)
            last_msg = result["messages"][-1]
            self.assertIn("Environmental Impact", last_msg["content"])

    async def test_outlet_writes_to_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            f = self._make_filter(db)
            body = {
                "model": "gpt-3.5-turbo",
                "messages": [{"role": "assistant", "content": "OK"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
            await f.outlet(body)
            conn = sqlite3.connect(db)
            count = conn.execute("SELECT COUNT(*) FROM usage_records").fetchone()[0]
            conn.close()
            self.assertEqual(count, 1)

    async def test_outlet_no_annotation_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            f = self._make_filter(db)
            f.valves.show_impact_in_response = False
            body = {
                "model": "gpt-4",
                "messages": [{"role": "assistant", "content": "Hello"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5},
            }
            result = await f.outlet(body)
            self.assertEqual(result["messages"][-1]["content"], "Hello")

    async def test_inlet_is_passthrough(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            f = self._make_filter(db)
            body = {"model": "gpt-4", "messages": [{"role": "user", "content": "Hi"}]}
            result = await f.inlet(body)
            self.assertEqual(result, body)

    async def test_outlet_survives_corrupt_body(self):
        """Filter must never crash the user's conversation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "test.db")
            f = self._make_filter(db)
            # Missing messages key
            body = {"model": "gpt-4"}
            result = await f.outlet(body)
            self.assertEqual(result, body)


# ===========================================================================
# Tools class
# ===========================================================================


class TestTools(unittest.TestCase):
    def _setup_db(self, tmp_dir) -> str:
        db_path = os.path.join(tmp_dir, "test.db")
        conn = init_db(db_path)
        today_ts = datetime.now(timezone.utc).isoformat()
        for model in ["gpt-4", "llama3:8b"]:
            insert_record(
                conn,
                timestamp=today_ts,
                user_id="u1",
                user_name="Bob",
                model=model,
                input_tokens=200,
                output_tokens=100,
                energy_wh=0.002,
                co2_g=0.0008,
                water_ml=0.0036,
                cost_usd=0.009 if model == "gpt-4" else 0.0,
                chat_id="c1",
            )
        conn.close()
        return db_path

    def test_get_impact_summary_no_db(self):
        t = Tools()
        t.valves.db_path = "/nonexistent/path/db.db"
        result = t.get_impact_summary()
        self.assertIn("No usage data", result)

    def test_get_impact_summary_with_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._setup_db(tmpdir)
            t = Tools()
            t.valves.db_path = db_path
            result = t.get_impact_summary()
            self.assertIn("gpt-4", result)
            self.assertIn("llama3:8b", result)
            self.assertIn("Total queries", result)

    def test_get_dashboard_html_no_db(self):
        t = Tools()
        t.valves.db_path = "/nonexistent/path/db.db"
        result = t.get_dashboard_html()
        self.assertIn("No usage data", result)

    def test_get_dashboard_html_with_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._setup_db(tmpdir)
            t = Tools()
            t.valves.db_path = db_path
            html = t.get_dashboard_html()
            self.assertIn("<!DOCTYPE html>", html)
            self.assertIn("gpt-4", html)
            self.assertIn("cdn.jsdelivr.net", html)

    def test_export_data_json_no_db(self):
        t = Tools()
        t.valves.db_path = "/nonexistent/path/db.db"
        result = json.loads(t.export_data_json())
        self.assertIn("error", result)

    def test_export_data_json_with_data(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._setup_db(tmpdir)
            t = Tools()
            t.valves.db_path = db_path
            result = json.loads(t.export_data_json())
            self.assertIn("records", result)
            self.assertEqual(len(result["records"]), 2)

    def test_export_data_json_has_exported_at(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = self._setup_db(tmpdir)
            t = Tools()
            t.valves.db_path = db_path
            result = json.loads(t.export_data_json())
            self.assertIn("exported_at", result)


# ===========================================================================
# render_dashboard_html (smoke test)
# ===========================================================================


class TestRenderDashboardHtml(unittest.TestCase):
    def test_renders_without_error(self):
        html = _render_dashboard_html(
            totals={"total_queries": 5, "total_tokens": 1000, "total_energy_wh": 0.01,
                    "total_co2_g": 0.004, "total_water_ml": 0.018, "total_cost_usd": 0.05},
            by_model=[{"model": "gpt-4", "queries": 5, "tokens": 1000,
                       "energy_wh": 0.01, "co2_g": 0.004, "water_ml": 0.018, "cost_usd": 0.05}],
            recent=[{"timestamp": "2024-01-01T12:00:00", "model": "gpt-4",
                     "total_tokens": 200, "co2_g": 0.0008, "water_ml": 0.0036, "cost_usd": 0.01}],
            daily=[{"day": "2024-01-01", "queries": 5, "co2_g": 0.004,
                    "water_ml": 0.018, "cost_usd": 0.05}],
        )
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("gpt-4", html)
        self.assertIn("cdn.jsdelivr.net", html)

    def test_empty_data_renders_without_error(self):
        html = _render_dashboard_html(
            totals={},
            by_model=[],
            recent=[],
            daily=[],
        )
        self.assertIn("<!DOCTYPE html>", html)


# ===========================================================================
# model_data.json integrity
# ===========================================================================


class TestModelDataJson(unittest.TestCase):
    def setUp(self):
        json_path = Path(__file__).parent.parent / "model_data.json"
        with open(json_path, encoding="utf-8") as fh:
            self.data = json.load(fh)

    def test_top_level_keys(self):
        self.assertIn("metadata", self.data)
        self.assertIn("models", self.data)
        self.assertIn("constants", self.data)

    def test_all_models_have_required_fields(self):
        required = {"energy_wh_per_1k_tokens", "input_cost_usd_per_1k_tokens", "output_cost_usd_per_1k_tokens"}
        for name, record in self.data["models"].items():
            for field in required:
                self.assertIn(field, record, f"Model '{name}' missing field '{field}'")

    def test_energy_values_are_positive(self):
        for name, record in self.data["models"].items():
            self.assertGreaterEqual(
                record["energy_wh_per_1k_tokens"], 0,
                f"Model '{name}' has negative energy value"
            )

    def test_cost_values_are_non_negative(self):
        for name, record in self.data["models"].items():
            self.assertGreaterEqual(record["input_cost_usd_per_1k_tokens"], 0)
            self.assertGreaterEqual(record["output_cost_usd_per_1k_tokens"], 0)

    def test_default_model_exists(self):
        self.assertIn("default", self.data["models"])

    def test_scientific_references_present(self):
        basis = self.data["metadata"]["scientific_basis"]
        self.assertIn("energy", basis)
        self.assertIn("water", basis)
        self.assertIn("carbon", basis)

    def test_constants_block(self):
        c = self.data["constants"]
        self.assertIn("carbon_intensity_g_co2_per_kwh", c)
        self.assertIn("water_usage_effectiveness_l_per_kwh", c)


if __name__ == "__main__":
    unittest.main()
