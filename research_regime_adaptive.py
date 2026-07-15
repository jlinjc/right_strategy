"""
research_regime_adaptive.py — regime 自適應積極度:牛市積極 / 熊市保守(vs 一套打到底)
========================================================================
Jason 洞察:不該一套策略回測到底。熊市(2022)保守很好;牛市過度保守=獲利被吃。
應先判 regime,再決定積極度。本測誠實驗證:牛市才開積極,能否『多賺牛市 + 不被2022套』。

★ 關鍵風險:regime 判斷會延遲。市場做頭時 MA200 還上彎→可能被判牛市→積極抱進初跌。
  所以 regime 必須夠快翻熊。用『MA200 上彎 且 信用(HYG+LQD)健康』當牛市確認(信用領先)。

Regime(PIT):BULL = MA200 較20日前上彎 且 信用健康比例 >= 1.0(兩者皆站上自己200MA);否則 BEAR。

變體(sizing 公式同;只差 regime 下的積極度):
  BASE      = 現行 live(全程保守:跌破即出〔VIX>30容忍〕、信用全壞出、cap 1.5)
  REG_HOLD  = 牛市『抱過洗損』(跌破需連續3日確認才出);熊市回 BASE 保守
  REG_LEV   = 牛市『加槓桿』(cap 1.5→2.0);熊市回 BASE
  REG_BOTH  = 牛市 抱過洗損 + 加槓桿;熊市回 BASE
比 Sharpe(risk-adjusted:若只是加槓桿→Sharpe持平=沿資本配置線,非真edge;若Sharpe升=真的regime擇時有效)。
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

TICKERS = ['SMH', 'QQQ', 'SPY']
CONFIRM = 3
SLOPE_LB = 20
BULL_CAP = 2.0        # 牛市 cap
START = '2006-01-01'
CRASH = {'COVID 2020': ('2020-02-15', '2020-09-01'), '2022 熊': ('2022-01-01', '2023-01-31'),
         '2018Q4': ('2018-09-15', '2019-04-30'), '2025 關稅': ('2025-02-01', '2025-08-31'),
         '2026 春崩': ('2026-02-01', '2026-07-15')}
BULL = {'2016-17 牛': ('2016-03-01', '2018-01-31'), '2019 牛': ('2019-01-01', '2020-01-31'),
        '2020-21 牛': ('2020-05-01', '2021-12-31'), '2023-24 牛': ('2023-01-01', '2024-12-31'),
        '2025-26 牛': ('2025-05-01', '2026-07-15')}


def dl(sym):
    s = yf.download(sym, start=START, auto_adjust=True, progress=False)['Close'].dropna()
    return s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s


def perf(r):
    r = r.dropna()
    if len(r) < 20: return {}
    mu, sd = r.mean(), r.std()
    eq = (1 + r).cumprod()
    return {'sharpe': mu / sd * np.sqrt(252) if sd > 0 else np.nan,
            'cagr': (eq.iloc[-1] ** (252 / len(r)) - 1) * 100,
            'mdd': (eq / eq.cummax() - 1).min() * 100}


def run(df, budget, cap, floor, variant):
    c = df['c'].values; ma = df['ma'].values; el = df['el'].values
    h = df['h'].values; vx = df['v'].values; idx = df.index
    n = len(c)
    below = c < el
    db = np.zeros(n, int); run_ = 0
    for t in range(n):
        run_ = run_ + 1 if below[t] else 0
        db[t] = run_
    ma_up = np.concatenate([[False] * SLOPE_LB, ma[SLOPE_LB:] > ma[:-SLOPE_LB]])
    expo = np.zeros(n); bull_flag = np.zeros(n); in_pos = False; last_base = 0.0
    exits = 0
    for t in range(n):
        credit_off = h[t] <= 0
        reclaim = c[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        bull = ma_up[t] and h[t] >= 1.0
        bull_flag[t] = 1.0 if bull else 0.0
        cap_eff = cap
        if bull and variant in ('REG_LEV', 'REG_BOTH'):
            cap_eff = BULL_CAP
        if not in_pos:
            if reclaim and not credit_off:
                in_pos = True
        else:
            if credit_off:
                in_pos = False; exits += 1
            elif below[t]:
                hold_buffer = bull and variant in ('REG_HOLD', 'REG_BOTH')
                if hold_buffer:
                    if db[t] >= CONFIRM:
                        in_pos = False; exits += 1
                else:  # 保守:VIX>30 容忍,否則跌破即出
                    if not (panic and db[t] < C.PANIC_DELAY):
                        in_pos = False; exits += 1
        if in_pos:
            if reclaim:
                sd = (c[t] - el[t]) / c[t]
                last_base = min(budget / max(sd, floor), cap_eff)
                expo[t] = last_base * h[t]
            else:
                expo[t] = last_base * h[t]
    return pd.Series(expo, index=idx), exits, pd.Series(bull_flag, index=idx)


def analyze(tk, close, health, vix):
    p = C.PARAMS.get(tk, C.DEFAULT_PARAM)
    budget, cap, buf = p['budget'], p['cap'], p['exit_buf']
    ma200 = close.rolling(C.MA).mean(); el = ma200 * buf; ret = close.pct_change()
    df = pd.concat([close.rename('c'), ma200.rename('ma'), el.rename('el'),
                    health.rename('h'), vix.rename('v'), ret.rename('r')], axis=1).dropna()
    V = {}
    for v in ['BASE', 'REG_HOLD', 'REG_LEV', 'REG_BOTH']:
        expo, exits, bull = run(df, budget, cap, C.STOP_DIST_FLOOR, v)
        V[v] = {'ret': (expo.shift(1) * df['r']).dropna(), 'expo': expo, 'exits': exits, 'bull': bull}
    bh = df['r']

    print("=" * 96)
    print(f"  {tk}   {df.index[0].date()}→{df.index[-1].date()}")
    bull_share = V['BASE']['bull'].mean() * 100
    bull_2022 = V['BASE']['bull'].loc['2022-01-01':'2022-12-31'].mean() * 100
    print(f"  regime: 全期 {bull_share:.0f}% 判為牛市 | 2022 只有 {bull_2022:.0f}% 判為牛市(越低=越快翻熊=越安全)")
    print("=" * 96)
    print(f"  {'變體':10}{'Sharpe':>8}{'CAGR':>9}{'MDD':>9}{'平均曝險':>10}{'出場':>7}")
    b = None
    for v in ['BASE', 'REG_HOLD', 'REG_LEV', 'REG_BOTH']:
        st = perf(V[v]['ret'])
        if v == 'BASE': b = st
        tag = f"  ΔSh {st['sharpe']-b['sharpe']:+.3f}" if v != 'BASE' else ''
        print(f"  {v:10}{st['sharpe']:>8.3f}{st['cagr']:>8.1f}%{st['mdd']:>8.1f}%"
              f"{V[v]['expo'].mean()*100:>9.0f}%{V[v]['exits']:>7d}{tag}")
    bhp = perf(bh); print(f"  {'買持':10}{bhp['sharpe']:>8.3f}{bhp['cagr']:>8.1f}%{bhp['mdd']:>8.1f}%")

    print(f"\n  [牛市窗報酬%](問:積極變體有沒有多賺回被吃的利潤?)")
    print(f"  {'窗':14}{'BASE':>9}{'HOLD':>9}{'LEV':>9}{'BOTH':>9}{'買持':>9}")
    for name, (a, z) in BULL.items():
        row = [((1 + V[v]['ret'].loc[a:z]).prod() - 1) * 100 for v in ['BASE', 'REG_HOLD', 'REG_LEV', 'REG_BOTH']]
        bw = ((1 + bh.loc[a:z]).prod() - 1) * 100
        print(f"  {name:14}" + "".join(f"{x:>8.1f}%" for x in row) + f"{bw:>8.1f}%")

    print(f"\n  [崩盤窗報酬%](問:牛市積極有沒有害到熊市保護?)")
    print(f"  {'窗':14}{'BASE':>9}{'HOLD':>9}{'LEV':>9}{'BOTH':>9}{'買持':>9}")
    for name, (a, z) in CRASH.items():
        seg = [V[v]['ret'].loc[a:z] for v in ['BASE', 'REG_HOLD', 'REG_LEV', 'REG_BOTH']]
        if all(len(s) == 0 for s in seg): continue
        row = [((1 + s).prod() - 1) * 100 if len(s) else np.nan for s in seg]
        bw = ((1 + bh.loc[a:z]).prod() - 1) * 100 if len(bh.loc[a:z]) else np.nan
        print(f"  {name:14}" + "".join(f"{x:>8.1f}%" if not np.isnan(x) else f"{'—':>9}" for x in row)
              + (f"{bw:>8.1f}%" if not np.isnan(bw) else f"{'—':>9}"))
    print()


def main():
    print("📥 下載 SMH/QQQ/SPY + HYG/LQD + ^VIX ...")
    closes = {tk: dl(tk) for tk in TICKERS}
    hyg, lqd, vix = dl('HYG'), dl('LQD'), dl('^VIX')
    hln = lambda s: (s >= s.rolling(C.MA).mean()).astype(float)
    for tk in TICKERS:
        idx = closes[tk].index
        health = pd.concat([hln(hyg).reindex(idx).ffill(), hln(lqd).reindex(idx).ffill()], axis=1).mean(axis=1)
        analyze(tk, closes[tk], health, vix.reindex(idx).ffill())


if __name__ == '__main__':
    main()
