"""research_q3_bull_dip_leverage.py - 問題3:牛市裡跌深上槓桿(2x)、漲回來換回1倍,有沒有 edge?
========================================================================
規則族(全部只在「牛市」=live 200MA 遲滯在場時動作;跌破→全現金,與現行系統同):
  觸發 2x(三種定義 × 各自門檻):
    DD   : 距 N 日高點回撤 ≥ X%          (N=63, X ∈ 5,7,10,12,15,20)
    MA200: 距 200MA ≤ Y%(貼近長均線)    (Y ∈ 2,4,6,8,10)
    MA50 : 收盤 < 50MA×(1−Z%)           (Z ∈ 0,2,4,6)
  回到 1x(三種):
    HIGH : 回撤收回到 < X/3(接近前高)
    MA50 : 收盤重新站上 50MA
    TIME : 固定持有 T 日(T ∈ 21,63)
對照組(同期、同成本):
  1x 擇時(現行二元版) / 2x 常駐擇時 / ★同平均曝險的固定槓桿(判斷「挑時機」本身有沒有價值)/
  live RiskTarget(本來就是「貼近200MA押多」的連續版,cap 1.5)/ 買持
槓桿成本:2x 部分 = 付現金利率 + 1%/年(≈ 槓桿ETF 內扣+借款);換手 0.10%(美) / 0.20%(台)。
合成 2x 用母指數日報酬×2(每日再平衡=和真的槓桿ETF同樣有波動耗損),並與真實 QLD/SSO/00631L 對帳。
用法: python research_q3_bull_dip_leverage.py
"""
import sys, warnings, itertools
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_data_2026q3 as D
import research_engine_2026q3 as E

data = D.load()
WINDOWS = [('FULL', None, None), ('~2009', None, '2009-12-31'), ('2010-16', '2010-01-01', '2016-12-31'),
           ('2017-26', '2017-01-01', None)]


def sub(r, a, b):
    if a: r = r[r.index >= pd.Timestamp(a)]
    if b: r = r[r.index <= pd.Timestamp(b)]
    return r


def base(tk, tw=False):
    s = data[tk]['Close'].dropna()
    if tw:
        import taiwan_status as T
        s = T.clean(s)
    I = E.indicators(s, E.params(tk))
    df = pd.DataFrame(I, index=s.index)
    df['ret'] = s.pct_change()
    df = df[~np.isnan(df['ma'])]
    return s.reindex(df.index), df


def sim(df, expo, cash, cost):
    P = {'x': df}
    return E.run_weights(df.index, P, expo.reshape(-1, 1), ['x'], cash, cost)


def dip_expo(s, df, trig, x, back, T=63):
    c = s.values; n = len(c)
    it = df['intrend'].values; ma = df['ma'].values; ma50 = df['ma50'].values
    hi63 = s.rolling(63, min_periods=1).max().values
    dd = c / hi63 - 1
    # ★重新上膛(防止在門檻附近天天來回切換被成本吃光——初版一年切 116 次):
    #   一段 2x 結束後,必須先「離開觸發區」一次才准再觸發。
    e = np.zeros(n); lev = False; t0 = 0; armed = True
    for t in range(n):
        if not it[t]:
            lev = False; e[t] = 0; armed = True; continue
        d200 = c[t] / ma[t] - 1
        if trig == 'DD':      fire, reset = dd[t] <= -x, dd[t] > -x / 3
        elif trig == 'MA200': fire, reset = d200 <= x, d200 > x + 0.03
        else:                 fire, reset = c[t] < ma50[t] * (1 - x), c[t] > ma50[t] * 1.01
        if not lev:
            if reset: armed = True
            if fire and armed: lev, t0, armed = True, t, False
        else:
            if back == 'HIGH':
                ref = x / 3 if trig == 'DD' else 0.03
                done = dd[t] > -ref
            elif back == 'MA50':
                done = c[t] > ma50[t]
            else:
                done = (t - t0) >= T
            if done: lev = False
        e[t] = 2.0 if lev else 1.0
    return e


