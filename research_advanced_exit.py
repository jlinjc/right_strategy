"""
research_advanced_exit.py — 比 D1/D2 更進階的出場:環境條件化的『跌破確認』
========================================================================
D2(所有跌破都延遲3日確認)失敗=太笨:真跌時多抱一段虧更多。更貼切的想法(承 VOLFULL
的教訓:連續看環境 > 二元一刀切):跌破 200MA 時,先問『這個破是假摔還是真壞?』——
  趨勢還健康(MA200上彎 / 信用滿血 / 波動低)→ 大概率假摔 → 給確認(抱過洗損);
  環境在惡化(MA200轉平/下彎 / 信用轉弱 / 波動飆)→ 真壞 → 立刻砍(保護)。
把『確認 vs 立刻砍』變成環境的函數,而不是固定天數。

隔離出場效果:sizing 全用原版 RiskTarget(cap1.5,不加牛市槓桿)。誠實尺 shift(1),全期。
  BASE   跌破即出(VIX>30 才容忍3日)= 現行
  E1_slope  跌破時 MA200 上彎→給3日確認;走平/下彎→立刻砍
  E2_vol    跌破時 波動<中位→給3日確認;波動>中位→立刻砍
  E3_credit 跌破時 信用滿血→給3日確認;信用<滿→立刻砍
  E4_depth  跌破淺(<2%)→給3日確認;跌破深(>2%)→立刻砍
  E5_combo  上四者的『健康』票數≥3 才給確認,否則立刻砍(多數決)
若某招 Sharpe > BASE 且 MDD 不惡化 → 找到更進階出場;否則=MA200出場已是局部最優。
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

TICKERS = ['SMH', 'QQQ', 'SPY']
START = '2006-01-01'
CONFIRM = 3
CRASH = {'COVID2020': ('2020-02-15', '2020-09-01'), '2022熊': ('2022-01-01', '2023-01-31'),
         '2018Q4': ('2018-09-15', '2019-04-30'), '2025關稅': ('2025-02-01', '2025-08-31')}


def dl(s):
    x = yf.download(s, start=START, auto_adjust=True, progress=False)['Close'].dropna()
    return x.iloc[:, 0] if isinstance(x, pd.DataFrame) else x


def perf(r):
    r = r.dropna(); sd = r.std(); eq = (1 + r).cumprod()
    return {'sharpe': r.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
            'cagr': (eq.iloc[-1] ** (252 / len(r)) - 1) * 100, 'mdd': (eq / eq.cummax() - 1).min() * 100}


def run(d, budget, cap, floor, mode):
    c = d['c'].values; ma = d['ma'].values; el = d['el'].values; h = d['h'].values
    vx = d['v'].values; slope = d['slp'].values; rv = d['rv'].values; mv = d['mv'].values
    n = len(c); below = c < el
    db = np.zeros(n, int); run_ = 0
    for t in range(n):
        run_ = run_ + 1 if below[t] else 0; db[t] = run_
    expo = np.zeros(n); in_pos = False; last_base = 0.0; exits = 0
    for t in range(n):
        credit_off = h[t] <= 0; reclaim = (not np.isnan(ma[t])) and c[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        if not in_pos:
            if reclaim and not credit_off: in_pos = True
        else:
            if credit_off:
                in_pos = False; exits += 1
            elif below[t]:
                # 決定:給確認(healthy=True)還是立刻砍
                if mode == 'BASE':
                    healthy = panic and db[t] < CONFIRM      # 現行:只有恐慌容忍
                    give = healthy
                else:
                    if mode == 'E1_slope':   ok = slope[t] > 0
                    elif mode == 'E2_vol':   ok = (not np.isnan(rv[t]) and not np.isnan(mv[t]) and rv[t] < mv[t])
                    elif mode == 'E3_credit':ok = h[t] >= 1.0
                    elif mode == 'E4_depth': ok = c[t] > el[t] * 0.98
                    elif mode == 'E5_combo':
                        votes = int(slope[t] > 0) + int(not np.isnan(rv[t]) and not np.isnan(mv[t]) and rv[t] < mv[t]) \
                                + int(h[t] >= 1.0) + int(c[t] > el[t] * 0.98)
                        ok = votes >= 3
                    else: ok = False
                    give = (ok or panic) and db[t] < CONFIRM   # 環境健康或恐慌→給確認,但仍上限CONFIRM日
                if not give:
                    in_pos = False; exits += 1
        if in_pos and reclaim:
            sd = (c[t] - el[t]) / c[t]; last_base = min(budget / max(sd, floor), cap); expo[t] = last_base * h[t]
        elif in_pos:
            expo[t] = last_base * h[t]
    return pd.Series(expo, index=d.index), exits


def analyze(tk, close, health, vix):
    p = C.PARAMS.get(tk, C.DEFAULT_PARAM); budget, cap, buf = p['budget'], p['cap'], p['exit_buf']
    ma200 = close.rolling(C.MA).mean(); el = ma200 * buf; ret = close.pct_change()
    rv = ret.rolling(20).std() * np.sqrt(252); mv = rv.rolling(252).median()
    d = pd.concat([close.rename('c'), ma200.rename('ma'), el.rename('el'), health.rename('h'),
                   vix.rename('v'), ret.rename('r'), (ma200 - ma200.shift(20)).rename('slp'),
                   rv.rename('rv'), mv.rename('mv')], axis=1).dropna()
    print("=" * 86)
    print(f"  {tk}   {d.index[0].date()}→{d.index[-1].date()}")
    print("=" * 86)
    print(f"  {'出場法':11}{'Sharpe':>9}{'CAGR':>9}{'MDD':>9}{'出場數':>8}   判定")
    res = {}; base = None
    for m in ['BASE', 'E1_slope', 'E2_vol', 'E3_credit', 'E4_depth', 'E5_combo']:
        ex, exits = run(d, budget, cap, C.STOP_DIST_FLOOR, m)
        r = (ex.shift(1) * d['r']).dropna(); st = perf(r); res[m] = (st, r)
        if m == 'BASE': base = st
        v = ''
        if m != 'BASE':
            v = '✅Sharpe↑且回撤不惡化' if (st['sharpe'] > base['sharpe'] + 0.005 and st['mdd'] >= base['mdd'] - 1.0) \
                else ('🟡Sharpe↑但回撤惡化' if st['sharpe'] > base['sharpe'] + 0.005 else '·')
        print(f"  {m:11}{st['sharpe']:>9.3f}{st['cagr']:>8.1f}%{st['mdd']:>8.1f}%{exits:>8d}   {v}")
    print(f"\n  [崩盤窗報酬%]  {'':2}" + "".join(f"{m.split('_')[0]:>9}" for m in ['BASE', 'E1', 'E2', 'E3', 'E4', 'E5']))
    for nm, (a, b) in CRASH.items():
        cells = [((1 + res[m][1].loc[a:b]).prod() - 1) * 100 for m in ['BASE', 'E1_slope', 'E2_vol', 'E3_credit', 'E4_depth', 'E5_combo']]
        if all(np.isnan(x) for x in cells): continue
        print(f"  {nm:12}" + "".join(f"{x:>8.1f}%" for x in cells))
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
