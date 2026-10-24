"""research_b11_us_layers.py - 美股濾網逐層拆解:每加一層,實際多了什麼(等權 5 檔,2002-2026)
  L0 不擇時(買進持有,等權)
  L1 + 200MA 擇時
  L2 + 信用哨(HYG+LQD 平滑)
  L3 + 信用哨(皆壞才清倉)
並列出重大危機年份逐年報酬,看信用哨在哪幾次真的有用。"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_b1_credit_canary as C1

P = R.build_panel('US5')
nore = np.zeros(P.n, dtype=bool)
IDX = R.sb_indices(P.n, B=2000, mean_block=63, seed=0)
allin = np.arange(P.k)[None, :].repeat(P.n, 0)
L = {}
L['L0 不擇時(等權買持)'] = R.run_plan(P, R.Plan(S=allin, E=np.ones((P.n, P.k)), reb=R.reb_days(P.idx, 0))).r
ew = R.ew_plan(P, reb=nore)
L['L1 + 200MA 擇時'] = R.run_plan(P, ew).r
for mode, lab in (('smooth', 'L2 + 信用哨(平滑,現行)'), ('binary', 'L3 + 信用哨(皆壞才清倉)')):
    h = C1.health(P.idx, ['HYG', 'LQD'], mode)[:, None]
    L[lab] = R.run_plan(P, R.Plan(S=ew.S, E=ew.E * h, reb=nore)).r
rows, prev = [], None
for k, r in L.items():
    m = R.metrics(r, P.rf)
    row = {'層': k, 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1), 'Sharpe': round(m['ShEx'], 2),
           'MDD%': round(m['MDD'], 1)}
    if prev is not None:
        bs = R.boot_delta_sharpe(r, L[prev], P.rf, IDX)
        row['比上一層 ΔSharpe'] = f"{bs['d']:+.3f} (p={bs['p']:.3f})"
    rows.append(row)
    if not k.startswith('L3'): prev = k
print(f'美股 5 檔等權 {P.idx[0].date()}→{P.idx[-1].date()}')
print(pd.DataFrame(rows).to_string(index=False))
yr = pd.DataFrame({k: (r.groupby(P.idx.year).apply(lambda x: (1 + x).prod() - 1) * 100) for k, r in L.items()}).round(1)
print('\n危機年份(%):'); print(yr.loc[[y for y in (2002, 2008, 2011, 2015, 2018, 2020, 2022) if y in yr.index]].to_string())
