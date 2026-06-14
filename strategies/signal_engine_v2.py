"""
综合信号引擎 v2.1
核心改进：
1. 动态权重：根据市场状态切换因子权重
2. 一票否决：对趋势向下/主力流出/顶背离死叉等硬条件直接排除
3. 信号冲突处理：看跌信号优先级高于看涨，冲突时强制保守
4. 资金面降权/惩罚：主力大幅流出时不仅低分，还触发额外惩罚
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional


class SignalEngineV2:
    """综合信号引擎 v2：基本面 + 技术面 + 动量 + 资金面 + 风控 + 一票否决"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.weights = self.config.get("signal_weights", {}).get("base", {
            "fundamental": 0.25,
            "technical": 0.40,
            "momentum": 0.10,
            "capital_flow": 0.25
        })
        self.veto_cfg = self.config.get("veto_rules", {})
        self.conflict_cfg = self.config.get("signal_conflict", {})

    def set_weights(self, weights: Dict[str, float]):
        """动态设置权重（通常由 MarketRegimeDetector 调用）"""
        self.weights = weights.copy()

    def calculate_momentum(self, hist_df: pd.DataFrame) -> Dict:
        """动量评分：近5日、20日、60日涨跌幅 + 趋势强度（与v1一致，增加更多元数据）"""
        if hist_df.empty or len(hist_df) < 60:
            return {
                "score": 0,
                "signals": [],
                "r5": 0, "r20": 0, "r60": 0,
                "trend_aligned_up": False,
                "trend_aligned_down": False
            }

        volatility = None  # 在作用域顶部初始化，避免 locals() 依赖
        close = hist_df["收盘"]
        latest = close.iloc[-1]
        d5 = close.iloc[-5] if len(hist_df) >= 5 else latest
        d20 = close.iloc[-20] if len(hist_df) >= 20 else latest
        d60 = close.iloc[-60] if len(hist_df) >= 60 else latest

        r5 = (latest - d5) / d5 * 100 if d5 > 0 else 0
        r20 = (latest - d20) / d20 * 100 if d20 > 0 else 0
        r60 = (latest - d60) / d60 * 100 if d60 > 0 else 0

        score = 0.0
        signals = []

        # 5日动量
        if r5 > 8:
            score += 0.15
            signals.append(f"5日强涨{r5:.1f}%")
        elif r5 > 3:
            score += 0.08
            signals.append(f"5日涨{r5:.1f}%")
        elif r5 < -8:
            score -= 0.2
            signals.append(f"5日大跌{r5:.1f}%")
        elif r5 < -3:
            score -= 0.1
            signals.append(f"5日跌{r5:.1f}%")

        # 20日动量
        if r20 > 15:
            score += 0.25
            signals.append(f"20日强涨{r20:.1f}%")
        elif r20 > 5:
            score += 0.12
            signals.append(f"20日涨{r20:.1f}%")
        elif r20 < -15:
            score -= 0.25
            signals.append(f"20日大跌{r20:.1f}%")
        elif r20 < -5:
            score -= 0.12
            signals.append(f"20日跌{r20:.1f}%")

        # 60日动量
        if r60 > 30:
            score += 0.3
            signals.append(f"60日强涨{r60:.1f}%")
        elif r60 > 10:
            score += 0.15
            signals.append(f"60日涨{r60:.1f}%")
        elif r60 < -20:
            score -= 0.3
            signals.append(f"60日深跌{r60:.1f}%")
        elif r60 < 0:
            score -= 0.1
            signals.append(f"60日微跌{r60:.1f}%")

        trend_aligned_up = r5 > 0 and r20 > 0 and r60 > 0
        trend_aligned_down = r5 < 0 and r20 < 0 and r60 < 0

        if trend_aligned_up:
            score += 0.1
            signals.append("趋势一致向上")
        elif trend_aligned_down:
            score -= 0.1
            signals.append("趋势一致向下")

        # === P2: 趋势质量评估 ===
        # 20日最大回撤
        recent_20 = close.iloc[-20:]
        peak = recent_20.cummax()
        drawdown = (recent_20 - peak) / peak * 100
        max_dd_20 = drawdown.min()

        # 回调控制：最大回撤超过10%扣分
        if max_dd_20 < -10:
            score -= 0.15
            signals.append(f"近期回撤{max_dd_20:.1f}%")

        # 波动率控制：20日波动率过大扣分
        returns_20 = close.iloc[-20:].pct_change().dropna()
        if len(returns_20) > 1:
            volatility = returns_20.std() * np.sqrt(20) * 100
            if volatility > 25:
                score -= 0.10
                signals.append(f"高波动{volatility:.1f}%")

        # 强势整理加分：上涨趋势中回调小
        if r5 > 0 and r20 > 0 and max_dd_20 > -5:
            score += 0.08
            signals.append("强势整理")

        return {
            "score": round(max(-1, min(1, score)), 2),
            "signals": signals,
            "r5": round(r5, 2),
            "r20": round(r20, 2),
            "r60": round(r60, 2),
            "trend_aligned_up": trend_aligned_up,
            "trend_aligned_down": trend_aligned_down,
            "max_dd_20": round(max_dd_20, 2),
            "volatility": round(volatility, 2) if volatility is not None else None
        }

    def calculate_capital_flow(self, code: str, spot_row: pd.Series,
                               moneyflow_df: pd.DataFrame = None,
                               top_list_df: pd.DataFrame = None,
                               top_inst_df: pd.DataFrame = None) -> Dict:
        """资金面评分：资金流向 + 龙虎榜 + 机构席位 + 换手率（v2保留v1逻辑，增加原始数据输出）"""
        score = 0.0
        signals = []
        details = {
            "主力净流入": 0,
            "散户净流出": 0,
            "净流入占比": 0,
            "换手率": 0
        }

        main_net = 0  # 万元
        net_ratio = 0
        turnover = 0

        # ---------- 1. 资金流向 ----------
        if moneyflow_df is not None and not moneyflow_df.empty:
            mf = moneyflow_df[moneyflow_df.get("代码", "") == code]
            if not mf.empty:
                r = mf.iloc[0]
                main_net = float(r.get("主力净流入", 0) or 0)
                retail_net = float(r.get("散户净流出", 0) or 0)
                net_ratio = float(r.get("净流入占比", 0) or 0)
                details["主力净流入"] = round(main_net, 2)
                details["散户净流出"] = round(retail_net, 2)
                details["净流入占比"] = round(net_ratio * 100, 2)

                if main_net > 5000:
                    score += 0.25
                    signals.append("主力大幅流入")
                elif main_net > 1000:
                    score += 0.15
                    signals.append("主力流入")
                elif main_net < -5000:
                    score -= 0.2
                    signals.append("主力大幅流出")
                elif main_net < -1000:
                    score -= 0.1
                    signals.append("主力流出")

                if retail_net < -500:
                    score += 0.15
                    signals.append("散户抛压")
                elif retail_net > 1000:
                    score -= 0.1
                    signals.append("散户追涨")

                if net_ratio > 0.05:
                    score += 0.1
                    signals.append(f"资金占比+{net_ratio*100:.1f}%")
                elif net_ratio < -0.05:
                    score -= 0.1
                    signals.append(f"资金占比{net_ratio*100:.1f}%")

        # ---------- 2. 龙虎榜 ----------
        if top_list_df is not None and not top_list_df.empty:
            tl = top_list_df[top_list_df.get("代码", "") == code]
            if not tl.empty:
                r = tl.iloc[0]
                inst_net = float(r.get("机构净买入", 0) or 0)
                net_amt = float(r.get("net_amount", 0) or 0)
                details["龙虎榜机构净买"] = round(inst_net, 2)
                details["龙虎榜净额"] = round(net_amt, 2)

                if inst_net > 5000:
                    score += 0.2
                    signals.append("机构爆买")
                elif inst_net > 1000:
                    score += 0.1
                    signals.append("机构买入")
                elif inst_net < -3000:
                    score -= 0.15
                    signals.append("机构大卖")

                if net_amt > 0:
                    score += 0.05
                    signals.append("龙虎榜净买")
                elif net_amt < 0:
                    score -= 0.05
                    signals.append("龙虎榜净卖")

        # ---------- 3. 龙虎榜机构席位明细 ----------
        if top_inst_df is not None and not top_inst_df.empty:
            ti = top_inst_df[top_inst_df.get("代码", "") == code]
            if not ti.empty:
                r = ti.iloc[0]
                net_buy = float(r.get("机构净买入", 0) or 0)
                details["机构席位净买"] = round(net_buy, 2)
                if net_buy > 0:
                    score += 0.1
                    signals.append("机构席位买入")
                elif net_buy < 0:
                    score -= 0.05

        # ---------- 4. 换手率 ----------
        turnover = spot_row.get("换手率", 0)
        if turnover and not pd.isna(turnover):
            turnover = float(turnover)
            if 2 <= turnover <= 10:
                score += 0.05
                signals.append(f"换手活跃{turnover:.1f}%")
            elif turnover > 20:
                score -= 0.1
                signals.append(f"换手过高{turnover:.1f}%")
            elif turnover < 0.5:
                score -= 0.05
                signals.append(f"流动性差{turnover:.1f}%")
            details["换手率"] = round(turnover, 2)

        # ---------- 5. 量比 (Phase 3: 实时成交量异常检测) ----------
        volume_ratio = spot_row.get("量比", 0)
        if volume_ratio and not pd.isna(volume_ratio):
            volume_ratio = float(volume_ratio)
            details["量比"] = round(volume_ratio, 2)
            # 量比 0.8-1.5 正常，<0.5 极度缩量，>3 异常放量
            if volume_ratio > 3:
                # 需要结合价格方向判断；这里只标记，由 combine 结合价量方向处理
                signals.append(f"量比异常{volume_ratio:.1f}")
            elif volume_ratio < 0.5:
                score -= 0.05
                signals.append(f"极度缩量{volume_ratio:.2f}")
            elif 1.5 <= volume_ratio <= 3:
                score += 0.05
                signals.append(f"量比放大{volume_ratio:.1f}")

        return {
            "score": round(max(-1, min(1, score)), 2),
            "signals": signals,
            "details": details,
            "main_net": main_net,
            "net_ratio": net_ratio,
            "turnover": turnover
        }

    def apply_veto_rules(self, fundamental: Dict, technical: Dict, momentum: Dict,
                         capital_flow: Dict) -> Optional[str]:
        """
        一票否决检测。
        返回: None 表示通过，字符串表示被哪条规则否决。
        """
        if not self.veto_cfg.get("enabled", False):
            return None

        tech_details = technical.get("details", {})
        cf_details = capital_flow.get("details", {})
        main_net = cf_details.get("主力净流入", 0)

        for rule in self.veto_cfg.get("rules", []):
            name = rule.get("name", "未命名规则")
            cond = rule.get("condition", {})
            matched = True

            for key, value in cond.items():
                if key == "momentum_r5_lt":
                    if not (momentum.get("r5", 0) < value):
                        matched = False
                elif key == "momentum_r20_lt":
                    if not (momentum.get("r20", 0) < value):
                        matched = False
                elif key == "momentum_r60_lt":
                    if not (momentum.get("r60", 0) < value):
                        matched = False
                elif key == "capital_main_net_lt":
                    if not (main_net < value):
                        matched = False
                elif key == "macd_divergence_eq":
                    if tech_details.get("macd_divergence") != value:
                        matched = False
                elif key == "macd_dead_eq":
                    if tech_details.get("macd_dead") != value:
                        matched = False
                elif key == "ma_bearish_eq":
                    if tech_details.get("ma_bearish") != value:
                        matched = False
                elif key == "rsi_gt":
                    rsi = technical.get("details", {}).get("rsi", 0)
                    if not (rsi > value):
                        matched = False
                else:
                    matched = False

                if not matched:
                    break

            if matched:
                return name

        return None

    def resolve_signal_conflicts(self, technical: Dict) -> Dict:
        """
        信号冲突处理：
        - 检测技术面中的看跌信号
        - 如有看跌信号，对技术面分数施加额外惩罚
        - 返回调整后的技术面分数以及是否触发冲突
        """
        if not self.conflict_cfg.get("enabled", False):
            return {"technical": technical, "conflict_triggered": False,
                    "bearish_signals": [], "penalty": 0}

        bearish_signals = self.conflict_cfg.get("bearish_priority_signals", [])
        multiplier = self.conflict_cfg.get("bearish_penalty_multiplier", 1.5)

        triggered_signals = []
        for sig in technical.get("signals", []):
            for bs in bearish_signals:
                if bs in sig:
                    triggered_signals.append(sig)
                    break

        if not triggered_signals:
            return {"technical": technical, "conflict_triggered": False,
                    "bearish_signals": [], "penalty": 0}

        # 计算惩罚：每出现一个看跌信号，额外扣除 technical 得分的一定比例
        # 这里采用简单策略：technical 原分 * (multiplier - 1) 作为额外惩罚
        original_tech_score = technical.get("score", 0)
        penalty = abs(original_tech_score) * (multiplier - 1) if original_tech_score < 0 else -0.15
        # 但惩罚要基于已有负面分数，若技术面分数为正但出现看跌信号，则强制归零并扣分
        if original_tech_score >= 0:
            penalty = -max(0.15 * len(triggered_signals), 0.15)

        new_tech_score = max(-1, min(1, original_tech_score + penalty))
        technical = dict(technical)
        technical["score"] = round(new_tech_score, 2)
        technical["signals"] = technical.get("signals", []) + ["[冲突]看跌信号优先"]

        return {
            "technical": technical,
            "conflict_triggered": True,
            "bearish_signals": triggered_signals,
            "penalty": round(penalty, 3)
        }

    def combine(self, fundamental: Dict, technical: Dict, momentum: Dict,
                capital_flow: Dict = None) -> Dict:
        """
        加权综合评分、一票否决、信号冲突处理、建议等级
        """
        cf = capital_flow or {"score": 0, "signals": [], "details": {}}

        # 1. 一票否决
        veto_reason = self.apply_veto_rules(fundamental, technical, momentum, cf)
        if veto_reason:
            return {
                "total_score": -1.0,
                "advice": "回避",
                "veto": True,
                "veto_reason": veto_reason,
                "details": {
                    "fundamental": fundamental,
                    "technical": technical,
                    "momentum": momentum,
                    "capital_flow": cf
                }
            }

        # 2. 信号冲突处理
        conflict_result = self.resolve_signal_conflicts(technical)
        technical_adjusted = conflict_result["technical"]

        # 3. 加权综合
        total = (
            fundamental["score"] * self.weights.get("fundamental", 0.25) +
            technical_adjusted["score"] * self.weights.get("technical", 0.30) +
            momentum["score"] * self.weights.get("momentum", 0.30) +
            cf["score"] * self.weights.get("capital_flow", 0.15)
        )

        # 资金面如果严重流出，额外惩罚综合分（避免15%权重不够）
        main_net = cf.get("details", {}).get("主力净流入", 0)
        if main_net < -5000:
            total -= 0.15
        elif main_net < -2000:
            total -= 0.08

        # 趋势一致向下，再次惩罚（动量权重已被计入，额外加码）
        if momentum.get("trend_aligned_down"):
            total -= 0.10

        # 趋势过滤器：防止"接飞刀"——价格跌破关键均线且中期走弱
        tech_details = technical_adjusted.get("details", {})
        below_ma60 = tech_details.get("below_ma60", False)
        below_ma250 = not tech_details.get("above_ma250", True)  # above_ma250 False/None -> below
        ma60_rising = tech_details.get("ma60_rising", True)
        r60 = momentum.get("r60", 0)
        r20 = momentum.get("r20", 0)

        if below_ma60 and not ma60_rising and r60 < -10:
            total -= 0.20
        elif below_ma60 and r60 < -10:
            total -= 0.10

        if below_ma250 and r20 < -5 and r60 < -10:
            total -= 0.10  # 叠加惩罚：年线下方且短中期均走弱

        total = round(max(-1, min(1, total)), 3)

        # 4. 建议等级（P0改进：收紧门槛，提升胜率）
        if total >= 0.55:
            advice = "强烈关注"
        elif total >= 0.35:
            advice = "关注"
        elif total >= 0.15:
            advice = "轻度关注"
        elif total > -0.15:
            advice = "观望"
        elif total > -0.35:
            advice = "谨慎"
        else:
            advice = "回避"

        # 5. 看跌信号冲突时强制降级
        if conflict_result["conflict_triggered"]:
            max_advice = self.conflict_cfg.get("max_advice_with_bearish", "观望")
            advice_rank = {
                "强烈关注": 5, "关注": 4, "轻度关注": 3,
                "观望": 2, "谨慎": 1, "回避": 0
            }
            max_rank = advice_rank.get(max_advice, 2)
            current_rank = advice_rank.get(advice, 0)
            if current_rank > max_rank:
                # 强制降到 max_advice 或更低一级
                if self.conflict_cfg.get("force_downgrade_when_conflict", True):
                    current_rank = max(0, max_rank - 1)
                else:
                    current_rank = max_rank
                rank_to_advice = {v: k for k, v in advice_rank.items()}
                advice = rank_to_advice.get(current_rank, max_advice)

        return {
            "total_score": total,
            "advice": advice,
            "conflict_triggered": conflict_result["conflict_triggered"],
            "bearish_signals": conflict_result["bearish_signals"],
            "details": {
                "fundamental": fundamental,
                "technical": technical_adjusted,
                "momentum": momentum,
                "capital_flow": cf
            }
        }

    def get_excluded_stocks(self, results: List[Dict]) -> List[Dict]:
        """汇总所有被一票否决的股票，用于报告展示"""
        excluded = []
        for r in results:
            if r.get("veto"):
                excluded.append({
                    "code": r.get("code"),
                    "name": r.get("name"),
                    "sector": r.get("sector"),
                    "reason": r.get("veto_reason"),
                    "total_score": r.get("total_score")
                })
        return excluded
