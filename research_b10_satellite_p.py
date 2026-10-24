"""research_b10_satellite_p.py - 正2(00631L)衛星 20%/40% vs 0% 的配對 bootstrap 檢定(含信用哨、每月拉回目標比例)"""
import sys; sys.path.insert(0, r'C:\Users\Jason\right_strategy'); sys.stdout.reconfigure(encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, research_rot_engine as R, research_q4_choices as Q4, research_b1_credit_canary as C1
Q4.ensure(['00631L.TW'])
P = R.build_panel('TW4', tickers=['0050.TW', '0052.TW', '006208.TW', '0051.TW', '00631L.TW'])
h = C1.health(P.idx, ['HYG', 'LQD', 'SOXX'], 'smooth')[:, None]
j = P.col('00631L.TW'); c50 = P.col('0050.TW'); core = [P.col(t) for t in ['0050.TW', '0052.TW', '006208.TW', '0051.TW']]
n = P.n; nore = R.reb_days(P.idx, 0)   # 衛星比例固定 → 每月拉回目標(不再平衡會讓正2那份越長越大)
S = np.full((n, 5), -1); E0 = np.zeros((n, 5))
for i, c in enumerate(core):
    S[:, i] = np.where(P.intrend[:, c], c, -1); E0[:, i] = np.where(P.intrend[:, c], 1.0, 0.0)
S[:, 4] = np.where(P.intrend[:, c50], j, -1)
IDX = R.sb_indices(n, B=2000, mean_block=63, seed=0)
res = {}
for sat in (0.0, 0.2, 0.4):
    E = E0 * (1 - sat); E[:, 4] = np.where(P.intrend[:, c50], 1.0, 0.0) * sat * 5   # 5 槽位 → 乘 5 還原
    res[sat] = R.run_plan(P, R.Plan(S=S, E=E * h, reb=nore)).r
for sat in (0.2, 0.4):
    bs = R.boot_delta_sharpe(res[sat], res[0.0], P.rf, IDX)
    m = R.metrics(res[sat], P.rf); m0 = R.metrics(res[0.0], P.rf)
    print(f"正2衛星{int(sat*100)}% − 0%: ΔSharpe {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}] p={bs['p']:.3f} | "
          f"CAGR {m['CAGR']:.1f}% vs {m0['CAGR']:.1f}% | 回撤 {m['MDD']:.1f}% vs {m0['MDD']:.1f}%")
