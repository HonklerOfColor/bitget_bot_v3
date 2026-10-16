#!/usr/bin/env python3
"""
V3 SNIPER BOT — Orderbuch-/Tape-Sniper
Vollständige Signal-Logik (Imbalance + Sweep + Trend)
"""

import time
import sys
import os
import json
from datetime import datetime
from loguru import logger
import config
from bitget_client import (
    get_orderbook,
    get_recent_trades,
    get_candles,
    get_ticker,
    get_balance,
    get_position,
    get_symbol_info,
    place_futures_order,
    place_limit_order_post_only,
    get_order_detail,
    cancel_order,
    set_position_sl_tp,
    set_leverage,
)

# ============================================================
# Logging Setup
# ============================================================
logger.remove()
logger.add(
    sys.stdout,
    level=config.LOG_LEVEL,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}"
)
logger.add(
    config.LOG_FILE,
    level=config.LOG_LEVEL,
    rotation="10 MB",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}"
)

logger.info("🚀 V3 Sniper Bot gestartet (Demo-Modus)")

# ============================================================
# Telegram Notify (nur Benachrichtigung, keine Trades)
# ============================================================
import urllib.request as _urlreq

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8610878816:AAEEXwGlEUed0wP4PJdDFaKGnppWLyuUKsk")
TG_CHAT_ID = "507397874"

def tg_send(text: str) -> None:
    """Sendet eine Telegram-Nachricht an den User."""
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"})
        req = _urlreq.Request(url, data=data.encode(), headers={"Content-Type": "application/json"})
        _urlreq.urlopen(req, timeout=10)
    except Exception as e:
        logger.error(f"Telegram Fehler: {e}")

# ============================================================
# Hilfsfunktionen
# ============================================================

def calculate_imbalance(orderbook: dict, depth: int = 10) -> float:
    """
    Berechnet Bid/Ask-Imbalance über die Top-N Levels.
    > 1.0 = mehr Bid-Volumen (bullisch)
    < 1.0 = mehr Ask-Volumen (bearisch)
    Unterstützt beide Formate:
    - normalisiert: {'bids': [...], 'asks': [...]}
    - roh (v3):    {'data': {'b': [...], 'a': [...]}}
    """
    data = orderbook.get("data", orderbook)
    bids = data.get("bids") or data.get("b") or []
    asks = data.get("asks") or data.get("a") or []

    bids = bids[:depth]
    asks = asks[:depth]

    bid_vol = sum(float(b[1]) for b in bids)
    ask_vol = sum(float(a[1]) for a in asks)

    if ask_vol == 0:
        return 999.0
    return bid_vol / ask_vol


# State für Sweep-Erkennung über mehrere Polls
_sweep_state = {}  # symbol → {"below_bid": bool, "above_ask": bool, "since": ts}

# ROE-Trailing State (wie V1)
_peak_roe = {}         # symbol → höchster ROE%
_last_pnl_check = {}   # symbol → letzter Trailing-Check (ts)

def detect_sweep(symbol: str, lookback: int = 3) -> dict:
    """
    State-basierte Sweep-Detection über mehrere Polls:
    - Preis unter Best-Bid (Stop-Jagd unten) → merken
    - Preis wieder über Best-Bid → BULLISH SWEEP erkannt
    - Preis über Best-Ask (Stop-Jagd oben) → merken
    - Preis wieder unter Best-Ask → BEARISH SWEEP erkannt
    """
    global _sweep_state

    ob = get_orderbook(symbol, depth=5)
    if ob.get("code") != "00000":
        return {"signal": None, "reason": "no_orderbook"}

    data = ob.get("data", ob)
    bids = data.get("bids") or data.get("b") or []
    asks = data.get("asks") or data.get("a") or []

    if not bids or not asks:
        return {"signal": None, "reason": "empty_orderbook"}

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])

    # Aktuellen Marktpreis holen (mid oder letzter Trade)
    tape = get_recent_trades(symbol, limit=5)
    trades = tape.get("data") or [] if tape.get("code") == "00000" else []
    if not trades:
        return {"signal": None, "reason": "no_tape"}
    last_px = float(trades[0].get("price", 0))

    st = _sweep_state.get(symbol, {"below_bid": False, "above_ask": False, "since": time.time()})
    now = time.time()

    # Zustand aktualisieren
    if last_px < best_bid:
        st["below_bid"] = True
        st["since"] = now
    if last_px > best_ask:
        st["above_ask"] = True
        st["since"] = now

    # Sweep feuern wenn Preis zurückkehrt
    if st["below_bid"] and last_px >= best_bid:
        _sweep_state[symbol] = {"below_bid": False, "above_ask": False, "since": now}
        return {"signal": "LONG", "reason": f"Bullish Sweep zurück über Bid {best_bid:.2f}"}

    if st["above_ask"] and last_px <= best_ask:
        _sweep_state[symbol] = {"below_bid": False, "above_ask": False, "since": now}
        return {"signal": "SHORT", "reason": f"Bearish Sweep zurück unter Ask {best_ask:.2f}"}

    # State-Timeout: nach 60s ohne Bestätigung zurücksetzen
    if now - st["since"] > 60:
        st["below_bid"] = False
        st["above_ask"] = False

    _sweep_state[symbol] = st
    return {"signal": None, "reason": "no_sweep"}


