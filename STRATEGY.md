# V3 SNIPER BOT — Strategie & Parameter

> Stand: 10.08.2026 · LIVE-Modus (DEMO_MODE=False, DRY_RUN=False)
> Arbeitstitel: Orderbuch-/Tape-Sniper

---

## 1. Grundidee

Der V3 jagt **Mikro-Ineffizienzen** im Orderbuch und Tape:
- **Orderbuch-Imbalance** → schiefe Bid/Ask-Volumenverteilung
- **Tick-Sweeps** → Stop-Hunts auf Tick-Ebene (Preis durchbricht Best-Bid/Ask und dreht zurück)
- **Trend-Filter** → nur mit dem 1H-Trend handeln (nie dagegen)

Alle 3 Bedingungen müssen **gleichzeitig** erfüllt sein → blitzschneller Market-Entry.

---

## 2. Signal-Logik (alle 3 Bedingungen = Trade)

### 2.1 Orderbuch-Imbalance
- Verhältnis Bid-Volumen : Ask-Volumen über Top-10-Level
- **LONG-Bias:** Ratio > 1.4 (viel mehr Käufer-Volumen)
- **SHORT-Bias:** Ratio < 0.7 (viel mehr Verkäufer-Volumen)

### 2.2 Tick-Sweep (Tape-Ebene)
- Letzte 15 Trades gegen Best-Bid/Ask geprüft:
  - **Bullish Sweep:** vorheriger Trade unter Best-Bid → letzter wieder darüber (Stop-Jagd unten)
  - **Bearish Sweep:** vorheriger Trade über Best-Ask → letzter wieder darunter (Stop-Jagd oben)
- Das ist die eigentliche "Sniper"-Erkennung — der Moment, wo Stop-Losses gejagt wurden und der Preis zurückspringt

### 2.3 Trend-Filter (1H EMA20)
- **LONG nur:** Preis > EMA20
- **SHORT nur:** Preis < EMA20
- **NEUTRAL:** kein Trade

### 2.4 Kombination
```
LONG  = Imbalance > 1.4 UND Trend LONG  UND Sweep LONG
SHORT = Imbalance < 0.7 UND Trend SHORT UND Sweep SHORT
```

---

## 3. Entry

| Parameter | Wert |
|-----------|------|
| Ordertyp | Market (IOC) |
| Hebel | 5× (isolated) |
| Pro Symbol | max. 1 Position |
| Global | max. 2 Positionen gleichzeitig |
| Size | `balance × RISK_PCT × leverage / |entry − SL|` |

---

## 4. Risk Management

| Parameter | Wert |
|-----------|------|
| **Risiko pro Trade** | 0.3% des Kontos (RISK_PCT = 0.003) |
| **SL** | ATR(14, 1H) × 1.3 unter/über Entry |
| **TP** | ATR(14, 1H) × 2.0 über/unter Entry |
| **SL/TP-Art** | Exchange-seitig (triggerType=mark_price) — Bot darf offline gehen |
| **Margin-Modus** | Isolated (Position isoliert vom Konto) |
| Fees | Maker 0.02% / Taker 0.06% — im Break-Even eingeplant |

---

## 5. Parameter (config.py)

```python
SYMBOLS = BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, HYPEUSDT  (je 5×)
IMBALANCE_RATIO_LONG   = 1.4
IMBALANCE_RATIO_SHORT  = 0.7
SWEEP_LOOKBACK         = 3
SWEEP_CLOSE_FACTOR     = 0.20
SL_ATR_MULT            = 1.3
TP_ATR_MULT            = 2.0
ROE_TRAIL_START        = 0.03   # (vorgesehen, noch nicht aktiv)
ROE_TRAIL_DIST         = 0.02   # (vorgesehen, noch nicht aktiv)
RISK_PCT               = 0.003
MAX_POSITIONS          = 2
```

---

## 6. Loop & Monitoring

| Aspekt | Wert |
|--------|------|
| Scan-Intervall | ~3–4 s pro Symbol |
| Trend-Update | bei jedem Loop (EMA20 aus 25 Kerzen) |
| Orderbuch-Depth | 15 Level (Top 10 für Imbalance) |
| Tape | letzte 15 Trades |
| Logging | loguru → stdout + logs/sniper_bot.log |
| Telegram | bei jedem Trade (Entry/SL/TP/Size) an Chat 507397874 |

---

## 7. Technik (UTA v3 API)

| Funktion | Endpoint |
|----------|----------|
| Orderbuch | `GET /api/v3/market/orderbook` (category=USDT-FUTURES, data.a=Asks, data.b=Bids) |
| Tape | `GET /api/v3/market/fills` |
| Ticker | `GET /api/v2/mix/market/ticker` |
| Candles | `GET /api/v2/mix/market/candles` |
| Balance | `GET /api/v2/mix/account/accounts` |
| Positionen | `GET /api/v2/mix/position/all-position` |
| Order | `POST /api/v2/mix/order/place-order` (market, IOC) |
| SL/TP | `POST /api/v2/mix/order/place-tpsl-order` |

**⚠️ WICHTIG (Signatur):** Query-Parameter gehören in die HMAC-Signatur! `_get()` signiert den kompletten Pfad inkl. Query-String — sonst 40009.

---

## 8. Changelog

| Datum | Änderung |
|-------|----------|
| 10.08.2026 | V3 angelegt: Imbalance+Sweep+Trend, Demo-Start |
| 10.08.2026 | Endpoint-Fix: v3 statt v2 für Orderbook/Tape |
| 10.08.2026 | Tick-basierte Sweep-Detektion (statt 1H-Kerzen) |
| 10.08.2026 | Schwellen gelockert (2.0/0.5 → 1.4/0.7) |
| 10.08.2026 | Trade-Logik komplett (ATR, Size, Entry, SL/TP, Positions-Check) |
| 10.08.2026 | **LIVE-SCHALTUNG** (DEMO_MODE=False) nach User-Freigabe |
| 10.08.2026 | Signatur-Fix (Query in HMAC) — Smoke-Test bestanden |
| 10.08.2026 | HYPEUSDT hinzugefügt (nur Live verfügbar) |

---

## 9. Bekannte Punkte / TODO

- [ ] ROE-Trailing (ab 3%, 2% unter Peak) ist konfiguriert, aber **noch nicht im Code** aktiv
- [ ] SL-Erweiterung bei Funding-Gegenrichtung (V1-Pattern) — optional
- [ ] Size-Rundung auf Symbol-Minimum verfeinern (minTradeNum aus SymbolInfo)
- [ ] Post-Mortem-Learnings-Datei (JSON) für spätere Analyse
- [ ] 2–4 Wochen Live-Beobachtung → dann Parameter-Review