def run(tk, tw=False, lev_real=None):
    s, df = base(tk, tw)
    cash = E.cash_daily(data, df.index) if not tw else np.zeros(len(df))
    cost = 0.002 if tw else 0.001
    it = df['intrend'].values.astype(float)
    res = {}
    res['買持1x'] = (df['ret'].fillna(0), 1.0)
    res['擇時1x(現行二元)'] = (sim(df, it, cash, cost), it[it > 0].mean())
    res['擇時2x常駐'] = (sim(df, it * 2, cash, cost), 2.0)
    rt = df['expo_rt'].values
    res['live RiskTarget(cap1.5)'] = (sim(df, rt, cash, cost), rt[it > 0].mean())
    grid = []
    for trig, xs in [('DD', [0.05, 0.07, 0.10, 0.12, 0.15, 0.20]), ('MA200', [0.02, 0.04, 0.06, 0.08, 0.10]),
                     ('MA50', [0.0, 0.02, 0.04, 0.06])]:
        for x in xs:
            for back, T in [('HIGH', 0), ('MA50', 0), ('TIME', 21), ('TIME', 63)]:
                e = dip_expo(s, df, trig, x, back, T)
                r = sim(df, e, cash, cost)
                avg = e[it > 0].mean()
                # 同平均曝險的固定槓桿(牛市中每天都用 avg 倍)
                rc = sim(df, it * avg, cash, cost)
                name = f'{trig}{x*100:.0f}%/{back}{T or ""}'
                grid.append((name, r, rc, avg, (e == 2).sum() / max((it > 0).sum(), 1),
                             (np.abs(np.diff(e)) == 1).sum() / (len(e) / 252)))
    return s, df, res, grid


def show(tk, tw=False):
    s, df, res, grid = run(tk, tw)
    print(f'\n{"=" * 110}\n{tk}  {df.index[0].date()} → {df.index[-1].date()}  ({len(df)/252:.1f} 年)\n{"=" * 110}')
    # 網格:每格跟「同平均曝險固定槓桿」比
    rows = []
    for name, r, rc, avg, frac2, swy in grid:
        m, mc = E.metrics(r), E.metrics(rc)
        win_w = []
        for wn, a, b in WINDOWS[1:]:
            ra, rca = sub(r, a, b), sub(rc, a, b)
            if len(ra) > 252:
                win_w.append(E.metrics(ra)['Sharpe'] > E.metrics(rca)['Sharpe'])
        rows.append({'規則': name, '2x時間%': round(frac2 * 100), '切換/年': round(swy, 1), '平均曝險': round(avg, 2),
                     'CAGR%': m['CAGR%'], 'Sharpe': m['Sharpe'], 'MDD%': m['MDD%'],
                     '同曝險固定槓桿 Sharpe': mc['Sharpe'], '同曝險 CAGR%': mc['CAGR%'], '同曝險 MDD%': mc['MDD%'],
                     'ΔSharpe': round(m['Sharpe'] - mc['Sharpe'], 2), '分段贏': f'{sum(win_w)}/{len(win_w)}'})
    g = pd.DataFrame(rows)
    print(f'參照組:')
    print(E.table([E.metrics(r, k) | {'牛市平均曝險': round(a, 2)} for k, (r, a) in res.items()]))
    print(f'\n網格 {len(g)} 格:ΔSharpe(跌深2x − 同平均曝險固定槓桿)>0 的格子 {int((g["ΔSharpe"] > 0).sum())}/{len(g)},'
          f' 中位 ΔSharpe {g["ΔSharpe"].median():+.2f};三段都贏的格子 {int((g["分段贏"].str.startswith(str(len(WINDOWS)-1))).sum())}')
    for trig in ['DD', 'MA200', 'MA50']:
        gg = g[g['規則'].str.startswith(trig)]
        print(f'   {trig:6s}: 贏 {int((gg["ΔSharpe"] > 0).sum())}/{len(gg)}  中位ΔSharpe {gg["ΔSharpe"].median():+.2f}')
    print(g.sort_values('ΔSharpe', ascending=False).head(8).to_string(index=False))
    print('  …最差 4 格:'); print(g.sort_values('ΔSharpe').head(4).to_string(index=False))
    return g, res


