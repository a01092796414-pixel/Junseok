import os
import tempfile
import unittest
from datetime import date, timedelta

from trading.backtest import Costs, run_backtest
from trading.data import Bar, load_csv, save_csv, synthetic_bars
from trading.risk import RiskConfig, RiskManager
from trading.strategies import BuyAndHold, MovingAverageCross, Momentum, sma

NO_COST = Costs(0, 0, 0)


def flat_bars(prices):
    d = date(2024, 1, 1)
    return [Bar(d + timedelta(days=i), p, p, p, p) for i, p in enumerate(prices)]


class TestData(unittest.TestCase):
    def test_csv_roundtrip_and_korean_headers(self):
        bars = synthetic_bars(30)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.csv")
            save_csv(bars, path)
            self.assertEqual(load_csv(path)[-1].close, bars[-1].close)
            kpath = os.path.join(tmp, "k.csv")
            with open(kpath, "w", encoding="utf-8") as f:
                f.write("일자,시가,고가,저가,종가,거래량\n2024.01.03,\"1,100\",1200,1000,1150,10\n2024.01.02,1000,1100,900,1050,5\n")
            k = load_csv(kpath)
            self.assertEqual([b.date for b in k], [date(2024, 1, 2), date(2024, 1, 3)])
            self.assertEqual(k[1].open, 1100)


class TestStrategies(unittest.TestCase):
    def test_sma(self):
        self.assertEqual(sma([1, 2, 3, 4], 2), [None, 1.5, 2.5, 3.5])

    def test_signals_do_not_use_future(self):
        bars = synthetic_bars(200)
        for strat in (MovingAverageCross(5, 20), Momentum(10)):
            full = strat.signals(bars)
            for cut in (50, 120):
                self.assertEqual(strat.signals(bars[:cut]), full[:cut])


class TestBacktest(unittest.TestCase):
    def test_hold_doubles_without_costs(self):
        r = run_backtest(flat_bars([100, 100, 150, 200]), BuyAndHold(), initial_cash=1000, costs=NO_COST)
        self.assertAlmostEqual(r.final_equity, 2000)
        self.assertAlmostEqual(r.total_return, 1.0)

    def test_executes_next_day_open(self):
        d = date(2024, 1, 1)
        bars = [Bar(d, 100, 100, 100, 100), Bar(d + timedelta(1), 110, 120, 100, 120)]
        r = run_backtest(bars, BuyAndHold(), initial_cash=1100, costs=NO_COST)
        self.assertEqual(r.trades[0].entry_price, 110)  # 다음 날 시가
        self.assertEqual(r.trades[0].shares, 10)

    def test_costs_reduce_return(self):
        bars = synthetic_bars(300)
        free = run_backtest(bars, MovingAverageCross(5, 20), costs=NO_COST)
        paid = run_backtest(bars, MovingAverageCross(5, 20), costs=Costs(0.001, 0.002, 0.002))
        self.assertLess(paid.final_equity, free.final_equity)

    def test_max_drawdown(self):
        r = run_backtest(flat_bars([100, 100, 200, 100, 150]), BuyAndHold(), initial_cash=1000, costs=NO_COST)
        self.assertAlmostEqual(r.max_drawdown, -0.5)


class TestRisk(unittest.TestCase):
    def test_limits(self):
        rm = RiskManager(RiskConfig(max_order_amount=100, max_daily_loss=50, max_position_pct=0.5, max_orders_per_day=3))
        self.assertTrue(rm.check_order("buy", 100, 100, 1000).ok)
        self.assertFalse(rm.check_order("buy", 101, 101, 1000).ok)
        self.assertFalse(rm.check_order("buy", 50, 600, 1000).ok)
        rm.record_pnl(-60)
        self.assertFalse(rm.check_order("buy", 10, 10, 1000).ok)
        self.assertTrue(rm.check_order("sell", 10, 0, 1000).ok)  # 손실 한도여도 매도는 허용

    def test_live_and_kill_switch(self):
        self.assertFalse(RiskManager().check_order("buy", 10, 10, 1000, live=True).ok)
        self.assertFalse(RiskManager(RiskConfig(kill_switch=True)).check_order("sell", 10, 0, 1000).ok)

    def test_order_count_limit(self):
        rm = RiskManager(RiskConfig(max_orders_per_day=2))
        self.assertTrue(rm.check_order("sell", 1, 0, 10).ok)
        self.assertTrue(rm.check_order("sell", 1, 0, 10).ok)
        self.assertFalse(rm.check_order("sell", 1, 0, 10).ok)
        rm.new_day()
        self.assertTrue(rm.check_order("sell", 1, 0, 10).ok)


if __name__ == "__main__":
    unittest.main()


class TestSmallAccount(unittest.TestCase):
    def test_fractional_and_min_order(self):
        bars = flat_bars([100_000_000, 100_000_000, 110_000_000])
        r = run_backtest(bars, BuyAndHold(), initial_cash=50_000, costs=NO_COST, fractional=True, min_order=5_000)
        self.assertAlmostEqual(r.final_equity, 55_000)
        r2 = run_backtest(bars, BuyAndHold(), initial_cash=4_000, costs=NO_COST, fractional=True, min_order=5_000)
        self.assertEqual(r2.trades, [])
        r3 = run_backtest(bars, BuyAndHold(), initial_cash=50_000, costs=NO_COST)
        self.assertEqual(r3.trades, [])  # 1주가 5만 원보다 비싸면 못 산다

    def test_simulation_runs(self):
        from trading.simulate import run_simulation
        _, sums = run_simulation("etf", runs=20)
        self.assertEqual(len(sums), 3)
        for s in sums:
            self.assertLessEqual(s.p05_final, s.median_final)
            self.assertLessEqual(s.median_final, s.p95_final)
