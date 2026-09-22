"""research_q2d_incremental.py - 「空手先別進」的增量價值:只看沒被「別追高」擋下的日子
美股別追高用現行各檔門檻(q2 已驗證);台股別追高用 q2b/q2c 選出的 12%。"""
import sys, warnings; warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8': sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_q2b_thresholds as Q, research_engine_2026q3 as E
for mk, tks, cut in [('美股', Q.US, 2014), ('台股', Q.TW, 2018)]:
    for X in [0.12, 0.15, 0.18, 0.20, 0.25]:
        dfs = []
        for tk in tks:
            s, I, on, dd = Q.series(tk)
            ext_thr = (E.params(tk)['thr'] - 1) if mk == '美股' else 0.12
            not_ext = dd['D50'] < ext_thr
            # 只在「沒追高」的日子標記;等待=等到 STOP<X
            dist = np.where(not_ext, dd['STOP'], np.nan)
            df = Q.arrival(s, I, on, np.nan_to_num(dist, nan=-1), X); dfs.append(df)
        df = pd.concat(dfs); m = Q.summarize(df)
        a, b = Q.summarize(df[df.yr < cut]), Q.summarize(df[df.yr >= cut])
        f = lambda z: f"{z['平均差%']:+.2f}%/勝{z['等待勝%']}%/{z['逐年']}" if z else '—'
        print(f'{mk} 未追高且離停損≥{X*100:.0f}%: ' + (f"n={m['n']} 平均{m['平均差%']:+.2f}% 中位{m['中位差%']:+.2f}% 勝{m['等待勝%']}% 逐年{m['逐年']}"
              f"  回撤 立即{m['立即買回撤%']}% vs 等{m['等待回撤%']}% | 前半 {f(a)} | 後半 {f(b)}" if m else '樣本不足'))
