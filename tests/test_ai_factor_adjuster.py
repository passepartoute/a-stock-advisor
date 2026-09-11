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

    # ---------- 改进#3：AI 热门行业反向验证 ----------

    def _make_reversal_config(self, enabled=True):
        cfg = self._make_config()
        cfg["ai_briefing"]["factor_adjustments"]["hot_sector_reversal"] = {
            "enabled": enabled,
            "r5_threshold": 8.0,
            "penalty": -0.10,
        }
        return cfg

    @staticmethod
    def _mk_result(code, sector, r5, total=0.30):
        return {
            "code": code, "name": code, "sector": sector,
            "total_score": total,
            "details": {"momentum": {"r5": r5}},
        }

    def test_hot_sector_reversal_applied_when_sector_overheated(self):
        """AI 热门行业且行业5日涨幅（中位数）>8%：综合分扣分"""
        adjuster = AIFactorAdjuster(self._make_reversal_config())
        signals = {"hot_sectors": ["半导体"]}
        results = [
            self._mk_result("A", "半导体", 12.0, total=0.30),
            self._mk_result("B", "半导体", 9.0, total=0.20),
            self._mk_result("C", "银行", 2.0, total=0.25),
        ]
        # 半导体 r5 中位数 = 10.5 > 8 -> 反转；银行不在热门名单
        count = adjuster.apply_hot_sector_reversal(results, signals)
        self.assertEqual(count, 2)
        self.assertAlmostEqual(results[0]["total_score"], 0.20, places=3)
        self.assertAlmostEqual(results[1]["total_score"], 0.10, places=3)
        self.assertAlmostEqual(results[2]["total_score"], 0.25, places=3)
        self.assertTrue(results[0]["ai_hot_reversal"])
        self.assertNotIn("ai_hot_reversal", results[2])

    def test_hot_sector_reversal_not_triggered_when_sector_cool(self):
        """行业5日涨幅低于阈值时不反转"""
        adjuster = AIFactorAdjuster(self._make_reversal_config())
        signals = {"hot_sectors": ["半导体"]}
        results = [
            self._mk_result("A", "半导体", 4.0),
            self._mk_result("B", "半导体", 5.0),
        ]
        count = adjuster.apply_hot_sector_reversal(results, signals)
        self.assertEqual(count, 0)
        self.assertAlmostEqual(results[0]["total_score"], 0.30, places=3)

    def test_hot_sector_reversal_disabled(self):
        """配置关闭时不做任何调整"""
        adjuster = AIFactorAdjuster(self._make_reversal_config(enabled=False))
        signals = {"hot_sectors": ["半导体"]}
        results = [self._mk_result("A", "半导体", 15.0)]
        count = adjuster.apply_hot_sector_reversal(results, signals)
        self.assertEqual(count, 0)
        self.assertAlmostEqual(results[0]["total_score"], 0.30, places=3)

    def test_hot_sector_reversal_no_signals(self):
        """无 AI 信号时返回 0"""
        adjuster = AIFactorAdjuster(self._make_reversal_config())
        results = [self._mk_result("A", "半导体", 15.0)]
        self.assertEqual(adjuster.apply_hot_sector_reversal(results, None), 0)

    def test_hot_sector_reversal_missing_momentum_skipped(self):
        """缺少动量数据的股票不影响行业统计，也不被扣分"""
        adjuster = AIFactorAdjuster(self._make_reversal_config())
        signals = {"hot_sectors": ["半导体"]}
        results = [
            {"code": "A", "sector": "半导体", "total_score": 0.30, "details": {}},
            self._mk_result("B", "半导体", 12.0),
        ]
        count = adjuster.apply_hot_sector_reversal(results, signals)
        # 行业统计只有 B(12.0) > 8 -> B 被扣分；A 无 r5 数据，跳过
        self.assertEqual(count, 1)
        self.assertAlmostEqual(results[0]["total_score"], 0.30, places=3)
        self.assertAlmostEqual(results[1]["total_score"], 0.20, places=3)


if __name__ == "__main__":
    unittest.main()
