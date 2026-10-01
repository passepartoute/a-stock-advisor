#!/usr/bin/env python3
"""
为 GitHub Pages 生成报告索引页。

扫描站点目录中的 .md 报告文件，按日期排序，分页输出静态页面：
第 1 页为 index.html，后续为 page_2.html、page_3.html ...
支持两种文件名格式：
  - report_YYYY-MM-DD.md
  - backtest_YYYYMMDD.md / avoid_list_YYYYMMDD.md
  - calibration_YYYY-MM-DD.md
"""
import argparse
import os
import re
from datetime import datetime, timedelta, timezone


REPORT_RE = re.compile(r"^report_(\d{4}-\d{2}-\d{2})\.md$")
OTHER_RE = re.compile(r"^(backtest|avoid_list)_(\d{4})(\d{2})(\d{2})\.md$")
CALIB_RE = re.compile(r"^calibration_(\d{4}-\d{2}-\d{2})\.md$")

LABELS = {
    "report": "每日报告",
    "backtest": "回测报告",
    "avoid_list": "避雷清单",
    "calibration": "校准报告",
}


def parse_filename(name: str):
    """从文件名提取日期和类型标签。"""
    m = REPORT_RE.match(name)
    if m:
        return m.group(1), LABELS["report"]
    m = OTHER_RE.match(name)
    if m:
        d = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
        return d, LABELS.get(m.group(1), "报告")
    m = CALIB_RE.match(name)
    if m:
        return m.group(1), LABELS["calibration"]
    return None, None


def page_filename(page: int) -> str:
    """第 1 页固定为 index.html，保证入口地址不变。"""
    return "index.html" if page == 1 else f"page_{page}.html"


def render_page(entries, page, total_pages, generated_at, days, total_entries):
    """渲染一页索引 HTML。entries 为本页 (date, label, name) 列表。"""
    rows = "\n".join(
        f'    <tr><td>{d}</td><td>{label}</td>'
        f'<td><a href="viewer.html?report={name}">{name}</a></td></tr>'
        for d, label, name in entries
    )

    nav = ""
    if total_pages > 1:
        parts = []
        if page > 1:
            parts.append(f'<a href="{page_filename(page - 1)}">上一页</a>')
        parts.append(f"第 {page} / {total_pages} 页")
        if page < total_pages:
            parts.append(f'<a href="{page_filename(page + 1)}">下一页</a>')
        nav = '  <p class="nav">' + " &middot; ".join(parts) + "</p>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>A-Stock 报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #f6f8fa; color: #1f2328; }}
    h1 {{ font-size: 1.6em; margin-bottom: 20px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; }}
    th, td {{ border: 1px solid #d1d9e0; padding: 10px 12px; text-align: left; }}
    th {{ background: #f6f8fa; font-weight: 600; }}
    a {{ color: #0969da; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .nav {{ margin-top: 16px; font-size: 0.95em; }}
    .footer {{ color: #656d76; font-size: 0.9em; margin-top: 24px; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #0d1117; color: #c9d1d9; }}
      table {{ background: #161b22; }}
      th {{ background: #21262d; }}
      th, td {{ border-color: #30363d; }}
      a {{ color: #58a6ff; }}
    }}
  </style>
</head>
<body>
  <h1>A-Stock 每日选股报告</h1>
  <table>
    <tr><th>日期</th><th>类型</th><th>链接</th></tr>
{rows}
  </table>
{nav}
  <p class="footer">共 {total_entries} 份报告 · 自动生成于 {generated_at} · 保留最近 {days} 天报告</p>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser(description="Generate GitHub Pages index for reports")
    parser.add_argument("site_dir", help="Directory containing .md reports and viewer.html")
    parser.add_argument("--days", type=int, default=7, help="Retention window in days")
    parser.add_argument("--page-size", type=int, default=100,
                        help="Entries per index page (default 100)")
    args = parser.parse_args()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    entries = []
    for name in os.listdir(args.site_dir):
        if not name.endswith(".md"):
            continue
        d, label = parse_filename(name)
        if d is None or d < cutoff:
            continue
        entries.append((d, label, name))

    # 按日期降序，同一天按类型固定顺序
    type_order = {"每日报告": 0, "回测报告": 1, "避雷清单": 2, "校准报告": 3}
    entries.sort(key=lambda x: (x[0], type_order.get(x[1], 99)), reverse=True)

    # 清掉上次生成的旧分页，避免页数变少时残留失效页面
    for name in os.listdir(args.site_dir):
        if re.match(r"^page_\d+\.html$", name):
            os.remove(os.path.join(args.site_dir, name))

    chunks = [entries[i:i + args.page_size]
              for i in range(0, len(entries), args.page_size)] or [[]]
    total_pages = len(chunks)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    for i, chunk in enumerate(chunks, start=1):
        html = render_page(chunk, i, total_pages, generated_at,
                           args.days, len(entries))
        path = os.path.join(args.site_dir, page_filename(i))
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    print(f"Generated {total_pages} page(s) in {args.site_dir} "
          f"with {len(entries)} reports")


if __name__ == "__main__":
    main()
