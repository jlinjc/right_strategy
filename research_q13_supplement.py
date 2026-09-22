"""research_q13_supplement.py - Q1/Q3 補充檢查
(a) Q1 用 live RiskTarget 倉位(非二元)重跑:輪動-lock vs 輪動-monthly vs 等權
(b) live RiskTarget(本身=連續版「貼近200MA押多」)vs 同平均曝險固定槓桿"""
import sys, warnings; warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8': sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_data_2026q3 as D, research_engine_2026q3 as E
data = D.load()
print('(a) Q1 改用 RiskTarget 倉位')
for tks, tw in [(list(E.US_PARAMS), False), (['0050.TW','0052.TW','006208.TW','0051.TW'], True), (list(E.TW_PARAMS), True)]:
    idx, P = E.panel(data, tks, tw=tw); cash = E.cash_daily(data, idx) if not tw else np.zeros(len(idx)); cost = .001 if tw else .0005
    rows = []
    for mode in ['daily', 'lock', 'monthly']:
        W, _ = E.rotation_weights(idx, P, tks, mode=mode, sizing='rt')
        rows.append(E.metrics(E.run_weights(idx, P, W, tks, cash, cost), f'輪動-{mode}(RT)'))
    Wt = E.timed_single_weights(P, tks, sizing='rt') / len(tks)
    rows.append(E.metrics(E.run_weights(idx, P, Wt, tks, cash, cost), '等權(RT)'))
    print(f'  {tks} {idx[0].date()}~'); print(E.table(rows))
print('\n(b) live RiskTarget vs 同平均曝險固定槓桿(同樣只在牛市持有)')
for tk, tw in [('SPY',0),('QQQ',0),('SMH',0),('XLK',0),('SOXX',0),('0050.TW',1),('0052.TW',1),('006208.TW',1)]:
    idx, P = E.panel(data, [tk], tw=bool(tw)); cash = E.cash_daily(data, idx) if not tw else np.zeros(len(idx))
    it = P[tk]['intrend'].values.astype(float); rt = P[tk]['expo_rt'].values
    avg = rt[it > 0].mean()
    a = E.metrics(E.run_weights(idx, P, rt.reshape(-1,1), [tk], cash, .001))
    b = E.metrics(E.run_weights(idx, P, (it*avg).reshape(-1,1), [tk], cash, .001))
    print(f'  {tk:10s} 平均曝險 {avg:.2f}  RiskTarget Sharpe {a["Sharpe"]} CAGR {a["CAGR%"]} MDD {a["MDD%"]}'
          f'  | 固定{avg:.2f}x Sharpe {b["Sharpe"]} CAGR {b["CAGR%"]} MDD {b["MDD%"]}  → ΔSharpe {a["Sharpe"]-b["Sharpe"]:+.2f}')
