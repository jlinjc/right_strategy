"""research_rot_verify_p1.py - 第一段驗證:基準 + 診斷 + 對稱檢驗(照 rot_refutation.md 事前規格)
========================================================================
基準:BL-LIVE-F(忠於 live:合格=收盤≥200MA、lock 21 交易日、失格即換最強)
      BL-LIVE-H(遲滯版)、BL-EW(各檔擇時、月底回 1/N、月內漂移)
診斷:D-d(同群換倉占比與之後21日損益)、D-f(擇時/挑選報酬分解)
對稱檢驗:BL-EW vs BL-LIVE-F(雙向)、BL-LIVE-H vs BL-LIVE-F,美股台股分開,G1~G8
用法: python research_rot_verify_p1.py [pool ...]
"""
import sys, hashlib, warnings, json
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R

B_BOOT = 1000          # 規格 2000 → 降到 1000(記憶體/時間;降低檢定解析度,方向不變)
SPEC = 'research_cache/rot_refutation.md'


def sha():
    return hashlib.sha256(open(SPEC, 'rb').read()).hexdigest()


def baselines(P, sizing='binary', cost_mult=1.0, offset=0, phase=0):
    reb = R.reb_days(P.idx, offset)
    out = {}
    out['BL-LIVE-F'] = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock',
                                       sizing=sizing, phase=phase, name='BL-LIVE-F')
    out['BL-LIVE-H'] = R.rotation_plan(P, elig='intrend', N=1, lock=21, switch='lock',
                                       sizing=sizing, phase=phase, name='BL-LIVE-H')
    out['BL-EW'] = R.ew_plan(P, elig='intrend', reb=reb, sizing=sizing)
    return {k: R.run_plan(P, p, cost_mult=cost_mult) for k, p in out.items()}


def seg_rows(P, res, sigma_T, segs):
    rows = []
    for nm, r in res.items():
        row = {'策略': nm}
        for sn, a, b in segs:
            s = R.sub(r.r, P.idx, a, b)
            if len(s) < 200:
                row[sn] = np.nan; continue
            rf = pd.Series(P.rf, index=P.idx).reindex(s.index).values
            row[sn] = round(R.sharpe_ex(s.values, rf), 2)
        rows.append(row)
    return pd.DataFrame(rows)


def d_f(P, res_f):
    """D-f 報酬分解:BL-LIVE-F 報酬 ≈ 擇時(當日合格者等權×曝險) + 挑選(持有 − 合格者等權)。"""
    S = res_f.plan.S[:, 0]
    sel = []
    for t in range(1, P.n):
        a = S[t - 1]
        if a < 0: continue
        el = np.where(P.above[t - 1])[0]
        if len(el) < 2: continue
        sel.append(P.ret[t, a] - P.ret[t, el].mean())
    sel = np.array(sel)
    if len(sel) < 100: return None
    return {'n': len(sel), '年化挑選報酬%': sel.mean() * 252 * 100,
            't值': sel.mean() / sel.std(ddof=1) * np.sqrt(len(sel)),
            '勝率%': (sel > 0).mean() * 100}


def d_d(P, res_f, groups):
    """D-d:同群換倉(例 SMH↔SOXX、0050↔006208)占比,以及換倉後21日『換 vs 不換』的差。"""
    if not groups: return None
    gof = {}
    for gi, g in enumerate(groups):
        for tk in g: gof[P.col(tk)] = gi if tk in P.tickers else None
    sw = [(t, o, nw) for (t, s, o, nw, kind) in res_f.plan.events if kind == 'switch' and o >= 0 and nw >= 0]
    if not sw: return None
    same = [(t, o, nw) for (t, o, nw) in sw if gof.get(o) is not None and gof.get(o) == gof.get(nw)]
    def fwd(t, j, h=21):
        e = min(t + h, P.n - 1)
        return np.prod(1 + P.ret[t + 1:e + 1, j]) - 1
    dif = lambda lst: np.array([fwd(t, nw) - fwd(t, o) for (t, o, nw) in lst]) if lst else np.array([])
    ds, da = dif(same), dif(sw)
    return {'換倉次數': len(sw), '同群換倉%': round(len(same) / len(sw) * 100, 1),
            '同群換倉後21日(換−不換)%': round(ds.mean() * 100, 2) if len(ds) else None,
            '全部換倉後21日(換−不換)%': round(da.mean() * 100, 2) if len(da) else None,
            '同群n': len(same)}


