import pandas as pd
import numpy as np

class TechnicalAnalyzer:
    """技术指标分析——完整版（MA/KDJ/MACD/RSI/背离/突破/回踩）"""

    def __init__(self, df: pd.DataFrame, config: dict = None):
        self.df = df.copy()
        self.config = config or {}
        # 从配置中读取技术指标参数（有默认值作为回退）
        self.macd_fast = self.config.get("macd_fast", 12)
        self.macd_slow = self.config.get("macd_slow", 26)
        self.macd_signal = self.config.get("macd_signal", 9)
        self.rsi_period = self.config.get("rsi_period", 14)
        self.kdj_k = self.config.get("kdj_k", 9)
        self.kdj_d = self.config.get("kdj_d", 3)
        self.kdj_j = self.config.get("kdj_j", 3)
        self.vol_breakout_ratio = self.config.get("volume_breakout_ratio", 1.5)
        self.vol_shrink_ratio = self.config.get("volume_shrink_ratio", 0.7)
        if not self.df.empty and len(self.df) >= 60:
            self._calculate_all()

    def _calculate_all(self):
        df = self.df
        close = df["收盘"]
        high = df.get("最高", close)
        low = df.get("最低", close)
        volume = df.get("成交量", pd.Series(1, index=df.index))

        # 均线
        df["MA5"] = close.rolling(window=5).mean()
        df["MA20"] = close.rolling(window=20).mean()
        df["MA60"] = close.rolling(window=60).mean()
        df["MA250"] = close.rolling(window=250).mean()

        # MACD
        df["MACD_DIF"], df["MACD_DEA"], df["MACD_BAR"] = self._macd(
            close, self.macd_fast, self.macd_slow, self.macd_signal
        )

        # RSI
        df["RSI"] = self._rsi(close, self.rsi_period)

        # KDJ
        df["K"], df["D"], df["J"] = self._kdj(
            high, low, close, self.kdj_k, self.kdj_d, self.kdj_j
        )

        # 成交量均线
        df["VOL_MA20"] = volume.rolling(window=20).mean()

        # ATR(14) — 平均真实波幅
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        df["ATR"] = tr.ewm(alpha=1/14, adjust=False).mean()

    def _rsi(self, series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _macd(self, series: pd.Series, fast=12, slow=26, signal=9):
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        dea = dif.ewm(span=signal, adjust=False).mean()
        bar = (dif - dea) * 2
        return dif, dea, bar

    def _kdj(self, high, low, close, n=9, m1=3, m2=3):
        lowest_low = low.rolling(window=n).min()
        highest_high = high.rolling(window=n).max()
        rsv = (close - lowest_low) / (highest_high - lowest_low) * 100
        k = rsv.ewm(com=m1-1, adjust=False).mean()
        d = k.ewm(com=m2-1, adjust=False).mean()
        j = 3 * k - 2 * d
        return k, d, j

    def _detect_divergence(self, price_col: str, indicator_col: str, lookback: int = 30) -> str:
        """检测背离：价格新低但指标未新低（底背离），或价格新高但指标未新高（顶背离）"""
        if len(self.df) < lookback + 10:
            return "none"

        window = self.df.iloc[-lookback:]
        price = window[price_col]
        indicator = window[indicator_col]

        price_min_idx = price.idxmin()
        price_max_idx = price.idxmax()
        indicator_min_idx = indicator.idxmin()
        indicator_max_idx = indicator.idxmax()

        # 底背离：价格创新低，但指标未创新低
        if price_min_idx == indicator_min_idx:
            pass
        elif price_min_idx > indicator_min_idx:
            return "bottom"

        # 顶背离：价格创新高，但指标未创新高
        if price_max_idx == indicator_max_idx:
            pass
        elif price_max_idx > indicator_max_idx:
            return "top"

        return "none"

    def score(self) -> dict:
        """返回技术面综合评分与详细信号"""
        if self.df.empty or len(self.df) < 60:
            return {"score": 0, "signals": [], "details": {}}

        latest = self.df.iloc[-1]
        prev = self.df.iloc[-2] if len(self.df) > 1 else latest
        prev2 = self.df.iloc[-3] if len(self.df) > 2 else prev
        signals = []
        score = 0.0
        details = {}

        # ---------- 1. 均线系统 ----------
        # 年线判断
        above_ma250 = latest["收盘"] > latest.get("MA250", 0) if not pd.isna(latest.get("MA250")) else False
        details["above_ma250"] = above_ma250
        # 价格与关键均线关系（供趋势过滤器使用）
        details["below_ma20"] = latest["收盘"] < latest.get("MA20", 0) if not pd.isna(latest.get("MA20")) else False
        details["below_ma60"] = latest["收盘"] < latest.get("MA60", 0) if not pd.isna(latest.get("MA60")) else False
        if above_ma250:
            score += 0.15
            signals.append("站上年线")

        # 均线多头排列
        ma_aligned = (
            latest["收盘"] > latest["MA5"] > latest["MA20"] > latest["MA60"]
        )
        ma_bearish = (
            latest["收盘"] < latest["MA5"] < latest["MA20"] < latest["MA60"]
        )
        details["ma_aligned"] = ma_aligned
        details["ma_bearish"] = ma_bearish

        if ma_aligned:
            score += 0.25
            signals.append("均线多头排列")
        elif ma_bearish:
            score -= 0.25
            signals.append("均线空头排列")

        # MA60 方向
        ma60_5days_ago = self.df.iloc[-6]["MA60"] if len(self.df) >= 6 else latest["MA60"]
        ma60_rising = latest["MA60"] > ma60_5days_ago
        details["ma60_rising"] = ma60_rising
        if ma60_rising:
            score += 0.1

        # ---------- 2. MACD ----------
        macd_golden = (
            prev["MACD_DIF"] < prev["MACD_DEA"] and latest["MACD_DIF"] > latest["MACD_DEA"]
        )
        macd_dead = (
            prev["MACD_DIF"] > prev["MACD_DEA"] and latest["MACD_DIF"] < latest["MACD_DEA"]
        )
        macd_above_zero = latest["MACD_DIF"] > 0 and latest["MACD_DEA"] > 0
        details["macd_golden"] = macd_golden
        details["macd_dead"] = macd_dead
        details["macd_above_zero"] = macd_above_zero

        if macd_golden:
            if macd_above_zero:
                score += 0.25
                signals.append("MACD零上金叉")
            # 零轴下方金叉不加分 — 往往是假信号，趋势未转多
        elif macd_dead:
            score -= 0.25
            signals.append("MACD死叉")

        # MACD 底背离
        macd_divergence = self._detect_divergence("收盘", "MACD_DIF", lookback=30)
        details["macd_divergence"] = macd_divergence
        if macd_divergence == "bottom":
            score += 0.15
            signals.append("MACD底背离")
        elif macd_divergence == "top":
            score -= 0.15
            signals.append("MACD顶背离")

        # ---------- 3. RSI ----------
        rsi = latest["RSI"]
        details["rsi"] = round(rsi, 1)
        if rsi < 30:
            score += 0.15
            signals.append("RSI超卖")
        elif rsi < 40:
            score += 0.05
            signals.append("RSI偏低")
        elif rsi > 70:
            score -= 0.15
            signals.append("RSI超买")
        elif rsi > 60:
            score -= 0.05
            signals.append("RSI偏高")

        # ---------- 4. KDJ ----------
        j = latest["J"]
        k = latest["K"]
        d = latest["D"]
        details["kdj_j"] = round(j, 1)
        details["kdj_kd"] = (round(k, 1), round(d, 1))

        # KDJ 低位金叉
        kdj_golden = prev["K"] < prev["D"] and latest["K"] > latest["D"]
        kdj_dead = prev["K"] > prev["D"] and latest["K"] < latest["D"]
        details["kdj_golden"] = kdj_golden
        details["kdj_dead"] = kdj_dead

        if kdj_golden and j < 20:
            score += 0.15
            signals.append("KDJ低位金叉")
        elif kdj_dead and j > 80:
            score -= 0.15
            signals.append("KDJ高位死叉")

        # ---------- 5. 成交量信号 ----------
        vol = latest.get("成交量", 0)
        vol_ma20 = latest.get("VOL_MA20", 0)
        if vol_ma20 > 0:
            vol_ratio = vol / vol_ma20
            details["volume_ratio"] = round(vol_ratio, 2)

            # 放量突破：今日放量 + 价格创近期新高
            recent_high = self.df["最高"].iloc[-20:].max()
            price_breakout = latest["收盘"] >= recent_high * 0.98
            if vol_ratio > self.vol_breakout_ratio and price_breakout:
                score += 0.2
                signals.append("放量突破")

            # 缩量回踩：价格在MA20附近 + 缩量
            near_ma20 = abs(latest["收盘"] - latest["MA20"]) / latest["MA20"] < 0.03
            if vol_ratio < self.vol_shrink_ratio and near_ma20 and latest["收盘"] > latest["MA20"]:
                score += 0.1
                signals.append("缩量回踩企稳")

            # 高位天量滞涨
            if vol_ratio > 2.5 and abs(latest["涨跌幅"]) < 2 and latest["收盘"] == latest["最高"]:
                score -= 0.15
                signals.append("高位放量滞涨")

            # 量价配合/背离：放量+上涨=做多，放量+下跌=出货
            pct_chg = latest.get("涨跌幅", 0)
            if vol_ratio > self.vol_breakout_ratio and pct_chg > 3:
                score += 0.08
                signals.append("量价齐升")
            elif vol_ratio > self.vol_breakout_ratio and pct_chg < -3:
                score -= 0.12
                signals.append("放量下跌")

        # ---------- 6. 形态：箱体突破 ----------
        if len(self.df) >= 40:
            recent_40 = self.df.iloc[-40:]
            box_high = recent_40["最高"].max()
            box_low = recent_40["最低"].min()
            box_range = (box_high - box_low) / box_low if box_low > 0 else 1
            details["box_range"] = round(box_range * 100, 1)

            # 长期横盘后突破（振幅 < 20%，今日突破上沿）
            if box_range < 0.25 and latest["收盘"] > box_high * 0.98 and vol_ratio > 1.3:
                score += 0.15
                signals.append("箱体突破")

        # 均线支撑/压力位（供风控模块使用）
        details["ma20_support"] = round(float(latest["MA20"]), 2) if "MA20" in latest else None
        details["ma60_support"] = round(float(latest["MA60"]), 2) if "MA60" in latest else None
        # ATR 波动率（供仓位管理使用）
        details["atr"] = round(float(latest["ATR"]), 2) if not pd.isna(latest.get("ATR")) else None

        return {"score": round(max(-1, min(1, score)), 2), "signals": signals, "details": details}
