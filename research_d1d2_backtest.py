"""
research_d1d2_backtest.py — 動手前關鍵驗證:D1(V底回補)+ D2(跌破觀察)vs 現行 live
========================================================================
診斷頁點出 D1/D2 兩缺陷。改的是『出場/進場核心判斷』,不能只憑事後好看就上。
本測回答三件事(誠實尺,訊號 shift(1),跨 SMH/QQQ/SPY):
  1. 救回多少報酬 / 少洗幾次(Sharpe/CAGR/MDD/換手/平均曝險)。
  2. ★D1 會不會在 2022 熊市反彈陷阱誤觸把你送回下跌 → 逐筆列出每次 D1 回補的下場。
  3. 拆 COVID2020 / 2022熊 / 2018Q4 / 2025關稅 各崩盤窗,看修改在真崩盤裡的行為。

四個變體(sizing 公式完全相同,只差『何時在場』→ 隔離 timing 效果):
  BASE   = 現行 live:站上200MA進、跌破×buf出(VIX>30且跌破<3日給恐慌容忍)、信用全壞出、RiskTarget×健康
  +D2    = 跌破需連續 CONFIRM 日確認才出(把恐慌容忍推廣到所有跌破);信用全壞仍立即出
  +D1    = 額外快速回補:現金中若『指數收復200MA + VIX近20日曾>30且正回落』→即使信用仍全壞也回補
  +D1D2  = 兩者都上
"""
import sys, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, yfinance as yf
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
import core_status as C

TICKERS = ['SMH', 'QQQ', 'SPY']
CONFIRM = 3          # D2:跌破需連續幾日確認
VSPIKE_LB = 20       # D1:近幾日內曾恐慌
VFALL_LB = 5         # D1:VIX 較幾日前回落
START = '2006-01-01'
WINDOWS = {
    'COVID 2020':   ('2020-02-15', '2020-09-01'),
    '2022 熊':      ('2022-01-01', '2023-01-31'),
    '2018Q4':       ('2018-09-15', '2019-04-30'),
    '2025 關稅':    ('2025-02-01', '2025-08-31'),
    '2026 春崩':    ('2026-02-01', '2026-07-15'),
}


def dl(sym):
    s = yf.download(sym, start=START, auto_adjust=True, progress=False)['Close'].dropna()
    return s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s


def perf(r):
    r = r.dropna()
    if len(r) < 20: return {}
    mu, sd = r.mean(), r.std()
    sh = mu / sd * np.sqrt(252) if sd > 0 else np.nan
    eq = (1 + r).cumprod()
    mdd = (eq / eq.cummax() - 1).min()
    cagr = eq.iloc[-1] ** (252 / len(r)) - 1
    return {'sharpe': sh, 'cagr': cagr * 100, 'mdd': mdd * 100}


def run_variant(close, ma200, exit_line, health, vix, budget, cap, floor, variant):
    """回傳 (expo Series, exits 次數, d1_reentries list[(date,idx)])。expo=當日目標曝險(未 shift)。"""
    n = len(close)
    idx = close.index
    below = (close < exit_line).values
    days_below = np.zeros(n, int); run = 0
    for t in range(n):
        run = run + 1 if below[t] else 0
        days_below[t] = run
    cl = close.values; ma = ma200.values; el = exit_line.values
    h = health.values; vx = vix.values
    expo = np.zeros(n); in_pos = False; last_base = 0.0
    exits = 0; d1_re = []
    for t in range(n):
        credit_off = h[t] <= 0
        reclaim = cl[t] >= ma[t]
        panic = (not np.isnan(vx[t])) and vx[t] > C.PANIC_VIX
        if not in_pos:
            entered = False
            if reclaim and not credit_off:
                in_pos = True; entered = True
            elif variant in ('D1', 'D1D2') and reclaim and credit_off:
                vspk = np.nanmax(vx[max(0, t - VSPIKE_LB):t + 1]) if t > 0 else vx[t]
                vfall = t >= VFALL_LB and not np.isnan(vx[t]) and not np.isnan(vx[t - VFALL_LB]) and vx[t] < vx[t - VFALL_LB]
                if (not np.isnan(vspk)) and vspk > C.PANIC_VIX and vfall:
                    in_pos = True; entered = True
                    d1_re.append((idx[t], t))
        else:
            if credit_off:
                in_pos = False; exits += 1
            elif below[t]:
                if variant in ('D2', 'D1D2'):
                    if days_below[t] >= CONFIRM:
                        in_pos = False; exits += 1
                else:  # BASE / D1:原恐慌容忍(VIX>30且<PANIC_DELAY日才容忍)
                    if not (panic and days_below[t] < C.PANIC_DELAY):
                        in_pos = False; exits += 1
        # 曝險
        if in_pos:
            if reclaim:
                sd = (cl[t] - el[t]) / cl[t]
                last_base = min(budget / max(sd, floor), cap)
                expo[t] = last_base * h[t]
            else:  # 容忍期在線下:凍結 base × 當前健康
                expo[t] = last_base * h[t]
    return pd.Series(expo, index=idx), exits, d1_re


