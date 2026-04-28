# AI Environmental Impact Plugin for Open WebUI

A plugin for [Open WebUI](https://github.com/open-webui/open-webui) that tracks every AI model interaction and reports its environmental footprint — **CO₂ emissions**, **water consumption**, **energy usage**, and **USD cost** — in real time.

Calculations are grounded in peer-reviewed research (see [Scientific Basis](#scientific-basis)).

---

## Features

| Feature | Description |
|---------|-------------|
| 🌱 Real-time footprint | Every assistant reply is annotated with energy, CO₂, water, and cost |
| 📊 Dashboard | Interactive HTML dashboard with charts and per-model breakdowns |
| 🗄️ Persistent storage | All records saved to a local SQLite database |
| 🔬 Science-backed | Calculations derived from Luccioni et al. (2023) and Li et al. (2023) |
| ⚙️ Configurable | Grid carbon intensity, WUE coefficient, and DB path are all adjustable |
| 🤖 20+ models | Built-in data for GPT-4, Claude, Llama, Mistral, Gemma, Phi-3, and more |

---

## Files

| File | Purpose |
|------|---------|
| `ai_impact_filter.py` | **Open WebUI Filter** — intercepts every response, calculates and stores impact, optionally annotates the chat message |
| `ai_impact_tool.py` | **Open WebUI Tool** — LLM-callable functions: `get_impact_summary`, `get_dashboard_html`, `export_data_json` |
| `model_data.json` | Energy and cost data for 20+ AI models with full scientific provenance |
| `dashboard.html` | Standalone HTML dashboard — open in any browser after exporting data |
| `tests/test_impact.py` | 60 unit tests covering all core logic |

---

## Installation

### 1 — Filter (usage tracker)

1. In Open WebUI, go to **Workspace → Functions → + Create Function**.
2. Paste the contents of `ai_impact_filter.py`.
3. Copy `model_data.json` to the same directory as the filter (or the plugin picks up a built-in fallback).
4. Save and **enable** the filter globally or per-workspace.

### 2 — Tool (dashboard queries)

1. In Open WebUI, go to **Workspace → Tools → + Create Tool**.
2. Paste the contents of `ai_impact_tool.py`.
3. Enable the tool in your model settings.
4. Ask the AI: *"Show me my AI environmental impact dashboard"* or *"Give me my AI usage summary."*

### 3 — Standalone dashboard

Export data from the chat:
> *"Export my AI impact data as JSON"*

Save the output as `data.json`, then open `dashboard.html` in a browser and upload the file (or drag-and-drop it).

---

## Configuration (Valves)

Both the filter and tool expose valves you can adjust in Open WebUI:

| Valve | Default | Description |
|-------|---------|-------------|
| `db_path` | `~/.local/share/ai_impact/usage.db` | SQLite database location |
| `show_impact_in_response` | `true` | Append footprint summary to each response |
| `carbon_intensity_g_co2_per_kwh` | `386.0` | Electricity grid carbon intensity (g CO₂/kWh) |
| `water_usage_effectiveness_l_per_kwh` | `1.8` | Data-centre WUE coefficient (L/kWh) |

---

## Scientific Basis

### Energy consumption
> **Luccioni, A.S., Viguier, S. & Ligozat, A-L. (2023).** *Power Hungry Processing: Watts Driving the Cost of AI Deployment?* [arXiv:2311.16863](https://arxiv.org/abs/2311.16863) / [DOI:10.1145/3627673.3679071](https://doi.org/10.1145/3627673.3679071)

The paper directly measures energy consumption of large language models on GPU hardware.  
**Baseline:** BLOOM-176B consumes ~0.025 Wh per 1 000 tokens on 8 × A100 GPUs.  
**Scaling:** Other models are estimated via `E = 0.025 × (params / 176B)^0.8`, with an additional 0.5× efficiency factor for cloud-optimised API deployments.

### Water consumption
> **Li, P., Yang, J., Islam, M.A. & Ren, S. (2023).** *Making AI Less "Thirsty": Uncovering and Addressing the Secret Water Footprint of AI Models.* [arXiv:2304.03271](https://arxiv.org/abs/2304.03271)

Water Usage Effectiveness (WUE) = **1.8 L/kWh** (average across major cloud data-centre campuses — Microsoft, Google).  
Formula: `water_mL = (energy_Wh / 1000) × 1.8 × 1000`

### Carbon emissions
> **US EPA eGRID 2022** — [epa.gov/egrid](https://www.epa.gov/egrid/download-data)

National US average annual carbon intensity: **386 g CO₂/kWh**.  
Formula: `co2_g = (energy_Wh / 1000) × 386`

---

## Running the tests

```bash
pip install pytest pydantic
python -m pytest tests/test_impact.py -v
```

Expected output: **60 passed**.

---

## Example output

When `show_impact_in_response` is enabled, each assistant message includes:

```
---
**🌱 AI Environmental Impact**
⚡ Energy: `0.0109 mWh`
☁️ CO₂: `0.0042 mg`
💧 Water: `0.0196 mL`
💰 Cost: `$0.000027`
*Model: gpt-4 — [methodology](https://arxiv.org/abs/2311.16863)*
```

---

## License

MIT — see [LICENSE](LICENSE).
