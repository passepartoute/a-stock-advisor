# -*- coding: utf-8 -*-
"""paper_trading.portfolio 账本测试"""
import json
import os
import tempfile
import unittest

from paper_trading.portfolio import Portfolio


def _buy_trade(code="600519", price=1450.0, shares=100, date="2026-10-30"):
    return {
        "date": date, "code": code, "name": "贵州茅台", "side": "BUY",
        "price": price, "shares": shares, "amount": price * shares,
        "commission": 21.75, "stamp_tax": 0.0,
        "reason": "挂单成交", "pnl": None, "pnl_pct": None,
    }


class TestPortfolioInit(unittest.TestCase):
    def test_init_defaults(self):
        p = Portfolio(initial_capital=500000.0)
        self.assertEqual(p.cash, 500000.0)
        self.assertEqual(p.positions, {})
        self.assertEqual(p.trades, [])
        self.assertEqual(p.daily_snapshots, [])
        self.assertIsNone(p.last_settled_date)
        self.assertEqual(p.realized_pnl, 0.0)

    def test_init_records_created_at(self):
        p = Portfolio(initial_capital=100000.0, created_at="2026-10-30")
        self.assertEqual(p.created_at, "2026-10-30")


class TestPortfolioPersistence(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "portfolio.json")
            p = Portfolio(initial_capital=500000.0, created_at="2026-10-30")
            p.cash = 354978.25
            p.positions["600519"] = {
                "code": "600519", "name": "贵州茅台", "shares": 100,
                "cost_price": 1450.0, "buy_date": "2026-10-30",
                "buy_price": 1450.0, "stop_loss_price": 1377.5,
                "target_price": 1885.0, "highest_price": 1500.0,
                "last_close": 1480.0,
            }
            p.trades.append(_buy_trade())
            p.daily_snapshots.append({
                "date": "2026-10-30", "cash": 354978.25,
                "market_value": 148000.0, "total_value": 502978.25,
                "daily_pnl": 2978.25, "benchmark_close": 3681.47,
            })
            p.realized_pnl = 123.45
            p.last_settled_date = "2026-10-30"
            p.save(path)

            q = Portfolio.load(path)
            self.assertIsNotNone(q)
            self.assertEqual(q.cash, 354978.25)
            self.assertEqual(q.positions["600519"]["highest_price"], 1500.0)
            self.assertEqual(len(q.trades), 1)
            self.assertEqual(q.daily_snapshots[0]["total_value"], 502978.25)
            self.assertEqual(q.realized_pnl, 123.45)
            self.assertEqual(q.last_settled_date, "2026-10-30")
            self.assertEqual(q.initial_capital, 500000.0)

    def test_load_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(Portfolio.load(os.path.join(d, "nope.json")))

    def test_saved_json_schema_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "portfolio.json")
            Portfolio(initial_capital=500000.0).save(path)
            data = json.load(open(path, encoding="utf-8"))
            for key in ("version", "initial_capital", "cash", "created_at",
                        "last_settled_date", "realized_pnl", "positions",
                        "trades", "daily_snapshots"):
                self.assertIn(key, data)


class TestPortfolioValuation(unittest.TestCase):
    def test_market_and_total_value(self):
        p = Portfolio(initial_capital=500000.0)
        p.cash = 300000.0
        p.positions["600519"] = {"code": "600519", "name": "贵州茅台",
                                 "shares": 100, "last_close": 1480.0}
        p.positions["000001"] = {"code": "000001", "name": "平安银行",
                                 "shares": 200, "last_close": 10.0}
        self.assertAlmostEqual(p.market_value(), 100 * 1480.0 + 200 * 10.0)
        self.assertAlmostEqual(p.total_value(), 300000.0 + 148000.0 + 2000.0)

    def test_market_value_with_price_override(self):
        p = Portfolio(initial_capital=500000.0)
        p.cash = 0.0
        p.positions["600519"] = {"code": "600519", "name": "贵州茅台",
                                 "shares": 100, "last_close": 1480.0}
        # 用给定价格（如当日收盘价）而非 last_close 估值
        self.assertAlmostEqual(p.market_value({"600519": 1500.0}), 150000.0)


if __name__ == "__main__":
    unittest.main()