def analyze(tk, close, health, vix):
    p = C.PARAMS.get(tk, C.DEFAULT_PARAM)
    budget, cap, buf = p['budget'], p['cap'], p['exit_buf']
    floor = C.STOP_DIST_FLOOR
    ma200 = close.rolling(C.MA).mean()
    exit_line = ma200 * buf
    ret = close.pct_change()
    df = pd.concat([close.rename('c'), ma200.rename('ma'), exit_line.rename('el'),
                    health.rename('h'), vix.rename('v'), ret.rename('r')], axis=1).dropna()
    variants = {}
    d1info = {}
    for v in ['BASE', 'D2', 'D1', 'D1D2']:
        expo, exits, d1_re = run_variant(df['c'], df['ma'], df['el'], df['h'], df['v'],
                                         budget, cap, floor, v)
        sret = (expo.shift(1) * df['r']).dropna()          # 誠實尺:昨收決定的曝險 × 今報酬
        variants[v] = {'ret': sret, 'expo': expo, 'exits': exits, 'avg_expo': expo.mean()}
        d1info[v] = d1_re
    bh = df['r']                                            # 買持

    print("=" * 92)
    print(f"  {tk}   {df.index[0].date()}→{df.index[-1].date()}   (entry_buf×{buf}, budget {budget}, cap {cap})")
    print("=" * 92)
    print(f"  {'變體':8}{'Sharpe':>8}{'CAGR':>9}{'MDD':>9}{'平均曝險':>10}{'出場次數':>9}")
    base_stats = None
    for v in ['BASE', 'D2', 'D1', 'D1D2']:
        st = perf(variants[v]['ret'])
        if v == 'BASE': base_stats = st
        tag = ''
        if v != 'BASE' and base_stats:
            ds = st['sharpe'] - base_stats['sharpe']
            tag = f"  (ΔSharpe {ds:+.3f})"
        print(f"  {v:8}{st['sharpe']:>8.3f}{st['cagr']:>8.1f}%{st['mdd']:>8.1f}%"
              f"{variants[v]['avg_expo']*100:>9.0f}%{variants[v]['exits']:>9d}{tag}")
    bhp = perf(bh)
    print(f"  {'買持':8}{bhp['sharpe']:>8.3f}{bhp['cagr']:>8.1f}%{bhp['mdd']:>8.1f}%")

    # 崩盤窗
    print(f"\n  [崩盤窗報酬%] (各變體 vs 買持)")
    print(f"  {'窗':12}{'BASE':>9}{'+D2':>9}{'+D1':>9}{'+D1D2':>9}{'買持':>9}")
    for name, (a, b) in WINDOWS.items():
        cells = []
        for v in ['BASE', 'D2', 'D1', 'D1D2']:
            seg = variants[v]['ret'].loc[a:b]
            cells.append((1 + seg).prod() - 1 if len(seg) else np.nan)
        segbh = bh.loc[a:b]
        bhw = (1 + segbh).prod() - 1 if len(segbh) else np.nan
        if all(np.isnan(x) for x in cells): continue
        print(f"  {name:12}" + "".join(f"{x*100:>8.1f}%" if not np.isnan(x) else f"{'—':>9}" for x in cells)
              + (f"{bhw*100:>8.1f}%" if not np.isnan(bhw) else f"{'—':>9}"))

    # ★D1 逐筆回補下場(baseline 不會做的那些=信用仍全壞時回補)
    d1_re = d1info['D1']
    print(f"\n  ★D1 快速回補逐筆({len(d1_re)} 次;僅列信用仍全壞時的『搶跑回補』):")
    if not d1_re:
        print("     (無)")
    else:
        c = df['c']; el = df['el']
        by_year = {}
        for dt, t in d1_re:
            by_year.setdefault(dt.year, 0); by_year[dt.year] += 1
        print("     年份分布:", ", ".join(f"{y}:{c_}" for y, c_ in sorted(by_year.items())))
        print(f"     {'回補日':12}{'之後21d':>9}{'之後63d':>9}{'20日內是否再跌破200MA(被套)':>22}")
        cv = c.values; elv = el.values; mav = df['ma'].values
        for dt, t in d1_re:
            f21 = cv[t + 21] / cv[t] - 1 if t + 21 < len(cv) else np.nan
            f63 = cv[t + 63] / cv[t] - 1 if t + 63 < len(cv) else np.nan
            # 被套 = 回補後 20 日內任一日收盤 < 200MA(跌回線下)
            trap = any(cv[t + k] < mav[t + k] for k in range(1, min(21, len(cv) - t)))
            s21 = f"{f21*100:+.1f}%" if not np.isnan(f21) else "—"
            s63 = f"{f63*100:+.1f}%" if not np.isnan(f63) else "—"
            print(f"     {str(dt.date()):12}{s21:>9}{s63:>9}{'⚠️是(bull trap)' if trap else '否':>22}")
    print()


def main():
    print("📥 下載 SMH/QQQ/SPY + HYG/LQD + ^VIX ...")
    closes = {tk: dl(tk) for tk in TICKERS}
    hyg, lqd, vix = dl('HYG'), dl('LQD'), dl('^VIX')
    # 信用健康(HYG+LQD breadth,各 vs 自己200MA)
    def hln(s): return (s >= s.rolling(C.MA).mean()).astype(float)
    for tk in TICKERS:
        idx = closes[tk].index
        health = pd.concat([hln(hyg).reindex(idx).ffill(),
                            hln(lqd).reindex(idx).ffill()], axis=1).mean(axis=1)
        v = vix.reindex(idx).ffill()
        analyze(tk, closes[tk], health, v)


if __name__ == '__main__':
    main()
