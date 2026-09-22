"""research_q2c_split_check.py - q2b 選出的門檻做前後半段樣本外檢查 + 美股波動標準化門檻換算成各檔%
選門檻原則:相鄰門檻一致為正的「平台區」中間值,不挑單點最佳。"""
import sys, warnings; warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8': sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_q2b_thresholds as Q
import research_engine_2026q3 as E
CASES = [('美股 別追高 D50z≥1.0', Q.US, 'D50z', 1.0, 2014), ('美股 別追高 D50z≥0.75', Q.US, 'D50z', 0.75, 2014),
         ('美股 空手先別進 STOP≥18%', Q.US, 'STOP', 0.18, 2014), ('美股 空手先別進 STOP≥15%', Q.US, 'STOP', 0.15, 2014),
         ('台股 別追高 D50≥12%', Q.TW, 'D50', 0.12, 2018), ('台股 別追高 D50≥10%', Q.TW, 'D50', 0.10, 2018),
         ('台股 空手先別進 STOP≥20%', Q.TW, 'STOP', 0.20, 2018), ('台股 空手先別進 STOP≥25%', Q.TW, 'STOP', 0.25, 2018)]
for lab, tks, key, X, cut in CASES:
    dfs = []
    for tk in tks:
        s, I, on, dd = Q.series(tk); df = Q.arrival(s, I, on, dd[key], X); df['tk'] = tk; dfs.append(df)
    df = pd.concat(dfs)
    a, b = Q.summarize(df[df.yr < cut]), Q.summarize(df[df.yr >= cut])
    f = lambda m: f"n={m['n']} 平均{m['平均差%']:+.2f}% 中位{m['中位差%']:+.2f}% 勝{m['等待勝%']}% 逐年{m['逐年']}" if m else '樣本不足'
    print(f'{lab:26s} | 前半(<{cut}) {f(a)} | 後半(≥{cut}) {f(b)}')
print('\n美股 D50z 門檻換算成各檔「離50MA %」(用近一年與全史的 50日波動中位):')
for tk in Q.US:
    s, I, on, dd = Q.series(tk)
    v = pd.Series(I['c']).pct_change().rolling(50).std().values * np.sqrt(50)
    print(f'  {tk:5s} 1.0σ = 全史中位 {np.nanmedian(v[on])*100:.1f}% / 近一年 {np.nanmedian(v[-252:])*100:.1f}% / 今天 {v[-1]*100:.1f}%'
          f'   (0.75σ 今天 {v[-1]*75:.1f}%)   現行固定門檻 +{(E.params(tk)["thr"]-1)*100:.0f}%')
