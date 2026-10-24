"""research_b9_final.py - 待決策項目的證據 + 剩下三項待辦
========================================================================
A 待決策:每一項在統一基準下(含信用哨、美股RiskTarget/台股固定1倍、不再平衡)算配對 bootstrap p 值
   (B=2000,恢復完整規格)。槓桿則補「最差12個月」「最長水下期」等風險數字(Sharpe 無法分出高下)。
B 待辦:XLK 的 credit_k(程式裡定義了但從沒被用到)→ 實作出來測 k=1.00/0.99/0.98/0.97
C 待辦:事前選池——每年用「過去一年日均成交金額」挑 16 檔候選中最流動的 5 檔(最受市場認可=事前可知)
D 待辦:Deflated Sharpe(Bailey & López de Prado 2014)套在「等權 − 單押」的主動報酬上,
   試驗次數 = 該池第二段驗證跑過的候選數
用法: python research_b9_final.py
"""
import sys, json, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd
from scipy.stats import norm, skew, kurtosis
import research_rot_engine as R
import research_b1_credit_canary as C1
import research_data_2026q3 as D

B = 2000
NORE = None


def credit_h(P, tks, mode='smooth'):
    return C1.health(P.idx, tks, mode)[:, None]


def plans(P, sizing, ctks, single, mode='smooth', pool_drop=None):
    nore = np.zeros(P.n, dtype=bool)
    h = credit_h(P, ctks, mode)
    out = {}
    ew = R.ew_plan(P, reb=nore, sizing=sizing)
    out['ew'] = R.Plan(S=ew.S, E=ew.E * h, reb=nore)
    rot = R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock', sizing=sizing)
    out['rotation'] = R.Plan(S=rot.S, E=rot.E * h, reb=nore)
    roth = R.rotation_plan(P, elig='intrend', N=1, lock=21, switch='lock', sizing=sizing)
    out['rotation_hold'] = R.Plan(S=roth.S, E=roth.E * h, reb=nore)
    j = P.col(single)
    S = np.where(P.intrend[:, j], j, -1)[:, None]
    E = (S >= 0).astype(float) * (P.expo_rt[:, [j]] if sizing == 'rt' else 1.0) * h
    out['single'] = R.Plan(S=S, E=E, reb=nore)
    return out


def pair(P, ra, rb, IDX):
    bs = R.boot_delta_sharpe(ra, rb, P.rf, IDX)
    return f"Δ{bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}] p={bs['p']:.3f}"


def risk_stats(r):
    eq = np.cumprod(1 + np.asarray(r))
    dd = eq / np.maximum.accumulate(eq) - 1
    uw, longest = 0, 0
    for x in dd:
        uw = uw + 1 if x < 0 else 0
        longest = max(longest, uw)
    roll = pd.Series(eq).pct_change(252).min() * 100
    return round(roll, 1), round(longest / 252, 1)


