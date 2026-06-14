# -*- coding: utf-8 -*-
"""
main.py 辅助函数单元测试
覆盖：大盘数据校验、个股数据校验、行业分散
"""
import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 直接导入 main.py 中的辅助函数
import importlib.util
spec = importlib.util.spec_from_file_location("main", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"))
main = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main)


class TestValidateIndexData(unittest.TestCase):

    def _make_index_df(self, days=250, valid=True):
        """构造上证指数历史数据"""
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        if valid:
            close = np.linspace(3000, 3500, days)
        else:
            close = np.full(days, np.nan)
        return pd.DataFrame({
            "日期": dates,
            "收盘": close,
            "最高": close * 1.01,
            "最低": close * 0.99,
            "开盘": close * 0.995,
        })

    def test_valid_index_data(self):
        """足够天数且收盘价有效时应返回 valid=True"""
        df = self._make_index_df(days=260)
        config = {"data_quality": {"min_history_days": 250}}
        result = main.validate_index_data(df, config)

        self.assertTrue(result["valid"])
        self.assertIsNotNone(result["ma250"])
        self.assertIsNone(result["error"])

    def test_invalid_not_enough_days(self):
        """天数不足时应返回 valid=False"""
        df = self._make_index_df(days=200)
        config = {"data_quality": {"min_history_days": 250}}
        result = main.validate_index_data(df, config)

        self.assertFalse(result["valid"])
        self.assertIn("不足", result["error"])

    def test_invalid_ma250_nan(self):
        """MA250 为 NaN 时应返回 valid=False"""
        df = self._make_index_df(days=260, valid=False)
        config = {"data_quality": {"min_history_days": 250}}
        result = main.validate_index_data(df, config)

        self.assertFalse(result["valid"])
        self.assertIn("无效", result["error"])


class TestValidateStockData(unittest.TestCase):

    def _make_hist(self, days=260):
        dates = pd.date_range(end=datetime.now(), periods=days, freq="B")
        close = np.linspace(10, 15, days)
        return pd.DataFrame({
            "日期": dates,
            "收盘": close,
        })

    def test_valid_stock_data(self):
        """足够天数的有效数据应通过"""
        hist = self._make_hist(days=260)
        config = {"data_quality": {"min_history_days": 250, "missing_stock_ma250_action": "skip"}}
        result = main.validate_stock_data(hist, "000001", config)

        self.assertTrue(result["valid"])

    def test_invalid_short_history(self):
        """历史数据太短应标记无效"""
        hist = self._make_hist(days=50)
        config = {"data_quality": {"min_history_days": 250, "missing_stock_ma250_action": "skip"}}
        result = main.validate_stock_data(hist, "000001", config)

        self.assertFalse(result["valid"])
        self.assertEqual(result["action"], "skip")


class TestSectorDiversification(unittest.TestCase):

    def _make_results(self, sectors):
        """根据行业列表构造结果"""
        results = []
        for i, sector in enumerate(sectors):
            results.append({
                "code": f"{i:06d}",
                "name": f"股票{i}",
                "sector": sector,
                "total_score": 0.5 - i * 0.01,
                "advice": "关注" if i < 5 else "轻度关注"
            })
        return results

    def test_limits_each_sector_to_one(self):
        """每个行业应最多入选1只"""
        sectors = ["银行", "银行", "银行", "证券", "白酒", "白酒"]
        results = self._make_results(sectors)
        config = {"position_management": {"max_sector_holdings": 1, "min_sectors_in_recommendation": 3}}

        filtered, notes = main.apply_sector_diversification(results, config)

        # 3 个不同行业，每个 1 只，共 3 只
        self.assertEqual(len(filtered), 3)
        sector_counts = {}
        for r in filtered:
            sector_counts[r["sector"]] = sector_counts.get(r["sector"], 0) + 1
        for count in sector_counts.values():
            self.assertLessEqual(count, 1)

    def test_limits_each_sector_to_two(self):
        """配置为 2 时，每个行业最多入选2只"""
        sectors = ["银行", "银行", "银行", "证券", "证券", "白酒"]
        results = self._make_results(sectors)
        config = {"position_management": {"max_sector_holdings": 2, "min_sectors_in_recommendation": 3}}

        filtered, notes = main.apply_sector_diversification(results, config)

        sector_counts = {}
        for r in filtered:
            sector_counts[r["sector"]] = sector_counts.get(r["sector"], 0) + 1
        self.assertLessEqual(sector_counts.get("银行", 0), 2)
        self.assertLessEqual(sector_counts.get("证券", 0), 2)

    def test_reports_warning_when_sectors_below_minimum(self):
        """覆盖行业数少于配置时应发出警告（需要过滤后股票数 >= min_sectors，但行业数 < min_sectors）"""
        # 6 只股票分布在 3 个行业，max_sector_holdings=2 时过滤后 6 只，但只覆盖 3 个行业
        sectors = ["银行", "银行", "证券", "证券", "白酒", "白酒"]
        results = self._make_results(sectors)
        config = {"position_management": {"max_sector_holdings": 2, "min_sectors_in_recommendation": 5}}

        filtered, notes = main.apply_sector_diversification(results, config)

        # 过滤后 6 只，但行业只有 3 个，低于 min_sectors=5
        self.assertEqual(len(filtered), 6)
        warning_notes = [n for n in notes if "低于最低要求" in n]
        self.assertEqual(len(warning_notes), 1)


if __name__ == "__main__":
    unittest.main()
