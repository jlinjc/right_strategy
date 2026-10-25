"""research_b18_two_blocks.py - 美股「每年選最強」分兩個候選區塊(Jason 2026-10-25)
========================================================================
區塊一「資深大盤/產業」(14 檔,1998 年前後就有):9 檔 SPDR 產業 + SPY/QQQ/DIA/IWM/MDY
區塊二「細分產業/主題」(~57 檔):半導體、軟體、生技、國防、銀行、能源服務、金礦…(含 SMH/SOXX/IGV/DRAM 等)
規則(事前、不偷看):每年第一個交易日,只用前一天以前的資料:
  - 候選資格:到那天為止已有 ≥756 個交易日(36 個月)資料 → 新上市的主題 ETF 滿 3 年才會進來
  - 分數:過去 36 個月報酬,取前 5
  - 區塊二另測「同一主題只取一檔」(避免 5 檔全是半導體)
持有:每檔 1/5(合併版 1/10)、各自 200MA 擇時、RiskTarget 上限 100%(不融資)、信用哨(HYG+LQD 平滑)、
  隔天開盤成交、成本 0.05%。所有版本(含固定 US5)用同一個「直接權重」模擬器,才能公平比較
  (各 ETF 上市日不同,不能用要求共同交易日的槽位引擎)。
⚠️限制:倖存者偏差——候選名單是今天還在交易的 ETF;這些年清算下市的主題 ETF 抓不到。
輸出 research_cache/b18_two_blocks.txt
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding='utf-8')
import warnings; warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
import research_rot_engine as R, research_b1_credit_canary as C1, research_q4_choices as Q4
import research_data_2026q3 as D

BLOCK1 = list(D.US_BROAD)
GROUPS = {
    '半導體': ['SMH', 'SOXX', 'XSD', 'PSI', 'FTXL', 'SOXQ', 'DRAM'],
    '軟體/網路': ['IGV', 'XSW', 'SKYY', 'CIBR', 'FDN', 'IGM'],
    '生技': ['IBB', 'XBI'], '製藥': ['XPH', 'PPH', 'IHE'], '醫材': ['IHI', 'XHE'], '醫療服務': ['XHS', 'IHF'],
    '國防航太': ['ITA', 'XAR'], '運輸': ['IYT', 'XTN', 'JETS'], '營建': ['XHB', 'ITB'], '銀行': ['KRE', 'KBE'],
    '保險': ['IAK'], '券商': ['IAI'], '油氣開採': ['XOP', 'IEO'], '油服': ['OIH', 'XES', 'IEZ'],
    '金屬礦業': ['XME', 'COPX'], '貴金屬': ['GDX', 'SIL'], '零售': ['XRT'], '電信': ['XTL', 'IYZ'],
    '潔淨能源': ['PBW', 'TAN', 'ICLN'], '鋰電': ['LIT'], '鈾': ['URA'], '不動產': ['IYR', 'VNQ'],
    '基建': ['PAVE'], '機器人': ['BOTZ', 'ROBO'], '休閒': ['PEJ'], '食品': ['PBJ'],
}
BLOCK2 = [t for g in GROUPS.values() for t in g]
GROUP_OF = {t: g for g, ts in GROUPS.items() for t in ts}
US5 = ['SMH', 'SOXX', 'QQQ', 'XLK', 'SPY']
ALL = sorted(set(BLOCK1 + BLOCK2 + US5))
COST, START = 0.0005, '2005-01-01'
OUT = []


def say(x=''):
    print(x); OUT.append(x)


def build():
    d = Q4.ensure(ALL + ['HYG', 'LQD', '^IRX'])
    cal = d['SPY'].index
    cal = cal[cal >= pd.Timestamp('1998-01-01')]
    n = len(cal); k = len(ALL)
    close = np.full((n, k), np.nan); intra = np.zeros((n, k))
    intrend = np.zeros((n, k), bool); expo = np.zeros((n, k)); nobs = np.zeros((n, k))
    for j, tk in enumerate(ALL):
        if tk not in d:
            continue
        df = d[tk]
        s = df['Close'].dropna().astype(float)
        ind = R._own_indicators(s, R.DEFAULT['buf'], R.DEFAULT['budget']).reindex(cal)
        first = s.index[0]
        live = cal >= first
        c = s.reindex(cal).ffill().where(live)
        close[:, j] = c.values
        intrend[:, j] = ind['intrend'].fillna(False).astype(bool).values & live
        expo[:, j] = np.nan_to_num(ind['expo'].values)
        nobs[:, j] = np.cumsum(live)
        x = (df['Close'] / df['Open'] - 1).replace([np.inf, -np.inf], np.nan).reindex(cal)
        x[(x.abs() > 0.2)] = np.nan
        intra[:, j] = x.fillna(0.0).values
    ret = np.zeros((n, k))
    ret[1:] = np.nan_to_num(close[1:] / close[:-1] - 1)
    irx = d['^IRX']['Close'].reindex(cal).ffill().fillna(0).values / 100.0 / 252
    h = C1.health(cal, ['HYG', 'LQD'], 'smooth')
    return cal, close, ret, intra, intrend, expo, nobs, irx, h


def select(cal, close, nobs, pool, group_once=False, N=5):
    """每年第一個交易日的選擇:回傳 {year: [tickers]}。只用 t0-1 以前的資料。"""
    idx = {t: ALL.index(t) for t in pool}
    picks = {}
    for y in sorted(set(cal.year)):
        t0 = np.where(cal.year == y)[0][0]
        if t0 < 757:
            continue
        cands = []
        for t, j in idx.items():
            if nobs[t0 - 1, j] >= 757 and not np.isnan(close[t0 - 1, j]) and not np.isnan(close[t0 - 757, j]):
                cands.append((close[t0 - 1, j] / close[t0 - 757, j] - 1, t))
        cands.sort(reverse=True)
        chosen, used = [], set()
        for sc, t in cands:
            if group_once and GROUP_OF.get(t) in used:
                continue
            chosen.append(t); used.add(GROUP_OF.get(t))
            if len(chosen) == N:
                break
        picks[y] = chosen
    return picks


def weights(cal, intrend, expo, h, blocks):
    """blocks = [(picks_by_year 或固定清單, 區塊權重)];每個區塊內每檔 = 區塊權重/5 × 在趨勢 × min(曝險,1) × 信用。"""
    W = np.zeros((len(cal), len(ALL)))
    for picks, bw in blocks:
        for t in range(len(cal)):
            lst = picks if isinstance(picks, list) else picks.get(cal[t].year, [])
            for tk in lst:
                j = ALL.index(tk)
                if intrend[t, j]:
                    W[t, j] += bw / 5 * min(expo[t, j], 1.0) * h[t]
    return W


def simulate(W, ret, intra, irx):
    """D_t = t 日收盤後決定的權重,t+1 開盤成交:
       r[u] = Σ D[u-2]·gap[u] + Σ D[u-1]·intra[u] + 現金 rf − 成本(加法近似)。"""
    gap = (1 + ret) / (1 + intra) - 1
    D1 = np.vstack([np.zeros((1, W.shape[1])), W[:-1]])      # D[u-1]
    D2 = np.vstack([np.zeros((2, W.shape[1])), W[:-2]])      # D[u-2]
    turn = np.abs(D1 - D2).sum(1)
    r = (D2 * gap).sum(1) + (D1 * intra).sum(1) + (1 - D1.sum(1)) * irx - turn * COST
    return r, turn


def main():
    cal, close, ret, intra, intrend, expo, nobs, irx, h = build()
    p1 = select(cal, close, nobs, BLOCK1)
    p2 = select(cal, close, nobs, BLOCK2)
    p2g = select(cal, close, nobs, BLOCK2, group_once=True)
    p16 = select(cal, close, nobs, BLOCK1 + ['SMH', 'SOXX'])
    V = {'A 固定 US5(現行)': [(US5, 1.0)],
         'B0 舊 16 檔(b17)': [(p16, 1.0)],
         'B1 區塊一:資深大盤/產業': [(p1, 1.0)],
         'B2 區塊二:細分產業': [(p2, 1.0)],
         'B2g 區塊二:同主題只取一檔': [(p2g, 1.0)],
         'C 合併:區塊一 50% + 區塊二(同主題一檔)50%': [(p1, 0.5), (p2g, 0.5)]}
    m0 = cal >= pd.Timestamp(START)
    rf = irx[m0]
    res = {}
    for k, blocks in V.items():
        r, turn = simulate(weights(cal, intrend, expo, h, blocks), ret, intra, irx)
        res[k] = (pd.Series(r[m0], index=cal[m0]), turn[m0])
    base = res['A 固定 US5(現行)'][0]
    IDX = R.sb_indices(len(base), B=2000, mean_block=63, seed=0)
    say(f'期間 {cal[m0][0].date()} → {cal[-1].date()};不融資、信用哨、隔天開盤成交、同一模擬器\n')
    for k, (r, turn) in res.items():
        m = R.metrics(r, rf)
        yr = r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1) * 100
        extra = ''
        if not k.startswith('A '):
            bs = R.boot_delta_sharpe(r.values, base.values, rf, IDX)
            extra = f"  | vs 固定US5 Δ超額Sharpe {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}]"
        say(f"{k:34s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}  "
            f"最差年 {yr.min():6.1f}%  年換手 {turn.sum() / (len(r) / 252):.1f}x{extra}")
    say('\n每年選中(區塊一 | 區塊二 同主題一檔 | 區塊二 不限主題):')
    for y in sorted(p1):
        if y < 2005:
            continue
        say(f"  {y}: {', '.join(p1[y])} | {', '.join(p2g.get(y, []))} | {', '.join(p2.get(y, []))}")


if __name__ == '__main__':
    main()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b18_two_blocks.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n')


# ── 補充(2026-10-25,探索性):36 個月可能是錯的「最強」定義(學術上 3~5 年報酬傾向反轉、12 個月動能才持續)──
def select_mom(cal, close, nobs, pool, look=252, skip=21, every='Y', group_once=False, N=5):
    """過去 look 日報酬(跳過最近 skip 日),每年(Y)或每季(Q)第一個交易日重選;資格同上(≥757 日)。"""
    idx = {t: ALL.index(t) for t in pool}
    picks = {}
    keyf = (lambda d: d.year) if every == 'Y' else (lambda d: (d.year, (d.month - 1) // 3))
    keys = pd.Index([keyf(d) for d in cal])
    for key in pd.unique(keys):
        t0 = np.where(keys == key)[0][0]
        if t0 < 757:
            continue
        cands = []
        for t, j in idx.items():
            a, b = close[t0 - 1 - skip, j], close[t0 - 1 - look, j]
            if nobs[t0 - 1, j] >= 757 and not np.isnan(a) and not np.isnan(b):
                cands.append((a / b - 1, t))
        cands.sort(reverse=True)
        chosen, used = [], set()
        for sc, t in cands:
            if group_once and GROUP_OF.get(t) in used:
                continue
            chosen.append(t); used.add(GROUP_OF.get(t))
            if len(chosen) == N:
                break
        picks[key] = chosen
    return picks, keyf


def weights_k(cal, intrend, expo, h, blocks):
    W = np.zeros((len(cal), len(ALL)))
    for picks, keyf, bw in blocks:
        for t in range(len(cal)):
            lst = picks if isinstance(picks, list) else picks.get(keyf(cal[t]), [])
            n = 5 if not isinstance(picks, list) else len(lst)
            for tk in lst:
                j = ALL.index(tk)
                if intrend[t, j]:
                    W[t, j] += bw / n * min(expo[t, j], 1.0) * h[t]
    return W


def extra():
    cal, close, ret, intra, intrend, expo, nobs, irx, h = build()
    Y = lambda d: d.year
    V = {'A 固定 US5(現行)': [(US5, Y, 1.0)],
         'Z1 區塊一 14 檔全拿(不挑)': [(BLOCK1, Y, 1.0)],
         'Z2 只持有 SPY': [(['SPY'], Y, 1.0)],
         'Z3 只持有 QQQ': [(['QQQ'], Y, 1.0)]}
    for nm, pool, g in (('區塊一', BLOCK1, False), ('區塊二同主題一檔', BLOCK2, True)):
        for every in ('Y', 'Q'):
            pk, kf = select_mom(cal, close, nobs, pool, every=every, group_once=g)
            V[f"M {nm}:12-1月動能、每{'年' if every == 'Y' else '季'}重選"] = [(pk, kf, 1.0)]
    m0 = cal >= pd.Timestamp(START); rf = irx[m0]
    res = {}
    for k, blocks in V.items():
        r, turn = simulate(weights_k(cal, intrend, expo, h, blocks), ret, intra, irx)
        res[k] = (pd.Series(r[m0], index=cal[m0]), turn[m0])
    base = res['A 固定 US5(現行)'][0]
    IDX = R.sb_indices(len(base), B=2000, mean_block=63, seed=0)
    say('\n[補充:對照組 + 12-1 月動能(探索性,非事前登錄)]')
    for k, (r, turn) in res.items():
        m = R.metrics(r, rf)
        ex = ''
        if not k.startswith('A '):
            bs = R.boot_delta_sharpe(r.values, base.values, rf, IDX)
            ex = f"  | vs 固定US5 Δ {bs['d']:+.3f} [{bs['lo']:+.2f},{bs['hi']:+.2f}]"
        say(f"{k:36s} CAGR {m['CAGR']:5.1f}%  MDD {m['MDD']:6.1f}%  超額Sharpe {m['ShEx']:.2f}  年換手 {turn.sum() / (len(r) / 252):.1f}x{ex}")


if __name__ == '__main__':
    extra()
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'research_cache', 'b18_two_blocks.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(OUT) + '\n')
