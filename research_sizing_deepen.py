"""
research_sizing_deepen.py — 深化 sizing:波動預測(EWMA)vs 實現波動 + vol×信用cushion
========================================================================
今天落地的 QQQ vol-timing 用『事後實現波動(20日)』,只 QQQ 穩健。兩個懸案:
  Q1 換 EWMA 波動『預測』(文獻 Moreira-Muir 用 forecast 比 realized 更有效)→ 更穩?能救 SMH?
  Q2 vol × 信用cushion(HYG/LQD 高於自己200MA 的距離=信用有多少緩衝)合併調 cap → 更多 juice?
牛市 cap = clip(base_cap × (中位vol/當前vol) × 信用因子, cap×0.667, cap×1.667)。進出全同原版,誠實尺。
robust:掃 vol 窗 {10,20,40,60} × {realized, ewma},數 SMH/QQQ 有幾窗勝過原版=穩健度。
"""
import sys, warnings, itertools
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

TICKERS = ['SMH', 'QQQ', 'SPY']
START = '2006-01-01'
LO, HI = 0.667, 1.667   # cap 上下限倍率(base 1.5 → [1.0,2.5])


def dl(s):
    x = yf.download(s, start=START, auto_adjust=True, progress=False)['Close'].dropna()
    return x.iloc[:, 0] if isinstance(x, pd.DataFrame) else x


def perf(r):
    r = r.dropna(); sd = r.std(); eq = (1 + r).cumprod()
    return {'sharpe': r.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
            'cagr': (eq.iloc[-1] ** (252 / len(r)) - 1) * 100, 'mdd': (eq / eq.cummax() - 1).min() * 100}


def vol_est(ret, win, kind):
    if kind == 'real':
        return ret.rolling(win).std() * np.sqrt(252)
    return ret.ewm(span=win).std() * np.sqrt(252)     # EWMA(預測型)


def state_expo(c, ma, el, h, vx, bull, capbar, budget, cap, floor):
    n = len(c); below = c < el
    db = np.zeros(n, int); run = 0
    for t in range(n):
        run = run + 1 if below[t] else 0; db[t] = run
    expo = np.zeros(n); in_pos = False; last_base = 0.0
    for t in range(n):
        credit_off = h[t] <= 0; reclaim = (not np.isnan(ma[t])) and c[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        cap_eff = (capbar[t] if bull[t] else cap)
        if not in_pos:
            if reclaim and not credit_off: in_pos = True
        else:
            if credit_off: in_pos = False
            elif below[t] and not (panic and db[t] < C.PANIC_DELAY): in_pos = False
        if in_pos and reclaim:
            sd = (c[t] - el[t]) / c[t]; last_base = min(budget / max(sd, floor), cap_eff); expo[t] = last_base * h[t]
        elif in_pos: expo[t] = last_base * h[t]
    return pd.Series(expo, index=range(n))


def build(df, ret, cap, win, kind, cred=None):
    v = vol_est(ret, win, kind); med = v.rolling(252).median()
    scale = (med / v)
    capbar = cap * scale
    if cred is not None:
        capbar = capbar * cred
    capbar = capbar.clip(cap * LO, cap * HI).reindex(df.index).values
    capbar = np.where(np.isnan(capbar), cap, capbar)
    return capbar


def analyze(tk, close, health, vix, cred_str):
    p = C.PARAMS.get(tk, C.DEFAULT_PARAM); budget, cap, buf = p['budget'], p['cap'], p['exit_buf']
    ma200 = close.rolling(C.MA).mean(); el = ma200 * buf; ret = close.pct_change()
    bull = (ma200 > ma200.shift(20)) & (health >= 1.0)
    # 信用cushion因子:信用距離越大→加碼,越薄→縮(中位為中心)
    cmed = cred_str.rolling(252).median()
    cred_factor = (0.8 + (cred_str - cmed) * 6).clip(0.8, 1.25)
    df = pd.concat([close.rename('c'), ma200.rename('ma'), el.rename('el'), health.rename('h'),
                    vix.rename('v'), ret.rename('r'), bull.rename('bull')], axis=1).dropna()
    a = (df['c'].values, df['ma'].values, df['el'].values, df['h'].values, df['v'].values, df['bull'].values)
    def run_cap(capbar): return (state_expo(*a, capbar, budget, cap, C.STOP_DIST_FLOOR).set_axis(df.index).shift(1) * df['r']).dropna()
    variants = {
        'BASE': np.where(df['bull'].values, cap, cap),
        'REAL20': build(df, ret, cap, 20, 'real'),
        'EWMA20': build(df, ret, cap, 20, 'ewma'),
        'EWMA40': build(df, ret, cap, 40, 'ewma'),
        'EWMA20+信用': build(df, ret, cap, 20, 'ewma', cred_factor),
    }
    print("=" * 82)
    print(f"  {tk}")
    print("=" * 82)
    print(f"  {'變體':14}{'Sharpe':>9}{'CAGR':>9}{'MDD':>9}   判定(vs BASE)")
    base_sh = None
    for name, capbar in variants.items():
        st = perf(run_cap(capbar))
        if name == 'BASE': base_sh = st['sharpe']
        v = ''
        if name != 'BASE':
            d = st['sharpe'] - base_sh
            v = f"{'✅' if d > 0.005 else '·'} {d:+.3f}"
        print(f"  {name:14}{st['sharpe']:>9.3f}{st['cagr']:>8.1f}%{st['mdd']:>8.1f}%   {v}")
    # 穩健:realized vs ewma 各窗
    print(f"  穩健(勝過BASE的窗數,窗∈10/20/40/60):", end='')
    for kind in ['real', 'ewma']:
        w = sum(1 for ww in [10, 20, 40, 60] if perf(run_cap(build(df, ret, cap, ww, kind)))['sharpe'] > base_sh + 0.005)
        print(f"  {kind}:{w}/4", end='')
    print("\n")


def main():
    print("📥 下載 ...")
    closes = {t: dl(t) for t in TICKERS}
    hyg, lqd, vix = dl('HYG'), dl('LQD'), dl('^VIX')
    hln = lambda s: (s >= s.rolling(C.MA).mean()).astype(float)
    hyg_d = hyg / hyg.rolling(C.MA).mean() - 1
    lqd_d = lqd / lqd.rolling(C.MA).mean() - 1
    for t in TICKERS:
        idx = closes[t].index
        health = pd.concat([hln(hyg).reindex(idx).ffill(), hln(lqd).reindex(idx).ffill()], axis=1).mean(axis=1)
        cred_str = pd.concat([hyg_d.reindex(idx).ffill(), lqd_d.reindex(idx).ffill()], axis=1).mean(axis=1)
        analyze(t, closes[t], health, vix.reindex(idx).ffill(), cred_str)


if __name__ == '__main__':
    main()
