"""research_b12_us_filters.py - 三個美股新濾網:市場寬度 / 殖利率曲線 / 雙動能
========================================================================
問題:加在現行美股系統(5檔等權 + 200MA + 信用哨 + RiskTarget)「之上」,還有沒有額外價值?

★事前判準(寫死後才跑,不准事後改)——要「採用」某濾網,必須全部通過:
  G1 效果:ΔSharpe ≥ +0.05,或 最大回撤改善 ≥ 3 個百分點且 ΔSharpe ≥ 0
  G2 顯著:配對 stationary block bootstrap(區塊63日,2000次)單尾 p,經 Holm(家族=本檔全部主設定)後 < 0.10
  G3 分段:2002-09 / 2010-16 / 2017-26 三段中至少兩段 ΔSharpe > 0,且沒有任何一段 < −0.10
  G4 參數鄰域:同一濾網的相鄰參數方向一致(不能只有單一參數好)
  G5 無後見之明池:在 14 檔產業池(BROAD,同一套系統)上 ΔSharpe ≥ 0
  G6 執行性:每年交易筆數增加 > 50% 只標註,不影響判定
主設定(進 Holm 家族):寬度(產業)<50%→0、殖利率倒掛→0、倒掛後轉正(12個月內)→0、
                      大盤雙動能(SPY 12M<國庫券)→0、逐檔雙動能(該檔 12M<國庫券)→0
參數鄰域(只看 G4):寬度門檻 40/60%、×0.5 減半版、雙動能 6 個月版
參考(不判定):用今天 500 檔成分股算的寬度(有生存者偏差)
用法: python research_b12_us_filters.py
"""
import sys, pickle, warnings
warnings.filterwarnings('ignore')
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import numpy as np, pandas as pd, yfinance as yf
import research_rot_engine as R
import research_b1_credit_canary as C1

B = 2000
SECTORS = ['XLB', 'XLE', 'XLF', 'XLI', 'XLK', 'XLP', 'XLU', 'XLV', 'XLY']
SEGS = [('S1', None, '2009-12-31'), ('S2', '2010-01-01', '2016-12-31'), ('S3', '2017-01-01', None)]


def series_on(idx, s, warm_ok=1.0):
    return s.reindex(idx).ffill().fillna(warm_ok).values


def breadth_sector(idx):
    d = R.data(); oks = []
    for tk in SECTORS:
        c = d[tk]['Close'].dropna(); oks.append((c >= c.rolling(200).mean()).astype(float))
    b = pd.concat(oks, axis=1).mean(axis=1)
    return b.reindex(idx).ffill().values


def breadth_stocks(idx):
    try:
        dd = pickle.load(open('research_cache/sp500_ohlc.pkl', 'rb'))
    except Exception:
        return None
    C = pd.DataFrame({t: v['Close'] for t, v in dd.items() if t not in ('SPY', 'QQQ')})
    ma = C.rolling(200).mean()
    b = (C >= ma).sum(1) / ma.notna().sum(1).clip(lower=1)
    b[ma.notna().sum(1) < 100] = np.nan
    return b.reindex(idx).ffill().values


def yield_curve(idx):
    tnx = yf.download('^TNX', start='1995-01-01', progress=False, auto_adjust=True)['Close'].squeeze()
    irx = R.data()['^IRX']['Close']
    spread = (tnx - irx).dropna()
    inv = (spread < 0).astype(float)
    # 倒掛後轉正:過去 252 日內曾倒掛,且現在已轉正
    was_inv = inv.rolling(252, min_periods=1).max()
    uninv = ((was_inv > 0) & (spread >= 0)).astype(float)
    return series_on(idx, 1 - inv), series_on(idx, 1 - uninv), spread


def dual_mom(P, months=12, level='market'):
    n = 21 * months
    d = R.data()
    irx = d['^IRX']['Close'].reindex(P.idx).ffill().fillna(0) / 100
    tb = irx * months / 12                          # 同期國庫券報酬(近似)
    if level == 'market':
        spy = d['SPY']['Close'].reindex(P.idx).ffill()
        ok = ((spy / spy.shift(n) - 1) > tb).astype(float)
        ok[spy.shift(n).isna()] = 1.0
        return np.repeat(ok.values[:, None], P.k, axis=1)
    out = np.ones((P.n, P.k))
    for j in range(P.k):
        c = pd.Series(P.close[:, j], index=P.idx)
        ok = ((c / c.shift(n) - 1) > tb).astype(float); ok[c.shift(n).isna()] = 1.0
        out[:, j] = ok.values
    return out


def build_filters(P):
    F = {}
    bs = breadth_sector(P.idx)
    for th in (0.4, 0.5, 0.6):
        F[f'寬度(產業)<{int(th*100)}%→0'] = np.where(np.nan_to_num(bs, nan=1) < th, 0.0, 1.0)
    F['寬度(產業)<50%→×0.5'] = np.where(np.nan_to_num(bs, nan=1) < 0.5, 0.5, 1.0)
    inv, uninv, _ = yield_curve(P.idx)
    F['殖利率倒掛→0'] = inv
    F['殖利率倒掛→×0.5'] = np.where(inv < 1, 0.5, 1.0)
    F['倒掛後轉正(12月內)→0'] = uninv
    F['大盤雙動能12M→0'] = dual_mom(P, 12, 'market')
    F['大盤雙動能6M→0'] = dual_mom(P, 6, 'market')
    F['逐檔雙動能12M→0'] = dual_mom(P, 12, 'asset')
    F['逐檔雙動能6M→0'] = dual_mom(P, 6, 'asset')
    st = breadth_stocks(P.idx)
    if st is not None:
        F['(參考)寬度(500檔·有偏差)<50%→0'] = np.where(np.nan_to_num(st, nan=1) < 0.5, 0.0, 1.0)
    return F


