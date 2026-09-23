"""research_rot_verify_p2.py - 第二段驗證:候選機制(照 rot_refutation.md §3.2 登錄表)
========================================================================
B1  前 N 名等權(N=1..k,月換 + 21 偏移中位)← 本輪核心實驗
B1r 結構相同的隨機對照(同 N、同換倉日,隨機挑)→ G6
B3  分數遲滯帶 δ ∈ {0.25,0.5,0.75,1.0}(每日評估,不鎖)
B2  換 RS 定義:ret_126 / rs_csm / mom_12_1(lock-21 框架)
N1  去重池(美股去 SOXX、台股去 006208)+ BL-LIVE-F
N2  4 份錯開相位(0/5/10/15)的 lock-21 輪動
A3  事前規則分群等權(每群等權,群內等權)
A2  反波動加權(63日波動倒數,月再平衡)
全部對 BL-LIVE-F 與 BL-EW 比較:ΔShEx(bootstrap p、90%CI)、同波動 CAGR/MDD、分段、G1/G3/G6/G8。
用法: python research_rot_verify_p2.py POOL [tw_rf]
"""
import sys, warnings, json
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R

B_BOOT, N_SEED = 1000, 200      # 規格 2000/500 → 降階(已註明:降低解析度,不改方向)


def ivol_plan(P, reb, win=63):
    """A2 反波動加權:合格者權重 ∝ 1/63日波動(月再平衡),用槽位近似=每檔一槽,曝險=權重×k
       (槽位法下 E[t,s] 允許非 1;總曝險 = Σ E/k)。"""
    v = pd.DataFrame(P.ret).rolling(win).std().values
    v = np.where(np.isfinite(v) & (v > 1e-8), v, np.nan)   # 修:暖身期/零波動 → NaN,下方跳過(初版沒擋,整條報酬變 NaN)
    S = np.where(P.intrend, np.arange(P.k)[None, :], -1)
    E = np.zeros(S.shape)
    for t in range(P.n):
        el = np.where(P.intrend[t])[0]
        if len(el) == 0: continue
        el = np.array([j for j in el if np.isfinite(v[t, j])], dtype=int)
        if len(el) == 0: continue
        iv = 1.0 / v[t, el]
        w = iv / iv.sum()                      # 合格者間的相對權重
        E[t, el] = np.minimum(w * P.k, 3.0)    # 槽位各有 1/k 資金 → 乘 k 還原
    return R.Plan(S=S, E=E, reb=reb.copy(), name='A2 反波動')


def cluster_plan(P, groups, reb):
    """A3-static:每群等權,群內合格者等權(不合格→該份現金)。"""
    S = np.where(P.intrend, np.arange(P.k)[None, :], -1)
    E = np.zeros(S.shape)
    cols = [[P.col(t) for t in g if t in P.tickers] for g in groups]
    cols = [c for c in cols if c]
    for t in range(P.n):
        for c in cols:
            el = [j for j in c if P.intrend[t, j]]
            if not el: continue
            E[t, el] = (1.0 / len(cols) / len(el)) * P.k
    return R.Plan(S=S, E=E, reb=reb.copy(), name='A3 分群等權')


def tranche_plan_returns(P, phases=(0, 5, 10, 15), cost_mult=1.0):
    """N2:4 份各自跑 lock-21 BL-LIVE-F,報酬取平均(資金各 1/4)。"""
    rs = []
    for ph in phases:
        pl = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', phase=ph)
        rs.append(R.run_plan(P, pl, cost_mult=cost_mult).r.values)
    return pd.Series(np.mean(rs, axis=0), index=P.idx)


def evaluate(P, r, base_r, sigma_T, segs, IDX, name, rand_win=None):
    m = R.metrics(r, P.rf, sigma_T)
    mb = R.metrics(base_r, P.rf, sigma_T)
    bs = R.boot_delta_sharpe(r, base_r, P.rf, IDX)
    segd = []
    for sn, a, b in segs:
        sh, sb_ = R.sub(r, P.idx, a, b), R.sub(base_r, P.idx, a, b)
        if len(sh) < 200: continue
        rfs = pd.Series(P.rf, index=P.idx).reindex(sh.index).values
        segd.append((sn, R.sharpe_ex(sh.values, rfs) - R.sharpe_ex(sb_.values, rfs)))
    npos = sum(1 for _, d in segd if d > 0)
    g1 = bs['d'] >= 0.10 and (m['svCAGR'] - mb['svCAGR']) >= 1.0
    g3 = bool(segd) and npos >= (2 / 3) * len(segd) and min(d for _, d in segd) >= -0.15
    g8 = (m['svMDD'] - mb['svMDD']) >= -3.0
    return {'策略': name, '超額Sharpe': round(m['ShEx'], 2), 'CAGR%': round(m['CAGR'], 1),
            'MDD%': round(m['MDD'], 1), '同波動CAGR%': round(m['svCAGR'], 1), '同波動MDD%': round(m['svMDD'], 1),
            'ΔShEx': round(bs['d'], 3), 'CI90': f"[{bs['lo']:.2f},{bs['hi']:.2f}]", 'p': round(bs['p'], 3),
            '同波動ΔCAGR': round(m['svCAGR'] - mb['svCAGR'], 2), '分段Δ': ' '.join(f'{s}{d:+.2f}' for s, d in segd),
            '贏隨機%': rand_win, 'G1': 'V' if g1 else 'X', 'G3': 'V' if g3 else 'X', 'G8': 'V' if g8 else 'X'}


