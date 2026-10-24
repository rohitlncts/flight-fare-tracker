#!/usr/bin/env python3
"""State and configuration manager for flight fare tracker.

Supports persistent storage across GitHub Actions runs via:
1. GitHub Repository Contents API on a dedicated 'bot-state' branch (when GITHUB_TOKEN & GITHUB_REPOSITORY are set)
2. Local JSON files (for local runs or fallback)
"""

from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

BASE_DIR = Path(__file__).resolve().parent

CONFIG_FILE = BASE_DIR / "config.json"
STATE_FILE = BASE_DIR / "last_state.json"
OFFSET_FILE = BASE_DIR / "bot_offset.json"

STATE_BRANCH = "bot-state"

DEFAULT_CONFIG: dict[str, Any] = {
    "is_paused": False,
    "routes": [],
    "discount_hints": {},
    "last_checked_at": None,
}

AIRPORTS: dict[str, str] = {
    "BLR": "Bengaluru",
    "BOM": "Mumbai",
    "DEL": "Delhi",
    "CCU": "Kolkata",
    "MAA": "Chennai",
    "HYD": "Hyderabad",
    "IXB": "Bagdogra",
    "DBR": "Darbhanga",
    "PXN": "Purnia",
    "PAT": "Patna",
    "PNQ": "Pune",
    "GOI": "Goa (Dabolim)",
    "GOX": "Goa (Mopa)",
    "COK": "Kochi",
    "AMD": "Ahmedabad",
    "JAI": "Jaipur",
    "LKO": "Lucknow",
    "GAU": "Guwahati",
    "TRV": "Thiruvananthapuram",
    "IXC": "Chandigarh",
    "BBI": "Bhubaneswar",
    "IDR": "Indore",
    "NAG": "Nagpur",
    "VNS": "Varanasi",
    "IXR": "Ranchi",
    "SXR": "Srinagar",
    "ATQ": "Amritsar",
    "VTZ": "Visakhapatnam",
    "BDQ": "Vadodara",
    "UDR": "Udaipur",
    "IXZ": "Port Blair",
    "BHO": "Bhopal",
    "IXA": "Agartala",
    "IXE": "Mangalore",
    "CJB": "Coimbatore",
    "TRZ": "Tiruchirappalli",
    "IXM": "Madurai",
    "STV": "Surat",
    "RPR": "Raipur",
    "IXJ": "Jammu",
    "IXL": "Leh",
    "DED": "Dehradun",
    "GWL": "Gwalior",
}

_sha_cache: dict[str, str] = {}


def get_airport_name(code: str) -> str:
    """Return friendly name with code, e.g. 'Bengaluru (BLR)', or just code."""
    code_upper = code.strip().upper()
    name = AIRPORTS.get(code_upper)
    return f"{name} ({code_upper})" if name else code_upper


def _get_github_env() -> tuple[str | None, str | None, str]:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    api_url = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    return token, repo, api_url