MAIN = ['寬度(產業)<50%→0', '殖利率倒掛→0', '倒掛後轉正(12月內)→0', '大盤雙動能12M→0', '逐檔雙動能12M→0']
NEIGH = {'寬度(產業)<50%→0': ['寬度(產業)<40%→0', '寬度(產業)<60%→0', '寬度(產業)<50%→×0.5'],
         '殖利率倒掛→0': ['殖利率倒掛→×0.5'], '倒掛後轉正(12月內)→0': [],
         '大盤雙動能12M→0': ['大盤雙動能6M→0'], '逐檔雙動能12M→0': ['逐檔雙動能6M→0']}


def run_pool(pool, sizing='rt'):
    P = R.build_panel(pool)
    nore = np.zeros(P.n, dtype=bool)
    ew = R.ew_plan(P, reb=nore, sizing=sizing)
    h = C1.health(P.idx, ['HYG', 'LQD'], 'smooth')[:, None]
    base = R.run_plan(P, R.Plan(S=ew.S, E=ew.E * h, reb=nore))
    F = build_filters(P)
    out = {}
    for k, f in F.items():
        f2 = f if f.ndim == 2 else f[:, None]
        out[k] = R.run_plan(P, R.Plan(S=ew.S, E=ew.E * h * f2, reb=nore))
    return P, base, out


def evaluate(P, base, res, IDX):
    mb = R.metrics(base.r, P.rf, res=base)
    rows = {}
    for k, r in res.items():
        m = R.metrics(r.r, P.rf, res=r)
        bs = R.boot_delta_sharpe(r.r, base.r, P.rf, IDX)
        segd = []
        for sn, a, b in SEGS:
            x, y = R.sub(r.r, P.idx, a, b), R.sub(base.r, P.idx, a, b)
            rfs = pd.Series(P.rf, index=P.idx).reindex(x.index).values
            segd.append(R.sharpe_ex(x.values, rfs) - R.sharpe_ex(y.values, rfs))
        rows[k] = {'濾網': k, 'CAGR%': round(m['CAGR'], 1), 'MDD%': round(m['MDD'], 1), 'Sharpe': round(m['ShEx'], 2),
                   'ΔSharpe': round(bs['d'], 3), 'p': round(bs['p'], 3), 'ΔMDD': round(m['MDD'] - mb['MDD'], 1),
                   '分段Δ': ' '.join(f'{x:+.2f}' for x in segd), '筆/年': round(m['Trades/yr'], 0),
                   '_segs': segd, '_d': bs['d'], '_dmdd': m['MDD'] - mb['MDD'], '_tr': m['Trades/yr'] / max(mb['Trades/yr'], 1)}
    return mb, rows


def holm(pv):
    items = sorted(pv.items(), key=lambda x: x[1]); m, run, out = len(items), 0.0, {}
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p)); out[k] = run
    return out


def main():
    P, base, res = run_pool('US5')
    IDX = R.sb_indices(P.n, B=B, mean_block=63, seed=0)
    mb, rows = evaluate(P, base, res, IDX)
    print(f'美股 5 檔 {P.idx[0].date()}→{P.idx[-1].date()}  基礎(等權+200MA+信用哨):'
          f'CAGR {mb["CAGR"]:.1f}%  MDD {mb["MDD"]:.1f}%  Sharpe {mb["ShEx"]:.2f}  {mb["Trades/yr"]:.0f}筆/年')
    df = pd.DataFrame([{k: v for k, v in r.items() if not k.startswith('_')} for r in rows.values()])
    print(df.to_string(index=False))
    # BROAD 無後見之明檢查(只跑主設定)
    Pb, baseb, resb = run_pool('BROAD', sizing='binary')
    IDXb = R.sb_indices(Pb.n, B=B, mean_block=63, seed=0)
    _, rowsb = evaluate(Pb, baseb, {k: resb[k] for k in MAIN}, IDXb)
    print('\nBROAD 14檔(無後見之明)上的 ΔSharpe:', {k: rowsb[k]['ΔSharpe'] for k in MAIN})
    hol = holm({k: rows[k]['p'] for k in MAIN})
    print('\n' + '=' * 100 + '\n事前判準檢核\n' + '=' * 100)
    for k in MAIN:
        r = rows[k]
        g1 = r['_d'] >= 0.05 or (r['_dmdd'] >= 3 and r['_d'] >= 0)
        g2 = hol[k] < 0.10
        g3 = sum(x > 0 for x in r['_segs']) >= 2 and min(r['_segs']) >= -0.10
        g4 = all(np.sign(rows[n]['_d']) == np.sign(r['_d']) for n in NEIGH[k]) if NEIGH[k] else None
        g5 = rowsb[k]['_d'] >= 0
        g6 = '⚠️交易+50%以上' if r['_tr'] > 1.5 else ''
        ok = g1 and g2 and g3 and (g4 is not False) and g5
        mark = lambda b: 'N/A' if b is None else ('✔' if b else '✘')
        print(f"  {k:22s} G1{mark(g1)} G2{mark(g2)}(Holm p={hol[k]:.3f}) G3{mark(g3)} G4{mark(g4)} G5{mark(g5)} {g6}"
              f" → {'✅ 採用' if ok else '❌ 不採用'}")


if __name__ == '__main__':
    main()
