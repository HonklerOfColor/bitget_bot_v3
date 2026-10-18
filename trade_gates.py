"""Order-Gates vor dem Versand. Kein Netzwerk, kein config-Import."""

from dataclasses import dataclass
import math
from decimal import Decimal, ROUND_DOWN

# Bitget USDT-M Standard (VIP0). Exit über place-pos-tpsl ist ein Trigger → Taker.
MAKER_FEE = 0.0002
TAKER_FEE = 0.0006
# Initial Margin darf das freie Guthaben nicht auffressen (Fee-Puffer).
MARGIN_BALANCE_CAP = 0.98


@dataclass(frozen=True)
class ContractSpec:
    min_trade_num: float
    size_step: float
    min_notional: float
    price_place: int = 2
    volume_place: int = 2


@dataclass(frozen=True)
class OrderDecision:
    allow: bool
    reason: str
    size: float = 0.0
    raw_size: float = 0.0
    notional: float = 0.0
    margin: float = 0.0


def calculate_size(balance: float, entry: float, sl: float, leverage: int, risk_pct: float) -> float:
    """Rohgröße = balance × risk_pct × leverage / |entry − SL|."""
    risk_dist = abs(entry - sl)
    if risk_dist <= 0 or leverage <= 0:
        return 0.0
    return (balance * risk_pct * leverage) / risk_dist


def floor_size(raw: float, step: float) -> float:
    """Auf den Kontrakt-Step abrunden. Niemals aufrunden."""
    if raw <= 0 or step <= 0 or not math.isfinite(raw) or not math.isfinite(step):
        return 0.0
    s = Decimal(str(step))
    if s <= 0:
        return 0.0
    steps = (Decimal(str(raw)) / s).to_integral_value(rounding=ROUND_DOWN)
    return float(steps * s)


def evaluate_entry(
    *,
    entries_paused: bool,
    balance: float,
    price: float,
    sl_price: float,
    tp_price: float,
    leverage: int,
    risk_pct: float,
    spec: ContractSpec,
    entry_is_maker: bool = True,
    maker_fee: float = MAKER_FEE,
    taker_fee: float = TAKER_FEE,
    margin_balance_cap: float = MARGIN_BALANCE_CAP,
) -> OrderDecision:
    """Ob diese Entry-Order raus darf, und mit welcher (abgerundeten) Size.

    Ablehnungsgründe:
    - entries_paused: kein belastbarer Edge nach Fees
    - below_min_size: Rohgröße liegt unter minTradeNum (nicht aufs Minimum aufblasen)
    - below_min_notional: Notional unter minTradeUSDT
    - margin_exceeds_balance: Initial Margin > Cap × freies Guthaben
    - tp_inside_fees: TP-Distanz deckt den Round-Trip (Entry + Taker-Exit) nicht
    """
    if entries_paused:
        return OrderDecision(False, "entries_paused")
    if price <= 0 or leverage <= 0 or balance < 0 or risk_pct < 0:
        return OrderDecision(False, "bad_inputs")
    if spec.size_step <= 0 or spec.min_trade_num <= 0:
        return OrderDecision(False, "bad_contract")

    raw = calculate_size(balance, price, sl_price, leverage, risk_pct)
    size = floor_size(raw, spec.size_step)
    notional = size * price
    margin = notional / leverage
    fields = dict(size=size, raw_size=raw, notional=notional, margin=margin)

    if size + 1e-12 < spec.min_trade_num:
        return OrderDecision(False, "below_min_size", **fields)
    if notional + 1e-9 < spec.min_notional:
        return OrderDecision(False, "below_min_notional", **fields)
    if margin > balance * margin_balance_cap:
        return OrderDecision(False, "margin_exceeds_balance", **fields)

    tp_dist = abs(tp_price - price)
    entry_fee = maker_fee if entry_is_maker else taker_fee
    # Exit bleibt Trigger-Market (Taker), auch wenn der Entry Post-Only ist.
    if tp_dist / price <= entry_fee + taker_fee:
        return OrderDecision(False, "tp_inside_fees", **fields)

    return OrderDecision(True, "ok", **fields)
