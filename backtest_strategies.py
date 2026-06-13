# [LOCAL VERSION DIFF]: 全新加入的檔案。定義各種回測策略（例如均線突破、HOD 突破等）的具體進出場邏輯。
"""
backtest_strategies.py - 回測策略訊號產生器
=============================================
將現有的 TD9 / 均線回測 / 動能突破 策略改寫成可回測版本。
每個策略實作 scan() 和 check_exit() 介面。

重要規則：
  - scan() 只能使用「截至當天」的數據，不可偷看未來
  - 回傳的 Signal.entry_price 是「觸發日收盤價」
  - 實際成交由 Portfolio 以「次日開盤價 + 滑點」處理
"""

import numpy as np
import pandas as pd
from typing import Optional
from backtest_engine import Signal, Position


# ============================================================
# 工具函式（從現有模組移植）
# ============================================================
def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series,
             window: int = 14) -> pd.Series:
    """計算 Average True Range"""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def calc_td_count_at(df: pd.DataFrame, idx: int) -> int:
    """
    計算到第 idx 根 K 棒為止的 TD Sequential 計數值。
    正數 = 連漲 (賣出竭盡), 負數 = 連跌 (買入竭盡)
    """
    td_count = 0
    start = max(4, 0)
    for i in range(start, idx + 1):
        if i - 4 < 0:
            continue
        cur = df['Close'].iloc[i]
        prior = df['Close'].iloc[i - 4]
        if cur > prior:
            td_count = td_count + 1 if td_count >= 0 else 1
        elif cur < prior:
            td_count = td_count - 1 if td_count <= 0 else -1
        else:
            td_count = 0
    return td_count


# ============================================================
# 基底策略
# ============================================================
class BaseStrategy:
    """所有策略的基底類別"""
    name: str = 'base'

    def scan(self, idx: int, ticker: str, df: pd.DataFrame,
             benchmark_df: pd.DataFrame) -> Optional[Signal]:
        """
        掃描第 idx 天是否觸發進場訊號。
        只能使用 df.iloc[:idx+1] 的數據。
        """
        raise NotImplementedError

    def check_exit(self, position: Position, idx: int,
                   df: pd.DataFrame) -> Optional[str]:
        """
        檢查持倉是否應該出場。
        回傳出場原因字串，或 None 表示繼續持有。
        """
        raise NotImplementedError


# ============================================================
# 策略 1: TD9 逆向買入
# ============================================================
class TD9BuyStrategy(BaseStrategy):
    """
    移植自 us_scanner_td9.py
    進場: TD ≤ -9 (DeMark 完整竭盡訊號，第9根) 且股價 > 200MA
    停損: 進場日最低價 或 entry - 1.5×ATR(14)，取較大者
    出場: 股價回到 10MA 之上 or 持有超過 10 天 or 觸發停損
    """
    name = 'td9_buy'

    def __init__(self, td_threshold: int = -9, max_hold_days: int = 10):
        self.td_threshold = td_threshold
        self.max_hold_days = max_hold_days

    def scan(self, idx: int, ticker: str, df: pd.DataFrame,
             benchmark_df: pd.DataFrame) -> Optional[Signal]:
        # 需要至少 200 天的數據才能計算 200MA
        if idx < 200:
            return None

        # 計算 TD 值
        td_val = calc_td_count_at(df, idx)
        if td_val > self.td_threshold:  # 注意: td_threshold 是負數
            return None

        close = df['Close'].iloc[idx]
        low = df['Low'].iloc[idx]

        # 趨勢濾網: 必須在 200MA 之上
        ma200 = df['Close'].iloc[max(0, idx-199):idx+1].mean()
        if close < ma200:
            return None

        # 計算 ATR 停損
        atr_series = calc_atr(
            df['High'].iloc[max(0, idx-20):idx+1],
            df['Low'].iloc[max(0, idx-20):idx+1],
            df['Close'].iloc[max(0, idx-20):idx+1],
            14
        )
        atr_val = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else close * 0.03

        # 停損: 取 (進場日最低價) 和 (entry - 1.5×ATR) 中較大者（較接近 entry 的）
        stop_by_low = low
        stop_by_atr = close - 1.5 * atr_val
        stop_loss = max(stop_by_low, stop_by_atr)

        # 確保停損至少低於收盤價 1%
        if stop_loss >= close * 0.99:
            stop_loss = close * 0.97

        return Signal(
            date=df.index[idx].date() if hasattr(df.index[idx], 'date') else df.index[idx],
            ticker=ticker,
            direction='long',
            strategy=self.name,
            entry_price=close,
            stop_loss=round(stop_loss, 2),
            reason=f'TD{td_val} 下跌竭盡 (>200MA)',
            priority=abs(td_val),  # TD9 > TD8
        )

    def check_exit(self, position: Position, idx: int,
                   df: pd.DataFrame) -> Optional[str]:
        if idx >= len(df):
            return None

        close = df['Close'].iloc[idx]
        low = df['Low'].iloc[idx]

        # 停損 (用最低價檢查，模擬盤中觸發)
        if low <= position.stop_loss:
            return f'停損觸發 (${position.stop_loss:.2f})'

        # 持有超過最大天數
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'

        # 股價回到 10MA 之上 (做 2 天確認)
        if idx >= 10:
            ma10 = df['Close'].iloc[idx-9:idx+1].mean()
            if close > ma10 and position.days_held >= 2:
                return f'已回到 10MA 之上 (${ma10:.2f})'

        return None


