# -*- coding: utf-8 -*-
"""paper_trading.report 每日虚拟盘报告测试"""
import unittest

from paper_trading.portfolio import Portfolio
from paper_trading.report import render_daily_report

DATE = "2026-10-30"


def _portfolio_with_data():
    p = Portfolio(initial_capital=500000.0, created_at="2026-10-01")
    p.cash = 350000.0
    p.realized_pnl = 1234.56
    p.positions["600519"] = {
        "code": "600519", "name": "贵州茅台", "shares": 100,
        "cost_price": 1450.0, "buy_date": "2026-10-28", "buy_price": 1450.0,
        "stop_loss_price": 1377.5, "target_price": 1885.0,
        "highest_price": 1500.0, "last_close": 1480.0, "buy_fee": 21.75,
    }
    p.daily_snapshots.append({
        "date": "2026-10-29", "cash": 500000.0, "market_value": 0.0,
        "total_value": 500000.0, "daily_pnl": 0.0, "benchmark_close": 3700.0,
    })
    p.daily_snapshots.append({
        "date": DATE, "cash": 350000.0, "market_value": 148000.0,
        "total_value": 498000.0, "daily_pnl": -2000.0,
        "benchmark_close": 3681.47,
    })
    return p


def _day_result():
    return {
        "date": DATE,
        "trades": [
            {"date": DATE, "code": "600519", "name": "贵州茅台",
             "side": "BUY", "price": 1450.0, "shares": 100,
             "amount": 145000.0, "commission": 21.75, "stamp_tax": 0.0,
             "reason": "挂单成交", "pnl": None, "pnl_pct": None},
            {"date": DATE, "code": "000001", "name": "平安银行",
             "side": "SELL", "price": 10.5, "shares": 200,
             "amount": 2100.0, "commission": 5.0, "stamp_tax": 2.1,
             "reason": "止损", "pnl": -107.1, "pnl_pct": -4.86},
        ],
        "unfilled": [
            {"code": "601808", "name": "中海油服", "reason": "高开取消"},
        ],
        "snapshot": {
            "date": DATE, "cash": 350000.0, "market_value": 148000.0,
            "total_value": 498000.0, "daily_pnl": -2000.0,
            "benchmark_close": 3681.47,
        },
    }


class TestRenderDailyReport(unittest.TestCase):
    def test_contains_all_sections(self):
        md = render_daily_report(_portfolio_with_data(), _day_result())
        for section in ("当日成交", "未成交", "当前持仓", "账户概览", "净值"):
            self.assertIn(section, md)
        self.assertIn(DATE, md)

    def test_trade_rows_rendered(self):
        md = render_daily_report(_portfolio_with_data(), _day_result())
        self.assertIn("600519", md)
        self.assertIn("平安银行", md)
        self.assertIn("止损", md)
        self.assertIn("高开取消", md)
        self.assertIn("-107.10", md)  # 实现盈亏

    def test_account_overview_numbers(self):
        md = render_daily_report(_portfolio_with_data(), _day_result())
        self.assertIn("498,000.00", md)      # 总净值
        self.assertIn("-2,000.00", md)       # 当日盈亏
        self.assertIn("-0.40%", md)          # 累计收益率
        self.assertIn("1,234.56", md)        # 已实现盈亏

    def test_benchmark_comparison(self):
        # 基准从 3700 -> 3681.47，归一化对比应出现
        md = render_daily_report(_portfolio_with_data(), _day_result())
        self.assertIn("上证", md)
        self.assertIn("-0.50%", md)  # 3681.47/3700-1 = -0.50%

    def test_empty_day_still_renders(self):
        p = Portfolio(initial_capital=500000.0, created_at=DATE)
        p.daily_snapshots.append({
            "date": DATE, "cash": 500000.0, "market_value": 0.0,
            "total_value": 500000.0, "daily_pnl": 0.0,
            "benchmark_close": None,
        })
        day = {"date": DATE, "trades": [], "unfilled": [],
               "snapshot": p.daily_snapshots[-1]}
        md = render_daily_report(p, day)
        self.assertIn("账户概览", md)
        self.assertIn("无成交", md)

    def test_position_distance_to_trailing_stop(self):
        # highest 1500 -> 止盈线 1425；现价 1480 距止盈线 (1480-1425)/1480=3.7%
        md = render_daily_report(_portfolio_with_data(), _day_result())
        self.assertIn("3.7%", md)


if __name__ == "__main__":
    unittest.main()
