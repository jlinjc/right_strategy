"""research_b15_tw_feasible.py - 台股所有比較用「隔天開盤成交」重跑(修正偷看一天)
========================================================================
research_b14_execution_lag.py 發現:台股回測把美股 t 日收盤(HYG/LQD/SOXX 信用哨、VIX)
用在台股 t 日收盤的交易,但美股收盤比台股晚約 14 小時 = 偷看一天。這裡把 live 在用的台股結論全部
用做得到的執行方式重跑:t 日收盤(台股)+ 美股 t 日收盤算訊號 → 台股 t+1 開盤成交。
  ① 持有方式:4 檔等權 / 3 檔等權 / 只持有 0050 / 單押最強(都含信用哨、不再平衡)
  ② V底救援(台股全部核心;research_b2_overlays.vbottom)
  ③ 正2 衛星 0% vs 20%(research_b13_satellite_fix 的 live 版)
  ④ 信用黃燈「空手先別進」到場測試(research_q2e;信用健康度延後一天 = 台股收盤時真正知道的)
對照:同一比較在舊假設(t 日收盤成交)下的結果。輸出 research_cache/b15_tw_feasible.txt
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import research_rot_engine as R, research_b1_credit_canary as C1
import research_b14_execution_lag as B14, research_b2_overlays as B2

TW4 = ['0050.TW', '0052.TW', '006208.TW', '0051.TW']
OUT = []


def say(x=''):
    print(x); OUT.append(x)


def run_both(P, S, E, reb):
    """回傳 (舊假設 t 日收盤成交, 隔天開盤成交) 兩條日報酬,及 Result。"""
    r0 = R.run_plan(P, R.Plan(S=S, E=E, reb=reb))
    r1 = R.run_plan(P, R.Plan(S=B14.shift(S, 1, -1), E=B14.shift(E, 1, 0.0), reb=reb))
    W = r1.W; dW = np.vstack([np.zeros((1, W.shape[1])), np.diff(W, axis=0)])
    ro = pd.Series(r1.r.values + (dW * B14.intraday(P)).sum(axis=1), index=P.idx)
    return (r0.r, r0), (ro, r1)


def table(P, variants, base_key, IDX, title):
    say(f'\n=== {title}  {P.idx[0].date()}~{P.idx[-1].date()} ===')
    res = {k: run_both(P, *v) for k, v in variants.items()}
    for mode, lab in ((0, '舊假設:t 日收盤成交'), (1, '隔天開盤成交(做得到)')):
        say(f'-- {lab}')
        b = res[base_key][mode][0]
        for k, v in res.items():
            r, rr = v[mode]
            m = R.metrics(r, P.rf, res=rr)
            extra = ''
            if k != base_key:
                bs = R.boot_delta_sharpe(r, b, P.rf, IDX)
                extra = f"  | vs {base_key}:Δ超額Sharpe {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}] 單尾p(Δ≤0)={bs['p']:.3f}"
            say(f"   {k:22s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}  筆/年 {m['Trades/yr']:.0f}{extra}")


def methods():
    P = R.build_panel('TW4', tickers=TW4)
    nore = np.zeros(P.n, dtype=bool)
    h = C1.health(P.idx, ['HYG', 'LQD', 'SOXX'], 'smooth')[:, None]
    IDX = R.sb_indices(P.n, B=2000, mean_block=63, seed=0)
    ew = R.ew_plan(P, reb=nore, sizing='binary')
    v = {'4 檔等權(預設)': (ew.S, ew.E * h, nore)}
    keep = [P.col(t) for t in TW4 if t != '006208.TW']
    S3 = np.where(ew.S[:, keep] >= 0, ew.S[:, keep], -1); E3 = ew.E[:, keep] * h
    v['3 檔等權'] = (S3, E3, nore)
    j = P.col('0050.TW')
    S1 = np.where(P.intrend[:, j], j, -1)[:, None]
    v['只持有 0050'] = (S1, (S1 >= 0).astype(float) * h, nore)
    rot = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', sizing='binary')
    v['單押最強'] = (rot.S, rot.E * h, nore)
    table(P, v, '4 檔等權(預設)', IDX, '① 台股持有方式(含信用哨、不再平衡)')
    # ② V底救援(在含信用哨的等權上;系統空手時才觸發,救援部位不乘信用乘數 = live)
    Sv, Ev = B2.vbottom(P, list(range(P.k)))
    base_S, base_E = ew.S, ew.E * h
    Sm = np.where(base_S >= 0, base_S, Sv); Em = np.where(base_S >= 0, base_E, Ev)
    table(P, {'等權+信用哨': (base_S, base_E, nore), '+ V底救援': (Sm, Em, nore)},
          '等權+信用哨', IDX, '② 台股 V底救援')


def satellite():
    import research_b13_satellite_fix as B13
    P = B13.P
    IDX = R.sb_indices(P.n, B=2000, mean_block=63, seed=0)
    v = {}
    for sat, nm in ((0.0, '純核心'), (0.2, '正2衛星 20%'), (0.4, '正2衛星 40%'), (1.0, '全押正2')):
        S, E = B13.plan(sat, B13.gate_live, True)
        v[nm] = (S, E, B13.REB)
    table(P, v, '純核心', IDX, '③ 台股正2衛星(live 閘門、含信用哨、每月拉回)')


def credit_yellow():
    """q2e 的到場測試,信用健康度延後一天(台股收盤時真正知道的是美股前一天收盤)。"""
    import research_q2b_thresholds as Q, research_engine_2026q3 as E
    data = Q.data

    def health(idx, tks, lag):
        hs = []
        for tk in tks:
            s = data[tk]['Close']; m = s.rolling(200).mean()
            ok = (s >= m).astype(float); ok[m.isna()] = np.nan; hs.append(ok.reindex(idx).ffill())
        hh = sum(hs) / len(hs)
        return (hh.shift(lag) if lag else hh).values

    say('\n=== ④ 台股信用黃燈「空手先別進」到場測試(驗證範圍:原始曝險≥50%) ===')
    for lag, lab in ((0, '舊對齊(偷看一天)'), (1, '信用延後一天(做得到)')):
        dfs = []
        for tk in Q.TW:
            s, I, on, dd = Q.series(tk); p = E.params(tk); h = health(s.index, ['HYG', 'LQD', 'SOXX'], lag)
            raw_ok = p['budget'] / np.maximum(dd['STOP'], .02) >= 0.5
            partial = (h > 0) & (h < 1)
            thr = p['budget'] * np.nan_to_num(h, nan=1) / 0.5
            flag_dist = np.where((dd['D50'] < 0.12) & raw_ok & partial, dd['STOP'] - thr, -1.0)
            dfs.append(Q.arrival(s, I, on, flag_dist, 0.0))
        df = pd.concat(dfs); m = Q.summarize(df)
        a, b = Q.summarize(df[df.yr < 2018]), Q.summarize(df[df.yr >= 2018])
        f = lambda z: f"{z['平均差%']:+.2f}%/勝{z['等待勝%']}%/{z['逐年']}" if z else '—'
        say(f"   {lab:16s} n={m['n']} 等待平均{m['平均差%']:+.2f}% 中位{m['中位差%']:+.2f}% 等待勝{m['等待勝%']}% "
            f"逐年{m['逐年']} 等不到{m['等不到%']}% | 前半 {f(a)} | 後半 {f(b)}")


if __name__ == '__main__':
    methods(); satellite(); credit_yellow()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b15_tw_feasible.txt'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(OUT) + '\n')
