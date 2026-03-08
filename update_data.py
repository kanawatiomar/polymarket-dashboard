"""
update_data.py — Fetches live Polymarket positions and writes data.json.
Uses our_orders.json as source of truth — looks up each order by ID directly.
Handles both weather arb positions and UFC bets (grouped by fighter).
"""
import json, subprocess, os, httpx, re
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

BASE     = Path(__file__).parent
POLY_DIR = BASE.parent / "poly-weather-arb"
CREDS    = POLY_DIR / "creds.json"
ENV_FILE = POLY_DIR / ".env"

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

env         = load_env()
PRIVATE_KEY = env.get("POLY_PRIVATE_KEY", "")
WALLET_ADDR = env.get("POLY_ADDRESS", "")

import py_clob_client.http_helpers.helpers as http_helpers
http_helpers._http_client = httpx.Client(http2=True, timeout=30, verify=False)

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

creds_data = json.loads(CREDS.read_text())
api_creds  = ApiCreds(
    api_key=creds_data["apiKey"],
    api_secret=creds_data["secret"],
    api_passphrase=creds_data["passphrase"],
)
client = ClobClient(
    "https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=api_creds, signature_type=0,
)

# ─── Helpers ────────────────────────────────────────────────────────────────

_price_cache = {}
def fetch_current_price(token_id):
    """Fetch the latest mid/last-trade price for a token from Polymarket CLOB."""
    if not token_id:
        return None
    if token_id in _price_cache:
        return _price_cache[token_id]
    try:
        r = httpx.get(
            f"https://clob.polymarket.com/last-trade-price?token_id={token_id}",
            timeout=6
        )
        data = r.json()
        price = float(data.get("price", 0))
        if price > 0:
            _price_cache[token_id] = price
            return price
    except:
        pass
    # Fallback: try midpoint from orderbook
    try:
        r = httpx.get(
            f"https://clob.polymarket.com/midpoint?token_id={token_id}",
            timeout=6
        )
        data = r.json()
        price = float(data.get("mid", 0))
        if price > 0:
            _price_cache[token_id] = price
            return price
    except:
        pass
    return None

_q_cache = {}
def fetch_question(token_id):
    if not token_id or token_id in _q_cache:
        return _q_cache.get(token_id, "Unknown market")
    try:
        r = httpx.get(
            f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}",
            timeout=8
        )
        data = r.json()
        if isinstance(data, list) and data:
            q = data[0].get("question", "Unknown market")
            _q_cache[token_id] = q
            return q
    except:
        pass
    _q_cache[token_id] = "Unknown market"
    return "Unknown market"

def extract_date(question):
    m = re.search(
        r'(January|February|March|April|May|June|July|August|September'
        r'|October|November|December)\s+(\d{1,2})',
        question or ""
    )
    if not m:
        return None
    months = ['January','February','March','April','May','June',
              'July','August','September','October','November','December']
    mo = str(months.index(m.group(1)) + 1).zfill(2)
    return f"2026-{mo}-{m.group(2).zfill(2)}"

def parse_ufc_question(question):
    """Extract event, fighter1, fighter2, division, card from UFC market title."""
    # e.g. "UFC 326: Reinier de Ridder vs. Caio Borralho (Middleweight, Main Card)"
    result = {"event": "UFC", "fighter1": "", "fighter2": "", "division": "", "card": ""}
    if not question:
        return result
    # Event (UFC NNN)
    em = re.match(r'(UFC\s+\d+)', question)
    if em:
        result["event"] = em.group(1)
    # Fighters: "X vs. Y" or "X vs Y"
    fm = re.search(r':\s+(.+?)\s+vs\.?\s+(.+?)(?:\s*\(|$)', question)
    if fm:
        result["fighter1"] = fm.group(1).strip()
        result["fighter2"] = fm.group(2).strip()
    # Division and card type
    pm = re.search(r'\(([^,]+),\s*([^)]+)\)', question)
    if pm:
        result["division"] = pm.group(1).strip()
        result["card"]     = pm.group(2).strip()
    return result

def get_balance():
    try:
        result = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=0)
        )
        return int(result.get("balance", 0)) / 1e6
    except Exception as e:
        print(f"Balance error: {e}")
        return 0.0

def build_position(order_id, label=None):
    """Fetch a single weather order by ID and return a position dict."""
    try:
        o = client.get_order(order_id)
    except Exception as e:
        print(f"  Error fetching {order_id[:12]}: {e}")
        return None

    status        = o.get("status", "").upper()
    original_size = float(o.get("original_size") or 0)
    size_matched  = float(o.get("size_matched")  or 0)
    remaining     = max(0.0, original_size - size_matched)
    price         = float(o.get("price") or 0)
    token_id      = o.get("asset_id", "")
    outcome       = o.get("outcome", "Yes")

    question    = fetch_question(token_id)
    market_date = extract_date(question)

    filled_cost   = round(size_matched * price, 4)
    filled_payout = round(size_matched, 2)
    total_cost    = round(original_size * price, 4)
    total_payout  = round(original_size, 2)

    current_price = fetch_current_price(token_id)
    unrealized_pnl = None
    if current_price is not None and size_matched > 0:
        unrealized_pnl = round((current_price - price) * size_matched, 4)

    return {
        "order_id"         : order_id,
        "label"            : label or question[:40],
        "question"         : question,
        "signal"           : outcome,
        "price"            : price,
        "size"             : original_size,
        "filled"           : size_matched,
        "remaining"        : remaining,
        "cost"             : total_cost,
        "potential_payout" : total_payout,
        "filled_cost"      : filled_cost,
        "filled_payout"    : filled_payout,
        "fill_pct"         : round(size_matched / original_size * 100, 1) if original_size else 0,
        "status"           : "live" if status == "LIVE" else "filled" if status == "MATCHED" else status.lower(),
        "market_date"      : market_date,
        "token_id"         : token_id,
        "current_price"    : current_price,
        "unrealized_pnl"   : unrealized_pnl,
    }

