"""
rs_selection.py - 橫斷面相對強度(RS)排名選股（洞#1 的系統化解法）
=====================================================================
把「手挑 AI 贏家」變成規則：每個交易日，對整個廣股池算多週期動能、跨股排
百分位，只允許在「RS 排名前 X%」的最強股做拉回進場。用規則重現「交易領導者」，
不靠事後諸葛 —— 這才是可重複、非主題紅利的 edge。

無 look-ahead：date D 的 RS 只用截至 D 的трailing 報酬，跨股排名只用 D 當天的值。
"""

import numpy as np
import pandas as pd

from filter_experiments import FilteredScaledExit, f_mom_positive, make_f_not_extended


def _rs_score(closes: pd.DataFrame, mode: str, bm_close: pd.Series = None) -> pd.DataFrame:
    """各種 RS 分數定義（皆 point-in-time，只用過去報酬）"""
    r21 = closes / closes.shift(21) - 1
    r63 = closes / closes.shift(63) - 1
    r126 = closes / closes.shift(126) - 1
    r189 = closes / closes.shift(189) - 1
    r252 = closes / closes.shift(252) - 1

    if mode == 'blend':       # 現行：0.4×3M + 0.3×6M + 0.3×12M
        return 0.4 * r63 + 0.3 * r126 + 0.3 * r252
    if mode == 'mom126':      # 純 6 個月動能
        return r126
    if mode == 'mom252':      # 純 12 個月動能
        return r252
    if mode == 'ibd':         # IBD 式 4 季加權（近期權重高）
        return 0.4 * r63 + 0.2 * r126 + 0.2 * r189 + 0.2 * r252
    if mode == '12m1m':       # 學術經典 12-1：跳過最近 1 個月避免短期反轉
        return closes.shift(21) / closes.shift(252) - 1
    if mode == 'voladj':      # 風險調整動能：6M報酬 / 日報酬波動（偏好平滑強勢）
        daily = closes.pct_change()
        vol = daily.rolling(126).std()
        return r126 / vol.replace(0, np.nan)
    if mode == 'fast':        # 較快：0.5×1M + 0.5×3M
        return 0.5 * r21 + 0.5 * r63
    if mode == 'residual':
        # 殘差動能（Blitz/Huij/Martens；2024-25 重驗）：對基準做 6M 滾動單因子
        # 迴歸，剝掉市場 beta，取「殘差資訊比率 = alpha / 殘差波動」。
        # = 剝 beta 後的 voladj。理論上半個波動、避開多數動能崩盤(崩盤本質是
        # beta 反轉的擁擠回補)。point-in-time：第 t 日只用 t-126..t 報酬迴歸。
        if bm_close is None:
            raise ValueError("residual 模式需要 bm_close（基準收盤序列）")
        W = 126
        r_s = closes.pct_change()
        r_m = bm_close.reindex(closes.index).pct_change()
        var_m = r_m.rolling(W).var()
        mean_m = r_m.rolling(W).mean()
        # 滾動 cov(個股, 市場) = E[XM] - E[X]E[M]
        cov = (r_s.mul(r_m, axis=0).rolling(W).mean()
               .sub(r_s.rolling(W).mean().mul(mean_m, axis=0), axis=0))
        beta = cov.div(var_m, axis=0)
        resid = r_s.sub(beta.mul(r_m, axis=0))   # 殘差日報酬；其窗內均值即 alpha
        return resid.rolling(W).mean() / resid.rolling(W).std().replace(0, np.nan)
    raise ValueError(f'unknown RS mode: {mode}')


def compute_rs_rank(stocks: dict, mode: str = 'voladj', bm_close: pd.Series = None) -> dict:
    """
    回傳 {timestamp: {ticker: rs_percentile(0~100)}}。
    跨股票橫斷面百分位排名（100=最強）。mode 見 _rs_score。
    預設 voladj（風險調整動能）— compare_rs_metric.py 證實最強(Sharpe+1.91)。
    mode='residual' 需傳 bm_close（剝市場 beta 的殘差動能）。
    """
    closes = pd.DataFrame({tk: s['Close'] for tk, s in stocks.items()}).sort_index()
    score = _rs_score(closes, mode, bm_close=bm_close)
    pct = score.rank(axis=1, pct=True) * 100   # 每列(每天)跨股排名

    rank = {}
    for ts, row in pct.iterrows():
        d = row.dropna()
        if len(d):
            rank[ts] = d.to_dict()
    return rank