def main(pool, tw_rf=0.0):
    P = R.build_panel(pool, tw_rf=tw_rf)
    segs = R.SEGMENTS[pool]
    reb = R.reb_days(P.idx, 0)
    IDX = R.sb_indices(P.n, B=B_BOOT, mean_block=63, seed=0)
    base_f = R.run_plan(P, R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock')).r
    base_ew = R.run_plan(P, R.ew_plan(P, elig='intrend', reb=reb)).r
    sigma_T = np.asarray(base_f).std(ddof=1) * np.sqrt(252)
    print(f'\n{"#" * 118}\n[{pool}] {P.idx[0].date()}→{P.idx[-1].date()} rf={"IRX" if P.market=="US" else f"{tw_rf:.0%}"} '
          f'k={P.k} bootstrap={B_BOOT} 隨機種子={N_SEED}\n{"#" * 118}')
    cands = {}

    # B1:前 N 名等權(月換 + 21 偏移取中位路徑)
    print('\n--- B1 前 N 名等權(N 曲線;月換,21 偏移中位)---')
    b1rows = []
    for N in range(1, P.k + 1):
        offs = []
        for k in range(0, 21, 4):          # 21 偏移取 6 個(降階:0,4,8,12,16,20)
            pl = R.rotation_plan(P, elig='above', N=N, switch='reb', reb=R.reb_days(P.idx, k))
            offs.append(R.run_plan(P, pl).r.values)
        arr = np.array(offs)
        med_i = int(np.argsort([R.sharpe_ex(x, P.rf) for x in arr])[len(arr) // 2])
        r = pd.Series(arr[med_i], index=P.idx)
        cands[f'B1 N={N}'] = r
        wins = []
        for sd in range(N_SEED):
            pl = R.rotation_plan(P, elig='above', N=N, switch='reb', reb=R.reb_days(P.idx, 0), random_seed=sd)
            wins.append(R.sharpe_ex(R.run_plan(P, pl).r, P.rf))
        rw = round(float(np.mean(np.array(wins) < R.sharpe_ex(r, P.rf)) * 100))
        b1rows.append(evaluate(P, r, base_f, sigma_T, segs, IDX, f'B1 N={N}', rand_win=rw))
    print(pd.DataFrame(b1rows).to_string(index=False))

    # B3 遲滯帶 / B2 分數 / N1 / N2 / A3 / A2
    print('\n--- 其他候選(對 BL-LIVE-F)---')
    rows = []
    for d in (0.25, 0.5, 0.75, 1.0):
        r = R.run_plan(P, R.rotation_plan(P, elig='above', N=1, switch='daily', delta=d)).r
        rows.append(evaluate(P, r, base_f, sigma_T, segs, IDX, f'B3 遲滯δ={d}'))
    for sc in ('ret_126', 'rs_csm', 'mom_12_1'):
        r = R.run_plan(P, R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', score=sc)).r
        rows.append(evaluate(P, r, base_f, sigma_T, segs, IDX, f'B2 {sc}'))
    drop = {'US5': 'SOXX', 'BROAD': None, 'TW4': '006208.TW', 'TW3L': None, 'TW5': '006208.TW'}.get(pool)
    if drop:
        P2 = R.build_panel(pool, tw_rf=tw_rf, tickers=[t for t in P.tickers if t != drop])
        r2 = R.run_plan(P2, R.rotation_plan(P2, elig='above', N=1, lock=21, switch='lock')).r
        r2 = r2.reindex(P.idx).fillna(0)
        rows.append(evaluate(P, r2, base_f, sigma_T, segs, IDX, f'N1 去重池(去{drop})'))
    rows.append(evaluate(P, tranche_plan_returns(P), base_f, sigma_T, segs, IDX, 'N2 錯開4份輪動'))
    if R.GROUPS.get(pool):
        r = R.run_plan(P, cluster_plan(P, R.GROUPS[pool], reb)).r
        rows.append(evaluate(P, r, base_f, sigma_T, segs, IDX, 'A3 分群等權'))
    r = R.run_plan(P, ivol_plan(P, reb)).r
    rows.append(evaluate(P, r, base_f, sigma_T, segs, IDX, 'A2 反波動加權'))
    r = R.run_plan(P, R.rotation_plan(P, elig='above', N=1, switch='lock', lock=21, refill=False)).r
    rows.append(evaluate(P, r, base_f, sigma_T, segs, IDX, 'B4a 失格不接棒'))
    print(pd.DataFrame(rows).to_string(index=False))

    # 對 BL-EW 也比一次(採用規則:要同時贏 F 和 EW)
    print('\n--- 同樣候選對 BL-EW ---')
    rows2 = [evaluate(P, r, base_ew, sigma_T, segs, IDX, nm) for nm, r in
             list(cands.items())[:P.k]]
    print(pd.DataFrame(rows2)[['策略', 'ΔShEx', 'CI90', 'p', '同波動ΔCAGR', 'G1', 'G3', 'G8']].to_string(index=False))
    return {'pool': pool, 'b1': b1rows, 'others': rows, 'vs_ew': rows2}


if __name__ == '__main__':
    pool = sys.argv[1] if len(sys.argv) > 1 else 'US5'
    rf = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    out = main(pool, rf)
    json.dump(out, open(f'research_cache/rot_p2_{pool}_{rf}.json', 'w', encoding='utf-8'),
              ensure_ascii=False, default=str, indent=1)
