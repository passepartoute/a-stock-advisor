import unittest
import pandas as pd
from utils.data_fetcher import DataFetcher


class _FakePro:
    """模拟 tushare pro 的 share_float 接口"""

    def share_float(self, float_date=None, **kwargs):
        data = {
            # 窗口内、已公告 → 应计入
            "20260110": pd.DataFrame([
                {"ts_code": "000001.SZ", "ann_date": "20260101",
                 "float_date": "20260110", "float_ratio": 8.0},
                {"ts_code": "000001.SZ", "ann_date": "20260101",
                 "float_date": "20260110", "float_ratio": 2.0},
            ]),
            # 窗口内、但公告日在基准日之后 → 应被过滤（未来数据泄漏）
            "20260115": pd.DataFrame([
                {"ts_code": "000002.SZ", "ann_date": "20260120",
                 "float_date": "20260115", "float_ratio": 20.0},
            ]),
        }
        return data.get(float_date, pd.DataFrame())


class TestShareFloatData(unittest.TestCase):
    def setUp(self):
        self.fetcher = DataFetcher(data_source="tushare", tushare_token="fake")
        self.fetcher._tushare_pro = _FakePro()

    def test_aggregation_and_lookahead_filter(self):
        df = self.fetcher.get_share_float_data(days_ahead=30, base_date="20260105")
        self.assertFalse(df.empty)
        # 000001 两次解禁合计 10.0
        row1 = df[df["代码"] == "000001"]
        self.assertEqual(float(row1.iloc[0]["解禁占比"]), 10.0)
        self.assertEqual(row1.iloc[0]["最近解禁日"], "20260110")
        # 000002 公告日晚于基准日，被过滤
        self.assertTrue(df[df["代码"] == "000002"].empty)

    def test_cache(self):
        df1 = self.fetcher.get_share_float_data(days_ahead=30, base_date="20260105")
        df2 = self.fetcher.get_share_float_data(days_ahead=30, base_date="20260105")
        self.assertIs(df1, df2)

    def test_no_pro_returns_empty(self):
        fetcher = DataFetcher(data_source="tushare", tushare_token="")
        fetcher._tushare_token = None  # 防止从配置文件重新加载
        fetcher._tushare_pro = None
        df = fetcher.get_share_float_data(days_ahead=30, base_date="20260105")
        self.assertTrue(df.empty)


if __name__ == "__main__":
    unittest.main()
