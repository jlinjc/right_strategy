"""
exit_experiments.py - 出場策略實驗室
=========================================
進場固定為 MA 拉回 (MAPullbackStrategy.scan 不變)，只替換出場邏輯，
用相同的 walk-forward 視窗公平比較哪種出場最適合這套右側動能策略。

所有變體共同點：
  - 都保留「硬停損」(low <= entry stop_loss) —— 右側動能仍需災難保護
  - 都保留一個「最大持有天」backstop，避免無限期套牢
  - 差別只在「獲利了結 / 移動停利」的邏輯

變體：
  ExitBaseline       現有出場（連2天跌破10MA + 2×ATR 移動停損）
  ExitMACross        純均線出場（跌破 N日EMA 收盤即走）
  ExitChandelier     吊燈出場（最高點 - mult×ATR 移動停利）
  ExitFibExtension   費波那契擴展停利（拉回擺動投射 1.272/1.618/2.0 目標）
  ExitAdamMirror     亞當理論二次鏡像（climax 長K / 趨勢反轉K 出場）
  ExitTimeStop       純時間出場（固定持有 N 天）
"""

import pandas as pd
from typing import Optional

from backtest_engine import Position
from backtest_strategies import MAPullbackStrategy, calc_atr


# ============================================================
# 共用工具
# ============================================================
def _entry_idx(position: Position, df: pd.DataFrame) -> Optional[int]:
    """用 entry_date 在 df 中定位進場 K 棒的整數索引"""
    if hasattr(df.index[0], 'date'):
        matches = df.index[df.index.date == position.entry_date]
    else:
        matches = df.index[df.index == position.entry_date]
    if len(matches) == 0:
        return None
    return df.index.get_loc(matches[0])


def _atr_at(df: pd.DataFrame, idx: int, window: int = 14) -> float:
    """計算第 idx 根的 ATR(window)"""
    lo = max(0, idx - window - 5)
    s = calc_atr(df['High'].iloc[lo:idx+1], df['Low'].iloc[lo:idx+1],
                 df['Close'].iloc[lo:idx+1], window)
    v = s.iloc[-1]
    return float(v) if not pd.isna(v) else float(df['Close'].iloc[idx]) * 0.03


def _hard_stop(position: Position, df: pd.DataFrame, idx: int) -> Optional[str]:
    """所有變體共用的硬停損檢查"""
    if df['Low'].iloc[idx] <= position.stop_loss:
        return f'停損觸發 (${position.stop_loss:.2f})'
    return None


# ============================================================
# 1. 基準線（= 現有 MAPullbackStrategy 出場，原樣繼承）
# ============================================================
class ExitBaseline(MAPullbackStrategy):
    """現有出場邏輯，直接沿用父類別 check_exit"""
    pass


# ============================================================
# 2. 純均線出場
# ============================================================
class ExitMACross(MAPullbackStrategy):
    """跌破 N日EMA 收盤就走（最乾淨的右側出場）"""
    def __init__(self, ema_span: int = 10, min_hold: int = 2,
                 max_hold_days: int = 40, **kw):
        super().__init__(max_hold_days=max_hold_days, **kw)
        self.ema_span = ema_span
        self.min_hold = min_hold

    def check_exit(self, position, idx, df):
        if idx >= len(df):
            return None
        hs = _hard_stop(position, df, idx)
        if hs:
            return hs
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'
        if position.days_held >= self.min_hold and idx >= self.ema_span:
            ema = df['Close'].iloc[:idx+1].ewm(span=self.ema_span, adjust=False).mean().iloc[-1]
            if df['Close'].iloc[idx] < ema:
                return f'收盤跌破 {self.ema_span}EMA (${ema:.2f})'
        return None


