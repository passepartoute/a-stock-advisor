import unittest
import json
import pandas as pd
from unittest.mock import MagicMock

from strategies.ai_briefing import AIBriefingAnalyzer, AI_BRIEFING_SCHEMA


class MockLLMClient:
    """用于测试的 Mock LLM 客户端"""

    def __init__(self, response_dict=None, return_none=False):
        self.return_none = return_none
        self.response_dict = response_dict or {
            "macro_sentiment": 0.5,
            "hot_sectors": ["半导体", "电力"],
            "cold_sectors": ["房地产"],
            "policy_themes": ["新质生产力"],
            "risk_events": [],
            "style_bias": "growth",
            "stock_mentions": [
                {"code": "600519", "name": "贵州茅台", "sentiment": 0.3, "context": "业绩超预期"}
            ],
            "veto_keywords": [],
            "confidence": 0.8,
            "raw_summary": "今日大盘情绪偏暖，半导体板块受政策利好。"
        }

    def complete(self, prompt, system=None, max_tokens=None, temperature=None, output_schema=None):
        if self.return_none:
            return None
        return json.dumps(self.response_dict, ensure_ascii=False)


class TestAIBriefingAnalyzer(unittest.TestCase):

    def _make_config(self):
        return {
            "ai_briefing": {
                "enabled": True,
                "llm_provider": "kimi",
                "model": "moonshot-v1-32k",
                "news_sources": {
                    "primary": "cls_alerts",
                    "fallbacks": [],
                    "max_items_per_source": 10,
                    "max_age_hours": 24,
                    "min_items_threshold": 1,
                    "max_briefing_chars": 50000,
                },
                "factor_adjustments": {"enabled": True},
            }
        }

    def test_analyze_returns_structured_signals(self):
        fetcher = MagicMock()
        fetcher.get_daily_briefing.return_value = pd.DataFrame({
            "时间": ["09:30", "10:00"],
            "来源": ["cls", "cls"],
            "标题": ["", ""],
            "内容": ["半导体板块大涨", "电力股受资金追捧"],
        })

        analyzer = AIBriefingAnalyzer(self._make_config(), llm_client=MockLLMClient())
        result = analyzer.analyze(fetcher)

        self.assertIsNotNone(result)
        self.assertEqual(result["macro_sentiment"], 0.5)
        self.assertIn("半导体", result["hot_sectors"])
        self.assertIn("房地产", result["cold_sectors"])
        self.assertEqual(result["style_bias"], "growth")
        self.assertEqual(len(result["stock_mentions"]), 1)
        self.assertEqual(result["stock_mentions"][0]["code"], "600519")

    def test_analyze_disabled_returns_none(self):
        config = self._make_config()
        config["ai_briefing"]["enabled"] = False
        analyzer = AIBriefingAnalyzer(config, llm_client=MockLLMClient())
        result = analyzer.analyze(MagicMock())
        self.assertIsNone(result)

    def test_analyze_llm_failure_returns_none(self):
        fetcher = MagicMock()
        fetcher.get_daily_briefing.return_value = pd.DataFrame({
            "时间": ["09:30"],
            "来源": ["cls"],
            "标题": [""],
            "内容": ["半导体板块大涨"],
        })
        analyzer = AIBriefingAnalyzer(self._make_config(), llm_client=MockLLMClient(return_none=True))
        result = analyzer.analyze(fetcher)
        self.assertIsNone(result)

    def test_analyze_empty_briefing_returns_none(self):
        fetcher = MagicMock()
        fetcher.get_daily_briefing.return_value = pd.DataFrame(columns=["时间", "来源", "标题", "内容"])
        analyzer = AIBriefingAnalyzer(self._make_config(), llm_client=MockLLMClient())
        result = analyzer.analyze(fetcher)
        self.assertIsNone(result)

    def test_normalize_clamps_values(self):
        analyzer = AIBriefingAnalyzer(self._make_config())
        parsed = {
            "macro_sentiment": 1.5,
            "style_bias": "unknown_style",
            "confidence": -0.5,
            "stock_mentions": [
                {"code": "000001", "name": "平安银行", "sentiment": 2.0},
                {"code": "", "name": "无效", "sentiment": 0},
            ],
        }
        result = analyzer._normalize(parsed)
        self.assertEqual(result["macro_sentiment"], 1.0)
        self.assertEqual(result["style_bias"], "neutral")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(len(result["stock_mentions"]), 1)
        self.assertEqual(result["stock_mentions"][0]["sentiment"], 1.0)

    def test_schema_has_required_fields(self):
        required = AI_BRIEFING_SCHEMA.get("required", [])
        for field in ["macro_sentiment", "hot_sectors", "cold_sectors", "style_bias",
                      "stock_mentions", "confidence", "raw_summary"]:
            self.assertIn(field, required)


if __name__ == "__main__":
    unittest.main()
