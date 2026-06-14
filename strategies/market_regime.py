"""
市场状态识别模块
根据大盘指数（上证指数）与年线的关系，判断当前市场处于 bull/bear/neutral，
并据此返回对应的因子权重配置。
"""
from typing import Dict, Literal


class MarketRegimeDetector:
    """检测当前市场状态，输出动态权重"""

    REGIME = Literal["bull_trend", "bear_trend", "neutral"]

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.dynamic_cfg = self.config.get("dynamic_weights", {})
        self.base_weights = self.config.get("signal_weights", {}).get("base", {
            "fundamental": 0.25,
            "technical": 0.40,
            "momentum": 0.10,
            "capital_flow": 0.25
        })

    def detect(self, market_env: dict) -> REGIME:
        """
        根据大盘环境判断市场状态
        - bull_trend: 大盘在年线之上
        - bear_trend: 大盘在年线之下
        - neutral: 震荡市（年线附近、波动率低）或数据缺失
        """
        above_ma250 = market_env.get("above_ma250")

        # === P3: 震荡市识别 ===
        # 当大盘在年线附近（±3%）且波动特征符合震荡时，判定为neutral
        sh_index = market_env.get("sh_index")
        sh_ma250 = market_env.get("sh_ma250")
        if sh_index and sh_ma250 and sh_ma250 > 0:
            distance_pct = abs(sh_index - sh_ma250) / sh_ma250
            # 距离年线很近（<2%）认为是震荡/转折期
            if distance_pct < 0.02:
                return "neutral"

        if above_ma250 is True:
            return "bull_trend"
        elif above_ma250 is False:
            return "bear_trend"
        else:
            return "neutral"

    def get_weights(self, regime: REGIME) -> Dict[str, float]:
        """获取对应市场状态的因子权重"""
        if not self.dynamic_cfg.get("enabled", False):
            return self.base_weights.copy()

        regime_weights = self.dynamic_cfg.get(regime)
        if regime_weights:
            return {
                "fundamental": regime_weights.get("fundamental", 0.25),
                "technical": regime_weights.get("technical", 0.30),
                "momentum": regime_weights.get("momentum", 0.30),
                "capital_flow": regime_weights.get("capital_flow", 0.15)
            }

        return self.base_weights.copy()

    def get_weights_for_env(self, market_env: dict) -> Dict[str, float]:
        """根据 market_env 直接返回权重"""
        regime = self.detect(market_env)
        return self.get_weights(regime)

    def describe(self, market_env: dict) -> str:
        """返回人类可读的市场状态描述"""
        regime = self.detect(market_env)
        desc_map = {
            "bull_trend": "上升趋势（大盘在年线之上）—— 提升动量权重",
            "bear_trend": "弱势市场（大盘在年线之下）—— 提升基本面与资金面防御权重",
            "neutral": "震荡/不明 —— 使用均衡权重"
        }
        return desc_map.get(regime, "未知")