def event_study(tk, tw=False):
    """牛市中『跌深』之後的前瞻報酬/波動 vs 一般牛市日——跌深後到底有沒有比較好賺?"""
    s, df = base(tk, tw)
    c = s.values; it = df['intrend'].values
    hi63 = s.rolling(63, min_periods=1).max().values
    dd = c / hi63 - 1
    r = df['ret'].values
    f21 = np.full(len(c), np.nan); f21[:-21] = c[21:] / c[:-21] - 1
    v21 = pd.Series(r).rolling(21).std().shift(-21).values * np.sqrt(252)
    out = [f'  {tk} 牛市日前瞻21日:']
    for lo, hi, lab in [(0, -0.03, '離高<3%'), (-0.03, -0.07, '回撤3-7%'), (-0.07, -0.12, '回撤7-12%'),
                        (-0.12, -1, '回撤>12%')]:
        m = it & (dd <= lo) & (dd > hi)
        if m.sum() < 30: continue
        out.append(f'    {lab:9s} n={int(m.sum()):5d}  均 {np.nanmean(f21[m])*100:+.2f}%  勝 {np.nanmean(f21[m] > 0)*100:.0f}%  '
                   f'之後年化波動 {np.nanmean(v21[m])*100:.0f}%  報酬/波動 {np.nanmean(f21[m])/np.nanmean(v21[m])*np.sqrt(12):.2f}')
    print('\n'.join(out))


def check_synthetic(under, real, tw=False):
    """合成2x vs 真實槓桿ETF 年化報酬對帳(確認合成成本假設不會美化結果)"""
    s = data[under]['Close'].dropna(); l = data[real]['Close'].dropna()
    if tw:
        import taiwan_status as T
        s = T.clean(s); l = T.clean(l)   # 00631L 2015-01 有假的 -95%
    idx = s.index.intersection(l.index)
    idx = idx[idx >= l.index[0] + pd.Timedelta(days=30)]
    ru = s.reindex(idx).pct_change().fillna(0); rl = l.reindex(idx).pct_change().fillna(0)
    cash = E.cash_daily(data, idx) if not tw else np.full(len(idx), 0.01 / 252)
    syn = 2 * ru - (cash + 0.01 / 252)
    yrs = len(idx) / 252
    cg = lambda r: ((1 + r).prod() ** (1 / yrs) - 1) * 100
    print(f'  對帳 {real} vs 合成2×{under} ({idx[0].date()}~):真實 CAGR {cg(rl):.1f}% / 合成 {cg(syn):.1f}%  '
          f'日報酬相關 {np.corrcoef(rl, syn)[0,1]:.3f}')


if __name__ == '__main__':
    print('Q3 牛市跌深上槓桿稽核\n')
    print('[合成2x可信度對帳]')
    check_synthetic('QQQ', 'QLD'); check_synthetic('SPY', 'SSO'); check_synthetic('0050.TW', '00631L.TW', tw=True)
    print('\n[事件研究:牛市中跌深之後是不是比較好賺?]')
    for tk in ['SPY', 'QQQ', 'SMH']:
        event_study(tk)
    event_study('0050.TW', tw=True)
    allg = {}
    for tk in ['SPY', 'QQQ', 'SMH', 'XLK']:
        allg[tk], _ = show(tk)
    allg['0050.TW'], _ = show('0050.TW', tw=True)
    print('\n' + '=' * 110 + '\n跨標的:同一條規則在幾個標的都贏「同曝險固定槓桿」?')
    names = allg['SPY']['規則']
    rows = []
    for nm in names:
        d = [float(allg[tk].loc[allg[tk]['規則'] == nm, 'ΔSharpe'].iloc[0]) for tk in allg]
        rows.append({'規則': nm, **{tk: v for tk, v in zip(allg, d)}, '贏的標的數': sum(v > 0 for v in d)})
    rr = pd.DataFrame(rows).sort_values('贏的標的數', ascending=False)
    print(f'5標的全贏的規則數 {int((rr["贏的標的數"] == 5).sum())}/{len(rr)};≥4 {int((rr["贏的標的數"] >= 4).sum())}')
    print(rr.head(12).to_string(index=False))
