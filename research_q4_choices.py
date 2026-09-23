"""research_q4_choices.py - 回答四個選擇題(2026-09-23)
========================================================================
A 槓桿階梯:美股(單押輪動×RiskTarget)、台股(等權×固定1倍)各乘 k 倍 → 報酬/回撤對照表
B 台股還能加哪些標的:觀察名單逐一加進等權池,看有沒有變好(歷史長度誠實標註)
C 新版 vs 原版:台股(等權+固定1倍)vs(單押輪動+RiskTarget);美股(未改)
D 正2(00631L):四種接法對照 + 和「直接把核心加槓桿」比較
用法: python research_q4_choices.py [A|B|C|D ...]
"""
import sys, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
import research_rot_engine as R
import research_data_2026q3 as D

TW_WATCH = ['00733.TW', '00762.TW', '00895.TW', '00935.TW', '00913.TW', '00921.TW',
            '00891.TW', '00892.TW', '00981A.TW', '00631L.TW', '0056.TW', '00692.TW']


def ensure(tks):
    """把不在快取裡的標的補下載進快取。"""
    import yfinance as yf, pickle, os
    d = D.load(); miss = [t for t in tks if t not in d]
    if miss:
        raw = yf.download(' '.join(miss), period='max', interval='1d', auto_adjust=True,
                          progress=False, group_by='ticker')
        for tk in miss:
            try:
                df = raw[tk].dropna(subset=['Close'])
                if len(df): d[tk] = df
            except Exception:
                pass
        pickle.dump(d, open(D.CACHE, 'wb'))
    return d


def show(rows, title):
    print(f'\n{"=" * 112}\n{title}\n{"=" * 112}')
    print(pd.DataFrame(rows).to_string(index=False))


def lever_ladder():
    """A:同一套規則,只改槓桿倍數。美股=現行單押輪動×RiskTarget;台股=等權×固定1倍。"""
    for pool, label, mk in [('US5', '美股(單押最強輪動 · RiskTarget)', 'US'), ('TW4', '台股(等權 · 固定1倍)', 'TW')]:
        P = R.build_panel(pool)
        rows = []
        for k in (0.5, 0.75, 1.0, 1.25, 1.5, 2.0):
            if mk == 'US':
                pl = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', sizing='rt')
                E = np.minimum(pl.E * k, 3.0)
                pl = R.Plan(S=pl.S, E=E, reb=pl.reb)
            else:
                pl = R.ew_plan(P, elig='intrend', reb=R.reb_days(P.idx, 0))
                pl = R.Plan(S=pl.S, E=pl.E * k, reb=pl.reb)
            r = R.run_plan(P, pl)
            m = R.metrics(r.r, P.rf, res=r)
            rows.append({'槓桿×': k, '平均曝險': round(m['Expo'], 2), 'CAGR%': round(m['CAGR'], 1),
                         'Vol%': round(m['Vol'], 1), '超額Sharpe': round(m['ShEx'], 2),
                         'MDD%': round(m['MDD'], 1), 'Calmar': round(m['Calmar'], 2),
                         '$10萬→': f"{10 * (1 + m['CAGR'] / 100) ** (P.n / 252):.0f}萬"})
        show(rows, f'A 槓桿階梯 — {label}  {P.idx[0].date()}→{P.idx[-1].date()}('
                   f'{P.n/252:.1f}年;k=1.0 是現行設定)')