def gates(P, rH, rB, sigma_T, segs, IDX, label):
    """G1/G2(未校正 p)/G3/G8 + 同波動數字。G4~G7 在跨池/隨機層另判。"""
    rf = P.rf
    mH, mB = R.metrics(rH, rf, sigma_T), R.metrics(rB, rf, sigma_T)
    bs = R.boot_delta_sharpe(rH, rB, rf, IDX)
    segd = []
    for sn, a, b in segs:
        sh = R.sub(rH, P.idx, a, b); sb_ = R.sub(rB, P.idx, a, b)
        if len(sh) < 200: continue
        rfs = pd.Series(rf, index=P.idx).reindex(sh.index).values
        segd.append((sn, R.sharpe_ex(sh.values, rfs) - R.sharpe_ex(sb_.values, rfs)))
    npos = sum(1 for _, d in segd if d > 0)
    return {'比較': label, 'ΔShEx': round(bs['d'], 3), 'CI90': f"[{bs['lo']:.2f},{bs['hi']:.2f}]",
            'p': round(bs['p'], 3), '同波動ΔCAGR': round(mH['svCAGR'] - mB['svCAGR'], 2),
            '同波動ΔMDD': round(mH['svMDD'] - mB['svMDD'], 2),
            '分段Δ': ' '.join(f'{sn}{d:+.2f}' for sn, d in segd),
            'G1': 'V' if (bs['d'] >= 0.10 and mH['svCAGR'] - mB['svCAGR'] >= 1.0) else 'X',
            'G3': 'V' if (segd and npos >= (2 / 3) * len(segd) and min(d for _, d in segd) >= -0.15) else 'X',
            'G8': 'V' if mH['svMDD'] - mB['svMDD'] >= -3.0 else 'X'}


