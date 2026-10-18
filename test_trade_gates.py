"""Gates ohne Netzwerk und ohne config.py."""

import unittest

from trade_gates import ContractSpec, calculate_size, evaluate_entry, floor_size

# Live-Stand 18.10.2026, Monitor-Subaccount at-HNKHNKHNKHNK
BALANCE = 2.38948644
BNB = ContractSpec(min_trade_num=0.01, size_step=0.01, min_notional=5, price_place=2, volume_place=2)
ETH = ContractSpec(min_trade_num=0.01, size_step=0.01, min_notional=5, price_place=2, volume_place=2)
SOL = ContractSpec(min_trade_num=0.1, size_step=0.1, min_notional=5, price_place=3, volume_place=1)
HYPE = ContractSpec(min_trade_num=0.01, size_step=0.01, min_notional=5, price_place=3, volume_place=2)
BTC = ContractSpec(min_trade_num=0.0001, size_step=0.0001, min_notional=5, price_place=1, volume_place=4)


def _entry(**kwargs):
    base = dict(
        entries_paused=False,
        balance=BALANCE,
        leverage=3,
        risk_pct=0.003,
        entry_is_maker=True,
    )
    base.update(kwargs)
    return evaluate_entry(**base)


class FloorSizeTests(unittest.TestCase):
    def test_does_not_round_half_up(self):
        # Der alte round(size, 2) machte aus 0.005 → 0.01 und setzte die Mindestorder.
        raw = calculate_size(BALANCE, 720.51, 720.51 - 1.3 * 3.2564, 3, 0.003)
        self.assertAlmostEqual(raw, 0.00508, places=4)
        self.assertEqual(round(raw, 2), 0.01)
        self.assertEqual(floor_size(raw, 0.01), 0.0)

    def test_floors_to_step(self):
        self.assertEqual(floor_size(1.0638, 0.01), 1.06)
        self.assertEqual(floor_size(0.0198, 0.1), 0.0)
        self.assertEqual(floor_size(0.000062, 0.0001), 0.0)


class LiveAccountTests(unittest.TestCase):
    def test_bnb_current_atr_is_below_min_size(self):
        price = 720.51
        atr = 3.2564
        decision = _entry(
            price=price,
            sl_price=price - 1.3 * atr,
            tp_price=price + 2.0 * atr,
            spec=BNB,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "below_min_size")
        self.assertEqual(decision.size, 0.0)

    def test_bnb_min_size_still_rejected_when_it_would_use_the_whole_balance(self):
        # Rohgröße knapp über 0.01, Notional ~7.2, Margin bei 3× ≈ ganzes Konto.
        price = 720.51
        sl_dist = BALANCE * 0.003 * 3 / 0.014  # raw ≈ 0.014 → floor 0.01
        decision = _entry(
            price=price,
            sl_price=price - sl_dist,
            tp_price=price + sl_dist,
            spec=BNB,
        )
        self.assertAlmostEqual(decision.size, 0.01, places=8)
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "margin_exceeds_balance")
        self.assertGreater(decision.margin, BALANCE * 0.98)

    def test_eth_btc_sol_hype_rejected_on_this_balance(self):
        eth_px, eth_atr = 2408.73, 15.21
        eth = _entry(price=eth_px, sl_price=eth_px - 1.3 * eth_atr, tp_price=eth_px + 2 * eth_atr, spec=ETH)
        self.assertEqual(eth.reason, "below_min_size")

        btc_px, btc_atr = 81654.4, 267.05
        btc = _entry(price=btc_px, sl_price=btc_px - 1.3 * btc_atr, tp_price=btc_px + 2 * btc_atr, spec=BTC)
        self.assertEqual(btc.reason, "below_min_size")

        sol_px, sol_atr = 103.239, 0.8358
        sol = _entry(price=sol_px, sl_price=sol_px - 1.3 * sol_atr, tp_price=sol_px + 2 * sol_atr, spec=SOL)
        self.assertEqual(sol.reason, "below_min_size")

        # HYPE-Rohgröße liegt über dem Step, Notional aber unter 5 USDT.
        hype_px = 78.482
        sl_dist = BALANCE * 0.003 * 3 / 0.026
        hype = _entry(price=hype_px, sl_price=hype_px - sl_dist, tp_price=hype_px + sl_dist * 2, spec=HYPE)
        self.assertAlmostEqual(hype.size, 0.02, places=8)
        self.assertEqual(hype.reason, "below_min_notional")
        self.assertLess(hype.notional, 5)


class RuleTests(unittest.TestCase):
    def test_pause_blocks_even_a_valid_order(self):
        price = 720.0
        decision = _entry(
            entries_paused=True,
            balance=500,
            price=price,
            sl_price=price - 4,
            tp_price=price + 8,
            spec=BNB,
        )
        self.assertFalse(decision.allow)
        self.assertEqual(decision.reason, "entries_paused")

    def test_funded_account_is_allowed_and_not_rounded_up(self):
        price = 720.51
        atr = 3.2564
        sl = price - 1.3 * atr
        decision = _entry(
            balance=500,
            price=price,
            sl_price=sl,
            tp_price=price + 2 * atr,
            spec=BNB,
        )
        raw = calculate_size(500, price, sl, 3, 0.003)
        self.assertTrue(decision.allow)
        self.assertEqual(decision.reason, "ok")
        self.assertEqual(decision.size, floor_size(raw, 0.01))
        self.assertLessEqual(decision.size, raw)
        self.assertGreater(decision.margin, 0)
        self.assertLess(decision.margin, 500 * 0.98)

    def test_tp_inside_round_trip_fee_is_rejected(self):
        price = 100.0
        # 0.05% TP < 0.08% Maker+Taker
        decision = _entry(
            balance=500,
            price=price,
            sl_price=price - 1,
            tp_price=price * (1 + 0.0005),
            spec=ContractSpec(0.01, 0.01, 5),
        )
        self.assertEqual(decision.reason, "tp_inside_fees")

    def test_bad_contract_rejected(self):
        decision = _entry(
            balance=500,
            price=100,
            sl_price=99,
            tp_price=102,
            spec=ContractSpec(min_trade_num=0.01, size_step=0, min_notional=5),
        )
        self.assertEqual(decision.reason, "bad_contract")


if __name__ == "__main__":
    unittest.main()