def _ensure_remote_branch(token: str, repo: str, api_url: str) -> bool:
    """Ensure bot-state branch exists on remote GitHub repo."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    # Check if branch exists
    resp = requests.get(
        f"{api_url}/repos/{repo}/git/ref/heads/{STATE_BRANCH}",
        headers=headers,
        timeout=15,
    )
    if resp.status_code == 200:
        return True

    # Branch doesn't exist, create it from default branch ref
    repo_info = requests.get(
        f"{api_url}/repos/{repo}",
        headers=headers,
        timeout=15,
    )
    if repo_info.status_code != 200:
        return False

    default_branch = repo_info.json().get("default_branch", "main")
    ref_resp = requests.get(
        f"{api_url}/repos/{repo}/git/ref/heads/{default_branch}",
        headers=headers,
        timeout=15,
    )
    if ref_resp.status_code != 200:
        return False

    commit_sha = ref_resp.json().get("object", {}).get("sha")
    if not commit_sha:
        return False

    create_resp = requests.post(
        f"{api_url}/repos/{repo}/git/refs",
        headers=headers,
        json={"ref": f"refs/heads/{STATE_BRANCH}", "sha": commit_sha},
        timeout=15,
    )
    return create_resp.status_code in (200, 201)


def _load_remote_file(filename: str) -> tuple[dict | None, str | None]:
    token, repo, api_url = _get_github_env()
    if not token or not repo:
        return None, None

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url = f"{api_url}/repos/{repo}/contents/{filename}?ref={STATE_BRANCH}"
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            sha = data.get("sha")
            content_b64 = data.get("content", "")
            raw = base64.b64decode(content_b64).decode("utf-8")
            _sha_cache[filename] = sha
            return json.loads(raw), sha
        if resp.status_code == 404:
            return None, None
    except Exception as exc:  # noqa: BLE001
        print(f"[state_manager] Warning reading remote {filename}: {exc}")
    return None, None


def _save_remote_file(filename: str, payload_dict: dict, commit_msg: str) -> bool:
    token, repo, api_url = _get_github_env()
    if not token or not repo:
        return False

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Ensure branch exists
    _ensure_remote_branch(token, repo, api_url)

    content_str = json.dumps(payload_dict, indent=2)
    content_b64 = base64.b64encode(content_str.encode("utf-8")).decode("utf-8")

    # Get latest sha if not cached
    sha = _sha_cache.get(filename)
    if not sha:
        try:
            get_resp = requests.get(
                f"{api_url}/repos/{repo}/contents/{filename}?ref={STATE_BRANCH}",
                headers=headers,
                timeout=15,
            )
            if get_resp.status_code == 200:
                sha = get_resp.json().get("sha")
                _sha_cache[filename] = sha
        except Exception:
            pass

    body: dict[str, Any] = {
        "message": commit_msg,
        "content": content_b64,
        "branch": STATE_BRANCH,
    }
    if sha:
        body["sha"] = sha

    try:
        url = f"{api_url}/repos/{repo}/contents/{filename}"
        put_resp = requests.put(url, headers=headers, json=body, timeout=20)
        if put_resp.status_code in (200, 201):
            _sha_cache[filename] = put_resp.json().get("content", {}).get("sha")
            return True
        if put_resp.status_code == 409:
            # Conflict: fetch latest sha and retry once
            get_resp = requests.get(
                f"{url}?ref={STATE_BRANCH}",
                headers=headers,
                timeout=15,
            )
            if get_resp.status_code == 200:
                body["sha"] = get_resp.json().get("sha")
                retry_resp = requests.put(url, headers=headers, json=body, timeout=20)
                if retry_resp.status_code in (200, 201):
                    _sha_cache[filename] = retry_resp.json().get("content", {}).get("sha")
                    return True
        print(f"[state_manager] Failed to save remote {filename}: {put_resp.status_code} {put_resp.text}")
    except Exception as exc:  # noqa: BLE001
        print(f"[state_manager] Exception saving remote {filename}: {exc}")
    return False


# --- Configuration ---

def load_config() -> dict[str, Any]:
    """Load configuration from remote bot-state branch or local file."""
    remote_data, _ = _load_remote_file("config.json")
    if remote_data is not None:
        CONFIG_FILE.write_text(json.dumps(remote_data, indent=2))
        return remote_data

    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass

    # Save default config if none exists
    save_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(config: dict[str, Any]) -> None:
    """Save configuration locally and to remote GitHub branch if available."""
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    _save_remote_file("config.json", config, "Update flight fare tracker config")


# --- State (last fares) ---

def load_state() -> dict[str, Any]:
    """Load last observed flight fares state."""
    remote_data, _ = _load_remote_file("last_state.json")
    if remote_data is not None:
        STATE_FILE.write_text(json.dumps(remote_data, indent=2))
        return remote_data

    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_state(state: dict[str, Any]) -> None:
    """Save flight fares state locally and to remote."""
    STATE_FILE.write_text(json.dumps(state, indent=2))
    _save_remote_file("last_state.json", state, "Update flight fares state")


# --- Telegram Bot Offset ---

def load_offset() -> int:
    """Load Telegram update offset."""
    remote_data, _ = _load_remote_file("bot_offset.json")
    if remote_data is not None:
        OFFSET_FILE.write_text(json.dumps(remote_data, indent=2))
        return int(remote_data.get("offset", 0))

    if OFFSET_FILE.exists():
        try:
            data = json.loads(OFFSET_FILE.read_text())
            return int(data.get("offset", 0))
        except Exception:
            pass
    return 0


def save_offset(offset: int) -> None:
    """Save Telegram update offset."""
    data = {"offset": offset}
    OFFSET_FILE.write_text(json.dumps(data, indent=2))
    _save_remote_file("bot_offset.json", data, "Update Telegram bot offset")


# --- Date & Route Utilities ---

def parse_date(date_str: str) -> date | None:
    """Parse flexible date strings: YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY."""
    date_str = date_str.strip()
    formats = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"]
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def expand_route_dates(route: dict[str, Any], max_days: int = 14) -> list[str]:
    """Expand a route config into a sorted list of YYYY-MM-DD date strings."""
    if "dates" in route and isinstance(route["dates"], list) and route["dates"]:
        # Direct list of dates
        valid = []
        for d in route["dates"]:
            p = parse_date(str(d))
            if p:
                valid.append(p.strftime("%Y-%m-%d"))
        return sorted(list(set(valid)))

    start_str = route.get("start_date")
    if not start_str:
        return []

    s_date = parse_date(start_str)
    if not s_date:
        return []

    end_str = route.get("end_date")
    e_date = parse_date(end_str) if end_str else s_date
    if not e_date or e_date < s_date:
        e_date = s_date

    # Bound number of days to max_days to prevent runaway queries
    delta = (e_date - s_date).days
    if delta >= max_days:
        e_date = s_date + timedelta(days=max_days - 1)

    result = []
    curr = s_date
    while curr <= e_date:
        result.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)

    return result
