#!/usr/bin/env python3
"""
Bootstraps the AI Impact plugin in Open WebUI automatically.

Steps
-----
1. Waits until Open WebUI reports a healthy status.
2. Signs in with the provided admin credentials, or creates the admin account
   on the very first run (the first user to sign up becomes admin in Open WebUI).
3. Registers the AI Impact Filter function, enables it, and marks it global
   so it runs for every model without any further UI interaction.
4. Registers the AI Impact Tool (users still need to attach it to specific
   models in Workspace → Models, but the code is pre-loaded).

Environment variables
---------------------
WEBUI_URL       Internal URL of the Open WebUI service.  Default: http://open-webui:8080
ADMIN_EMAIL     Admin account e-mail.  REQUIRED.
ADMIN_PASSWORD  Admin account password.  REQUIRED.
ADMIN_NAME      Display name used when creating the account.  Default: Admin
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration (from environment)
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("WEBUI_URL", "http://open-webui:8080").rstrip("/")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "Admin")

FILTER_PATH = Path("/scripts/ai_impact_filter.py")
TOOL_PATH = Path("/scripts/ai_impact_tool.py")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _request(method: str, path: str, data: dict | None = None, token: str | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return {"_status": exc.code, "_error": exc.read().decode(errors="replace")}
    except Exception as exc:
        return {"_error": str(exc)}


# ---------------------------------------------------------------------------
# Step 1 – Wait for Open WebUI
# ---------------------------------------------------------------------------


def wait_healthy(retries: int = 60, interval: int = 5) -> None:
    print("Waiting for Open WebUI to become healthy …", flush=True)
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(f"{BASE_URL}/health", timeout=5) as resp:
                if resp.status == 200:
                    print("Open WebUI is up.", flush=True)
                    return
        except Exception:
            pass
        print(f"  Not ready (attempt {attempt}/{retries}), retrying in {interval}s …", flush=True)
        time.sleep(interval)
    sys.exit("ERROR: Open WebUI did not become healthy in time.  Aborting setup.")


# ---------------------------------------------------------------------------
# Step 2 – Authenticate
# ---------------------------------------------------------------------------


def authenticate() -> str:
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        sys.exit(
            "ERROR: ADMIN_EMAIL and ADMIN_PASSWORD must be set.\n"
            "Add them to your .env file or pass them as environment variables."
        )

    # Attempt sign-in first (handles re-runs after the account already exists).
    resp = _request("POST", "/api/v1/auths/signin", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    if token := resp.get("token"):
        print("Signed in as admin.", flush=True)
        return token

    # First-ever run: no accounts exist yet — sign up to create the admin account.
    resp = _request("POST", "/api/v1/auths/signup", {
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "name": ADMIN_NAME,
    })
    if token := resp.get("token"):
        print("Admin account created and signed in.", flush=True)
        return token

    sys.exit(
        "ERROR: Cannot authenticate with Open WebUI.\n"
        "  • If the admin account already exists, make sure ADMIN_EMAIL and\n"
        "    ADMIN_PASSWORD match those credentials.\n"
        "  • If you created your account through the browser before running\n"
        "    this setup, set ADMIN_EMAIL / ADMIN_PASSWORD to match that account."
    )


# ---------------------------------------------------------------------------
# Step 3 – Register the Filter, enable it, make it global
# ---------------------------------------------------------------------------


def register_filter(token: str) -> None:
    fid = "ai_impact_filter"
    content = FILTER_PATH.read_text()

    existing = _request("GET", f"/api/v1/functions/id/{fid}", token=token)
    if "id" not in existing:
        resp = _request("POST", "/api/v1/functions/create", {
            "id": fid,
            "name": "AI Impact Filter",
            "content": content,
            "meta": {
                "description": (
                    "Tracks CO₂, water, energy, and cost for every AI model response."
                ),
                "manifest": {},
            },
        }, token=token)
        if "id" not in resp:
            sys.exit(f"ERROR: Failed to create filter: {resp}")
        print(f"Filter '{fid}' created.", flush=True)
        # Re-fetch so the toggle checks below have accurate state.
        existing = _request("GET", f"/api/v1/functions/id/{fid}", token=token)
    else:
        print(f"Filter '{fid}' already registered.", flush=True)

    if not existing.get("is_active"):
        _request("POST", f"/api/v1/functions/id/{fid}/toggle", token=token)
        print(f"Filter '{fid}' enabled.", flush=True)
    else:
        print(f"Filter '{fid}' is already enabled.", flush=True)

    if not existing.get("is_global"):
        _request("POST", f"/api/v1/functions/id/{fid}/toggle/global", token=token)
        print(f"Filter '{fid}' set to global.", flush=True)
    else:
        print(f"Filter '{fid}' is already global.", flush=True)


# ---------------------------------------------------------------------------
# Step 4 – Register the Tool
# ---------------------------------------------------------------------------


def register_tool(token: str) -> None:
    tid = "ai_impact_tool"
    content = TOOL_PATH.read_text()

    existing = _request("GET", f"/api/v1/tools/id/{tid}", token=token)
    if "id" not in existing:
        resp = _request("POST", "/api/v1/tools/create", {
            "id": tid,
            "name": "AI Impact Tool",
            "content": content,
            "meta": {
                "description": "Query AI usage summaries and export impact data as JSON.",
                "manifest": {},
            },
        }, token=token)
        if "id" not in resp:
            sys.exit(f"ERROR: Failed to create tool: {resp}")
        print(f"Tool '{tid}' created.", flush=True)
        print(
            "NOTE: To use the tool, go to Workspace → Models, edit your model,\n"
            "      and enable 'AI Impact Tool' under Tools.",
            flush=True,
        )
    else:
        print(f"Tool '{tid}' already registered.", flush=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    wait_healthy()
    token = authenticate()
    register_filter(token)
    register_tool(token)
    print("Setup complete.", flush=True)
