"""
AI 简报集成测试：验证 AI 层与现有模块的接口是否打通。

不发起真实网络请求，全部使用 Mock。
"""
import unittest
import pandas as pd
from unittest.mock import MagicMock, patch

from strategies.ai_briefing import AIBriefingAnalyzer
from strategies.ai_factor_adjuster import AIFactorAdjuster
from strategies.fundamental import FundamentalScreener
from strategies.signal_engine_v2 import SignalEngineV2
from reports.daily_report import DailyReport


class MockLLMClient:
    def __init__(self, response_dict):
        self.response_dict = response_dict

    def complete(self, prompt, system=None, max_tokens=None, temperature=None, output_schema=None):
        import json
        return json.dumps(self.response_dict, ensure_ascii=False)


class TestAIIntegration(unittest.TestCase):

    def _ai_signals(self):
        return {
            "macro_sentiment": 0.6,
            "hot_sectors": ["半导体"],
            "cold_sectors": ["房地产"],
            "policy_themes": ["新质生产力"],
            "risk_events": [],
            "style_bias": "growth",
            "stock_mentions": [
                {"code": "600005", "name": "半导体股", "sentiment": 0.5, "context": "政策利好"}
            ],
            "veto_keywords": [],
            "confidence": 0.8,
            "raw_summary": "成长风格占优，半导体受利好。"
        }

    def _config(self):
        return {
            "investment_style": "balanced",
            "data_source": "mock",
            "stock_pool": {
                "preferred_sectors": ["半导体", "电力"],
                "excluded_sectors": ["教育", "游戏"],
                "custom_stocks": [],
            },
            "market_cap": {
                "balanced": {"min": 50, "max": 2000},
                "absolute_min": 30,
            },
            "valuation": {
                "max_pe": 100,
                "exclude_negative_pe": False,
                "max_pb": 8,
                "min_dividend_yield": 0.0,
                "high_dividend_yield": 3.0,
            },
            "pledge_avoidance": {"enabled": False},
            "signal_weights": {
                "base": {
                    "fundamental": 0.20,
                    "technical": 0.45,
                    "momentum": 0.10,
                    "capital_flow": 0.25,
                }
            },
            "veto_rules": {"enabled": False, "rules": []},
            "signal_conflict": {"enabled": False},
            "ai_briefing": {
                "enabled": True,
                "llm_provider": "kimi",
                "model": "moonshot-v1-32k",
                "news_sources": {
                    "max_items_per_source": 10,
                    "max_age_hours": 24,
                    "min_items_threshold": 1,
                    "max_briefing_chars": 50000,
                },
                "factor_adjustments": {
                    "enabled": True,
                    "weight_shift_cap": 0.15,
                    "macro_weight_map": {
                        "bullish": {"fundamental": 0.05, "technical": 0.05,
                                    "momentum": 0.05, "capital_flow": -0.05},
                        "bearish": {"fundamental": 0.05, "technical": -0.05,
                                    "momentum": -0.05, "capital_flow": 0.05},
                        "neutral": {"fundamental": 0, "technical": 0,
                                    "momentum": 0, "capital_flow": 0},
                    },
                    "style_weight_map": {
                        "growth": {"fundamental": -0.05, "technical": 0.05,
                                   "momentum": 0.10, "capital_flow": 0},
                        "neutral": {"fundamental": 0, "technical": 0,
                                    "momentum": 0, "capital_flow": 0},
                    },
                    "fundamental": {
                        "hot_sector_bonus": 0.10,
                        "cold_sector_penalty": -0.10,
                        "policy_theme_bonus": 0.08,
                        "risk_event_penalty": -0.15,
                    },
                    "stock_sentiment": {
                        "enabled": True,
                        "positive_bonus": 0.15,
                        "negative_penalty": -0.20,
                    },
                    "max_total_score_delta": 0.20,
                },
            },
            "report": {"output_dir": "reports/output"},
        }

    def test_fundamental_screener_uses_ai_signals(self):
        config = self._config()
        spot = pd.DataFrame({
            "代码": ["600001", "600002", "600003", "600004"],
            "名称": ["股票1", "股票2", "股票3", "股票4"],
            "所属行业": ["半导体", "房地产", "电力", "教育"],
            "总市值": [5e9, 5e9, 5e9, 5e9],
            "市盈率": [10, 10, 10, 10],
            "市净率": [1.5, 1.5, 1.5, 1.5],
            "股息率": [1, 1, 1, 1],
        })
        screener = FundamentalScreener(spot, config)
        ai_signals = self._ai_signals()

        # 无 AI 信号：房地产和教育被排除，只剩半导体和电力
        candidates_no_ai = screener.screen(ai_signals=None)
        self.assertEqual(len(candidates_no_ai), 2)

        # 有 AI 信号：房地产在 cold_sectors，教育被配置排除
        candidates_ai = screener.screen(ai_signals=ai_signals)
        self.assertEqual(len(candidates_ai), 2)
        self.assertIn("半导体", candidates_ai["所属行业"].values)
        self.assertIn("电力", candidates_ai["所属行业"].values)

    def test_fundamental_score_uses_ai_signals(self):
        config = self._config()
        screener = FundamentalScreener(pd.DataFrame(), config)
        row = pd.Series({
            "代码": "600005",
            "名称": "半导体股",
            "所属行业": "半导体",
            "总市值": 1e10,
            "市盈率": 15,
            "市净率": 1.5,
            "股息率": 3.0,
        })
        score_no_ai = screener.score(row)
        score_ai = screener.score(row, ai_signals=self._ai_signals())
        self.assertGreater(score_ai["score"], score_no_ai["score"])

    def test_signal_engine_weights_adjusted(self):
        config = self._config()
        engine = SignalEngineV2(config)
        base = config["signal_weights"]["base"]
        adjuster = AIFactorAdjuster(config)
        new_weights = adjuster.adjust_weights(base, self._ai_signals())
        engine.set_weights(new_weights)
        self.assertAlmostEqual(sum(engine.weights.values()), 1.0, places=4)

    def test_daily_report_renders_ai_signals(self):
        config = self._config()
        reporter = DailyReport(config["report"]["output_dir"])
        results = [{
            "code": "600005", "name": "半导体股", "sector": "半导体",
            "total_score": 0.5, "advice": "关注", "latest_price": 50.0,
            "ma20": 48, "ma60": 47, "ma250": 45,
            "details": {
                "fundamental": {"score": 0.4, "signals": ["AI行业利好(+0.1)"]},
                "technical": {"score": 0.3, "signals": []},
                "momentum": {"score": 0.1, "signals": []},
                "capital_flow": {"score": 0.2, "signals": []},
            },
            "ai_sentiment_reason": "AI简报提及: 半导体股",
        }]
        filepath, content = reporter.generate_markdown(
            results, market_env={"valid": True, "above_ma250": True},
            ai_signals=self._ai_signals()
        )
        self.assertIn("## AI 宏观解读", content)
        self.assertIn("成长", content)
        self.assertIn("半导体", content)
        self.assertIn("AI信号", content)
        # 个股情绪原因写入 result，报告内容含 AI 信号列；避免编码断言具体中文字串
        self.assertIn("600005", content)

    def test_ai_briefing_analyzer_with_mock_fetcher(self):
        config = self._config()
        fetcher = MagicMock()
        fetcher.get_daily_briefing.return_value = pd.DataFrame({
            "时间": ["09:30"],
            "来源": ["cls"],
            "标题": [""],
            "内容": ["半导体板块受政策利好大涨"],
        })
        llm = MockLLMClient(self._ai_signals())
        analyzer = AIBriefingAnalyzer(config, llm_client=llm)
        result = analyzer.analyze(fetcher)
        self.assertIsNotNone(result)
        self.assertEqual(result["style_bias"], "growth")


if __name__ == "__main__":
    unittest.main()
