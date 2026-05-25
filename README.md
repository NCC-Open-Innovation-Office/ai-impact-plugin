# AI Environmental Impact Plugin for Open WebUI

A plugin for [Open WebUI](https://github.com/open-webui/open-webui) that tracks every AI model interaction and reports its environmental footprint — **CO₂ emissions**, **water consumption**, **energy usage**, and **USD cost** — in real time.

Calculations are grounded in peer-reviewed research (see [Scientific Basis](#scientific-basis)).

---

## Features

| Feature | Description |
|---------|-------------|
| 🌱 Real-time footprint | Every assistant reply is annotated with energy, CO₂, water, and cost (WebUI chat only — see [Limitations](#limitations)) |
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
| `setup.py` | Init script run by Docker Compose to auto-register and enable the filter and tool via the Open WebUI API |
| `.env.example` | Template for the environment variables required by the Docker Compose stack |
| `tests/test_impact.py` | 60 unit tests covering all core logic |

---

## Installation

### 🚀 One-Click Deploy (Docker Compose)

The fastest way to get started is using Docker Compose, which deploys Open WebUI, the AI Impact Dashboard, and a one-shot setup service that registers and activates the plugins automatically.

1. Clone this repository:
   ```bash
   git clone https://github.com/NCC-Open-Innovation-Office/ai-impact-plugin.git
   cd ai-impact-plugin
   ```
2. Create your environment file:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set at minimum:
   ```
   ADMIN_EMAIL=you@example.com
   ADMIN_PASSWORD=a-strong-password
   WEBUI_SECRET_KEY=a-random-secret-string
   ```
   > **First run:** if no Open WebUI accounts exist yet the setup service creates this admin account automatically.  
   > **Subsequent runs:** it signs in with these credentials — make sure they match the account you used to log in the first time.
3. Start the stack:
   ```bash
   docker compose up -d
   ```
4. Access the services:
   - **Open WebUI**: `http://localhost:3000`
   - **AI Impact Dashboard**: `http://localhost:8080`

The `open-webui-setup` container starts after Open WebUI is healthy, then registers the filter (enabled + global) and the tool automatically — no browser clicks required. You can watch it with:
```bash
docker logs open-webui-setup
```

> **One remaining manual step — attach the tool to a model:** Open WebUI does not expose an API to enable a tool globally across all models. To use the tool, go to **Workspace → Models**, edit your model, and enable **AI Impact Tool** under Tools. The filter runs for every model with no extra configuration.

---

### 🛠️ Manual Installation

> **Note:** Filter Functions require admin access. Only Open WebUI administrators can create and manage Functions.

#### 1 — Filter (usage tracker)

1. In Open WebUI, go to **Admin Panel → Functions → + Create Function**.
2. Click **Import from URL** and paste:
   ```
   https://raw.githubusercontent.com/NCC-Open-Innovation-Office/ai-impact-plugin/main/ai_impact_filter.py
   ```
   Alternatively, paste the contents of `ai_impact_filter.py` directly into the code editor.
3. Copy `model_data.json` to the same directory as the filter (or the plugin picks up a built-in fallback).
4. Save the function, then do **both** of the following — the filter will not run without either step:
   - **Toggle it ON** — click the pill switch next to the function name so it turns blue/enabled.
   - **Click the 🌐 globe icon** — this makes it global. Without the globe icon, the filter only applies to models you manually configure it on and is silently skipped for all other chats.

#### 2 — Tool (dashboard queries)

1. In Open WebUI, go to **Workspace → Tools → + Create Tool**.
2. Click **Import from URL** and paste:
   ```
   https://raw.githubusercontent.com/NCC-Open-Innovation-Office/ai-impact-plugin/main/ai_impact_tool.py
   ```
   Alternatively, paste the contents of `ai_impact_tool.py` directly into the code editor.
3. Enable the tool in your model's settings (Workspace → Models → edit your model → Tools).
4. Make sure **Native (Agentic) function calling** is enabled for the model (Admin Panel → Settings → Models → Function Calling → `Native`).
5. Ask the AI: *"Give me my AI usage summary"* or *"Export my AI impact data as JSON."*

#### 3 — Standalone dashboard

Export data from the chat:
> *"Export my AI impact data as JSON"*

Copy the JSON from the response, save it as `data.json`, then open `dashboard.html` in a browser and upload the file (or drag-and-drop it).


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

## Limitations

| Limitation | Detail |
|------------|--------|
| WebUI chat only | The filter's `outlet()` hook only fires for Open WebUI chat requests. Direct API calls to `/api/chat/completions` (e.g. from curl, Continue.dev, or other integrations) are **not tracked** unless the caller also posts to `/api/chat/completed`. |
| Token count accuracy | The filter reads exact token counts from the provider response when available: OpenAI/compatible (`usage.prompt_tokens` / `completion_tokens`), Anthropic (`usage.input_tokens` / `output_tokens`), and Ollama (`prompt_eval_count` / `eval_count`). It falls back to character-based estimation (1 token ≈ 4 chars) only when no usage field is returned — which can occur with some streaming configurations. |
| `chat_id` may be empty | The `chat_id` field recorded in the database is read from the request body and may not always be populated, depending on the client. |


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
