# -*- coding: utf-8 -*-
"""
RiskManager 单元测试 v2
覆盖：支撑/压力位计算、止损/目标价逻辑、仓位建议
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.risk_manager import RiskManager


class TestRiskManager(unittest.TestCase):

    def _build_config(self):
        return {
            "stop_loss_method": "support_resistance",
            "stop_loss_pct": -8.0,
            "trailing_stop_pct": 8.0,
            "target_method": "support_resistance",
            "target_profit_pct": 30.0,
            "support_lookback": 20,
            "resistance_lookback": 60,
            "conservative_target": True,
            "ma20_exit_pct": 50,
            "ma60_exit": True,
            "position_management": {
                "max_holdings": 10,
                "min_holdings": 5,
                "max_single_position": 20,
                "max_conviction_position": 30,
                "cash_reserve": 15
            }
        }

    def _make_hist(self, days=100, latest_price=50.0, support=45.0, resistance=60.0):
        """构造确定性的历史K线，使 recent_low == support，recent_high == resistance"""
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")

        # 默认收盘价：从 support*1.02 线性涨到 latest_price
        close = np.linspace(support * 1.02, latest_price, days)
        high = close.copy()
        low = close.copy()

        # 近 20 天中设置一个明确低点 = support
        low[-10] = support
        close[-10] = support * 1.005  # 当天收盘略高于低点
        high[-10] = support * 1.02

        # 近 60 天中设置一个明确高点 = resistance
        high[-30] = resistance
        close[-30] = resistance * 0.995
        low[-30] = resistance * 0.98

        # 最后一天收盘价精确为 latest_price
        close[-1] = latest_price
        high[-1] = latest_price * 1.005
        low[-1] = latest_price * 0.995

        return pd.DataFrame({
            "日期": dates,
            "收盘": np.round(close, 2),
            "最高": np.round(high, 2),
            "最低": np.round(low, 2),
            "开盘": np.round(close * 0.995, 2),
            "成交量": np.full(days, 100000),
        })

    def setUp(self):
        self.config = self._build_config()
        self.risk = RiskManager(self.config)

    # ---------- 1. 支撑/压力位计算 ----------

    def test_support_resistance_calculation(self):
        """应正确计算近期支撑和压力位"""
        hist = self._make_hist(days=100, latest_price=50.0, support=45.0, resistance=60.0)
        sr = self.risk._calculate_support_resistance(hist)

        self.assertIsNotNone(sr["support"])
        self.assertIsNotNone(sr["resistance"])
        self.assertIsNotNone(sr["ma20"])
        self.assertIsNotNone(sr["ma60"])

    def test_support_uses_ma20_when_higher(self):
        """支撑位应取 recent_low 和 MA20 中较高者"""
        hist = self._make_hist(days=100, latest_price=50.0, support=40.0, resistance=60.0)
        sr = self.risk._calculate_support_resistance(hist)

        # 支撑位应该 >= MA20 或 >= recent_low
        self.assertGreaterEqual(sr["support"], min(sr["recent_low"], sr["ma20"]))

    # ---------- 2. 止损/目标价逻辑 ----------

    def test_stop_loss_uses_support(self):
        """当支撑位合理时，止损位应使用支撑位（而非固定比例）"""
        hist = self._make_hist(days=100, latest_price=50.0, support=48.0, resistance=60.0)
        advice = self.risk.get_risk_advice(
            "000001", "测试", 50.0, 0.5, {}, hist_df=hist
        )

        # 固定止损位 = 50 * 0.92 = 46.0
        # 只要计算出的支撑位 > 46.0 * 0.95 = 43.7，就应使用支撑位
        fixed_stop = round(50.0 * (1 + self.config["stop_loss_pct"] / 100), 2)
        self.assertIsNotNone(advice["support"])
        self.assertGreater(advice["support"], fixed_stop * 0.95)
        # 止损位应等于支撑位（或非常接近）
        self.assertAlmostEqual(advice["stop_loss_price"], advice["support"], places=1)

    def test_stop_loss_falls_back_to_fixed_when_support_too_low(self):
        """支撑位过低（跌幅超过固定止损过多）时应回退到固定比例"""
        # 构造一个在近期突然跳涨的股票：前 95 天在 30 附近，最后 5 天跳到 45
        # 这样 recent_low=30，MA20 也会很低，触发固定止损回退
        days = 100
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        close = np.full(days, 30.0)
        close[-5:] = 45.0
        high = close * 1.01
        low = close * 0.99
        low[-10] = 30.0  # 近20天内的明确低点

        hist = pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": high,
            "最低": low,
            "开盘": close * 0.995,
            "成交量": np.full(days, 100000),
        })

        advice = self.risk.get_risk_advice(
            "000001", "测试", 45.0, 0.5, {}, hist_df=hist
        )

        expected_fixed = round(45.0 * (1 + self.config["stop_loss_pct"] / 100), 2)
        self.assertEqual(advice["stop_loss_price"], expected_fixed)

    def test_target_uses_conservative_resistance(self):
        """保守模式下目标价应取压力位和固定目标的较小者"""
        hist = self._make_hist(days=100, latest_price=50.0, support=48.0, resistance=55.0)
        advice = self.risk.get_risk_advice(
            "000001", "测试", 50.0, 0.5, {}, hist_df=hist
        )

        # 固定目标 = 50 * 1.30 = 65.0
        fixed_target = round(50.0 * (1 + self.config["target_profit_pct"] / 100), 2)
        # 保守模式下目标价不应超过压力位，也不应超过固定目标
        self.assertLessEqual(advice["target_price"], fixed_target)
        self.assertLessEqual(advice["target_price"], advice["resistance"] + 0.01)
        self.assertIsNotNone(advice["resistance"])

    def test_risk_reward_ratio_calculated(self):
        """应计算盈亏比"""
        hist = self._make_hist(days=100, latest_price=50.0, support=48.0, resistance=55.0)
        advice = self.risk.get_risk_advice(
            "000001", "测试", 50.0, 0.5, {}, hist_df=hist
        )

        notes = " ".join(advice["risk_notes"])
        self.assertIn("盈亏比", notes)

    # ---------- 3. 仓位建议 ----------

    def test_position_high_score(self):
        """高分应建议高仓位"""
        pct = self.risk._suggest_position(0.7)
        self.assertEqual(pct, 30)

    def test_position_medium_score(self):
        """中等分数应建议中等仓位"""
        pct = self.risk._suggest_position(0.3)
        self.assertEqual(pct, 15)

    def test_position_low_score(self):
        """低分/负分应建议0仓位"""
        pct = self.risk._suggest_position(-0.1)
        self.assertEqual(pct, 0)

    def test_portfolio_advice_respects_max_holdings(self):
        """组合建议不应超过最大持股数"""
        results = [{"advice": "强烈关注"}] * 20
        portfolio = self.risk.get_portfolio_advice(results)
        self.assertEqual(portfolio["target_holdings"], 10)


if __name__ == "__main__":
    unittest.main()
