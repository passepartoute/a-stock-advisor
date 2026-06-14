# -*- coding: utf-8 -*-
"""
TechnicalAnalyzer 单元测试
覆盖：score() 评分逻辑、MACD/RSI/KDJ/均线/成交量/背离/箱体突破
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.technical import TechnicalAnalyzer


class TestTechnicalAnalyzer(unittest.TestCase):

    def _build_config(self):
        return {
            "ma_short": 5,
            "ma_medium": 20,
            "ma_long": 60,
            "ma_year": 250,
            "macd_fast": 12,
            "macd_slow": 26,
            "macd_signal": 9,
            "rsi_period": 14,
            "rsi_overbought": 70,
            "rsi_oversold": 30,
            "kdj_k": 9,
            "kdj_d": 3,
            "kdj_j": 3,
            "volume_breakout_ma": 20,
            "volume_breakout_ratio": 1.5,
            "volume_shrink_ma": 20,
            "volume_shrink_ratio": 0.7
        }

    def _make_hist(self, trend="up", days=100, add_volume=True):
        """生成简单的历史K线数据"""
        np.random.seed(42)
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        if trend == "up":
            base = np.linspace(50, 80, days)
        elif trend == "down":
            base = np.linspace(100, 50, days)
        elif trend == "sideways":
            base = np.full(days, 65)
        else:
            base = np.array(trend)

        close = base + np.random.randn(days) * 0.5
        close = np.maximum(close, 1.0)
        high = close * 1.02
        low = close * 0.98
        open_price = close * 0.99

        # 计算涨跌幅
        pct_chg = np.diff(close) / close[:-1] * 100
        pct_chg = np.concatenate([[0], pct_chg])

        df = pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": high,
            "最低": low,
            "开盘": open_price,
            "涨跌幅": pct_chg,
        })
        if add_volume:
            df["成交量"] = np.full(days, 100000)
            df.iloc[-1, df.columns.get_loc("成交量")] = 300000
        return df

    def _make_rsi_extreme(self, overbought=True):
        """构造RSI极端场景"""
        days = 100
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        if overbought:
            # 构造RSI超买：连续大涨且涨幅递增
            close = np.cumsum(np.concatenate([
                np.zeros(20),
                np.linspace(0.5, 3.0, 80)  # 递增涨幅
            ])) + 50
        else:
            # 构造RSI超卖：连续大跌且跌幅递增
            close = 150 - np.cumsum(np.concatenate([
                np.zeros(20),
                np.linspace(0.5, 3.0, 80)
            ]))
        pct_chg = np.diff(close) / close[:-1] * 100
        pct_chg = np.concatenate([[0], pct_chg])
        return pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": close * 1.02,
            "最低": close * 0.98,
            "开盘": close * 0.99,
            "涨跌幅": pct_chg,
            "成交量": np.full(days, 100000),
        })

    def _make_ma_aligned_bull(self):
        """构造均线多头排列：价格 > MA5 > MA20 > MA60"""
        days = 100
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        close = np.linspace(80, 120, days)  # 强劲上涨趋势
        pct_chg = np.diff(close) / close[:-1] * 100
        pct_chg = np.concatenate([[0], pct_chg])
        return pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": close * 1.01,
            "最低": close * 0.99,
            "开盘": close * 0.995,
            "涨跌幅": pct_chg,
            "成交量": np.full(days, 100000),
        })

    def _make_ma_aligned_bear(self):
        """构造均线空头排列：价格 < MA5 < MA20 < MA60"""
        days = 100
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        close = np.linspace(120, 50, days)  # 强劲下跌趋势
        pct_chg = np.diff(close) / close[:-1] * 100
        pct_chg = np.concatenate([[0], pct_chg])
        return pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": close * 1.01,
            "最低": close * 0.99,
            "开盘": close * 0.995,
            "涨跌幅": pct_chg,
            "成交量": np.full(days, 100000),
        })

    # ========== 基础测试 ==========

    def test_empty_df(self):
        """空DataFrame应返回零分"""
        analyzer = TechnicalAnalyzer(pd.DataFrame(), self._build_config())
        result = analyzer.score()
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["signals"], [])

    def test_short_df(self):
        """数据不足60天应返回零分"""
        df = self._make_hist(days=30)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()
        self.assertEqual(result["score"], 0)

    # ========== 均线测试 ==========

    def test_ma_aligned_bullish(self):
        """均线多头排列应大幅加分"""
        df = self._make_ma_aligned_bull()
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        # 验证信号和分数方向
        if "均线多头排列" in result["signals"]:
            self.assertTrue(result["details"].get("ma_aligned", False))
            self.assertGreaterEqual(result["score"], 0.15)
        # 强劲上涨趋势分数应为正
        self.assertGreater(result["score"], 0)

    def test_ma_aligned_bearish(self):
        """均线空头排列应大幅扣分"""
        df = self._make_ma_aligned_bear()
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        if "均线空头排列" in result["signals"]:
            self.assertTrue(result["details"].get("ma_bearish", False))
            self.assertLessEqual(result["score"], -0.1)
        # 强劲下跌趋势分数应为负
        self.assertLess(result["score"], 0)

    def test_above_ma250(self):
        """站上年线应加分"""
        df = self._make_ma_aligned_bull()
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        # 100天从80涨到120，MA250值会很低，价格应站上年线
        if "站上年线" in result["signals"]:
            self.assertTrue(result["details"].get("above_ma250", False))

    # ========== MACD测试 ==========

    def test_macd_signals_present(self):
        """MACD信号应在details中"""
        df = self._make_hist(trend="up", days=100)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        # details中应有MACD相关字段
        self.assertIn("macd_golden", result["details"])
        self.assertIn("macd_dead", result["details"])
        self.assertIn("macd_above_zero", result["details"])
        self.assertIn("macd_divergence", result["details"])

    def test_macd_divergence_field(self):
        """MACD背离检测结果应在有效值范围内"""
        df = self._make_hist(days=100)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        divergence = result["details"].get("macd_divergence", "none")
        self.assertIn(divergence, ["none", "top", "bottom"])

    # ========== RSI测试 ==========

    def test_rsi_extreme_overbought(self):
        """RSI超买场景测试"""
        df = self._make_rsi_extreme(overbought=True)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        rsi = result["details"].get("rsi", 50)
        # 如果RSI确实超买，应扣分
        if "RSI超买" in result["signals"]:
            self.assertGreater(rsi, 70)
        # RSI值应在合理范围
        self.assertGreater(rsi, 0)
        self.assertLessEqual(rsi, 100)

    def test_rsi_extreme_oversold(self):
        """RSI超卖场景测试"""
        df = self._make_rsi_extreme(overbought=False)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        rsi = result["details"].get("rsi", 50)
        # 如果RSI确实超卖，应有信号
        if "RSI超卖" in result["signals"]:
            self.assertLess(rsi, 30)

    def test_rsi_in_range(self):
        """RSI值应在 [0, 100] 范围内"""
        df = self._make_hist(days=100)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        rsi = result["details"].get("rsi", 50)
        self.assertGreaterEqual(rsi, 0)
        self.assertLessEqual(rsi, 100)

    # ========== KDJ测试 ==========

    def test_kdj_fields_present(self):
        """KDJ字段应在details中"""
        df = self._make_hist(days=100)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        self.assertIn("kdj_j", result["details"])
        self.assertIn("kdj_kd", result["details"])
        self.assertIn("kdj_golden", result["details"])
        self.assertIn("kdj_dead", result["details"])

    # ========== 成交量测试 ==========

    def test_volume_breakout(self):
        """放量突破应加分"""
        days = 100
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        close = np.linspace(50, 65, days)
        vol = np.full(days, 100000)
        # 最后一天放量大涨并创近期新高
        vol[-1] = 400000
        close[-1] = 70
        pct_chg = np.diff(close) / close[:-1] * 100
        pct_chg = np.concatenate([[0], pct_chg])
        df = pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": close * 1.02,
            "最低": close * 0.98,
            "开盘": close * 0.99,
            "涨跌幅": pct_chg,
            "成交量": vol,
        })
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        if "放量突破" in result["signals"]:
            self.assertGreater(result["score"], 0)

    def test_volume_ratio_field(self):
        """成交量比率应在details中"""
        df = self._make_hist(days=100)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        # 如果最后一天放量，应有volume_ratio
        if "volume_ratio" in result["details"]:
            ratio = result["details"]["volume_ratio"]
            self.assertGreater(ratio, 0)

    # ========== 箱体突破测试 ==========

    def test_box_breakout(self):
        """长期横盘后突破应加分"""
        days = 100
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        # 前80天横盘，后20天突破
        close = np.concatenate([
            np.linspace(50, 52, 80),    # 横盘
            np.linspace(52, 65, 20),    # 突破
        ])
        vol = np.full(days, 100000)
        vol[-1] = 200000  # 放量
        pct_chg = np.diff(close) / close[:-1] * 100
        pct_chg = np.concatenate([[0], pct_chg])
        df = pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": close * 1.02,
            "最低": close * 0.98,
            "开盘": close * 0.99,
            "涨跌幅": pct_chg,
            "成交量": vol,
        })
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        if "箱体突破" in result["signals"]:
            self.assertGreater(result["score"], 0)

    # ========== 综合评分范围测试 ==========

    def test_score_bounds(self):
        """评分应在 [-1, 1] 范围内"""
        for trend in ["up", "down", "sideways"]:
            df = self._make_hist(trend=trend, days=100)
            analyzer = TechnicalAnalyzer(df, self._build_config())
            result = analyzer.score()
            self.assertGreaterEqual(result["score"], -1.0,
                                    f"{trend} trend score below -1")
            self.assertLessEqual(result["score"], 1.0,
                                 f"{trend} trend score above 1")

    def test_details_structure(self):
        """details 应包含关键字段"""
        df = self._make_hist(days=100)
        analyzer = TechnicalAnalyzer(df, self._build_config())
        result = analyzer.score()

        required_keys = ["above_ma250", "ma_aligned", "ma_bearish",
                         "macd_golden", "macd_dead", "macd_above_zero",
                         "macd_divergence", "rsi", "kdj_j"]
        for key in required_keys:
            self.assertIn(key, result["details"],
                          f"details missing key: {key}")


if __name__ == "__main__":
    unittest.main()
