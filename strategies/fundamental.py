import pandas as pd
import re
from strategies.ai_factor_adjuster import AIFactorAdjuster

class FundamentalScreener:
    """基本面筛选器——完整版：行业 + 市值 + 估值 + ST排除"""

    # 行业关键词映射（用于模糊匹配）
    PREFERRED_KEYWORDS = [
        "银行", "证券", "保险", "信托", "期货",
        "白酒", "啤酒", "食品", "饮料", "乳业", "调味", "休闲食品",
        "家电", "空调", "厨电", "小家电",
        "医疗", "医药", "生物", "化学制药", "中药", "器械", "CXO", "CRO",
        "电力", "水电", "核电", "风电", "光伏", "电网", "燃气",
        "公路", "铁路", "高速", "港口", "航运",
        "机械", "设备", "半导体", "芯片", "电子",
        "石油", "石化", "煤炭", "有色", "黄金", "铜", "铝", "钢铁",
        "汽车", "零部件", "轮胎",
        "化工", "化学", "材料",
    ]

    EXCLUDED_KEYWORDS = [
        "教育", "培训", "辅导", "学校",
        "互联网", "互金", "网贷", "P2P",
        "游戏", "网游", "手游",
        "影视", "院线", "传媒", "广告",
        "ST", "*ST", "退市",
    ]

    def __init__(self, spot_df: pd.DataFrame, config: dict):
        self.df = spot_df.copy()
        self.config = config

        # 提取子配置
        self.stock_pool_cfg = config.get("stock_pool", {})
        self.market_cap_cfg = config.get("market_cap", {})
        self.valuation_cfg = config.get("valuation", {})
        self.investment_style = config.get("investment_style", "balanced")

        # 股权质押避雷配置
        self.pledge_cfg = config.get("pledge_avoidance", {})
        self.pledge_enabled = self.pledge_cfg.get("enabled", True)
        self.pledge_threshold = self.pledge_cfg.get("threshold_pct", 30.0)
        self.pledge_high_risk = self.pledge_cfg.get("high_risk_pct", 50.0)

        # 成长行业差异化估值阈值（2026-11-13：AI 主线被全局 PE≤50/PB≤5/股息≥0.5%
        # 三件套整体封死在信号池外，与 AI 简报"成长风格占优"冲突）
        # 只放宽估值硬阈值与市值上限；市值下限、亏损股排除、行业黑名单、
        # 质押避雷、一票否决均不变。未配置 growth_override 时行为与旧版完全一致。
        gv = self.valuation_cfg.get("growth_override", {}) or {}
        self.growth_industries = gv.get("industries", [])
        self.growth_max_pe = gv.get("max_pe") or 0
        self.growth_max_pb = gv.get("max_pb") or 0
        self.growth_min_dy = gv.get("min_dividend_yield")  # 0 表示不设下限
        self.growth_max_cap = gv.get("max_cap") or 0       # 亿；0 表示不调整上限

        # 股权质押数据（外部注入）
        self.pledge_df = None
        # 记录被排除的高质押股票
        self.avoided_pledge_stocks = []

        # 确定市值范围
        self._set_market_cap_range()

    def set_pledge_data(self, pledge_df: pd.DataFrame):
        """注入股权质押数据，用于过滤高质押股票"""
        if pledge_df is not None and not pledge_df.empty:
            self.pledge_df = pledge_df.copy()

    def _get_pledge_ratio(self, code: str) -> float:
        """获取单只股票的质押比例，无数据返回0"""
        if self.pledge_df is None or self.pledge_df.empty:
            return 0.0
        row = self.pledge_df[self.pledge_df.get("代码", "") == code]
        if row.empty:
            return 0.0
        return float(row.iloc[0].get("质押比例", 0) or 0)

    def _set_market_cap_range(self):
        """根据投资风格确定市值范围"""
        style_map = {
            "conservative": self.market_cap_cfg.get("conservative", {"min": 500, "max": 100000}),
            "balanced": self.market_cap_cfg.get("balanced", {"min": 200, "max": 1000}),
            "aggressive": self.market_cap_cfg.get("aggressive", {"min": 100, "max": 500}),
        }
        self.mc = style_map.get(self.investment_style, style_map["balanced"])
        self.absolute_min = self.market_cap_cfg.get("absolute_min", 50)

    def _is_st(self, name: str) -> bool:
        """判断是否为 ST/*ST/退市风险"""
        if pd.isna(name):
            return True
        name = str(name)
        return bool(re.search(r'[*ST退]|退市|ST', name))

    def _sector_match(self, sector: str, keywords: list) -> bool:
        """行业关键词模糊匹配"""
        if pd.isna(sector):
            return False
        sector = str(sector)
        return any(kw in sector for kw in keywords)

    def _is_growth_industry(self, sector) -> bool:
        """是否为配置了差异化估值阈值的成长行业（双向子串匹配，兼容
        tushare 的'元器件'与东财的'电子元件'等不同行业命名）"""
        if not self.growth_industries or pd.isna(sector):
            return False
        s = str(sector).strip()
        if not s:
            return False
        return any(kw in s or s in kw for kw in self.growth_industries)

    def _pe_limit(self, sector) -> float:
        """PE 上限：成长行业用放宽阈值，其余用全局阈值（0=不限制）"""
        if self.growth_max_pe and self._is_growth_industry(sector):
            return self.growth_max_pe
        return self.valuation_cfg.get("max_pe", 0)

    def _pb_limit(self, sector) -> float:
        """PB 上限：成长行业用放宽阈值，其余用全局阈值（0=不限制）"""
        if self.growth_max_pb and self._is_growth_industry(sector):
            return self.growth_max_pb
        return self.valuation_cfg.get("max_pb", 0)

    def _dy_limit(self, sector):
        """股息率下限：成长行业可设为 0（不设下限），None 表示沿用全局"""
        if self.growth_min_dy is not None and self._is_growth_industry(sector):
            return self.growth_min_dy
        return self.valuation_cfg.get("min_dividend_yield", 0)

    def screen(self, ai_signals: dict = None) -> pd.DataFrame:
        """基本面初筛：排除 + 硬条件过滤"""
        df = self.df.copy()
        if df.empty:
            return df

        initial_count = len(df)

        # 0. AI 行业信号调整（在原有配置基础上扩展白名单/黑名单）
        preferred = self.stock_pool_cfg.get("preferred_sectors", [])
        excluded = self.stock_pool_cfg.get("excluded_sectors", [])
        if ai_signals:
            adjuster = AIFactorAdjuster(self.config)
            preferred, excluded = adjuster.adjust_sector_filter(preferred, excluded, ai_signals)
            added_hot = [s for s in preferred if s not in self.stock_pool_cfg.get("preferred_sectors", [])]
            added_cold = [s for s in excluded if s not in self.stock_pool_cfg.get("excluded_sectors", [])]
            if added_hot:
                print(f"     [AI] 加入热门行业: {', '.join(added_hot)}")
            if added_cold:
                print(f"     [AI] 加入回避行业: {', '.join(added_cold)}")

        # 1. 排除 ST/*ST/退市
        if "名称" in df.columns:
            mask_st = ~df["名称"].apply(self._is_st)
            df = df[mask_st].copy()

        # 2. 行业过滤
        if "所属行业" in df.columns:
            # 先排除黑名单
            if excluded:
                mask_ex = ~df["所属行业"].apply(
                    lambda x: self._sector_match(x, excluded)
                )
                df = df[mask_ex].copy()

            # 白名单过滤（如有配置）
            if preferred:
                mask_pre = df["所属行业"].apply(
                    lambda x: self._sector_match(x, preferred)
                )
                df = df[mask_pre].copy()

            # 行业标签大面积缺失时给出醒目警告（数据源异常信号，
            # 下游行业分散约束会把所有股票当同一行业砍到个位数）
            if preferred or excluded:
                empty = df["所属行业"].apply(
                    lambda x: pd.isna(x) or not str(x).strip() or str(x).strip().lower() == "nan"
                ).sum()
                if len(df) > 0 and empty / len(df) > 0.5:
                    print(f"     [!] 严重警告: {empty}/{len(df)} 只入选股票缺少行业标签，"
                          f"行业白/黑名单过滤可能未生效，请检查数据源完整性")
        elif preferred or excluded:
            print(f"     [!] 严重警告: 股票数据缺少'所属行业'列，"
                  f"{len(preferred)}条白名单 + {len(excluded)}条黑名单行业过滤规则被静默跳过，"
                  f"推荐列表可能集中于个别行业（2026-11-04/06 事故模式）")

        # 3. 市值过滤（成长行业可放宽上限，如 balanced 2000亿 上限对
        #    中芯国际/海光等主线龙头过紧；下限与绝对下限不变）
        if "总市值" in df.columns:
            cap_min = self.mc["min"] * 1e8
            abs_min = self.absolute_min * 1e8
            cap_max = self.mc["max"] * 1e8
            if self.growth_max_cap and "所属行业" in df.columns:
                cap_max_s = pd.Series(cap_max, index=df.index)
                growth_mask = df["所属行业"].apply(self._is_growth_industry)
                cap_max_s[growth_mask] = self.growth_max_cap * 1e8
                df = df[(df["总市值"] >= cap_min)
                        & (df["总市值"] <= cap_max_s)
                        & (df["总市值"] >= abs_min)].copy()
            else:
                df = df[(df["总市值"] >= cap_min)
                        & (df["总市值"] <= cap_max)].copy()
                df = df[df["总市值"] >= abs_min].copy()

        # 4. PE 过滤（成长行业差异化阈值；亏损股排除规则不变）
        if "市盈率" in df.columns:
            df = df[df["市盈率"].notna()].copy()
            if self.valuation_cfg.get("exclude_negative_pe", True):
                df = df[df["市盈率"] > 0].copy()
            if self.growth_max_pe and "所属行业" in df.columns:
                keep = df.apply(
                    lambda r: (r["市盈率"] <= self._pe_limit(r["所属行业"]))
                    if self._pe_limit(r["所属行业"]) > 0 else True,
                    axis=1)
                df = df[keep].copy()
            else:
                max_pe = self.valuation_cfg.get("max_pe", 0)
                if max_pe > 0:
                    df = df[df["市盈率"] <= max_pe].copy()

        # 5. PB 过滤（成长行业差异化阈值）
        if "市净率" in df.columns:
            df = df[df["市净率"].notna()].copy()
            if self.growth_max_pb and "所属行业" in df.columns:
                keep = df.apply(
                    lambda r: (r["市净率"] <= self._pb_limit(r["所属行业"]))
                    if self._pb_limit(r["所属行业"]) > 0 else True,
                    axis=1)
                df = df[keep].copy()
            else:
                max_pb = self.valuation_cfg.get("max_pb", 0)
                if max_pb > 0:
                    df = df[df["市净率"] <= max_pb].copy()

        # 6. 股息率过滤（成长行业可取消下限，如股息率 0 仍保留）
        if "股息率" in df.columns:
            if self.growth_min_dy is not None and "所属行业" in df.columns:
                keep = df.apply(
                    lambda r: (r["股息率"] >= self._dy_limit(r["所属行业"]))
                    if self._dy_limit(r["所属行业"]) > 0 else True,
                    axis=1)
                df = df[keep].copy()
            else:
                min_dy = self.valuation_cfg.get("min_dividend_yield", 0)
                if min_dy > 0:
                    df = df[df["股息率"] >= min_dy].copy()

        # 6.5 成长行业差异化阈值生效提示（供 CI 日志审计）
        if (self.growth_max_pe or self.growth_max_pb
                or self.growth_min_dy is not None) and "所属行业" in df.columns:
            n_growth = int(df["所属行业"].apply(self._is_growth_industry).sum())
            print(f"     成长行业差异化估值: {n_growth}/{len(df)} 只按放宽阈值保留"
                  f"(PE<={self.growth_max_pe or '-'}, PB<={self.growth_max_pb or '-'}, "
                  f"股息>={self.growth_min_dy if self.growth_min_dy is not None else '-'})")

        # 7. 股权质押避雷过滤
        if self.pledge_enabled and self.pledge_df is not None and not self.pledge_df.empty:
            before_pledge = len(df)
            mask_pledge = []
            avoided = []
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                ratio = self._get_pledge_ratio(code)
                if ratio >= self.pledge_high_risk:
                    # 极高风险：直接排除
                    avoided.append({"code": code, "name": name, "ratio": ratio, "level": "高风险"})
                    mask_pledge.append(False)
                elif ratio >= self.pledge_threshold:
                    # 超过警戒线：排除并记录
                    avoided.append({"code": code, "name": name, "ratio": ratio, "level": "警戒线"})
                    mask_pledge.append(False)
                else:
                    mask_pledge.append(True)
            df = df[mask_pledge].copy() if mask_pledge else df.iloc[0:0].copy()
            self.avoided_pledge_stocks = avoided
            after_pledge = len(df)
            if avoided:
                print(f"     股权质押避雷: 排除 {len(avoided)} 只"
                      f"(≥{self.pledge_threshold}%质押), {before_pledge} -> {after_pledge} 只")
                # 输出前5个被排除的股票
                for a in avoided[:5]:
                    print(f"       [!] {a['code']} {a['name']} 质押{a['ratio']:.1f}% [{a['level']}]")
                if len(avoided) > 5:
                    print(f"       ... 等共 {len(avoided)} 只")

        # 8. 自定义股票白名单（强制纳入，跳过硬条件但保留ST/黑名单/质押检查）
        custom_codes = self.stock_pool_cfg.get("custom_stocks", [])
        if custom_codes:
            added_custom = []
            for code in custom_codes:
                code = str(code).strip()
                # 已在候选池中，跳过
                if not df.empty and code in df["代码"].astype(str).values:
                    continue
                # 从原始数据中查找
                orig = self.df[self.df["代码"].astype(str) == code]
                if orig.empty:
                    continue
                row = orig.iloc[0]
                name = str(row.get("名称", ""))
                # ST检查
                if self._is_st(name):
                    print(f"       [custom] {code} {name} 为ST股，不纳入")
                    continue
                # 黑名单行业检查
                sector = str(row.get("所属行业", ""))
                if excluded and self._sector_match(sector, excluded):
                    print(f"       [custom] {code} {name} 行业在黑名单，不纳入")
                    continue
                # 股权质押检查（高质押也纳入，但提示）
                if self.pledge_enabled and self.pledge_df is not None:
                    ratio = self._get_pledge_ratio(code)
                    if ratio >= self.pledge_threshold:
                        print(f"       [custom] {code} {name} 质押{ratio:.1f}%（高质押，仍纳入）")
                    else:
                        print(f"       [custom] {code} {name} 纳入")
                else:
                    print(f"       [custom] {code} {name} 纳入")
                added_custom.append(row)
            if added_custom:
                df = pd.concat([df, pd.DataFrame(added_custom)], ignore_index=True)
                df = df.drop_duplicates(subset=["代码"], keep="first")
                print(f"     自定义白名单: 纳入 {len(added_custom)} 只")

        final_count = len(df)
        print(f"     基本面初筛: {initial_count} -> {final_count} 只")
        return df.reset_index(drop=True)

    def score(self, stock_row: pd.Series, financial_data: dict = None,
              ai_signals: dict = None, ah_premium_map: dict = None) -> dict:
        """
        对单只股票基本面打分 -1 ~ 1
        financial_data: 预留，Phase 2 传入详细财务指标
        ai_signals: AI 简报提取的宏观信号，用于行业加分/扣分
        ah_premium_map: A/H 溢价映射 {code: premium_pct}
        """
        score = 0.0
        signals = []

        pe = stock_row.get("市盈率", None)
        pb = stock_row.get("市净率", None)
        dy = stock_row.get("股息率", None)
        cap = stock_row.get("总市值", 0) / 1e8  # 转为亿

        # ---------- 1. 市值评分 ----------
        if cap >= 500:
            score += 0.15
            signals.append("大市值蓝筹")
        elif cap >= 200:
            score += 0.10
            signals.append("中大盘")
        elif cap >= 100:
            score += 0.05
            signals.append("中型市值")
        else:
            score -= 0.1
            signals.append("小市值")

        # ---------- 2. PE 评分 ----------
        if pe is not None and pe > 0:
            if pe < 15:
                score += 0.25
                signals.append("PE极低估值")
            elif pe < 25:
                score += 0.15
                signals.append("PE低估值")
            elif pe < 35:
                score += 0.05
            elif pe > 60:
                score -= 0.2
                signals.append("PE偏高")
        elif pe is not None and pe <= 0:
            score -= 0.3
            signals.append("亏损股")

        # ---------- 3. PB 评分 ----------
        if pb is not None and pb > 0:
            if pb < 1.5:
                score += 0.2
                signals.append("PB低估值")
            elif pb < 2.5:
                score += 0.1
            # 扣分阈值跟随生效的筛选上限（含成长行业差异化阈值），
            # 与 screen() 的 PB 过滤单一来源，避免"过了筛选仍被扣分"
            pb_limit = self._pb_limit(stock_row.get("所属行业", ""))
            if pb_limit > 0 and pb > pb_limit:
                score -= 0.15
                signals.append("PB偏高")

        # ---------- 4. 股息率评分 ----------
        if dy is not None and dy > 0:
            high_dy = self.valuation_cfg.get("high_dividend_yield", 3.0)
            if dy >= high_dy:
                score += 0.2
                signals.append(f"高股息{dy:.1f}%")
            elif dy >= 2.0:
                score += 0.1
                signals.append(f"股息率{dy:.1f}%")

        # ---------- 5. 行业偏好加分 ----------
        sector = stock_row.get("所属行业", "")
        if self._sector_match(sector, self.PREFERRED_KEYWORDS):
            score += 0.1
            signals.append("行业偏好")

        # ---------- 6. PEG 评分（P2: 市盈率/盈利增速）----------
        profit_growth = None
        if financial_data:
            profit_growth = financial_data.get("profit_growth_yoy")
        # 尝试从 stock_row 获取盈利增速
        if profit_growth is None:
            profit_growth = stock_row.get("净利润增长率", None)
        if profit_growth is None:
            profit_growth = stock_row.get("profit_growth", None)

        if pe is not None and pe > 0 and profit_growth is not None and profit_growth > 0:
            peg = pe / profit_growth
            if peg < 0.5:
                score += 0.20
                signals.append(f"PEG极低{peg:.2f}")
            elif peg < 1.0:
                score += 0.10
                signals.append(f"PEG合理{peg:.2f}")
            elif peg > 2.0:
                score -= 0.10
                signals.append(f"PEG偏高{peg:.2f}")

        # ---------- 7. 估值趋势评分（P2: 当前PE vs 历史中位数）----------
        pe_median = None
        if financial_data:
            pe_median = financial_data.get("pe_median_1y")
        if pe_median is None:
            pe_median = stock_row.get("pe_percentile", None)

        if pe is not None and pe > 0 and pe_median is not None and pe_median > 0:
            pe_trend = (pe - pe_median) / pe_median
            if pe_trend < -0.20:
                score += 0.10
                signals.append("PE处于历史低位")
            elif pe_trend > 0.30:
                score -= 0.08
                signals.append("PE处于历史高位")

        # ---------- 8. A/H 股溢价评分（可选跨市场估值锚）----------
        ah_cfg = self.config.get("ah_premium", {})
        if ah_cfg.get("enabled", False) and ah_premium_map:
            code = str(stock_row.get("代码", ""))
            premium = ah_premium_map.get(code)
            if premium is not None:
                high_threshold = ah_cfg.get("high_premium_threshold", 80.0)
                low_threshold = ah_cfg.get("low_premium_threshold", 20.0)
                score_cap = ah_cfg.get("score_cap", 0.10)

                if premium <= low_threshold:
                    # 溢价率低或折价，A 股相对 H 股不贵，加分
                    bonus = min(score_cap, score_cap * (1 - premium / low_threshold))
                    score += bonus
                    signals.append(f"AH溢价低{premium:.1f}%")
                elif premium >= high_threshold:
                    # 溢价率过高，A 股相对 H 股偏贵，扣分
                    penalty = min(score_cap, score_cap * (premium / high_threshold - 1))
                    score -= penalty
                    signals.append(f"AH溢价高{premium:.1f}%")

        # ---------- 9. 财务数据评分（预留）----------
        if financial_data:
            roe = financial_data.get("roe", 0)
            gm = financial_data.get("gross_margin", 0)
            nm = financial_data.get("net_margin", 0)
            debt = financial_data.get("debt_ratio", 0)

            if roe >= 15:
                score += 0.2
                signals.append(f"ROE{roe:.1f}%")
            elif roe >= 12:
                score += 0.1
                signals.append(f"ROE{roe:.1f}%")
            elif roe < 5:
                score -= 0.1

            if gm >= 40:
                score += 0.1
            if nm >= 15:
                score += 0.1
            if debt > 70:
                score -= 0.1
                signals.append("负债率高")

            # Phase 3: 筹码集中度
            holder_trend = financial_data.get("holder_trend", "")
            holder_change = financial_data.get("holder_change_pct", 0)
            if holder_trend == "concentrate":
                score += 0.10
                signals.append(f"筹码集中{holder_change:+.1f}%")
            elif holder_trend == "disperse":
                score -= 0.05
                signals.append(f"筹码分散{holder_change:+.1f}%")

        # ---------- 10. AI 行业信号调整 ----------
        if ai_signals:
            adjuster = AIFactorAdjuster(self.config)
            original_score = score
            score = adjuster.adjust_fundamental_score(score, sector, ai_signals)
            delta = round(score - original_score, 3)
            if delta > 0.001:
                signals.append(f"AI行业利好(+{delta})")
            elif delta < -0.001:
                signals.append(f"AI行业利空({delta})")

        return {"score": round(max(-1, min(1, score)), 2), "signals": signals}
