# -*- coding: utf-8 -*-
"""
MarketRegimeDetector 单元测试
覆盖：市场状态判断、动态权重选择
"""
import unittest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.market_regime import MarketRegimeDetector


class TestMarketRegimeDetector(unittest.TestCase):

    def _build_config(self):
        return {
            "dynamic_weights": {
                "enabled": True,
                "bull_trend": {
                    "fundamental": 0.20,
                    "technical": 0.30,
                    "momentum": 0.35,
                    "capital_flow": 0.15
                },
                "bear_trend": {
                    "fundamental": 0.35,
                    "technical": 0.20,
                    "momentum": 0.10,
                    "capital_flow": 0.35
                },
                "neutral": {
                    "fundamental": 0.25,
                    "technical": 0.30,
                    "momentum": 0.25,
                    "capital_flow": 0.20
                }
            },
            "signal_weights": {
                "base": {
                    "fundamental": 0.25,
                    "technical": 0.30,
                    "momentum": 0.30,
                    "capital_flow": 0.15
                }
            }
        }

    def setUp(self):
        self.config = self._build_config()
        self.detector = MarketRegimeDetector(self.config)

    def test_detect_bull_trend(self):
        """大盘在年线之上应识别为 bull_trend"""
        env = {"above_ma250": True, "sh_index": 3500, "sh_ma250": 3400}
        self.assertEqual(self.detector.detect(env), "bull_trend")

    def test_detect_bear_trend(self):
        """大盘在年线之下应识别为 bear_trend"""
        env = {"above_ma250": False, "sh_index": 3200, "sh_ma250": 3400}
        self.assertEqual(self.detector.detect(env), "bear_trend")

    def test_detect_neutral_when_missing(self):
        """年线数据缺失时应识别为 neutral"""
        env = {"above_ma250": None}
        self.assertEqual(self.detector.detect(env), "neutral")

    def test_bull_weights_have_highest_momentum(self):
        """牛市权重中动量应最高"""
        weights = self.detector.get_weights("bull_trend")
        self.assertEqual(weights["momentum"], 0.35)
        self.assertEqual(weights["fundamental"], 0.20)

    def test_bear_weights_have_highest_fundamental_and_capital(self):
        """熊市权重中基本面和资金面应最高"""
        weights = self.detector.get_weights("bear_trend")
        self.assertEqual(weights["fundamental"], 0.35)
        self.assertEqual(weights["capital_flow"], 0.35)
        self.assertEqual(weights["momentum"], 0.10)

    def test_neutral_weights_balanced(self):
        """震荡市权重应相对均衡"""
        weights = self.detector.get_weights("neutral")
        self.assertEqual(weights["fundamental"], 0.25)
        self.assertEqual(weights["technical"], 0.30)
        self.assertEqual(weights["momentum"], 0.25)
        self.assertEqual(weights["capital_flow"], 0.20)

    def test_weights_sum_to_one(self):
        """所有权重应归一化为1"""
        for regime in ["bull_trend", "bear_trend", "neutral"]:
            weights = self.detector.get_weights(regime)
            total = sum(weights.values())
            self.assertAlmostEqual(total, 1.0, places=6)

    def test_disabled_dynamic_falls_back_to_base(self):
        """关闭动态权重时应返回 base 权重"""
        config = self._build_config()
        config["dynamic_weights"]["enabled"] = False
        detector = MarketRegimeDetector(config)

        weights = detector.get_weights_for_env({"above_ma250": True})
        self.assertEqual(weights, config["signal_weights"]["base"])

    def test_describe_returns_readable_text(self):
        """describe 应返回人类可读的描述"""
        bull = self.detector.describe({"above_ma250": True})
        bear = self.detector.describe({"above_ma250": False})
        neutral = self.detector.describe({"above_ma250": None})

        self.assertIn("上升趋势", bull)
        self.assertIn("弱势市场", bear)
        self.assertIn("震荡", neutral)


if __name__ == "__main__":
    unittest.main()
