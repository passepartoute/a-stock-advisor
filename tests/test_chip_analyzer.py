import unittest
import pandas as pd
from strategies.chip_analyzer import ChipAnalyzer


class TestChipAnalyzer(unittest.TestCase):
    def setUp(self):
        self.config = {
            "chip_concentration": {
                "enabled": True,
                "weight": 0.15,
                "concentration_90": {"very_low": 10.0, "low": 20.0, "high": 40.0},
                "concentration_70": {"very_low": 5.0, "low": 10.0, "high": 25.0},
                "avg_cost": {"premium_threshold": 5.0, "discount_threshold": 5.0},
                "profit_ratio": {"high": 60.0, "low": 30.0},
                "combination": {
                    "strong_concentration_bonus": 0.25,
                    "concentration_bonus": 0.15,
                    "dispersion_penalty": -0.15,
                    "cost_premium_bonus": 0.15,
                    "cost_above_bonus": 0.08,
                    "cost_discount_penalty": -0.15,
                    "low_profit_penalty": -0.10,
                },
                "score_cap": 0.30,
            }
        }
        self.analyzer = ChipAnalyzer(self.config)

    def _make_chip_df(self, c90, c70, avg_cost, profit_ratio):
        return pd.DataFrame([{
            "代码": "000001",
            "90%集中度": c90,
            "70%集中度": c70,
            "平均成本": avg_cost,
            "获利比例": profit_ratio,
        }])

    def test_strong_concentration_and_above_cost(self):
        chip_df = self._make_chip_df(8.0, 4.0, 10.0, 70.0)
        spot = pd.Series({"收盘价": 11.0})
        result = self.analyzer.score("000001", spot, chip_df)
        self.assertGreater(result["score"], 0.25)
        self.assertIn("筹码高度集中", result["signals"])
        self.assertIn("站上成本+10.0%", result["signals"])
        self.assertIn("获利盘高", result["signals"])

    def test_dispersion_and_below_cost(self):
        chip_df = self._make_chip_df(45.0, 30.0, 10.0, 20.0)
        spot = pd.Series({"收盘价": 9.0})
        result = self.analyzer.score("000001", spot, chip_df)
        self.assertLess(result["score"], -0.20)
        self.assertIn("筹码分散", result["signals"])
        self.assertIn("跌破成本10.0%", result["signals"])

    def test_missing_data(self):
        result = self.analyzer.score("000001", pd.Series({"收盘价": 10.0}), None)
        self.assertEqual(result["score"], 0)
        self.assertIn("筹码数据缺失", result["signals"])

    def test_disabled(self):
        config = {"chip_concentration": {"enabled": False}}
        analyzer = ChipAnalyzer(config)
        chip_df = self._make_chip_df(8.0, 4.0, 10.0, 70.0)
        spot = pd.Series({"收盘价": 11.0})
        result = analyzer.score("000001", spot, chip_df)
        self.assertEqual(result["score"], 0)
        self.assertEqual(result["signals"], [])

    def test_zero_avg_cost(self):
        chip_df = self._make_chip_df(8.0, 4.0, 0.0, 70.0)
        spot = pd.Series({"收盘价": 11.0})
        result = self.analyzer.score("000001", spot, chip_df)
        self.assertGreater(result["score"], 0)
        self.assertNotIn("站上成本", " ".join(result["signals"]))


if __name__ == "__main__":
    unittest.main()