def part_a():
    print('=' * 110 + '\nA 待決策項目的證據(統一基準;p = 配對 bootstrap,B=2000)\n' + '=' * 110)
    # 美股
    P = R.build_panel('US5'); IDX = R.sb_indices(P.n, B=B, mean_block=63, seed=0)
    pl = plans(P, 'rt', ['HYG', 'LQD'], 'QQQ')
    rr = {k: R.run_plan(P, v).r for k, v in pl.items()}
    print(f'\n[美股 {P.idx[0].date()}~]')
    print('  等權 − 單押最強 :', pair(P, rr['ew'], rr['rotation'], IDX))
    print('  等權 − 只持有QQQ:', pair(P, rr['ew'], rr['single'], IDX))
    print('  單押 − 只持有QQQ:', pair(P, rr['rotation'], rr['single'], IDX))
    plw = plans(P, 'rt', ['HYG', 'LQD'], 'QQQ', mode='binary')
    rw = R.run_plan(P, plw['ew']).r
    print('  信用哨「皆壞才清倉」− 「平滑」(等權上):', pair(P, rw, rr['ew'], IDX))
    print('  停損線~200MA 之間:續抱 − 換掉(只對單押有意義):', pair(P, rr['rotation_hold'], rr['rotation'], IDX))
    # 槓桿風險數字
    print('\n  槓桿(美股等權):')
    for k in (1.0, 1.25):
        e = pl['ew']; r = R.run_plan(P, R.Plan(S=e.S, E=e.E * k, reb=e.reb)).r
        m = R.metrics(r, P.rf); w12, uw = risk_stats(r)
        print(f'    ×{k}: CAGR {m["CAGR"]:.1f}%  回撤 {m["MDD"]:.1f}%  最差12個月 {w12}%  最長水下 {uw} 年  Sharpe {m["ShEx"]:.2f}')
    # 台股
    for pool, tks, lab in [('TW4', ['0050.TW', '0052.TW', '006208.TW', '0051.TW'], '台股主池 2013~'),
                           ('TW4', ['0050.TW', '0052.TW', '0051.TW'], '台股長樣本 2009~(無006208)')]:
        P = R.build_panel(pool, tickers=tks); IDX = R.sb_indices(P.n, B=B, mean_block=63, seed=0)
        pl = plans(P, 'binary', ['HYG', 'LQD', 'SOXX'], '0050.TW')
        rr = {k: R.run_plan(P, v).r for k, v in pl.items()}
        print(f'\n[{lab} {P.idx[0].date()}~]')
        print('  等權 − 單押最強 :', pair(P, rr['ew'], rr['rotation'], IDX))
        print('  等權 − 只持有0050:', pair(P, rr['ew'], rr['single'], IDX))
        print('  停損線~200MA 之間:續抱 − 換掉:', pair(P, rr['rotation_hold'], rr['rotation'], IDX))
        if len(tks) == 4:
            P3 = R.build_panel(pool, tickers=['0050.TW', '0052.TW', '0051.TW'], start=str(P.idx[0].date()))
            r3 = R.run_plan(P3, plans(P3, 'binary', ['HYG', 'LQD', 'SOXX'], '0050.TW')['ew']).r
            n = min(len(r3), len(rr['ew'])); I2 = R.sb_indices(n, B=B, mean_block=63, seed=0)
            bs = R.boot_delta_sharpe(rr['ew'].values[-n:], r3.values[-n:], P.rf[-n:], I2)
            print(f"  4檔等權 − 3檔等權: Δ{bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}] p={bs['p']:.3f}")
            print('\n  槓桿(台股4檔等權):')
            for k in (1.0, 1.25):
                e = pl['ew']; r = R.run_plan(P, R.Plan(S=e.S, E=e.E * k, reb=e.reb)).r
                m = R.metrics(r, P.rf); w12, uw = risk_stats(r)
                print(f'    ×{k}: CAGR {m["CAGR"]:.1f}%  回撤 {m["MDD"]:.1f}%  最差12個月 {w12}%  最長水下 {uw} 年  Sharpe {m["ShEx"]:.2f}')


def part_b():
    print('\n' + '=' * 110 + '\nB XLK 的 credit_k:實作「XLK 只在 HYG < 200MA×k 才算信用壞」(其他4檔不變)\n' + '=' * 110)
    P = R.build_panel('US5'); IDX = R.sb_indices(P.n, B=B, mean_block=63, seed=0)
    d = R.data()
    hy = d['HYG']['Close'].dropna(); lq = d['LQD']['Close'].dropna()
    base_h = C1.health(P.idx, ['HYG', 'LQD'], 'smooth')
    nore = np.zeros(P.n, dtype=bool)
    ew = R.ew_plan(P, reb=nore, sizing='rt')
    jx = P.col('XLK')
    res = {}
    for k in (1.00, 0.99, 0.98, 0.97):
        okh = (hy >= hy.rolling(200).mean() * k).astype(float).reindex(P.idx).ffill().fillna(1).values
        okl = (lq >= lq.rolling(200).mean() * k).astype(float).reindex(P.idx).ffill().fillna(1).values
        H = np.repeat(base_h[:, None], P.k, axis=1)
        H[:, jx] = (okh + okl) / 2
        res[k] = R.run_plan(P, R.Plan(S=ew.S, E=ew.E * H, reb=nore)).r
    for k in (0.99, 0.98, 0.97):
        print(f'  k={k:.2f}(現行註解宣稱 0.98)− k=1.00(實際在跑的):', pair(P, res[k], res[1.00], IDX))


