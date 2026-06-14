from datetime import datetime
import os


class DailyReport:
    """每日投资建议报告生成器 v2.1"""

    def __init__(self, output_dir="reports/output"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def generate_markdown(self, results: list, market_env: dict = None,
                          date_str: str = None, avoid_list: list = None,
                          veto_list: list = None, div_notes: list = None,
                          pre_market_orders: list = None) -> str:
        """生成 Markdown 报告"""
        date_str = date_str or datetime.now().strftime("%Y-%m-%d")
        avoid_list = avoid_list or []
        veto_list = veto_list or []
        div_notes = div_notes or []
        pre_market_orders = pre_market_orders or []

        lines = [
            f"# A股每日投资建议 - {date_str}",
            "",
            f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "> 数据来源：Tushare Pro + akshare",
            "> 策略：多因子选股 v2.1（基本面 + 技术面 + 动量 + 资金面 + 一票否决 + 动态权重 + 风控）",
            ""
        ]

        # 大盘环境
        if market_env:
            valid_mark = "[数据正常]" if market_env.get("valid") else "[数据异常]"
            lines.extend([
                "## 大盘环境",
                "",
                f"- **上证指数**: {market_env.get('sh_index', 'N/A')} (年线: {market_env.get('sh_ma250', 'N/A')}) {valid_mark}",
                f"- **年线状态**: {'上方' if market_env.get('above_ma250') else '下方' if market_env.get('above_ma250') is False else '未知'}",
            ])
            capital = market_env.get("capital", {})
            if capital:
                north = capital.get("north_money")
                margin = capital.get("margin_rzye")
                if north is not None:
                    lines.append(f"- **北向资金**: {'净流入' if north >= 0 else '净流出'} {abs(north):.1f} 亿")
                if margin is not None:
                    lines.append(f"- **融资余额**: {margin:.0f} 亿")
            lines.append("- **判断**: " + " | ".join(market_env.get("notes", [])))
            lines.append("")

        # 今日精选
        lines.extend([
            "## 今日精选",
            "",
            "| 排名 | 代码 | 名称 | 行业 | 综合评分 | 建议 | 最新价 | 技术面 | 基本面 | 动量 | 资金面 | 关键信号 |",
            "|------|------|------|------|----------|------|--------|--------|--------|------|--------|----------|"
        ])

        for i, r in enumerate(results[:20], 1):
            d = r["details"]
            signals = " / ".join(
                d["technical"]["signals"][:2] +
                d["fundamental"]["signals"][:1] +
                d["momentum"]["signals"][:1]
            )
            if r.get("conflict_triggered"):
                signals += " / [看跌信号冲突]"
            price = r.get("latest_price", 0)
            cf_score = d.get("capital_flow", {}).get("score", 0)
            lines.append(
                f"| {i} | {r['code']} | {r['name']} | {r.get('sector','')} | "
                f"{r['total_score']} | {r['advice']} | {price} | "
                f"{d['technical']['score']} | {d['fundamental']['score']} | "
                f"{d['momentum']['score']} | {cf_score} | {signals} |"
            )

        # 行业分散说明
        if div_notes:
            lines.extend(["", "### 行业分散", ""])
            for note in div_notes:
                lines.append(f"- {note}")
            lines.append("")

        lines.extend(["", "## 详细分析与风控", ""])

        for i, r in enumerate(results[:15], 1):
            d = r["details"]
            risk = r.get("risk", {})
            lines.extend([
                f"### {i}. {r['code']} {r['name']} ({r['advice']})",
                "",
                f"- **行业**: {r.get('sector', 'N/A')}",
                f"- **最新价**: {r.get('latest_price', 'N/A')}",
                f"- **综合评分**: {r['total_score']}",
                f"- **技术面评分**: {d['technical']['score']} — {', '.join(d['technical']['signals'])}",
                f"- **基本面评分**: {d['fundamental']['score']} — {', '.join(d['fundamental']['signals'])}",
                f"- **动量评分**: {d['momentum']['score']} — {', '.join(d['momentum']['signals'])}",
                f"- **资金面评分**: {d.get('capital_flow', {}).get('score', 0)} — {', '.join(d.get('capital_flow', {}).get('signals', []))}",
                f"- **均线**: MA20={r.get('ma20','N/A')} MA60={r.get('ma60','N/A')} 年线={r.get('ma250','N/A')}",
                ""
            ])
            if r.get("conflict_triggered"):
                lines.append(f"- **信号冲突**: 出现看跌信号 `{', '.join(r.get('bearish_signals', []))}`，建议等级已被强制下调")
                lines.append("")
            if risk:
                lines.extend([
                    "**风控建议**:",
                    f"- 止损位: {risk.get('stop_loss_price', 'N/A')} (支撑: {risk.get('support', 'N/A')})",
                    f"- 目标价: {risk.get('target_price', 'N/A')} (压力: {risk.get('resistance', 'N/A')})",
                    f"- 移动止盈: 从高点回撤触发",
                    f"- 建议仓位: {risk.get('position_pct', 'N/A')}%",
                    ""
                ])

        # ====== 开盘前挂单指南 ======
        if pre_market_orders:
            lines.extend(self._render_pre_market_section(pre_market_orders))

        # 一票否决清单
        if veto_list:
            lines.extend([
                "",
                "## ! 一票否决清单",
                "",
                "> 以下股票因触发硬排除规则被自动过滤，不进入推荐：",
                "",
                "| 代码 | 名称 | 行业 | 排除原因 |",
                "|------|------|------|----------|"
            ])
            for v in veto_list[:30]:
                lines.append(
                    f"| {v.get('code', '')} | {v.get('name', '')} | "
                    f"{v.get('sector', '')} | {v.get('reason', '')} |"
                )
            if len(veto_list) > 30:
                lines.append(f"| ... | 等共 {len(veto_list)} 只 | - | - |")
            lines.append("")

        # 避雷清单
        if avoid_list:
            lines.extend([
                "",
                "## ! 股权质押避雷清单",
                "",
                "> 以下股票因大股东质押比例过高（≥30%）被自动排除，建议回避：",
                "",
                "| 代码 | 名称 | 质押比例 | 风险等级 |",
                "|------|------|----------|----------|"
            ])
            for a in avoid_list[:30]:
                lines.append(
                    f"| {a.get('code', '')} | {a.get('name', '')} | "
                    f"{a.get('ratio', 0):.1f}% | {a.get('level', '')} |"
                )
            if len(avoid_list) > 30:
                lines.append(f"| ... | 等共 {len(avoid_list)} 只 | - | - |")
            lines.append("")

        lines.extend([
            "---",
            "",
            "**免责声明**：以上分析仅基于历史数据和算法模型，不构成投资建议。投资有风险，入市需谨慎。"
        ])

        content = "\n".join(lines)
        filepath = os.path.join(self.output_dir, f"report_{date_str}.md")
        with open(filepath, "w", encoding="utf-8-sig") as f:
            f.write(content)

        return filepath, content

    # ------------------------------------------------------------------
    # 开盘前挂单指南（Markdown 章节）
    # ------------------------------------------------------------------

    def _render_pre_market_section(self, orders: list) -> list:
        """渲染开盘前挂单指南的 Markdown 内容"""
        lines = [
            "",
            "## 开盘前挂单指南",
            "",
            "> 以下挂单建议基于前日收盘数据生成。请在次日 9:15 集合竞价前在券商 APP 中设置限价单和条件单。",
            "> 若开盘价高开超过阈值或低开跌破止损价，请手动放弃对应挂单。",
            "",
            "### 挂单汇总",
            "",
            "| 代码 | 名称 | 建议 | 前日收盘 | 挂单价 | 仓位 | 止损价 | 目标价 | 盈亏比 |",
            "|------|------|------|----------|--------|------|--------|--------|--------|",
        ]

        for o in orders:
            rr = o.get("risk_reward", "")
            rr_str = f"1:{rr:.1f}" if isinstance(rr, (int, float)) and rr > 0 else "-"
            lines.append(
                f"| {o['code']} | {o['name']} | {o['advice']} | "
                f"{o['latest_price']} | **{o['suggested_price']}** | "
                f"{o['position_pct']}% | "
                f"{o['stop_loss_price']} | {o['target_price']} | {rr_str} |"
            )

        lines.extend(["", "### 高开/低开应对规则", ""])

        # 只在第一只股票展示完整规则矩阵（所有股票共享同一套阈值）
        if orders:
            o = orders[0]
            for gr in o.get("gap_rules", []):
                icon = {"CANCEL": "[X]", "REDUCE": "[-]", "NORMAL": "[O]"}.get(gr["action"], "[?]")
                lines.append(f"- **{icon} {gr['scenario']}**：{gr['description']}")

        lines.extend(["", "### 条件单设置参数", ""])
        lines.extend([
            "条件单可在券商 APP（如华泰涨乐财富通、同花顺等）中设置，支持自动触发：",
            "",
            "| 代码 | 名称 | 止损触发价 | 止盈触发价 | 移动止盈 |",
            "|------|------|-----------|-----------|---------|",
        ])

        for o in orders:
            op = o.get("order_params", {})
            lines.append(
                f"| {o['code']} | {o['name']} | "
                f"{op.get('stop_loss_trigger', '-')} | "
                f"{op.get('take_profit_trigger', '-')} | "
                f"高点回撤{self._get_trailing_pct()}% |"
            )

        lines.extend([
            "",
            f"> **有效期建议**：限价单当日有效；条件止损/止盈单建议设为 5 个交易日内有效，到期未触发需刷新。",
        ])

        return lines

    def _get_trailing_pct(self) -> float:
        """从风险配置获取移动止盈回撤比例"""
        # 这个值由 RiskManager 配置控制，这里做一个合理的默认值
        return 5.0

    def _print_pre_market_console(self, console, orders: list):
        """终端输出开盘前挂单指南"""
        from rich.table import Table
        from rich import box

        console.print(f"[bold cyan]{'='*60}[/bold cyan]")
        console.print(f"[bold cyan]开盘前挂单指南[/bold cyan]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]")
        console.print("[dim]以下挂单价为限价单建议，请在券商 APP 中设置条件止损/止盈单[/dim]\n")

        # 挂单汇总表
        pm_table = Table(box=box.SIMPLE_HEAD)
        pm_table.add_column("代码", justify="center", style="bold")
        pm_table.add_column("名称", max_width=8)
        pm_table.add_column("建议")
        pm_table.add_column("挂单价", justify="right", style="green")
        pm_table.add_column("仓位", justify="right")
        pm_table.add_column("止损", justify="right", style="red")
        pm_table.add_column("目标", justify="right", style="yellow")

        for o in orders:
            pm_table.add_row(
                o["code"], o["name"], o["advice"],
                f"{o['suggested_price']:.2f}",
                f"{o['position_pct']}%",
                f"{o['stop_loss_price']:.2f}",
                f"{o['target_price']:.2f}",
            )

        console.print(pm_table)
        console.print("")

        # 高开/低开规则（只印一次，所有股票共享阈值）
        console.print("[bold]高开 / 低开应对规则：[/bold]")
        if orders:
            for gr in orders[0].get("gap_rules", []):
                if gr["action"] == "CANCEL":
                    color = "red"
                elif gr["action"] == "REDUCE":
                    color = "yellow"
                else:
                    color = "green"
                console.print(f"  [{color}]{gr['action']:6s}[/{color}] {gr['scenario']:30s} — {gr['description']}")

        console.print("\n[bold]条件单设置：[/bold]")
        cond_table = Table(box=box.SIMPLE)
        cond_table.add_column("代码", justify="center", style="bold")
        cond_table.add_column("名称")
        cond_table.add_column("止损触发", justify="right", style="red")
        cond_table.add_column("止盈触发", justify="right", style="yellow")
        cond_table.add_column("移动止盈")

        for o in orders:
            op = o.get("order_params", {})
            cond_table.add_row(
                o["code"], o["name"],
                f"{op.get('stop_loss_trigger', '-'):.2f}" if isinstance(op.get('stop_loss_trigger'), (int, float)) else str(op.get('stop_loss_trigger', '-')),
                f"{op.get('take_profit_trigger', '-'):.2f}" if isinstance(op.get('take_profit_trigger'), (int, float)) else str(op.get('take_profit_trigger', '-')),
                f"回撤{self._get_trailing_pct()}%",
            )

        console.print(cond_table)
        console.print(f"\n[dim]限价单当日有效，条件止损/止盈单建议设5个交易日[/dim]\n")

    def print_console(self, results: list, market_env: dict = None,
                      avoid_list: list = None, veto_list: list = None,
                      pre_market_orders: list = None):
        """终端彩色输出"""
        from rich.console import Console
        from rich.table import Table
        from rich import box

        console = Console()
        avoid_list = avoid_list or []
        veto_list = veto_list or []
        pre_market_orders = pre_market_orders or []

        console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
        console.print(f"[bold cyan]A股每日投资建议 - {datetime.now().strftime('%Y-%m-%d')}[/bold cyan]")
        console.print(f"[bold cyan]{'='*60}[/bold cyan]\n")

        # 大盘环境
        if market_env:
            sh = market_env.get('sh_index', 'N/A')
            ma250 = market_env.get('sh_ma250', 'N/A')
            valid = market_env.get('valid', False)
            valid_mark = "[数据正常]" if valid else "[数据异常]"
            color = "green" if market_env.get('above_ma250') else "red"
            capital_notes = ""
            capital = market_env.get("capital", {})
            if capital.get("north_money") is not None:
                north = capital["north_money"]
                capital_notes += f" | 北向{'流入' if north >= 0 else '流出'} {abs(north):.1f}亿"
            console.print(
                f"[bold]大盘环境[/bold]: 上证 {sh} | 年线 {ma250} | "
                f"[{color}]{'上方' if market_env.get('above_ma250') else '下方'}[/{color}] "
                f"{valid_mark}{capital_notes}\n"
            )

        # 结果表格
        table = Table(box=box.SIMPLE_HEAD)
        table.add_column("排名", justify="center", style="bold")
        table.add_column("代码")
        table.add_column("名称", max_width=8)
        table.add_column("行业", max_width=8)
        table.add_column("评分", justify="right")
        table.add_column("建议", justify="center")
        table.add_column("最新价", justify="right")
        table.add_column("资金面")
        table.add_column("关键信号")

        advice_colors = {
            "强烈关注": "bold red",
            "关注": "green",
            "轻度关注": "yellow",
            "观望": "dim",
            "谨慎": "red",
            "回避": "strike red"
        }

        for i, r in enumerate(results[:20], 1):
            d = r["details"]
            signals = " / ".join(
                d["technical"]["signals"][:2] +
                d["fundamental"]["signals"][:1]
            )
            if r.get("conflict_triggered"):
                signals += " / [冲突]"
            cf_signals = " / ".join(d.get("capital_flow", {}).get("signals", [])[:2])
            color = advice_colors.get(r["advice"], "white")
            price = r.get("latest_price", 0)
            table.add_row(
                str(i), r["code"], r["name"], r.get("sector", ""),
                str(r["total_score"]),
                f"[{color}]{r['advice']}[/{color}]",
                f"{price:.2f}",
                cf_signals,
                signals
            )

        console.print(table)
        console.print(f"\n[dim]共筛选出 {len(results)} 只股票，显示前 20 名[/dim]\n")

        # ==== 开盘前挂单控制台输出 ====
        if pre_market_orders:
            self._print_pre_market_console(console, pre_market_orders)

        # 一票否决清单
        if veto_list:
            console.print(f"[bold red]! 一票否决清单 ({len(veto_list)} 只)[/bold red]")
            console.print("[dim]以下股票因触发硬排除规则未进入推荐[/dim]\n")
            veto_table = Table(box=box.SIMPLE)
            veto_table.add_column("代码", justify="center")
            veto_table.add_column("名称")
            veto_table.add_column("行业")
            veto_table.add_column("排除原因")
            for v in veto_list[:10]:
                veto_table.add_row(
                    v.get("code", ""),
                    v.get("name", ""),
                    v.get("sector", ""),
                    v.get("reason", "")
                )
            if len(veto_list) > 10:
                veto_table.add_row("...", f"等共 {len(veto_list)} 只", "", "")
            console.print(veto_table)
            console.print("")

        # 避雷清单
        if avoid_list:
            console.print(f"[bold red]! 股权质押避雷清单 ({len(avoid_list)} 只)[/bold red]")
            console.print("[dim]以下股票因大股东质押比例过高被自动排除[/dim]\n")
            avoid_table = Table(box=box.SIMPLE)
            avoid_table.add_column("代码", justify="center")
            avoid_table.add_column("名称")
            avoid_table.add_column("质押比例", justify="right")
            avoid_table.add_column("风险等级", justify="center")
            for a in avoid_list[:20]:
                level_color = "red" if a.get("level") == "高风险" else "yellow"
                avoid_table.add_row(
                    a.get("code", ""),
                    a.get("name", ""),
                    f"{a.get('ratio', 0):.1f}%",
                    f"[{level_color}]{a.get('level', '')}[/{level_color}]"
                )
            if len(avoid_list) > 20:
                avoid_table.add_row("...", f"等共 {len(avoid_list)} 只", "", "")
            console.print(avoid_table)
            console.print("")
