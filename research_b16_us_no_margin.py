"""research_b16_us_no_margin.py - 美股不融資版(2026-10-25 Jason:不打算融資融券)
========================================================================
現行美股倉位 = RiskTarget(曝險 = min(budget ÷ 離停損距離, 150%)),上限 150% 代表會融資。
不融資的兩種做法(每一份最多 100%,總部位不超過資金):
  A RiskTarget 上限 100%:曝險 = min(budget ÷ 離停損距離, 100%)
  B 固定 1 倍(和台股一樣):在場就 100%
都含信用哨(HYG+LQD 平滑)、5 檔等權、不再平衡、隔天開盤成交(research_b14 的算法)。
對照:現行(上限 150%,會融資)。輸出 research_cache/b16_us_no_margin.txt
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import research_rot_engine as R, research_b1_credit_canary as C1, research_b14_execution_lag as B14

P = R.build_panel('US5', tickers=['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY'])
nore = np.zeros(P.n, dtype=bool)
h = C1.health(P.idx, ['HYG', 'LQD'], 'smooth')[:, None]
rt = R.ew_plan(P, reb=nore, sizing='rt')
bn = R.ew_plan(P, reb=nore, sizing='binary')
ix = B14.intraday(P)
IDX = R.sb_indices(P.n, B=2000, mean_block=63, seed=0)


def opened(S, E):
    r1 = R.run_plan(P, R.Plan(S=B14.shift(S, 1, -1), E=B14.shift(E, 1, 0.0), reb=nore))
    W = r1.W; dW = np.vstack([np.zeros((1, W.shape[1])), np.diff(W, axis=0)])
    return pd.Series(r1.r.values + (dW * ix).sum(axis=1), index=P.idx), r1


V = {'現行:RiskTarget 上限150%(融資)': (rt.S, rt.E * h),
     'A:RiskTarget 上限100%': (rt.S, np.minimum(rt.E, 1.0) * h),
     'B:固定 1 倍': (bn.S, bn.E * h)}
res = {k: opened(*v) for k, v in V.items()}
lines = [f'美股 5 檔等權 + 信用哨,隔天開盤成交  {P.idx[0].date()}~{P.idx[-1].date()}']
for k, (r, rr) in res.items():
    m = R.metrics(r, P.rf, res=rr)
    yr = r.groupby(P.idx.year).apply(lambda x: (1 + x).prod() - 1) * 100
    tot = rr.W.sum(axis=1)
    lines.append(f"{k:30s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}  最差年 {yr.min():6.1f}%  "
                 f"筆/年 {m['Trades/yr']:.0f}  平均總曝險 {tot.mean():.2f}  最高 {tot.max():.2f}")
base = res['B:固定 1 倍'][0]
bs = R.boot_delta_sharpe(res['A:RiskTarget 上限100%'][0], base, P.rf, IDX)
lines.append(f"A − B:Δ超額Sharpe {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}] 單尾p(Δ≤0)={bs['p']:.3f}")
out = '\n'.join(lines)
print(out)
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b16_us_no_margin.txt'), 'w', encoding='utf-8') as f:
    f.write(out + '\n')


# ── 持有方式比較(不融資版:RiskTarget 上限 100%)──
def methods():
    lines = ['\n[美股持有方式,不融資(RiskTarget 上限100%)+ 信用哨,隔天開盤成交]']
    capE = lambda E: np.minimum(E, 1.0) * h
    rot = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', sizing='rt')
    j = P.col('QQQ')
    S1 = np.where(P.intrend[:, j], j, -1)[:, None]
    E1 = (S1 >= 0).astype(float) * P.expo_rt[:, [j]]
    vv = {'5 檔等權': (rt.S, capE(rt.E)), '單押最強': (rot.S, capE(rot.E)), '只持有 QQQ': (S1, capE(E1))}
    rr = {k: opened(*v) for k, v in vv.items()}
    b = rr['5 檔等權'][0]
    for k, (r, res_) in rr.items():
        m = R.metrics(r, P.rf, res=res_)
        extra = ''
        if k != '5 檔等權':
            bs = R.boot_delta_sharpe(r, b, P.rf, IDX)
            extra = f"  | vs 等權 Δ {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}]"
        lines.append(f"{k:12s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}  筆/年 {m['Trades/yr']:.0f}{extra}")
    return lines


if __name__ == '__main__':
    ml = methods()
    print('\n'.join(ml))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b16_us_no_margin.txt'), 'a', encoding='utf-8') as f:
        f.write('\n'.join(ml) + '\n')