# ============================================================
# 策略 2: 均線回測買入
# ============================================================
class MAPullbackStrategy(BaseStrategy):
    """
    移植自 us_scanner_ma.py
    進場: 多頭趨勢 (> 200MA) 且回測觸碰 10/20/60MA 且收在均線之上
    停損: entry - 1.5×ATR(14) 或 跌破觸碰的均線
    出場: 移動停利 (連續 2 天跌破 10MA) or 觸發停損
    """
    name = 'ma_pullback'

    def __init__(self, ma_windows=(10, 20, 60), max_hold_days: int = 20,
                 require_market_uptrend: bool = False):
        self.ma_windows = ma_windows
        self.max_hold_days = max_hold_days
        # 市場環境門檻：QQQ 收盤 > 自身 21日EMA 才允許產生訊號
        # (O'Neil Follow-through + Weinstein Stage 2 概念，過濾修正期)
        self.require_market_uptrend = require_market_uptrend

    def scan(self, idx: int, ticker: str, df: pd.DataFrame,
             benchmark_df: pd.DataFrame) -> Optional[Signal]:
        if idx < 200:
            return None

        close = df['Close'].iloc[idx]
        high = df['High'].iloc[idx]
        low = df['Low'].iloc[idx]

        # 市場環境門檻：大盤(QQQ)必須在 21日EMA 之上 (不偷看未來)
        if self.require_market_uptrend and benchmark_df is not None:
            cur_date = df.index[idx]
            bm_mask = benchmark_df.index <= cur_date
            bm_close = benchmark_df['Close'][bm_mask]
            if len(bm_close) >= 21:
                bm_ema21 = bm_close.ewm(span=21, adjust=False).mean().iloc[-1]
                if bm_close.iloc[-1] < bm_ema21:
                    return None

        # 趨勢濾網
        ma200 = df['Close'].iloc[max(0, idx-199):idx+1].mean()
        if close < ma200:
            return None

        # 檢查是否觸碰某條均線
        touched_ma = None
        touched_ma_val = None

        for w in self.ma_windows:
            if idx < w:
                continue
            ma_val = df['Close'].iloc[idx-w+1:idx+1].mean()
            if pd.isna(ma_val):
                continue

            # 觸碰條件: 最低價 <= 均線 <= 最高價 且 收盤 >= 均線 × 0.99
            is_touching = (low <= ma_val <= high) or (ma_val < low <= ma_val * 1.005)

            if is_touching and close >= ma_val * 0.99:
                touched_ma = f'{w}MA'
                touched_ma_val = ma_val
                break  # 取最短的均線（最敏感的）

        if touched_ma is None:
            return None

        # 額外過濾: 均線要呈多頭排列 (10 > 20)
        if idx >= 20:
            ma10 = df['Close'].iloc[idx-9:idx+1].mean()
            ma20 = df['Close'].iloc[idx-19:idx+1].mean()
            if ma10 < ma20 * 0.99:  # 均線死叉，不是好的回測環境
                return None

        # RS 濾網: 過去 63 天個股漲幅必須超越 benchmark (SPY/QQQ)
        if idx >= 63 and len(benchmark_df) >= 63:
            current_date = df.index[idx]
            start_date = df.index[idx - 63]
            stock_ret = close / df['Close'].iloc[idx - 63] - 1
            bm_mask = (benchmark_df.index >= start_date) & (benchmark_df.index <= current_date)
            if bm_mask.sum() >= 20:
                bm_ret = benchmark_df['Close'][bm_mask].iloc[-1] / benchmark_df['Close'][bm_mask].iloc[0] - 1
                if stock_ret <= bm_ret:  # 跑輸大盤，不是領頭羊，跳過
                    return None

        # 52 週高點濾網: 距離 52 週高點不能超過 25%
        if idx >= 252:
            high_52w = df['High'].iloc[max(0, idx - 252):idx + 1].max()
            if close < high_52w * 0.75:
                return None

        # ATR 停損
        atr_series = calc_atr(
            df['High'].iloc[max(0, idx-20):idx+1],
            df['Low'].iloc[max(0, idx-20):idx+1],
            df['Close'].iloc[max(0, idx-20):idx+1],
            14
        )
        atr_val = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else close * 0.03

        stop_by_atr = close - 1.5 * atr_val
        stop_by_ma = touched_ma_val * 0.99 if touched_ma_val else close * 0.97
        stop_loss = max(stop_by_atr, stop_by_ma)

        if stop_loss >= close * 0.99:
            stop_loss = close * 0.97

        return Signal(
            date=df.index[idx].date() if hasattr(df.index[idx], 'date') else df.index[idx],
            ticker=ticker,
            direction='long',
            strategy=self.name,
            entry_price=close,
            stop_loss=round(stop_loss, 2),
            reason=f'多頭回測 {touched_ma} (>200MA)',
            priority=1.0 if touched_ma == '60MA' else 0.5,  # 60MA 權重更高
        )

    def check_exit(self, position: Position, idx: int,
                   df: pd.DataFrame) -> Optional[str]:
        if idx >= len(df):
            return None

        close = df['Close'].iloc[idx]
        low = df['Low'].iloc[idx]

        # 停損
        if low <= position.stop_loss:
            return f'停損觸發 (${position.stop_loss:.2f})'

        # 最大持有天數
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'

        # 移動停利: 連續 2 天收在 10MA 之下
        if idx >= 10 and position.days_held >= 3:
            ma10 = df['Close'].iloc[idx-9:idx+1].mean()
            if close < ma10:
                # 再看前一天是否也跌破
                if idx >= 11:
                    prev_close = df['Close'].iloc[idx-1]
                    prev_ma10 = df['Close'].iloc[idx-10:idx].mean()
                    if prev_close < prev_ma10:
                        return f'連續 2 天跌破 10MA (${ma10:.2f})'

        # 移動停損: 從最高點回落超過 2×ATR
        if position.days_held >= 2:
            atr_series = calc_atr(
                df['High'].iloc[max(0, idx-20):idx+1],
                df['Low'].iloc[max(0, idx-20):idx+1],
                df['Close'].iloc[max(0, idx-20):idx+1],
                14
            )
            atr_val = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else close * 0.03
            trailing_stop = position.highest_since_entry - 2 * atr_val
            if close < trailing_stop:
                return f'移動停損觸發 (最高${position.highest_since_entry:.2f} → 停損${trailing_stop:.2f})'

        return None


