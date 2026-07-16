"""
research_smart_combine.py — 能不能做出「比原版/自適應都好」(真的拉高 Sharpe,不只沿槓桿線)?
========================================================================
Jason:能不能取兩者平衡、做出比兩個結合都好的結果?
數學上「更好」=Sharpe 真的更高=跳出資本配置線=把曝險集中在『未來風險報酬比最高』時、
崩盤前收手。regime(牛/熊)太粗(牛市末端照樣加槓桿→崩盤加深)。這裡測『波動率細分』:
  平靜牛市才重押、動盪(崩盤前兆)自動縮 —— 這是文獻(Moreira-Muir 波動率擇時)唯一
  可能真的拉高 Sharpe 的機制。誠實對照:Jason 記憶裡舊 VolTarget 測過是持平,看這次疊在
  regime+RiskTarget 上會不會不同。

變體(進出全同原版,只差牛市 cap 怎麼給):
  BASE       牛市不加碼(cap 1.5)= 原版
  LEV_2.0    牛市固定 cap 2.0        = 自適應(沿槓桿線)
  VOLUP      牛市 cap = clip(1.5×中位vol/實現vol, 1.5, 2.5)  只在『平靜牛市』加碼(不縮)
  VOLFULL    牛市 cap = clip(1.5×中位vol/實現vol, 1.0, 2.5)  平靜加碼 + 動盪牛市縮到<1.5
若 VOLUP/VOLFULL 的 Sharpe > BASE 且 > LEV_2.0 → 真的比兩者好;否則=還是同一條線。
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

TICKERS = ['SMH', 'QQQ', 'SPY']
START = '2006-01-01'
CRASH = {'COVID2020': ('2020-02-15', '2020-09-01'), '2022熊': ('2022-01-01', '2023-01-31'),
         '2025關稅': ('2025-02-01', '2025-08-31')}


def dl(s):
    x = yf.download(s, start=START, auto_adjust=True, progress=False)['Close'].dropna()
    return x.iloc[:, 0] if isinstance(x, pd.DataFrame) else x


def perf(r):
    r = r.dropna(); mu, sd = r.mean(), r.std(); eq = (1 + r).cumprod()
    return {'sharpe': mu / sd * np.sqrt(252) if sd > 0 else np.nan,
            'cagr': (eq.iloc[-1] ** (252 / len(r)) - 1) * 100,
            'mdd': (eq / eq.cummax() - 1).min() * 100}


def state_expo(c, ma, el, h, vx, bull, rv, medv, budget, cap, floor, mode):
    n = len(c); below = c < el
    db = np.zeros(n, int); run = 0
    for t in range(n):
        run = run + 1 if below[t] else 0; db[t] = run
    expo = np.zeros(n); in_pos = False; last_base = 0.0
    for t in range(n):
        credit_off = h[t] <= 0; reclaim = (not np.isnan(ma[t])) and c[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        # cap_eff by mode
        if bull[t]:
            if mode == 'BASE':
                cap_eff = cap
            elif mode == 'LEV_2.0':
                cap_eff = 2.0
            else:
                scale = (medv[t] / rv[t]) if (rv[t] and not np.isnan(rv[t]) and not np.isnan(medv[t])) else 1.0
                raw = cap * scale
                cap_eff = min(max(raw, 1.5 if mode == 'VOLUP' else 1.0), 2.5)
        else:
            cap_eff = cap
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
    return expo


def analyze(tk, close, health, vix):
    p = C.PARAMS.get(tk, C.DEFAULT_PARAM); budget, cap, buf = p['budget'], p['cap'], p['exit_buf']
    ma200 = close.rolling(C.MA).mean(); el = ma200 * buf; ret = close.pct_change()
    rv = ret.rolling(20).std() * np.sqrt(252); medv = rv.rolling(252).median()
    slope_up = ma200 > ma200.shift(20)
    df = pd.concat([close.rename('c'), ma200.rename('ma'), el.rename('el'), health.rename('h'),
                    vix.rename('v'), ret.rename('r'), rv.rename('rv'), medv.rename('mv'),
                    (slope_up & (health >= 1.0)).rename('bull')], axis=1).dropna()
    print("=" * 84)
    print(f"  {tk}   {df.index[0].date()}→{df.index[-1].date()}   牛市 {df['bull'].mean()*100:.0f}%")
    print("=" * 84)
    print(f"  {'變體':10}{'Sharpe':>9}{'CAGR':>9}{'MDD':>9}{'平均曝險':>10}   判定")
    res = {}
    base_sh = lev_sh = None
    for mode in ['BASE', 'LEV_2.0', 'VOLUP', 'VOLFULL']:
        ex = state_expo(df['c'].values, df['ma'].values, df['el'].values, df['h'].values, df['v'].values,
                        df['bull'].values, df['rv'].values, df['mv'].values, budget, cap, C.STOP_DIST_FLOOR, mode)
        r = (pd.Series(ex, index=df.index).shift(1) * df['r']).dropna()
        st = perf(r); res[mode] = (st, r)
        if mode == 'BASE': base_sh = st['sharpe']
        if mode == 'LEV_2.0': lev_sh = st['sharpe']
        verdict = ''
        if mode in ('VOLUP', 'VOLFULL'):
            better_both = st['sharpe'] > base_sh + 0.005 and st['sharpe'] > lev_sh + 0.005
            verdict = '✅比兩者都好' if better_both else '≈同一條線(沒更好)'
        print(f"  {mode:10}{st['sharpe']:>9.3f}{st['cagr']:>8.1f}%{st['mdd']:>8.1f}%"
              f"{pd.Series(ex, index=df.index).mean()*100:>9.0f}%   {verdict}")
    print(f"\n  [崩盤窗報酬%]  {'':4}" + "".join(f"{m:>10}" for m in ['BASE', 'LEV_2.0', 'VOLUP', 'VOLFULL']))
    for nm, (a, b) in CRASH.items():
        cells = [((1 + res[m][1].loc[a:b]).prod() - 1) * 100 for m in ['BASE', 'LEV_2.0', 'VOLUP', 'VOLFULL']]
        if all(np.isnan(x) for x in cells): continue
        print(f"  {nm:14}" + "".join(f"{x:>9.1f}%" for x in cells))
    print()


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
