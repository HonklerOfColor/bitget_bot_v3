"""
Bitget API Client — Futures (USDT-Perpetual)
Erweitert für V3 Sniper (Orderbuch + Tape)
"""
import time
import hmac
import hashlib
import base64
import json
import socket
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from loguru import logger
import config

BASE_URL = "https://api.bitget.com"

# DNS-Cache + Retry Session
_session = None

def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=3,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
        _session.mount("https://", adapter)
        _session.mount("http://", adapter)
        _session.trust_env = False
    return _session


def _sign(timestamp: str, method: str, path: str, body: str = "") -> str:
    msg = f"{timestamp}{method.upper()}{path}{body}"
    mac = hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode()


def _headers(method: str, path: str, body: str = "") -> dict:
    timestamp = str(int(time.time() * 1000))
    sign = _sign(timestamp, method, path, body)

    h = {
        "ACCESS-KEY": config.API_KEY,
        "ACCESS-SIGN": sign,
        "ACCESS-TIMESTAMP": timestamp,
        "ACCESS-PASSPHRASE": config.PASSPHRASE,
        "Content-Type": "application/json",
        "locale": "en-US",
    }

    if config.DEMO_MODE:
        h["paptrading"] = "1"

    return h


def _get(path: str, params: dict = None) -> dict:
    session = _get_session()

    # WICHTIG: Query-Parameter gehören in die Signatur (Bitget-Anforderung!)
    full_path = path
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        full_path = f"{path}?{qs}"

    url = f"{BASE_URL}{full_path}"
    headers = _headers("GET", full_path)

    try:
        resp = session.get(url, headers=headers, timeout=10)
        return resp.json()
    except Exception as e:
        logger.error(f"GET {path} failed: {e}")
        return {"code": "99999", "msg": str(e)}


def _post(path: str, data: dict) -> dict:
    session = _get_session()
    url = f"{BASE_URL}{path}"
    body = json.dumps(data)
    headers = _headers("POST", path, body)

    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] POST {path} | {data}")
        return {"code": "00000", "msg": "DRY_RUN"}

    try:
        resp = session.post(url, headers=headers, data=body, timeout=10)
        return resp.json()
    except Exception as e:
        logger.error(f"POST {path} failed: {e}")
        return {"code": "99999", "msg": str(e)}


# ============================================================
# Bestehende Funktionen (aus V1)
# ============================================================

def get_candles(symbol: str, granularity: str, limit: int = 100) -> dict:
    params = {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE,
        "granularity": granularity,
        "limit": str(limit)
    }
    return _get("/api/v2/mix/market/candles", params)


def get_ticker(symbol: str) -> dict:
    params = {"symbol": symbol, "productType": config.PRODUCT_TYPE}
    return _get("/api/v2/mix/market/ticker", params)


def get_symbol_info(symbol: str) -> dict:
    params = {"productType": config.PRODUCT_TYPE, "symbol": symbol}
    return _get("/api/v2/mix/market/contracts", params)


def get_balance(margin_coin: str = "USDT") -> dict:
    params = {"productType": config.PRODUCT_TYPE, "marginCoin": margin_coin}
    return _get("/api/v2/mix/account/accounts", params)


def set_leverage(symbol: str, leverage: int, hold_side: str) -> dict:
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] set_leverage {symbol} {leverage}x {hold_side}")
        return {"code": "00000"}
    return _post("/api/v2/mix/account/set-leverage", {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE,
        "marginCoin": config.MARGIN_COIN,
        "leverage": str(leverage),
        "holdSide": hold_side
    })


def get_position(symbol: str = None) -> dict:
    params = {"productType": config.PRODUCT_TYPE}
    if symbol:
        params["symbol"] = symbol
    return _get("/api/v2/mix/position/all-position", params)


def place_futures_order(symbol: str, side: str, trade_side: str, size: float, order_type: str = "market") -> dict:
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] {side.upper()} {trade_side} {size} {symbol}")
        return {"code": "00000"}
    return _post("/api/v2/mix/order/place-order", {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE,
        "marginCoin": config.MARGIN_COIN,
        "marginMode": config.MARGIN_MODE,
        "side": side,
        "tradeSide": trade_side,
        "orderType": order_type,
        "size": str(size),
        "force": "ioc"
    })


