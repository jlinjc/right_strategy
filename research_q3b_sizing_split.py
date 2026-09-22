"""research_q3b_sizing_split.py - Q3 收穫的落地檢查:RiskTarget 倉位 vs 固定倉位(美台分開、前後半段)
live RiskTarget = 連續版「貼近200MA押多」。比:二元1x、同平均曝險固定倍數。"""
import sys, warnings; warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8': sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_data_2026q3 as D, research_engine_2026q3 as E
data = D.load()
for mk, tks, cut, cost in [('美股', list(E.US_PARAMS), '2014-01-01', .001), ('台股', list(E.TW_PARAMS), '2018-01-01', .002)]:
    print(f'\n== {mk}(前半 < {cut[:4]} / 後半 ≥)')
    for tk in tks:
        idx, P = E.panel(data, [tk], tw=(mk == '台股'))
        cash = E.cash_daily(data, idx) if mk == '美股' else np.zeros(len(idx))
        it = P[tk]['intrend'].values.astype(float); rt = P[tk]['expo_rt'].values; avg = rt[it > 0].mean()
        R = {'RiskTarget': E.run_weights(idx, P, rt.reshape(-1, 1), [tk], cash, cost),
             '二元1x': E.run_weights(idx, P, it.reshape(-1, 1), [tk], cash, cost),
             f'固定{avg:.2f}x': E.run_weights(idx, P, (it * avg).reshape(-1, 1), [tk], cash, cost)}
        line = f'  {tk:10s}'
        for k, r in R.items():
            a = E.metrics(r[r.index < cut]); b = E.metrics(r[r.index >= cut]); f = E.metrics(r)
            line += f' | {k}: 全{f["Sharpe"]:.2f}/{f["CAGR%"]:.1f}%/{f["MDD%"]:.0f}% 前{a.get("Sharpe", float("nan")):.2f} 後{b["Sharpe"]:.2f}'
        print(line)