def tw_candidates():
    """B:觀察名單逐一加進台股等權池(4檔基準),看有沒有變好。"""
    d = ensure(TW_WATCH)
    print(f'\n{"=" * 112}\nB 台股候選標的:資料長度與加進等權池的效果\n{"=" * 112}')
    base = ['0050.TW', '0052.TW', '006208.TW', '0051.TW']
    info = []
    for tk in TW_WATCH:
        if tk not in d: info.append({'標的': tk, '狀態': '抓不到資料'}); continue
        s = D.tw_clean(d[tk]['Close'].dropna())
        yrs = len(s) / 252
        info.append({'標的': tk, '起點': str(s.index[0].date()), '年數': round(yrs, 1),
                     '可回測(需>200日+夠長)': '可' if yrs >= 3 else '太短'})
    print(pd.DataFrame(info).to_string(index=False))
    rows = []
    P0 = R.build_panel('TW4')
    r0 = R.run_plan(P0, R.ew_plan(P0, reb=R.reb_days(P0.idx, 0)))
    s0 = R.metrics(r0.r, P0.rf)
    rows.append({'池子': '基準:4檔等權', '起點': str(P0.idx[0].date()), 'CAGR%': round(s0['CAGR'], 1),
                 '超額Sharpe': round(s0['ShEx'], 2), 'MDD%': round(s0['MDD'], 1), 'ΔSharpe(同期比較)': '—'})
    for tk in TW_WATCH:
        if tk not in d: continue
        try:
            P = R.build_panel('TW4', tickers=base + [tk])
        except Exception as e:
            rows.append({'池子': f'+{tk}', '起點': f'建panel失敗 {e}'}); continue
        if P.n < 252 * 3:
            rows.append({'池子': f'+{tk}', '起點': str(P.idx[0].date()), 'CAGR%': None,
                         '超額Sharpe': None, 'MDD%': None, 'ΔSharpe(同期比較)': '期間太短(<3年)不判定'})
            continue
        r = R.run_plan(P, R.ew_plan(P, reb=R.reb_days(P.idx, 0)))
        m = R.metrics(r.r, P.rf)
        # 同期基準(把 4 檔池截到同一段)
        Pb = R.build_panel('TW4', start=str(P.idx[0].date()))
        rb = R.run_plan(Pb, R.ew_plan(Pb, reb=R.reb_days(Pb.idx, 0)))
        mb = R.metrics(rb.r, Pb.rf)
        rows.append({'池子': f'+{tk}', '起點': str(P.idx[0].date()), 'CAGR%': round(m['CAGR'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     'ΔSharpe(同期比較)': round(m['ShEx'] - mb['ShEx'], 3)})
    show(rows, 'B 每次只加一檔進等權池(和同期的 4 檔池比)')


def v2_vs_v1():
    """C:新版 vs 原版(同期間、同成本、同 bootstrap)。"""
    IDXC = {}
    for pool, lab in [('TW4', '台股(主判定池 4 檔)'), ('TW3L', '台股(長樣本 3 檔)'), ('US5', '美股(5 檔)')]:
        P = R.build_panel(pool)
        reb = R.reb_days(P.idx, 0)
        old = R.run_plan(P, R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock',
                                            sizing='rt' if pool.startswith('US') else 'rt'))
        new = R.run_plan(P, R.ew_plan(P, reb=reb) if not pool.startswith('US') else
                         R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', sizing='rt'))
        IDX = R.sb_indices(P.n, B=1000, mean_block=63, seed=0)
        bs = R.boot_delta_sharpe(new.r, old.r, P.rf, IDX)
        mo, mn = R.metrics(old.r, P.rf, res=old), R.metrics(new.r, P.rf, res=new)
        rows = [{'版本': '原版(單押輪動 + RiskTarget)', 'CAGR%': round(mo['CAGR'], 1), 'Vol%': round(mo['Vol'], 1),
                 '超額Sharpe': round(mo['ShEx'], 2), 'MDD%': round(mo['MDD'], 1), '筆/年': round(mo['Trades/yr'], 1)},
                {'版本': '新版' + ('(等權 + 固定1倍)' if not pool.startswith('US') else '(美股未改)'),
                 'CAGR%': round(mn['CAGR'], 1), 'Vol%': round(mn['Vol'], 1),
                 '超額Sharpe': round(mn['ShEx'], 2), 'MDD%': round(mn['MDD'], 1), '筆/年': round(mn['Trades/yr'], 1)}]
        show(rows, f'C 新版 vs 原版 — {lab}  {P.idx[0].date()}→{P.idx[-1].date()}  '
                   f'ΔSharpe {bs["d"]:+.3f} [90%CI {bs["lo"]:.2f},{bs["hi"]:.2f}] p={bs["p"]:.3f}')


def leveraged_tw():
    """D:正2(00631L)四種接法 + 和「核心直接加槓桿」比較(同期間)。"""
    d = ensure(['00631L.TW'])
    P = R.build_panel('TW4', tickers=['0050.TW', '0052.TW', '006208.TW', '0051.TW', '00631L.TW'])
    j = P.col('00631L.TW')
    core = [P.col(t) for t in ['0050.TW', '0052.TW', '006208.TW', '0051.TW']]
    reb = R.reb_days(P.idx, 0)
    n, k = P.n, P.k
    rows = []

    def sim(E, S, name, note=''):
        r = R.run_plan(P, R.Plan(S=S, E=E, reb=reb))
        m = R.metrics(r.r, P.rf, res=r)
        yr = pd.Series(r.r.values, index=P.idx).groupby(P.idx.year).apply(lambda x: (1 + x).prod() - 1) * 100
        rows.append({'做法': name, 'CAGR%': round(m['CAGR'], 1), 'Vol%': round(m['Vol'], 1),
                     '超額Sharpe': round(m['ShEx'], 2), 'MDD%': round(m['MDD'], 1),
                     'Calmar': round(m['Calmar'], 2), '筆/年': round(m['Trades/yr'], 1),
                     '最差年%': round(yr.min(), 1), '最好年%': round(yr.max(), 1), '備註': note})
        return r

    # 1 純核心 4 檔等權(不碰正2)
    S = np.full((n, 4), -1); E = np.zeros((n, 4))
    for i, c in enumerate(core):
        S[:, i] = np.where(P.intrend[:, c], c, -1); E[:, i] = np.where(P.intrend[:, c], 1.0, 0.0)
    sim(E, S, '① 4檔等權(新版現況)')
    # 2 4檔等權 + 20% 正2 衛星(正2 用自己的200MA)
    S2 = np.full((n, 5), -1); E2 = np.zeros((n, 5))
    S2[:, :4] = S; E2[:, :4] = E * 0.8
    S2[:, 4] = np.where(P.intrend[:, j], j, -1); E2[:, 4] = np.where(P.intrend[:, j], 1.0, 0.0)
    sim(E2, S2, '② 核心80% + 正2衛星20%(正2看自己200MA)')
    # 3 同上但正2 用 0050 的 200MA 當閘門
    c50 = P.col('0050.TW')
    S3 = S2.copy(); E3 = E2.copy()
    S3[:, 4] = np.where(P.intrend[:, c50], j, -1); E3[:, 4] = np.where(P.intrend[:, c50], 1.0, 0.0)
    sim(E3, S3, '③ 核心80% + 正2衛星20%(正2看0050的200MA)')
    # 4 正2 衛星 40%
    S4 = S2.copy(); E4 = np.zeros((n, 5)); E4[:, :4] = E * 0.6; E4[:, 4] = E2[:, 4] * 2
    sim(E4, S4, '④ 核心60% + 正2衛星40%')
    # 5 對照:不用正2,直接把核心等權加槓桿到同樣的平均曝險
    for mult in (1.2, 1.4):
        sim(E * mult, S, f'⑤ 對照:核心等權 ×{mult}(不用正2,用融資)')
    # 6 純買持正2
    Sb = np.full((n, 1), j); Eb = np.ones((n, 1))
    sim(Eb, Sb, '⑥ 對照:純買進持有正2(不擇時)')
    # 7 純正2擇時
    S7 = np.where(P.intrend[:, j], j, -1)[:, None]; E7 = np.where(P.intrend[:, j], 1.0, 0.0)[:, None]
    sim(E7, S7, '⑦ 對照:全押正2 + 200MA擇時')
    show(rows, f'D 正2(00631L)接法對照  {P.idx[0].date()}→{P.idx[-1].date()}({P.n/252:.1f}年;台股成本0.1%/邊)')
    # 逐年
    print('\n逐年報酬(%):')
    ys = {}
    for name, (E_, S_) in {'①核心等權': (E, S), '②+正2衛星20%': (E2, S2), '⑦全押正2擇時': (E7, S7)}.items():
        r = R.run_plan(P, R.Plan(S=S_, E=E_, reb=reb)).r
        ys[name] = (pd.Series(r.values, index=P.idx).groupby(P.idx.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(1)
    print(pd.DataFrame(ys).to_string())


if __name__ == '__main__':
    parts = sys.argv[1:] or ['A', 'B', 'C', 'D']
    if 'A' in parts: lever_ladder()
    if 'B' in parts: tw_candidates()
    if 'C' in parts: v2_vs_v1()
    if 'D' in parts: leveraged_tw()
