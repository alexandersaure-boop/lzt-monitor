#!/usr/bin/env python3
"""
LZT Market Monitor
------------------
Polls lzt.market for new listings matching a saved search and posts them
to Discord via webhook. Only alerts on *new* items (tracked in seen_items.json).

Two run modes:
  - Loop mode  (default): runs forever, polling every POLL_INTERVAL_SECONDS.
  - One-shot   (RUN_ONCE=1 env var): runs a single cycle and exits.
    Used by the GitHub Actions workflow.

Credentials can be supplied either by editing the constants below OR via
environment variables (LZT_API_TOKEN, DISCORD_WEBHOOK_URL, LZT_SEARCH_URL).
Env vars win when both are set.
"""

import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

# ============================================================================
# CONFIGURATION
# ============================================================================
# These are fallbacks used when the corresponding env var is not set.
# For GitHub Actions, leave these as-is and set secrets instead.
# For running locally, you can either set env vars or fill these in.

LZT_API_TOKEN = os.environ.get("LZT_API_TOKEN") or "PASTE_YOUR_LZT_API_TOKEN_HERE"
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL") or "PASTE_YOUR_DISCORD_WEBHOOK_URL_HERE"
SEARCH_URL = os.environ.get("LZT_SEARCH_URL") or ("https://lzt.market/steam/dayz/?hours_played[221100]=700&nsb=1&daybreak=30&order_by=price_to_up#title")

# Loop-mode poll interval. Ignored in RUN_ONCE mode.
POLL_INTERVAL_SECONDS = 120

# If set to "1", run a single cycle and exit (used by GitHub Actions).
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"

STATE_FILE = "seen_items.json"
LOG_FILE = "monitor.log"

# Target Steam app id used for extracting per-game hours into the Discord embed
TARGET_APP_ID = 221100

# ============================================================================

API_BASE = "https://api.lzt.market"
USER_AGENT = "lzt-monitor/1.1 (+personal use)"

# --- graceful Ctrl+C ---
_running = True
def _sigint(signum, frame):
    global _running
    _running = False
    print("\n[!] Shutdown requested — finishing current cycle…")
signal.signal(signal.SIGINT, _sigint)


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------- state ---------------------------------------------------------

def load_state() -> dict:
    p = Path(STATE_FILE)
    if not p.exists():
        return {"seen": [], "initialized": False}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):       # legacy format
            return {"seen": data, "initialized": True}
        return data
    except (json.JSONDecodeError, OSError) as e:
        log(f"⚠ Could not load state ({e}); starting fresh.")
        return {"seen": [], "initialized": False}


