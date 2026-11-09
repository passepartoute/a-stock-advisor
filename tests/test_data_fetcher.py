# -*- coding: utf-8 -*-
"""
DataFetcher 数据完整性校验测试
覆盖：akshare 股票列表必需列校验、数据源优先级与 akshare_enabled 开关
"""
import unittest
import pandas as pd
import sys
import os
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.data_fetcher import DataFetcher


class TestStockListAkshareIntegrity(unittest.TestCase):
    """_get_stock_list_akshare 必需列校验（2026-11 事故防护层 1）"""

    def _fetcher(self, spot_df):
        f = DataFetcher(data_source="auto", akshare_enabled=True)
        with mock.patch.object(
            sys.modules["utils.data_fetcher"].ak, "stock_zh_a_spot_em",
            return_value=spot_df,
        ):
            return f._get_stock_list_akshare()

    def test_missing_industry_column_returns_empty(self):
        """缺"所属行业"列时必须视为获取失败（返回空），让上层降级到 tushare"""
        spot = pd.DataFrame({
            "代码": ["000001"], "名称": ["平安银行"],
            "总市值": [5e10], "最新价": [10.0],
        })
        result = self._fetcher(spot)
        self.assertTrue(result.empty)

    def test_missing_market_cap_returns_empty(self):
        """缺"总市值"列时同样视为获取失败"""
        spot = pd.DataFrame({
            "代码": ["000001"], "名称": ["平安银行"],
            "所属行业": ["银行"], "最新价": [10.0],
        })
        result = self._fetcher(spot)
        self.assertTrue(result.empty)

    def test_complete_columns_returned(self):
        """必需列齐全时正常返回"""
        spot = pd.DataFrame({
            "代码": ["000001"], "名称": ["平安银行"],
            "所属行业": ["银行"], "总市值": [5e10], "最新价": [10.0],
        })
        result = self._fetcher(spot)
        self.assertEqual(len(result), 1)
        self.assertIn("所属行业", result.columns)


class TestSourcePriorityAndToggle(unittest.TestCase):
    """数据源优先级（tushare > akshare）与 akshare_enabled 开关"""

    def _stub_fetcher(self, data_source, akshare_enabled):
        calls = []

        class Stub(DataFetcher):
            def _get_stock_list_tushare(self):
                calls.append("tushare")
                return pd.DataFrame()

            def _get_stock_list_akshare(self):
                calls.append("akshare")
                return pd.DataFrame({"代码": ["1"]})

            def _get_mock_stock_list(self):
                calls.append("mock")
                return pd.DataFrame()

        return Stub(data_source=data_source,
                    akshare_enabled=akshare_enabled), calls

    def test_auto_tushare_first_when_akshare_enabled(self):
        f, calls = self._stub_fetcher("auto", True)
        f.get_stock_list()
        self.assertEqual(calls, ["tushare", "akshare"])

    def test_auto_skips_akshare_when_disabled(self):
        f, calls = self._stub_fetcher("auto", False)
        f.get_stock_list()
        self.assertNotIn("akshare", calls)

    def test_explicit_akshare_falls_back_to_tushare_when_disabled(self):
        f, calls = self._stub_fetcher("akshare", False)
        f.get_stock_list()
        self.assertNotIn("akshare", calls)
        self.assertIn("tushare", calls)


if __name__ == "__main__":
    unittest.main()