def compute_id_rank(stocks: dict, window: int = 252) -> dict:
    """
    Frog-in-the-Pan 資訊離散度（Da, Gurun & Warachka；2024 重驗）。
      ID = sign(過去 window 日總報酬) × (%下跌日 − %上漲日)
    回傳 {timestamp: {ticker: ID 橫斷面百分位 0~100}}。
    **低百分位 = 連續型(很多小漲日、平滑爬升) = 動能持續性高 = 該留；**
    高百分位 = 跳躍型(少數大跳空) = 動能多半一次反映完 = 該避。
    point-in-time：第 t 日的 ID 只用 t-window..t 的日報酬符號分布。
    """
    closes = pd.DataFrame({tk: s['Close'] for tk, s in stocks.items()}).sort_index()
    rets = closes.pct_change()
    pret = closes / closes.shift(window) - 1
    pos = (rets > 0).rolling(window).mean()
    neg = (rets < 0).rolling(window).mean()
    id_val = np.sign(pret) * (neg - pos)
    pct = id_val.rank(axis=1, pct=True) * 100

    rank = {}
    for ts, row in pct.iterrows():
        d = row.dropna()
        if len(d):
            rank[ts] = d.to_dict()
    return rank


class RSRankScaledExit(FilteredScaledExit):
    """
    在定案系統(MA拉回+動能+不追高 / 分批出場)之上，再加橫斷面 RS 排名閘門：
    只有 RS 百分位 >= rs_threshold 的股票才允許進場。
    rs_rank 由 compute_rs_rank() 預先算好後注入。
    """
    def __init__(self, rs_rank: dict, rs_threshold: float = 80.0, **kw):
        super().__init__(**kw)
        self.rs_rank = rs_rank
        self.rs_threshold = rs_threshold

    def scan(self, idx, ticker, df, benchmark_df):
        date = df.index[idx]
        pct = self.rs_rank.get(date, {}).get(ticker)
        if pct is None or pct < self.rs_threshold:
            return None
        return super().scan(idx, ticker, df, benchmark_df)


def make_rs_strat(rs_rank, threshold):
    return [RSRankScaledExit(
        rs_rank=rs_rank, rs_threshold=threshold,
        filters=[('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))],
        atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]


class RSIDScaledExit(RSRankScaledExit):
    """
    在 RS 排名選股之上，再加 Frog-in-the-Pan ID 閘門：
    只允許 ID 百分位 ≤ id_max_pct（連續型動能）的股票進場，擋掉跳躍型。
    用來測「ID 在既有選股 metric 之上是否還有增量鑑別力」。
    """
    def __init__(self, id_rank: dict, id_max_pct: float = 50.0, **kw):
        super().__init__(**kw)
        self.id_rank = id_rank
        self.id_max_pct = id_max_pct

    def scan(self, idx, ticker, df, benchmark_df):
        date = df.index[idx]
        idp = self.id_rank.get(date, {}).get(ticker)
        if idp is not None and idp > self.id_max_pct:
            return None   # 跳躍型(離散)動能，持續性差，擋掉
        return super().scan(idx, ticker, df, benchmark_df)


def make_rs_id_strat(rs_rank, id_rank, threshold=80.0, id_max_pct=50.0):
    return [RSIDScaledExit(
        rs_rank=rs_rank, rs_threshold=threshold,
        id_rank=id_rank, id_max_pct=id_max_pct,
        filters=[('mom+', f_mom_positive), ('not_ext', make_f_not_extended(1.08))],
        atr_target_mult=3.0, scale_frac=0.5, trail_mult=3.5)]