def set_position_sl_tp(symbol: str, hold_side: str, sl_price: float, tp_price: float) -> dict:
    """
    Setzt SL/TP auf der GESAMTEN Position (position-level, V1-Methode).
    Nutzt place-pos-tpsl — kein Size-Parameter → deckt 100% ab (entire, kein partial).
    """
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] SET SL={sl_price:.4f} TP={tp_price:.4f} für {symbol} {hold_side}")
        return {"code": "00000"}

    # Preis-Dezimalstellen aus Symbol-Info (checkBDScale-Fehler vermeiden!)
    info = _get("/api/v2/mix/market/contracts", {"symbol": symbol, "productType": config.PRODUCT_TYPE})
    price_place = 2
    if info.get("code") == "00000" and info.get("data"):
        price_place = int(info["data"][0].get("pricePlace", 2))

    sl_rounded = round(sl_price, price_place)
    tp_rounded = round(tp_price, price_place)

    payload = {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE,
        "marginCoin": config.MARGIN_COIN,
        "holdSide": hold_side,
        "stopSurplusTriggerPrice": f"{tp_rounded}",
        "stopSurplusTriggerType": "mark_price",
        "stopLossTriggerPrice": f"{sl_rounded}",
        "stopLossTriggerType": "mark_price",
    }
    result = _post("/api/v2/mix/order/place-pos-tpsl", payload)
    if result.get("code") == "00000":
        logger.info(f"  ✅ pos-tpsl TP={tp_rounded} SL={sl_rounded} ({symbol} {hold_side})")
    else:
        logger.warning(f"  ⚠️  pos-tpsl Fehler: code={result.get('code')} {result.get('msg')}")
    return result


def place_limit_order_post_only(symbol: str, side: str, trade_side: str, size: float, price: float) -> dict:
    """Limit-Order mit Post-Only (Maker) — force=post_only (MC-Finding 14.09.)."""
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] POST-ONLY {side.upper()} {trade_side} {size} {symbol} @ {price}")
        return {"code": "00000"}
    return _post("/api/v2/mix/order/place-order", {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE,
        "marginCoin": config.MARGIN_COIN,
        "marginMode": config.MARGIN_MODE,
        "side": side,
        "tradeSide": trade_side,
        "orderType": "limit",
        "price": str(price),
        "size": str(size),
        "force": "post_only",
    })


def get_order_detail(symbol: str, order_id: str) -> dict:
    """Order-Detail (State/Filled) — für die Maker-Entry-Warteschleife."""
    params = {"symbol": symbol, "productType": config.PRODUCT_TYPE, "orderId": str(order_id)}
    return _get("/api/v2/mix/order/detail", params)


def cancel_order(symbol: str, order_id: str) -> dict:
    """Storniert eine offene Order."""
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] cancel order {order_id} {symbol}")
        return {"code": "00000"}
    return _post("/api/v2/mix/order/cancel-order", {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE,
        "marginCoin": config.MARGIN_COIN,
        "orderId": str(order_id),
    })


def close_all_positions(symbol: str) -> dict:
    if config.DRY_RUN:
        logger.info(f"[DRY-RUN] Close all positions {symbol}")
        return {"code": "00000"}
    return _post("/api/v2/mix/order/close-positions", {
        "symbol": symbol,
        "productType": config.PRODUCT_TYPE
    })


# ============================================================
# NEU für V3 Sniper: Orderbuch & Tape
# ============================================================

def get_orderbook(symbol: str, depth: int = 20) -> dict:
    """
    Holt das Orderbuch (Bid/Ask) für ein Symbol (UTA v3 API).
    depth = Anzahl der Level (max. 1000 bei Bitget).
    Normalisiert die Response auf {'bids': [...], 'asks': [...]}.
    """
    params = {
        "category": config.PRODUCT_TYPE,
        "symbol": symbol,
        "limit": str(depth)
    }
    raw = _get("/api/v3/market/orderbook", params)
    if raw.get("code") != "00000":
        return raw

    data = raw.get("data", {})
    # v3 Format: data.a = Asks (asc), data.b = Bids (desc)
    raw["data"] = {
        "bids": data.get("b", []),
        "asks": data.get("a", []),
        "ts": data.get("ts", ""),
    }
    return raw


def get_recent_trades(symbol: str, limit: int = 50) -> dict:
    """
    Holt die letzten Trades (Tape) für ein Symbol.
    """
    params = {
        "category": config.PRODUCT_TYPE,
        "symbol": symbol,
        "limit": str(limit)
    }
    return _get("/api/v3/market/fills", params)