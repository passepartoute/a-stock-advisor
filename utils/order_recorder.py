# -*- coding: utf-8 -*-
"""每日挂单持久化。

把 PreMarketPlanner 生成的挂单 dict 列表原样存为
reports/output/orders_YYYY-MM-DD.json，由 workflow 归档到 gh-pages，
供 paper_settle.py 虚拟盘结算使用（次日按挂单模拟成交）。

只记录数据，不做任何决策；任何异常都不应中断主流程。
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional


def dump_orders(orders: List[Dict], output_dir: str,
                date_str: Optional[str] = None) -> str:
    """把当天的挂单列表写入 orders_<date>.json，返回文件路径。

    orders: PreMarketPlanner.plan_order() 返回的 dict 列表
    （含 suggested_price/position_pct/stop_loss_price/target_price/
    gap_rules/order_params 等），原样序列化，不裁剪字段。
    """
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    payload = {
        "date": date_str,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(orders),
        "orders": list(orders),
    }
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"orders_{date_str}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path
