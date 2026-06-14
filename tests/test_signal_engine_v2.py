# -*- coding: utf-8 -*-
"""
SignalEngineV2 单元测试
覆盖：一票否决、信号冲突、动态权重、资金面额外惩罚
使用 Python 内置 unittest，无需 pytest
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.signal_engine_v2 import SignalEngineV2


class TestSignalEngineV2(unittest.TestCase):

    def _build_config(self):
        return {
            "signal_weights": {
                "base": {
                    "fundamental": 0.25,
                    "technical": 0.30,
                    "momentum": 0.30,
                    "capital_flow": 0.15
                }
            },
            "veto_rules": {
                "enabled": True,
                "rules": [
                    {
                        "name": "趋势一致向下",
                        "condition": {
                            "momentum_r5_lt": 0,
                            "momentum_r20_lt": 0,
                            "momentum_r60_lt": 0
                        },
                        "action": "exclude"
                    },
                    {
                        "name": "主力大幅流出且中期走弱",
                        "condition": {
                            "capital_main_net_lt": -5000,
                            "momentum_r20_lt": -5
                        },
                        "action": "exclude"
                    },
                    {
                        "name": "顶背离死叉共振",
                        "condition": {
                            "macd_divergence_eq": "top",
                            "macd_dead_eq": True
                        },
                        "action": "exclude"
                    },
                    {
                        "name": "均线空头排列且长期走弱",
                        "condition": {
                            "ma_bearish_eq": True,
                            "momentum_r60_lt": 0
                        },
                        "action": "exclude"
                    },
                    {
                        "name": "RSI超买且短期滞涨",
                        "condition": {
                            "rsi_gt": 70,
                            "momentum_r5_lt": 0
                        },
                        "action": "exclude"
                    }
                ]
            },
            "signal_conflict": {
                "enabled": True,
                "bearish_priority_signals": [
                    "MACD顶背离", "MACD死叉", "均线空头排列",
                    "RSI超买", "KDJ高位死叉", "高位放量滞涨"
                ],
                "max_advice_with_bearish": "观望",
                "bearish_penalty_multiplier": 1.5,
                "force_downgrade_when_conflict": True
            }
        }

    def _make_hist(self, trend="up", days=100):
        """生成简单的历史K线数据（趋势明确、噪声很小，确保测试稳定）"""
        np.random.seed(42)
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        if trend == "up":
            # 从 50 涨到 80，近5/20/60日全部上涨
            base = np.linspace(50, 80, days)
        elif trend == "down":
            # 从 100 跌到 50，近5/20/60日全部下跌
            base = np.linspace(100, 50, days)
        else:
            base = np.full(days, 65)
        # 极小幅噪声，不改变趋势方向
        close = base + np.random.randn(days) * 0.1
        close = np.maximum(close, 1.0)
        return pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": close * 1.02,
            "最低": close * 0.98,
            "开盘": close * 0.99,
            "成交量": np.full(days, 100000),
        })

    def setUp(self):
        self.config = self._build_config()
        self.engine = SignalEngineV2(self.config)

    # ---------- 1. 一票否决测试 ----------

    def test_veto_trend_aligned_down(self):
        """趋势一致向下应被一票否决"""
        hist = self._make_hist("down", days=100)
        momentum = self.engine.calculate_momentum(hist)

        # 构造基本面/技术面/资金面都为中性的输入
        fundamental = {"score": 0.6, "signals": []}
        technical = {"score": 0.2, "signals": [], "details": {}}
        capital = {"score": 0.0, "signals": [], "details": {"主力净流入": 0}}

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertTrue(result["veto"])
        self.assertEqual(result["advice"], "回避")
        self.assertEqual(result["veto_reason"], "趋势一致向下")
        self.assertEqual(result["total_score"], -1.0)

    def test_veto_macd_top_divergence_dead_cross(self):
        """MACD顶背离+死叉应被一票否决"""
        fundamental = {"score": 0.8, "signals": []}
        technical = {
            "score": 0.3,
            "signals": ["MACD顶背离", "MACD死叉"],
            "details": {"macd_divergence": "top", "macd_dead": True}
        }
        momentum = {"score": 0.1, "signals": [], "r5": 1, "r20": 2, "r60": 3}
        capital = {"score": 0.0, "signals": [], "details": {"主力净流入": 0}}

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertTrue(result["veto"])
        self.assertEqual(result["veto_reason"], "顶背离死叉共振")

    def test_veto_main_capital_outflow(self):
        """主力大幅流出且中期走弱应被一票否决（注意避免同时触发趋势一致向下）"""
        fundamental = {"score": 0.7, "signals": []}
        technical = {"score": 0.2, "signals": [], "details": {}}
        # r5>0, r60>0 避免触发"趋势一致向下"，仅触发"主力大幅流出且中期走弱"
        momentum = {"score": -0.1, "signals": [], "r5": 1, "r20": -6, "r60": 2}
        capital = {
            "score": -0.5,
            "signals": ["主力大幅流出"],
            "details": {"主力净流入": -8000}
        }

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertTrue(result["veto"])
        self.assertEqual(result["veto_reason"], "主力大幅流出且中期走弱")

    def test_veto_bearish_ma_aligned(self):
        """均线空头排列+长期走弱应被一票否决（避免同时触发趋势一致向下）"""
        fundamental = {"score": 0.8, "signals": []}
        technical = {
            "score": -0.2,
            "signals": ["均线空头排列"],
            "details": {"ma_bearish": True}
        }
        # r5>0, r20>0, r60<0：只触发"均线空头排列且长期走弱"，不触发"趋势一致向下"
        momentum = {"score": -0.1, "signals": [], "r5": 1, "r20": 2, "r60": -3}
        capital = {"score": 0.0, "signals": [], "details": {"主力净流入": 0}}

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertTrue(result["veto"])
        self.assertEqual(result["veto_reason"], "均线空头排列且长期走弱")

    def test_veto_rsi_overbought(self):
        """RSI超买+短期滞涨应被一票否决"""
        fundamental = {"score": 0.8, "signals": []}
        technical = {
            "score": 0.1,
            "signals": ["RSI超买"],
            "details": {"rsi": 75}
        }
        momentum = {"score": 0.1, "signals": [], "r5": -1, "r20": 2, "r60": 5}
        capital = {"score": 0.0, "signals": [], "details": {"主力净流入": 0}}

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertTrue(result["veto"])
        self.assertEqual(result["veto_reason"], "RSI超买且短期滞涨")

    def test_no_veto_for_good_stock(self):
        """符合要求的股票不应被否决"""
        fundamental = {"score": 0.7, "signals": []}
        technical = {
            "score": 0.4,
            "signals": ["站上年线", "均线多头排列"],
            "details": {"macd_divergence": "none", "macd_dead": False, "ma_bearish": False, "rsi": 55}
        }
        momentum = {"score": 0.3, "signals": [], "r5": 2, "r20": 5, "r60": 10}
        capital = {"score": 0.2, "signals": [], "details": {"主力净流入": 2000}}

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertFalse(result.get("veto", False))
        self.assertGreater(result["total_score"], 0)

    # ---------- 2. 信号冲突测试 ----------

    def test_bearish_signal_conflict_downgrades(self):
        """MACD顶背离出现时，即使基本面好也应降级"""
        fundamental = {"score": 0.8, "signals": []}
        technical = {
            "score": 0.3,
            "signals": ["站上年线", "MACD顶背离"],
            "details": {"macd_divergence": "top", "macd_dead": False, "rsi": 55}
        }
        momentum = {"score": 0.2, "signals": [], "r5": 3, "r20": 5, "r60": 8}
        capital = {"score": 0.1, "signals": [], "details": {"主力净流入": 1000}}

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertTrue(result["conflict_triggered"])
        self.assertIn("MACD顶背离", result["bearish_signals"])
        # 有看跌信号冲突时，最高只能是 观望 或更低
        self.assertIn(result["advice"], ["观望", "谨慎", "回避"])

    def test_macd_dead_conflict(self):
        """MACD死叉出现时强制降级"""
        fundamental = {"score": 0.6, "signals": []}
        technical = {
            "score": 0.2,
            "signals": ["MACD死叉", "MACD底背离"],
            "details": {"macd_dead": True, "macd_divergence": "bottom", "rsi": 50}
        }
        momentum = {"score": 0.1, "signals": [], "r5": 1, "r20": 2, "r60": 3}
        capital = {"score": 0.0, "signals": [], "details": {"主力净流入": 0}}

        result = self.engine.combine(fundamental, technical, momentum, capital)

        self.assertTrue(result["conflict_triggered"])
        self.assertIn(result["advice"], ["观望", "谨慎", "回避"])

    # ---------- 3. 资金面惩罚测试 ----------

    def test_capital_outflow_penalty(self):
        """主力大幅流出应触发额外扣分"""
        fundamental = {"score": 0.5, "signals": []}
        technical = {"score": 0.3, "signals": [], "details": {}}
        momentum = {"score": 0.2, "signals": [], "r5": 1, "r20": 2, "r60": 3}

        # 主力资金 -8000 万
        capital_bad = {"score": -0.2, "signals": [], "details": {"主力净流入": -8000}}
        result_bad = self.engine.combine(fundamental, technical, momentum, capital_bad)

        # 主力资金 +2000 万
        capital_good = {"score": 0.1, "signals": [], "details": {"主力净流入": 2000}}
        result_good = self.engine.combine(fundamental, technical, momentum, capital_good)

        self.assertLess(result_bad["total_score"], result_good["total_score"])

    # ---------- 4. 动态权重测试 ----------

    def test_dynamic_weights_change_result(self):
        """切换权重应改变综合评分（使用正动量避免触发否决）"""
        fundamental = {"score": 0.8, "signals": []}   # 基本面很好
        technical = {"score": 0.1, "signals": [], "details": {}}  # 技术面一般
        momentum = {"score": 0.3, "signals": [], "r5": 2, "r20": 5, "r60": 10}
        capital = {"score": 0.0, "signals": [], "details": {"主力净流入": 0}}

        # 高基本面权重
        self.engine.set_weights({
            "fundamental": 0.6, "technical": 0.2, "momentum": 0.1, "capital_flow": 0.1
        })
        result_fundamental = self.engine.combine(fundamental, technical, momentum, capital)

        # 高动量权重
        self.engine.set_weights({
            "fundamental": 0.1, "technical": 0.2, "momentum": 0.6, "capital_flow": 0.1
        })
        result_momentum = self.engine.combine(fundamental, technical, momentum, capital)

        # 基本面 0.8 * 0.6 + 动量 0.3 * 0.1 + 其他 ≈ 0.52
        # 基本面 0.8 * 0.1 + 动量 0.3 * 0.6 + 其他 ≈ 0.29
        self.assertGreater(result_fundamental["total_score"], result_momentum["total_score"])

    # ---------- 5. 动量计算测试 ----------

    def test_momentum_up_trend(self):
        """上涨趋势应得到正动量分和趋势一致向上信号"""
        hist = self._make_hist("up", days=100)
        momentum = self.engine.calculate_momentum(hist)

        self.assertGreater(momentum["score"], 0)
        self.assertTrue(momentum["trend_aligned_up"])
        self.assertFalse(momentum["trend_aligned_down"])
        self.assertIn("趋势一致向上", momentum["signals"])

    def test_momentum_down_trend(self):
        """下跌趋势应得到负动量分和趋势一致向下信号"""
        hist = self._make_hist("down", days=100)
        momentum = self.engine.calculate_momentum(hist)

        self.assertLess(momentum["score"], 0)
        self.assertTrue(momentum["trend_aligned_down"])
        self.assertFalse(momentum["trend_aligned_up"])
        self.assertIn("趋势一致向下", momentum["signals"])


if __name__ == "__main__":
    unittest.main()
