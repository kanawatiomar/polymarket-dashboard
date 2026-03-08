import httpx, json
from pathlib import Path

data_path = Path(r'C:\Users\kanaw\.openclaw\workspace\polymarket-dashboard\data.json')
data = json.loads(data_path.read_text())

def get_price(token_id):
    if not token_id:
        return None
    try:
        r = httpx.get(f'https://clob.polymarket.com/last-trade-price?token_id={token_id}', timeout=8)
        p = float(r.json().get('price', 0))
        if p > 0:
            return p
    except:
        pass
    try:
        r = httpx.get(f'https://clob.polymarket.com/midpoint?token_id={token_id}', timeout=8)
        p = float(r.json().get('mid', 0))
        if p > 0:
            return p
    except:
        pass
    return None

all_positions = data.get('open_orders', []) + data.get('filled_positions', [])
for pos in all_positions:
    tid = pos.get('token_id')
    cur = get_price(tid)
    pos['current_price'] = cur
    if cur is not None and pos.get('filled', 0) > 0:
        entry = float(pos['price'])
        filled = float(pos['filled'])
        pos['unrealized_pnl'] = round((cur - entry) * filled, 4)
    else:
        pos['unrealized_pnl'] = None
    print(f"{pos['label']:35s}  entry={pos['price']:.3f}  now={cur}  upnl={pos.get('unrealized_pnl')}")

for bet in data.get('ufc_bets', []):
    tid = bet.get('token_id')
    cur = get_price(tid)
    bet['current_price'] = cur
    if cur is not None and bet.get('shares', 0) > 0:
        entry = float(bet['price'])
        shares = float(bet['shares'])
        bet['unrealized_pnl'] = round((cur - entry) * shares, 4)
    else:
        bet['unrealized_pnl'] = None
    print(f"{bet['fighter']:35s}  entry={bet['price']:.3f}  now={cur}  upnl={bet.get('unrealized_pnl')}")

data_path.write_text(json.dumps(data, indent=2))
print('\ndata.json updated!')
