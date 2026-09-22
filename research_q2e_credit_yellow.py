"""research_q2e_credit_yellow.py - 「信用部分示警期間才變黃」用 live 真正的信用哨重測(美台分開)
live:美股哨=HYG+LQD(健康度0/0.5/1);台股哨=HYG+LQD+SOXX(0/⅓/⅔/1)。
信用打折後的 D3 re-check 等價於:0<h<1 且 離停損 > budget×h/0.5 → 空手先別進。
別追高用:美股現行各檔門檻 / 台股 12%(q2c 驗證)。只看「沒追高、原始曝險≥50%」的日子(=純信用造成的黃)。"""
import sys, warnings; warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8': sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_q2b_thresholds as Q, research_engine_2026q3 as E
data = Q.data
def health(idx, tks):
    hs = []
    for tk in tks:
        s = data[tk]['Close']; m = s.rolling(200).mean()
        ok = (s >= m).astype(float); ok[m.isna()] = np.nan; hs.append(ok.reindex(idx).ffill())
    return (sum(hs) / len(hs)).values
for mk, tks, can, cut in [('美股', Q.US, ['HYG', 'LQD'], 2016), ('台股', Q.TW, ['HYG', 'LQD', 'SOXX'], 2018)]:
    dfs = []
    for tk in tks:
        s, I, on, dd = Q.series(tk); p = E.params(tk); h = health(s.index, can)
        ext_thr = (p['thr'] - 1) if mk == '美股' else 0.12
        raw_ok = p['budget'] / np.maximum(dd['STOP'], .02) >= 0.5
        partial = (h > 0) & (h < 1)
        thr = p['budget'] * np.nan_to_num(h, nan=1) / 0.5          # 打折後的離停損門檻
        flag_dist = np.where((dd['D50'] < ext_thr) & raw_ok & partial, dd['STOP'] - thr, -1.0)
        df = Q.arrival(s, I, on, flag_dist, 0.0); dfs.append(df)
    df = pd.concat(dfs); m = Q.summarize(df)
    a, b = Q.summarize(df[df.yr < cut]), Q.summarize(df[df.yr >= cut])
    f = lambda z: f"{z['平均差%']:+.2f}%/勝{z['等待勝%']}%/{z['逐年']}" if z else '—'
    print(f"{mk} 純信用造成的黃燈: n={m['n']} 平均{m['平均差%']:+.2f}% 中位{m['中位差%']:+.2f}% 勝{m['等待勝%']}% 逐年{m['逐年']} 等不到{m['等不到%']}%"
          f" | 前半 {f(a)} | 後半 {f(b)}")