# ============================================================
# 3. 吊燈出場 (Chandelier Exit)
# ============================================================
class ExitChandelier(MAPullbackStrategy):
    """移動停利 = 進場後最高價 - mult×ATR；跌破即走"""
    def __init__(self, mult: float = 3.0, min_hold: int = 1,
                 max_hold_days: int = 40, **kw):
        super().__init__(max_hold_days=max_hold_days, **kw)
        self.mult = mult
        self.min_hold = min_hold

    def check_exit(self, position, idx, df):
        if idx >= len(df):
            return None
        hs = _hard_stop(position, df, idx)
        if hs:
            return hs
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'
        if position.days_held >= self.min_hold:
            atr = _atr_at(df, idx)
            chand = position.highest_since_entry - self.mult * atr
            if df['Close'].iloc[idx] < chand:
                return (f'吊燈停利 (最高${position.highest_since_entry:.2f} '
                        f'- {self.mult}×ATR → ${chand:.2f})')
        return None


# ============================================================
# 4. 費波那契擴展停利
# ============================================================
class ExitFibExtension(MAPullbackStrategy):
    """
    用「拉回前的上漲腿」投射 Fib 擴展目標停利。
      impulse_low  = 進場前 base 的低點 (lookback 區間最低)
      swing_high   = 進場前的擺動高點 (lookback 區間最高)
      pullback_low = 進場區的低點
      target       = pullback_low + (swing_high - impulse_low) × ratio
    觸及 target 即在強勢中了結；同時保留硬停損 + 吊燈 backstop 防止回吐。
    """
    def __init__(self, ratio: float = 1.618, lookback: int = 40,
                 backstop_mult: float = 3.5, max_hold_days: int = 40, **kw):
        super().__init__(max_hold_days=max_hold_days, **kw)
        self.ratio = ratio
        self.lookback = lookback
        self.backstop_mult = backstop_mult

    def check_exit(self, position, idx, df):
        if idx >= len(df):
            return None
        hs = _hard_stop(position, df, idx)
        if hs:
            return hs
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'

        e = _entry_idx(position, df)
        if e is not None and e >= 5:
            lo = max(0, e - self.lookback)
            impulse_low = float(df['Low'].iloc[lo:e+1].min())
            swing_high = float(df['High'].iloc[lo:e+1].max())
            pullback_low = float(df['Low'].iloc[max(0, e-5):e+1].min())
            leg = swing_high - impulse_low
            if leg > 0:
                target = pullback_low + leg * self.ratio
                if df['High'].iloc[idx] >= target:
                    return f'Fib {self.ratio} 擴展達標 (${target:.2f})'

        # backstop 吊燈，避免大幅回吐
        if position.days_held >= 1:
            atr = _atr_at(df, idx)
            chand = position.highest_since_entry - self.backstop_mult * atr
            if df['Close'].iloc[idx] < chand:
                return f'回吐 backstop (${chand:.2f})'
        return None


# ============================================================
# 5. 亞當理論二次鏡像出場
# ============================================================
class ExitAdamMirror(MAPullbackStrategy):
    """
    亞當理論：價格走勢趨於對稱，進場後的上漲擺動會在「鏡像點」竭盡。
    實作（務實版，可再調）：
      A) climax 長紅K — 在獲利且乖離放大時出現超大實體陽線(全距 > climax_mult×ATR
         且收在當日高檔)，視為 blow-off 高潮，逆勢賣在強勢裡。
      B) 趨勢反轉K — 出現大實體陰線(實體 > rev_mult×ATR 且收盤 < 前一日收盤)，
         鏡像反轉確認，出場。
    保留硬停損 + 最大持有天。
    """
    def __init__(self, climax_mult: float = 2.5, rev_mult: float = 1.5,
                 min_hold: int = 2, max_hold_days: int = 40, **kw):
        super().__init__(max_hold_days=max_hold_days, **kw)
        self.climax_mult = climax_mult
        self.rev_mult = rev_mult
        self.min_hold = min_hold

    def check_exit(self, position, idx, df):
        if idx >= len(df):
            return None
        hs = _hard_stop(position, df, idx)
        if hs:
            return hs
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'
        if position.days_held < self.min_hold:
            return None

        o = float(df['Open'].iloc[idx]); h = float(df['High'].iloc[idx])
        l = float(df['Low'].iloc[idx]); c = float(df['Close'].iloc[idx])
        prev_c = float(df['Close'].iloc[idx-1]) if idx >= 1 else c
        atr = _atr_at(df, idx)
        rng = h - l
        body = abs(c - o)

        # A) climax 長紅K (blow-off 高潮，賣在強勢)
        in_profit = c > position.entry_price
        if in_profit and rng > self.climax_mult * atr and c >= o and c >= h - rng * 0.3:
            return f'亞當鏡像: climax 長紅K 高潮 (全距{rng/atr:.1f}×ATR)'

        # B) 趨勢反轉大陰K
        if c < o and body > self.rev_mult * atr and c < prev_c:
            return f'亞當鏡像: 反轉大陰K (實體{body/atr:.1f}×ATR)'
        return None


