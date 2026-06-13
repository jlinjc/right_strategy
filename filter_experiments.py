"""
filter_experiments.py - 進場濾網實驗室
==========================================
進場固定 MA 拉回、出場固定 Ⓗ(分批 3ATR + 吊燈3.5)，每次只加一個濾網當
「額外進場條件」，跟無濾網基準比 OOS，找出真正有用的濾網。

濾網函式介面: f(idx, df, benchmark_df) -> bool  (True=通過, False=擋掉訊號)
全部只用「截至 idx 當天」的資料，無 look-ahead。

可乾淨回測的技術面濾網：
  f_vol_dryup     進場日量縮 (健康拉回，O'Neil)
  f_vol_surge     進場日放量 (反彈帶量確認)
  f_mom_positive  TTM squeeze 動能為正 (動能未轉弱)
  f_adx_strong    ADX > 門檻 (趨勢夠強，非盤整)
  f_rs_margin     3M 報酬贏 QQQ 達 X% 以上 (領頭羊更嚴格)
  f_not_extended  收盤未過度乖離 10MA (不追高)

forward PE 等基本面：無逐日歷史快照，無法乾淨回測(look-ahead)，另以「當下篩選」處理。
"""

import numpy as np
import pandas as pd

from backtest_strategies import calc_atr
from exit_experiments import ExitScaledHybrid


# ============================================================
# 濾網函式
# ============================================================
def f_vol_dryup(idx, df, bm):
    """進場日量 < 20日均量 (拉回量縮 = 健康)"""
    if idx < 20:
        return True
    v = float(df['Volume'].iloc[idx])
    avg = float(df['Volume'].iloc[idx-20:idx].mean())
    return avg <= 0 or v < avg


def f_vol_surge(idx, df, bm):
    """進場日量 > 20日均量 ×1.2 (反彈帶量)"""
    if idx < 20:
        return True
    v = float(df['Volume'].iloc[idx])
    avg = float(df['Volume'].iloc[idx-20:idx].mean())
    return avg <= 0 or v > avg * 1.2


def _ttm_mom(idx, df, length=20):
    """TTM squeeze 動能值 (close - (donchian中點 + sma)/2)，正=多頭動能"""
    if idx < length:
        return 0.0
    c = df['Close'].iloc[idx-length+1:idx+1]
    h = df['High'].iloc[idx-length+1:idx+1]
    l = df['Low'].iloc[idx-length+1:idx+1]
    sma = float(c.mean())
    donch_mid = (float(h.max()) + float(l.min())) / 2
    mid = (donch_mid + sma) / 2
    return float(c.iloc[-1]) - mid


def f_mom_positive(idx, df, bm):
    """TTM 動能 > 0"""
    return _ttm_mom(idx, df) > 0


def _adx(idx, df, n=14):
    """計算第 idx 根的 ADX(n)（用 idx 之前的資料，無 look-ahead）"""
    lo = max(0, idx - 3 * n)
    h = df['High'].iloc[lo:idx+1]
    l = df['Low'].iloc[lo:idx+1]
    c = df['Close'].iloc[lo:idx+1]
    if len(h) < n + 2:
        return 0.0
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    plus_di = 100 * pd.Series(plus_dm, index=h.index).rolling(n).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=h.index).rolling(n).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(n).mean()
    v = adx.iloc[-1]
    return float(v) if not pd.isna(v) else 0.0


def make_f_adx(threshold=20.0):
    def f(idx, df, bm):
        return _adx(idx, df) >= threshold
    return f


def make_f_rs_margin(margin_pct=10.0):
    """個股 3M 報酬 - QQQ 3M 報酬 >= margin_pct%"""
    def f(idx, df, bm):
        if idx < 63 or bm is None:
            return True
        stock_ret = float(df['Close'].iloc[idx] / df['Close'].iloc[idx-63] - 1) * 100
        cur_date = df.index[idx]
        bm_mask = bm.index <= cur_date
        bm_close = bm['Close'][bm_mask]
        if len(bm_close) < 63:
            return True
        bm_ret = float(bm_close.iloc[-1] / bm_close.iloc[-63] - 1) * 100
        return (stock_ret - bm_ret) >= margin_pct
    return f


def make_f_not_extended(max_ext=1.08):
    """收盤 < 10MA × max_ext (不追高乖離過大的拉回)"""
    def f(idx, df, bm):
        if idx < 10:
            return True
        ma10 = float(df['Close'].iloc[idx-9:idx+1].mean())
        return float(df['Close'].iloc[idx]) < ma10 * max_ext
    return f


# ── 系統化大盤 REGIME 濾網（用大盤自身長期趨勢，非被否決的21EMA）──
def _bm_close_upto(idx, df, bm):
    """取 benchmark 截至當前 K 棒日期的收盤序列（無 look-ahead）"""
    cur = df.index[idx]
    return bm['Close'][bm.index <= cur]


def make_f_index_above_ma(n=200):
    """大盤(QQQ) 收盤 > 自身 N 日均線 才允許進場（順大盤主趨勢）"""
    def f(idx, df, bm):
        if bm is None:
            return True
        c = _bm_close_upto(idx, df, bm)
        if len(c) < n:
            return True
        return float(c.iloc[-1]) >= float(c.iloc[-n:].mean())
    return f


def make_f_index_golden(fast=50, slow=200):
    """大盤 fast 均線 >= slow 均線（多頭排列/金叉狀態）才進場"""
    def f(idx, df, bm):
        if bm is None:
            return True
        c = _bm_close_upto(idx, df, bm)
        if len(c) < slow:
            return True
        return float(c.iloc[-fast:].mean()) >= float(c.iloc[-slow:].mean())
    return f


def make_f_index_rising(n=50, lookback=10):
    """大盤 N 日均線「上升中」(今天 > lookback 天前) 才進場（趨勢方向，非位置）"""
    def f(idx, df, bm):
        if bm is None:
            return True
        c = _bm_close_upto(idx, df, bm)
        if len(c) < n + lookback:
            return True
        ma_now = float(c.iloc[-n:].mean())
        ma_prev = float(c.iloc[-n-lookback:-lookback].mean())
        return ma_now >= ma_prev
    return f


# ============================================================
# 帶濾網的策略 (進場 MA拉回 + 濾網 + 出場 Ⓗ)
# ============================================================
class FilteredScaledExit(ExitScaledHybrid):
    """
    在 Ⓗ(分批 3ATR + 吊燈3.5) 的基礎上，進場再套一層濾網。
    filters: [(name, func), ...]，全部通過才送出訊號。
    """
    def __init__(self, filters=None, atr_target_mult=3.0, scale_frac=0.5,
                 trail_mult=3.5, **kw):
        super().__init__(atr_target_mult=atr_target_mult, scale_frac=scale_frac,
                         trail_mult=trail_mult, **kw)
        self.filters = filters or []

    def scan(self, idx, ticker, df, benchmark_df):
        sig = super().scan(idx, ticker, df, benchmark_df)
        if sig is None:
            return None
        for _name, f in self.filters:
            if not f(idx, df, benchmark_df):
                return None
        return sig
