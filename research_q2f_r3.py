"""research_q2f_r3.py - 驗證 R3「追高但平靜牛市(MA200上彎 且 VIX<20日均)→可小額試單」
在「別追高」日子裡分 R3 成立/不成立,各做到場測試(等拉回 vs 立即買)。美台分開。
R3 若有根據:R3 成立時「立即買」應 ≥ 等待(差 ≤0),不成立時等待較好。"""
import sys, warnings; warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8': sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_q2b_thresholds as Q, research_engine_2026q3 as E
data = Q.data
vix = data['^VIX']['Close']
for mk, tks, thr_fn, cut in [('美股', Q.US, lambda tk: E.params(tk)['thr'] - 1, 2014), ('台股', Q.TW, lambda tk: 0.12, 2018)]:
    res = {True: [], False: []}
    for tk in tks:
        s, I, on, dd = Q.series(tk)
        ma = pd.Series(I['ma'], index=s.index)
        up = (ma > ma.shift(21)).values
        v = vix.reindex(s.index).ffill(); vm = v.rolling(20).mean()
        calm = (v < vm).values
        r3 = up & calm
        ext = dd['D50'] >= thr_fn(tk)
        for flag in (True, False):
            dist = np.where(ext & (r3 == flag), dd['D50'], -1.0)
            df = Q.arrival(s, I, on, dist, thr_fn(tk)); res[flag].append(df)
    for flag in (True, False):
        df = pd.concat(res[flag]); m = Q.summarize(df)
        a, b = Q.summarize(df[df.yr < cut]), Q.summarize(df[df.yr >= cut])
        f = lambda z: f"{z['平均差%']:+.2f}%/等待勝{z['等待勝%']}%/{z['逐年']}" if z else '—'
        print(f"{mk} 追高且R3{'成立' if flag else '不成立'}: n={m['n']} 等待−立即 平均{m['平均差%']:+.2f}% 中位{m['中位差%']:+.2f}% "
              f"等待勝{m['等待勝%']}% 逐年等待較好{m['逐年']} | 前半 {f(a)} | 後半 {f(b)}")
