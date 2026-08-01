# -*- coding: utf-8 -*-
"""
A/H 股溢价因子单元测试
覆盖：DataFetcher mock 路径、FundamentalScreener 溢价评分逻辑
"""
import unittest
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.fundamental import FundamentalScreener
from utils.data_fetcher import DataFetcher
from utils.mock_data import generate_mock_ah_premium


class TestAhPremium(unittest.TestCase):

    def _build_config(self, enabled=True):
        return {
            "investment_style": "balanced",
            "stock_pool": {"preferred_sectors": [], "excluded_sectors": [], "custom_stocks": []},
            "market_cap": {
                "conservative": {"min": 500, "max": 100000},
                "balanced": {"min": 200, "max": 1000},
                "aggressive": {"min": 100, "max": 500},
                "absolute_min": 50
            },
            "valuation": {
                "max_pe": 50, "exclude_negative_pe": True, "max_pb": 5,
                "min_dividend_yield": 0, "high_dividend_yield": 3.0
            },
            "pledge_avoidance": {"enabled": False},
            "ah_premium": {
                "enabled": enabled,
                "high_premium_threshold": 80.0,
                "low_premium_threshold": 20.0,
                "score_cap": 0.10
            }
        }

    def test_disabled_ah_premium_no_effect(self):
        """关闭 A/H 溢价时，无论溢价多少都不影响分数"""
        row = pd.Series({
            "代码": "600000",
            "总市值": 5e11, "市盈率": 10, "市净率": 1.2,
            "股息率": 3.0, "所属行业": "银行"
        })
        config = self._build_config(enabled=False)
        screener = FundamentalScreener(pd.DataFrame(), config)
        result_no_premium = screener.score(row, ah_premium_map={"600000": 120.0})
        result_low_premium = screener.score(row, ah_premium_map={"600000": 10.0})

        self.assertEqual(result_no_premium["score"], result_low_premium["score"])
        self.assertNotIn("AH溢价高", " ".join(result_no_premium["signals"]))

    def test_high_premium_penalty(self):
        """A/H 溢价过高应扣分"""
        row = pd.Series({
            "代码": "600000",
            "总市值": 5e11, "市盈率": 10, "市净率": 1.2,
            "股息率": 3.0, "所属行业": "银行"
        })
        config = self._build_config(enabled=True)
        screener = FundamentalScreener(pd.DataFrame(), config)

        base = screener.score(row, ah_premium_map={})
        high = screener.score(row, ah_premium_map={"600000": 100.0})

        self.assertLess(high["score"], base["score"])
        self.assertTrue(any("AH溢价高" in s for s in high["signals"]))

    def test_low_premium_bonus(self):
        """A/H 溢价低应加分"""
        row = pd.Series({
            "代码": "600000",
            "总市值": 5e11, "市盈率": 10, "市净率": 1.2,
            "股息率": 3.0, "所属行业": "银行"
        })
        config = self._build_config(enabled=True)
        screener = FundamentalScreener(pd.DataFrame(), config)

        base = screener.score(row, ah_premium_map={})
        low = screener.score(row, ah_premium_map={"600000": 10.0})

        self.assertGreater(low["score"], base["score"])
        self.assertTrue(any("AH溢价低" in s for s in low["signals"]))

    def test_non_ah_stock_neutral(self):
        """非 A+H 公司不应受 A/H 溢价影响"""
        row = pd.Series({
            "代码": "600001",
            "总市值": 5e11, "市盈率": 10, "市净率": 1.2,
            "股息率": 3.0, "所属行业": "银行"
        })
        config = self._build_config(enabled=True)
        screener = FundamentalScreener(pd.DataFrame(), config)

        base = screener.score(row, ah_premium_map={})
        with_premium = screener.score(row, ah_premium_map={"600000": 10.0})

        self.assertEqual(base["score"], with_premium["score"])

    def test_data_fetcher_mock_ah_premium(self):
        """DataFetcher mock 模式应返回模拟 A/H 溢价数据"""
        fetcher = DataFetcher(data_source="mock")
        df = fetcher.get_ah_premium_data()

        self.assertIsInstance(df, pd.DataFrame)
        self.assertIn("代码", df.columns)
        self.assertIn("AH溢价率", df.columns)
        self.assertGreater(len(df), 0)

    def test_generate_mock_ah_premium_shape(self):
        """mock 生成器结构正确"""
        df = generate_mock_ah_premium()
        self.assertIn("代码", df.columns)
        self.assertIn("AH溢价率", df.columns)
        self.assertTrue((df["AH溢价率"] >= 0).all())


if __name__ == "__main__":
    unittest.main()
