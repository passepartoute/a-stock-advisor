# -*- coding: utf-8 -*-
"""一次性回放工具：从历史 report_*.md 的"开盘前挂单指南"表格反解析 orders。

表头: | 代码 | 名称 | 建议 | 前日收盘 | 挂单价 | 仓位 | 止损价 | 目标价 | 盈亏比 |
挂单价带 ** 加粗标记；仓位带 %。生成 orders_<报告日期>.json
（报告盘前生成，挂单就在报告日执行，与 main.py 的 orders 日期语义一致）。

用法: python3 replay_orders_from_reports.py <pages_dir> <out_dir> [--since YYYY-MM-DD]
"""
import glob
import json
import os
import re
import sys

REPORT_RE = re.compile(r"^report_(\d{4}-\d{2}-\d{2})\.md$")


def parse_orders(text: str):
    sec = re.search(r"### 挂单汇总(.*?)###", text, re.S)
    if not sec:
        return []
    orders = []
    for line in sec.group(1).splitlines():
        cells = [c.strip().strip("*").strip() for c in
                 line.strip().strip("|").split("|")]
        if len(cells) < 8 or not re.match(r"^\d{6}$", cells[0]):
            continue
        try:
            orders.append({
                "code": cells[0],
                "name": cells[1],
                "advice": cells[2],
                "latest_price": float(cells[3]),
                "suggested_price": float(cells[4]),
                "position_pct": float(cells[5].rstrip("%")),
                "stop_loss_price": float(cells[6]),
                "target_price": float(cells[7]),
                "gap_rules": [],
                "order_params": {},
            })
        except ValueError:
            continue
    return orders


def main():
    pages_dir, out_dir = sys.argv[1], sys.argv[2]
    since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "0000-00-00"
    os.makedirs(out_dir, exist_ok=True)
    n_files = n_orders = 0
    for path in sorted(glob.glob(os.path.join(pages_dir, "report_*.md"))):
        m = REPORT_RE.match(os.path.basename(path))
        if not m or m.group(1) < since:
            continue
        date = m.group(1)
        text = open(path, encoding="utf-8-sig").read()
        orders = parse_orders(text)
        if not orders:
            continue
        payload = {"date": date, "generated_at": f"{date} 09:00:00",
                   "count": len(orders), "orders": orders,
                   "source": "parsed_from_report_md"}
        out = os.path.join(out_dir, f"orders_{date}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        n_files += 1
        n_orders += len(orders)
        print(f"  {date}: {len(orders)} 条挂单")
    print(f"[OK] 共 {n_files} 天 / {n_orders} 条挂单 -> {out_dir}")


if __name__ == "__main__":
    main()