def run_pool(pool, tw_rf=0.0):
    P = R.build_panel(pool, tw_rf=tw_rf)
    segs = R.SEGMENTS[pool]
    res = baselines(P)
    sigma_T = np.asarray(res['BL-LIVE-F'].r).std(ddof=1) * np.sqrt(252)
    print(f'\n{"=" * 118}\n[{pool}] {P.idx[0].date()} → {P.idx[-1].date()} ({P.n/252:.1f}年) '
          f'標的={P.tickers} rf={"IRX" if P.market=="US" else f"{tw_rf:.0%}"} 成本={P.cost*100:.3f}%/邊\n{"=" * 118}')
    rows = []
    for nm, r in res.items():
        m = R.metrics(r.r, P.rf, sigma_T, r)
        rows.append({'策略': nm, '平均曝險': round(m['Expo'], 2), '平均持有檔': round(m['Nhold'], 2),
                     '筆/年': round(m['Trades/yr'], 1), 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     '同波動CAGR%': round(m['svCAGR'], 1), '同波動MDD%': round(m['svMDD'], 1)})
    wt = R.WIDEST[pool]
    if wt in P.tickers:
        rr = R.run_plan(P, R.single_timed_plan(P, wt))
        m = R.metrics(rr.r, P.rf, sigma_T, rr)
        rows.append({'策略': f'(參考)單押擇時 {wt}', '平均曝險': round(m['Expo'], 2), '平均持有檔': round(m['Nhold'], 2),
                     '筆/年': round(m['Trades/yr'], 1), 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     '同波動CAGR%': round(m['svCAGR'], 1), '同波動MDD%': round(m['svMDD'], 1)})
        bh = R.buyhold(P, wt); m = R.metrics(bh, P.rf, sigma_T)
        rows.append({'策略': f'(參考)買持 {wt}', 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     '同波動CAGR%': round(m['svCAGR'], 1), '同波動MDD%': round(m['svMDD'], 1)})
    print(pd.DataFrame(rows).to_string(index=False))
    if segs:
        print('\n分段超額 Sharpe:'); print(seg_rows(P, res, sigma_T, segs).to_string(index=False))

    print('\n診斷 D-f(挑選 alpha:持有標的 − 當日合格者等權):', d_f(P, res['BL-LIVE-F']))
    print('診斷 D-d(同群換倉):', d_d(P, res['BL-LIVE-F'], R.GROUPS.get(pool)))

    IDX = R.sb_indices(P.n, B=B_BOOT, mean_block=63, seed=0)
    print('\n對稱檢驗(G1/G3/G8 + 未校正 p;G2 需家族校正,見摘要):')
    cmps = [(res['BL-EW'].r, res['BL-LIVE-F'].r, 'BL-EW − BL-LIVE-F'),
            (res['BL-LIVE-F'].r, res['BL-EW'].r, 'BL-LIVE-F − BL-EW'),
            (res['BL-LIVE-H'].r, res['BL-LIVE-F'].r, 'BL-LIVE-H − BL-LIVE-F')]
    grows = [gates(P, a, b, sigma_T, segs, IDX, lab) for a, b, lab in cmps]
    print(pd.DataFrame(grows).to_string(index=False))

    print('\nG5 穩健性(ΔShEx:BL-EW − BL-LIVE-F):')
    rob = []
    for lab, kw in [('主設定', {}), ('2×成本', {'cost_mult': 2.0}), ('統一buf=0.99', {'buf': 0.99}),
                    ('RT倉位', {'sizing': 'rt'})]:
        if 'buf' in kw:
            P2 = R.build_panel(pool, buf_override=kw['buf'], tw_rf=tw_rf); r2 = baselines(P2)
            d = R.sharpe_ex(r2['BL-EW'].r, P2.rf) - R.sharpe_ex(r2['BL-LIVE-F'].r, P2.rf)
        else:
            r2 = baselines(P, **kw)
            d = R.sharpe_ex(r2['BL-EW'].r, P.rf) - R.sharpe_ex(r2['BL-LIVE-F'].r, P.rf)
        rob.append({'條件': lab, 'ΔShEx': round(d, 3)})
    offs = []
    for k in range(21):
        r2 = baselines(P, offset=k, phase=k)
        offs.append(R.sharpe_ex(r2['BL-EW'].r, P.rf) - R.sharpe_ex(r2['BL-LIVE-F'].r, P.rf))
    offs = np.array(offs)
    rob.append({'條件': f'21偏移中位(>0比例 {np.mean(offs>0)*100:.0f}%)', 'ΔShEx': round(float(np.median(offs)), 3)})
    print(pd.DataFrame(rob).to_string(index=False))
    print(f'   偏移分佈 min/25%/中位/75%/max = {offs.min():.2f}/{np.quantile(offs,.25):.2f}/'
          f'{np.median(offs):.2f}/{np.quantile(offs,.75):.2f}/{offs.max():.2f}')
    return {'pool': pool, 'rows': rows, 'gates': grows, 'rob': rob, 'offs': offs.tolist(),
            'df': d_f(P, res['BL-LIVE-F']), 'dd': d_d(P, res['BL-LIVE-F'], R.GROUPS.get(pool))}


if __name__ == '__main__':
    print(f'規格 sha256 = {sha()}')
    print(f'執行 {pd.Timestamp.now():%Y-%m-%d %H:%M}  bootstrap B={B_BOOT}(規格2000→降階,已註明)')
    pools = sys.argv[1:] or ['US5', 'BROAD', 'TW4', 'TW3L', 'TW5']
    allres = {}
    for pool in pools:
        if pool.startswith('TW'):
            for rf in (0.0, 0.01):
                allres[f'{pool}_rf{rf}'] = run_pool(pool, tw_rf=rf)
        else:
            allres[pool] = run_pool(pool)
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != 'rows'} for k, v in allres.items()},
              open('research_cache/rot_p1.json', 'w', encoding='utf-8'), ensure_ascii=False, default=str, indent=1)
