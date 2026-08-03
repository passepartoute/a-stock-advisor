"""筹码分布分析器"""
import pandas as pd
import numpy as np
from typing import Dict, List


class ChipAnalyzer:
    """基于筹码集中度、平均成本、获利比例打分"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.cfg = self.config.get("chip_concentration", {})
        self.enabled = self.cfg.get("enabled", True)

    def score(self, symbol: str, spot_row: pd.Series,
              chip_df: pd.DataFrame = None) -> Dict:
        """计算筹码因子得分 [-1, 1]"""
        if not self.enabled:
            return {"score": 0, "signals": [], "details": {}}

        if chip_df is None or chip_df.empty:
            return {"score": 0, "signals": ["筹码数据缺失"], "details": {}}

        row = chip_df[chip_df.get("代码", "").astype(str).str.strip() == str(symbol).strip()]
        if row.empty:
            return {"score": 0, "signals": ["筹码数据缺失"], "details": {}}

        r = row.iloc[0]
        close = float(spot_row.get("收盘价", 0) or 0)
        avg_cost = float(r.get("平均成本", 0) or 0)
        c90 = float(r.get("90%集中度", np.nan) or np.nan)
        c70 = float(r.get("70%集中度", np.nan) or np.nan)
        profit_ratio = float(r.get("获利比例", np.nan) or np.nan)

        details = {
            "90%集中度": round(c90, 2) if not pd.isna(c90) else None,
            "70%集中度": round(c70, 2) if not pd.isna(c70) else None,
            "平均成本": round(avg_cost, 2) if avg_cost > 0 else None,
            "获利比例": round(profit_ratio, 2) if not pd.isna(profit_ratio) else None,
        }

        if pd.isna(c90) and pd.isna(c70):
            return {"score": 0, "signals": ["筹码数据缺失"], "details": details}

        score = 0.0
        signals: List[str] = []
        combo = self.cfg.get("combination", {})
        score_cap = self.cfg.get("score_cap", 0.30)

        c90_cfg = self.cfg.get("concentration_90", {})
        c70_cfg = self.cfg.get("concentration_70", {})

        c90_very_low = c90_cfg.get("very_low", 10.0)
        c90_low = c90_cfg.get("low", 20.0)
        c90_high = c90_cfg.get("high", 40.0)
        c70_very_low = c70_cfg.get("very_low", 5.0)
        c70_low = c70_cfg.get("low", 10.0)
        c70_high = c70_cfg.get("high", 25.0)

        c90_ok = not pd.isna(c90)
        c70_ok = not pd.isna(c70)

        if (c90_ok and c90 <= c90_very_low) and (c70_ok and c70 <= c70_very_low):
            score += combo.get("strong_concentration_bonus", 0.25)
            signals.append("筹码高度集中")
        elif (c90_ok and c90 <= c90_low) or (c70_ok and c70 <= c70_low):
            score += combo.get("concentration_bonus", 0.15)
            signals.append("筹码较集中")
        elif (c90_ok and c90 >= c90_high) or (c70_ok and c70 >= c70_high):
            score += combo.get("dispersion_penalty", -0.15)
            signals.append("筹码分散")

        if close > 0 and avg_cost > 0:
            premium = (close - avg_cost) / avg_cost * 100
            details["成本溢价%"] = round(premium, 2)
            premium_thr = self.cfg.get("avg_cost", {}).get("premium_threshold", 5.0)
            discount_thr = self.cfg.get("avg_cost", {}).get("discount_threshold", 5.0)

            if premium >= premium_thr:
                score += combo.get("cost_premium_bonus", 0.15)
                signals.append(f"站上成本+{premium:.1f}%")
            elif premium >= 0:
                score += combo.get("cost_above_bonus", 0.08)
                signals.append("成本上方")
            elif premium <= -discount_thr:
                score += combo.get("cost_discount_penalty", -0.15)
                signals.append(f"跌破成本{abs(premium):.1f}%")

        if not pd.isna(profit_ratio):
            pr_cfg = self.cfg.get("profit_ratio", {})
            if profit_ratio >= pr_cfg.get("high", 60.0):
                score += 0.10
                signals.append("获利盘高")
            elif profit_ratio <= pr_cfg.get("low", 30.0):
                score += -0.10
                signals.append("套牢盘重")

        score = max(-score_cap, min(score_cap, score))
        score = max(-1, min(1, score))

        return {
            "score": round(score, 3),
            "signals": signals,
            "details": details
        }