def save_state(state: dict) -> None:
    if len(state["seen"]) > 3000:        # cap growth
        state["seen"] = state["seen"][-3000:]
    Path(STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


# ---------- API -----------------------------------------------------------

def build_api_url(search_url: str) -> tuple[str, str]:
    """Convert a lzt.market web URL into the matching API URL + query string."""
    parsed = urlparse(search_url)
    if "lzt.market" not in parsed.netloc and "zelenka.guru" not in parsed.netloc:
        raise ValueError("SEARCH_URL must be a lzt.market (or zelenka.guru) URL")
    path = parsed.path or "/"
    return f"{API_BASE}{path}", parsed.query


def fetch_listings(session: requests.Session, api_url: str, query: str):
    url = f"{api_url}?{query}" if query else api_url
    try:
        r = session.get(url, timeout=30)
    except requests.RequestException as e:
        log(f"⚠ Network error: {e}")
        return None

    if r.status_code == 429:
        log("⚠ Rate-limited (429). Sleeping 30s.")
        time.sleep(30)
        return None
    if r.status_code == 401:
        log("✗ 401 Unauthorized — LZT_API_TOKEN is invalid or expired. Stopping.")
        sys.exit(1)
    if r.status_code == 403:
        log("✗ 403 Forbidden — token is missing the 'market' scope. Stopping.")
        sys.exit(1)
    if r.status_code >= 500:
        log(f"⚠ Server error {r.status_code}. Retrying next cycle.")
        return None
    if not r.ok:
        log(f"⚠ Unexpected {r.status_code}: {r.text[:200]}")
        return None

    try:
        return r.json()
    except ValueError:
        log("⚠ Response was not JSON. Skipping cycle.")
        return None


# ---------- formatting ---------------------------------------------------

def fmt_price(item: dict) -> str:
    p = item.get("price")
    cur = (item.get("price_currency") or "").upper()
    if p is None:
        return "?"
    return f"{p} {cur}".strip()


def extract_target_hours(item: dict) -> str | None:
    """Hours for TARGET_APP_ID. lzt.market returns these as hours directly,
    not as Steam-API-standard minutes. If we ever see a huge value, assume
    someone switched to minutes and convert."""
    sg = item.get("steam_full_games")
    if not isinstance(sg, dict):
        return None
    for container in (sg.get("list"), sg):
        if not isinstance(container, dict):
            continue
        entry = container.get(str(TARGET_APP_ID)) or container.get(TARGET_APP_ID)
        if not isinstance(entry, dict):
            continue
        for field in ("playtime_forever", "hours_played", "hours", "playtime"):
            val = entry.get(field)
            if isinstance(val, (int, float)) and val >= 0:
                # lzt returns hours. Defensive fallback if that ever changes:
                if val > 50000:
                    return f"{int(val) // 60}h"
                return f"{int(val)}h"
    return None


def build_embed(item: dict) -> dict:
    item_id = item.get("item_id")
    title = item.get("title_en") or item.get("title") or "Untitled"
    link = f"https://lzt.market/{item_id}/"

    fields = [{"name": "Price", "value": fmt_price(item), "inline": True}]

    target_hrs = extract_target_hours(item)
    if target_hrs:
        fields.append({"name": f"Hours (app {TARGET_APP_ID})",
                       "value": target_hrs, "inline": True})

    sg = item.get("steam_full_games")
    if isinstance(sg, dict) and sg.get("total"):
        fields.append({"name": "Games", "value": str(sg["total"]), "inline": True})

    country = item.get("steam_country") or item.get("account_country")
    if country:
        fields.append({"name": "Country", "value": str(country), "inline": True})

    last_act = (item.get("steam_last_activity")
                or item.get("account_last_activity")
                or item.get("last_activity"))
    if last_act:
        try:
            fields.append({"name": "Last active",
                           "value": f"<t:{int(last_act)}:R>", "inline": True})
        except (TypeError, ValueError):
            pass

    mafile = item.get("steam_mafile") or item.get("has_mafile")
    if mafile:
        fields.append({"name": "MaFile", "value": "✅", "inline": True})

    desc = (item.get("description") or "").strip().replace("\r", "")
    if len(desc) > 500:
        desc = desc[:500] + "…"

    embed = {
        "title": title[:250],
        "url": link,
        "description": desc,
        "color": 0x00B37E,
        "fields": fields,
        "footer": {"text": f"item_id {item_id}"},
    }
    pub = item.get("published_date")
    if pub:
        try:
            embed["timestamp"] = datetime.fromtimestamp(
                int(pub), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    return embed


def send_discord(webhook: str, items: list[dict]) -> None:
    for start in range(0, len(items), 10):
        chunk = items[start:start + 10]
        payload = {"embeds": [build_embed(it) for it in chunk]}
        if start == 0:
            n = len(items)
            payload["content"] = f"🔔 **{n} new listing{'s' if n != 1 else ''}**"
        try:
            r = requests.post(webhook, json=payload, timeout=15)
            if r.status_code == 429:
                wait = 2.0
                try:
                    wait = float(r.json().get("retry_after", 2)) + 0.5
                except ValueError:
                    pass
                time.sleep(wait)
                r = requests.post(webhook, json=payload, timeout=15)
            if not r.ok:
                log(f"⚠ Discord {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log(f"⚠ Discord webhook error: {e}")


# ---------- one cycle ----------------------------------------------------

def run_cycle(session, api_url: str, query: str, state: dict, seen: set) -> None:
    data = fetch_listings(session, api_url, query)

    if not data or not isinstance(data.get("items"), list):
        return

    items = data["items"]
    new_items = [i for i in items if i.get("item_id") not in seen]

    if not state.get("initialized"):
        for it in items:
            seen.add(it.get("item_id"))
        state["seen"] = list(seen)
        state["initialized"] = True
        save_state(state)
        log(f"  Baseline captured ({len(items)} listings).")
    elif new_items:
        log(f"  🔔 {len(new_items)} new listing"
            f"{'s' if len(new_items) != 1 else ''}")
        for it in new_items:
            log(f"     • {it.get('title_en') or it.get('title')} — "
                f"{fmt_price(it)} — https://lzt.market/{it.get('item_id')}/")
        send_discord(DISCORD_WEBHOOK_URL, new_items)
        for it in new_items:
            seen.add(it.get("item_id"))
        state["seen"] = list(seen)
        save_state(state)
    else:
        log(f"  · no new listings ({len(items)} on page)")


# ---------- main ---------------------------------------------------------

def main() -> None:
    if not LZT_API_TOKEN or "PASTE" in LZT_API_TOKEN:
        print("✗ LZT_API_TOKEN is not set (env var or constant in script).")
        print("  Get yours at: https://lzt.market/account/api")
        sys.exit(1)
    if not DISCORD_WEBHOOK_URL or "PASTE" in DISCORD_WEBHOOK_URL:
        print("✗ DISCORD_WEBHOOK_URL is not set (env var or constant in script).")
        print("  Create one: Discord server → Settings → Integrations → Webhooks")
        sys.exit(1)

    api_url, query = build_api_url(SEARCH_URL)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {LZT_API_TOKEN}",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })

    state = load_state()
    seen = set(state.get("seen", []))

    log(f"▶ Monitoring  {api_url}{'?' + query if query else ''}")
    if RUN_ONCE:
        log("▶ Mode: one-shot (GitHub Actions)")
    else:
        log(f"▶ Mode: loop — interval {POLL_INTERVAL_SECONDS}s")
    if not state.get("initialized"):
        log("▶ First run — will capture current listings as baseline (no alerts).")

    while _running:
        run_cycle(session, api_url, query, state, seen)

        if RUN_ONCE:
            break

        for _ in range(POLL_INTERVAL_SECONDS):
            if not _running:
                break
            time.sleep(1)

    log("▶ Done." if RUN_ONCE else "▶ Stopped.")


if __name__ == "__main__":
    main()