def build_ufc_bet(fighter_name, order_ids):
    """Aggregate multiple orders for one UFC fighter into a single bet card."""
    total_shares = 0.0
    total_cost   = 0.0
    status       = "filled"
    question     = ""
    token_id     = ""

    for oid in order_ids:
        try:
            o = client.get_order(oid)
        except Exception as e:
            print(f"  Error fetching UFC order {oid[:12]}: {e}")
            continue

        o_status = o.get("status", "").upper()
        if o_status == "LIVE":
            status = "live"
        elif o_status == "CANCELED" and status != "live":
            status = "cancelled"

        matched  = float(o.get("size_matched") or 0)
        price    = float(o.get("price") or 0)
        total_shares += matched
        total_cost   += matched * price

        if not token_id:
            token_id = o.get("asset_id", "")
        if not question:
            question = fetch_question(token_id)

    if total_shares == 0:
        return None

    avg_price = total_cost / total_shares if total_shares else 0
    meta = parse_ufc_question(question)

    # Determine opponent
    f1, f2 = meta["fighter1"], meta["fighter2"]
    # Which fighter is "ours"? Match by last name fragment
    fighter_last = fighter_name.split()[-1].lower()
    if fighter_last in f1.lower():
        opponent = f2
    elif fighter_last in f2.lower():
        opponent = f1
    else:
        # fallback: use outcome field from last order
        opponent = f1 if fighter_name != f1 else f2

    current_price = fetch_current_price(token_id)
    unrealized_pnl = None
    if current_price is not None and total_shares > 0:
        unrealized_pnl = round((current_price - avg_price) * total_shares, 4)

    return {
        "fighter"          : fighter_name,
        "opponent"         : opponent,
        "event"            : meta["event"],
        "division"         : meta["division"],
        "card"             : meta["card"],
        "question"         : question,
        "price"            : round(avg_price, 4),
        "shares"           : round(total_shares, 2),
        "cost"             : round(total_cost, 4),
        "potential_payout" : round(total_shares, 2),
        "profit_if_win"    : round(total_shares - total_cost, 4),
        "order_ids"        : order_ids,
        "status"           : status,
        "token_id"         : token_id,
        "current_price"    : current_price,
        "unrealized_pnl"   : unrealized_pnl,
    }

def git_push():
    try:
        os.chdir(BASE)
        subprocess.run(["git", "add", "data.json"], check=True)
        subprocess.run(["git", "commit", "-m", "auto: update positions"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("Pushed to GitHub.")
    except subprocess.CalledProcessError as e:
        print(f"Git: {e}")

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Updating dashboard...")

    orders_file = BASE / "our_orders.json"
    if not orders_file.exists():
        print("ERROR: our_orders.json not found")
        return

    our_orders = json.loads(orders_file.read_text())
    balance    = get_balance()

    # Separate weather vs UFC orders
    weather_orders = [o for o in our_orders if o.get("category", "weather") == "weather"]
    ufc_orders_raw = [o for o in our_orders if o.get("category") == "ufc"]

    # ── Weather positions ────────────────────────────────────────────────
    open_orders      = []
    filled_positions = []

    for entry in weather_orders:
        oid   = entry["order_id"]
        label = entry.get("label", "")
        print(f"  Fetching weather: {label} ({oid[:12]}...)")
        pos = build_position(oid, label)
        if not pos:
            continue
        if pos["status"] == "live":
            open_orders.append(pos)
        else:
            filled_positions.append(pos)

    # ── UFC bets ─────────────────────────────────────────────────────────
    # Group by fighter
    ufc_by_fighter = defaultdict(list)
    for entry in ufc_orders_raw:
        fighter = entry.get("fighter", entry.get("label", "Unknown"))
        ufc_by_fighter[fighter].append(entry["order_id"])

    ufc_bets = []
    for fighter, order_ids in ufc_by_fighter.items():
        print(f"  Fetching UFC: {fighter} ({len(order_ids)} orders)")
        bet = build_ufc_bet(fighter, order_ids)
        if bet:
            ufc_bets.append(bet)

    # ── Totals ────────────────────────────────────────────────────────────
    weather_invested = sum(p["cost"] for p in open_orders + filled_positions)
    ufc_invested     = sum(b["cost"] for b in ufc_bets)
    total_invested   = weather_invested + ufc_invested

    realized_pnl = sum(
        (p["filled_payout"] - p["filled_cost"] if p.get("status") == "won"
         else -p["filled_cost"] if p.get("status") == "lost" else 0)
        for p in filled_positions
    ) + sum(
        (b["profit_if_win"] if b.get("status") == "won"
         else -b["cost"] if b.get("status") == "lost" else 0)
        for b in ufc_bets
    )

    output = {
        "updated"          : datetime.now(timezone.utc).isoformat(),
        "wallet"           : WALLET_ADDR,
        "usdc_balance"     : round(balance, 4),
        "total_invested"   : round(total_invested, 4),
        "realized_pnl"     : round(realized_pnl, 4),
        "open_orders"      : open_orders,
        "filled_positions" : filled_positions,
        "ufc_bets"         : ufc_bets,
    }

    (BASE / "data.json").write_text(json.dumps(output, indent=2))
    print(
        f"Written — {len(open_orders)} open, {len(filled_positions)} filled, "
        f"{len(ufc_bets)} UFC bets | balance ${balance:.2f} | invested ${total_invested:.2f}"
    )

    git_push()

if __name__ == "__main__":
    main()