def part_c():
    print('\n' + '=' * 110 + '\nC 事前選池:每年用過去一年「日均成交金額」挑 16 檔中最流動的 5 檔\n' + '=' * 110)
    import research_b4_pool_hindsight as B4
    cand = B4.CAND
    P = R.build_panel('BROAD', tickers=cand)
    d = D.load()
    dv = pd.DataFrame({tk: (d[tk]['Close'] * d[tk]['Volume']) for tk in cand}).reindex(P.idx)
    m = np.zeros((P.n, P.k), dtype=bool)
    yrs = P.idx.year
    for y in sorted(set(yrs)):
        ii = np.where(yrs == y)[0]; t0 = ii[0]
        if t0 < 252: continue
        avg = dv.iloc[t0 - 252:t0].mean().values
        m[np.ix_(ii, np.argsort(-avg)[:5])] = True
    start = P.idx[756]
    us5 = np.isin(np.array(P.tickers), B4.US5)[None, :].repeat(P.n, 0)
    series = {'US5(事後挑)': B4.sim(P, B4.pool_w(P, us5)),
              '事前:過去36月報酬前5': B4.sim(P, B4.pool_w(P, B4.exante_mask(P, 'ret'))),
              '事前:流動性前5': B4.sim(P, B4.pool_w(P, m))}
    IDX = R.sb_indices(int((P.idx >= start).sum()), B=B, mean_block=63, seed=0)
    base = R.sub(series['US5(事後挑)'], P.idx, str(start.date()))
    rf = pd.Series(P.rf, index=P.idx).reindex(base.index).values
    for k, s in series.items():
        x = R.sub(s, P.idx, str(start.date())); mm = R.metrics(x, rf)
        extra = '' if k.startswith('US5') else '  ' + pair(type('P', (), {'rf': rf})(), base.values, x.values, IDX).replace('Δ', 'US5領先Δ')
        print(f'  {k:18s} CAGR {mm["CAGR"]:5.1f}%  Sharpe {mm["ShEx"]:.2f}  回撤 {mm["MDD"]:.1f}%{extra}')
    picks = {y: [P.tickers[j] for j in np.where(m[np.where(yrs == y)[0][0]])[0]] for y in (2010, 2015, 2020, 2025)}
    print('  流動性前5 的池子(抽樣年份):', picks)


def dsr(active, n_trials, trial_sr_var):
    """Deflated Sharpe(每期單位)。active=主動報酬序列;trial_sr_var=試驗 Sharpe(每期)的變異數。"""
    x = np.asarray(active); T = len(x)
    sr = x.mean() / x.std(ddof=1)
    g3, g4 = skew(x), kurtosis(x, fisher=False)
    em = 0.5772156649
    sr0 = np.sqrt(trial_sr_var) * ((1 - em) * norm.ppf(1 - 1 / n_trials) + em * norm.ppf(1 - 1 / (n_trials * np.e)))
    z = (sr - sr0) * np.sqrt(T - 1) / np.sqrt(1 - g3 * sr + (g4 - 1) / 4 * sr ** 2)
    return float(norm.cdf(z)), sr * np.sqrt(252), sr0 * np.sqrt(252)


def part_d():
    print('\n' + '=' * 110 + '\nD Deflated Sharpe:等權 − 單押 的主動報酬,扣掉「試了很多種才挑到」的運氣\n' + '=' * 110)
    for pool, f in [('US5', 'rot_p2_US5_0.0.json'), ('TW4', 'rot_p2_TW4_0.0.json'), ('TW3L', None), ('BROAD', None)]:
        P = R.build_panel(pool)
        rot = R.run_plan(P, R.rotation_plan(P, elig='above', N=1, lock=21, switch='lock')).r
        ew = R.run_plan(P, R.ew_plan(P, reb=R.reb_days(P.idx, 0))).r
        # 規格 §3.4.5:先把兩者調到同一波動(以單押的波動為準)再相減,否則只量到「報酬差」,
        #   漏掉等權的主要優勢(較低波動)。初版直接相減 → TW4 IR≈0,是量錯了東西。
        sig = np.asarray(rot).std(ddof=1) * np.sqrt(252)
        ew_v, _ = R.scale_to_vol(ew.values, P.rf, sig)
        rot_v, _ = R.scale_to_vol(rot.values, P.rf, sig)
        active = ew_v - rot_v
        trials = []
        try:
            j = json.load(open('research_cache/' + (f or f'rot_p2_{pool}_0.0.json'), encoding='utf-8'))
            for grp in ('b1', 'others'):
                for row in j[grp]:
                    v = row.get('ΔShEx')
                    if v is not None and v == v: trials.append(v / np.sqrt(252))
        except Exception:
            pass
        n = max(len(trials) + 1, 2)
        var = np.var(trials, ddof=1) if len(trials) > 2 else (0.1 / np.sqrt(252)) ** 2
        p, sr_a, sr0_a = dsr(active, n, var)
        print(f'  {pool:6s} 主動報酬年化 IR {sr_a:+.2f} | 運氣門檻(試驗{n}次)SR0 {sr0_a:.2f} | DSR = {p:.3f}'
              f"(門檻:美股≥0.95/台股≥0.90){'  ✔' if p >= (0.95 if pool in ('US5', 'BROAD') else 0.90) else '  ✘'}")


if __name__ == '__main__':
    parts = sys.argv[1:] or ['A', 'B', 'C', 'D']
    for k, f in (('A', part_a), ('B', part_b), ('C', part_c), ('D', part_d)):
        if k in parts: f()
