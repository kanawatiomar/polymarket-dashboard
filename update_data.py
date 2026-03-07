"""
update_data.py — Fetches live Polymarket positions and writes data.json.
Run by cron every 5 minutes; auto-commits and pushes to GitHub Pages.
"""
import json, subprocess, sys, os, httpx
from pathlib import Path
from datetime import datetime, timezone

# --- Config ---
BASE      = Path(__file__).parent
POLY_DIR  = BASE.parent / "poly-weather-arb"
CREDS     = POLY_DIR / "creds.json"
ENV_FILE  = POLY_DIR / ".env"

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env

env = load_env()
PRIVATE_KEY = env.get("POLY_PRIVATE_KEY","")
WALLET_ADDR = env.get("POLY_ADDRESS","")

# --- Patch httpx (NordVPN handles routing, no proxy needed) ---
import py_clob_client.http_helpers.helpers as http_helpers
http_helpers._http_client = httpx.Client(http2=True, timeout=30, verify=False)

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

creds_data  = json.loads(CREDS.read_text())
api_creds   = ApiCreds(
    api_key=creds_data["apiKey"],
    api_secret=creds_data["secret"],
    api_passphrase=creds_data["passphrase"],
)
client = ClobClient(
    "https://clob.polymarket.com",
    key=PRIVATE_KEY, chain_id=137, creds=api_creds, signature_type=0,
)

def get_balance():
    try:
        result = client.get_balance_allowance(
            params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=0)
        )
        bal = int(result.get("balance", 0))
        return bal / 1e6
    except Exception as e:
        print(f"Balance error: {e}")
        return 0.0

def get_open_orders():
    try:
        raw = client.get_orders()
        if not isinstance(raw, list):
            raw = []
        orders = []
        for o in raw:
            price  = float(o.get("price",  0))
            size   = float(o.get("size",   0))
            filled = float(o.get("size_matched", 0))
            status = o.get("status", "live").lower()
            if status in ("cancelled","canceled","expired"):
                continue
            side = o.get("side","BUY").upper()
            signal = "YES" if side == "BUY" else "NO"
            # Try to get question from asset_id via Gamma
            token_id = o.get("asset_id","")
            question = fetch_question(token_id)
            market_date = extract_date(question)
            orders.append({
                "order_id": o.get("id",""),
                "question": question,
                "signal": signal,
                "price": price,
                "size": size,
                "filled": filled,
                "cost": round(price * size, 4),
                "potential_payout": round(size, 2),
                "status": status,
                "market_date": market_date,
                "edge": None,
                "token_id": token_id,
            })
        return orders
    except Exception as e:
        print(f"Orders error: {e}")
        return []

def get_filled_trades():
    try:
        raw = client.get_trades()
        if not isinstance(raw, list):
            raw = []
        seen = {}
        for t in raw:
            asset = t.get("asset_id","")
            if asset not in seen:
                seen[asset] = {
                    "token_id": asset,
                    "question": fetch_question(asset),
                    "side": t.get("side","BUY"),
                    "price": float(t.get("price",0)),
                    "size": float(t.get("size",0)),
                    "status": "filled",
                }
        filled = []
        for asset, pos in seen.items():
            signal = "YES" if pos["side"].upper() == "BUY" else "NO"
            cost = round(pos["price"] * pos["size"], 4)
            market_date = extract_date(pos["question"])
            filled.append({
                "question": pos["question"],
                "signal": signal,
                "price": pos["price"],
                "size": pos["size"],
                "cost": cost,
                "potential_payout": round(pos["size"], 2),
                "status": "filled",
                "market_date": market_date,
                "token_id": asset,
            })
        return filled
    except Exception as e:
        print(f"Trades error: {e}")
        return []

_q_cache = {}
def fetch_question(token_id):
    if not token_id or token_id in _q_cache:
        return _q_cache.get(token_id, "Loading...")
    try:
        url = f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
        r = httpx.get(url, timeout=8)
        data = r.json()
        if isinstance(data, list) and data:
            q = data[0].get("question","Unknown market")
            _q_cache[token_id] = q
            return q
    except:
        pass
    _q_cache[token_id] = "Unknown market"
    return "Unknown market"

def extract_date(question):
    import re
    m = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})', question or "")
    if not m:
        return None
    month_map = {v:str(i).zfill(2) for i,v in enumerate(
        ['January','February','March','April','May','June',
         'July','August','September','October','November','December'], 1)}
    mo = month_map[m.group(1)]
    day = m.group(2).zfill(2)
    yr = "2026"
    return f"{yr}-{mo}-{day}"

def git_push():
    try:
        os.chdir(BASE)
        subprocess.run(["git","add","data.json"], check=True)
        subprocess.run(["git","commit","-m","auto: update positions"], check=True)
        subprocess.run(["git","push"], check=True)
        print("Pushed to GitHub.")
    except subprocess.CalledProcessError as e:
        print(f"Git error: {e}")

def main():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Updating dashboard...")

    balance   = get_balance()
    open_ord  = get_open_orders()
    filled    = get_filled_trades()

    # Merge: if an open order is also in filled trades, move to filled
    filled_tokens = {f["token_id"] for f in filled}
    open_ord = [o for o in open_ord if o["token_id"] not in filled_tokens]

    invested = sum(o["cost"] for o in open_ord) + sum(f["cost"] for f in filled)
    pnl = sum(
        (f["potential_payout"] - f["cost"] if f["status"] == "won" else -f["cost"] if f["status"] == "lost" else 0)
        for f in filled
    )

    output = {
        "updated"          : datetime.now(timezone.utc).isoformat(),
        "wallet"           : WALLET_ADDR,
        "usdc_balance"     : round(balance, 4),
        "total_invested"   : round(invested, 4),
        "realized_pnl"     : round(pnl, 4),
        "open_orders"      : open_ord,
        "filled_positions" : filled,
    }

    out_path = BASE / "data.json"
    out_path.write_text(json.dumps(output, indent=2))
    print(f"Written {out_path} — {len(open_ord)} open, {len(filled)} filled, balance ${balance:.2f}")

    git_push()

if __name__ == "__main__":
    main()
