"""research_b13_satellite_fix.py - 正2(00631L)衛星重算:修正 q4/b10 的核心比例 bug,並對齊 live 規則
========================================================================
2026-10-25 v2 UX 第二輪稽核 R2-B7:
  research_q4_choices.py(②③④)與 research_b10_satellite_p.py 用 5 槽位引擎時,核心 4 槽只乘 (1-sat)、
  沒有乘 5/4 → 核心實際只放 64%(sat=20%)/48%(sat=40%);b10 的「0% 對照組」其實是 80% 核心 + 20% 現金。
修正做法:讓每個槽位曝險都 ≤1(不靠 E>1 補,否則引擎會對單一槽位計融資成本):
  0%  → 4 槽(核心各 1 槽)
  20% → 5 槽(核心 4 槽 + 正2 1 槽)
  40% → 20 槽(核心各 3 槽 + 正2 8 槽 = 60% / 40%)
  100% → 1 槽(全押正2)
live 規則對齊(v2_status.py 衛星卡):正2 持有條件 = 母股 0050 站上自己 200MA「且」正2 自己沒跌破停損線(intrend),
並且和核心一樣乘上台股信用哨(HYG+LQD+SOXX 平滑)。另列「只看 0050 閘門」版本供對照(舊研究用的接法)。
每月拉回目標比例(衛星比例固定;不拉回的話正2那份會越長越大)。
輸出 research_cache/b13_satellite_fix.txt
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import research_rot_engine as R, research_q4_choices as Q4, research_b1_credit_canary as C1

Q4.ensure(['00631L.TW'])
CORE = ['0050.TW', '0052.TW', '006208.TW', '0051.TW']
P = R.build_panel('TW4', tickers=CORE + ['00631L.TW'])
h = C1.health(P.idx, ['HYG', 'LQD', 'SOXX'], 'smooth')
j = P.col('00631L.TW'); c50 = P.col('0050.TW'); core = [P.col(t) for t in CORE]
n = P.n
REB = R.reb_days(P.idx, 0)
gate_live = P.intrend[:, c50] & P.intrend[:, j]     # live:0050 閘門 且 正2 自身未跌破停損線
gate_old = P.intrend[:, c50]                         # 舊研究:只看 0050 閘門


def plan(sat, gate, credit=True):
    """回傳 (S, E):槽位資產與曝險;每槽曝險 ≤1。"""
    if sat == 0:
        reps_core, reps_sat = 1, 0
    elif sat == 0.2:
        reps_core, reps_sat = 1, 1
    elif sat == 0.4:
        reps_core, reps_sat = 3, 8
    elif sat == 1.0:
        reps_core, reps_sat = 0, 1
    else:
        raise ValueError(sat)
    cols_S, cols_E = [], []
    for c in core:
        for _ in range(reps_core):
            cols_S.append(np.where(P.intrend[:, c], c, -1)); cols_E.append(np.where(P.intrend[:, c], 1.0, 0.0))
    for _ in range(reps_sat):
        cols_S.append(np.where(gate, j, -1)); cols_E.append(np.where(gate, 1.0, 0.0))
    S = np.stack(cols_S, axis=1); E = np.stack(cols_E, axis=1)
    if credit:
        E = E * h[:, None]
    return S, E


def run(sat, gate, credit=True):
    S, E = plan(sat, gate, credit)
    return R.run_plan(P, R.Plan(S=S, E=E, reb=REB))


def row(name, res):
    m = R.metrics(res.r, P.rf, res=res)
    yr = pd.Series(res.r.values, index=P.idx).groupby(P.idx.year).apply(lambda x: (1 + x).prod() - 1) * 100
    return (f"{name:34s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}  "
            f"最差年 {yr.min():6.1f}%  筆/年 {m['Trades/yr']:.0f}")


lines = [f"期間 {P.idx[0].date()} ~ {P.idx[-1].date()}(00631L 上市後);每月拉回目標比例", '']
IDX = R.sb_indices(n, B=2000, mean_block=63, seed=0)
for credit in (True, False):
    lines.append(f"=== {'含信用哨(現行設定)' if credit else '無信用哨'} ===")
    base = run(0.0, gate_live, credit)
    lines.append(row('0%(純核心 4 檔等權)', base))
    for sat in (0.2, 0.4):
        for gname, g in (('live:0050閘門+自身停損', gate_live), ('舊:只看0050閘門', gate_old)):
            res = run(sat, g, credit)
            bs = R.boot_delta_sharpe(res.r, base.r, P.rf, IDX)
            lines.append(row(f'{int(sat*100)}% 衛星({gname})', res)
                         + f"  | Δ超額Sharpe vs 0% {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}] p={bs['p']:.3f}")
    lines.append(row('100% 全押正2(live 閘門)', run(1.0, gate_live, credit)))
    lines.append('')
out = '\n'.join(lines)
print(out)
os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache'), exist_ok=True)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b13_satellite_fix.txt'), 'w', encoding='utf-8') as f:
    f.write(out + '\n')
