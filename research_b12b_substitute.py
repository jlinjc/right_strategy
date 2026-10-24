"""research_b12b_substitute.py - 參考(不判定):三個新濾網能不能「取代」信用哨?
基礎改成只有 200MA(不含信用哨),比較 +信用哨 / +寬度 / +殖利率倒掛 / +雙動能 各自加上去的效果,
並列 2008 / 2015 / 2018 / 2020 / 2022 逐年。"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_b1_credit_canary as C1
import research_b12_us_filters as F

P = R.build_panel('US5'); nore = np.zeros(P.n, dtype=bool)
ew = R.ew_plan(P, reb=nore, sizing='rt')
IDX = R.sb_indices(P.n, B=2000, mean_block=63, seed=0)
filt = F.build_filters(P)
h = C1.health(P.idx, ['HYG', 'LQD'], 'smooth')
V = {'只有200MA': np.ones(P.n), '+信用哨(現行)': h, '+寬度<50%': filt['寬度(產業)<50%→0'],
     '+殖利率倒掛': filt['殖利率倒掛→0'], '+大盤雙動能12M': filt['大盤雙動能12M→0'][:, 0]}
res = {k: R.run_plan(P, R.Plan(S=ew.S, E=ew.E * v[:, None], reb=nore)).r for k, v in V.items()}
rows = []
for k, r in res.items():
    m = R.metrics(r, P.rf); row = {'版本': k, 'CAGR%': round(m['CAGR'], 1), 'MDD%': round(m['MDD'], 1), 'Sharpe': round(m['ShEx'], 2)}
    if k != '只有200MA':
        bs = R.boot_delta_sharpe(r, res['只有200MA'], P.rf, IDX); row['vs 只有200MA'] = f"{bs['d']:+.3f} (p={bs['p']:.3f})"
    rows.append(row)
print(pd.DataFrame(rows).to_string(index=False))
yr = pd.DataFrame({k: r.groupby(P.idx.year).apply(lambda x: (1 + x).prod() - 1) * 100 for k, r in res.items()}).round(1)
print('\n危機年份(%):'); print(yr.loc[[2008, 2015, 2018, 2020, 2022]].to_string())
