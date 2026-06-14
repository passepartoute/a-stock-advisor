import pandas as pd
import numpy as np


class RiskManager:
    """风控管理 v2：止损、止盈、仓位建议，支持支撑/压力位计算"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.stop_loss_method = self.config.get("stop_loss_method", "support_resistance")
        self.stop_loss_pct = self.config.get("stop_loss_pct", -8.0)
        self.trailing_stop_pct = self.config.get("trailing_stop_pct", 8.0)
        self.target_method = self.config.get("target_method", "support_resistance")
        self.target_profit_pct = self.config.get("target_profit_pct", 30.0)
        self.support_lookback = self.config.get("support_lookback", 20)
        self.resistance_lookback = self.config.get("resistance_lookback", 60)
        self.conservative_target = self.config.get("conservative_target", True)
        self.ma20_exit_pct = self.config.get("ma20_exit_pct", 50)
        self.ma60_exit = self.config.get("ma60_exit", True)
        self.pe_percentile_sell = self.config.get("pe_percentile_sell", 80)

        # 仓位管理
        pos_cfg = self.config.get("position_management", {})
        self.max_holdings = pos_cfg.get("max_holdings", 10)
        self.min_holdings = pos_cfg.get("min_holdings", 5)
        self.max_single_position = pos_cfg.get("max_single_position", 20)
        self.max_conviction_position = pos_cfg.get("max_conviction_position", 30)
        self.max_sector_position = pos_cfg.get("max_sector_position", 30)
        self.cash_reserve = pos_cfg.get("cash_reserve", 15)

    def _calculate_support_resistance(self, hist_df: pd.DataFrame) -> dict:
        """基于历史K线计算近期支撑/压力位"""
        result = {
            "support": None,
            "resistance": None,
            "recent_low": None,
            "recent_high": None,
            "ma20": None,
            "ma60": None
        }
        if hist_df is None or hist_df.empty or len(hist_df) < 20:
            return result

        close = hist_df["收盘"]
        low = hist_df.get("最低", close)
        high = hist_df.get("最高", close)

        # 近期低点支撑
        support_window = min(self.support_lookback, len(hist_df))
        result["recent_low"] = round(low.iloc[-support_window:].min(), 2)
        result["recent_high"] = round(high.iloc[-self.resistance_lookback:].max(), 2) if len(hist_df) >= self.resistance_lookback else None

        # 均线支撑
        if len(hist_df) >= 20:
            result["ma20"] = round(close.iloc[-20:].mean(), 2)
        if len(hist_df) >= 60:
            result["ma60"] = round(close.iloc[-60:].mean(), 2)

        # 支撑位取 recent_low 与 MA20 中较高者，但不能高于当前价
        latest_price = float(close.iloc[-1])
        candidates = [result["recent_low"], result["ma20"]]
        valid = [c for c in candidates if c is not None]
        if valid:
            raw_support = max(valid)
            # 如果股价已跌破所有常规支撑，回退到固定比例止损（不计入 support）
            if raw_support > latest_price:
                result["support"] = None
                result["support_broken"] = True
            else:
                result["support"] = raw_support
                result["support_broken"] = False

        # 压力位取近期高点
        result["resistance"] = result["recent_high"]

        return result

    def get_risk_advice(self, code: str, name: str, latest_price: float,
                        total_score: float, technical_details: dict,
                        hist_df: pd.DataFrame = None) -> dict:
        """
        根据当前价格、历史K线、评分，给出风控建议
        """
        advice = {
            "stop_loss_price": None,
            "trailing_stop_price": None,
            "target_price": None,
            "ma20_support": None,
            "ma60_support": None,
            "support": None,
            "resistance": None,
            "position_pct": None,
            "risk_notes": []
        }

        if latest_price <= 0:
            return advice

        sr = self._calculate_support_resistance(hist_df) if hist_df is not None else {}

        # ---------- 止损位 ----------
        if self.stop_loss_method == "support_resistance" and sr.get("support"):
            support = sr["support"]
            # 支撑位不能比固定止损更宽松（即跌幅不能超过固定止损比例的2倍）
            max_sl = latest_price * (1 + self.stop_loss_pct / 100)
            if support > max_sl * 0.95:  # 允许支撑位比固定止损略低5%
                stop_price = round(support, 2)
                advice["stop_loss_price"] = stop_price
                loss_pct = (stop_price - latest_price) / latest_price * 100
                advice["risk_notes"].append(f"止损位: {stop_price} ({loss_pct:.1f}%, 近期支撑)")
            else:
                stop_price = round(max_sl, 2)
                advice["stop_loss_price"] = stop_price
                advice["risk_notes"].append(f"止损位: {stop_price} ({self.stop_loss_pct}%, 固定比例)")
            advice["support"] = support
        elif self.stop_loss_method == "support_resistance" and sr.get("support_broken"):
            # 股价已跌破所有常规支撑位，回退到固定比例止损
            stop_price = round(latest_price * (1 + self.stop_loss_pct / 100), 2)
            advice["stop_loss_price"] = stop_price
            advice["risk_notes"].append(f"止损位: {stop_price} ({self.stop_loss_pct}%, 支撑已破)")
        else:
            stop_price = round(latest_price * (1 + self.stop_loss_pct / 100), 2)
            advice["stop_loss_price"] = stop_price
            advice["risk_notes"].append(f"止损位: {stop_price} ({self.stop_loss_pct}%, 固定比例)")

        # ---------- 目标价 ----------
        fixed_target = round(latest_price * (1 + self.target_profit_pct / 100), 2)
        if self.target_method == "support_resistance" and sr.get("resistance"):
            resistance = sr["resistance"]
            if self.conservative_target:
                target_price = min(resistance, fixed_target)
            else:
                target_price = resistance
            target_price = round(target_price, 2)
            gain_pct = (target_price - latest_price) / latest_price * 100
            advice["target_price"] = target_price
            if target_price == resistance and resistance < fixed_target:
                advice["risk_notes"].append(f"目标价: {target_price} (+{gain_pct:.1f}%, 近期压力位，低于固定比例)")
            else:
                advice["risk_notes"].append(f"目标价: {target_price} (+{gain_pct:.1f}%, 压力位/固定比例)")
            advice["resistance"] = resistance
        else:
            advice["target_price"] = fixed_target
            advice["risk_notes"].append(f"目标价: {fixed_target} (+{self.target_profit_pct}%, 固定比例)")

        # ---------- 移动止盈 ----------
        trailing_price = round(latest_price * (1 - self.trailing_stop_pct / 100), 2)
        advice["trailing_stop_price"] = trailing_price
        advice["risk_notes"].append(f"移动止盈: 从高点回撤{self.trailing_stop_pct}%卖出")

        # ---------- 盈亏比 ----------
        rr = None
        if advice["stop_loss_price"] and advice["target_price"]:
            reward = advice["target_price"] - latest_price
            risk = latest_price - advice["stop_loss_price"]
            if risk > 0:
                rr = reward / risk
                advice["risk_ratio"] = round(rr, 2)
                advice["risk_notes"].append(f"盈亏比: 1:{rr:.2f}")
        if rr is None:
            advice["risk_ratio"] = None

        # ---------- 均线支撑 ----------
        if technical_details:
            ma20 = technical_details.get("ma20_support")
            ma60 = technical_details.get("ma60_support")
            if ma20:
                advice["ma20_support"] = round(ma20, 2)
                advice["risk_notes"].append(f"跌破MA20({round(ma20,2)})减仓{self.ma20_exit_pct}%")
            if ma60:
                advice["ma60_support"] = round(ma60, 2)
                if self.ma60_exit:
                    advice["risk_notes"].append(f"跌破MA60({round(ma60,2)})清仓")

        # 合并从 hist_df 计算的均线
        if sr.get("ma20"):
            advice["ma20_support"] = sr["ma20"]
        if sr.get("ma60"):
            advice["ma60_support"] = sr["ma60"]

        # ---------- 仓位建议 ----------
        position_pct = self._suggest_position(total_score)
        advice["position_pct"] = position_pct
        advice["risk_notes"].append(f"建议仓位: {position_pct}%")

        return advice

    def _suggest_position(self, total_score: float) -> int:
        """根据综合评分建议仓位比例"""
        if total_score >= 0.6:
            return self.max_conviction_position
        elif total_score >= 0.4:
            return self.max_single_position
        elif total_score >= 0.2:
            return min(15, self.max_single_position)
        elif total_score >= 0:
            return min(10, self.max_single_position)
        else:
            return 0

    def get_portfolio_advice(self, results: list) -> dict:
        """组合层面的仓位管理建议"""
        strong = [r for r in results if r["advice"] in ("强烈关注", "关注")]
        n_strong = len(strong)

        if n_strong >= self.max_holdings:
            target_count = self.max_holdings
        elif n_strong >= self.min_holdings:
            target_count = n_strong
        else:
            target_count = n_strong

        total_position = 100 - self.cash_reserve
        if target_count > 0:
            avg_position = total_position // target_count
        else:
            avg_position = 0

        return {
            "cash_reserve_pct": self.cash_reserve,
            "target_holdings": target_count,
            "avg_position_pct": avg_position,
            "strong_candidates": n_strong,
            "notes": [
                f"建议持仓 {target_count} 只",
                f"现金储备 {self.cash_reserve}%",
                f"平均每只仓位约 {avg_position}%"
            ]
        }
