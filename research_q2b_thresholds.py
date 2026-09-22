"""research_q2b_thresholds.py - 「多遠算太遠」門檻掃描(美股、台股分開)
========================================================================
兩條黃燈的距離都掃一整排門檻,每個門檻做「到場測試」:
  某天在牛市(收盤 ≥ 200MA)且距離 ≥ X →
    A 當天收盤買,抱到系統出場(跌破 200MA×buf)或最多 252 日
    B 等到距離第一次 < X 才買,抱到同一終點;等不到就一直現金
  指標:B−A 對數報酬差(平均/中位)、等待勝率、逐年等待較好的年數(樣本重疊→看逐年一致性)、
        A/B 持有期間最大回撤均值(風險面)、等不到的比例。
距離定義:
  D50  = 收盤/50MA − 1(%)                     ← 別追高
  D50z = D50 ÷ (50日波動×√50)(波動標準化,檢驗「一條規則通用各檔」)
  STOP = (收盤 − 200MA×buf)/收盤(%)            ← 空手先別進(live:曝險=budget/STOP<50%)
美股、台股分開彙總(股性不同,不合併)。
用法: python research_q2b_thresholds.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_data_2026q3 as D
import research_engine_2026q3 as E

data = D.load()
H = 252
US = list(E.US_PARAMS)
TW = list(E.TW_PARAMS)


def series(tk):
    s = data[tk]['Close'].dropna()
    tw = tk.endswith('.TW')
    if tw:
        import taiwan_status as T
        s = T.clean(s)
    p = E.params(tk)
    I = E.indicators(s, p)
    c = I['c']
    d50 = c / I['ma50'] - 1
    vol50 = pd.Series(c).pct_change().rolling(50).std().values * np.sqrt(50)
    d50z = d50 / vol50
    stop = I['stopd']
    on = (c >= I['ma']) & ~np.isnan(I['ma'])
    return s, I, on, {'D50': d50, 'D50z': d50z, 'STOP': stop}


def arrival(s, I, on, dist, X):
    c = I['c']; n = len(c); lc = np.log(c); it = I['intrend']
    nxt_exit = np.full(n, n - 1); ne = n - 1
    for t in range(n - 1, -1, -1):
        if not it[t]: ne = t
        nxt_exit[t] = ne
    below = on & (dist < X)
    nxt_ok = np.full(n, -1); nb = -1
    for t in range(n - 1, -1, -1):
        nxt_ok[t] = nb
        if below[t]: nb = t
    # 路徑最大回撤(進場後到終點,相對進場後高點)
    rows = []
    flag = on & (dist >= X) & ~np.isnan(dist)
    for t in np.where(flag)[0]:
        end = min(nxt_exit[t], t + H, n - 1)
        if end <= t + 5: continue
        A = lc[end] - lc[t]
        pa = c[t:end + 1]; mddA = (pa / np.maximum.accumulate(pa) - 1).min()
        g = nxt_ok[t]
        if g != -1 and g < end:
            B = lc[end] - lc[g]; pb = c[g:end + 1]; mddB = (pb / np.maximum.accumulate(pb) - 1).min(); miss = 0
        else:
            B, mddB, miss = 0.0, 0.0, 1
        rows.append((s.index[t].year, A, B, mddA, mddB, miss))
    return pd.DataFrame(rows, columns=['yr', 'A', 'B', 'mddA', 'mddB', 'miss'])


def summarize(df):
    if len(df) < 30: return None
    d = df['B'] - df['A']
    yr = df.assign(d=d).groupby('yr')['d'].mean()
    return {'n': len(df), '平均差%': round(d.mean() * 100, 2), '中位差%': round(d.median() * 100, 2),
            '等待勝%': round((d > 0).mean() * 100), '逐年': f'{int((yr > 0).sum())}/{len(yr)}',
            '立即買回撤%': round(df['mddA'].mean() * 100, 1), '等待回撤%': round(df['mddB'].mean() * 100, 1),
            '等不到%': round(df['miss'].mean() * 100)}


def scan(tks, key, grid, label):
    print(f'\n{"=" * 110}\n{label}  距離={key}\n{"=" * 110}')
    cache = {tk: series(tk) for tk in tks}
    # 各檔現行門檻
    rows = []
    for X in grid:
        pooled = []
        per = {}
        for tk in tks:
            s, I, on, dd = cache[tk]
            df = arrival(s, I, on, dd[key], X)
            pooled.append(df)
            sm = summarize(df)
            per[tk] = sm['平均差%'] if sm else None
        sm = summarize(pd.concat(pooled))
        if sm:
            rows.append({'門檻': X, **sm, **{f'{tk.replace(".TW","")}': v for tk, v in per.items()}})
    print(pd.DataFrame(rows).to_string(index=False))


def base_rates(tks, label):
    """現況:各檔距離分佈(牛市日)讓門檻有感——多常碰到"""
    print(f'\n[{label} 牛市日距離分佈(百分位)]')
    for tk in tks:
        s, I, on, dd = series(tk)
        q50 = np.nanpercentile(dd['D50'][on], [50, 75, 90, 95]) * 100
        qs = np.nanpercentile(dd['STOP'][on], [50, 75, 90, 95]) * 100
        p = E.params(tk)
        print(f'  {tk:10s} 離50MA P50/75/90/95 = {q50.round(1)}  現行門檻 +{(p["thr"]-1)*100:.0f}%  |  '
              f'離停損 P50/75/90/95 = {qs.round(1)}  現行D3門檻 {p["budget"]/0.5*100:.1f}%')


if __name__ == '__main__':
    base_rates(US, '美股'); base_rates(TW, '台股')
    g50 = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.12, 0.15]
    gz = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]
    gs = [0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40]
    scan(US, 'D50', g50, '美股 別追高(離50MA%)')
    scan(TW, 'D50', g50, '台股 別追高(離50MA%)')
    scan(US, 'D50z', gz, '美股 別追高(波動標準化)')
    scan(TW, 'D50z', gz, '台股 別追高(波動標準化)')
    scan(US, 'STOP', gs, '美股 空手先別進(離停損線%)')
    scan(TW, 'STOP', gs, '台股 空手先別進(離停損線%)')
