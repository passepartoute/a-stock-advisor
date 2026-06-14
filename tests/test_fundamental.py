# -*- coding: utf-8 -*-
"""
FundamentalScreener 单元测试
覆盖：screen() 筛选逻辑、score() 评分逻辑、股权质押过滤
"""
import unittest
import pandas as pd
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.fundamental import FundamentalScreener


class TestFundamentalScreener(unittest.TestCase):

    def _build_config(self):
        return {
            "investment_style": "balanced",
            "stock_pool": {
                "preferred_sectors": ["银行", "证券", "白酒", "电力"],
                "excluded_sectors": ["教育", "游戏", "影视"],
                "custom_stocks": []
            },
            "market_cap": {
                "conservative": {"min": 500, "max": 100000},
                "balanced": {"min": 200, "max": 1000},
                "aggressive": {"min": 100, "max": 500},
                "absolute_min": 50
            },
            "valuation": {
                "max_pe": 50,
                "exclude_negative_pe": True,
                "max_pb": 5,
                "min_dividend_yield": 2.0,
                "high_dividend_yield": 3.0
            },
            "pledge_avoidance": {
                "enabled": True,
                "threshold_pct": 30.0,
                "high_risk_pct": 50.0
            }
        }

    def _make_spot_df(self, data):
        """构造股票列表DataFrame"""
        return pd.DataFrame(data)

    # ========== screen() 测试 ==========

    def test_screen_excludes_st(self):
        """应排除 ST/*ST/退市股票"""
        # 市值控制在 balanced 范围 200-1000亿内
        data = {
            "代码": ["000001", "000002", "000003", "000004"],
            "名称": ["平安银行", "万科A", "*ST退市", "ST垃圾"],
            "所属行业": ["银行", "房地产", "房地产", "房地产"],
            "总市值": [5e10, 5e10, 5e10, 5e10],
            "市盈率": [10, 15, 5, 8],
            "市净率": [1.2, 1.5, 0.8, 0.9],
            "股息率": [3.0, 2.5, 1.0, 1.5]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        # 放宽条件确保通过
        config["stock_pool"]["preferred_sectors"] = []
        config["valuation"]["min_dividend_yield"] = 0
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)
        self.assertIn("000002", codes)
        self.assertNotIn("000003", codes)
        self.assertNotIn("000004", codes)

    def test_screen_sector_excluded(self):
        """应排除黑名单行业"""
        data = {
            "代码": ["000001", "000002", "000003"],
            "名称": ["平安银行", "王者荣耀", "电影学院"],
            "所属行业": ["银行", "游戏", "影视"],
            "总市值": [5e10, 5e10, 5e10],
            "市盈率": [10, 20, 15],
            "市净率": [1.2, 2.0, 1.5],
            "股息率": [3.0, 1.0, 1.0]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        config["stock_pool"]["preferred_sectors"] = []
        config["valuation"]["min_dividend_yield"] = 0
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)
        self.assertNotIn("000002", codes)  # 游戏
        self.assertNotIn("000003", codes)  # 影视

    def test_screen_sector_preferred(self):
        """白名单模式下只保留指定行业"""
        data = {
            "代码": ["000001", "000002", "000003"],
            "名称": ["平安银行", "贵州茅台", "教育第一"],
            "所属行业": ["银行", "白酒", "教育"],
            "总市值": [5e10, 5e10, 5e10],
            "市盈率": [10, 25, 15],
            "市净率": [1.2, 5.0, 1.5],
            "股息率": [3.0, 1.5, 1.0]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        config["valuation"]["min_dividend_yield"] = 0
        config["valuation"]["max_pb"] = 10
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)  # 银行在白名单
        self.assertIn("000002", codes)  # 白酒在白名单
        self.assertNotIn("000003", codes)  # 教育不在白名单

    def test_screen_market_cap_filter(self):
        """应正确过滤市值范围"""
        data = {
            "代码": ["000001", "000002", "000003"],
            "名称": ["大盘股", "中盘股", "小盘股"],
            "所属行业": ["银行", "银行", "银行"],
            "总市值": [2e12, 5e10, 5e9],  # 2万亿, 500亿, 50亿
            "市盈率": [10, 15, 20],
            "市净率": [1.2, 1.5, 1.8],
            "股息率": [3.0, 2.5, 2.0]
        }
        df = self._make_spot_df(data)
        config = self._build_config()  # balanced: 200-1000亿
        config["stock_pool"]["preferred_sectors"] = []
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        # 3000亿 > max(1000亿)，被排除
        self.assertNotIn("000001", codes)
        # 500亿在范围内
        self.assertIn("000002", codes)
        # 50亿 < min(200亿)，被排除
        self.assertNotIn("000003", codes)

    def test_screen_pe_filter(self):
        """应正确过滤PE"""
        data = {
            "代码": ["000001", "000002", "000003", "000004"],
            "名称": ["低PE", "正常PE", "高PE", "亏损"],
            "所属行业": ["银行", "银行", "银行", "银行"],
            "总市值": [5e10, 5e10, 5e10, 5e10],
            "市盈率": [10, 35, 80, -5],  # -5表示亏损
            "市净率": [1.2, 1.5, 2.0, 0.8],
            "股息率": [3.0, 2.5, 1.0, 0]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        config["stock_pool"]["preferred_sectors"] = []
        config["valuation"]["min_dividend_yield"] = 0
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)  # PE=10 < 50
        self.assertIn("000002", codes)  # PE=35 < 50
        self.assertNotIn("000003", codes)  # PE=80 > 50
        self.assertNotIn("000004", codes)  # 亏损 PE=-5 < 0

    def test_screen_pb_filter(self):
        """应正确过滤PB"""
        data = {
            "代码": ["000001", "000002", "000003"],
            "名称": ["低PB", "正常PB", "高PB"],
            "所属行业": ["银行", "银行", "银行"],
            "总市值": [5e10, 5e10, 5e10],
            "市盈率": [10, 15, 20],
            "市净率": [1.0, 3.0, 8.0],
            "股息率": [3.0, 2.5, 1.0]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        config["stock_pool"]["preferred_sectors"] = []
        config["valuation"]["min_dividend_yield"] = 0
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)  # PB=1 < 5
        self.assertIn("000002", codes)  # PB=3 < 5
        self.assertNotIn("000003", codes)  # PB=8 > 5

    def test_screen_dividend_yield_filter(self):
        """应正确过滤股息率"""
        data = {
            "代码": ["000001", "000002"],
            "名称": ["高股息", "低股息"],
            "所属行业": ["银行", "银行"],
            "总市值": [5e10, 5e10],
            "市盈率": [10, 15],
            "市净率": [1.2, 1.5],
            "股息率": [3.5, 1.0]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        config["stock_pool"]["preferred_sectors"] = []
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)  # 股息率3.5% >= 2.0%
        self.assertNotIn("000002", codes)  # 股息率1.0% < 2.0%

    def test_screen_pledge_filter(self):
        """应正确过滤高质押股票"""
        data = {
            "代码": ["000001", "000002", "000003"],
            "名称": ["无质押", "警戒线", "高风险"],
            "所属行业": ["银行", "银行", "银行"],
            "总市值": [5e10, 5e10, 5e10],
            "市盈率": [10, 15, 20],
            "市净率": [1.2, 1.5, 1.8],
            "股息率": [3.0, 2.5, 2.0]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        config["stock_pool"]["preferred_sectors"] = []
        screener = FundamentalScreener(df, config)

        # 注入质押数据
        pledge_df = pd.DataFrame({
            "代码": ["000001", "000002", "000003"],
            "质押比例": [10.0, 35.0, 55.0]
        })
        screener.set_pledge_data(pledge_df)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)  # 质押10% < 30%
        self.assertNotIn("000002", codes)  # 质押35% >= 30%
        self.assertNotIn("000003", codes)  # 质押55% >= 50% 高风险

    def test_screen_combined_filters(self):
        """综合筛选：多个条件同时满足"""
        data = {
            "代码": ["000001", "000002", "000003", "000004", "000005"],
            "名称": ["完美标的", "ST排除", "高PE", "小市值", "黑名单"],
            "所属行业": ["银行", "银行", "银行", "银行", "游戏"],
            "总市值": [5e10, 5e10, 5e10, 1e9, 5e10],  # 500亿, 500亿, 500亿, 10亿, 500亿
            "市盈率": [10, 15, 80, 15, 15],
            "市净率": [1.2, 1.5, 2.0, 1.5, 1.5],
            "股息率": [3.0, 2.5, 2.0, 2.5, 2.5]
        }
        df = self._make_spot_df(data)
        config = self._build_config()
        config["stock_pool"]["preferred_sectors"] = []
        screener = FundamentalScreener(df, config)
        result = screener.screen()

        codes = result["代码"].astype(str).tolist()
        self.assertIn("000001", codes)
        self.assertNotIn("000002", codes)  # ST（名称含排除词）
        self.assertNotIn("000003", codes)  # PE=80 > 50
        self.assertNotIn("000004", codes)  # 市值10亿 < 绝对下限50亿
        self.assertNotIn("000005", codes)  # 游戏在黑名单

    # ========== score() 测试 ==========

    def test_score_large_cap(self):
        """大市值应得高分"""
        row = pd.Series({
            "总市值": 8e11,  # 8000亿
            "市盈率": 10,
            "市净率": 1.2,
            "股息率": 3.5,
            "所属行业": "银行"
        })
        screener = FundamentalScreener(pd.DataFrame(), self._build_config())
        result = screener.score(row)

        self.assertGreater(result["score"], 0.5)
        self.assertIn("大市值蓝筹", result["signals"])
        self.assertIn("PE极低估值", result["signals"])
        # 高股息信号包含具体数值，如"高股息3.5%"
        self.assertTrue(any("高股息" in s for s in result["signals"]))

    def test_score_small_cap_penalty(self):
        """小市值应扣分"""
        row = pd.Series({
            "总市值": 5e9,  # 50亿 < 100亿，属于小市值
            "市盈率": 10,
            "市净率": 1.2,
            "股息率": 1.0,
            "所属行业": "银行"
        })
        screener = FundamentalScreener(pd.DataFrame(), self._build_config())
        result = screener.score(row)

        self.assertIn("小市值", result["signals"])
        # 小市值-0.1，PE低+0.25，PB低+0.2，行业+0.1 = 0.45
        self.assertLess(result["score"], 0.5)

    def test_score_loss_maker(self):
        """亏损股应大幅扣分"""
        # 使用小市值+高PB来确保总分为负
        row = pd.Series({
            "总市值": 5e9,   # 小市值
            "市盈率": -5,   # 亏损
            "市净率": 6.0,  # 高PB
            "股息率": 0,
            "所属行业": "游戏"  # 非偏好行业
        })
        screener = FundamentalScreener(pd.DataFrame(), self._build_config())
        result = screener.score(row)

        self.assertIn("亏损股", result["signals"])
        self.assertLess(result["score"], 0)

    def test_score_high_pb_penalty(self):
        """高PB应扣分"""
        row = pd.Series({
            "总市值": 5e11,
            "市盈率": 10,
            "市净率": 8.0,
            "股息率": 3.0,
            "所属行业": "银行"
        })
        screener = FundamentalScreener(pd.DataFrame(), self._build_config())
        result = screener.score(row)

        self.assertIn("PB偏高", result["signals"])

    def test_score_industry_preference(self):
        """白名单行业应加分"""
        row = pd.Series({
            "总市值": 5e11,
            "市盈率": 10,
            "市净率": 1.2,
            "股息率": 3.0,
            "所属行业": "银行"
        })
        screener = FundamentalScreener(pd.DataFrame(), self._build_config())
        result = screener.score(row)

        self.assertIn("行业偏好", result["signals"])

    def test_score_bounds(self):
        """评分应在 [-1, 1] 范围内"""
        # 极好
        row_good = pd.Series({
            "总市值": 1e12, "市盈率": 8, "市净率": 0.8,
            "股息率": 5.0, "所属行业": "银行"
        })
        # 极差
        row_bad = pd.Series({
            "总市值": 1e9, "市盈率": -10, "市净率": 10.0,
            "股息率": 0, "所属行业": "游戏"
        })
        screener = FundamentalScreener(pd.DataFrame(), self._build_config())

        result_good = screener.score(row_good)
        result_bad = screener.score(row_bad)

        self.assertLessEqual(result_good["score"], 1.0)
        self.assertGreaterEqual(result_good["score"], -1.0)
        self.assertLessEqual(result_bad["score"], 1.0)
        self.assertGreaterEqual(result_bad["score"], -1.0)


if __name__ == "__main__":
    unittest.main()
