"""
AI 信号 → 量化因子调整器

把 AIBriefingAnalyzer 提取的宏观信号映射为可量化的选股调整：
1. 因子权重调整（基于宏观情绪 + 风格偏好）
2. 基本面行业得分调整（热门/冷门/政策/风险）
3. 个股情绪叠加（仅对简报明确提及的个股）

本模块纯函数，无外部调用，便于测试。
"""
from typing import Dict, Any, List, Tuple, Optional


class AIFactorAdjuster:
    """AI 驱动的因子调整器"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.ai_cfg = self.config.get("ai_briefing", {})
        self.fa_cfg = self.ai_cfg.get("factor_adjustments", {})
        self.enabled = self.fa_cfg.get("enabled", True)

    def adjust_weights(self, base_weights: Dict[str, float],
                       ai_signals: Dict[str, Any]) -> Dict[str, float]:
        """
        根据宏观情绪和风格偏好调整因子权重。
        返回新的权重 dict，保证各项和为 1.0。
        """
        if not self.enabled or not ai_signals:
            return base_weights.copy()

        new_weights = base_weights.copy()
        macro = float(ai_signals.get("macro_sentiment", 0))
        style = str(ai_signals.get("style_bias", "neutral")).lower()

        # 宏观情绪映射
        if macro > 0.3:
            macro_regime = "bullish"
        elif macro < -0.3:
            macro_regime = "bearish"
        else:
            macro_regime = "neutral"

        macro_shift = self.fa_cfg.get("macro_weight_map", {}).get(macro_regime, {})
        style_shift = self.fa_cfg.get("style_weight_map", {}).get(style, {})

        cap = self.fa_cfg.get("weight_shift_cap", 0.15)

        for key in new_weights:
            delta = macro_shift.get(key, 0.0) + style_shift.get(key, 0.0)
            delta = max(-cap, min(cap, delta))
            new_weights[key] += delta

        # 归一化
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}
        else:
            # 极端异常，回退到 base
            return base_weights.copy()

        return new_weights

    def adjust_fundamental_score(self, score: float, sector: str,
                                 ai_signals: Dict[str, Any]) -> float:
        """
        根据 AI 行业信号调整基本面得分。
        返回调整后的得分（已 clip 到 [-1, 1]）。
        """
        if not self.enabled or not ai_signals:
            return score

        delta = 0.0
        sector = str(sector or "")

        ff_cfg = self.fa_cfg.get("fundamental", {})

        hot_sectors = ai_signals.get("hot_sectors", [])
        cold_sectors = ai_signals.get("cold_sectors", [])
        policy_themes = ai_signals.get("policy_themes", [])
        risk_events = ai_signals.get("risk_events", [])

        if self._sector_match(sector, hot_sectors):
            delta += ff_cfg.get("hot_sector_bonus", 0.10)
        if self._sector_match(sector, cold_sectors):
            delta += ff_cfg.get("cold_sector_penalty", -0.10)
        if self._sector_match(sector, policy_themes):
            delta += ff_cfg.get("policy_theme_bonus", 0.08)
        if self._sector_match(sector, risk_events):
            delta += ff_cfg.get("risk_event_penalty", -0.15)

        max_delta = self.fa_cfg.get("max_total_score_delta", 0.20)
        delta = max(-max_delta, min(max_delta, delta))

        return round(max(-1.0, min(1.0, score + delta)), 3)

    def apply_stock_sentiment_overlay(self, results: List[Dict[str, Any]],
                                      ai_signals: Dict[str, Any]) -> int:
        """
        对简报中明确提及的个股施加情绪叠加。
        返回实际影响的个股数量。
        """
        if not self.enabled or not ai_signals:
            return 0

        ss_cfg = self.fa_cfg.get("stock_sentiment", {})
        if not ss_cfg.get("enabled", True):
            return 0

        mentions = {m["code"]: m for m in ai_signals.get("stock_mentions", [])
                    if isinstance(m, dict) and m.get("code")}
        if not mentions:
            return 0

        positive_bonus = ss_cfg.get("positive_bonus", 0.15)
        negative_penalty = ss_cfg.get("negative_penalty", -0.20)

        count = 0
        for r in results:
            code = str(r.get("code", "")).strip()
            if code not in mentions:
                continue

            sentiment = float(mentions[code].get("sentiment", 0))
            delta = 0.0
            if sentiment > 0.2:
                delta = positive_bonus
            elif sentiment < -0.2:
                delta = negative_penalty

            if delta != 0:
                r["total_score"] = round(max(-1.0, min(1.0, r["total_score"] + delta)), 3)
                r["ai_sentiment_delta"] = delta
                r["ai_sentiment_reason"] = (
                    f"AI简报提及: {mentions[code].get('name', '')} "
                    f"({mentions[code].get('context', '')[:40]})"
                )
                count += 1

        return count

    def get_macro_notes(self, ai_signals: Dict[str, Any]) -> List[str]:
        """生成报告中使用的人可读宏观注释"""
        if not ai_signals:
            return []

        macro = float(ai_signals.get("macro_sentiment", 0))
        if macro > 0.3:
            sentiment_str = "偏多"
        elif macro < -0.3:
            sentiment_str = "偏空"
        else:
            sentiment_str = "中性"

        style_map = {
            "growth": "成长",
            "value": "价值",
            "defensive": "防御",
            "momentum": "动量",
            "neutral": "均衡",
        }
        style = style_map.get(str(ai_signals.get("style_bias", "neutral")).lower(), "均衡")

        notes = [
            f"宏观情绪: {sentiment_str} ({macro:+.2f})",
            f"风格偏好: {style}",
            f"热门行业: {', '.join(ai_signals.get('hot_sectors', [])[:8])}",
            f"风险事件: {', '.join(ai_signals.get('risk_events', [])[:5])}",
        ]
        return notes

    @staticmethod
    def _sector_match(sector: str, keywords: List[str]) -> bool:
        """行业关键词子串匹配"""
        if not sector or not keywords:
            return False
        sector = str(sector)
        return any(kw and kw in sector for kw in keywords)

    def adjust_sector_filter(self, preferred_sectors: List[str],
                             excluded_sectors: List[str],
                             ai_signals: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        """
        根据 AI 信号扩展行业白名单和黑名单。
        返回 (new_preferred, new_excluded)。
        """
        if not self.enabled or not ai_signals:
            return preferred_sectors.copy(), excluded_sectors.copy()

        preferred = list(preferred_sectors)
        excluded = list(excluded_sectors)

        for s in ai_signals.get("hot_sectors", []):
            if s and s not in preferred:
                preferred.append(s)
        for s in ai_signals.get("cold_sectors", []):
            if s and s not in excluded:
                excluded.append(s)

        return preferred, excluded