# ============================================================
# 策略 3: 動能突破買入
# ============================================================
class MomentumBreakoutStrategy(BaseStrategy):
    """
    移植自 us_momentum_scanner.py
    進場: 3M 報酬 > 20% + 10日整理 < 12% + 突破前 20 日高點 + 量 > 1.5×20日均量
    停損: 突破日最低價 或 entry - 1.5×ATR(14)
    出場: 移動停利 (跌破 10MA 收盤價) or 觸發停損
    """
    name = 'momentum_breakout'

    def __init__(self, min_3m_return: float = 20.0,
                 max_consolidation: float = 12.0,
                 volume_multiplier: float = 1.5,
                 max_hold_days: int = 30):
        self.min_3m_return = min_3m_return
        self.max_consolidation = max_consolidation
        self.volume_multiplier = volume_multiplier
        self.max_hold_days = max_hold_days

    def scan(self, idx: int, ticker: str, df: pd.DataFrame,
             benchmark_df: pd.DataFrame) -> Optional[Signal]:
        # 需要至少 63 天 (3 個月) + 20 天額外數據
        if idx < 83:
            return None

        close = df['Close'].iloc[idx]
        high = df['High'].iloc[idx]
        low = df['Low'].iloc[idx]
        volume = df['Volume'].iloc[idx]

        # 3M 報酬率
        close_3m_ago = df['Close'].iloc[idx - 63]
        ret_3m = (close / close_3m_ago - 1) * 100
        if ret_3m < self.min_3m_return:
            return None

        # 均線濾網: 股價 > 50MA
        ma50 = df['Close'].iloc[idx-49:idx+1].mean()
        if close < ma50:
            return None

        # 10日整理範圍 (波動收斂)
        recent_high = df['High'].iloc[idx-9:idx+1].max()
        recent_low = df['Low'].iloc[idx-9:idx+1].min()
        consolidation = (recent_high / recent_low - 1) * 100
        if consolidation > self.max_consolidation:
            return None

        # 突破前 20 日高點 (不含今天)
        prev_20d_high = df['High'].iloc[idx-20:idx].max()
        if high <= prev_20d_high:
            return None

        # 量能確認
        avg_vol_20 = df['Volume'].iloc[idx-20:idx].mean()
        if avg_vol_20 > 0 and volume < avg_vol_20 * self.volume_multiplier:
            return None

        # ATR 停損
        atr_series = calc_atr(
            df['High'].iloc[max(0, idx-20):idx+1],
            df['Low'].iloc[max(0, idx-20):idx+1],
            df['Close'].iloc[max(0, idx-20):idx+1],
            14
        )
        atr_val = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else close * 0.03

        stop_by_low = low
        stop_by_atr = close - 1.5 * atr_val
        stop_loss = max(stop_by_low, stop_by_atr)

        if stop_loss >= close * 0.99:
            stop_loss = close * 0.97

        return Signal(
            date=df.index[idx].date() if hasattr(df.index[idx], 'date') else df.index[idx],
            ticker=ticker,
            direction='long',
            strategy=self.name,
            entry_price=close,
            stop_loss=round(stop_loss, 2),
            reason=f'動能突破 (3M:{ret_3m:.0f}% 整理:{consolidation:.1f}% 量:{volume/avg_vol_20:.1f}x)',
            priority=ret_3m / 10,  # 動能越強優先級越高
        )

    def check_exit(self, position: Position, idx: int,
                   df: pd.DataFrame) -> Optional[str]:
        if idx >= len(df):
            return None

        close = df['Close'].iloc[idx]
        low = df['Low'].iloc[idx]

        # 停損
        if low <= position.stop_loss:
            return f'停損觸發 (${position.stop_loss:.2f})'

        # 最大持有天數
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'

        # 移動停利: 跌破 10MA (持倉至少 3 天後)
        if idx >= 10 and position.days_held >= 3:
            ma10 = df['Close'].iloc[idx-9:idx+1].mean()
            if close < ma10:
                return f'跌破 10MA (${ma10:.2f})'

        # 移動停損: 從最高點回落超過 2×ATR
        if position.days_held >= 2:
            atr_series = calc_atr(
                df['High'].iloc[max(0, idx-20):idx+1],
                df['Low'].iloc[max(0, idx-20):idx+1],
                df['Close'].iloc[max(0, idx-20):idx+1],
                14
            )
            atr_val = atr_series.iloc[-1] if not pd.isna(atr_series.iloc[-1]) else close * 0.03
            trailing_stop = position.highest_since_entry - 2 * atr_val
            if close < trailing_stop:
                return f'移動停損觸發 (最高${position.highest_since_entry:.2f})'

        return None


# ============================================================
# 策略清單（方便外部調用）
# ============================================================
ALL_STRATEGIES = [
    TD9BuyStrategy(),
    MAPullbackStrategy(),
    MomentumBreakoutStrategy(),
]
