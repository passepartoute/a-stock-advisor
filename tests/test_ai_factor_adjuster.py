import unittest
from strategies.ai_factor_adjuster import AIFactorAdjuster


class TestAIFactorAdjuster(unittest.TestCase):

    def _make_config(self):
        return {
            "ai_briefing": {
                "enabled": True,
                "factor_adjustments": {
                    "enabled": True,
                    "weight_shift_cap": 0.15,
                    "macro_weight_map": {
                        "bullish": {
                            "fundamental": 0.05,
                            "technical": 0.05,
                            "momentum": 0.05,
                            "capital_flow": -0.05,
                        },
                        "bearish": {
                            "fundamental": 0.05,
                            "technical": -0.05,
                            "momentum": -0.05,
                            "capital_flow": 0.05,
                        },
                        "neutral": {
                            "fundamental": 0.0,
                            "technical": 0.0,
                            "momentum": 0.0,
                            "capital_flow": 0.0,
                        },
                    },
                    "style_weight_map": {
                        "growth": {
                            "fundamental": -0.05,
                            "technical": 0.05,
                            "momentum": 0.10,
                            "capital_flow": 0.0,
                        },
                        "value": {
                            "fundamental": 0.10,
                            "technical": -0.05,
                            "momentum": -0.05,
                            "capital_flow": 0.0,
                        },
                        "neutral": {
                            "fundamental": 0.0,
                            "technical": 0.0,
                            "momentum": 0.0,
                            "capital_flow": 0.0,
                        },
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
            }
        }

    def test_adjust_weights_normalizes_to_one(self):
        adjuster = AIFactorAdjuster(self._make_config())
        base = {"fundamental": 0.20, "technical": 0.45, "momentum": 0.10, "capital_flow": 0.25}
        signals = {"macro_sentiment": 0.5, "style_bias": "growth"}
        new_weights = adjuster.adjust_weights(base, signals)
        self.assertAlmostEqual(sum(new_weights.values()), 1.0, places=4)

    def test_adjust_weights_respects_cap(self):
        adjuster = AIFactorAdjuster(self._make_config())
        base = {"fundamental": 0.20, "technical": 0.45, "momentum": 0.10, "capital_flow": 0.25}
        # bullish + growth: momentum shift would be 0.15, capped at 0.15
        signals = {"macro_sentiment": 0.5, "style_bias": "growth"}
        new_weights = adjuster.adjust_weights(base, signals)
        for key in base:
            self.assertLessEqual(abs(new_weights[key] - base[key]), 0.15 + 1e-6)

    def test_adjust_weights_no_signals_unchanged(self):
        adjuster = AIFactorAdjuster(self._make_config())
        base = {"fundamental": 0.20, "technical": 0.45, "momentum": 0.10, "capital_flow": 0.25}
        new_weights = adjuster.adjust_weights(base, None)
        self.assertEqual(new_weights, base)

    def test_adjust_fundamental_score_hot_sector(self):
        adjuster = AIFactorAdjuster(self._make_config())
        signals = {"hot_sectors": ["半导体"], "cold_sectors": [], "policy_themes": [], "risk_events": []}
        score = adjuster.adjust_fundamental_score(0.0, "半导体及元件", signals)
        self.assertAlmostEqual(score, 0.10, places=2)

    def test_adjust_fundamental_score_cold_sector(self):
        adjuster = AIFactorAdjuster(self._make_config())
        signals = {"hot_sectors": [], "cold_sectors": ["房地产"], "policy_themes": [], "risk_events": []}
        score = adjuster.adjust_fundamental_score(0.0, "房地产开发", signals)
        self.assertAlmostEqual(score, -0.10, places=2)

    def test_adjust_fundamental_score_respects_max_delta(self):
        adjuster = AIFactorAdjuster(self._make_config())
        signals = {
            "hot_sectors": ["半导体"],
            "cold_sectors": ["房地产"],
            "policy_themes": ["新质生产力"],
            "risk_events": ["监管收紧"],
        }
        # raw delta = 0.10 - 0.10 + 0.08 - 0.15 = -0.07, within cap
        score = adjuster.adjust_fundamental_score(0.5, "半导体", signals)
        self.assertGreaterEqual(score, -1.0)
        self.assertLessEqual(score, 1.0)

    def test_apply_stock_sentiment_overlay(self):
        adjuster = AIFactorAdjuster(self._make_config())
        signals = {
            "stock_mentions": [
                {"code": "600519", "name": "贵州茅台", "sentiment": 0.5, "context": "业绩好"},
                {"code": "000001", "name": "平安银行", "sentiment": -0.5, "context": "利空"},
            ]
        }
        results = [
            {"code": "600519", "total_score": 0.5},
            {"code": "000001", "total_score": 0.3},
            {"code": "000002", "total_score": 0.1},
        ]
        count = adjuster.apply_stock_sentiment_overlay(results, signals)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(results[0]["total_score"], 0.65, places=2)
        self.assertAlmostEqual(results[1]["total_score"], 0.1, places=2)
        self.assertEqual(results[2]["total_score"], 0.1)

    def test_apply_stock_sentiment_overlay_neutral_no_change(self):
        adjuster = AIFactorAdjuster(self._make_config())
        signals = {
            "stock_mentions": [
                {"code": "600519", "name": "贵州茅台", "sentiment": 0.1, "context": "中性"},
            ]
        }
        results = [{"code": "600519", "total_score": 0.5}]
        count = adjuster.apply_stock_sentiment_overlay(results, signals)
        self.assertEqual(count, 0)
        self.assertEqual(results[0]["total_score"], 0.5)

    def test_get_macro_notes(self):
        adjuster = AIFactorAdjuster(self._make_config())
        signals = {
            "macro_sentiment": 0.6,
            "style_bias": "growth",
            "hot_sectors": ["半导体", "电力"],
            "risk_events": ["地缘冲突"],
        }
        notes = adjuster.get_macro_notes(signals)
        self.assertTrue(any("偏多" in n for n in notes))
        self.assertTrue(any("成长" in n for n in notes))
        self.assertTrue(any("半导体" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
