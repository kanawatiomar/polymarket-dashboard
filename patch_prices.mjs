import { readFileSync, writeFileSync } from 'fs';

const dataPath = new URL('./data.json', import.meta.url).pathname.replace(/^\//, '');

const data = JSON.parse(readFileSync(dataPath, 'utf8'));

async function getPrice(tokenId) {
  if (!tokenId) return null;
  try {
    const r = await fetch(`https://clob.polymarket.com/last-trade-price?token_id=${tokenId}`);
    const d = await r.json();
    const p = parseFloat(d.price || 0);
    if (p > 0) return p;
  } catch {}
  try {
    const r = await fetch(`https://clob.polymarket.com/midpoint?token_id=${tokenId}`);
    const d = await r.json();
    const p = parseFloat(d.mid || 0);
    if (p > 0) return p;
  } catch {}
  return null;
}

const allPositions = [...(data.open_orders || []), ...(data.filled_positions || [])];
for (const pos of allPositions) {
  const cur = await getPrice(pos.token_id);
  pos.current_price = cur;
  if (cur !== null && (pos.filled || 0) > 0) {
    pos.unrealized_pnl = Math.round((cur - pos.price) * pos.filled * 10000) / 10000;
  } else {
    pos.unrealized_pnl = null;
  }
  console.log(`${(pos.label || '').padEnd(35)}  entry=${pos.price?.toFixed(3)}  now=${cur}  upnl=${pos.unrealized_pnl}`);
}

for (const bet of (data.ufc_bets || [])) {
  const cur = await getPrice(bet.token_id);
  bet.current_price = cur;
  if (cur !== null && (bet.shares || 0) > 0) {
    bet.unrealized_pnl = Math.round((cur - bet.price) * bet.shares * 10000) / 10000;
  } else {
    bet.unrealized_pnl = null;
  }
  console.log(`${(bet.fighter || '').padEnd(35)}  entry=${bet.price?.toFixed(3)}  now=${cur}  upnl=${bet.unrealized_pnl}`);
}

writeFileSync(dataPath, JSON.stringify(data, null, 2));
console.log('\ndata.json updated!');
