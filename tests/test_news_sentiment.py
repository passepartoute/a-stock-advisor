import unittest
import pandas as pd
from strategies.news_sentiment import NewsSentimentAnalyzer, apply_sentiment_action


class TestNewsSentimentAnalyzer(unittest.TestCase):

    def setUp(self):
        self.config = {
            "news_sentiment": {
                "enabled": True,
                "downgrade_threshold": -0.1,
                "negative_threshold": -0.5,
                "score_weights": {"news": 0.5, "comment": 0.3, "rating": 0.2},
                "veto_rules": {
                    "enabled": True,
                    "instant_veto_keywords": ["立案", "调查", "退市", "债务违约"],
                    "keywords": ["立案", "调查", "处罚", "退市", "亏损", "减持"],
                    "negative_news_count_threshold": 2,
                    "veto_on_downgrade_to": ["减持", "卖出"],
                    "veto_on_bearish_comment_threshold": -0.6,
                }
            }
        }
        self.analyzer = NewsSentimentAnalyzer(self.config)

    def test_empty_data_passes(self):
        """无数据时应中性通过"""
        result = self.analyzer.analyze("000001")
        self.assertEqual(result["action"], "pass")
        self.assertEqual(result["score"], 0.0)

    def test_negative_news_downgrade(self):
        """单条负面新闻应触发降级"""
        news_df = pd.DataFrame({
            "标题": ["某公司收到监管处罚决定"],
        })
        result = self.analyzer.analyze("000001", news_df=news_df)
        self.assertEqual(result["action"], "downgrade")
        self.assertLess(result["score"], 0)
        self.assertEqual(result["negative_count"], 1)

    def test_multiple_negative_news_veto(self):
        """多条负面新闻应触发否决"""
        news_df = pd.DataFrame({
            "标题": ["某公司被立案调查", "某公司大股东计划减持"],
        })
        result = self.analyzer.analyze("000001", news_df=news_df)
        self.assertEqual(result["action"], "veto")
        self.assertTrue(result["negative_count"] >= 2)

    def test_instant_veto_keyword(self):
        """即时否决关键词直接跳过"""
        news_df = pd.DataFrame({
            "标题": ["某公司公告存在退市风险"],
        })
        result = self.analyzer.analyze("000001", news_df=news_df)
        self.assertEqual(result["action"], "veto")

    def test_negation_whitelist(self):
        """否定词白名单：'不减持' 不视为负面"""
        news_df = pd.DataFrame({
            "标题": ["某公司大股东承诺不减持"],
        })
        result = self.analyzer.analyze("000001", news_df=news_df)
        self.assertEqual(result["negative_count"], 0)
        self.assertEqual(result["action"], "pass")

    def test_bearish_comment(self):
        """看空评论应降低分数"""
        comment = {"sentiment": "看空", "score": -0.8}
        result = self.analyzer.analyze("000001", comment_data=comment)
        self.assertLess(result["score"], 0)
        self.assertEqual(result["action"], "veto")

    def test_downgrade_rating(self):
        """评级下调至减持应否决"""
        rating = {"rating": "减持", "change": "下调"}
        result = self.analyzer.analyze("000001", rating_data=rating)
        self.assertEqual(result["action"], "veto")

    def test_apply_sentiment_action_downgrade(self):
        """apply_sentiment_action 应正确降级"""
        stock = {"code": "000001", "name": "测试", "advice": "关注", "total_score": 0.4}
        sentiment = {"action": "downgrade", "action_reason": "负面新闻"}
        new_stock = apply_sentiment_action(stock, sentiment)
        self.assertEqual(new_stock["advice"], "轻度关注")
        self.assertTrue(new_stock.get("sentiment_downgraded"))

    def test_apply_sentiment_action_veto(self):
        """apply_sentiment_action 应正确否决"""
        stock = {"code": "000001", "name": "测试", "advice": "强烈关注", "total_score": 0.6}
        sentiment = {"action": "veto", "action_reason": "立案"}
        new_stock = apply_sentiment_action(stock, sentiment)
        self.assertEqual(new_stock["advice"], "回避")
        self.assertTrue(new_stock.get("veto"))
        self.assertEqual(new_stock["total_score"], -1.0)

    def test_apply_sentiment_action_pass(self):
        """apply_sentiment_action 对 pass 不修改"""
        stock = {"code": "000001", "name": "测试", "advice": "关注", "total_score": 0.4}
        sentiment = {"action": "pass"}
        new_stock = apply_sentiment_action(stock, sentiment)
        self.assertEqual(new_stock["advice"], "关注")

    def test_news_age_filter(self):
        """过期新闻应被过滤，不参与负面统计"""
        from datetime import datetime, timedelta
        old_time = (datetime.now() - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")
        recent_time = (datetime.now() - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
        news_df = pd.DataFrame({
            "标题": ["旧亏损公告", "新亏损公告"],
            "发布时间": [old_time, recent_time],
            "摘要": ["", ""],
            "内容": ["", ""],
        })
        result = self.analyzer.analyze("000001", news_df=news_df)
        self.assertEqual(result["news_count"], 1)
        self.assertEqual(result["negative_count"], 1)
        self.assertEqual(result["action"], "downgrade")

    def test_tushare_source_path(self):
        """配置 data_source=tushare 时，fetcher 应能走 tushare 公告路径（mock）"""
        from unittest.mock import MagicMock
        from utils.data_fetcher import DataFetcher

        fetcher = DataFetcher(data_source="tushare")
        mock_pro = MagicMock()
        mock_pro.anns_d.return_value = pd.DataFrame({
            "title": ["公司收到监管问询函", "业绩预亏公告"],
            "ann_date": ["20260701", "20260701"],
            "ann_type": ["监管", "业绩"],
        })
        fetcher._tushare_pro = mock_pro

        df = fetcher.get_stock_news("600519", limit=10, source="tushare")
        self.assertEqual(len(df), 2)
        self.assertIn("标题", df.columns)
        self.assertIn("发布时间", df.columns)
        self.assertIn("监管问询", df.iloc[0]["标题"])

    def test_akshare_raw_columns_standardized(self):
        """NewsSentimentAnalyzer 应兼容 akshare 原始列名（新闻标题/新闻摘要/新闻链接）"""
        news_df = pd.DataFrame({
            "新闻标题": ["某公司收到监管处罚决定"],
            "发布时间": ["2026-07-01 10:00:00"],
            "新闻摘要": [""],
            "新闻链接": ["http://example.com"],
        })
        result = self.analyzer.analyze("000001", news_df=news_df)
        self.assertEqual(result["news_count"], 1)
        self.assertEqual(result["negative_count"], 1)
        self.assertEqual(result["action"], "downgrade")


if __name__ == "__main__":
    unittest.main()
