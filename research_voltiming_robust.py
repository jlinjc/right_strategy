"""
research_voltiming_robust.py — VOLFULL(波動縮放牛市槓桿)參數穩健性掃描
========================================================================
上一步:VOLFULL 在 SMH/QQQ 用同樣平均風險換到 +0.03~0.04 Sharpe。丟真錢前必驗:
換 波動窗 / clip界 / 中位窗 後,是否『跨參數一致 > 原版 且 > 裸槓桿』;
一動就垮 = curve-fit 收案;跨格穩健 = 真 edge(Moreira-Muir),可落地。

尺:誠實 shift(1),全期(含GFC)。基準 = BASE(cap1.5)與 LEV2.0。
VOLFULL 牛市 cap = clip(1.5 × 中位vol / 實現vol, clip_lo, clip_hi);熊市維持保守。
"""
import sys, warnings, itertools
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

TICKERS = ['SMH', 'QQQ', 'SPY']
START = '2006-01-01'
VOL_WINS = [10, 20, 40, 60]
MED_WINS = [126, 252]
CLIPS = [(1.0, 2.0), (1.0, 2.5), (0.8, 2.5), (1.0, 3.0)]


def dl(s):
    x = yf.download(s, start=START, auto_adjust=True, progress=False)['Close'].dropna()
    return x.iloc[:, 0] if isinstance(x, pd.DataFrame) else x


def sharpe(r):
    r = r.dropna(); sd = r.std()
    return r.mean() / sd * np.sqrt(252) if sd > 0 else np.nan


def expo_series(c, ma, el, h, vx, bull, rv, medv, budget, cap, floor, mode, clip_lo, clip_hi):
    n = len(c); below = c < el
    db = np.zeros(n, int); run = 0
    for t in range(n):
        run = run + 1 if below[t] else 0; db[t] = run
    expo = np.zeros(n); in_pos = False; last_base = 0.0
    for t in range(n):
        credit_off = h[t] <= 0; reclaim = (not np.isnan(ma[t])) and c[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        if not bull[t]:
            cap_eff = cap
        elif mode == 'BASE':
            cap_eff = cap
        elif mode == 'LEV':
            cap_eff = 2.0
        else:
            scale = (medv[t] / rv[t]) if (rv[t] and not np.isnan(rv[t]) and not np.isnan(medv[t])) else 1.0
            cap_eff = min(max(cap * scale, clip_lo), clip_hi)
        if not in_pos:
            if reclaim and not credit_off: in_pos = True
        else:
            if credit_off: in_pos = False
            elif below[t] and not (panic and db[t] < C.PANIC_DELAY): in_pos = False
        if in_pos and reclaim:
            sd = (c[t] - el[t]) / c[t]; last_base = min(budget / max(sd, floor), cap_eff)
            expo[t] = last_base * h[t]
        elif in_pos:
            expo[t] = last_base * h[t]
    return pd.Series(expo, index=range(n))


def analyze(tk, close, health, vix):
    p = C.PARAMS.get(tk, C.DEFAULT_PARAM); budget, cap, buf = p['budget'], p['cap'], p['exit_buf']
    ma200 = close.rolling(C.MA).mean(); el = ma200 * buf; ret = close.pct_change()
    slope_up = (ma200 > ma200.shift(20))
    base = pd.concat([close.rename('c'), ma200.rename('ma'), el.rename('el'), health.rename('h'),
                      vix.rename('v'), ret.rename('r'), (slope_up & (health >= 1.0)).rename('bull')], axis=1)
    dfb = base.dropna()
    args = (dfb['c'].values, dfb['ma'].values, dfb['el'].values, dfb['h'].values, dfb['v'].values, dfb['bull'].values)
    r_ret = dfb['r'].values
    def perf_of(ex):
        s = pd.Series(ex.values, index=dfb.index).shift(1) * dfb['r']
        return sharpe(s.dropna())
    sh_base = perf_of(expo_series(*args, None, None, budget, cap, C.STOP_DIST_FLOOR, 'BASE', 0, 0))
    sh_lev = perf_of(expo_series(*args, None, None, budget, cap, C.STOP_DIST_FLOOR, 'LEV', 0, 0))
    print("=" * 88)
    print(f"  {tk}  基準:BASE(cap1.5) Sharpe {sh_base:.3f} | LEV2.0 {sh_lev:.3f}  (VOLFULL 要同時 > 這兩個)")
    print("=" * 88)
    print(f"  {'volWin':>7}{'medWin':>7}{'clip':>10}{'Sharpe':>9}{'vsBASE':>9}{'vsLEV':>8}   判定")
    win = tot = 0
    for vw, mw, (lo, hi) in itertools.product(VOL_WINS, MED_WINS, CLIPS):
        rvs = (ret.rolling(vw).std() * np.sqrt(252))
        medv = rvs.rolling(mw).median()
        rv_al = rvs.reindex(dfb.index).values
        mv_al = medv.reindex(dfb.index).values
        ex = expo_series(*args, rv_al, mv_al, budget, cap, C.STOP_DIST_FLOOR, 'VOL', lo, hi)
        sh = perf_of(ex)
        tot += 1
        ok = sh > sh_base + 0.005 and sh > sh_lev + 0.005
        win += 1 if ok else 0
        print(f"  {vw:>7}{mw:>7}{f'[{lo},{hi}]':>10}{sh:>9.3f}{sh-sh_base:>+9.3f}{sh-sh_lev:>+8.3f}   {'✅' if ok else '·'}")
    print(f"  → {win}/{tot} 組同時勝過 BASE 與 LEV(越高越穩健,非 curve-fit)\n")


def main():
    print("📥 下載 ...")
    closes = {t: dl(t) for t in TICKERS}
    hyg, lqd, vix = dl('HYG'), dl('LQD'), dl('^VIX')
    hln = lambda s: (s >= s.rolling(C.MA).mean()).astype(float)
    for t in TICKERS:
        idx = closes[t].index
        health = pd.concat([hln(hyg).reindex(idx).ffill(), hln(lqd).reindex(idx).ffill()], axis=1).mean(axis=1)
        analyze(t, closes[t], health, vix.reindex(idx).ffill())


if __name__ == '__main__':
    main()
