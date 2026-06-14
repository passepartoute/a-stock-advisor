"""
开盘前挂单规划器
为每日推荐股票生成开盘前限价单、条件单参数和高开/低开应对策略。
"""
from typing import Dict, List, Optional


# 哪些建议等级值得设挂单
_ORDERABLE_ADVICE = {"强烈关注", "关注", "轻度关注"}


class PreMarketPlanner:
    """基于前日风控数据，生成开盘前挂单指令"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.pmo_cfg = self.config.get("pre_market_order", {})
        self.gap_up_cancel_pct = self.pmo_cfg.get("gap_up_cancel_pct", 3.0)
        self.gap_up_reduce_pct = self.pmo_cfg.get("gap_up_reduce_pct", 1.0)
        self.gap_down_safe_pct = self.pmo_cfg.get("gap_down_safe_pct", 2.0)
        self.ma20_premium_max_pct = self.pmo_cfg.get("ma20_premium_max_pct", 5.0)
        self.ma20_premium_near_pct = self.pmo_cfg.get("ma20_premium_near_pct", 2.0)
        # 从风控配置拿移动止盈回撤比例
        risk_cfg = self.config.get("risk_management", {})
        self.trailing_stop_pct = risk_cfg.get("trailing_stop_pct", 5.0)

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def generate_all(self, results: list) -> List[Dict]:
        """对值得挂单的股票批量生成挂单计划"""
        orders = []
        seen = set()
        for r in results:
            advice = r.get("advice", "")
            code = r.get("code", "")
            if advice not in _ORDERABLE_ADVICE:
                continue
            # 行业分散后可能出现重复（理论上不会，但加一层去重）
            if code in seen:
                continue
            seen.add(code)
            plan = self.plan_order(r)
            if plan:
                orders.append(plan)
        return orders

    def plan_order(self, stock_result: dict) -> Optional[Dict]:
        """
        为单只股票生成开盘前挂单方案。

        stock_result 需包含字段：
          code, name, advice, latest_price, ma20,
          risk: { stop_loss_price, target_price, position_pct, support }

        返回 dict:
          code, name, advice, suggested_price, position_pct,
          stop_loss_price, target_price,
          gap_up_cancel, gap_up_reduce, gap_normal, gap_down_safe, gap_down_cancel,
          order_params: { stop_loss_trigger, take_profit_trigger, trailing_stop_desc }
        """
        code = stock_result.get("code", "?")
        name = stock_result.get("name", "?")
        advice = stock_result.get("advice", "")
        latest_price = stock_result.get("latest_price", 0)
        ma20 = stock_result.get("ma20", latest_price)
        risk = stock_result.get("risk", {}) or {}

        stop_loss_price = risk.get("stop_loss_price", 0)
        target_price = risk.get("target_price", 0)
        position_pct = risk.get("position_pct", 10)

        if latest_price <= 0:
            return None

        # ---- 建议挂单价格 ----
        suggested_price = self._calc_suggested_price(latest_price, ma20, stop_loss_price)

        # ---- 高开/低开决策矩阵 ----
        gap_rules = self._calc_gap_rules(latest_price, stop_loss_price, position_pct, advice)

        # ---- 条件单参数 ----
        order_params = self._calc_order_params(stop_loss_price, target_price)

        # 计算相对于前日收盘的涨跌空间
        if stop_loss_price and stop_loss_price > 0:
            sl_pct = round((stop_loss_price - latest_price) / latest_price * 100, 1)
            risk = latest_price - stop_loss_price
        else:
            sl_pct = None
            risk = 0

        if target_price and target_price > 0:
            tp_pct = round((target_price - latest_price) / latest_price * 100, 1)
            reward = target_price - latest_price
        else:
            tp_pct = None
            reward = 0

        if risk > 0 and reward > 0:
            risk_reward = round(reward / risk, 2)
        else:
            risk_reward = None

        return {
            "code": code,
            "name": name,
            "advice": advice,
            "latest_price": latest_price,
            "ma20": ma20,
            "suggested_price": suggested_price,
            "position_pct": position_pct,
            "stop_loss_price": stop_loss_price,
            "target_price": target_price,
            "stop_loss_pct": sl_pct,
            "target_pct": tp_pct,
            "risk_reward": risk_reward,
            "gap_rules": gap_rules,
            "order_params": order_params,
        }

    # ------------------------------------------------------------------
    # 私有计算
    # ------------------------------------------------------------------

    def _calc_suggested_price(self, latest_price: float, ma20: float,
                              stop_loss_price: float) -> float:
        """计算建议限价挂单价"""
        if ma20 <= 0:
            return round(latest_price, 2)

        premium = (latest_price - ma20) / ma20 * 100  # 收盘价相对 MA20 的偏离

        if premium <= self.ma20_premium_near_pct:
            # 靠近 MA20：直接按前日收盘价挂
            suggested = latest_price
        elif premium <= self.ma20_premium_max_pct:
            # 略高于 MA20：取收盘价与 MA20 的中点
            suggested = (latest_price + ma20) / 2
        else:
            # 偏离 MA20 较大：挂单设在 MA20 上方 1%，等回调
            suggested = ma20 * (1 + 0.01)

        # 下限保护：不能低于止损价上方 0.5%
        if stop_loss_price and stop_loss_price > 0:
            floor = stop_loss_price * 1.005
            suggested = max(suggested, floor)

        return round(suggested, 2)

    def _calc_gap_rules(self, latest_price: float, stop_loss_price: float,
                        position_pct: int, advice: str) -> List[Dict]:
        """
        生成高开/低开决策矩阵。返回不同开盘场景的处理规则列表。
        """
        rules = []

        # 场景 1：高开 > 3%
        rules.append({
            "scenario": f"高开 >{self.gap_up_cancel_pct}%",
            "action": "CANCEL",
            "description": "放弃挂单，大幅高开后追高风险大，等待回调再考虑",
            "position": 0,
        })

        # 场景 2：高开 1-3%
        half_pos = max(5, position_pct // 2)
        rules.append({
            "scenario": f"高开 {self.gap_up_reduce_pct}%-{self.gap_up_cancel_pct}%",
            "action": "REDUCE",
            "description": f"仓位减半至 {half_pos}%，限价维持不追高",
            "position": half_pos,
        })

        # 场景 3：平开 ±1%
        rules.append({
            "scenario": "平开 ±1%",
            "action": "NORMAL",
            "description": f"正常挂单，仓位 {position_pct}%，限价执行",
            "position": position_pct,
        })

        # 场景 4：低开 0~-2%（未破止损）
        sl_pct = round((stop_loss_price - latest_price) / latest_price * 100, 1) if stop_loss_price else -5
        rules.append({
            "scenario": f"低开 0%~-{self.gap_down_safe_pct}%（未破止损）",
            "action": "NORMAL",
            "description": "正常挂单，低开反而提供更好买点",
            "position": position_pct,
        })

        # 场景 5：低开跌破止损
        rules.append({
            "scenario": f"低开跌破止损价（>{abs(sl_pct)}%）",
            "action": "CANCEL",
            "description": "放弃挂单，开盘即破止损说明方向判断错误",
            "position": 0,
        })

        return rules

    def _calc_order_params(self, stop_loss_price: float,
                           target_price: float) -> Dict:
        """生成条件单设置参数"""
        return {
            "stop_loss_trigger": stop_loss_price,
            "take_profit_trigger": target_price,
            "trailing_stop_desc": f"从持仓期间最高点回撤 {self.trailing_stop_pct}% 触发卖出",
            "validity": "当日有效（建议设为期 5 个交易日）",
        }

    # ------------------------------------------------------------------
    # 便捷查询
    # ------------------------------------------------------------------

    def summarize(self, orders: List[Dict]) -> str:
        """生成一段简短的操作提醒文字"""
        if not orders:
            return "今日无适合开盘前挂单的标的。"

        n = len(orders)
        # 取前3只作为重点关注
        top_codes = "、".join(f"{o['code']} {o['name']}" for o in orders[:3])
        # 根据评分等级分类
        strong = [o for o in orders if o["advice"] in ("强烈关注", "关注")]
        mild = [o for o in orders if o["advice"] == "轻度关注"]

        parts = []
        if strong:
            parts.append(f"重点关注 {len(strong)} 只")
        if mild:
            parts.append(f"轻度关注 {len(mild)} 只")
        detail = "、".join(parts)

        return (
            f"共 {n} 只值得挂单（{detail}），"
            f"TOP3: {top_codes}。"
            f"高开>{self.gap_up_cancel_pct}% 或低开破止损则放弃对应挂单。"
        )
