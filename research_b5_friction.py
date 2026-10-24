"""research_b5_friction.py - 待辦⑤:等權的實務摩擦(人肉執行到底要下幾張單?)
========================================================================
等權的代價是交易次數:美股約 70 筆/年、台股約 50~100 筆/年。這裡回答:
  1. 再平衡頻率:每月 / 每季 / 每年 / 從不(只在進出場時動)→ 績效差多少、少下多少單
  2. 跟「只持有一檔」比:美股 QQQ 擇時、台股 0050 擇時(一年只動幾次)
  3. 台股小資金:最低手續費(多數券商 NT$20/筆)對 10/30/100 萬資金的實際拖累
規則與 live 一致(等權、各自 200MA 擇時、信用哨乘數;美股 RiskTarget、台股固定1倍),美台分開。
用法: python research_b5_friction.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_b1_credit_canary as C1


def reb_mask(idx, how):
    if how == 'never':
        return np.zeros(len(idx), dtype=bool)
    return R.reb_days(idx, 0, freq={'month': 'M', 'quarter': 'Q', 'year': 'Y'}[how])


def run(pool, tickers, sizing, credit, single):
    P = R.build_panel(pool, tickers=tickers)
    h = C1.health(P.idx, credit, 'smooth')
    IDX = R.sb_indices(P.n, B=1000, mean_block=63, seed=0)
    rows, base = [], None
    for how in ('month', 'quarter', 'year', 'never'):
        pl = R.ew_plan(P, reb=reb_mask(P.idx, how), sizing=sizing)
        r = R.run_plan(P, R.Plan(S=pl.S, E=pl.E * h[:, None], reb=pl.reb), band=0.10)
        m = R.metrics(r.r, P.rf, res=r)
        if base is None:
            base = r.r
            d, p = 0.0, None
        else:
            bs = R.boot_delta_sharpe(r.r, base, P.rf, IDX); d, p = bs['d'], bs['p']
        rows.append({'做法': '等權 · 再平衡=' + {'month': '每月', 'quarter': '每季', 'year': '每年', 'never': '從不'}[how],
                     'CAGR%': round(m['CAGR'], 1), '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     '筆/年': round(m['Trades/yr'], 1), 'Δ vs 每月': round(d, 3), 'p': None if p is None else round(p, 3)})
    j = P.col(single)
    S = np.where(P.intrend[:, j], j, -1)[:, None]
    E = (S >= 0).astype(float) * (P.expo_rt[:, [j]] if sizing == 'rt' else 1.0) * h[:, None]
    r = R.run_plan(P, R.Plan(S=S, E=E, reb=np.zeros(P.n, bool)), band=0.10)
    m = R.metrics(r.r, P.rf, res=r)
    bs = R.boot_delta_sharpe(r.r, base, P.rf, IDX)
    rows.append({'做法': f'只持有 {single.replace(".TW", "")}(擇時+信用哨)', 'CAGR%': round(m['CAGR'], 1),
                 '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1), '筆/年': round(m['Trades/yr'], 1),
                 'Δ vs 每月': round(bs['d'], 3), 'p': round(bs['p'], 3)})
    print(f'\n{"=" * 110}\n[{pool}] {P.idx[0].date()}→{P.idx[-1].date()}  標的 {tickers}\n{"=" * 110}')
    print(pd.DataFrame(rows).to_string(index=False))
    return rows


def tw_fee_drag(trades_per_year, n_tickers):
    """台股最低手續費拖累:每筆最低 NT$20(多數券商電子單);一般費率 0.1425%×折扣(以 6 折=0.0855% 計)。"""
    print(f'\n台股最低手續費拖累(每年約 {trades_per_year:.0f} 筆、{n_tickers} 檔,每筆平均金額 = 資金÷{n_tickers}):')
    for cap in (100_000, 300_000, 1_000_000, 3_000_000):
        per = cap / n_tickers * 0.25                 # 一筆交易平均是一份的 1/4(再平衡多為小額調整)
        fee = max(per * 0.000855, 20)
        drag = trades_per_year * fee / cap * 100
        print(f'   資金 NT${cap:>9,}:每筆約 NT${per:>8,.0f} → 手續費 NT${fee:>4.0f}(最低20)'
              f' → 一年拖累約 {drag:.2f}%')


if __name__ == '__main__':
    run('US5', ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY'], 'rt', ['HYG', 'LQD'], 'QQQ')
    tw = run('TW4', ['0050.TW', '0052.TW', '006208.TW', '0051.TW'], 'binary', ['HYG', 'LQD', 'SOXX'], '0050.TW')
    tw3 = run('TW4', ['0050.TW', '0052.TW', '0051.TW'], 'binary', ['HYG', 'LQD', 'SOXX'], '0050.TW')
    tw_fee_drag(tw[0]['筆/年'], 4)
    tw_fee_drag(tw[1]['筆/年'], 4)
