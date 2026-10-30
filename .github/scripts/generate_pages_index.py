#!/usr/bin/env python3
"""
为 GitHub Pages 生成报告索引页。

按类别分开展示，避免全部混在一个大列表：
  - index.html / page_N.html  每日报告（含回测、避雷清单）
  - paper.html                虚拟盘每日结算
  - calibration.html          因子权重 IC 校准报告
每个页面顶部有类别导航。索引页按日期排序分页（第 1 页 index.html，
后续 page_2.html ...）；类别页为单页（数据量小）。

支持的文件名格式：
  - report_YYYY-MM-DD.md
  - backtest_YYYYMMDD.md / avoid_list_YYYYMMDD.md
  - calibration_YYYY-MM-DD.md
  - portfolio_YYYY-MM-DD.md
"""
import argparse
import os
import re
from datetime import datetime, timedelta, timezone


REPORT_RE = re.compile(r"^report_(\d{4}-\d{2}-\d{2})\.md$")
OTHER_RE = re.compile(r"^(backtest|avoid_list)_(\d{4})(\d{2})(\d{2})\.md$")
CALIB_RE = re.compile(r"^calibration_(\d{4}-\d{2}-\d{2})\.md$")
PORTFOLIO_RE = re.compile(r"^portfolio_(\d{4}-\d{2}-\d{2})\.md$")

LABELS = {
    "report": "每日报告",
    "backtest": "回测报告",
    "avoid_list": "避雷清单",
    "calibration": "校准报告",
    "portfolio": "虚拟盘",
}

# 类别 -> (页面文件, 页面标题)
CATEGORY_PAGES = {
    "main": ("index.html", "A-Stock 每日选股报告"),
    "paper": ("paper.html", "虚拟盘每日结算"),
    "calibration": ("calibration.html", "因子权重校准报告"),
}
LABEL_TO_CATEGORY = {"虚拟盘": "paper", "校准报告": "calibration"}


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
    m = PORTFOLIO_RE.match(name)
    if m:
        return m.group(1), LABELS["portfolio"]
    return None, None


def page_filename(page: int) -> str:
    """第 1 页固定为 index.html，保证入口地址不变。"""
    return "index.html" if page == 1 else f"page_{page}.html"


def _top_nav(current: str) -> str:
    """类别导航条，当前类别加粗。"""
    links = []
    for key in ("main", "paper", "calibration"):
        href, title = CATEGORY_PAGES[key]
        name = {"main": "每日报告", "paper": "虚拟盘",
                "calibration": "校准报告"}[key]
        if key == current:
            links.append(f"<b>{name}</b>")
        else:
            links.append(f'<a href="{href}">{name}</a>')
    return '  <p class="topnav">' + " &middot; ".join(links) + "</p>"


def render_page(entries, page, total_pages, generated_at, days,
                total_entries, category="main", title=None,
                out_file=None):
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

    title = title or CATEGORY_PAGES[category][1]
    topnav = _top_nav(category)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC", sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; background: #f6f8fa; color: #1f2328; }}
    h1 {{ font-size: 1.6em; margin-bottom: 12px; }}
    table {{ border-collapse: collapse; width: 100%; background: #fff; }}
    th, td {{ border: 1px solid #d1d9e0; padding: 10px 12px; text-align: left; }}
    th {{ background: #f6f8fa; font-weight: 600; }}
    a {{ color: #0969da; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .topnav {{ margin-bottom: 20px; font-size: 1.0em; }}
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
  <h1>{title}</h1>
{topnav}
  <table>
    <tr><th>日期</th><th>类型</th><th>链接</th></tr>
{rows}
  </table>
{nav}
  <p class="footer">共 {total_entries} 份报告 · 自动生成于 {generated_at} · 保留最近 {days} 天报告</p>
</body>
</html>"""


def collect_entries(site_dir: str, cutoff: str):
    """扫描 .md 文件，返回 {(date, label, name)} 按类别分组的 dict。"""
    groups = {"main": [], "paper": [], "calibration": []}
    for name in os.listdir(site_dir):
        if not name.endswith(".md"):
            continue
        d, label = parse_filename(name)
        if d is None or d < cutoff:
            continue
        cat = LABEL_TO_CATEGORY.get(label, "main")
        groups[cat].append((d, label, name))
    # 按日期降序，同一天按类型固定顺序
    type_order = {"每日报告": 0, "回测报告": 1, "避雷清单": 2,
                  "校准报告": 3, "虚拟盘": 4}
    for cat in groups:
        groups[cat].sort(key=lambda x: (x[0], type_order.get(x[1], 99)),
                         reverse=True)
    return groups


def main():
    parser = argparse.ArgumentParser(description="Generate GitHub Pages index for reports")
    parser.add_argument("site_dir", help="Directory containing .md reports and viewer.html")
    parser.add_argument("--days", type=int, default=7, help="Retention window in days")
    parser.add_argument("--page-size", type=int, default=100,
                        help="Entries per index page (default 100)")
    args = parser.parse_args()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    groups = collect_entries(args.site_dir, cutoff)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 清掉上次生成的旧页面，避免页数变少或类别清空时残留失效页面
    stale = [r"^page_\d+\.html$", r"^paper\.html$", r"^calibration\.html$"]
    for name in os.listdir(args.site_dir):
        if any(re.match(p, name) for p in stale):
            os.remove(os.path.join(args.site_dir, name))

    # 主页（报告类）分页
    entries = groups["main"]
    chunks = [entries[i:i + args.page_size]
              for i in range(0, len(entries), args.page_size)] or [[]]
    for i, chunk in enumerate(chunks, start=1):
        html = render_page(chunk, i, len(chunks), generated_at,
                           args.days, len(entries), category="main")
        with open(os.path.join(args.site_dir, page_filename(i)),
                  "w", encoding="utf-8") as f:
            f.write(html)

    # 类别页（单页，无分页）
    made = []
    for cat in ("paper", "calibration"):
        entries = groups[cat]
        if not entries:
            continue
        out_file, title = CATEGORY_PAGES[cat]
        html = render_page(entries, 1, 1, generated_at, args.days,
                           len(entries), category=cat, title=title)
        with open(os.path.join(args.site_dir, out_file),
                  "w", encoding="utf-8") as f:
            f.write(html)
        made.append(f"{out_file}({len(entries)})")

    print(f"Generated {len(chunks)} page(s) + {len(made)} category page(s) "
          f"in {args.site_dir} with "
          f"{sum(len(g) for g in groups.values())} reports "
          f"{' '.join(made)}")


if __name__ == "__main__":
    main()
