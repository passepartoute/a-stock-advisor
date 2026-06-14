import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import os
import warnings
warnings.filterwarnings("ignore")

class DataFetcher:
    """
    多数据源封装：akshare(主) -> tushare(备) -> mock(兜底)
    自动降级，任一数据源可用即可运行
    """

    def __init__(self, cache_dir="data", data_source="auto", tushare_token=None):
        self.cache_dir = cache_dir
        self.data_source = data_source  # "auto" | "akshare" | "tushare" | "mock"
        self._tushare_token = tushare_token or self._load_tushare_token()
        self._tushare_pro = None
        self._northbound_cache = None
        self._margin_cache = None
        self._financial_cache = {}
        self._last_trade_date = None
        # 资金面数据缓存
        self._moneyflow_cache = None
        self._top_list_cache = None
        self._top_inst_cache = None
        self._market_capital_cache = None
        # 股权质押数据缓存
        self._pledge_cache = None
        self._suspend_cache = None
        self._limit_list_cache = None
        self._holder_cache = {}

    def _load_tushare_token(self):
        """从本地文件加载 tushare token"""
        token_path = os.path.join(os.path.dirname(__file__), "..", "config", ".tushare_token")
        token_path = os.path.abspath(token_path)
        if os.path.exists(token_path):
            with open(token_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        return None

    def _get_tushare_pro(self):
        """延迟初始化 tushare pro"""
        if self._tushare_pro is None and self._tushare_token:
            try:
                import tushare as ts
                self._tushare_pro = ts.pro_api(self._tushare_token)
            except Exception as e:
                print(f"[WARN] tushare 初始化失败: {e}")
        return self._tushare_pro

    def _get_last_trade_date(self):
        """获取最近交易日（通过 tushare daily 接口验证）"""
        if self._last_trade_date:
            return self._last_trade_date
        pro = self._get_tushare_pro()
        if pro:
            try:
                # 从最近 10 天倒推，找到有数据的交易日
                for i in range(10):
                    d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
                    df = pro.daily(trade_date=d, limit=1)
                    if df is not None and not df.empty:
                        self._last_trade_date = d
                        return d
            except Exception:
                pass
        # fallback: 昨天
        self._last_trade_date = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        return self._last_trade_date

    # ==================== 股票列表 ====================

    def get_stock_list(self, use_mock: bool = False):
        """获取 A 股所有股票列表，含完整字段"""
        if use_mock or self.data_source == "mock":
            return self._get_mock_stock_list()

        if self.data_source in ("auto", "akshare"):
            df = self._get_stock_list_akshare()
            if not df.empty:
                print("     [数据源] akshare")
                return df
            print("     [WARN] akshare 获取失败，尝试 tushare...")

        if self.data_source in ("auto", "tushare"):
            df = self._get_stock_list_tushare()
            if not df.empty:
                print("     [数据源] tushare")
                return df
            print("     [WARN] tushare 获取失败")

        print("     [WARN] 所有数据源均失败，使用模拟数据")
        return self._get_mock_stock_list()

    def _get_stock_list_akshare(self):
        """akshare 获取股票列表"""
        try:
            df = ak.stock_zh_a_spot_em()
            keep_cols = [
                "代码", "名称", "所属行业",
                "总市值", "流通市值",
                "市盈率-动态", "市净率", "股息率",
                "涨跌幅", "换手率", "振幅", "量比",
                "最高", "最低", "今开", "昨收", "最新价",
                "成交量", "成交额"
            ]
            cols = [c for c in keep_cols if c in df.columns]
            df = df[cols].copy()
            rename_map = {
                "市盈率-动态": "市盈率",
                "最新价": "收盘价"
            }
            df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
            if "成交量" in df.columns:
                df = df[df["成交量"] > 0]
            if "收盘价" in df.columns:
                df = df[df["收盘价"] > 0]
            return df.reset_index(drop=True)
        except Exception as e:
            return pd.DataFrame()

    def _get_stock_list_tushare(self):
        """tushare 获取股票列表"""
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            # 1. 获取基础信息
            basics = pro.stock_basic(exchange="", list_status="L")
            basics = basics[["ts_code", "name", "industry"]].copy()
            basics.rename(columns={"ts_code": "代码", "name": "名称", "industry": "所属行业"}, inplace=True)
            basics["代码"] = basics["代码"].str.replace(r"\.SH|\.SZ", "", regex=True)

            # 2. 获取每日指标 (daily_basic: close/pe_ttm/pb/dv_ttm/total_mv/circ_mv/turnover_rate)
            trade_date = self._get_last_trade_date()
            daily = pro.daily_basic(trade_date=trade_date)
            daily.rename(columns={
                "ts_code": "代码",
                "close": "收盘价",
                "pe_ttm": "市盈率",
                "pb": "市净率",
                "dv_ttm": "股息率",
                "total_mv": "总市值",
                "circ_mv": "流通市值",
                "turnover_rate": "换手率",
                "volume_ratio": "量比",
                "pct_chg": "涨跌幅"
            }, inplace=True)
            daily["代码"] = daily["代码"].str.replace(r"\.SH|\.SZ", "", regex=True)
            # 单位转换：total_mv 是万元 -> 元
            if "总市值" in daily.columns:
                daily["总市值"] = daily["总市值"] * 10000
            if "流通市值" in daily.columns:
                daily["流通市值"] = daily["流通市值"] * 10000
            # 股息率 tushare 是百分比数值(如3.5表示3.5%)，代码统一按百分比处理
            if "股息率" in daily.columns:
                daily["股息率"] = daily["股息率"]  # 保持原样，配置文件中阈值也按此单位

            # 3. 合并
            df = pd.merge(basics, daily, on="代码", how="inner")
            # 清洗：排除停牌/未交易（收盘价<=0 或 换手率为0）
            if "收盘价" in df.columns:
                df = df[df["收盘价"] > 0]
            if "换手率" in df.columns:
                df = df[df["换手率"] > 0]

            # 4. 保留统一列名（daily_basic 无成交量/成交额，用空值占位）
            keep = ["代码", "名称", "所属行业", "总市值", "流通市值",
                    "市盈率", "市净率", "股息率", "涨跌幅", "换手率", "量比", "收盘价"]
            available = [c for c in keep if c in df.columns]
            result = df[available].copy()
            # 补充策略可能需要的字段（用0占位）
            for col in ["成交量", "成交额", "振幅", "最高", "最低", "今开", "昨收", "量比"]:
                if col not in result.columns:
                    result[col] = 0
            return result.reset_index(drop=True)
        except Exception as e:
            return pd.DataFrame()

    def _get_mock_stock_list(self):
        """模拟数据"""
        mock_path = os.path.join(self.cache_dir, "mock_spot.csv")
        if os.path.exists(mock_path):
            return pd.read_csv(mock_path, encoding="utf-8-sig")
        from utils.mock_data import generate_mock_spot
        df = generate_mock_spot(200)
        os.makedirs(self.cache_dir, exist_ok=True)
        df.to_csv(mock_path, index=False, encoding="utf-8-sig")
        return df

    # ==================== 历史 K 线 ====================

    def get_hist_data(self, symbol: str, period: str = "daily", days: int = 300, use_mock: bool = False):
        if use_mock or self.data_source == "mock":
            return self._get_mock_hist(days)

        if self.data_source in ("auto", "akshare"):
            df = self._get_hist_data_akshare(symbol, period, days)
            if not df.empty:
                return df

        if self.data_source in ("auto", "tushare"):
            df = self._get_hist_data_tushare(symbol, days)
            if not df.empty:
                return df

        return self._get_mock_hist(days)

    def _get_hist_data_akshare(self, symbol: str, period: str, days: int):
        if symbol.startswith("sh") or symbol.startswith("sz"):
            symbol = symbol[2:]
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        try:
            df = ak.stock_zh_a_hist(
                symbol=symbol, period=period,
                start_date=start, end_date=end, adjust="qfq"
            )
            if df is not None and not df.empty:
                df["日期"] = pd.to_datetime(df["日期"])
                df = df.sort_values("日期").reset_index(drop=True)
            return df
        except Exception:
            return pd.DataFrame()

    def _get_hist_data_tushare(self, symbol: str, days: int):
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            if symbol.startswith("sh") or symbol.startswith("sz"):
                symbol = symbol[2:]
            suffix = ".SH" if symbol.startswith("6") else ".SZ"
            ts_code = f"{symbol}{suffix}"
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
            df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            if df is not None and not df.empty:
                df.rename(columns={
                    "trade_date": "日期",
                    "open": "开盘",
                    "high": "最高",
                    "low": "最低",
                    "close": "收盘",
                    "vol": "成交量",
                    "amount": "成交额",
                    "pct_chg": "涨跌幅",
                    "pre_close": "昨收"
                }, inplace=True)
                df["日期"] = pd.to_datetime(df["日期"])
                df = df.sort_values("日期").reset_index(drop=True)
            return df
        except Exception:
            return pd.DataFrame()

    def _get_mock_hist(self, days: int):
        from utils.mock_data import generate_mock_hist
        trend = np.random.choice(["up", "down", "sideways"], p=[0.4, 0.3, 0.3])
        return generate_mock_hist(days=days, trend=trend)

    # ==================== 指数 ====================

    def get_index_hist(self, symbol: str = "000001", days: int = 300):
        if self.data_source in ("auto", "akshare"):
            df = self._get_hist_data_akshare(symbol, "daily", days)
            if not df.empty:
                return df
        if self.data_source in ("auto", "tushare"):
            pro = self._get_tushare_pro()
            if pro:
                try:
                    ts_code = "000001.SH" if symbol == "000001" else f"{symbol}.SH"
                    end = datetime.now().strftime("%Y%m%d")
                    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
                    df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
                    if df is not None and not df.empty:
                        df.rename(columns={
                            "trade_date": "日期",
                            "open": "开盘",
                            "high": "最高",
                            "low": "最低",
                            "close": "收盘",
                            "vol": "成交量",
                            "amount": "成交额",
                            "pct_chg": "涨跌幅"
                        }, inplace=True)
                        df["日期"] = pd.to_datetime(df["日期"])
                        df = df.sort_values("日期").reset_index(drop=True)
                    return df
                except Exception:
                    pass
        return pd.DataFrame()

    # ==================== 财务数据 ====================

    def get_financial_indicators(self, symbol: str) -> dict:
        if symbol in self._financial_cache:
            return self._financial_cache[symbol]

        result = {}
        try:
            if symbol.startswith("sh") or symbol.startswith("sz"):
                symbol = symbol[2:]
            df = ak.stock_financial_analysis_indicator(symbol=symbol)
            if df is not None and not df.empty:
                latest = df.iloc[0]
                result = {
                    "roe": float(latest.get("净资产收益率", 0) or 0),
                    "gross_margin": float(latest.get("销售毛利率", 0) or 0),
                    "net_margin": float(latest.get("销售净利率", 0) or 0),
                    "revenue_growth": float(latest.get("营业收入增长率", 0) or 0),
                    "profit_growth_yoy": float(latest.get("净利润增长率", 0) or 0),
                    "debt_ratio": float(latest.get("资产负债率", 0) or 0),
                }
        except Exception:
            pass

        if not result:
            pro = self._get_tushare_pro()
            if pro:
                try:
                    suffix = ".SH" if symbol.startswith("6") else ".SZ"
                    df = pro.fina_indicator(ts_code=f"{symbol}{suffix}", limit=1)
                    if df is not None and not df.empty:
                        r = df.iloc[0]
                        result = {
                            "roe": float(r.get("roe", 0) or 0),
                            "gross_margin": float(r.get("grossprofit_margin", 0) or 0),
                            "net_margin": float(r.get("netprofit_margin", 0) or 0),
                            "revenue_growth": float(r.get("q_sales_yoy", 0) or 0),
                            "profit_growth_yoy": float(r.get("q_netprofit_yoy", 0) or 0),
                            "debt_ratio": float(r.get("debt_to_assets", 0) or 0),
                        }
                except Exception:
                    pass

        self._financial_cache[symbol] = result
        return result

    # ==================== 北向/融资融券 ====================

    def get_northbound_holding(self) -> pd.DataFrame:
        if self._northbound_cache is not None:
            return self._northbound_cache
        try:
            df = ak.stock_hsgt_stock_em()
            self._northbound_cache = df
            return df
        except Exception:
            return pd.DataFrame()

    def get_margin_data(self) -> pd.DataFrame:
        if self._margin_cache is not None:
            return self._margin_cache
        try:
            df = ak.stock_margin_detail_em()
            self._margin_cache = df
            return df
        except Exception:
            return pd.DataFrame()

    def get_daily_spot(self) -> pd.DataFrame:
        return self.get_stock_list()

    # ==================== 资金面高级数据 (tushare) ====================

    def get_moneyflow_data(self, codes: list, trade_date: str = None) -> pd.DataFrame:
        """获取个股资金流向数据 (moneyflow)"""
        if self._moneyflow_cache is not None:
            return self._moneyflow_cache
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            trade_date = trade_date or self._get_last_trade_date()
            # tushare moneyflow 不支持批量代码，需逐个查询或按日期查
            # 策略：先按日期查当日全部，再过滤
            df = pro.moneyflow(trade_date=trade_date)
            if df is not None and not df.empty:
                df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                # 计算关键指标
                # 主力净流入 = 大单 + 特大单买入 - 卖出
                df["主力净流入"] = (
                    df.get("buy_lg_amount", 0) + df.get("buy_elg_amount", 0)
                    - df.get("sell_lg_amount", 0) - df.get("sell_elg_amount", 0)
                )
                # 散户净流出 = 小单买入 - 小单卖出（负值表示散户在卖）
                df["散户净流出"] = df.get("buy_sm_amount", 0) - df.get("sell_sm_amount", 0)
                # 净流入占比（用买入金额合计估算成交额）
                total_turnover = (
                    df.get("buy_sm_amount", 0) + df.get("buy_md_amount", 0)
                    + df.get("buy_lg_amount", 0) + df.get("buy_elg_amount", 0)
                )
                df["净流入占比"] = df["主力净流入"] / total_turnover.replace(0, np.nan)
                # 过滤
                if codes:
                    df = df[df["代码"].isin(codes)]
                self._moneyflow_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] 资金流向获取失败: {e}")
        return pd.DataFrame()

    def get_top_list_data(self, trade_date: str = None) -> pd.DataFrame:
        """获取当日龙虎榜 (top_list)"""
        if self._top_list_cache is not None:
            return self._top_list_cache
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            trade_date = trade_date or self._get_last_trade_date()
            df = pro.top_list(trade_date=trade_date)
            if df is not None and not df.empty:
                df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                # 机构席位净买入（l_buy = 机构买入, l_sell = 机构卖出）
                df["机构净买入"] = df.get("l_buy", 0) - df.get("l_sell", 0)
                self._top_list_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] 龙虎榜获取失败: {e}")
        return pd.DataFrame()

    def get_top_inst_data(self, trade_date: str = None) -> pd.DataFrame:
        """获取龙虎榜机构席位明细 (top_inst)"""
        if self._top_inst_cache is not None:
            return self._top_inst_cache
        pro = self._get_tushare_pro()
        if not pro:
            return pd.DataFrame()
        try:
            trade_date = trade_date or self._get_last_trade_date()
            df = pro.top_inst(trade_date=trade_date)
            if df is not None and not df.empty:
                df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                # side=0 买入, side=1 卖出; 聚合每个股票的机构净买入
                inst_buy = df[df.get("side", 1) == 0].groupby("代码")["net_buy"].sum()
                inst_sell = df[df.get("side", 1) == 1].groupby("代码")["net_buy"].sum().abs()
                summary = pd.DataFrame({
                    "机构买入": inst_buy,
                    "机构卖出": inst_sell
                }).fillna(0)
                summary["机构净买入"] = summary["机构买入"] - summary["机构卖出"]
                summary = summary.reset_index()
                self._top_inst_cache = summary
                return summary
        except Exception as e:
            print(f"     [WARN] 龙虎榜机构明细获取失败: {e}")
        return pd.DataFrame()

    # ==================== 停牌/涨跌停/股东人数 (Phase 3) ====================

    def get_suspend_stocks(self) -> set:
        """获取当前停牌的股票代码集合 (pro.suspend_daily)"""
        if self._suspend_cache is not None:
            return self._suspend_cache
        result = set()
        pro = self._get_tushare_pro()
        if pro:
            try:
                df = pro.suspend_daily(suspend_type="S")
                if df is not None and not df.empty:
                    codes = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                    result = set(codes.tolist())
            except Exception:
                pass
        self._suspend_cache = result
        return result

    def get_limit_list_data(self) -> pd.DataFrame:
        """获取当日涨跌停股票 (pro.limit_list)"""
        if self._limit_list_cache is not None:
            return self._limit_list_cache
        result = pd.DataFrame()
        pro = self._get_tushare_pro()
        if pro:
            try:
                trade_date = self._get_last_trade_date()
                # U=涨停 D=跌停
                df = pro.limit_list(trade_date=trade_date, limit_type="D")
                if df is not None and not df.empty:
                    df["代码"] = df["ts_code"].str.replace(r"\.SH|\.SZ", "", regex=True)
                    result = df
                self._limit_list_cache = result
            except Exception:
                pass
        return result

    def get_holder_trend(self, symbol: str) -> dict:
        """获取股东人数变化趋势 (pro.stk_holdernumber)
        返回: {"trend": "concentrate"|"disperse"|"stable", "change_pct": float}
        股东人数下降 = 筹码集中 = 利好
        """
        if symbol in self._holder_cache:
            return self._holder_cache[symbol]
        result = {"trend": "stable", "change_pct": 0}
        pro = self._get_tushare_pro()
        if pro:
            try:
                suffix = ".SH" if symbol.startswith("6") else ".SZ"
                df = pro.stk_holdernumber(ts_code=f"{symbol}{suffix}", limit=3)
                if df is not None and not df.empty and len(df) >= 2:
                    current = float(df.iloc[0]["holder_num"])
                    previous = float(df.iloc[1]["holder_num"])
                    if previous > 0:
                        change_pct = (current - previous) / previous * 100
                        result["change_pct"] = round(change_pct, 2)
                        if change_pct < -5:
                            result["trend"] = "concentrate"
                        elif change_pct > 10:
                            result["trend"] = "disperse"
            except Exception:
                pass
        self._holder_cache[symbol] = result
        return result

    # ==================== 股权质押数据 ====================

    def get_pledge_ratio_data(self) -> pd.DataFrame:
        """
        获取A股股权质押比例数据 (akshare)
        返回: DataFrame[代码, 名称, 质押比例, 质押股数, 质押市值, 笔数]
        """
        if self._pledge_cache is not None:
            return self._pledge_cache

        if self.data_source == "mock":
            return pd.DataFrame()

        try:
            # akshare 接口：获取股票质押比例
            df = ak.stock_gpzy_pledge_ratio_em()
            if df is not None and not df.empty:
                # 统一列名
                rename_map = {
                    "股票代码": "代码",
                    "股票简称": "名称",
                    "质押比例": "质押比例",
                    "质押股数": "质押股数",
                    "质押市值": "质押市值",
                    "笔数": "笔数",
                }
                # 只保留存在的列
                actual_rename = {k: v for k, v in rename_map.items() if k in df.columns}
                df = df.rename(columns=actual_rename)
                # 确保代码列为字符串
                if "代码" in df.columns:
                    df["代码"] = df["代码"].astype(str).str.strip()
                # 确保质押比例为数值
                if "质押比例" in df.columns:
                    df["质押比例"] = pd.to_numeric(df["质押比例"], errors="coerce").fillna(0)
                self._pledge_cache = df
                return df
        except Exception as e:
            print(f"     [WARN] 股权质押数据获取失败: {e}")
        return pd.DataFrame()

    def get_high_pledge_stocks(self, threshold: float = 30.0) -> pd.DataFrame:
        """
        获取高质押比例股票列表（避雷清单）
        threshold: 质押比例阈值（%），默认30%
        返回: DataFrame[代码, 名称, 质押比例]
        """
        df = self.get_pledge_ratio_data()
        if df.empty or "质押比例" not in df.columns:
            return pd.DataFrame()
        high = df[df["质押比例"] >= threshold].copy()
        if not high.empty and "代码" in high.columns:
            high = high.sort_values("质押比例", ascending=False).reset_index(drop=True)
        return high

    def get_market_capital_env(self) -> dict:
        """获取大盘资金面环境：北向资金 + 融资融券"""
        if self._market_capital_cache is not None:
            return self._market_capital_cache
        env = {
            "north_money": None,
            "margin_rzye": None,
            "notes": []
        }
        pro = self._get_tushare_pro()
        if not pro:
            return env
        trade_date = self._get_last_trade_date()
        # 北向资金
        try:
            df = pro.moneyflow_hsgt(trade_date=trade_date)
            if df is not None and not df.empty:
                north = df.iloc[0].get("north_money", 0)
                env["north_money"] = round(north / 1e4, 2)  # 转为亿
                if north > 0:
                    env["notes"].append(f"北向净流入 {env['north_money']:.1f} 亿")
                elif north < 0:
                    env["notes"].append(f"北向净流出 {abs(env['north_money']):.1f} 亿")
                else:
                    env["notes"].append("北向资金平衡")
        except Exception:
            pass
        # 融资融券
        try:
            df = pro.margin(trade_date=trade_date)
            if df is not None and not df.empty:
                rzye = df["rzye"].sum() if "rzye" in df.columns else 0
                env["margin_rzye"] = round(rzye / 1e8, 2)  # 转为亿
                env["notes"].append(f"融资余额 {env['margin_rzye']:.0f} 亿")
        except Exception:
            pass
        self._market_capital_cache = env
        return env
