"""research_b17_exante_pool.py - 美股「每年選最強前 5 檔」規則 vs 人工固定 5 檔(現行設定)
========================================================================
Jason 2026-10-25:把「每年選最強前 5 檔」正式變成規則,不再用人工固定的 5 檔;先回測再決定上不上 live。
規則(事前,不偷看):每年第一個交易日,用「前一個交易日以前」的 756 日(36 個月)報酬,
  從 16 檔候選(9 檔 SPDR 產業 + SPY/QQQ/DIA/IWM/MDY + SMH/SOXX,research_b4 同一份)挑前 5 名,當年度等權持有。
  ⚠️候選名單本身也是今天列的(SMH/SOXX 有一點事後色彩),但每一年選誰完全由規則決定。
現行設定:5 槽等權、各自 200MA 擇時、RiskTarget 上限 100%(不融資)、信用哨(HYG+LQD 平滑)、不再平衡、
  隔天開盤成交(research_b14 的 shift + intraday 算法)、成本 0.05%。
比較:
  A 固定 US5(現行 live,US5 逐檔校準參數)
  A' 固定 US5(預設參數)— 和規則版同一套參數,分開「選池」和「調參」兩種事後效果
  C 每年選最強前 5(36 月報酬)
  D 每年選最強前 5(36 月 Sharpe)— 敏感度
輸出 research_cache/b17_exante_pool.txt
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import research_rot_engine as R, research_b1_credit_canary as C1, research_b14_execution_lag as B14
import research_b4_pool_hindsight as B4

CAND, US5 = B4.CAND, B4.US5
OUT = []


def say(x=''):
    print(x); OUT.append(x)


def opened(P, S, E):
    nore = np.zeros(P.n, dtype=bool)
    r1 = R.run_plan(P, R.Plan(S=B14.shift(S, 1, -1), E=B14.shift(E, 1, 0.0), reb=nore))
    W = r1.W; dW = np.vstack([np.zeros((1, W.shape[1])), np.diff(W, axis=0)])
    return pd.Series(r1.r.values + (dW * B14.intraday(P)).sum(axis=1), index=P.idx), r1


def slots_from_mask(P, mask):
    """mask[t, j] = 當年被選中 → 5 個槽位,槽位內依 ticker 排序;在趨勢中才持有。曝險 = RiskTarget 上限 100%。"""
    S = np.full((P.n, 5), -1); E = np.zeros((P.n, 5))
    for t in range(P.n):
        cols = np.where(mask[t])[0][:5]
        for s, j in enumerate(cols):
            if P.intrend[t, j]:
                S[t, s] = j; E[t, s] = min(P.expo_rt[t, j], 1.0)
    return S, E


def pick_mask(P, how):
    m = np.zeros((P.n, P.k), dtype=bool)
    years = P.idx.year
    picks = {}
    for y in sorted(set(years)):
        idx = np.where(years == y)[0]; t0 = idx[0]
        if t0 < 756:
            continue
        win = P.ret[t0 - 756:t0]
        score = np.prod(1 + win, axis=0) - 1 if how == 'ret' else win.mean(0) / np.maximum(win.std(0), 1e-9)
        top = np.sort(np.argsort(-score)[:5])
        m[np.ix_(idx, top)] = True
        picks[y] = [P.tickers[j] for j in top]
    return m, picks


def main():
    P = R.build_panel('BROAD', tickers=CAND)
    h = C1.health(P.idx, ['HYG', 'LQD'], 'smooth')[:, None]
    start = P.idx[756]
    res = {}
    us5m = np.isin(np.array(P.tickers), US5)[None, :].repeat(P.n, 0)
    S, E = slots_from_mask(P, us5m); res["A' 固定 US5(預設參數)"] = opened(P, S, E * h)
    mC, picksC = pick_mask(P, 'ret'); S, E = slots_from_mask(P, mC); res['C 每年選最強前5(36月報酬)'] = opened(P, S, E * h)
    mD, _ = pick_mask(P, 'sharpe'); S, E = slots_from_mask(P, mD); res['D 每年選最強前5(36月Sharpe)'] = opened(P, S, E * h)
    # A:現行 live(US5 校準參數),另建 panel,取同一段期間
    PU = R.build_panel('US5', tickers=US5)
    hu = C1.health(PU.idx, ['HYG', 'LQD'], 'smooth')[:, None]
    ew = R.ew_plan(PU, reb=np.zeros(PU.n, dtype=bool), sizing='rt')
    rA, _ = opened(PU, ew.S, np.minimum(ew.E, 1.0) * hu)
    rA = rA.reindex(P.idx).fillna(0.0)
    res = {'A 固定 US5(現行 live)': (rA, None), **res}

    rf = pd.Series(P.rf, index=P.idx)
    s0 = str(start.date())
    say(f'候選 {len(CAND)} 檔:{", ".join(CAND)}')
    say(f'比較期間 {s0} → {P.idx[-1].date()}(規則需要 36 個月暖身);不融資、信用哨、隔天開盤成交\n')
    base = R.sub(res['A 固定 US5(現行 live)'][0], P.idx, s0)
    rfs = rf.reindex(base.index).values
    IDX = R.sb_indices(len(base), B=2000, mean_block=63, seed=0)
    for k, (r, _) in res.items():
        s = R.sub(r, P.idx, s0)
        m = R.metrics(s, rfs)
        yr = s.groupby(s.index.year).apply(lambda x: (1 + x).prod() - 1) * 100
        extra = ''
        if not k.startswith('A '):
            bs = R.boot_delta_sharpe(s.values, base.values, rfs, IDX)
            extra = f"  | vs 固定US5 Δ超額Sharpe {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}]"
        say(f"{k:30s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}  最差年 {yr.min():6.1f}%{extra}")
    # 逐年
    a = base; c = R.sub(res['C 每年選最強前5(36月報酬)'][0], P.idx, s0)
    yr = pd.DataFrame({'固定US5': a.groupby(a.index.year).apply(lambda x: (1 + x).prod() - 1) * 100,
                       '每年選最強': c.groupby(c.index.year).apply(lambda x: (1 + x).prod() - 1) * 100}).round(1)
    yr['差'] = (yr['固定US5'] - yr['每年選最強']).round(1)
    yr['當年選中'] = [', '.join(picksC.get(y, [])) for y in yr.index]
    say('\n逐年報酬(%)與規則選中的 5 檔:')
    say(yr.to_string())
    say(f"\n固定US5 勝過規則的年數:{int((yr['差'] > 0).sum())}/{len(yr)}")


if __name__ == '__main__':
    main()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b17_exante_pool.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n')
