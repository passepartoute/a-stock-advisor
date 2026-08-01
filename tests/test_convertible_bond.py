# -*- coding: utf-8 -*-
"""
可转债因子单元测试
覆盖：DataFetcher mock 路径、SignalEngineV2 可转债辅助信号
"""
import unittest
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.signal_engine_v2 import SignalEngineV2
from utils.data_fetcher import DataFetcher
from utils.mock_data import generate_mock_cb_data


class TestConvertibleBond(unittest.TestCase):

    def _build_config(self, enabled=True):
        return {
            "signal_weights": {
                "base": {"fundamental": 0.20, "technical": 0.25, "momentum": 0.10, "capital_flow": 0.45}
            },
            "veto_rules": {"enabled": False},
            "signal_conflict": {"enabled": False},
            "convertible_bond": {
                "enabled": enabled,
                "low_premium_threshold": -2.0,
                "high_premium_threshold": 80.0,
                "score_cap": 0.08
            }
        }

    def _make_fundamental(self, score=0.3):
        return {"score": score, "signals": []}

    def _make_technical(self, score=0.2):
        return {"score": score, "signals": [], "details": {"above_ma250": True, "ma60_rising": True}}

    def _make_momentum(self, score=0.1):
        return {"score": score, "signals": [], "r5": 2, "r20": 5, "r60": 10, "trend_aligned_down": False}

    def _make_capital(self, score=0.3):
        return {"score": score, "signals": [], "details": {"主力净流入": 2000}}

    def test_disabled_cb_no_effect(self):
        """关闭可转债时，aux_signal 不影响综合分"""
        engine = SignalEngineV2(self._build_config(enabled=False))
        f = self._make_fundamental()
        t = self._make_technical()
        m = self._make_momentum()
        c = self._make_capital()

        base = engine.combine(f, t, m, c)
        with_cb = engine.combine(f, t, m, c, aux_signal={"cb_premium": -5.0})

        self.assertEqual(base["total_score"], with_cb["total_score"])

    def test_low_cb_premium_bonus(self):
        """可转债负溢价应小幅加分"""
        engine = SignalEngineV2(self._build_config(enabled=True))
        f = self._make_fundamental()
        t = self._make_technical()
        m = self._make_momentum()
        c = self._make_capital()

        base = engine.combine(f, t, m, c)
        low = engine.combine(f, t, m, c, aux_signal={"cb_premium": -5.0})

        self.assertGreater(low["total_score"], base["total_score"])
        # 调整幅度不超过 score_cap
        self.assertLessEqual(low["total_score"] - base["total_score"], 0.08 + 1e-6)

    def test_high_cb_premium_penalty(self):
        """可转债高溢价应小幅扣分"""
        engine = SignalEngineV2(self._build_config(enabled=True))
        f = self._make_fundamental()
        t = self._make_technical()
        m = self._make_momentum()
        c = self._make_capital()

        base = engine.combine(f, t, m, c)
        high = engine.combine(f, t, m, c, aux_signal={"cb_premium": 100.0})

        self.assertLess(high["total_score"], base["total_score"])
        self.assertLessEqual(base["total_score"] - high["total_score"], 0.08 + 1e-6)

    def test_cb_no_data_neutral(self):
        """无可转债数据时应为中性"""
        engine = SignalEngineV2(self._build_config(enabled=True))
        f = self._make_fundamental()
        t = self._make_technical()
        m = self._make_momentum()
        c = self._make_capital()

        base = engine.combine(f, t, m, c)
        empty = engine.combine(f, t, m, c, aux_signal={})

        self.assertEqual(base["total_score"], empty["total_score"])

    def test_data_fetcher_mock_cb_data(self):
        """DataFetcher mock 模式应返回模拟可转债数据"""
        fetcher = DataFetcher(data_source="mock")
        df = fetcher.get_cb_data()

        self.assertIsInstance(df, pd.DataFrame)
        self.assertIn("正股代码", df.columns)
        self.assertIn("转股溢价率", df.columns)
        self.assertGreater(len(df), 0)

    def test_generate_mock_cb_data_shape(self):
        """mock 生成器结构正确"""
        df = generate_mock_cb_data()
        self.assertIn("正股代码", df.columns)
        self.assertIn("转股溢价率", df.columns)


if __name__ == "__main__":
    unittest.main()
