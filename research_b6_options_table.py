"""research_b6_options_table.py - 決策清單「持有方式」各選項,統一基準重算
========================================================================
之前各選項的數字來自不同研究(有的沒含信用哨、有的每月再平衡)→ 不能直接比。
這裡全部用 live 同一套:信用哨乘數(美股 HYG+LQD、台股 HYG+LQD+SOXX 平滑)、
美股 RiskTarget / 台股固定1倍、不再平衡(research_b5:再平衡無差別)、成本 美0.05%/台0.10%。
同一市場內所有選項用同一段期間。
用法: python research_b6_options_table.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_b1_credit_canary as C1

NEVER = None


def credit(P, tks):
    return C1.health(P.idx, tks, 'smooth')[:, None]


def go(pool, tickers, sizing, ctks, single, label, start=None):
    P = R.build_panel(pool, tickers=tickers, start=start)
    h = credit(P, ctks)
    nore = np.zeros(P.n, dtype=bool)
    out = {}
    ew = R.ew_plan(P, reb=nore, sizing=sizing)
    out['等權(不再平衡)'] = R.run_plan(P, R.Plan(S=ew.S, E=ew.E * h, reb=nore))
    rot = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', sizing=sizing)
    out['單押最強(原版輪動)'] = R.run_plan(P, R.Plan(S=rot.S, E=rot.E * h, reb=nore))
    j = P.col(single)
    S = np.where(P.intrend[:, j], j, -1)[:, None]
    E = (S >= 0).astype(float) * (P.expo_rt[:, [j]] if sizing == 'rt' else 1.0) * h
    out[f'只持有 {single.replace(".TW", "")}'] = R.run_plan(P, R.Plan(S=S, E=E, reb=nore))
    rows = []
    for k, r in out.items():
        m = R.metrics(r.r, P.rf, res=r)
        rows.append({'選項': k, 'CAGR%': round(m['CAGR'], 1), 'MDD%': round(m['MDD'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), '筆/年': round(m['Trades/yr'], 1)})
    print(f'\n[{label}] {P.idx[0].date()}→{P.idx[-1].date()}')
    print(pd.DataFrame(rows).to_string(index=False))
    return rows


if __name__ == '__main__':
    go('US5', ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY'], 'rt', ['HYG', 'LQD'], 'QQQ', '美股 5 檔')
    go('TW4', ['0050.TW', '0052.TW', '006208.TW', '0051.TW'], 'binary', ['HYG', 'LQD', 'SOXX'], '0050.TW', '台股 4 檔')
    go('TW4', ['0050.TW', '0052.TW', '0051.TW'], 'binary', ['HYG', 'LQD', 'SOXX'], '0050.TW', '台股 3 檔(同期)',
       start='2013-04-16')