def get_trend_filter(symbol: str) -> str:
    """
    1H EMA(20) Trend-Filter.
    LONG  → Preis > EMA
    SHORT → Preis < EMA
    """
    candles = get_candles(symbol, "1H", 25)
    if not candles.get("data"):
        return "NEUTRAL"

    closes = [float(c[4]) for c in candles["data"]]
    ema = sum(closes[-20:]) / 20
    last_price = closes[-1]

    if last_price > ema:
        return "LONG"
    elif last_price < ema:
        return "SHORT"
    return "NEUTRAL"


# ============================================================
# Trading-Funktionen (ATR, Size, Entry, SL/TP)
# ============================================================

def get_atr(symbol: str, period: int = 14) -> float:
    """Berechnet den ATR (Average True Range) aus 1H-Kerzen."""
    candles = get_candles(symbol, "1H", period + 1)
    if not candles.get("data") or len(candles["data"]) < period + 1:
        return 0.0

    trs = []
    for i in range(1, len(candles["data"])):
        high = float(candles["data"][i][2])
        low = float(candles["data"][i][3])
        prev_close = float(candles["data"][i - 1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    return sum(trs[-period:]) / period


def get_price_place(symbol: str) -> int:
    """pricePlace (Preis-Dezimalstellen) aus den Contracts — PFLICHT für Limit-Preise."""
    try:
        info = get_symbol_info(symbol)
        if info.get("code") == "00000" and info.get("data"):
            return int(info["data"][0].get("pricePlace", 2))
    except Exception:
        pass
    return 2


def calculate_size(balance: float, entry: float, sl: float, leverage: int, risk_pct: float) -> float:
    """
    Berechnet die Positionsgröße:
    size = balance × risk_pct × leverage / (entry − SL)
    """
    risk_dist = abs(entry - sl)
    if risk_dist <= 0:
        return 0.0
    return (balance * risk_pct * leverage) / risk_dist


def get_open_positions() -> list:
    """Liefert alle offenen Positionen (ohne PIPPIN)."""
    raw = get_position()
    positions = []
    if raw.get("code") == "00000":
        for p in raw.get("data") or []:
            if float(p.get("total", 0)) > 0 and p.get("symbol") != "PIPPINUSDT":
                positions.append(p)
    return positions


def setup_leverage(symbol: str, leverage: int) -> None:
    """Setzt Hebel für LONG+SHORT auf der Exchange (muss VOR jeder Order passieren!)."""
    for hold_side in ["long", "short"]:
        try:
            r = set_leverage(symbol, leverage, hold_side)
            if r.get("code") == "00000":
                logger.info(f"  ⚙️  Hebel {leverage}x gesetzt ({symbol} {hold_side})")
            else:
                logger.warning(f"  ⚙️  Hebel-Setup {symbol} {hold_side}: {r.get('msg')}")
        except Exception as e:
            logger.warning(f"  ⚙️  Hebel-Exception {symbol} {hold_side}: {e}")


def trailing_sl(positions: list) -> None:
    """
    ROE-basiertes Trailing (V1-Logik):
    - Aktiv ab ROE_TRAIL_START (3% Peak-ROE)
    - SL wird 2% unter Peak-ROE nachgeführt (ROE_TRAIL_DIST)
    - Läuft alle 10s pro Position
    """
    global _peak_roe, _last_pnl_check

    for pos in positions:
        symbol = pos.get("symbol")
        if symbol == "PIPPINUSDT":
            continue
        try:
            now = time.time()
            last = _last_pnl_check.get(symbol, 0)
            if now - last < 10:
                continue
            _last_pnl_check[symbol] = now

            hold_side = pos.get("holdSide", "long")
            entry = float(pos.get("openPriceAvg", 0))
            mark = float(pos.get("markPrice", 0))
            size = float(pos.get("total", 0))
            pnl = float(pos.get("unrealizedPL", 0))
            margin = float(pos.get("marginSize", 0))

            if entry <= 0 or margin <= 0 or size <= 0:
                continue

            roe_pct = (pnl / margin) * 100

            # Peak-ROE aktualisieren
            if roe_pct > _peak_roe.get(symbol, -9999):
                _peak_roe[symbol] = roe_pct

            # Trailing aktiv ab ROE_TRAIL_START (3%), SL 2% unter Peak
            if _peak_roe.get(symbol, 0) >= config.ROE_TRAIL_START * 100:
                target_roe = _peak_roe[symbol] - config.ROE_TRAIL_DIST * 100
                pnl_target = target_roe / 100 * margin
                current_sl = float(pos.get("stopLoss") or 0)

                if hold_side == "short":
                    new_sl = round(entry - pnl_target / size, 3)
                    # SL näher an den Markt bringen (nur enger, nie weiter weg)
                    if new_sl > mark and (current_sl == 0 or new_sl < current_sl):
                        tp = float(pos.get("takeProfit") or 0)
                        r = set_position_sl_tp(symbol, "short", new_sl, tp)
                        logger.info(f"🔒 {symbol} SHORT: Trailing-SL → {new_sl} (Peak={_peak_roe[symbol]:.1f}%, ROE={roe_pct:.1f}%)")
                else:  # long
                    new_sl = round(entry + pnl_target / size, 3)
                    if new_sl < mark and (current_sl == 0 or new_sl > current_sl):
                        tp = float(pos.get("takeProfit") or 0)
                        r = set_position_sl_tp(symbol, "long", new_sl, tp)
                        logger.info(f"🔒 {symbol} LONG: Trailing-SL → {new_sl} (Peak={_peak_roe[symbol]:.1f}%, ROE={roe_pct:.1f}%)")
        except Exception as e:
            logger.debug(f"  ⚠️ Trailing {symbol}: {e}")


def open_trade(symbol: str, side: str, leverage: int) -> dict:
    """
    Öffnet einen Trade bei Signal.
    side = "LONG" oder "SHORT"
    DRY_RUN=True → nur Log/Telegram, keine echte Order.
    """
    logger.success(f"🎯 SIGNAL: {symbol} {side} — {leverage}x")

    # ⚙️ WICHTIG: Hebel VOR der Order setzen (sonst Exchange-Default!)
    setup_leverage(symbol, leverage)

    # Marktpreis + ATR
    ticker = get_ticker(symbol)
    if ticker.get("code") != "00000":
        return {"ok": False, "reason": "no_ticker"}
    price = float(ticker["data"][0]["lastPr"])
    atr = get_atr(symbol)

    if atr <= 0:
        return {"ok": False, "reason": "no_atr"}

    # SL/TP basierend auf ATR
    sl_dist = config.SL_ATR_MULT * atr
    tp_dist = config.TP_ATR_MULT * atr

    if side == "LONG":
        sl = price - sl_dist
        tp = price + tp_dist
    else:
        sl = price + sl_dist
        tp = price - tp_dist

    # Kontostand + Size
    bal_raw = get_balance()
    balance = 0.0
    if bal_raw.get("code") == "00000" and bal_raw.get("data"):
        balance = float(bal_raw["data"][0].get("available", 0))

    size = calculate_size(balance, price, sl, leverage, config.RISK_PCT)
    if size <= 0:
        return {"ok": False, "reason": "size_zero"}

    # Size auf 2 Dezimalstellen runden (Bitget checkScale=2 für min. Orders)
    size = round(size, 2)
    if size <= 0:
        return {"ok": False, "reason": "size_too_small"}

    logger.info(f"  Entry {price:.2f} | SL {sl:.2f} | TP {tp:.2f} | Size {size} | Balance {balance:.2f}")

    if config.DRY_RUN:
        # DRY-RUN: nur simulieren
        msg = (
            f"🎯 <b>V3 Sniper Signal → Trade (DRY-RUN)</b>\n"
            f"<b>{symbol}</b> → <b>{side}</b> {leverage}x\n"
            f"Entry: {price:.2f} | SL: {sl:.2f} | TP: {tp:.2f}\n"
            f"Size: {size} | ATR: {atr:.2f}"
        )
        tg_send(msg)
        return {"ok": True, "dry_run": True, "entry": price, "sl": sl, "tp": tp, "size": size}

    # ECHTER TRADE (nur wenn DRY_RUN=False!)
    # side: buy/sell (Bitget), tradeSide: open/close (Bitget v2!)
    # LONG  → side=buy,  tradeSide=open
    # SHORT → side=sell, tradeSide=open
    buy_sell = "buy" if side == "LONG" else "sell"
    hold_side = side.lower()

    if config.MAKER_ENTRY:
        # ── Maker-Entry (MC-Finding 14.09.): Post-Only Limit statt IOC-Market ──
        # Spart ~⅓ der Fees (Einstieg 0,06% Taker → 0,02% Maker).
        # Füllt er nicht innerhalb MAKER_TIMEOUT_S, wird das Signal verworfen.
        ob = get_orderbook(symbol, depth=5)
        data = ob.get("data", {}) if ob.get("code") == "00000" else {}
        bids = data.get("bids") or data.get("b") or []
        asks = data.get("asks") or data.get("a") or []
        if not bids or not asks:
            logger.warning(f"  ⚠️ Maker: kein Orderbuch ({ob.get('msg')}) — überspringe")
            return {"ok": False, "reason": "orderbook leer"}
        pp = get_price_place(symbol)
        limit_px = round(float(bids[0][0]) if side == "LONG" else float(asks[0][0]), pp)
        order = place_limit_order_post_only(symbol, buy_sell, "open", size, limit_px)
        if order.get("code") != "00000":
            logger.error(f"Post-Only-Order fehlgeschlagen: {order.get('msg')}")
            return {"ok": False, "reason": order.get("msg")}
        _od = order.get("data") or {}
        order_id = _od.get("orderId") if isinstance(_od, dict) else str(_od)
        if not order_id:
            logger.error("Post-Only-Order ohne orderId zurückgekommen")
            return {"ok": False, "reason": "no_order_id"}
        logger.info(f"  📄 Post-Only Entry {symbol} {side} {size} @ {limit_px} (ID {order_id}) — warte auf Fill (max {config.MAKER_TIMEOUT_S}s)")

        waited = 0
        while waited < config.MAKER_TIMEOUT_S:
            time.sleep(3)
            waited += 3
            det = get_order_detail(symbol, order_id)
            d = det.get("data") or {}
            if isinstance(d, list):
                d = d[0] if d else {}
            state = str(d.get("state", "")).lower()
            if state == "filled" or state in ("canceled", "cancelled"):
                break

        # Offenen Rest stornieren (Timeout oder Partial-Fill)
        try:
            cancel_order(symbol, order_id)
        except Exception:
            pass
        time.sleep(1)

        # Tatsächliche Position (= Fill) prüfen — Size kann partial sein
        raw_pos = get_position(symbol)
        pos_row = None
        for r in (raw_pos.get("data") or []):
            if str(r.get("symbol")) == symbol and float(r.get("total", 0) or 0) > 0:
                pos_row = r
                break
        if not pos_row:
            logger.info(f"  ⏭️ Maker nicht gefüllt ({waited}s) — Signal verworfen")
            return {"ok": False, "reason": "maker_unfilled"}

        entry_avg = float(pos_row.get("openPriceAvg") or limit_px)
        filled_size = float(pos_row.get("total", 0) or 0)
        # SL/TP auf den ECHTEN Fill-Preis rechnen
        if side == "LONG":
            sl = entry_avg - sl_dist
            tp = entry_avg + tp_dist
        else:
            sl = entry_avg + sl_dist
            tp = entry_avg - tp_dist
        set_position_sl_tp(symbol, hold_side, sl, tp)
        logger.info(f"✅ Maker-Entry gefüllt: {symbol} {side} {filled_size} @ {entry_avg} (SL {sl:.2f} / TP {tp:.2f})")
        return {"ok": True, "entry": entry_avg, "sl": sl, "tp": tp, "size": filled_size, "maker": True}

    # Fallback: IOC-Market (alte Methode, per config.MAKER_ENTRY=False aktivierbar)
    order = place_futures_order(symbol, buy_sell, "open", size)
    if order.get("code") != "00000":
        logger.error(f"Order fehlgeschlagen: {order.get('msg')}")
        return {"ok": False, "reason": order.get("msg")}

    # Exchange-SL/TP auf der GESAMTEN Position setzen (entire, V1-Methode)
    set_position_sl_tp(symbol, hold_side, sl, tp)

    logger.info(f"✅ Order platziert: {symbol} {side} {size} @ {price}")
    return {"ok": True, "entry": price, "sl": sl, "tp": tp, "size": size}


# ============================================================
# Hauptloop
# ============================================================

def run():
    logger.info("Starte Sniper-Loop (DRY_RUN=True → keine Orders)")
    warn_cooldown = {}  # symbol → letzter Warnungs-Zeitstempel

    # ⚙️ Startup: Hebel für alle Symbole vorbereiten (LONG+SHORT)
    for sym_cfg in config.SYMBOLS:
        setup_leverage(sym_cfg["symbol"], sym_cfg["leverage"])
        time.sleep(0.5)

    while True:
        try:
            for sym_cfg in config.SYMBOLS:
                symbol = sym_cfg["symbol"]

                # 1. Orderbuch
                ob = get_orderbook(symbol, depth=15)
                if ob.get("code") != "00000":
                    # Warnung nur alle 5 Minuten pro Symbol (Log-Spam vermeiden)
                    now = time.time()
                    if now - warn_cooldown.get(symbol, 0) > 300:
                        logger.warning(f"{symbol} Orderbook Fehler: {ob.get('msg')}")
                        warn_cooldown[symbol] = now
                    continue

                imbalance = calculate_imbalance(ob, depth=10)

                # 2. Trend-Filter
                trend = get_trend_filter(symbol)

                # 3. Sweep-Detection (Tick-basiert, nutzt intern Orderbuch+Tape)
                sweep = detect_sweep(symbol, lookback=config.SWEEP_LOOKBACK)

                # 5. Finale Signal-Logik
                signal = None
                reason = ""

                # 🕐 Handelsfenster-Sperre: Verluststunden 2-4/14/17/20/22 Uhr
                # (Simulation über 1577 Trades: 10-13 Uhr war GUT, diese Stunden = Verlust)
                _h = time.localtime().tm_hour
                if _h not in (2, 3, 4, 14, 17, 20, 22):
                    # Learning 26.08. (1577 Trades): SHORTs −231 USDT → NUR LONG
                    # LONG Signal
                    if (imbalance > config.IMBALANCE_RATIO_LONG and
                        trend == "LONG" and
                        sweep.get("signal") == "LONG"):
                        signal = "LONG"
                        reason = f"Imbalance {imbalance:.2f} + Sweep LONG + Trend LONG"
                    # SHORT deaktiviert (LONG_ONLY)
                    else:
                        signal = None
                else:
                    reason = f"Handelsfenster gesperrt ({_h}:00)"

                # Logging + Trade bei Signal
                if signal:
                    logger.success(f"{symbol} → {signal} | {reason}")

                    # Positions-Check: max. 1 pro Symbol, global max. MAX_POSITIONS
                    open_positions = get_open_positions()
                    sym_has_pos = any(p.get("symbol") == symbol for p in open_positions)
                    if sym_has_pos:
                        logger.info(f"  ⏭️  {symbol} hat bereits eine offene Position — überspringe")
                        continue
                    if len(open_positions) >= config.MAX_POSITIONS:
                        logger.info(f"  ⏭️  MAX_POSITIONS erreicht ({config.MAX_POSITIONS}) — überspringe")
                        continue

                    # Trade öffnen (DRY_RUN=True → nur Simulation + Telegram)
                    result = open_trade(symbol, signal, sym_cfg["leverage"])
                    if not result.get("ok"):
                        logger.warning(f"  Trade nicht ausgeführt: {result.get('reason')}")
                else:
                    logger.debug(
                        f"{symbol} | Imb={imbalance:.2f} | "
                        f"Trend={trend} | Sweep={sweep.get('signal')} | Kein Signal"
                    )

            # ROE-Trailing: offene Positionen überwachen (ab 3% Peak-ROE SL nachführen)
            try:
                open_positions = get_open_positions()
                if open_positions:
                    trailing_sl(open_positions)
            except Exception as e:
                logger.debug(f"  ⚠️ Trailing-Check: {e}")

            time.sleep(3)  # 3 Sekunden Takt

        except KeyboardInterrupt:
            logger.warning("Bot manuell gestoppt")
            break
        except Exception as e:
            import traceback
            logger.error(f"Fehler im Hauptloop: {e}")
            logger.error(traceback.format_exc())
            time.sleep(5)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        import traceback
        logger.error(f"FATAL: {e}")
        logger.error(traceback.format_exc())
        sys.exit(1)