# ============================================================
# 7. 混合：分批出場 (Fib 停利一半 + 寬吊燈抱另一半)
# ============================================================
class ExitScaledHybrid(MAPullbackStrategy):
    """
    右側動能「抱贏家」的混合出場：
      第一階段 — 觸及 Fib 擴展目標(預設 1.618)先賣 scale_frac(預設 50%) 鎖利
      第二階段 — 剩餘部位用寬吊燈(trail_mult×ATR)讓它繼續跑
      全程保留硬停損 + 最大持有天 backstop
    第一階段用 ('scale', fraction, fill_price, reason) 回傳給引擎做部分平倉。
    """
    def __init__(self, fib_ratio: float = 1.618, scale_frac: float = 0.5,
                 trail_mult: float = 3.5, lookback: int = 40,
                 atr_target_mult: float = None, max_hold_days: int = 40, **kw):
        super().__init__(max_hold_days=max_hold_days, **kw)
        self.fib_ratio = fib_ratio
        self.scale_frac = scale_frac
        self.trail_mult = trail_mult
        self.lookback = lookback
        # 若設定，第一段目標改用 進場價 + atr_target_mult×ATR(進場日)，
        # 比 Fib 擴展近、更常觸發分批
        self.atr_target_mult = atr_target_mult

    def _first_target(self, position, df):
        e = _entry_idx(position, df)
        if e is None or e < 5:
            return None
        if self.atr_target_mult is not None:
            atr0 = _atr_at(df, e)
            return position.entry_price + self.atr_target_mult * atr0
        lo = max(0, e - self.lookback)
        impulse_low = float(df['Low'].iloc[lo:e+1].min())
        swing_high = float(df['High'].iloc[lo:e+1].max())
        pullback_low = float(df['Low'].iloc[max(0, e-5):e+1].min())
        leg = swing_high - impulse_low
        if leg <= 0:
            return None
        return pullback_low + leg * self.fib_ratio

    def check_exit(self, position, idx, df):
        if idx >= len(df):
            return None
        hs = _hard_stop(position, df, idx)
        if hs:
            return hs
        if position.days_held >= self.max_hold_days:
            return f'持有已達 {self.max_hold_days} 天'

        already_scaled = getattr(position, 'scaled_out', False)

        # 第一階段：觸及目標，先賣一半鎖利
        if not already_scaled:
            target = self._first_target(position, df)
            if target is not None and df['High'].iloc[idx] >= target:
                lbl = (f'{self.atr_target_mult}×ATR' if self.atr_target_mult is not None
                       else f'Fib {self.fib_ratio}')
                return ('scale', self.scale_frac, target,
                        f'分批1: {lbl} 達標賣{int(self.scale_frac*100)}% (${target:.2f})')

        # 第二階段：剩餘部位寬吊燈移動停利
        if position.days_held >= 1:
            atr = _atr_at(df, idx)
            chand = position.highest_since_entry - self.trail_mult * atr
            if df['Close'].iloc[idx] < chand:
                tag = '分批2: ' if already_scaled else ''
                return f'{tag}寬吊燈停利 ({self.trail_mult}×ATR → ${chand:.2f})'
        return None


# ============================================================
# 6. 純時間出場
# ============================================================
class ExitTimeStop(MAPullbackStrategy):
    """固定持有 N 天就走（只配硬停損），測時間因子的純粹貢獻"""
    def __init__(self, hold_days: int = 10, **kw):
        super().__init__(max_hold_days=hold_days, **kw)

    def check_exit(self, position, idx, df):
        if idx >= len(df):
            return None
        hs = _hard_stop(position, df, idx)
        if hs:
            return hs
        if position.days_held >= self.max_hold_days:
            return f'持有滿 {self.max_hold_days} 天'
        return None
