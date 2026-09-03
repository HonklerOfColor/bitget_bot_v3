#!/usr/bin/env python3
"""V3 Smoke-Test — nur lesende Operationen, KEINE Orders."""
import sys
sys.path.insert(0, '/opt/data/bitget_bot_v3')
import config
from bitget_client import (
    get_balance, get_position, get_ticker, get_candles,
    get_orderbook, get_recent_trades, get_symbol_info,
)

print(f"=== V3 SMOKE-TEST (LIVE-Modus: DEMO_MODE={config.DEMO_MODE}, DRY_RUN={config.DRY_RUN}) ===")
print()

# 1. Balance
bal = get_balance()
if bal.get("code") == "00000" and bal.get("data"):
    d = bal["data"][0]
    print(f"✅ Balance: Equity={d.get('equity','?')} Available={d.get('available','?')} USDT")
else:
    print(f"❌ Balance: {bal.get('code')} {bal.get('msg')}")

# 2. Positionen
pos = get_position()
if pos.get("code") == "00000":
    opens = [p for p in (pos.get("data") or []) if float(p.get("total", 0)) > 0]
    print(f"✅ Positionen: {len(opens)} offen")
    for p in opens:
        print(f"   {p.get('symbol')} {p.get('holdSide')} {p.get('total')} PnL={p.get('unrealizedPL')}")
else:
    print(f"❌ Positionen: {pos.get('code')} {pos.get('msg')}")

# 3. Ticker BTC
tick = get_ticker("BTCUSDT")
if tick.get("code") == "00000":
    print(f"✅ Ticker BTC: {tick['data'][0].get('lastPr')}")
else:
    print(f"❌ Ticker: {tick.get('msg')}")

# 4. Candles
cand = get_candles("BTCUSDT", "1H", 3)
print(f"✅ Candles: {len(cand.get('data', []))} Kerzen" if cand.get("code") == "00000" else f"❌ Candles: {cand.get('msg')}")

# 5. Orderbook (v3)
ob = get_orderbook("BTCUSDT", depth=5)
if ob.get("code") == "00000":
    d = ob["data"]
    print(f"✅ Orderbook: {len(d.get('bids',[]))} Bids, {len(d.get('asks',[]))} Asks")
else:
    print(f"❌ Orderbook: {ob.get('code')} {ob.get('msg')}")

# 6. Trades (Tape)
tr = get_recent_trades("BTCUSDT", limit=5)
if tr.get("code") == "00000":
    print(f"✅ Tape: {len(tr.get('data', []))} Trades")
else:
    print(f"❌ Tape: {tr.get('code')} {tr.get('msg')}")

# 7. Symbol-Info (für MIN_SIZE / Preis-Stellen)
info = get_symbol_info("BTCUSDT")
if info.get("code") == "00000" and info.get("data"):
    d = info["data"][0]
    print(f"✅ SymbolInfo BTC: minTradeNum={d.get('minTradeNum')} pricePlace={d.get('pricePlace')} sizeMultiplier={d.get('sizeMultiplier')}")
else:
    print(f"❌ SymbolInfo: {info.get('msg')}")

print()
print("=== SMOKE-TEST FERTIG (keine Orders platziert) ===")
