# -*- coding: utf-8 -*-
"""paper_settle CLI 的行情源与配置解析测试（stub fetcher，零网络）"""
import os
import unittest
from unittest import mock

import pandas as pd

from paper_settle import (Bar, DataFetcherBarProvider, IndexCalendar,
                          _resolve_data_source)


def _df(rows, with_pre_close=True):
    """rows: (date, open, high, low, close, pre_close)"""
    data = {
        "日期": [r[0] for r in rows],
        "开盘": [r[1] for r in rows],
        "最高": [r[2] for r in rows],
        "最低": [r[3] for r in rows],
        "收盘": [r[4] for r in rows],
    }
    if with_pre_close:
        data["昨收"] = [r[5] for r in rows]
    return pd.DataFrame(data)


class StubFetcher:
    """记录调用、可控制返回值的假 DataFetcher。真实方法均落空时
    get_hist_data 会返回 mock 随机数据——结算路径绝不允许走到那。"""

    def __init__(self, tushare_df=None, akshare_df=None):
        self.tushare_df = tushare_df if tushare_df is not None else pd.DataFrame()
        self.akshare_df = akshare_df if akshare_df is not None else pd.DataFrame()
        self.mock_called = False

    def _get_hist_data_tushare(self, symbol, days):
        return self.tushare_df

    def _get_hist_data_akshare(self, symbol, period, days):
        return self.akshare_df

    def _get_mock_hist(self, days):
        self.mock_called = True
        raise AssertionError("结算路径禁止触碰 mock 行情")


class TestNeverUseMockData(unittest.TestCase):
    def test_empty_real_sources_means_suspension_not_mock(self):
        fetcher = StubFetcher()  # 两个真实源都返回空
        provider = DataFetcherBarProvider(fetcher)
        bar = provider.get_bar("600519", "2026-10-29")
        self.assertIsNone(bar, "真实源失败应按停牌处理，禁止 mock 数据入账")

    def test_akshare_fallback_when_tushare_empty(self):
        ak = _df([("2026-10-29", 10.0, 10.2, 9.9, 10.1, 10.0)],
                 with_pre_close=False)
        fetcher = StubFetcher(akshare_df=ak)
        provider = DataFetcherBarProvider(fetcher)
        bar = provider.get_bar("600519", "2026-10-29")
        self.assertIsNotNone(bar)
        self.assertEqual(bar.close, 10.1)


class TestPreCloseColumn(unittest.TestCase):
    def test_official_pre_close_used_when_present(self):
        # 除息日：前一行收盘 10.0，官方昨收 9.5——必须用官方值
        df = _df([
            ("2026-10-28", 9.9, 10.05, 9.85, 10.0, 9.9),
            ("2026-10-29", 9.6, 9.7, 9.55, 9.65, 9.5),
        ])
        provider = DataFetcherBarProvider(StubFetcher(tushare_df=df))
        bar = provider.get_bar("600519", "2026-10-29")
        self.assertEqual(bar.pre_close, 9.5)

    def test_fallback_to_previous_row_close(self):
        df = _df([
            ("2026-10-28", 9.9, 10.05, 9.85, 10.0),
            ("2026-10-29", 10.1, 10.2, 10.0, 10.15),
        ], with_pre_close=False)
        provider = DataFetcherBarProvider(StubFetcher(tushare_df=df))
        bar = provider.get_bar("600519", "2026-10-29")
        self.assertEqual(bar.pre_close, 10.0)


class TestResolveDataSource(unittest.TestCase):
    def test_env_overrides_config(self):
        with mock.patch.dict(os.environ, {"DATA_SOURCE": "tushare"}):
            self.assertEqual(
                _resolve_data_source({"data_source": "akshare"}), "tushare")

    def test_config_used_when_no_env(self):
        env = {k: v for k, v in os.environ.items() if k != "DATA_SOURCE"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                _resolve_data_source({"data_source": "akshare"}), "akshare")
            self.assertEqual(_resolve_data_source({}), "auto")


class _StubPro:
    """假 tushare pro：index_daily 返回上证指数。"""

    def index_daily(self, ts_code, start_date, end_date):
        assert ts_code == "000001.SH"
        return pd.DataFrame({
            "trade_date": ["20261030", "20261029"],
            "close": [3681.47, 3651.43],
        })


class _StubIndexFetcher:
    def __init__(self, pro):
        self._pro = pro

    def _get_tushare_pro(self):
        return self._pro


class TestIndexCalendar(unittest.TestCase):
    def test_uses_tushare_index_daily(self):
        cal = IndexCalendar(_StubIndexFetcher(_StubPro()))
        # 必须是上证指数（~3600），绝不能是平安银行（股票000001，~11）
        self.assertEqual(cal.closes["2026-10-30"], 3681.47)
        self.assertEqual(cal.closes["2026-10-29"], 3651.43)

    def test_akshare_fallback_uses_index_api(self):
        fake_ak = mock.MagicMock()
        fake_ak.stock_zh_index_daily.return_value = pd.DataFrame({
            "date": ["2026-10-29", "2026-10-30"],
            "close": [3651.43, 3681.47],
        })
        with mock.patch.dict("sys.modules", {"akshare": fake_ak}):
            cal = IndexCalendar(_StubIndexFetcher(None))
        self.assertEqual(cal.closes["2026-10-30"], 3681.47)
        # 确认走的是指数接口，不是 stock_zh_a_hist（会返回平安银行）
        fake_ak.stock_zh_index_daily.assert_called_once()


if __name__ == "__main__":
    unittest.main()
